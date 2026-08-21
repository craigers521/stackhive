"""Deployments blueprint: HTML pages, REST API, and GitLab pipeline webhook."""
import builtins
import hashlib
import hmac
import logging
import re

import yaml
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from .. import AppError
from ..decorators import admin_required, editor_required, viewer_required
from ..extensions import db
from ..models import ConfigurationProfile, DeploymentDevice, DeploymentRecord, Device
from ..services import ansible as ansible_service
from ..services import profiles as profiles_service
from ..services import settings as settings_service
from ..services.gitlab import GitLabConflict, GitLabError, GitLabUnavailable

logger = logging.getLogger(__name__)
audit = logging.getLogger("app.audit")

bp = Blueprint("deployments", __name__)

IN_FLIGHT_STATUSES = ("pending", "approved", "running")


def _utcnow():
    """Current UTC time as a naive datetime."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    """Format a datetime as ISO-8601 UTC, or None."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _git_client():
    """Return the GitLab client, or None when unconfigured."""
    try:
        return profiles_service._git_client(current_app)
    except GitLabUnavailable:
        return None


def _local_sha(record_id):
    """Pseudo-SHA for local-only (no GitLab) environments."""
    return hashlib.sha1(f"local-deploy-{record_id}".encode()).hexdigest()


def _inflight_device_ids():
    """Set of device ids with a pending, approved, or running deployment."""
    rows = (
        DeploymentDevice.query.join(DeploymentRecord, DeploymentDevice.deployment_id == DeploymentRecord.id)
        .filter(DeploymentRecord.status.in_(IN_FLIGHT_STATUSES))
        .with_entities(DeploymentDevice.device_id)
        .all()
    )
    return {r[0] for r in rows}


def _validate_targets(device_ids, profile):
    """Validate target devices against the profile; raises AppError on failure."""
    if not device_ids:
        raise AppError(400, "Bad Request", "device_ids is required and must be a non-empty array")
    devices = []
    inflight = _inflight_device_ids()
    for raw in device_ids:
        try:
            netbox_id = int(raw)
        except (TypeError, ValueError):
            raise AppError(400, "Bad Request", f"invalid device id: {raw!r}")
        device = Device.query.filter_by(netbox_id=netbox_id).first()
        if device is None:
            raise AppError(404, "Not Found", f"device {netbox_id} not found")
        if device.cloud_managed:
            raise AppError(409, "Conflict", f"device {device.hostname} is cloud-managed and excluded from deployments")
        if device.stale:
            raise AppError(409, "Conflict", f"device {device.hostname} is stale; sync inventory before deploying")
        if device.role != profile.device_role:
            raise AppError(422, "Unprocessable", f"device {device.hostname} role '{device.role}' does not match profile role '{profile.device_role}'")
        if device.id in inflight:
            raise AppError(409, "Conflict", f"device {device.hostname} has an in-flight deployment")
        if any(d.id == device.id for d in devices):
            raise AppError(400, "Bad Request", f"duplicate device {device.hostname}")
        devices.append(device)
    return devices


def _render_devices(devices, profile):
    """Render previews for all target devices; 422 on any render failure."""
    results = {}
    for device in devices:
        try:
            results[device.id] = ansible_service.render_preview(current_app, device, profile)
        except ansible_service.AnsiblePreviewError as exc:
            raise AppError(422, "Unprocessable", f"render failed for {device.hostname}: {exc}")
    return results


def _previous_render(device):
    """Most recent rendered config for a device (used for diffing)."""
    rows = (
        DeploymentDevice.query.join(DeploymentRecord)
        .filter(DeploymentDevice.device_id == device.id)
        .order_by(DeploymentRecord.started_at.desc())
        .all()
    )
    from ..services import netconf

    for row in rows:
        if row.deployment.preview_output:
            return row.deployment.preview_output
    return None


