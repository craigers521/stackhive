"""Inventory blueprint: device list/detail pages, device APIs, sync trigger."""
import builtins
import logging
import os

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_

from .. import AppError
from ..decorators import admin_required, editor_required, viewer_required
from ..extensions import cache, db
from ..models import Device, DeviceType
from ..services import netconf, drift

logger = logging.getLogger(__name__)

bp = Blueprint("inventory", __name__)


def _netbox_available():
    """Cached NetBox reachability probe (30s TTL) used for 503 surfacing."""
    from ..services.netbox import make_client, NetBoxError

    def check():
        """One-shot NetBox reachability probe (cached 30s)."""
        try:
            return make_client(current_app).is_available()
        except Exception:
            return False

    return cache.cached(timeout=30)(check)()


def _inventory_query(filters):
    """Build the filtered device query for list endpoints."""
    query = Device.query
    role = filters.get("role")
    type_name = filters.get("type")
    site = filters.get("site")
    status = filters.get("status")
    search = filters.get("search")
    if role:
        query = query.filter(Device.role == role)
    if type_name:
        query = query.join(DeviceType, Device.device_type_id == DeviceType.id).filter(
            DeviceType.model == type_name
        )
    if site:
        query = query.filter(Device.site == site)
    if status:
        query = query.filter(Device.monitoring_status == status)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(Device.hostname.ilike(like), Device.mgmt_ip.ilike(like), Device.serial_number.ilike(like))
        )
    return query.order_by(Device.hostname.asc())


def _filters_from_args():
    """Build the device query filters from request args (role, site, type, status, q)."""
    return {k: request.args.get(k) for k in ("role", "type", "site", "status", "search") if request.args.get(k)}


def _guard_source_unavailable():
    """Raise 503 only when the inventory source is down AND no cache exists."""
    if Device.query.count() > 0:
        return False
    if _netbox_available():
        return False
    raise AppError(503, "Service Unavailable", "NetBox inventory unavailable; no cached devices")


@bp.get("/inventory")
@viewer_required
def list():
    """HTML device list with filters and 50-per-page pagination."""
    _guard_source_unavailable()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50
    query = _inventory_query(_filters_from_args())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return render_template(
        "inventory/list.html",
        devices=pagination.items,
        pagination=pagination,
        filters=_filters_from_args(),
        source_down=not _netbox_available(),
        role_options=sorted({d.role for d in Device.query.with_entities(Device.role).distinct().all() if d[0]}),
        type_options=sorted({d.model for d in DeviceType.query.with_entities(DeviceType.model).distinct().all() if d[0]}),
        device_count_total=Device.query.count(),
    )


@bp.get("/inventory/<int:netbox_id>")
@viewer_required
def detail(netbox_id):
    """HTML stacked-section device detail page."""
    device = Device.query.filter_by(netbox_id=netbox_id).first_or_404()
    interfaces = drift.device_interface_rows(device)
    history = drift.device_deployment_history(device, limit=10)
    profile = drift.effective_profile(device)
    overrides = drift.device_overrides(device)
    import yaml

    return render_template(
        "inventory/detail.html",
        device=device,
        interfaces=interfaces,
        history=history,
        profile=profile,
        overrides=overrides,
        overrides_yaml=yaml.safe_dump(overrides, default_flow_style=False).strip() if overrides else "",
        grafana_url=drift.device_grafana_url(device),
    )


@bp.post("/inventory/<int:netbox_id>/overrides")
@editor_required
def edit_overrides(netbox_id):
    """Save per-device overrides as YAML (host_vars) via the profiles service."""
    from ..services import profiles as profiles_service

    device = Device.query.filter_by(netbox_id=netbox_id).first_or_404()
    raw = request.form.get("variables", "")
    try:
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            raise ValueError("overrides must be a YAML mapping")
    except yaml.YAMLError as exc:
        raise AppError(400, "Bad Request", f"Invalid YAML: {exc}")
    if data is None:
        data = {}
    variables = {
        str(k): (str(v) if not isinstance(v, (dict, builtins.list)) else yaml.safe_dump(v, default_flow_style=False).strip())
        for k, v in data.items()
    }
    try:
        profiles_service.save_device_overrides(
            current_app, device, variables, message=f"override: update {device.hostname}"
        )
    except profiles_service.ProfilesConflict as exc:
        raise AppError(409, "Conflict", str(exc))
    except profiles_service.ProfilesError as exc:
        raise AppError(400, "Bad Request", str(exc))
    return redirect(url_for("inventory.detail", netbox_id=netbox_id))


@bp.get("/api/devices")
@viewer_required
def api_devices():
    """Paginated JSON device list (default 25, max 100 per page)."""
    _guard_source_unavailable()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(1, request.args.get("per_page", 25, type=int)))
    query = _inventory_query(_filters_from_args())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(
        {
            "devices": [d.to_dict() for d in pagination.items],
            "total": pagination.total,
            "page": page,
            "per_page": per_page,
        }
    )


