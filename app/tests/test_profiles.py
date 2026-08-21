"""Profiles user story tests: CRUD, validation, RBAC, override precedence."""
import pytest


class TestProfileCRUD:
    """Profile create, list, update, delete."""
    def test_create_profile_with_templates_and_variables(self, client):
        """Create persists nested templates, variables, and interface rows."""
        from conftest import login
        from app.models import ConfigurationProfile

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "access-leaf",
                "device_role": "access-switch",
                "templates": [
                    {"name": "vlans", "content": "{% for v in vlans %}vlan {{ v.id }}\n{% endfor %}", "order": 1},
                    {"name": "routing", "content": "ip route 0.0.0.0/0 {{ default_gateway }}\n", "order": 2},
                ],
                "variables": {
                    "domain_name": "lab.example.com",
                    "ntp_servers": ["10.0.1.53", "10.0.1.54"],
                    "default_gateway": "10.0.1.1",
                },
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "access-leaf"
        assert data["device_role"] == "access-switch"
        assert {t["name"] for t in data["templates"]} == {"vlans", "routing"}
        assert data["templates"][0]["order"] == 1
        assert data["variables"]["domain_name"] == "lab.example.com"
        assert data["variables"]["ntp_servers"] == ["10.0.1.53", "10.0.1.54"]

        profile = ConfigurationProfile.query.filter_by(name="access-leaf").one()
        assert len(profile.templates) == 2
        assert len(profile.variables) == 3
        assert profile.variables[0].git_path.startswith("group_vars/access-leaf")

    def test_list_profiles_with_filters(self, client):
        """List honours the role and search filters."""
        from conftest import login

        login(client, "editor")
        assert client.post("/api/profiles", json={"name": "a1", "device_role": "access-switch"}).status_code == 201
        assert client.post("/api/profiles", json={"name": "c1", "device_role": "core-switch"}).status_code == 201

        login(client, "viewer")
        assert len(client.get("/api/profiles").get_json()["profiles"]) == 2
        data = client.get("/api/profiles?role=core-switch").get_json()
        assert [p["name"] for p in data["profiles"]] == ["c1"]
        data = client.get("/api/profiles?search=a1").get_json()
        assert [p["name"] for p in data["profiles"]] == ["a1"]

    def test_update_profile_full_replacement(self, client):
        """Update replaces templates and variables wholesale."""
        from conftest import login
        from app.models import ConfigurationProfile

        login(client, "editor")
        client.post(
            "/api/profiles",
            json={
                "name": "p1",
                "device_role": "access-switch",
                "templates": [{"name": "aaa", "content": "aaa\n"}],
                "variables": {"k": "v", "k2": "v2"},
            },
        )
        pid = ConfigurationProfile.query.first().id
        version = ConfigurationProfile.query.first().version

        resp = client.put(
            f"/api/profiles/{pid}",
            json={
                "templates": [{"name": "vlans", "content": "vlan 99\n"}],
                "variables": {"only": "one"},
                "version": version,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert {t["name"] for t in data["templates"]} == {"vlans"}
        assert data["variables"] == {"only": "one"}

    def test_update_profile_conflict_on_stale_version(self, client):
        """A stale base_version returns 409."""
        from conftest import login
        from app.models import ConfigurationProfile

        login(client, "editor")
        client.post("/api/profiles", json={"name": "p1", "device_role": "access-switch"})
        pid = ConfigurationProfile.query.first().id
        resp = client.put(f"/api/profiles/{pid}", json={"variables": {"k": "v"}, "version": "bogus-version"})
        assert resp.status_code == 409

    def test_delete_profile(self, client):
        """Delete removes the profile."""
        from conftest import login
        from app.models import ConfigurationProfile

        login(client, "editor")
        client.post("/api/profiles", json={"name": "p1", "device_role": "access-switch"})
        pid = ConfigurationProfile.query.first().id
        assert client.delete(f"/api/profiles/{pid}").status_code == 204
        assert ConfigurationProfile.query.count() == 0
        assert client.get(f"/api/profiles/{pid}").status_code == 404


class TestProfileValidation:
    """Input validation on profile payloads."""
    def test_invalid_jinja_rejected(self, client):
        """Templates that fail to compile are rejected with 422."""
        from conftest import login

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "bad",
                "device_role": "access-switch",
                "templates": [{"name": "x", "content": "{% for broken %}"}],
            },
        )
        assert resp.status_code == 400
        assert "jinja" in resp.get_json()["details"].lower()

    def test_duplicate_name_conflict(self, client):
        """A duplicate profile name returns 409."""
        from conftest import login

        login(client, "editor")
        assert client.post("/api/profiles", json={"name": "dup", "device_role": "access-switch"}).status_code == 201
        resp = client.post("/api/profiles", json={"name": "dup", "device_role": "core-switch"})
        assert resp.status_code == 409

    def test_single_active_profile_per_role(self, client):
        """A second active profile for a role returns 409."""
        from conftest import login

        login(client, "editor")
        assert client.post("/api/profiles", json={"name": "first", "device_role": "access-switch"}).status_code == 201
        resp = client.post("/api/profiles", json={"name": "second", "device_role": "access-switch"})
        assert resp.status_code == 409

    def test_invalid_name_rejected(self, client):
        """An invalid profile name is rejected with 400."""
        from conftest import login

        login(client, "editor")
        resp = client.post("/api/profiles", json={"name": "bad name!", "device_role": "access-switch"})
        assert resp.status_code == 400

    def test_unknown_interface_type_rejected(self, client):
        """Interface rows for unknown types are rejected."""
        from conftest import login

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "c1",
                "device_role": "core-switch",
                "interface_mappings": [
                    {
                        "name": "uplinks",
                        "interface_type": "FortyGigE",
                        "interface_range": "1-2",
                        "content": "no switchport\n",
                    }
                ],
            },
        )
        assert resp.status_code == 400
        assert "interface" in resp.get_json()["details"].lower()

    def test_range_beyond_interface_count_rejected(self, client):
        """Interface ranges beyond the type count are rejected."""
        from conftest import login

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "a1",
                "device_role": "access-switch",
                "interface_mappings": [
                    {
                        "name": "access",
                        "interface_type": "GigabitEthernet",
                        "interface_range": "1-99",
                        "content": "switchport\n",
                    }
                ],
            },
        )
        assert resp.status_code == 400