def _commit_manifest(record, profile, devices, message):
    """Commit the deployment manifest; returns (sha, git_branch) or (None, None)."""
    client = _git_client()
    branch = f"deploy/{record.id}"
    manifest = {
        "deployment_id": record.id,
        "profile": profile.name,
        "triggered_by": record.operator.username if record.operator else None,
        "triggered_at": _iso(record.started_at),
        "message": message or "",
        "devices": [
            {
                "hostname": d.hostname,
                "netbox_id": d.netbox_id,
                "mgmt_ip": d.mgmt_ip.split("/")[0] if d.mgmt_ip else d.mgmt_ip,
            }
            for d in devices
        ],
    }
    path = f"deployments/manifests/deploy-{record.id}.yml"
    files = {path: yaml.safe_dump(manifest, default_flow_style=False)}
    if client is None:
        return _local_sha(record.id), None
    try:
        working = profiles_service._working_branch(current_app)
        client.create_branch(branch, working)
        sha = client.commit_files(branch, f"deployment: {record.id} — {profile.name}", files, start_branch=branch)
        return sha, branch
    except GitLabError as exc:
        logger.warning("deployment_git_commit_failed id=%s: %s", record.id, exc)
        raise AppError(502, "Bad Gateway", f"GitLab commit failed: {exc}")


MAX_MR_TITLE_DEVICES = 10


def _mr_title(profile, devices):
    """Plan-conformant MR title: ``Deploy <profile> to <devices>``.

    Long device lists are truncated in the title; the full list lives in the
    MR description.
    """
    names = [d.hostname for d in devices]
    if len(names) > MAX_MR_TITLE_DEVICES:
        shown = ", ".join(names[:MAX_MR_TITLE_DEVICES])
        shown += f", … (+{len(names) - MAX_MR_TITLE_DEVICES} more)"
    else:
        shown = ", ".join(names)
    return f"Deploy {profile.name} to {shown}"


MR_DESCRIPTION_DIFF_LIMIT = 20000


def _mr_description(record, profile, devices):
    """MR body: target device list plus a rendered config diff preview.

    The diff section is capped so multi-device rollouts do not produce
    unmergeable description sizes.
    """
    lines = [f"Deployment #{record.id} — profile `{profile.name}` (role `{profile.device_role}`)"]
    lines.append(f"Triggered by {record.operator.username if record.operator else 'unknown'} at {_iso(record.started_at) or 'now'}")
    if record.error_message:
        lines.append(f"\nNote: {record.error_message}")
    lines.append("\n## Target devices")
    for d in devices:
        ip = d.mgmt_ip.split("/")[0] if d.mgmt_ip else "n/a"
        lines.append(f"- {d.hostname} (netbox {d.netbox_id}, {ip})")

    diff_sections = []
    for row in record.devices:
        if row.config_diff:
            diff_sections.append(f"```diff\n## {row.device.hostname}\n{row.config_diff}\n```")
    if diff_sections:
        body = "\n\n".join(diff_sections)
        if len(body) > MR_DESCRIPTION_DIFF_LIMIT:
            body = body[:MR_DESCRIPTION_DIFF_LIMIT] + "\n… (diff preview truncated)"
        lines.append("\n## Rendered config diff preview")
        lines.append(body)
    return "\n".join(lines)


def _open_merge_request(record, profile, devices):
    """Open the working-to-production merge request for a deployment."""
    client = _git_client()
    if client is None or record.git_branch is None:
        return
    production = settings_service.get_setting(current_app, "git_production_branch") or "main"
    try:
        title = _mr_title(profile, devices)
        description = _mr_description(record, profile, devices)
        client.create_merge_request(record.git_branch, production, title, description)
    except GitLabError as exc:
        logger.warning("deployment_mr_failed id=%s: %s", record.id, exc)
        raise AppError(502, "Bad Gateway", f"GitLab merge request failed: {exc}")


