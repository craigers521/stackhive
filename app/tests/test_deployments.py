"""Deployments user story tests: preview, create, approvals, webhook, statuses."""
import pytest

from app.models import ConfigurationProfile, DeploymentDevice, DeploymentRecord, Device
from app.services.ansible import PreviewResult


@pytest.fixture
def profile(db_session):
    """Create a seeded configuration profile via the API."""
    from app.models import User

    profile = ConfigurationProfile(
        name="access-leaf",
        device_role="access-switch",
        is_active=True,
        git_path="group_vars/access-leaf/",
        created_by_id=User.query.get(1).id,
        updated_by_id=User.query.get(1).id,
    )
    db_session.add(profile)
    db_session.commit()
    return profile


@pytest.fixture
def fake_render(monkeypatch):
    """Replace the ansible preview renderer with a deterministic stub."""
    from app.services import ansible as ansible_service

    def _render(app, device, profile):
        """Monkeypatch the ansible renderer to a static config string."""
        return PreviewResult(
            hostname=device.hostname,
            profile_name=profile.name,
            config=f"hostname {device.hostname}\n!\n! rendered from {profile.name}\n",
            snippets=["hostname", "vlans"],
            variables_used={"domain_name": "lab.example.com"},
        )

    monkeypatch.setattr(ansible_service, "render_preview", _render)
    return _render


@pytest.fixture
def no_git(monkeypatch):
    """Disable GitLab so deployments run in local-only mode."""
    from app.services import profiles as profiles_service

    monkeypatch.setattr(profiles_service, "_git_client", lambda app: None)


class _FakeGitClient:
    """In-memory GitLab client stub recording branches, MRs, pipelines."""
    def __init__(self):
        """Initialize empty branch/MR/pipeline state."""
        self.branches = []
        self.commits = []
        self.mrs = []
        self.merged = []
        self.pipelines = []

    def create_branch(self, branch, ref):
        """Record a branch creation (idempotent)."""
        self.branches.append((branch, ref))
        return {"name": branch}

    def commit_files(self, branch, message, files, start_branch=None):
        """Record a file commit and return a fake SHA."""
        self.commits.append({"branch": branch, "message": message, "files": files, "start_branch": start_branch})
        return "a" * 39 + format(len(self.commits), "x")

    def create_merge_request(self, source_branch, target_branch, title, description=""):
        """Record an open merge request and return it."""
        self.mrs.append({"source_branch": source_branch, "target_branch": target_branch, "title": title})
        return {"iid": 100, "web_url": "http://gitlab.test/-/merge_requests/100"}

    def list_merge_requests(self, source_branch=None, state="all"):
        """Return recorded merge requests for a source branch."""
        return [{"iid": 100, "source_branch": source_branch}] if self.mrs else []

    def merge_merge_request(self, mr_iid):
        """Mark a recorded merge request as merged."""
        self.merged.append(mr_iid)
        return {"state": "merged"}

    def list_pipelines(self, ref=None, per_page=10):
        """Return recorded pipelines for a ref."""
        return [{"id": 1000, "status": "created", "sha": "abc", "web_url": "http://gitlab.test/pipelines/1000"}]


@pytest.fixture
def git_client(monkeypatch):
    """Expose the fake GitLab client through the app wiring."""
    from app.services import profiles as profiles_service

    fake = _FakeGitClient()
    monkeypatch.setattr(profiles_service, "_git_client", lambda app: fake)
    return fake


