"""T059 — quickstart.md validation scenarios 2-8 in one integrated walkthrough.

Scenario 1 (container bring-up) is validated with `docker compose config`
(docker-compose.yml + docker-compose.netbox.yml); scenarios 2-8 run here
against the app with REAL Ansible rendering (skipped when ansible-playbook
is absent) so the preview/deploy/ZTP paths use the same playbook code the
GitLab runner executes.
"""
import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ansible-playbook") is None,
    reason="ansible-playbook not installed",
)


def test_quickstart_scenarios_2_through_8(app, client, db_session, monkeypatch):
    """Walk quickstart scenarios 2-8 end to end (skipped when ansible-playbook is absent)."""
    from conftest import login
    from app.models import ConfigurationProfile, DeploymentRecord, Device
    from app.routes import ztp as ztp_routes
    from app.services import profiles as profiles_service

    # Local-only git mode: GitLab is not part of the app test (the MR/pipeline
    # integration path is covered by test_deployments with a fake Git client).
    monkeypatch.setattr(profiles_service, "_git_client", lambda a: None)
    ztp_routes._rate_hits.clear()

    # ------------------------------------------------------------------
    # Scenario 2: Device inventory
    # ------------------------------------------------------------------
    login(client, "viewer")
    resp = client.get("/api/devices")
    assert resp.status_code == 200
    data = resp.get_json()
    hostnames = {d["hostname"] for d in data["devices"]}
    assert {"sw-access-01", "sw-access-02", "sw-core-01"} <= hostnames
    assert data["total"] == 3
    by_host = {d["hostname"]: d for d in data["devices"]}
    assert by_host["sw-access-01"]["role"] == "access-switch"
    assert by_host["sw-access-01"]["ip_address"]
    # Role filter
    resp = client.get("/api/devices?role=core-switch")
    assert [d["hostname"] for d in resp.get_json()["devices"]] == ["sw-core-01"]
    # Detail page renders with interfaces (from device type)
    detail = client.get("/inventory/101")
    assert detail.status_code == 200
    assert b"GigabitEthernet" in detail.data

    # ------------------------------------------------------------------
    # Scenario 3: Configuration profile creation
    # ------------------------------------------------------------------
    login(client, "editor")
    resp = client.post(
        "/api/profiles",
        json={
            "name": "access-switch-base",
            "device_role": "access-switch",
            "templates": [
                {"name": "vlan", "content": "vlan {{ vlan_management }}\n name mgmt-vlan\n!\n", "order": 1},
                {"name": "routing", "content": "ip route 0.0.0.0/0 {{ default_gateway }}\n", "order": 2},
            ],
            "variables": {"vlan_management": "100", "default_gateway": "10.0.1.1"},
        },
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    profile = ConfigurationProfile.query.filter_by(name="access-switch-base").one()
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert any(p["name"] == "access-switch-base" for p in resp.get_json()["profiles"])

    # ------------------------------------------------------------------
    # Scenario 4: Preview with override precedence (real Ansible render)
    # ------------------------------------------------------------------
    login(client, "viewer")
    resp = client.post(
        "/api/deployments/preview",
        json={"device_id": 101, "profile_id": profile.id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    preview = resp.get_json()
    assert "vlan 100" in preview["config"]
    assert "ip route 0.0.0.0/0 10.0.1.1" in preview["config"]
    assert {"vlan", "routing"} <= {s["name"] for s in preview["snippets"]}

    # Device override beats the profile default
    login(client, "editor")
    resp = client.put(
        "/api/devices/101/overrides",
        json={"variables": {"vlan_management": "200"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    login(client, "viewer")
    resp = client.post(
        "/api/deployments/preview",
        json={"device_id": 101, "profile_id": profile.id},
    )
    assert "vlan 200" in resp.get_json()["config"]

    # ------------------------------------------------------------------
    # Scenario 5: Deployment flow (record -> approval -> pipeline webhook)
    # ------------------------------------------------------------------
    login(client, "editor")
    resp = client.post(
        "/api/deployments",
        json={"device_ids": [101], "profile_id": profile.id},
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "101" in body["device_ids"]
    record = DeploymentRecord.query.filter_by(id=int(body["deployment_id"])).one()
    assert record.status == "pending"
    assert len(record.git_commit_sha) == 40
    login(client, "viewer")
    resp = client.get("/api/deployments")
    assert resp.status_code == 200
    assert any(r["deployment_id"] == str(record.id) for r in resp.get_json()["deployments"])
    # Approval is admin-only
    login(client, "editor")
    assert client.post(f"/api/deployments/{record.id}/approve").status_code == 403
    login(client, "admin")
    resp = client.post(f"/api/deployments/{record.id}/approve")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert DeploymentRecord.query.get(record.id).status == "approved"
    # Pipeline webhook (success) completes the loop
    resp = client.post(
        "/api/webhooks/gitlab/pipeline",
        json={
            "pipeline_id": 7,
            "status": "success",
            "commit_sha": record.git_commit_sha,
            "devices": [{"hostname": "sw-access-01", "status": "success"}],
        },
        headers={"X-GitLab-Token": "test-webhook-token"},
    )
    assert resp.status_code == 200
    assert DeploymentRecord.query.get(record.id).status == "success"
    assert Device.query.filter_by(netbox_id=101).one().config_status == "deployed"
    # Bad token rejected
    resp = client.post(
        "/api/webhooks/gitlab/pipeline",
        json={"pipeline_id": 8, "status": "success"},
        headers={"X-GitLab-Token": "wrong"},
    )
    assert resp.status_code == 401

    # ------------------------------------------------------------------
    # Scenario 6: Monitoring (graceful degradation without Grafana)
    # ------------------------------------------------------------------
    login(client, "viewer")
    resp = client.get("/monitoring")
    assert resp.status_code == 200
    assert b"sw-core-01" in resp.data
    resp = client.get("/api/monitoring/devices?role=access-switch")
    assert resp.status_code == 200
    entries = {e["hostname"]: e for e in resp.get_json()["devices"]}
    assert entries["sw-access-01"]["status"] == "up"  # DB fallback
    assert entries["sw-access-01"]["grafana_url"]
    # Infra API surfaces 502 when Grafana is unreachable
    assert client.get("/api/monitoring/infrastructure").status_code == 502

    # ------------------------------------------------------------------
    # Scenario 7: ZTP provisioning (real ztp.yml render)
    # ------------------------------------------------------------------
    login(client, "editor")
    # Day-0 onboarding targets a pending device (102, access-switch)
    resp = client.post(
        "/api/onboarding/ztp",
        json={
            "device_id": 102,
            "serial": "FCW2345A002",
            "hostname": "sw-access-02",
            "profile_id": profile.id,
        },
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "generated"
    # A second provision for the same device conflicts
    resp = client.post(
        "/api/onboarding/ztp",
        json={
            "device_id": 102,
            "serial": "FCW2345A002",
            "hostname": "sw-access-02",
            "profile_id": profile.id,
        },
    )
    assert resp.status_code == 409

    # Public endpoints: no session required
    anon = app.test_client()
    txt = anon.get("/ztp/FCW2345A002.txt")
    assert txt.status_code == 200
    assert b"source" in txt.data and b"FCW2345A002.cfg" in txt.data
    cfg = anon.get("/ztp/FCW2345A002.cfg")
    assert cfg.status_code == 200
    # Day-0 config = constrained bootstrap + netconf snippet set
    assert b"hostname sw-access-02" in cfg.data
    assert b"ip netconf" in cfg.data
    assert anon.get("/ztp/UNKNOWN123.txt").status_code == 404
    from app.models import ZTPProvision

    provision = ZTPProvision.query.one()
    assert provision.status == "delivered"  # fetch marks delivered

    # Post-boot telemetry arrives -> refresh flips to onboarded
    device = Device.query.filter_by(netbox_id=102).one()
    assert device.config_status == "pending"
    device.monitoring_status = "up"
    db_session.commit()
    from app.services import refresh

    with app.app_context():
        refresh.refresh_ztp_onboarding(app)
    db_session.refresh(provision)
    db_session.refresh(device)
    assert provision.status == "onboarded"
    assert device.config_status == "onboarded"

    # ------------------------------------------------------------------
    # Scenario 8: RBAC
    # ------------------------------------------------------------------
    login(client, "viewer")
    assert client.post("/api/profiles", json={"name": "nope", "device_role": "access-switch"}).status_code == 403
    assert client.get("/api/settings").status_code == 403
    assert client.post("/api/deployments", json={"device_ids": [101], "profile_id": profile.id}).status_code == 403
    login(client, "editor")
    assert client.get("/api/settings").status_code == 403
    assert client.post("/api/profiles", json={"name": "editor-solo", "device_role": "core-switch"}).status_code == 201
    login(client, "admin")
    assert client.get("/api/settings").status_code == 200
    users = client.get("/api/users").get_json()["users"]
    assert {u["username"] for u in users} >= {"admin", "editor", "viewer"}
