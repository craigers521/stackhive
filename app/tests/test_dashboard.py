"""Dashboard summary: device health, deployments, and active ZTP count."""
import pytest

DAY0 = "hostname sw-x\n!\n"


@pytest.fixture
def fake_ztp_render(monkeypatch):
    """Stub the ZTP day-0 renderer to a fixed config."""
    from app.services import ztp as ztp_service

    monkeypatch.setattr(ztp_service, "generate_ztp_app", lambda app, device, profile: DAY0)


class TestDashboardSummary:
    """Dashboard summary payload: device health and active ZTP count."""
    def test_landing_redirects_unauthenticated(self, client):
        """Unauthenticated visitors are redirected to the login page."""
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_summary_counts_device_health(self, client):
        """device_health counts up/down/total from the seeded devices."""
        from conftest import login

        login(client, "viewer")
        data = client.get("/api/dashboard").get_json()
        assert data["device_health"] == {"up": 1, "down": 1, "total": 3}
        assert data["ztp"]["active"] == 0

    def test_summary_active_ztp_counts_only_live_states(self, client, fake_ztp_render):
        """ztp.active counts live provisions only; onboarding drops the count."""
        from conftest import login
        from app.extensions import db
        from app.models import Device, ZTPProvision

        login(client, "editor")
        assert (
            client.post(
                "/api/profiles",
                json={"name": "acc-dash", "device_role": "access-switch"},
            ).status_code
            == 201
        )
        profile = client.get("/api/profiles").get_json()["profiles"][0]
        resp = client.post(
            "/api/onboarding/ztp",
            json={
                "device_id": 102,
                "serial": "FCW2345A002",
                "hostname": "sw-access-02",
                "profile_id": profile["id"],
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)

        login(client, "viewer")
        assert client.get("/api/dashboard").get_json()["ztp"]["active"] == 1

        client.get("/ztp/FCW2345A002.cfg")  # fetch marks delivered (still active)
        assert client.get("/api/dashboard").get_json()["ztp"]["active"] == 1

        device = Device.query.filter_by(netbox_id=102).one()
        device.monitoring_status = "up"
        db.session.commit()
        from app.services import refresh

        refresh.refresh_ztp_onboarding(client.application)
        assert ZTPProvision.query.one().status == "onboarded"
        assert client.get("/api/dashboard").get_json()["ztp"]["active"] == 0

    def test_landing_page_renders_onboarding_card(self, client):
        """The landing page renders the Onboarding card."""
        from conftest import login

        login(client, "viewer")
        page = client.get("/").get_data(as_text=True)
        assert "Onboarding" in page