class TestPreviewEndpoint:
    """POST /api/deployments/preview: validation and output shape."""
    def test_preview_returns_config_and_labels(self, client, profile, fake_render):
        """Preview returns the rendered config, snippets, and profile name."""
        from conftest import login

        login(client, "viewer")
        resp = client.post(
            "/api/deployments/preview",
            json={"device_id": 101, "profile_id": profile.id},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["hostname"] == "sw-access-01"
        assert "hostname sw-access-01" in data["config"]
        assert "vlans" in data["snippets"]

    def test_preview_requires_device_id(self, client, profile, fake_render):
        """A missing device_id is rejected with 400."""
        from conftest import login

        login(client, "viewer")
        assert client.post("/api/deployments/preview", json={}).status_code == 400

    def test_preview_unknown_device_404(self, client, profile, fake_render):
        """An unknown device id returns 404."""
        from conftest import login

        login(client, "viewer")
        assert client.post("/api/deployments/preview", json={"device_id": 999}).status_code == 404

    def test_preview_render_failure_422(self, client, profile, monkeypatch):
        """A render failure surfaces as 422 with the detail."""
        from conftest import login
        from app.services import ansible as ansible_service

        def _boom(app, device, prof):
            """Renderer replacement that always raises AnsiblePreviewError."""
            raise ansible_service.AnsiblePreviewError("playbook exploded", returncode=2)

        monkeypatch.setattr(ansible_service, "render_preview", _boom)
        login(client, "viewer")
        resp = client.post(
            "/api/deployments/preview",
            json={"device_id": 101, "profile_id": profile.id},
        )
        assert resp.status_code == 422


class TestCreateDeployment:
    """POST /api/deployments: creation validation and Git side effects."""
    def test_create_202_and_record_persisted(self, client, profile, fake_render, no_git, db_session):
        """A valid create returns 202 and persists the pending record."""
        from conftest import login

        login(client, "editor")
        resp = client.post(
            "/api/deployments",
            json={"device_ids": [101, 102], "profile_id": profile.id},
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "pending"
        assert sorted(data["device_ids"]) == ["101", "102"]
        assert len(data["git_commit_sha"]) == 40  # local pseudo-SHA fallback

        record = DeploymentRecord.query.filter_by(id=int(data["deployment_id"])).one()
        assert len(record.git_commit_sha) == 40
        rows = DeploymentDevice.query.filter_by(deployment_id=record.id).all()
        assert len(rows) == 2
        assert record.preview_output and "hostname sw-access-01" in record.preview_output
        assert record.operator.username == "editor"

    def test_create_with_git_commits_manifest_and_opens_mr(self, client, profile, fake_render, git_client):
        """With Git configured, the manifest is committed and an MR opened."""
        from conftest import login

        login(client, "editor")
        resp = client.post(
            "/api/deployments",
            json={"device_ids": [101], "profile_id": profile.id},
        )
        assert resp.status_code == 202
        record = DeploymentRecord.query.first()
        assert git_client.branches and git_client.branches[0][0] == f"deploy/{record.id}"
        assert git_client.commits and "deployments/manifests/deploy-" in "".join(git_client.commits[0]["files"])
        assert git_client.mrs and git_client.mrs[0]["target_branch"] == "main"

    def test_cloud_managed_device_409(self, client, profile, fake_render, no_git, db_session):
        """Cloud-managed devices are excluded from deployments."""
        from conftest import login

        device = Device.query.filter_by(netbox_id=101).one()
        device.cloud_managed = True
        db_session.commit()
        login(client, "editor")
        resp = client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        assert resp.status_code == 409

    def test_role_mismatch_422(self, client, profile, fake_render, no_git):
        """A device/profile role mismatch returns 422."""
        from conftest import login

        login(client, "editor")
        resp = client.post("/api/deployments", json={"device_ids": [103], "profile_id": profile.id})
        assert resp.status_code == 422  # sw-core-01 (core-switch) vs access profile

    def test_inflight_device_409(self, client, profile, fake_render, no_git):
        """A device with an in-flight deployment returns 409."""
        from conftest import login

        login(client, "editor")
        assert client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id}).status_code == 202
        resp = client.post("/api/deployments", json={"device_ids": [101, 102], "profile_id": profile.id})
        assert resp.status_code == 409

    def test_viewer_cannot_create(self, client, profile, fake_render, no_git):
        """Viewers are denied deployment creation with 403."""
        from conftest import login

        login(client, "viewer")
        resp = client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        assert resp.status_code == 403


class TestApprove:
    """Approval gate: admin only, state transitions, MR merge."""
    def _create(self, client, profile, fake_render, no_git):
        """Create a pending deployment record directly (helper)."""
        from conftest import login

        login(client, "editor")
        resp = client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        assert resp.status_code == 202
        return DeploymentRecord.query.first()

    def test_editor_cannot_approve(self, client, profile, fake_render, no_git):
        """Editors are denied approval with 403."""
        self._create(client, profile, fake_render, no_git)
        from conftest import login

        login(client, "editor")
        from app.models import DeploymentRecord as Rec

        record = Rec.query.first()
        assert client.post(f"/api/deployments/{record.id}/approve").status_code == 403

    def test_admin_approves_and_reapprove_conflicts(self, client, profile, fake_render, no_git):
        """Admin approval succeeds once; a re-approval returns 409."""
        record = self._create(client, profile, fake_render, no_git)
        from conftest import login

        login(client, "admin")
        resp = client.post(f"/api/deployments/{record.id}/approve")
        assert resp.status_code == 200
        db_record = DeploymentRecord.query.get(record.id)
        assert db_record.status == "approved"
        assert db_record.approver.username == "admin"
        assert client.post(f"/api/deployments/{record.id}/approve").status_code == 409

    def test_approve_merges_mr_with_git(self, client, profile, fake_render, git_client):
        """Approval merges the open merge request via the Git client."""
        from conftest import login

        login(client, "editor")
        client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        record = DeploymentRecord.query.first()
        login(client, "admin")
        resp = client.post(f"/api/deployments/{record.id}/approve")
        assert resp.status_code == 200
        assert git_client.merged == [100]
        db_record = DeploymentRecord.query.get(record.id)
        assert db_record.pipeline_id == 1000


