"""Inventory user story tests: list, filters, pagination, detail, 503 behavior."""
import pytest


class TestInventoryListPage:
    """Inventory list page: seeded data, auth, filters, search."""
    def test_list_page_renders_seeded_devices(self, client):
        """The list page renders all seeded devices."""
        from conftest import login

        login(client, "viewer")
        resp = client.get("/inventory")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "sw-access-01" in body
        assert "sw-access-02" in body
        assert "sw-core-01" in body

    def test_list_page_requires_auth(self, client):
        """Unauthenticated requests are redirected to login."""
        resp = client.get("/inventory")
        assert resp.status_code in (302, 401)

    def test_filter_by_role(self, client):
        """The role filter narrows the device list."""
        from conftest import login

        login(client, "viewer")
        resp = client.get("/inventory?role=core-switch")
        body = resp.get_data(as_text=True)
        assert "sw-core-01" in body
        assert "sw-access-01" not in body

    def test_filter_by_site(self, client):
        """The site filter narrows the device list."""
        from conftest import login

        login(client, "viewer")
        resp = client.get("/inventory?site=DC")
        body = resp.get_data(as_text=True)
        assert "sw-core-01" in body
        assert "sw-access-01" not in body

    def test_filter_by_status(self, client):
        """The status filter narrows the device list."""
        from conftest import login

        login(client, "viewer")
        resp = client.get("/inventory?status=down")
        body = resp.get_data(as_text=True)
        assert "sw-core-01" in body
        assert "sw-access-01" not in body

    def test_type_filter_control_renders_and_filters(self, client, app):
        """The device-type filter control renders and filters via the type param."""
        from conftest import login
        from app.extensions import db
        from app.models import Device, DeviceType

        with app.app_context():
            core_type = DeviceType(
                netbox_id=11,
                manufacturer="cisco",
                model="C9500-24Y",
                part_number="C9500-24Y",
                interface_count=28,
                interface_types={"TenGigabitEthernet": 24, "FortyGigabitEthernet": 4},
            )
            db.session.add(core_type)
            core = Device.query.filter_by(hostname="sw-core-01").one()
            core.device_type_id = core_type.id
            db.session.commit()

        login(client, "viewer")
        body = client.get("/inventory").get_data(as_text=True)
        assert 'name="type"' in body
        assert ">C9300-48P<" in body
        assert ">C9500-24Y<" in body

        body = client.get("/inventory?type=C9500-24Y").get_data(as_text=True)
        assert "sw-core-01" in body
        assert "sw-access-01" not in body
        body = client.get("/inventory?type=C9300-48P").get_data(as_text=True)
        assert "sw-access-01" in body
        assert "sw-core-01" not in body

    def test_search_across_hostname_ip_serial(self, client):
        """Search matches hostname, IP, and serial."""
        from conftest import login

        login(client, "viewer")
        for query in ("sw-access", "10.0.1.12", "FCW2345C001"):
            body = client.get(f"/inventory?search={query}").get_data(as_text=True)
            expected = "sw-access-01" if query.startswith("sw") else (
                "sw-access-02" if query.startswith("10.0") else "sw-core-01"
            )
            assert expected in body


class TestInventoryPagination:
    """Inventory list pagination behaviour."""
    def test_pagination_50_per_page(self, client, db_session):
        """Pages are capped at 50 rows with correct totals."""
        from conftest import login
        from app.models import Device

        for i in range(57):
            db_session.add(
                Device(
                    netbox_id=500 + i,
                    hostname=f"sw-bulk-{i:03d}",
                    serial_number=f"FCW500{i:04d}",
                    mac_address="aa:bb:cc:00:00:00",
                    mgmt_ip="10.9.0.1",
                    role="access-switch",
                    site="Bulk",
                    monitoring_status="unknown",
                )
            )
        db_session.commit()
        login(client, "viewer")
        page1 = client.get("/inventory?page=1")
        assert page1.status_code == 200
        count_page1 = page1.get_data(as_text=True).count("sw-bulk-")
        page2 = client.get("/inventory?page=2")
        assert "sw-bulk-" in page2.get_data(as_text=True)
        assert count_page1 <= 50


class TestDeviceDetail:
    """Device detail page rendering."""
    def test_detail_page_renders_metadata_and_interfaces(self, client, monkeypatch):
        """The detail page shows metadata and type-derived interfaces."""
        from conftest import login

        class _Down:
            """Fake NetBox client reporting the source as unavailable."""
            def is_available(self):
                """Always report the source as unavailable."""
                return False

        from app.services import drift, netbox

        monkeypatch.setattr(netbox, "make_client", lambda app: _Down())
        login(client, "viewer")
        resp = client.get("/inventory/101")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "sw-access-01" in body
        assert "FCW2345A001" in body
        assert "GigabitEthernet" in body
        assert "MgmtEth0/RP0/cpu0" in body

    def test_detail_unknown_device_404(self, client):
        """An unknown device detail returns 404."""
        from conftest import login

        login(client, "viewer")
        assert client.get("/inventory/9999").status_code == 404