class TestProfileRBAC:
    """Role enforcement on profile endpoints."""
    def test_viewer_cannot_create(self, client):
        """Viewers cannot create profiles."""
        from conftest import login

        login(client, "viewer")
        resp = client.post("/api/profiles", json={"name": "nope", "device_role": "access-switch"})
        assert resp.status_code == 403

    def test_viewer_cannot_update_or_delete(self, client):
        """Viewers cannot update or delete profiles."""
        from conftest import login

        login(client, "editor")
        client.post("/api/profiles", json={"name": "p1", "device_role": "access-switch"})
        from app.models import ConfigurationProfile

        pid = ConfigurationProfile.query.first().id
        login(client, "viewer")
        assert client.put(f"/api/profiles/{pid}", json={"variables": {"a": "b"}}).status_code == 403
        assert client.delete(f"/api/profiles/{pid}").status_code == 403

    def test_viewer_can_read(self, client):
        """Viewers can list and read profiles."""
        from conftest import login
        from app.models import ConfigurationProfile

        login(client, "editor")
        client.post("/api/profiles", json={"name": "p1", "device_role": "access-switch"})
        pid = ConfigurationProfile.query.first().id
        login(client, "viewer")
        assert client.get(f"/api/profiles/{pid}").status_code == 200
        assert client.get("/profiles").status_code == 200


