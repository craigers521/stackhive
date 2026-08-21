"""Onboarding blueprint: ZTP provisioning queue (UI + API) and Meraki flow."""
import logging

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from .. import AppError
from ..decorators import editor_required, viewer_required
from ..extensions import db
from ..models import ConfigurationProfile, Device, ZTPProvision
from ..services import meraki as meraki_service
from ..services import profiles as profiles_service
from ..services import settings as settings_service
from ..services import ztp as ztp_service

logger = logging.getLogger(__name__)

bp = Blueprint("onboarding", __name__)


def _ztp_base_url():
    """Resolvable ZTP base URL (setting, else app config)."""
    return settings_service.get_setting(current_app, "ztp_base_url") or app_config_ztp()


def app_config_ztp():
    """ZTP base URL straight from the Flask config."""
    return current_app.config.get("ZTP_BASE_URL", "")


def _commit_ztp_files(provision, serial, script, config):
    """Persist the ZTP artifacts to the Git repo (when configured)."""
    client = _git_client()
    if client is None:
        return
    try:
        branch = profiles_service._working_branch(current_app)
        client.push_with_rebase(
            branch,
            f"ztp: {serial} day-0 artifacts",
            {
                f"ztp/{serial}/script.txt": script,
                f"ztp/{serial}/day-0.cfg": config,
            },
        )
    except Exception as exc:  # noqa: BLE001 - git is best-effort for ZTP
        logger.warning("ztp_git_commit_failed serial=%s: %s", serial, exc)
        db.session.rollback()


def _git_client():
    """Return the GitLab client, or None when unconfigured (best-effort)."""
    from ..services.gitlab import GitLabUnavailable

    try:
        return profiles_service._git_client(current_app)
    except GitLabUnavailable:
        return None


def create_ztp_provision(user, device, serial, hostname, profile, is_meraki=False, meraki_network_id=None, meraki_dashboard_url=None):
    """Shared create flow: render day-0 config, persist record, commit to Git."""
    if device.cloud_managed and not is_meraki:
        raise AppError(409, "Conflict", "cloud-managed devices onboard via Meraki, not ZTP")
    existing = device.ztp_provision
    if existing is not None:
        raise AppError(409, "Conflict", f"device {device.hostname} already has a ZTP provision ({existing.status})")

    config = ztp_service.generate_ztp_app(current_app, device, profile)
    if is_meraki:
        if not meraki_network_id:
            raise AppError(422, "Unprocessable", "network_id is required for Meraki onboarding")
        try:
            api_key, org_id, dash_url = ztp_service.resolve_meraki_inputs(current_app, meraki_dashboard_url)
        except ztp_service.ZtpError as exc:
            raise AppError(422, "Unprocessable", str(exc)) from exc
        config += ztp_service.meraki_day0_block(api_key, org_id, meraki_network_id, dash_url)
        # FR-015: Meraki-onboarded devices stay in inventory flagged
        # cloud-managed and are excluded from direct NETCONF deployment.
        device.cloud_managed = True
    base = _ztp_base_url()
    script = ztp_service.ztp_script_for(serial, base)

    provision = ZTPProvision(
        device_id=device.id,
        config_content=config,
        script_content=script,
        url=f"{base}/ztp/{serial}",
        status="generated",
        is_meraki=is_meraki,
        git_path=f"ztp/{serial}/",
    )
    db.session.add(provision)
    db.session.flush()
    _commit_ztp_files(provision, serial, script, config)
    db.session.commit()

    if is_meraki:
        _reserve_meraki_device(device, hostname, meraki_network_id, meraki_dashboard_url)
    return provision


def _reserve_meraki_device(device, hostname, network_id, dashboard_url):
    """Best-effort device reservation in the Meraki dashboard (T051)."""
    from ..services import credential

    try:
        client = meraki_service.make_meraki_client(current_app)
    except (meraki_service.MerakiUnavailable, credential.CredentialError) as exc:
        logger.info("meraki_not_configured: %s", exc)
        return
    try:
        if network_id:
            client.get_network(network_id)
        existing = client.find_device(device.serial_number)
        if existing is None:
            client.add_device(device.serial_number, name=hostname, notes=f"StackHive ZTP onboarding {provision_note(device)}")
        else:
            client.update_device(device.serial_number, name=hostname)
    except meraki_service.MerakiError as exc:
        logger.warning("meraki_reservation_failed serial=%s: %s", device.serial_number, exc)
        provision = device.ztp_provision
        if provision is not None:
            provision.error_message = f"meraki reservation failed: {exc}"
            if not provision.set_status("failed"):
                pass
            db.session.commit()


def provision_note(device):
    """Short note suffix used when reserving devices in Meraki."""
    return f"({device.hostname})"


# Queue states: a provision is "pending" for onboarding purposes until it
# reaches a terminal state (onboarded/failed/cancelled).
ZTP_ACTIVE_STATES = ("pending", "generated", "delivered")


def _pending_entries():
    """Active ZTP provisions (pending/generated/delivered), newest first.

    Terminal-state provisions are excluded from the queue so the page shows
    only work that still needs attention; their history remains on the
    device detail page and in the database.
    """
    rows = (
        ZTPProvision.query.join(Device, ZTPProvision.device_id == Device.id)
        .filter(ZTPProvision.status.in_(ZTP_ACTIVE_STATES))
        .order_by(ZTPProvision.created_at.desc())
        .all()
    )
    return rows


