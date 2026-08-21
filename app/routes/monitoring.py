"""Monitoring blueprint: device status page/API and infrastructure health."""
import logging

from flask import Blueprint, current_app, jsonify, render_template, request

from .. import AppError
from ..decorators import viewer_required
from ..models import Device
from ..services import grafana

logger = logging.getLogger(__name__)

bp = Blueprint("monitoring", __name__)

INFRA_SERVICES = ("netbox", "gitlab", "grafana", "influxdb", "app")


def _last_check_iso(value):
    """ISO-8601 format of a last_check timestamp, or None."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _merged_device_statuses():
    """DB last-known statuses merged with the live Grafana alert map."""
    try:
        live = grafana.get_device_statuses(current_app)
    except grafana.GrafanaError as exc:
        logger.warning("grafana_unavailable: %s", exc)
        live = None
    entries = []
    for device in Device.query.order_by(Device.hostname).all():
        status = (live or {}).get(device.hostname) or device.monitoring_status
        entries.append(
            {
                "hostname": device.hostname,
                "ip_address": device.mgmt_ip.split("/")[0] if device.mgmt_ip else device.mgmt_ip,
                "status": status,
                "last_check": _last_check_iso(device.last_check),
                "grafana_url": grafana.device_url(current_app, device),
            }
        )
    return entries


@bp.get("/monitoring")
@viewer_required
def devices():
    """HTML monitoring page: device statuses + infrastructure health."""
    entries = _merged_device_statuses()
    try:
        infra = grafana.get_infra_statuses(current_app, INFRA_SERVICES)
    except grafana.GrafanaError as exc:
        logger.warning("grafana_unavailable: %s", exc)
        infra = {s: "degraded" for s in INFRA_SERVICES}
    role = request.args.get("role")
    if role:
        entries = [e for e in entries if (d := next((x for x in Device.query.all() if x.hostname == e["hostname"]), None)) and d.role == role]
    role_options = sorted({d.role for d in Device.query.with_entities(Device.role).distinct().all() if d[0]})
    netbox_ids = {d.hostname: d.netbox_id for d in Device.query.all()}
    return render_template(
        "monitoring/devices.html",
        entries=entries,
        infra=infra,
        infra_url=grafana.infra_url(current_app),
        role_options=role_options,
        netbox_ids=netbox_ids,
    )


@bp.get("/api/monitoring/devices")
@viewer_required
def api_devices():
    """Device status entries, optionally filtered by role/site."""
    role = request.args.get("role")
    site = request.args.get("site")
    entries = _merged_device_statuses()
    devices = {d.hostname: d for d in Device.query.all()}
    if role or site:
        entries = [
            e
            for e in entries
            if (not role or devices[e["hostname"]].role == role)
            and (not site or devices[e["hostname"]].site == site)
        ]
    return jsonify({"devices": entries})


@bp.get("/api/monitoring/infrastructure")
@viewer_required
def api_infrastructure():
    """Infrastructure service health entries."""
    try:
        statuses = grafana.get_infra_statuses(current_app, INFRA_SERVICES)
    except grafana.GrafanaError as exc:
        raise AppError(502, "Bad Gateway", f"Grafana API unavailable: {exc}")
    url = grafana.infra_url(current_app)
    return jsonify(
        {
            "services": [
                {
                    "name": name,
                    "status": statuses.get(name, "degraded"),
                    "last_check": None,
                    "grafana_url": url,
                }
                for name in INFRA_SERVICES
            ]
        }
    )
