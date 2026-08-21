"""Ansible preview rendering tests (real ansible-playbook subprocess).

These tests exercise the same playbook path the GitLab runner uses:
workspace mirroring the git layout, host_vars beat group_vars, and the
preview.json artifact contract. Skipped automatically when
ansible-playbook is not installed.
"""
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ansible-playbook") is None,
    reason="ansible-playbook not installed",
)


def _create_profile(client, name, device_role, templates, variables):
    """Create a profile via the API and return its ORM row."""
    from conftest import login

    login(client, "editor")
    resp = client.post(
        "/api/profiles",
        json={
            "name": name,
            "device_role": device_role,
            "templates": [{"name": t, "content": c, "order": i} for i, (t, c) in enumerate(templates)],
            "variables": variables,
        },
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    from app.models import ConfigurationProfile

    return ConfigurationProfile.query.filter_by(name=name).one()


class TestAnsiblePreview:
    """Preview rendering through the real ansible-playbook."""
    def test_preview_renders_snippets_from_profile_templates(self, app, client):
        """Profile template snippets appear in the rendered config."""
        from app.models import Device
        from app.services import ansible as ansible_service

        profile = _create_profile(
            client,
            "access-leaf",
            "access-switch",
            [
                ("vlan", "vlan 42\n name test-vlan\n!\n"),
                ("routing", "ip route 0.0.0.0/0 {{ default_gateway }}\n"),
            ],
            {"default_gateway": "10.0.1.1"},
        )
        device = Device.query.filter_by(netbox_id=101).one()

        with app.app_context():
            result = ansible_service.render_preview(app, device, profile)

        assert "vlan 42" in result.config
        assert "name test-vlan" in result.config
        assert "ip route 0.0.0.0/0 10.0.1.1" in result.config
        snippet_names = {s["name"] for s in result.snippets}
        assert "vlan" in snippet_names and "routing" in snippet_names
        assert result.variables_used.get("default_gateway") == "10.0.1.1"

    def test_host_vars_override_beats_group_vars(self, app, client, db_session):
        """Device host_vars override profile group_vars."""
        from app.models import Device
        from app.services import ansible as ansible_service
        from app.services import profiles as profiles_service

        profile = _create_profile(
            client,
            "access-leaf",
            "access-switch",
            [("routing", "ip route 0.0.0.0/0 {{ default_gateway }}\n")],
            {"default_gateway": "10.0.1.1"},
        )
        device = Device.query.filter_by(netbox_id=101).one()
        profiles_service.save_device_overrides(app, device, {"default_gateway": "10.9.9.9"})
        db_session.commit()

        with app.app_context():
            result = ansible_service.render_preview(app, device, profile)

        assert "ip route 0.0.0.0/0 10.9.9.9" in result.config
        assert "10.0.1.1" not in result.config

    def test_preview_failure_raises_ansible_preview_error(self, app, client):
        """A broken template surfaces AnsiblePreviewError."""
        from app.models import Device
        from app.services import ansible as ansible_service

        profile = _create_profile(
            client,
            "access-leaf",
            "access-switch",
            [("routing", "{% for _ in does_not_exist %}x{% endfor %}")],
            {},
        )
        device = Device.query.filter_by(netbox_id=101).one()

        with app.app_context(), pytest.raises(ansible_service.AnsiblePreviewError) as excinfo:
            ansible_service.render_preview(app, device, profile)
        assert excinfo.value.returncode is not None

    def test_interface_blocks_appended_for_interface_templates(self, app, client):
        """Interface template rows render per-interface blocks."""
        from app.models import ConfigurationProfile, Device
        from app.services import ansible as ansible_service

        profile = _create_profile(
            client,
            "access-leaf",
            "access-switch",
            [],
            {},
        )
        from app.extensions import db

        it = ConfigurationProfile.query.filter_by(name="access-leaf").one()
        from app.models import InterfaceTemplate

        mapping = InterfaceTemplate(
            profile_id=it.id,
            name="access",
            content="switchport access vlan 100\n description lab-uplink\n",
            interface_type="TenGigabitEthernet",
            interface_range="1-2",
            display_order=1,
            is_enabled=True,
            git_path=f"templates/{it.name}/interfaces/access.j2",
        )
        db.session.add(mapping)
        db.session.commit()
        device = Device.query.filter_by(netbox_id=101).one()

        with app.app_context():
            result = ansible_service.render_preview(app, device, profile)

        assert result.config.count("description lab-uplink") == 2  # TenGigabitEthernet1-2