def create_deployment(user, device_ids, profile_id, message):
    """Shared create flow for the API and HTML endpoints."""
    profile = db.session.get(ConfigurationProfile, int(profile_id))
    if profile is None:
        raise AppError(404, "Not Found", "Profile not found")
    if not profile.is_active:
        raise AppError(422, "Unprocessable", f"profile '{profile.name}' is not active")
    devices = _validate_targets(device_ids, profile)
    renders = _render_devices(devices, profile)

    record = DeploymentRecord(
        profile_id=profile.id,
        device_count=len(devices),
        user_id=user.id,
        status="pending",
        git_commit_sha="pending",
        git_branch="",
        started_at=_utcnow(),
    )
    db.session.add(record)
    db.session.flush()

    combined = []
    for device in devices:
        render = renders[device.id]
        previous = _previous_render(device)
        diff = ""
        if previous:
            from ..services import netconf

            diff = netconf.diff_configs(previous, render.config)
        row = DeploymentDevice(
            deployment_id=record.id,
            device_id=device.id,
            status="success",
            message="preview rendered",
            config_diff=diff or render.config,
            started_at=record.started_at,
        )
        db.session.add(row)
        combined.append(f"# === {device.hostname} ({profile.name}) ===\n{render.config}")
    record.preview_output = "\n\n".join(combined)

    sha, branch = _commit_manifest(record, profile, devices, message)
    record.git_commit_sha = sha
    record.git_branch = branch or ""
    db.session.commit()
    _open_merge_request(record, profile, devices)
    db.session.commit()
    return record


def _pending_deployments_query():
    """Query of pending deployment records, newest first."""
    return (
        DeploymentRecord.query.filter_by(status="pending")
        .order_by(DeploymentRecord.started_at.desc())
    )


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

@bp.get("/deployments")
@viewer_required
def list():
    """HTML deployment history with status filter."""
    status = request.args.get("status") or ""
    query = DeploymentRecord.query.order_by(DeploymentRecord.started_at.desc())
    if status:
        query = query.filter(DeploymentRecord.status == status)
    deployments = query.all()
    pending = len(DeploymentRecord.query.filter_by(status="pending").all())
    return render_template("deployments/list.html", deployments=deployments, status=status, pending=pending)


@bp.get("/deployments/new")
@editor_required
def new():
    """Deployment creation form: pick a profile, then its devices."""
    profiles = ConfigurationProfile.query.filter_by(is_active=True).order_by(ConfigurationProfile.name).all()
    return render_template("deployments/new.html", profiles=profiles)


@bp.get("/deployments/new/devices")
@editor_required
def device_options():
    """HTMX partial: device checkboxes for the selected profile's device role."""
    profile_id = request.args.get("profile_id") or ""
    devices = []
    if profile_id:
        profile = db.session.get(ConfigurationProfile, int(profile_id))
        if profile is not None:
            devices = Device.query.filter_by(role=profile.device_role).order_by(Device.hostname).all()
    return render_template("deployments/_device_options.html", devices=devices)


@bp.post("/deployments")
@editor_required
def create():
    """POST /deployments: create (or preview) from the HTML form and redirect."""
    from flask_login import current_user

    profile_id = request.form.get("profile_id") or ""
    device_ids = request.form.getlist("device_ids")
    message = (request.form.get("message") or "").strip() or None
    if not profile_id:
        flash("Select a profile", "danger")
        return redirect(url_for("deployments.new"))
    if (request.form.get("action") or "create") == "preview":
        if not device_ids:
            flash("Select at least one device to preview", "danger")
            return redirect(url_for("deployments.new"))
        return redirect(url_for("deployments.preview_new", profile_id=profile_id, device_ids=",".join(device_ids)))
    try:
        record = create_deployment(current_user, device_ids, profile_id, message)
    except AppError as exc:
        flash(exc.details or exc.name, "danger")
        return redirect(url_for("deployments.new"))
    flash(f"Deployment {record.id} created (pending approval)", "success")
    return redirect(url_for("deployments.detail_view", did=record.id))


@bp.get("/deployments/<int:did>")
@viewer_required
def detail_view(did):
    """Deployment detail: pipeline state, per-device results, diff, approve gate."""
    record = DeploymentRecord.query.get_or_404(did)
    profiles = ConfigurationProfile.query.filter_by(is_active=True).order_by(ConfigurationProfile.name).all()
    return render_template(
        "deployments/detail.html",
        record=record,
        operators_pending=_pending_deployments_query().count(),
    )