class TestDeviceOverrides:
    """Per-device variable overrides."""
    def test_override_wins_over_profile_variable(self, client, monkeypatch):
        """Device overrides win over profile defaults in renders."""
        from conftest import login
        from app.models import ConfigurationProfile, Device
        from app.services import profiles as profiles_service

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "a1",
                "device_role": "access-switch",
                "variables": {"domain_name": "profile.example.com", "vlan_id": 10},
            },
        )
        assert resp.status_code == 201

        client.put(
            "/api/devices/101/overrides",
            json={"variables": {"domain_name": "host.example.com"}},
        )

        device = Device.query.filter_by(netbox_id=101).one()
        profile = ConfigurationProfile.query.filter_by(name="a1").one()
        merged = profiles_service.effective_variables(client.application, device, profile)
        assert merged["domain_name"] == "host.example.com"
        assert merged["vlan_id"] == 10

    def test_overrides_roundtrip_and_replacement(self, client):
        """The override endpoints round-trip and replace values."""
        from conftest import login

        login(client, "viewer")
        data = client.get("/api/devices/101/overrides").get_json()
        assert data["variables"] == {}

        login(client, "editor")
        client.put("/api/devices/101/overrides", json={"variables": {"a": "1", "b": True}})
        data = client.get("/api/devices/101/overrides").get_json()
        assert data["variables"]["a"] == "1"
        assert data["variables"]["b"] in ("true", True)

        client.put("/api/devices/101/overrides", json={"variables": {"c": "x"}})
        data = client.get("/api/devices/101/overrides").get_json()
        assert set(data["variables"]) == {"c"}

    def test_overrides_require_editor(self, client):
        """Viewer writes are denied on overrides."""
        from conftest import login

        login(client, "viewer")
        resp = client.put("/api/devices/101/overrides", json={"variables": {"a": "1"}})
        assert resp.status_code == 403

    def test_override_invalid_variable_key_rejected(self, client):
        """Unknown variable keys are rejected."""
        from conftest import login

        login(client, "editor")
        resp = client.put("/api/devices/101/overrides", json={"variables": {"bad-key!": "1"}})
        assert resp.status_code == 400


class TestVarsFileWriter:
    """Regression: group_vars YAML must stay valid with 2+ variables (T059)."""

    def test_multi_variable_group_vars_is_valid_yaml(self, client):
        """The generated group_vars file parses as YAML."""
        import yaml

        from conftest import login
        from app.models import ConfigurationProfile
        from app.services import profiles as profiles_service

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "multi-var",
                "device_role": "access-switch",
                "templates": [],
                "variables": {
                    "vlan_management": "100",
                    "default_gateway": "10.0.1.1",
                    "domain_name": "lab.example.com",
                },
            },
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        profile = ConfigurationProfile.query.filter_by(name="multi-var").one()

        files = profiles_service._profile_git_files(profile)
        content = files["group_vars/multi-var/vars.yml"]
        parsed = yaml.safe_load(content)
        assert parsed == {
            "vlan_management": "100",
            "default_gateway": "10.0.1.1",
            "domain_name": "lab.example.com",
        }
        # Every variable key sits on its own line (no concatenation)
        assert len([l for l in content.splitlines() if l.strip()]) == 3


class TestInterfaceTypePresentation:
    """FR-006: the interface editor presents the device type's layouts."""

    def test_edit_form_presents_role_interface_types(self, client):
        """The edit form shows the role's known interface types."""
        from conftest import login
        from app.models import ConfigurationProfile

        login(client, "editor")
        resp = client.post(
            "/api/profiles",
            json={
                "name": "acc-leaf-ui",
                "device_role": "access-switch",
                "templates": [{"name": "vlans", "content": "vlan 10\n", "order": 1}],
            },
        )
        assert resp.status_code == 201
        pid = resp.get_json()["id"]
        profile = ConfigurationProfile.query.filter_by(name="acc-leaf-ui").one()

        page = client.get(f"/profiles/{profile.id}/edit").get_data(as_text=True)
        assert "Known interface types for <strong>access-switch</strong>" in page
        assert "GigabitEthernet (&times;48)" in page
        assert "TenGigabitEthernet (&times;4)" in page
        # Type inputs render as constrained selects, not free text
        assert "name=\"i_type_0\"" in page
        assert "<select class=\"form-select form-select-sm\" name=\"i_type_0\">" in page
        assert "placeholder=\"interface type" not in page

    def test_new_form_presents_all_known_types(self, client):
        """The new form shows the union of known interface types."""
        from conftest import login

        login(client, "editor")
        page = client.get("/profiles/new").get_data(as_text=True)
        assert "across synced device types" in page
        assert "GigabitEthernet (&times;48)" in page
        assert "<select class=\"form-select form-select-sm\" name=\"i_type_2\">" in page

    def test_form_without_device_types_falls_back_to_free_text(self, client, app):
        """Without device types the form falls back to free-text inputs."""
        from conftest import login

        with app.app_context():
            from app.extensions import db
            from app.models import DeviceType

            for dt in DeviceType.query.all():
                dt.interface_types = {}
            db.session.commit()

        login(client, "editor")
        page = client.get("/profiles/new").get_data(as_text=True)
        assert "No known interface definitions for the target role yet" in page
        assert 'name="i_type_0" placeholder="interface type' in page
