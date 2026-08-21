"""Monitoring user story tests: Grafana mapping, deep-links, stale cache, 502."""
import pytest

ALERTS = [
    {"id": 1, "state": "OK", "tags": ["hostname:sw-access-01"]},
    {"id": 2, "state": "Alerting", "tags": ["hostname:sw-core-01", "service:netbox"]},
    {"id": 3, "state": "Pending", "tags": ["hostname:sw-access-02"]},
    {"id": 4, "state": "OK", "tags": ["service:gitlab"]},
    {"id": 5, "state": "Alerting", "tags": ["service:influxdb"]},
]


class FakeGrafanaClient:
    """Grafana stub with canned status payloads."""
    def __init__(self, alerts=None, raise_exc=None):
        """Store the canned device and infrastructure statuses."""
        self._alerts = alerts
        self.raise_exc = raise_exc

    def device_statuses(self):
        """Return the canned device statuses."""
        if self.raise_exc:
            raise self.raise_exc
        return {}

    def infrastructure_statuses(self, services):
        """Return the canned infrastructure statuses."""
        if self.raise_exc:
            raise self.raise_exc
        return {}


class _ScriptedGrafanaSession:
    """requests.Session double: scripted alerts payload, then availability switch."""

    def __init__(self, alerts):
        """requests session double returning queued responses."""
        self._alerts = alerts

    def get(self, url, params=None, timeout=None):
        """Pop and return the next queued response."""
        resp = _Resp(200, self._alerts)
        return resp


class _Resp:
    """Minimal response object with a json method."""
    def __init__(self, status_code, json_body):
        """Store the JSON payload."""
        self.status_code = status_code
        self._json = json_body

    def json(self):
        """Return the stored payload."""
        return self._json

    def raise_for_status(self):
        """No-op status raising."""
        pass


@pytest.fixture
def grafana_down(monkeypatch):
    """Make the Grafana service report itself unavailable."""
    from app.services import grafana

    class _Down:
        """Grafana client stub that is always unavailable."""
        def __init__(self):
            """No state."""
            self.session = None

        def device_statuses(self):
            """Raise the unavailable error for device statuses."""
            raise grafana.GrafanaError("grafana down")

        def infrastructure_statuses(self, services):
            """Raise the unavailable error for infrastructure statuses."""
            raise grafana.GrafanaError("grafana down")

    monkeypatch.setattr(grafana, "make_client", lambda app: _Down())
    return _Down


def test_device_status_mapping(app):
    """Grafana alert states map to up, down, degraded, unknown."""
    from app.services.grafana import GrafanaClient

    client = GrafanaClient("http://grafana.test", "tok", timeout=2, retries=0)
    client.session = _ScriptedGrafanaSession(ALERTS)
    statuses = client.device_statuses()
    assert statuses["sw-access-01"] == "up"
    assert statuses["sw-core-01"] == "down"
    assert statuses["sw-access-02"] == "unknown"


def test_infra_status_mapping(app):
    """Service health states map to up, down, unknown."""
    from app.services.grafana import GrafanaClient

    client = GrafanaClient("http://grafana.test", "tok", timeout=2, retries=0)
    client.session = _ScriptedGrafanaSession(ALERTS)
    statuses = client.infrastructure_statuses(["netbox", "gitlab", "influxdb"])
    assert statuses["netbox"] == "down"
    assert statuses["gitlab"] == "healthy"
    assert statuses["influxdb"] == "down"


def test_device_url_with_var_hostname_and_ip(app):
    """Deep links embed the hostname and IP dashboard variables."""
    from app.models import Device
    from app.services import grafana

    device = Device.query.filter_by(netbox_id=101).one()
    url = grafana.device_url(app, device)
    assert url.startswith("http://localhost:8080/grafana/d/")
    assert "var-hostname=sw-access-01" in url
    assert "var-ip=10.0.1.11/24" in url


def test_device_url_custom_uid_precedence(app):
    """A custom dashboard UID wins over the default."""
    from app.models import Device
    from app.services import grafana

    device = Device.query.filter_by(netbox_id=101).one()
    device.grafana_dashboard_uid = "my-custom-uid"
    url = grafana.device_url(app, device)
    assert "/d/my-custom-uid" in url


def test_stale_cache_served_when_grafana_unreachable(app):
    """The last good payload is served while Grafana is down."""
    from app.services.grafana import GrafanaClient

    client = GrafanaClient("http://grafana.test", "tok", timeout=2, retries=0)
    good = _ScriptedGrafanaSession(ALERTS)
    client.session = good
    assert client.device_statuses()["sw-access-01"] == "up"

    import requests

    class _BrokenSession:
        """Session whose requests raise connection errors."""
        def get(self, url, params=None, timeout=None):
            """Raise a connection error on GET."""
            raise requests.ConnectionError("boom")

    client.session = _BrokenSession()
    # force the cache to be stale so a fresh fetch is attempted
    client._alerts_at = 0.0
    # but the stale cache is only ~30s old here; simulate age
    client._alerts_at = client._alerts_at - 60
    statuses = client.device_statuses()
    assert statuses["sw-core-01"] == "down"


def test_502_on_grafana_auth_error(client, monkeypatch):
    """Grafana authentication failures surface as 502."""
    from conftest import login
    from app.services import grafana

    class _AuthFailed:
        """Grafana client stub failing authentication."""
        def device_statuses(self):
            """Raise the authentication-failure error."""
            raise grafana.GrafanaAuthError("bad token")

        def infrastructure_statuses(self, services):
            """Raise the authentication-failure error."""
            raise grafana.GrafanaAuthError("bad token")

    monkeypatch.setattr(grafana, "make_client", lambda app: _AuthFailed())
    login(client, "viewer")
    resp = client.get("/api/monitoring/infrastructure")
    assert resp.status_code == 502


def test_monitoring_page_and_api_with_db_statuses(client, db_session):
    """The page and API fall back to DB statuses without Grafana."""
    from conftest import login

    login(client, "viewer")
    resp = client.get("/monitoring")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "sw-access-01" in body

    data = client.get("/api/monitoring/devices").get_json()
    hosts = {d["hostname"]: d for d in data["devices"]}
    assert hosts["sw-core-01"]["status"] == "down"  # from Device.monitoring_status
    assert hosts["sw-access-01"]["status"] == "up"

    data = client.get("/api/monitoring/devices?role=core-switch").get_json()
    assert [d["hostname"] for d in data["devices"]] == ["sw-core-01"]


def test_monitoring_api_requires_viewer(client):
    """The monitoring API denies unauthenticated clients."""
    resp = client.get("/api/monitoring/devices")
    assert resp.status_code == 401


def test_grafana_reachable_overrides_db(app, client, monkeypatch):
    """Live Grafana state wins over the last-known DB status."""
    from conftest import login
    from app.services import grafana

    class _Live:
        """Grafana client stub returning canned live statuses."""
        def device_statuses(self):
            """Return the canned device statuses."""
            return {"sw-access-01": "down"}

        def infrastructure_statuses(self, services):
            """Return the canned infrastructure statuses."""
            return {s: "healthy" for s in services}

    monkeypatch.setattr(grafana, "make_client", lambda app: _Live())
    login(client, "viewer")
    data = client.get("/api/monitoring/devices").get_json()
    hosts = {d["hostname"]: d for d in data["devices"]}
    assert hosts["sw-access-01"]["status"] == "down"  # DB said up, live says down
    infra = client.get("/api/monitoring/infrastructure").get_json()
    assert {s["name"]: s["status"] for s in infra["services"]}["netbox"] == "healthy"