@bp.post("/deployments/<int:did>/approve")
@admin_required
def approve(did):
    """POST /deployments/<id>/approve: the admin approval gate (HTML)."""
    from flask_login import current_user

    record = DeploymentRecord.query.get_or_404(did)
    try:
        result = approve_deployment(current_user, record)
    except AppError as exc:
        flash(exc.details or exc.name, "danger")
        return redirect(url_for("deployments.detail_view", did=did))
    flash(f"Deployment {record.id} approved", "success")
    return redirect(url_for("deployments.detail_view", did=did))


def _split_preview(record):
    """Split a combined preview_output into per-device rendered blocks.

    The create flow stores one block per device headed by
    ``# === <hostname> (<profile>) ===``; the preview page renders each
    block as its own card with the device link.
    """
    if not record.preview_output:
        return []
    parts = re.split(r"^# === (.+?) ===\s*$", record.preview_output, flags=re.M)
    sections = []
    for i in range(1, len(parts) - 1, 2):
        header = parts[i]
        hostname = header.split("(")[0].strip()
        sections.append({"hostname": hostname, "config": parts[i + 1].strip()})
    return sections


@bp.get("/deployments/<int:did>/preview.html")
@viewer_required
def preview_page(did):
    """Rendered preview artifact page for a deployment record."""
    record = DeploymentRecord.query.get_or_404(did)
    hostnames = {row.device.hostname: row.device.netbox_id for row in record.devices if row.device}
    return render_template(
        "deployments/preview.html", record=record, sections=_split_preview(record), hostnames=hostnames
    )


@bp.get("/deployments/preview")
@viewer_required
def preview_new():
    """Pre-deployment preview (FR-009): renders the selected devices for review.

    Stateless: the render runs on every view so the page always shows the
    current configuration variables; nothing is committed until the form is
    confirmed and the deployment is approved. Snippets are annotated with
    their source template (profile override vs role default) and the
    effective variable set is shown per device.
    """
    from ..services.ansible import annotate_snippets

    profile_id = request.args.get("profile_id") or ""
    device_ids = [d for d in (request.args.get("device_ids") or "").split(",") if d]
    if not profile_id or not device_ids:
        flash("Select a profile and at least one device", "warning")
        return redirect(url_for("deployments.new"))
    try:
        profile = db.session.get(ConfigurationProfile, int(profile_id))
        if profile is None:
            raise AppError(404, "Not Found", "Profile not found")
        if not profile.is_active:
            raise AppError(422, "Unprocessable", f"profile '{profile.name}' is not active")
        devices = _validate_targets(device_ids, profile)
        renders = _render_devices(devices, profile)
    except AppError as exc:
        flash(exc.details or exc.name, "danger")
        return redirect(url_for("deployments.new"))
    items = [
        {
            "device": device,
            "config": renders[device.id].config,
            "snippets": annotate_snippets(profile, renders[device.id].snippets),
            "variables_used": renders[device.id].variables_used,
        }
        for device in devices
    ]
    return render_template(
        "deployments/preview_new.html",
        profile=profile,
        items=items,
        device_ids=",".join(device_ids),
    )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@bp.post("/api/deployments/preview")
@viewer_required
def api_preview():
    """Render a live preview for one device (no DB record created)."""
    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    profile_id = data.get("profile_id")
    if device_id is None:
        raise AppError(400, "Bad Request", "device_id is required")
    device = Device.query.filter_by(netbox_id=int(device_id)).first()
    if device is None:
        raise AppError(404, "Not Found", "Device not found")
    if profile_id:
        profile = db.session.get(ConfigurationProfile, int(profile_id))
        if profile is None:
            raise AppError(404, "Not Found", "Profile not found")
    else:
        from ..services import drift

        profile = drift.effective_profile(device)
        if profile is None:
            raise AppError(404, "Not Found", f"no active profile for role '{device.role}'")
    try:
        result = ansible_service.render_preview(current_app, device, profile)
    except ansible_service.AnsiblePreviewError as exc:
        raise AppError(422, "Unprocessable", f"render failed: {exc}")
    return jsonify(
        {
            "device_id": str(device.netbox_id),
            "hostname": device.hostname,
            "profile_name": profile.name,
            "config": result.config,
            "snippets": result.snippets,
            "variables_used": result.variables_used,
        }
    )