@bp.get("/api/devices/<int:netbox_id>")
@viewer_required
def api_device(netbox_id):
    """Full JSON device detail object."""
    device = Device.query.filter_by(netbox_id=netbox_id).first_or_404()
    return jsonify(
        device.to_detail_dict(
            interfaces=drift.device_interface_rows(device),
            overrides=drift.device_overrides(device),
            deployment_history=drift.device_deployment_history(device, limit=10),
            assigned_profiles=[p] if (p := (drift.effective_profile(device) and _profile_dict(drift.effective_profile(device)))) else [],
        )
    )


def _profile_dict(profile):
    """Compact profile object embedded in device detail responses."""
    return {
        "id": str(profile.id),
        "name": profile.name,
        "device_role": profile.device_role,
    }


@bp.post("/api/devices/<int:netbox_id>/drift-check")
@editor_required
def api_drift_check(netbox_id):
    """Compare running config to the last deployed render; flag drift."""
    from datetime import datetime, timezone

    device = Device.query.filter_by(netbox_id=netbox_id).first_or_404()
    if device.config_status not in ("deployed",):
        return jsonify(
            {
                "device_id": str(device.netbox_id),
                "status": device.config_status,
                "diff": "",
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    last_render = None
    if device.last_deployment is not None:
        last_render = device.last_deployment.preview_output
    user = os.environ.get("ANSIBLE_NETCONF_USER")
    password = os.environ.get("ANSIBLE_NETCONF_PASSWORD")
    if not user or not password:
        raise AppError(422, "Unprocessable", "NETCONF credentials are not configured")
    try:
        running = netconf.fetch_running_config(device.mgmt_ip, user, password)
    except netconf.NetconfUnreachable as exc:
        raise AppError(422, "Unprocessable", str(exc))
    diff = netconf.diff_configs(last_render, running)
    if diff and last_render:
        device.set_config_status("modified")
        db.session.commit()
        status = "modified"
    else:
        status = "deployed"
    return jsonify(
        {
            "device_id": str(device.netbox_id),
            "status": status,
            "diff": diff,
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


@bp.post("/api/inventory/sync")
@editor_required
def api_inventory_sync():
    """Editor-triggered full NetBox inventory sync."""
    from ..services.netbox import sync_inventory, NetBoxUnavailable, NetBoxError

    try:
        report = sync_inventory(current_app)
    except NetBoxUnavailable as exc:
        raise AppError(503, "Service Unavailable", str(exc))
    except (NetBoxError, Exception) as exc:
        logger.exception("inventory_sync_failed")
        raise AppError(500, "Internal Server Error", f"Inventory sync failed: {exc}")
    cache.delete("inventory")
    return jsonify({"report": report}), 202


@bp.post("/inventory/sync")
@editor_required
def inventory_sync_form():
    """POST /inventory/sync: HTML form variant of the NetBox sync trigger.

    Redirects back with a flash report so the empty-state CTA on the
    inventory page can refresh the list server-side (no client JS).
    """
    from ..services.netbox import NetBoxUnavailable, sync_inventory

    try:
        report = sync_inventory(current_app)
    except NetBoxUnavailable as exc:
        flash(str(exc), "warning")
    except Exception:
        logger.exception("inventory_sync_failed")
        flash("Inventory sync failed", "danger")
    else:
        cache.delete("inventory")
        flash(f"Inventory synced: {report}", "success")
    return redirect(request.referrer or url_for("inventory.list"))


@bp.get("/api/device-types")
@viewer_required
def api_device_types():
    """List device models with interface layout data."""
    query = DeviceType.query
    manufacturer = request.args.get("manufacturer")
    model = request.args.get("model")
    search = request.args.get("search")
    if manufacturer:
        query = query.filter(DeviceType.manufacturer == manufacturer)
    if model:
        query = query.filter(DeviceType.model == model)
    if search:
        like = f"%{search}%"
        query = query.filter(or_(DeviceType.model.ilike(like), DeviceType.manufacturer.ilike(like)))
    return jsonify({"device_types": [dt.to_dict() for dt in query.order_by(DeviceType.model).all()]})


@bp.put("/api/device-types/<int:device_type_id>")
@admin_required
def api_device_type_update(device_type_id):
    """Maintain local interface data for a device model (admin)."""
    dt = DeviceType.query.get(device_type_id)
    if dt is None:
        raise AppError(404, "Not Found", "Device type not found")
    data = request.get_json(silent=True) or {}
    if "interface_types" in data:
        types = data["interface_types"]
        if not isinstance(types, dict) or any(
            not isinstance(v, int) or v < 0 for v in types.values()
        ):
            raise AppError(400, "Bad Request", "interface_types must map type names to non-negative integers")
        dt.interface_types = types
        dt.interface_count = sum(types.values())
    for field in ("manufacturer", "model", "part_number", "slot_config", "uplink_slots", "management_interfaces", "interface_count"):
        if field in data:
            setattr(dt, field, data[field])
    if dt.interface_types and sum(dt.interface_types.values()) != (dt.interface_count or 0):
        raise AppError(400, "Bad Request", "interface_count must equal the sum of interface_types values")
    db.session.add(dt)
    db.session.commit()
    return jsonify(dt.to_dict())
