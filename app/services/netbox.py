"""NetBox REST API client with caching, retries and standardized errors."""
import logging
import time

import requests

logger = logging.getLogger(__name__)


class NetBoxError(Exception):
    """Base error for NetBox integration failures."""

    def __init__(self, message, status=None):
        """Keep the error message plus the upstream status when known."""
        super().__init__(message)
        self.status = status


class NetBoxUnavailable(NetBoxError):
    """NetBox is unreachable or authentication failed (surfaced as 503)."""


class NetBoxNotFound(NetBoxError):
    """The requested NetBox object does not exist (surfaced as 404)."""


class NetBoxClient:
    """Thin client over the NetBox REST API (Token auth, all-pages helpers)."""

    def __init__(self, base_url, token, timeout=10, retries=3):
        """Store endpoint, token, timeout, retries; prepare the HTTP session."""
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {token}"})

    def _url(self, path):
        """Absolute URL for a NetBox API path."""
        return f"{self.base_url}{path}"

    def _get(self, path, params=None, _retry=True):
        """GET a path with retry/backoff on 5xx; map auth/timeout failures.

        401/403 and connection errors raise ``NetBoxUnavailable``; 404 raises
        ``NetBoxNotFound``; 5xx retries up to ``retries`` times then raises
        ``NetBoxUnavailable``.
        """
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
                if resp.status_code in (401, 403):
                    logger.error("netbox_auth_failed path=%s status=%s", path, resp.status_code)
                    raise NetBoxUnavailable("NetBox inventory unavailable", status=resp.status_code)
                if resp.status_code == 404:
                    raise NetBoxNotFound(f"Not found in NetBox: {path}", status=404)
                if 500 <= resp.status_code < 600:
                    last_exc = NetBoxUnavailable(f"NetBox server error {resp.status_code}", status=resp.status_code)
                    if attempt < self.retries:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    raise last_exc
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                if isinstance(exc, (NetBoxUnavailable, NetBoxNotFound)):
                    raise
                last_exc = NetBoxUnavailable(f"NetBox inventory unavailable: {exc}")
                if _retry and attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise last_exc from exc
        raise last_exc

    def _all_pages(self, path, params=None, per_page=500):
        """Iterate a NetBox list endpoint, yielding rows across all pages."""
        offset = 0
        query = dict(params or {})
        query.update({"limit": per_page, "offset": offset})
        while True:
            page = self._get(path, params=query)
            for row in page.get("results", []):
                yield row
            next_url = page.get("next")
            if not next_url:
                return
            offset += per_page
            query["offset"] = offset

    # -- availability -----------------------------------------------------

    def is_available(self):
        """Return True when the NetBox status endpoint responds successfully."""
        try:
            self._get("/api/status/", _retry=False)
            return True
        except NetBoxError:
            return False

    # -- lookups -----------------------------------------------------------

    def list_device_roles(self):
        """Return all device roles as {id, name, slug, color}."""
        return list(
            {"id": r["id"], "name": r["name"], "slug": r["slug"], "color": r.get("color", "00ff00")}
            for r in self._all_pages("/api/dcim/device-roles/")
        )

    def list_sites(self):
        """Return all sites as {id, name, slug}."""
        return list(
            {"id": s["id"], "name": s["name"], "slug": s["slug"]}
            for s in self._all_pages("/api/dcim/sites/")
        )

    def list_device_types(self):
        """Return all device types including manufacturer and part id."""
        return list(
            {
                "id": t["id"],
                "model": t["model"],
                "manufacturer": (t.get("manufacturer") or {}).get("name", "")
                if isinstance(t.get("manufacturer"), dict)
                else (t.get("manufacturer_id") or ""),
                "part_number": t.get("part_id") or "",
            }
            for t in self._all_pages("/api/dcim/device-types/")
        )

    def get_device_type_interfaces(self, device_type_id):
        """Return interface template counts {type_name: count} for a model."""
        templates = list(
            self._all_pages("/api/dcim/interface-templates/", {"device_type": device_type_id})
        )
        counts = {}
        for row in templates:
            type_name = row.get("type") or "unknown"
            counts[type_name] = counts.get(type_name, 0) + 1
        return counts

    # -- devices ------------------------------------------------------------

    def list_devices(self, role=None, device_type=None, site=None, tag=None, status=None):
        """Return all devices with the given filters as raw NetBox rows."""
        params = {}
        if role:
            params["role"] = role
        if device_type:
            params["device_type"] = device_type
        if site:
            params["site"] = site
        if tag:
            params["tag"] = tag
        if status:
            params["status"] = status
        return list(self._all_pages("/api/dcim/devices/", params))

    def get_device(self, device_id):
        """Return one device by NetBox id; raises NetBoxNotFound on 404."""
        return self._get(f"/api/dcim/devices/{device_id}/")

    def list_device_interfaces(self, device_id):
        """Return the interface list for a device as raw NetBox rows."""
        return list(self._all_pages(f"/api/dcim/devices/{device_id}/interfaces/"))


