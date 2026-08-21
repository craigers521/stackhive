"""Phase 10 convergence tests: pipeline poll fallback, nightly drift, shared ZTP ledger."""
import os
from datetime import datetime, timezone

from app.extensions import db
from app.models import ConfigurationProfile, DeploymentDevice, DeploymentRecord, Device
from app.services import drift as drift_service
from app.services import gitlab as gitlab_service


def _utcnow():
    """Current naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_record(app, device, status="approved", pipeline_id=None):
    """Deployment record + device row wiring for poll/drift tests."""
    from app.models import User

    admin_id = User.query.filter_by(username="admin").one().id
    profile = ConfigurationProfile(
        name="acc-test",
        device_role=device.role,
        is_active=True,
        git_path="group_vars/acc-test/",
        created_by_id=admin_id,
        updated_by_id=admin_id,
    )
    db.session.add(profile)
    db.session.flush()
    record = DeploymentRecord(
        profile_id=profile.id,
        device_count=1,
        user_id=admin_id,
        status=status,
        git_commit_sha="local-deploy",
        git_branch="",
        started_at=_utcnow(),
        pipeline_id=pipeline_id,
        preview_output=f"# === {device.hostname} (acc-test) ===\nhostname {device.hostname}\n",
    )
    db.session.add(record)
    db.session.flush()
    row = DeploymentDevice(
        deployment_id=record.id,
        device_id=device.id,
        status="success",
        message="preview rendered",
        config_diff=record.preview_output,
        started_at=_utcnow(),
    )
    db.session.add(row)
    device.last_deployment = record
    db.session.commit()
    return record


class FakePipelineClient:
    """Duck-typed GitLab client returning a scripted pipeline object."""

    def __init__(self, pipeline):
        """Store the scripted pipeline and empty job list."""
        self.pipeline = pipeline
        self.calls = []

    def get_pipeline(self, pipeline_id):
        """Return the scripted pipeline for any id."""
        self.calls.append(("get_pipeline", pipeline_id))
        return self.pipeline

    def get_pipeline_jobs(self, pipeline_id):
        """Return one failed job (exercises the error-message suffix)."""
        self.calls.append(("get_pipeline_jobs", pipeline_id))
        return [{"name": "deploy", "status": "failed"}]


class TestPipelinePollFallback:
    """T073: in-flight records reconcile via get_pipeline/get_pipeline_jobs."""

    def test_successful_pipeline_moves_record_and_device(self, app, monkeypatch):
        """A success pipeline finalizes the record and marks the device deployed."""
        fake = FakePipelineClient({"id": 7, "status": "success", "web_url": "http://gl/p/7"})
        monkeypatch.setattr(gitlab_service, "make_client", lambda a: fake)
        device = Device.query.filter_by(netbox_id=101).one()
        record = _make_record(app, device, status="approved", pipeline_id=7)

        changed = gitlab_service.sync_inflight_pipelines(app)

        assert changed == 1
        assert record.status == "success"
        assert record.pipeline_id == 7
        assert row_status() == "success"
        assert device.config_status == "deployed"

    def test_failed_pipeline_marks_record_and_device_failed(self, app, monkeypatch):
        """A failed pipeline marks the record failed with job context in the message."""
        fake = FakePipelineClient({"id": 9, "status": "failed", "web_url": None})
        monkeypatch.setattr(gitlab_service, "make_client", lambda a: fake)
        device = Device.query.filter_by(netbox_id=103).one()
        record = _make_record(app, device, status="running", pipeline_id=9)

        changed = gitlab_service.sync_inflight_pipelines(app)

        assert changed == 1
        assert record.status == "failed"
        assert "pipeline 9 failed" in (record.error_message or "")
        assert "deploy" in (record.error_message or "")
        assert device.config_status == "failed"
        assert ("get_pipeline_jobs", 9) in fake.calls

    def test_running_pipeline_upgrades_approved_record(self, app, monkeypatch):
        """created/running pipeline states flip the record to running."""
        fake = FakePipelineClient({"id": 3, "status": "running"})
        monkeypatch.setattr(gitlab_service, "make_client", lambda a: fake)
        device = Device.query.filter_by(netbox_id=101).one()
        record = _make_record(app, device, status="approved", pipeline_id=3)

        gitlab_service.sync_inflight_pipelines(app)

        assert record.status == "running"

    def test_gitlab_unconfigured_is_a_noop(self, app, monkeypatch):
        """No GitLab configured: poll skips without touching records."""
        def _unconfigured(a):
            """Raise like make_client does when unconfigured."""
            raise gitlab_service.GitLabUnavailable("GITLAB_PROJECT_ID is not configured")

        monkeypatch.setattr(gitlab_service, "make_client", _unconfigured)
        device = Device.query.filter_by(netbox_id=101).one()
        _make_record(app, device, status="approved", pipeline_id=5)

        assert gitlab_service.sync_inflight_pipelines(app) == 0
        assert DeploymentRecord.query.filter_by(pipeline_id=5).one().status == "approved"


class TestNightlyDrift:
    """T075: nightly read-back sweep gated on setting + 02:00 local."""

    def test_disabled_setting_skips_sweep(self, app, monkeypatch):
        """drift_check_enabled=false: no work, no NETCONF attempts."""
        from app.services import settings as settings_service

        monkeypatch.setenv("ANSIBLE_NETCONF_USER", "netuser")
        monkeypatch.setenv("ANSIBLE_NETCONF_PASSWORD", "netpass")
        settings_service.set_setting(app, "drift_check_enabled", "false")
        fetched = []
        monkeypatch.setattr(
            "app.services.netconf.fetch_running_config", lambda *a, **k: fetched.append(a) or "x"
        )
        monkeypatch.setattr(drift_service, "DRIFT_CHECK_HOUR", _current_hour())
        drift_service._last_drift_run = None

        assert drift_service.nightly_drift_check(app) == 0
        assert fetched == []

    def test_outside_drift_hour_is_a_noop(self, app, monkeypatch):
        """Outside the 02:00 hour the sweep does not run."""
        monkeypatch.setattr(drift_service, "DRIFT_CHECK_HOUR", (_current_hour() + 9) % 24)
        drift_service._last_drift_run = None

        assert drift_service.nightly_drift_check(app) == 0

    def test_divergent_readback_flags_device_modified(self, app, monkeypatch):
        """Running config differing from the stored render marks the device modified."""
        monkeypatch.setenv("ANSIBLE_NETCONF_USER", "netuser")
        monkeypatch.setenv("ANSIBLE_NETCONF_PASSWORD", "netpass")
        monkeypatch.setattr(
            "app.services.netconf.fetch_running_config",
            lambda *a, **k: "hostname sw-access-01\nbanner unauthorized change\n",
        )
        monkeypatch.setattr(drift_service, "DRIFT_CHECK_HOUR", _current_hour())
        drift_service._last_drift_run = None
        device = Device.query.filter_by(netbox_id=101).one()
        _make_record(app, device, status="success")

        modified = drift_service.nightly_drift_check(app)

        assert modified == 1
        assert device.config_status == "modified"
        assert drift_service._last_drift_run == datetime.now().date()

    def test_matching_readback_keeps_device_deployed(self, app, monkeypatch):
        """Identical running config leaves the device deployed."""
        monkeypatch.setenv("ANSIBLE_NETCONF_USER", "netuser")
        monkeypatch.setenv("ANSIBLE_NETCONF_PASSWORD", "netpass")

        def _same(*a, **k):
            """Return exactly the stored render (no drift)."""
            return "hostname sw-access-01"

        monkeypatch.setattr("app.services.netconf.fetch_running_config", _same)
        monkeypatch.setattr(drift_service, "DRIFT_CHECK_HOUR", _current_hour())
        drift_service._last_drift_run = None
        device = Device.query.filter_by(netbox_id=101).one()
        _make_record(app, device, status="success")

        assert drift_service.nightly_drift_check(app) == 0
        assert device.config_status == "deployed"


class TestZtpRateLedgerSharing:
    """T077: the ZTP ledger is process-shared (file-backed) with a clean clear()."""

    def _hit(self, app, ip, n=1):
        """Record n requests from ip against the module ledger."""
        from app.routes import ztp as ztp_routes

        results = []
        with app.app_context():
            for _ in range(n):
                results.append(ztp_routes._rate_hits.hit(ip))
        return results

    def test_budget_enforced_then_clear_resets(self, app):
        """10 hits pass, the 11th is limited; clear() empties the state file."""
        import os

        from app.routes import ztp as ztp_routes

        ztp_routes._rate_hits.clear()
        ip = "10.9.9.9"
        results = self._hit(app, ip, ztp_routes.RATE_LIMIT_PER_MINUTE)
        assert results == [False] * ztp_routes.RATE_LIMIT_PER_MINUTE
        assert self._hit(app, ip, 1) == [True]

        state_file = os.path.join(app.instance_path, ztp_routes.RATE_STATE_NAME)
        assert os.path.exists(state_file)
        ztp_routes._rate_hits.clear()
        assert not os.path.exists(state_file)
        assert self._hit(app, ip, 1) == [False]
        ztp_routes._rate_hits.clear()


def row_status():
    """The status of the single DeploymentDevice row."""
    return DeploymentDevice.query.one().status


def _current_hour():
    """Current local hour (the sweep fires inside the DRIFT_CHECK_HOUR hour)."""
    return datetime.now().hour