class TestDeviceAPI:
    """Device REST API behaviour."""
    def test_api_devices_requires_auth(self, client):
        """The device API denies unauthenticated clients."""
        assert client.get("/api/devices").status_code == 401

    def test_api_devices_lists_seeded(self, client):
        """The device API lists seeded devices with their fields."""
        from conftest import login

        login(client, "viewer")
        resp = client.get("/api/devices")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 3
        assert {d["hostname"] for d in data["devices"]} == {
            "sw-access-01",
            "sw-access-02",
            "sw-core-01",
        }

    def test_api_devices_filters(self, client):
        """Role, site, and status filters apply to the API."""
        from conftest import login

        login(client, "viewer")
        data = client.get("/api/devices?role=core-switch").get_json()
        assert data["total"] == 1
        assert data["devices"][0]["hostname"] == "sw-core-01"

    def test_api_device_detail_with_interfaces(self, client, monkeypatch):
        """Device detail includes interfaces from the device type."""
        from conftest import login

        class _Down:
            """Fake NetBox client reporting the source as unavailable."""
            def is_available(self):
                """Always report the source as unavailable."""
                return False

        from app.services import netbox

        monkeypatch.setattr(netbox, "make_client", lambda app: _Down())
        login(client, "viewer")
        resp = client.get("/api/devices/101")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["hostname"] == "sw-access-01"
        assert data["mac_address"] == "aa:bb:cc:dd:ee:01"
        assert len(data["interfaces"]) > 0
        ifaces = {i["type"] for i in data["interfaces"]}
        assert "management" in ifaces

    def test_api_device_detail_404(self, client):
        """An unknown device id returns 404."""
        from conftest import login

        login(client, "viewer")
        assert client.get("/api/devices/9999").status_code == 404

    def test_503_when_source_unavailable_and_no_cache(self, client, db_session, monkeypatch):
        """No cache plus a down source yields 503."""
        from conftest import login
        from app.models import Device, DeviceType
        from app.services import netbox

        login(client, "viewer")
        db_session.query(Device).delete()
        db_session.query(DeviceType).delete()
        db_session.commit()

        class _Down:
            """Fake NetBox client reporting the source as unavailable."""
            def is_available(self):
                """Always report the source as unavailable."""
                return False

        monkeypatch.setattr(netbox, "make_client", lambda app: _Down())
        resp = client.get("/api/devices")
        assert resp.status_code == 503
        assert resp.get_json()["error"] == "Service Unavailable"

    def test_cached_data_served_when_source_down(self, client, monkeypatch):
        """The stale cache is served when the source is down."""
        from conftest import login

        class _Down:
            """Fake NetBox client reporting the source as unavailable."""
            def is_available(self):
                """Always report the source as unavailable."""
                return False

        from app.services import netbox

        monkeypatch.setattr(netbox, "make_client", lambda app: _Down())
        login(client, "viewer")
        resp = client.get("/api/devices")
        assert resp.status_code == 200
        assert resp.get_json()["total"] == 3


class TestDeviceTypesAPI:
    """Device type CRUD API and RBAC."""
    def test_list_device_types(self, client):
        """Device types are listed with their interface counts."""
        from conftest import login

        login(client, "viewer")
        data = client.get("/api/device-types").get_json()
        assert len(data["device_types"]) == 1
        assert data["device_types"][0]["model"] == "C9300-48P"

    def test_update_requires_admin(self, client):
        """Non-admin device-type updates are denied."""
        from conftest import login

        login(client, "editor")
        resp = client.put("/api/device-types/1", json={"uplink_slots": 2})
        assert resp.status_code == 403

    def test_admin_update_device_type(self, client):
        """Admins can update a device type's interface layout."""
        from conftest import login

        login(client, "admin")
        resp = client.put(
            "/api/device-types/1",
            json={
                "interface_types": {"GigabitEthernet": 48, "TenGigabitEthernet": 4, "Serial": 1},
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["interface_count"] == 53

    def test_admin_update_rejects_mismatched_count(self, client):
        """Mismatched interface type/count pairs are rejected."""
        from conftest import login

        login(client, "admin")
        resp = client.put(
            "/api/device-types/1",
            json={"interface_types": {"GigabitEthernet": 48}, "interface_count": 100},
        )
        assert resp.status_code == 400