def make_client(app):
    """Build a configured NetBoxClient from app settings/credentials."""
    from . import credential, settings

    base_url = settings.get_setting(app, "netbox_url") or app.config["NETBOX_URL"]
    token, _row = credential.resolve_token(app, "netbox", "NETBOX_TOKEN")
    return NetBoxClient(
        base_url,
        token,
        timeout=app.config["HTTP_TIMEOUT"],
        retries=app.config["HTTP_RETRIES"],
    )


def _map_custom_fields(device_row):
    """Extract StackHive custom fields from a NetBox device row."""
    cf = device_row.get("custom_fields") or {}
    return {
        "cloud_managed": bool(cf.get("cloud_managed", False)),
        "grafana_dashboard_uid": cf.get("grafana_dashboard") or None,
        "last_deployment": cf.get("last_deployment") or None,
    }


def _primary_ip(device_row):
    """Return the management IP (address string without prefix) from primary_ip."""
    ip = device_row.get("primary_ip")
    if isinstance(ip, dict):
        address = ip.get("address") or ""
        return address.split("/")[0]
    if isinstance(ip, str):
        return ip.split("/")[0]
    return ""


def sync_inventory(app, client=None):
    """Full NetBox inventory sync: upsert DeviceType + Device rows, flag stale.

    Returns a report dict with counts. Raises ``NetBoxUnavailable`` when the
    inventory source cannot be reached (caller surfaces 503).
    """
    from datetime import datetime, timezone

    from ..extensions import db
    from ..models import Device, DeviceType

    client = client or make_client(app)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    roles = {r["id"]: r for r in client.list_device_roles()}
    sites = {s["id"]: s for s in client.list_sites()}
    types = {t["id"]: t for t in client.list_device_types()}

    synced_types = 0
    for dt_id, dt in types.items():
        iface_counts = {}
        try:
            iface_counts = client.get_device_type_interfaces(dt_id)
        except NetBoxError:
            logger.warning("device_type_interfaces_unavailable id=%s", dt_id)
        row = (
            DeviceType.query.filter_by(netbox_id=dt_id).first()
            or DeviceType.query.filter_by(manufacturer=dt["manufacturer"], model=dt["model"]).first()
        )
        if row is None:
            row = DeviceType(netbox_id=dt_id)
            db.session.add(row)
        else:
            row.netbox_id = dt_id
        row.manufacturer = dt["manufacturer"]
        row.model = dt["model"]
        row.part_number = dt["part_number"] or row.part_number
        if iface_counts:
            row.interface_types = iface_counts
            row.interface_count = sum(iface_counts.values())
        synced_types += 1

    collisions = 0
    synced_devices = 0
    seen_netbox_ids = set()
    for raw in client.list_devices():
        netbox_id = raw["id"]
        seen_netbox_ids.add(netbox_id)
        dt_id = raw.get("device_type")
        dt_row = None
        if isinstance(dt_id, int):
            meta = types.get(dt_id)
            if meta:
                dt_row = (
                    DeviceType.query.filter_by(netbox_id=dt_id).first()
                    or DeviceType.query.filter_by(manufacturer=meta["manufacturer"], model=meta["model"]).first()
                )
        role_slug = (raw.get("role") or {}).get("slug") if isinstance(raw.get("role"), dict) else ""
        if not role_slug and isinstance(raw.get("role"), int) and raw["role"] in roles:
            role_slug = roles[raw["role"]]["slug"]
        site_name = (raw.get("site") or {}).get("name") if isinstance(raw.get("site"), dict) else ""
        if not site_name and isinstance(raw.get("site"), int) and raw["site"] in sites:
            site_name = sites[raw["site"]]["name"]
        custom = _map_custom_fields(raw)
        hostname = raw["name"]

        existing_collision = Device.query.filter(
            Device.hostname == hostname, Device.netbox_id != netbox_id
        ).first()
        if existing_collision is not None:
            collisions += 1
            logger.warning(
                "hostname_collision hostname=%s netbox_id=%s existing_id=%s",
                hostname, netbox_id, existing_collision.id,
            )
            continue

        device = Device.query.filter_by(netbox_id=netbox_id).first()
        created = device is None
        if created:
            device = Device(netbox_id=netbox_id)
            db.session.add(device)
        device.hostname = hostname
        device.serial_number = raw.get("serial") or ""
        device.mac_address = raw.get("mac_address") or ""
        device.mgmt_ip = _primary_ip(raw)
        device.role = role_slug or device.role
        device.site = site_name or device.site
        device.device_type_id = dt_row.id if dt_row else device.device_type_id
        device.cloud_managed = custom["cloud_managed"]
        if custom["grafana_dashboard_uid"]:
            device.grafana_dashboard_uid = custom["grafana_dashboard_uid"]
        device.tags = raw.get("tags") or []
        device.stale = False
        device.last_netbox_sync = now
        synced_devices += 1

    stale_flagged = 0
    for device in Device.query.filter_by(stale=False).all():
        if device.netbox_id not in seen_netbox_ids:
            device.stale = True
            stale_flagged += 1
            logger.info("device_marked_stale hostname=%s", device.hostname)

    db.session.commit()
    report = {
        "devices_synced": synced_devices,
        "types_synced": synced_types,
        "stale_flagged": stale_flagged,
        "collisions": collisions,
        "synced_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return report
