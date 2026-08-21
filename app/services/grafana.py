"""Grafana API client: device status, infra health, dashboard deep-links."""
import logging
import time

import requests

logger = logging.getLogger(__name__)


class GrafanaError(Exception):
    """Base error for Grafana integration failures."""


class GrafanaAuthError(GrafanaError):
    """Grafana rejected authentication (surfaced as 502)."""


_STATUS_MAP_DEVICE = {"OK": "up", "Alerting": "down", "Pending": "unknown"}
_STATUS_MAP_SERVICE = {"OK": "healthy", "Alerting": "down"}


class GrafanaClient:
    """Client for Grafana alert state and dashboard discovery with stale cache."""

    def __init__(self, base_url, token, timeout=10, retries=3):
        """Store endpoint, token, timeout, retries; prepare the HTTP session."""
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self._alerts_cache = None
        self._alerts_at = 0.0
        self._dashboards_cache = None
        self._dashboards_at = 0.0

    def _get(self, path, params=None, allow_stale=False, cache_key=None):
        """GET with retry on 5xx; 401/403 raise GrafanaAuthError; stale cache support."""
        last = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(
                    f"{self.base_url}{path}", params=params, timeout=self.timeout
                )
                if resp.status_code in (401, 403):
                    logger.error("grafana_auth_failed path=%s", path)
                    raise GrafanaAuthError(f"Grafana authentication failed ({resp.status_code})")
                if resp.status_code == 404:
                    return None
                if 500 <= resp.status_code < 600:
                    last = GrafanaError(f"Grafana server error {resp.status_code}")
                    if attempt < self.retries:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                if isinstance(exc, (GrafanaAuthError, GrafanaError)) and isinstance(exc, GrafanaAuthError):
                    raise
                last = GrafanaError(f"Grafana unavailable: {exc}")
                if attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
        if allow_stale and cache_key == "alerts" and self._alerts_cache is not None:
            logger.warning("grafana_stale_cache_served reason=%s", last)
            return self._alerts_cache
        raise last

    def list_alerts(self):
        """Return all alert states (cached; stale served when Grafana is down)."""
        now = time.monotonic()
        if self._alerts_cache is not None and now - self._alerts_at < 30:
            return self._alerts_cache
        data = self._get("/api/alerts/", allow_stale=True, cache_key="alerts")
        if data is not None:
            self._alerts_cache = data
            self._alerts_at = now
        return self._alerts_cache or []

    def device_statuses(self):
        """Map hostname -> up/down/unknown from alerts tagged hostname:<name>."""
        statuses = {}
        for alert in self.list_alerts():
            for tag in alert.get("tags", []):
                if tag.startswith("hostname:"):
                    hostname = tag.split(":", 1)[1].strip()
                    state = alert.get("state") or alert.get("currentState") or ""
                    statuses[hostname] = _STATUS_MAP_DEVICE.get(state, "unknown")
        return statuses

    def infrastructure_statuses(self, services):
        """Map service name -> healthy/degraded/down from service:<name> tags."""
        found = {}
        for alert in self.list_alerts():
            for tag in alert.get("tags", []):
                if tag.startswith("service:"):
                    name = tag.split(":", 1)[1].strip()
                    if name in services and name not in found:
                        state = alert.get("state") or alert.get("currentState") or ""
                        found[name] = _STATUS_MAP_SERVICE.get(state, "degraded")
        return {s: found.get(s, "degraded") for s in services}

    def search_dashboards(self):
        """Discover dashboard uids by title (cached)."""
        now = time.monotonic()
        if self._dashboards_cache is not None and now - self._dashboards_at < 300:
            return self._dashboards_cache
        data = self._get("/api/search?type=dash-db") or []
        self._dashboards_cache = {
            d.get("title"): d.get("uid") for d in data if d.get("title")
        }
        self._dashboards_at = now
        return self._dashboards_cache


def make_client(app):
    """Build a configured GrafanaClient from app settings/credentials."""
    from . import credential, settings

    base_url = settings.get_setting(app, "grafana_url") or app.config["GRAFANA_URL"]
    try:
        token, _row = credential.resolve_token(app, "grafana", "GRAFANA_TOKEN")
    except credential.CredentialError as exc:
        raise GrafanaAuthError(f"Grafana token not configured: {exc}") from exc
    return GrafanaClient(
        base_url, token, timeout=app.config["HTTP_TIMEOUT"], retries=app.config["HTTP_RETRIES"]
    )


def get_device_statuses(app):
    """Convenience: hostname -> status map (raises when Grafana is down)."""
    return make_client(app).device_statuses()


def get_infra_statuses(app, services):
    """Convenience: service -> health map (raises when Grafana is down)."""
    return make_client(app).infrastructure_statuses(services)


def device_url(app, device):
    """Deep-link URL for a device dashboard (custom UID wins)."""
    from . import settings

    base = (settings.get_setting(app, "grafana_url") or "").rstrip("/")
    uid = device.grafana_dashboard_uid or settings.get_setting(app, "device_dashboard_uid")
    parts = []
    if device.hostname:
        parts.append(f"var-hostname={device.hostname}")
    if device.mgmt_ip:
        parts.append(f"var-ip={device.mgmt_ip}")
    url = f"{base}/d/{uid}"
    if parts:
        url += "?" + "&".join(parts)
    return url


def infra_url(app):
    """Deep-link URL for the fixed infrastructure dashboard."""
    from . import settings

    base = (settings.get_setting(app, "grafana_url") or "").rstrip("/")
    return f"{base}/d/{settings.get_setting(app, 'infra_dashboard_uid')}"