class TestWebhook:
    """GitLab pipeline webhook: token auth and status handling."""
    def test_invalid_token_401(self, client, profile, fake_render, no_git):
        """A missing or wrong webhook token is rejected with 401."""
        from conftest import login

        login(client, "editor")
        client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        resp = client.post(
            "/api/webhooks/gitlab/pipeline",
            json={"pipeline_id": 1, "status": "success"},
            headers={"X-GitLab-Token": "wrong"},
        )
        assert resp.status_code == 401

    def test_success_webhook_marks_devices_deployed(self, client, profile, fake_render, no_git, db_session):
        """A success pipeline marks the record success and devices deployed."""
        from conftest import login

        login(client, "editor")
        client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        record = DeploymentRecord.query.first()
        resp = client.post(
            "/api/webhooks/gitlab/pipeline",
            json={"pipeline_id": 42, "status": "success", "commit_sha": record.git_commit_sha,
                  "devices": [{"hostname": "sw-access-01", "status": "success"}]},
            headers={"X-GitLab-Token": "test-webhook-token"},
        )
        assert resp.status_code == 200
        db_record = DeploymentRecord.query.get(record.id)
        assert db_record.status == "success"
        assert db_record.pipeline_id == 42
        device = Device.query.filter_by(netbox_id=101).one()
        assert device.config_status == "deployed"
        row = DeploymentDevice.query.filter_by(deployment_id=record.id).one()
        assert row.status == "success"

    def test_failed_webhook_marks_record_and_device_failed(self, client, profile, fake_render, no_git, db_session):
        """A failed pipeline marks the record and device rows failed."""
        from conftest import login

        login(client, "editor")
        client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        record = DeploymentRecord.query.first()
        resp = client.post(
            "/api/webhooks/gitlab/pipeline",
            json={"pipeline_id": 43, "status": "failed", "commit_sha": record.git_commit_sha},
            headers={"X-GitLab-Token": "test-webhook-token"},
        )
        assert resp.status_code == 200
        db_record = DeploymentRecord.query.get(record.id)
        assert db_record.status == "failed"
        device = Device.query.filter_by(netbox_id=101).one()
        assert device.config_status == "failed"

    def test_unknown_pipeline_ignored(self, client, no_git):
        """Unrecognized pipelines are acknowledged and ignored."""
        resp = client.post(
            "/api/webhooks/gitlab/pipeline",
            json={"pipeline_id": 999, "status": "success"},
            headers={"X-GitLab-Token": "test-webhook-token"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ignored"] is True

    def test_running_status_moves_record_to_running(self, client, profile, fake_render, no_git):
        """A running pipeline moves the record to running."""
        from conftest import login

        login(client, "editor")
        client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id})
        record = DeploymentRecord.query.first()
        client.post(
            "/api/webhooks/gitlab/pipeline",
            json={"pipeline_id": 44, "status": "running", "commit_sha": record.git_commit_sha},
            headers={"X-GitLab-Token": "test-webhook-token"},
        )
        assert DeploymentRecord.query.get(record.id).status == "running"


class TestConfigStatusTransitions:
    """Device.config_status transition-map enforcement."""
    def test_illegal_transition_returns_false(self, db_session):
        """Illegal config_status transitions are refused."""
        device = Device.query.filter_by(netbox_id=101).one()  # deployed
        assert device.set_config_status("pending") is False
        assert device.config_status == "deployed"

    def test_legal_transitions(self, db_session):
        """Legal config_status transitions succeed."""
        device = Device.query.filter_by(netbox_id=101).one()  # deployed
        assert device.set_config_status("failed") is True
        assert device.set_config_status("deployed") is True
        pend = Device.query.filter_by(netbox_id=102).one()  # pending
        assert pend.set_config_status("onboarded") is True
        assert pend.set_config_status("deployed") is True