@bp.get("/onboarding")
@viewer_required
def index():
    """HTML onboarding queue with the provisioning form."""
    from ..services import drift as drift_service

    profiles = ConfigurationProfile.query.filter_by(is_active=True).order_by(ConfigurationProfile.name).all()
    devices = Device.query.order_by(Device.hostname).all()
    provisions = _pending_entries()
    # ZTPProvision stores no profile reference: the queue shows the active
    # profile bound to each device's role (the one the provision rendered with).
    profile_by_role = {}
    for p in provisions:
        device = p.device
        if device is not None and device.role not in profile_by_role:
            profile = drift_service.effective_profile(device)
            profile_by_role[device.role] = profile.name if profile else None
    return render_template(
        "onboarding/index.html",
        provisions=provisions,
        profiles=profiles,
        devices=devices,
        profile_by_role=profile_by_role,
    )


@bp.post("/onboarding")
@editor_required
def create():
    """POST /onboarding: create a ZTP or Meraki provision from the HTML form."""
    device_id = (request.form.get("device_id") or "").strip()
    serial = (request.form.get("serial") or "").strip()
    hostname = (request.form.get("hostname") or "").strip()
    profile_id = (request.form.get("profile_id") or "").strip()
    is_meraki = request.form.get("is_meraki") == "1"
    network_id = (request.form.get("network_id") or "").strip() or None
    try:
        device = Device.query.filter_by(netbox_id=int(device_id)).first()
        if device is None:
            raise AppError(404, "Not Found", "Device not found")
        if not serial:
            raise AppError(400, "Bad Request", "serial is required")
        if not hostname:
            hostname = device.hostname
        profile = db.session.get(ConfigurationProfile, int(profile_id))
        if profile is None:
            raise AppError(404, "Not Found", "Profile not found")
        from flask_login import current_user

        create_ztp_provision(
            current_user,
            device,
            serial,
            hostname,
            profile,
            is_meraki=is_meraki,
            meraki_network_id=network_id,
        )
    except AppError as exc:
        flash(exc.details or exc.name, "danger")
        return redirect(url_for("onboarding.index"))
    flash(f"ZTP provision created for {hostname}", "success")
    return redirect(url_for("onboarding.index"))


@bp.post("/onboarding/<int:pid>/cancel")
@editor_required
def cancel(pid):
    """POST /onboarding/<id>/cancel: cancel a live provision (HTML)."""
    provision = ZTPProvision.query.get_or_404(pid)
    if not provision.set_status("cancelled"):
        flash(f"provision cannot be cancelled from status '{provision.status}'", "warning")
    else:
        db.session.commit()
        flash(f"ZTP provision for {provision.device.hostname} cancelled", "success")
    return redirect(url_for("onboarding.index"))


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@bp.get("/api/onboarding/ztp")
@viewer_required
def api_ztp_list():
    """GET /api/onboarding/ztp: list provisions with device info."""
    return jsonify({"devices": [p.to_dict() for p in _pending_entries()]})


@bp.post("/api/onboarding/ztp")
@editor_required
def api_ztp_create():
    """POST /api/onboarding/ztp: create a ZTP provision; 201 with the artifact URLs."""
    from flask_login import current_user

    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    serial = (data.get("serial") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    profile_id = data.get("profile_id")
    if not device_id or not serial or not hostname or not profile_id:
        raise AppError(400, "Bad Request", "device_id, serial, hostname, and profile_id are required")
    device = Device.query.filter_by(netbox_id=int(device_id)).first()
    if device is None:
        raise AppError(404, "Not Found", "Device not found")
    profile = db.session.get(ConfigurationProfile, int(profile_id))
    if profile is None:
        raise AppError(404, "Not Found", "Profile not found")
    try:
        provision = create_ztp_provision(
            current_user,
            device,
            serial,
            hostname,
            profile,
            is_meraki=bool(data.get("is_meraki", False)),
        )
    except ztp_service.ZtpError as exc:
        raise AppError(422, "Unprocessable", str(exc))
    return jsonify(
        {
            "device_id": str(device.netbox_id),
            "serial": serial,
            "ztp_url": f"{provision.url}.txt",
            "config_url": f"{provision.url}.cfg",
            "status": provision.status,
        }
    ), 201


@bp.post("/api/onboarding/meraki")
@editor_required
def api_meraki_create():
    """POST /api/onboarding/meraki: create a Meraki onboarding provision."""
    from flask_login import current_user

    data = request.get_json(silent=True) or {}
    device_id = data.get("device_id")
    serial = (data.get("serial") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    network_id = (data.get("network_id") or "").strip()
    dashboard_url = (data.get("dashboard_url") or "").strip() or None
    if not device_id or not serial or not hostname or not network_id:
        raise AppError(400, "Bad Request", "device_id, serial, hostname, and network_id are required")
    device = Device.query.filter_by(netbox_id=int(device_id)).first()
    if device is None:
        raise AppError(404, "Not Found", "Device not found")
    from ..services import drift

    profile = drift.effective_profile(device)
    if profile is None:
        raise AppError(404, "Not Found", f"no active profile for role '{device.role}'")
    try:
        provision = create_ztp_provision(
            current_user,
            device,
            serial,
            hostname,
            profile,
            is_meraki=True,
            meraki_network_id=network_id,
            meraki_dashboard_url=dashboard_url,
        )
    except ztp_service.ZtpError as exc:
        raise AppError(422, "Unprocessable", str(exc))
    return jsonify(
        {
            "device_id": str(device.netbox_id),
            "serial": serial,
            "ztp_url": f"{provision.url}.txt",
            "config_url": f"{provision.url}.cfg",
            "meraki_network_id": network_id,
            "status": provision.status,
        }
    ), 201
