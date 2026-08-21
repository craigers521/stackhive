"""ZTP tests: public artifact serving, rate limiting, Meraki, lifecycle."""
import pytest

DAY0 = "hostname sw-new-01\n!\n! day-0 config\n"


@pytest.fixture
def fake_ztp_render(monkeypatch):
    """Stub the ZTP day-0 renderer to a fixed config."""
    from app.services import ztp as ztp_service

    monkeypatch.setattr(ztp_service, "generate_ztp_app", lambda app, device, profile: DAY0)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset the module rate-limit state around each test."""
    from app.routes import ztp as ztp_routes

    ztp_routes._rate_hits.clear()
    yield
    ztp_routes._rate_hits.clear()


def _make_profile(client, name="access-leaf", role="access-switch"):
    """Create a profile via the API and return its ORM row."""
    from conftest import login
    from app.models import ConfigurationProfile

    login(client, "editor")
    resp = client.post("/api/profiles", json={"name": name, "device_role": role})
    assert resp.status_code == 201
    return ConfigurationProfile.query.filter_by(name=name).one()


def _create_provision(
    client, device_netbox_id, fake_ztp_render, is_meraki=False, profile_name="access-leaf", role="access-switch"
):
    """Create a ZTP provision for a device via the API."""
    from conftest import login
    from app.models import Device

    login(client, "editor")
    device = Device.query.filter_by(netbox_id=device_netbox_id).one()
    profile = _make_profile(client, profile_name, role)
    if is_meraki:
        resp = client.post(
            "/api/onboarding/meraki",
            json={
                "device_id": device.netbox_id,
                "serial": device.serial_number,
                "hostname": device.hostname,
                "network_id": "N-12345",
            },
        )
    else:
        resp = client.post(
            "/api/onboarding/ztp",
            json={
                "device_id": device.netbox_id,
                "serial": device.serial_number,
                "hostname": device.hostname,
                "profile_id": profile.id,
            },
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return device, resp.get_json()


class TestZtpServing:
    """Public ZTP artifact endpoints and rate limiting."""
    def test_script_served_unauthenticated_with_source_command(self, client, fake_ztp_render):
        """The boot script is public and sources the day-0 config."""
        from app.models import Device

        device, data = _create_provision(client, 102, fake_ztp_render)
        serial = device.serial_number

        resp = client.get(f"/ztp/{serial}.txt")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert f"source http://stackhive.local/ztp/{serial}.cfg" in body

    def test_day0_config_served(self, client, fake_ztp_render):
        """The day-0 config is served publicly."""
        from app.models import Device

        device, data = _create_provision(client, 102, fake_ztp_render)
        resp = client.get(f"/ztp/{device.serial_number}.cfg")
        assert resp.status_code == 200
        assert "day-0 config" in resp.get_data(as_text=True)

    def test_startup_config_fallback_endpoint(self, client, fake_ztp_render):
        """The startup-config fallback path serves the config."""
        from app.models import Device

        device, _ = _create_provision(client, 102, fake_ztp_render)
        resp = client.get(f"/ztp/{device.serial_number}/startup-config.conf")
        assert resp.status_code == 200
        assert "day-0 config" in resp.get_data(as_text=True)

    def test_image_list_endpoint(self, client, fake_ztp_render):
        """The image list endpoint serves the placeholder."""
        from app.models import Device

        device, _ = _create_provision(client, 102, fake_ztp_render)
        resp = client.get(f"/ztp/{device.serial_number}/image-list.txt")
        assert resp.status_code == 200

    def test_unknown_serial_404(self, client):
        """Unknown serials return 404."""
        assert client.get("/ztp/FCW00000NONE.txt").status_code == 404
        assert client.get("/ztp/FCW00000NONE.cfg").status_code == 404

    def test_fetch_marks_provision_delivered(self, client, fake_ztp_render):
        """Fetching the config transitions the provision to delivered."""
        from app.models import Device, ZTPProvision

        device, _ = _create_provision(client, 102, fake_ztp_render)
        provision = ZTPProvision.query.one()
        assert provision.status == "generated"
        client.get(f"/ztp/{device.serial_number}.cfg")
        assert ZTPProvision.query.one().status == "delivered"

    def test_rate_limit_429_on_rapid_unauthenticated_fetches(self, client, fake_ztp_render):
        """A burst past the budget gets 429 with Retry-After."""
        from app.routes import ztp as ztp_routes

        device, _ = _create_provision(client, 102, fake_ztp_render)
        url = f"/ztp/{device.serial_number}.cfg"
        codes = [client.get(url).status_code for _ in range(ztp_routes.RATE_LIMIT_PER_MINUTE + 2)]
        assert codes[: ztp_routes.RATE_LIMIT_PER_MINUTE] == [200] * ztp_routes.RATE_LIMIT_PER_MINUTE
        assert 429 in codes
        limited = [c for c in codes if c == 429][0:1]
        resp = client.get(url)
        assert resp.headers.get("Retry-After")


class TestZtpLifecycle:
    """Provision state transitions up to onboarded."""
    def test_onboarded_when_device_reports_up(self, client, fake_ztp_render, db_session):
        """A delivered provision becomes onboarded when the device reports up."""
        from app.models import Device, ZTPProvision
        from app.services import refresh

        device, _ = _create_provision(client, 102, fake_ztp_render)
        provision = ZTPProvision.query.one()
        client.get(f"/ztp/{device.serial_number}.cfg")  # delivered
        db_session.refresh(device)
        device.monitoring_status = "up"
        db_session.commit()

        refresh.refresh_ztp_onboarding(client.application)

        db_session.refresh(provision)
        db_session.refresh(device)
        assert provision.status == "onboarded"
        assert device.config_status == "onboarded"

    def test_render_failure_422(self, client, monkeypatch):
        """A render failure returns 422."""
        from conftest import login
        from app.models import ConfigurationProfile, Device
        from app.services import ztp as ztp_service

        def _boom(app, device, profile):
            """Renderer stub that raises ZtpError."""
            raise ztp_service.ZtpError("ztp render failed (rc=2): bad template")

        monkeypatch.setattr(ztp_service, "generate_ztp_app", _boom)
        login(client, "editor")
        profile = _make_profile(client)
        device = Device.query.filter_by(netbox_id=102).one()
        resp = client.post(
            "/api/onboarding/ztp",
            json={
                "device_id": device.netbox_id,
                "serial": device.serial_number,
                "hostname": device.hostname,
                "profile_id": profile.id,
            },
        )
        assert resp.status_code == 422

    def test_existing_provision_conflicts(self, client, fake_ztp_render):
        """A second provision for the same device returns 409."""
        from conftest import login
        from app.models import ConfigurationProfile, Device

        device, _ = _create_provision(client, 102, fake_ztp_render)
        login(client, "editor")
        profile = ConfigurationProfile.query.filter_by(name="access-leaf").one()
        resp = client.post(
            "/api/onboarding/ztp",
            json={
                "device_id": device.netbox_id,
                "serial": device.serial_number,
                "hostname": device.hostname,
                "profile_id": profile.id,
            },
        )
        assert resp.status_code == 409

    @pytest.fixture
    def no_meraki_api(self, monkeypatch, client):
        """Stub the Meraki client so tests never hit the network."""
        from app.services import meraki as meraki_service

        def _unavailable(app):
            """Stub raising MerakiUnavailable (best-effort reservation path)."""
            raise meraki_service.MerakiUnavailable("not configured in tests")

        monkeypatch.setattr(meraki_service, "make_meraki_client", _unavailable)
        monkeypatch.setenv("MERAKI_API_KEY", "test-meraki-api-key")
        client.application.config["MERAKI_ORGANIZATION_ID"] = "86123"

    def test_meraki_provision_cfg_contains_meraki_block(self, client, fake_ztp_render, no_meraki_api, db_session):
        """A Meraki provision embeds the day-0 mdt controller commands and flags the device cloud-managed."""
        from app.models import Device, ZTPProvision

        device, data = _create_provision(client, 102, fake_ztp_render, is_meraki=True)
        assert data["status"] == "generated"
        provision = ZTPProvision.query.one()
        assert provision.is_meraki is True
        content = provision.config_content
        assert "mdt controller name meraki" in content
        assert "mdt controller meraki" in content
        assert "license key test-meraki-api-key" in content
        assert "organization id 86123" in content
        assert "network id N-12345" in content
        assert "dashboard url https://n2c.meraki.com" in content
        assert "day-0 config" in content
        db_session.refresh(device)
        assert device.cloud_managed is True

    def test_meraki_provision_fails_without_credentials(self, client, fake_ztp_render, no_meraki_api, monkeypatch):
        """A Meraki provision without an API key or org id returns 422."""
        from conftest import login
        from app.models import Device

        monkeypatch.delenv("MERAKI_API_KEY", raising=False)
        client.application.config.pop("MERAKI_ORGANIZATION_ID", None)
        client.application.config.pop("MERAKI_ORG_ID", None)

        login(client, "editor")
        profile = _make_profile(client)
        device = Device.query.filter_by(netbox_id=102).one()
        resp = client.post(
            "/api/onboarding/meraki",
            json={
                "device_id": device.netbox_id,
                "serial": device.serial_number,
                "hostname": device.hostname,
                "network_id": "N-12345",
            },
        )
        assert resp.status_code == 422
        db_device = Device.query.filter_by(netbox_id=102).one()
        assert db_device.cloud_managed in (False, None)

    def test_meraki_dashboard_url_override(self, client, fake_ztp_render, no_meraki_api, db_session):
        """A dashboard_url override lands in the day-0 mdt controller block."""
        from conftest import login
        from app.models import ConfigurationProfile, Device, ZTPProvision

        login(client, "editor")
        profile = _make_profile(client)
        device = Device.query.filter_by(netbox_id=102).one()
        resp = client.post(
            "/api/onboarding/meraki",
            json={
                "device_id": device.netbox_id,
                "serial": device.serial_number,
                "hostname": device.hostname,
                "network_id": "N-99999",
                "dashboard_url": "https://myorg.meraki.com",
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        provision = ZTPProvision.query.one()
        assert "dashboard url https://myorg.meraki.com" in provision.config_content


class _FakeGit:
    """Records push commits for cleanup assertions."""

    def __init__(self):
        """Store the list of recorded push commits."""
        self.commits = []

    def push_with_rebase(self, branch, message, files):
        """Append the commit to the recorded list and return a fake SHA."""
        self.commits.append((branch, message, dict(files)))
        return "sha-fake"


class TestZtpArtifactCleanup:
    """Daily purge of served artifacts for terminal-state provisions (plan NFR)."""

    @pytest.fixture(autouse=True)
    def _reset_cleanup_gate(self, monkeypatch):
        """Reset the daily cleanup gate for each test."""
        from app.services import refresh

        monkeypatch.setattr(refresh, "_artifact_cleanup_last_run", None)
        yield

    def _onboard(self, client, fake_ztp_render, db_session, device_netbox_id, profile_name="access-leaf", role="access-switch"):
        """Create, deliver, and onboard a provision for a device."""
        from app.models import ZTPProvision
        from app.services import refresh

        device, _ = _create_provision(
            client, device_netbox_id, fake_ztp_render, profile_name=profile_name, role=role
        )
        provision = ZTPProvision.query.filter_by(device_id=device.id).one()
        client.get(f"/ztp/{device.serial_number}.cfg")  # delivered
        db_session.refresh(device)
        device.monitoring_status = "up"
        device.config_status = "pending"  # onboarding only targets unconfigured devices
        db_session.commit()
        refresh.refresh_ztp_onboarding(client.application)
        db_session.refresh(provision)
        assert provision.status == "onboarded"
        return device, provision

    def test_purges_old_terminal_provision(self, client, fake_ztp_render, db_session, monkeypatch):
        """An old terminal provision loses its git artifacts and DB content."""
        from datetime import timedelta

        from app.models import ZTPProvision
        from app.models.mixins import utcnow
        from app.services import gitlab, refresh

        fake = _FakeGit()
        monkeypatch.setattr(gitlab, "make_client", lambda app: fake)

        device, provision = self._onboard(client, fake_ztp_render, db_session, 102)
        serial = device.serial_number
        db_session.refresh(provision)
        provision.updated_at = utcnow() - timedelta(days=40)
        db_session.commit()

        refresh.refresh_ztp_artifact_cleanup(client.application)

        db_session.refresh(provision)
        assert provision.config_content == ""
        assert provision.script_content == ""
        assert provision.artifact_purged is True
        purges = [c for c in fake.commits if "purge" in c[1]]
        assert len(purges) == 1
        assert purges[0][2] == {
            f"ztp/{serial}/script.txt": None,
            f"ztp/{serial}/day-0.cfg": None,
        }

    def test_keeps_recent_and_nonterminal(self, client, fake_ztp_render, db_session, monkeypatch):
        """Recent or non-terminal provisions are left untouched."""
        from datetime import timedelta

        from app.extensions import db
        from app.models import Device, ZTPProvision
        from app.models.mixins import utcnow
        from app.services import gitlab, refresh

        fake = _FakeGit()
        monkeypatch.setattr(gitlab, "make_client", lambda app: fake)

        device_idle = Device.query.filter_by(netbox_id=101).one()
        device_old, provision_old = self._onboard(client, fake_ztp_render, db_session, 102)
        provision_old.updated_at = utcnow() - timedelta(days=40)
        device_recent, provision_recent = self._onboard(
            client, fake_ztp_render, db_session, 103, profile_name="acc-leaf-two", role="core-switch"
        )
        db.session.add(
            ZTPProvision(
                device_id=device_idle.id,
                config_content="keep me",
                script_content="source http://x\n",
                url="http://x/ztp/FCW2345A001",
                status="generated",
                git_path="ztp/FCW2345A001",
                updated_at=utcnow() - timedelta(days=40),
            )
        )
        db.session.commit()

        refresh.refresh_ztp_artifact_cleanup(client.application)

        db.session.refresh(provision_recent)
        assert provision_old.artifact_purged is True and provision_old.config_content == ""
        assert provision_recent.artifact_purged is False
        assert "day-0 config" in provision_recent.config_content
        fresh = ZTPProvision.query.filter_by(device_id=device_idle.id).first()
        assert fresh is not None and fresh.config_content == "keep me" and fresh.artifact_purged is False

    def test_git_unavailable_still_clears_db(self, client, fake_ztp_render, db_session, monkeypatch):
        """DB content is cleared even when Git is down."""
        from datetime import timedelta

        from app.services import gitlab, refresh
        from app.models.mixins import utcnow

        def _unavailable(app):
            """make_client stub raising GitLabUnavailable."""
            raise gitlab.GitLabUnavailable("GitLab is down")

        monkeypatch.setattr(gitlab, "make_client", _unavailable)
        _, provision = self._onboard(client, fake_ztp_render, db_session, 102)
        db_session.refresh(provision)
        provision.updated_at = utcnow() - timedelta(days=40)
        db_session.commit()

        refresh.refresh_ztp_artifact_cleanup(client.application)

        db_session.refresh(provision)
        assert provision.config_content == ""
        assert provision.artifact_purged is True

    def test_runs_once_per_day(self, client, fake_ztp_render, db_session, monkeypatch):
        """The cleanup runs at most once per UTC day."""
        from datetime import timedelta

        from app.services import gitlab, refresh
        from app.models.mixins import utcnow

        fake = _FakeGit()
        monkeypatch.setattr(gitlab, "make_client", lambda app: fake)

        _, first = self._onboard(client, fake_ztp_render, db_session, 102)
        db_session.refresh(first)
        first.updated_at = utcnow() - timedelta(days=40)
        db_session.commit()
        refresh.refresh_ztp_artifact_cleanup(client.application)
        db_session.refresh(first)
        assert first.artifact_purged is True

        _, second = self._onboard(
            client, fake_ztp_render, db_session, 103, profile_name="acc-leaf-two", role="core-switch"
        )
        db_session.refresh(second)
        second.updated_at = utcnow() - timedelta(days=40)
        db_session.commit()
        refresh.refresh_ztp_artifact_cleanup(client.application)  # same UTC day

        db_session.refresh(second)
        assert second.artifact_purged is False
        assert len([c for c in fake.commits if "purge" in c[1]]) == 1
