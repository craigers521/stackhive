"""Device presentation helpers, on-demand and nightly config-drift checks."""
import logging
import os
import re

logger = logging.getLogger(__name__)

# Nightly sweep fires on the first refresh tick inside this local hour
# (plan: drift detection job at 02:00 local).
DRIFT_CHECK_HOUR = 2
_last_drift_run = None

MANAGEMENT_TYPE_HINTS = ("Management", "Mgmt", "T1", "Serial")
UPLINK_TYPES = ("TenGigabitEthernet", "TwentyFiveGigE", "FortyGigE", "HundredGigE", "FortyGigabitEthernet")


def effective_profile(device):
    """Return the active profile matching the device role, or None."""
    from ..models import ConfigurationProfile

    return (
        ConfigurationProfile.query.filter_by(device_role=device.role, is_active=True)
        .order_by(ConfigurationProfile.updated_at.desc())
        .first()
    )


def _device_last_render(device):
    """Per-device rendered block from the last deployment's preview_output.

    The create flow stores one block per device headed by
    ``# === <hostname> (<profile>) ===`` (the same split the preview page
    uses); devices without a stored render return None.
    """
    record = device.last_deployment
    if record is None or not record.preview_output:
        return None
    header = re.compile(r"^# === " + re.escape(device.hostname) + r" \(.+?\) ===\s*\n", re.M)
    match = header.search(record.preview_output)
    if not match:
        return record.preview_output
    remainder = record.preview_output[match.end():]
    next_header = re.search(r"^# === ", remainder, re.M)
    block = remainder[: next_header.start()] if next_header else remainder
    return block.strip() or None


def nightly_drift_check(app):
    """Nightly NETCONF read-back drift sweep over deployed devices.

    Gated on the ``drift_check_enabled`` setting (default on) and limited to
    one run per local calendar day, fired on the first refresh tick at
    02:00 local (plan non-functional decision: drift detection). Every
    non-cloud-managed device in ``deployed`` state has its running config
    read back over NETCONF and compared with the stored render of its last
    deployment; any divergence marks it ``modified`` so the dashboard and
    inventory surface the drift. Unreachable devices are skipped and logged
    so a maintenance window does not flag the fleet. Returns the number of
    devices marked modified.
    """
    global _last_drift_run

    from datetime import datetime

    from ..extensions import db
    from ..models import Device
    from . import netconf, settings as settings_service

    if not settings_service.get_setting(app, "drift_check_enabled"):
        return 0
    now_local = datetime.now()
    if now_local.hour != DRIFT_CHECK_HOUR or _last_drift_run == now_local.date():
        return 0
    _last_drift_run = now_local.date()

    user = os.environ.get("ANSIBLE_NETCONF_USER")
    password = os.environ.get("ANSIBLE_NETCONF_PASSWORD")
    if not user or not password:
        logger.info("nightly_drift_skipped reason=no NETCONF credentials configured")
        return 0

    modified = 0
    devices = Device.query.filter_by(config_status="deployed", cloud_managed=False).all()
    for device in devices:
        last_render = _device_last_render(device)
        if not last_render:
            logger.info("nightly_drift_no_render host=%s", device.hostname)
            continue
        try:
            running = netconf.fetch_running_config(device.mgmt_ip or device.hostname, user, password)
        except netconf.NetconfUnreachable as exc:
            logger.warning("nightly_drift_unreachable host=%s reason=%s", device.hostname, exc)
            continue
        if netconf.diff_configs(last_render, running) and device.set_config_status("modified"):
            modified += 1
    if modified:
        db.session.commit()
    logger.info("nightly_drift_complete checked=%d modified=%d", len(devices), modified)
    return modified


def device_overrides(device):
    """Return {key: value} for the device's host_vars override variables."""
    return {v.key: v.value for v in device.overrides}


def device_grafana_url(device):
    """Grafana deep-link for the device; custom UID wins over the default."""
    from flask import current_app

    from . import settings as settings_service

    app = current_app
    uid = device.grafana_dashboard_uid or settings_service.get_setting(app, "device_dashboard_uid")
    base = (settings_service.get_setting(app, "grafana_url") or "").rstrip("/")
    url = f"{base}/d/{uid}"
    query = []
    if device.hostname:
        query.append(f"var-hostname={device.hostname}")
    if device.mgmt_ip:
        query.append(f"var-ip={device.mgmt_ip}")
    if query:
        url += "?" + "&".join(query)
    return url


def device_interface_rows(device):
    """Interface rows for detail pages: NetBox live data when reachable,
    otherwise a layout derived from the DeviceType definition."""
    rows = _netbox_interfaces(device)
    if rows is not None:
        return rows
    return _derive_interfaces(device)


def _netbox_interfaces(device):
    """Best-effort live interface fetch; None when NetBox is unavailable."""
    from .netbox import make_client, NetBoxError

    try:
        client = make_client(_flask_app())
        raw_rows = client.list_device_interfaces(device.netbox_id)
    except Exception:
        return None
    rows = []
    for row in raw_rows:
        slug = row.get("type") or "ethernet"
        rows.append(
            {
                "name": row.get("name", ""),
                "type": slug,
                "slot": 0,
                "port": 0,
                "enabled": bool(row.get("enabled", True)),
                "description": row.get("description") or "",
            }
        )
    return rows


def _flask_app():
    """The current Flask app object (for service calls outside a request)."""
    from flask import current_app

    return current_app._get_current_object()


def _derive_interfaces(device):
    """Generate interface rows from the DeviceType interface layout."""
    rows = []
    dt = device.device_type
    if dt is None or not dt.interface_types:
        return rows
    for type_name, count in (dt.interface_types or {}).items():
        for i in range(1, int(count) + 1):
            rows.append(
                {
                    "name": f"{type_name}{i}",
                    "type": _classify(type_name),
                    "slot": 0,
                    "port": i,
                    "enabled": True,
                    "description": "",
                }
            )
    for mgmt in dt.management_interfaces or []:
        rows.append(
            {
                "name": mgmt,
                "type": "management",
                "slot": 0,
                "port": 0,
                "enabled": True,
                "description": "",
            }
        )
    return rows


def _classify(type_name):
    """Map an interface type name to the management/uplink/access class."""
    for hint in MANAGEMENT_TYPE_HINTS:
        if hint.lower() in type_name.lower():
            return "management"
    for uplink in UPLINK_TYPES:
        if uplink.lower() in type_name.lower():
            return "uplink"
    return "access"


def device_deployment_history(device, limit=10):
    """Most recent deployment batch rows that touched this device."""
    from ..models import DeploymentDevice, Device as DeviceModel

    rows = (
        DeploymentDevice.query.join(DeviceModel, DeploymentDevice.device_id == DeviceModel.id)
        .filter(DeploymentDevice.device_id == device.id)
        .order_by(DeploymentDevice.started_at.desc())
        .limit(limit)
        .all()
    )
    return [row.to_dict() | {"deployment_id": str(row.deployment_id)} for row in rows]