@bp.post("/api/deployments")
@editor_required
def api_deploy():
    """POST /api/deployments: create a deployment; 202 with the record."""
    from flask_login import current_user

    data = request.get_json(silent=True) or {}
    device_ids = data.get("device_ids")
    profile_id = data.get("profile_id")
    if not isinstance(device_ids, builtins.list) or not device_ids:
        raise AppError(400, "Bad Request", "device_ids must be a non-empty array")
    if not profile_id:
        raise AppError(400, "Bad Request", "profile_id is required")
    record = create_deployment(current_user, device_ids, profile_id, data.get("message"))
    rows = DeploymentDevice.query.filter_by(deployment_id=record.id).all()
    return jsonify(
        {
            "deployment_id": str(record.id),
            "status": record.status,
            "device_ids": [str(r.device.netbox_id) for r in rows],
            "profile_id": str(record.profile_id),
            "triggered_by": record.operator.username if record.operator else None,
            "triggered_at": _iso(record.started_at),
            "git_commit_sha": record.git_commit_sha,
            "pipeline_id": record.pipeline_id,
        }
    ), 202


@bp.get("/api/deployments")
@viewer_required
def api_deployments():
    """GET /api/deployments: paginated list with device, profile, status filters."""
    query = DeploymentRecord.query
    device_id = request.args.get("device_id")
    profile_id = request.args.get("profile_id")
    status = request.args.get("status")
    if device_id:
        device = Device.query.filter_by(netbox_id=int(device_id)).first()
        if device is None:
            raise AppError(404, "Not Found", "Device not found")
        query = query.join(DeploymentDevice, DeploymentRecord.id == DeploymentDevice.deployment_id).filter(
            DeploymentDevice.device_id == device.id
        )
    if profile_id:
        query = query.filter(DeploymentRecord.profile_id == int(profile_id))
    if status:
        query = query.filter(DeploymentRecord.status == status)
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 25, type=int)))
    pagination = query.order_by(DeploymentRecord.started_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        {
            "deployments": [r.to_dict() for r in pagination.items],
            "total": pagination.total,
            "page": page,
        }
    )


@bp.get("/api/deployments/<int:did>")
@viewer_required
def api_deployment(did):
    """GET /api/deployments/<id>: one deployment record."""
    record = DeploymentRecord.query.get_or_404(did)
    return jsonify(record.to_dict())


@bp.post("/api/deployments/<int:did>/approve")
@admin_required
def api_approve(did):
    """POST /api/deployments/<id>/approve: approve and return the JSON result."""
    from flask_login import current_user

    record = DeploymentRecord.query.get_or_404(did)
    result = approve_deployment(current_user, record)
    return jsonify({"status": result.status, "pipeline_id": result.pipeline_id})


# ---------------------------------------------------------------------------
# Approval + webhook
# ---------------------------------------------------------------------------

def approve_deployment(user, record):
    """Admin gate: approve a pending deployment (merge MR, trigger pipeline)."""
    if record.status != "pending":
        raise AppError(409, "Conflict", f"deployment is already {record.status}")
    client = _git_client()
    pipeline_id = None
    pipeline_url = None
    if client is not None and record.git_branch:
        try:
            mrs = client.list_merge_requests(source_branch=record.git_branch, state="opened")
            if mrs:
                client.merge_merge_request(mrs[0]["iid"])
            pipelines = client.list_pipelines(ref=settings_service.get_setting(current_app, "git_production_branch") or "main", per_page=5)
            for pipe in pipelines or []:
                if pipe.get("status") in ("created", "pending", "running", "success", "failed"):
                    pipeline_id = pipe.get("id")
                    pipeline_url = pipe.get("web_url")
                    break
        except GitLabError as exc:
            logger.warning("approve_gitlab_failed id=%s: %s", record.id, exc)
            raise AppError(502, "Bad Gateway", f"GitLab approval step failed: {exc}")
    record.status = "approved"
    record.approved_by_id = user.id
    record.pipeline_id = pipeline_id
    record.pipeline_url = pipeline_url
    db.session.commit()
    audit.info(
        "deployment_approved deployment_id=%s profile=%s devices=%d by=%s pipeline_id=%s",
        record.id,
        record.profile.name if record.profile else "?",
        record.device_count,
        user.username,
        pipeline_id or "none",
    )
    return record


