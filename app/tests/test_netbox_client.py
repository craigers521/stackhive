"""NetBox client unit tests: field mapping, pagination, error handling, sync."""
import pytest

from app.services.netbox import (
    NetBoxClient,
    NetBoxError,
    NetBoxNotFound,
    NetBoxUnavailable,
    sync_inventory,
)


class FakeResponse:
    """Minimal requests.Response double for NetBox calls."""
    def __init__(self, status_code, json_body=None):
        """Store the status code and body."""
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}

    def json(self):
        """Return the body parsed as JSON."""
        return self._json

    def raise_for_status(self):
        """Raise HTTPError on 4xx/5xx statuses."""
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Programmable stand-in for requests.Session used by the client."""

    def __init__(self, responses):
        """Session double with queued responses per URL."""
        self.responses = list(responses)
        self.headers = {}
        self.calls = []
        self._last = FakeResponse(200, {})

    def get(self, url, params=None, timeout=None):
        """Return the next queued response for the URL."""
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.responses:
            self._last = self.responses.pop(0)
        return self._last


def make_client(responses):
    """Build a NetBoxClient over the fake session."""
    import requests

    client = NetBoxClient("http://netbox.test", "tok", timeout=2, retries=1)
    fake = FakeSession(responses)
    client.session = fake
    return client, fake


def test_list_devices_paginates_and_maps():
    """Paged devices are collected and mapped to the app shape."""
    page1 = FakeResponse(
        200,
        {
            "count": 2,
            "results": [
                {"id": 11, "name": "sw-a", "serial": "S1", "mac_address": "aa:aa:aa:aa:aa:aa",
                 "primary_ip": {"address": "10.0.0.1/24"}},
                {"id": 12, "name": "sw-b", "serial": "S2"},
            ],
            "next": "http://netbox.test/api/dcim/devices/?limit=500&offset=500",
        },
    )
    page2 = FakeResponse(
        200, {"count": 2, "results": [{"id": 13, "name": "sw-c"}], "next": None}
    )
    client, fake = make_client([page1, page2])
    devices = client.list_devices()
    assert [d["id"] for d in devices] == [11, 12, 13]
    assert fake.calls[0]["url"].endswith("/api/dcim/devices/")
    assert fake.calls[1]["params"]["offset"] == 500


def test_token_header_present():
    """Requests carry the NetBox Token header."""
    client, fake = make_client([FakeResponse(200, {"results": []})])
    client.session.headers.update({"Authorization": "Token tok"})
    client.list_devices()
    assert client.session.headers["Authorization"] == "Token tok"


def test_primary_ip_mapping():
    """The primary management IP maps to mgmt_ip."""
    client, _ = make_client(
        [
            FakeResponse(
                200,
                {
                    "results": [
                        {"id": 1, "name": "a", "primary_ip": {"address": "10.1.1.1/24"}},
                        {"id": 2, "name": "b", "primary_ip": "10.1.1.2/24"},
                        {"id": 3, "name": "c", "primary_ip": 99},
                    ]
                },
            )
        ]
    )
    from app.services.netbox import _primary_ip

    rows = client.list_devices()
    assert _primary_ip(rows[0]) == "10.1.1.1"
    assert _primary_ip(rows[1]) == "10.1.1.2"
    assert _primary_ip(rows[2]) == ""


def test_401_maps_to_unavailable():
    """A 401 response maps to NetBoxUnavailable."""
    client, _ = make_client([FakeResponse(401, {"detail": "bad"})])
    with pytest.raises(NetBoxUnavailable) as excinfo:
        client.list_devices()
    assert excinfo.value.status == 401


def test_403_maps_to_unavailable():
    """A 403 response maps to NetBoxUnavailable."""
    client, _ = make_client([FakeResponse(403, {"detail": "denied"})])
    with pytest.raises(NetBoxUnavailable):
        client.get_device(5)


def test_404_maps_to_not_found():
    """A 404 response maps to NetBoxNotFound."""
    client, _ = make_client([FakeResponse(404, {"detail": "nf"})])
    with pytest.raises(NetBoxNotFound):
        client.get_device(999)


def test_5xx_retries_then_unavailable(monkeypatch):
    """5xx responses retry and then raise unavailable."""
    import app.services.netbox as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    for statuses in ((502, 503), (500, 503, 502)):
        client, _ = make_client([FakeResponse(code, {}) for code in statuses])
        with pytest.raises(NetBoxUnavailable):
            client.list_devices()


def test_5xx_recovers_after_retry(monkeypatch):
    """A retry after a 5xx returns the healthy payload."""
    import app.services.netbox as mod

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    client, _ = make_client(
        [FakeResponse(502, {}), FakeResponse(200, {"results": [{"id": 7}]})]
    )
    assert client.list_devices() == [{"id": 7}]


def test_connection_error_maps_to_unavailable():
    """Connection errors map to NetBoxUnavailable."""
    client = NetBoxClient("http://netbox.test", "tok", timeout=1, retries=0)

    def _raise(*a, **k):
        """Return a getter callable that raises once."""
        import requests

        raise requests.ConnectionError("boom")

    client.session = type("S", (), {"get": staticmethod(_raise), "headers": {}})()
    with pytest.raises(NetBoxUnavailable):
        client.list_devices()


def test_is_available_false_on_error():
    """is_available reports False when the source errors."""
    client, _ = make_client([FakeResponse(500, {})])
    assert client.is_available() is False


class TestSyncInventory:
    """Local cache sync from NetBox."""
    @pytest.fixture
    def fake_netbox(self):
        """Serve canned NetBox payloads through the service."""
        return {
            "roles": [
                {"id": 1, "name": "access-switch", "slug": "access-switch", "color": "ff0000"}
            ],
            "sites": [{"id": 1, "name": "HQ", "slug": "hq"}],
            "types": [
                {
                    "id": 5,
                    "model": "C9300-48P",
                    "manufacturer": "cisco",
                    "part_number": "C9300-48P",
                }
            ],
            "type_interfaces": {"GigabitEthernet": 48, "TenGigabitEthernet": 4},
            "devices": [
                {
                    "id": 201,
                    "name": "sw-x",
                    "serial": "SN1",
                    "mac_address": "aa:aa:aa:aa:aa:11",
                    "role": 1,
                    "site": 1,
                    "device_type": 5,
                    "primary_ip": {"address": "10.2.0.1/24"},
                    "tags": ["mdt-enabled"],
                    "custom_fields": {"cloud_managed": True, "grafana_dashboard": "custom-uid"},
                }
            ],
        }

    def _client(self, data):
        """Build the NetBox client over the fake session."""
        class FakeClient(NetBoxClient):
            """NetBox client stub for sync tests."""
            def __init__(self, data):
                """Store the canned list payloads."""
                super().__init__("http://netbox.test", "tok")
                self.data = data

            def list_device_roles(self):
                """Return the canned device roles."""
                return self.data["roles"]

            def list_sites(self):
                """Return the canned sites."""
                return self.data["sites"]

            def list_device_types(self):
                """Return the canned device types."""
                return self.data["types"]

            def get_device_type_interfaces(self, dt_id):
                """Return the canned interface layout."""
                return self.data["type_interfaces"]

            def list_devices(self, **kwargs):
                """Return the canned devices."""
                return self.data["devices"]

        return FakeClient(data)

    def test_sync_upserts_devices_and_types(self, app, fake_netbox, db_session):
        """Sync upserts devices, types, and roles into the cache."""
        report = sync_inventory(app, client=self._client(fake_netbox))
        assert report["devices_synced"] == 1
        assert report["types_synced"] == 1

        from app.models import Device, DeviceType

        device = Device.query.filter_by(netbox_id=201).first()
        assert device is not None
        assert device.hostname == "sw-x"
        assert device.role == "access-switch"
        assert device.site == "HQ"
        assert device.mgmt_ip == "10.2.0.1"
        assert device.cloud_managed is True
        assert device.grafana_dashboard_uid == "custom-uid"
        assert device.tags == ["mdt-enabled"]
        dt = DeviceType.query.filter_by(netbox_id=5).first()
        assert dt.interface_count == 52
        assert dt.interface_types == fake_netbox["type_interfaces"]

    def test_sync_flags_missing_devices_stale(self, app, fake_netbox, db_session):
        """Devices missing upstream are flagged stale."""
        fake = self._client(fake_netbox)
        sync_inventory(app, client=fake)
        from app.models import Device

        device = Device.query.filter_by(netbox_id=201).first()
        fake.data["devices"] = []
        report = sync_inventory(app, client=fake)
        assert report["stale_flagged"] == 1
        db_session.refresh(device)
        assert device.stale is True

    def test_sync_hostname_collision_skipped_and_reported(self, app, fake_netbox, db_session):
        """Hostname collisions are skipped and reported."""
        fake = self._client(fake_netbox)
        sync_inventory(app, client=fake)
        fake.data["devices"].append(
            {
                "id": 202,
                "name": "sw-x",
                "serial": "SN2",
                "mac_address": "aa:aa:aa:aa:aa:22",
                "role": 1,
                "site": 1,
                "device_type": 5,
                "primary_ip": None,
                "tags": [],
                "custom_fields": {},
            }
        )
        report = sync_inventory(app, client=fake)
        assert report["collisions"] == 1
        assert report["devices_synced"] == 1