@bp.post("/api/webhooks/gitlab/pipeline")
def pipeline_webhook():
    """GitLab pipeline status webhook (token-authenticated, not session)."""
    expected = current_app.config.get("GITLAB_WEBHOOK_TOKEN") or current_app.config.get("GITLAB_SHARED_SECRET") or ""
    provided = request.headers.get("X-GitLab-Token", "")
    # Constant-time comparison avoids leaking expected-secret length/timing.
    if not expected or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise AppError(401, "Unauthorized", "invalid webhook token")
    body = request.get_json(silent=True) or {}
    # Two accepted body shapes: the custom contract shape and the native
    # GitLab pipeline event (what the CI notify job forwards).
    attrs = {}
    if "pipeline_id" in body:
        pipeline_id = body.get("pipeline_id")
        status = body.get("status")
        sha = body.get("commit_sha")
        device_results = body.get("devices") or []
    else:
        attrs = body.get("object_attributes") or {}
        if isinstance(attrs.get("commit"), dict):
            sha = attrs.get("commit", {}).get("id")
        else:
            sha = attrs.get("sha") or attrs.get("before")
        status = attrs.get("status")
        pipeline_id = attrs.get("id")
        device_results = []
    if not pipeline_id:
        raise AppError(400, "Bad Request", "missing pipeline id")

    record = DeploymentRecord.query.filter_by(pipeline_id=pipeline_id).first()
    if record is None and sha:
        record = DeploymentRecord.query.filter_by(git_commit_sha=sha).first()
    if record is None:
        logger.info("webhook_pipeline_unknown id=%s sha=%s", pipeline_id, sha)
        return jsonify({"status": "ok", "ignored": True})

    record.pipeline_id = pipeline_id
    pipeline_url = attrs.get("web_url") or (body.get("project") or {}).get("web_url")
    if pipeline_url:
        record.pipeline_url = pipeline_url
    record.pipeline_status = status
    now = _utcnow()

    if status in ("created", "pending", "preparing", "running"):
        record.status = "running"
    elif status == "success":
        record.status = "success"
        record.completed_at = now
        _finalize_devices(record, success=True, now=now, device_results=device_results)
    elif status in ("failed", "canceled", "skipped"):
        record.status = "failed" if status == "failed" else "cancelled"
        record.completed_at = now
        record.error_message = f"pipeline {pipeline_id} {status}"
        _finalize_devices(record, success=False, now=now, device_results=device_results)
    else:
        logger.info("webhook_pipeline_status_ignored id=%s status=%s", record.id, status)
    db.session.commit()
    return jsonify({"status": "ok", "deployment_id": str(record.id)})


def _finalize_devices(record, success, now, device_results=None):
    """Propagate pipeline outcome to per-device rows and device config status."""
    results_by_host = {d.get("hostname"): d for d in (device_results or []) if isinstance(d, dict)}
    for row in record.devices:
        result = results_by_host.get(row.device.hostname)
        if result and result.get("status"):
            row.status = "success" if result["status"] == "success" else "failed"
        else:
            row.status = "success" if success else "failed"
        row.message = (result or {}).get("message") or f"pipeline {record.pipeline_id} {'succeeded' if success else 'failed'}"
        if (result or {}).get("diff"):
            row.config_diff = result["diff"]
        row.completed_at = now
        device = row.device
        if success:
            if device.set_config_status("deployed"):
                device.last_deployment = record
        else:
            if not device.set_config_status("failed"):
                # illegal from current state (e.g. pending devices stay unchanged)
                logger.info("config_status_hold device=%s from=%s", device.hostname, device.config_status)
    db.session.flush()
