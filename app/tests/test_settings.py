"""Settings tests: settings API, credential rotation, user management, RBAC."""
import pytest


class TestSettingsAPI:
    """System settings and credential rotation API."""
    def test_get_settings_redacts_tokens(self, client, monkeypatch):
        """Token values are redacted in the response."""
        from conftest import login
        from app.services import credential

        monkeypatch.setenv("NETBOX_TOKEN", "supersecrettoken")
        login(client, "admin")
        data = client.get("/api/settings").get_json()
        assert data["netbox_token"].startswith("****")
        assert "supersecrettoken" not in str(data["netbox_token"])
        assert "netbox_url" in data

    def test_put_settings_persists(self, client, db_session):
        """PUT persists the editable settings."""
        from conftest import login
        from app.extensions import db
        from app.models import SystemSetting

        login(client, "admin")
        resp = client.put("/api/settings", json={"refresh_interval": 120})
        assert resp.status_code == 200
        assert resp.get_json()["refresh_interval"] == 120
        row = SystemSetting.query.filter_by(key="refresh_interval").one()
        assert row.value == "120"

    def test_put_unknown_setting_400(self, client):
        """An unknown setting key returns 400."""
        from conftest import login

        login(client, "admin")
        assert client.put("/api/settings", json={"not_a_setting": 1}).status_code == 400

    def test_rotate_credential(self, client):
        """Credential rotation stores and redacts the new token."""
        from conftest import login
        from app.services import credential

        login(client, "admin")
        resp = client.put("/api/settings/credentials/netbox", json={"token": "newtoken123"})
        assert resp.status_code == 200
        assert resp.get_json()["token"].startswith("****")
        stored = credential.get_credential(client.application, "netbox")
        token, _ = credential.resolve_token(client.application, "netbox", "NETBOX_TOKEN")
        assert token == "newtoken123"

    def test_settings_require_admin(self, client):
        """Non-admins are denied settings access."""
        from conftest import login

        login(client, "editor")
        assert client.get("/api/settings").status_code == 403
        assert client.get("/settings").status_code == 403
        login(client, "viewer")
        assert client.get("/api/settings").status_code == 403


class TestUsersAPI:
    """User management API and safety guards."""
    def test_create_user(self, client):
        """Create a user with a role and password."""
        from conftest import login

        login(client, "admin")
        resp = client.post(
            "/api/users",
            json={"username": "newbie", "password": "Passw0rd!", "role": "viewer"},
        )
        assert resp.status_code == 201
        assert resp.get_json()["role"] == "viewer"

    def test_create_user_invalid_role_400(self, client):
        """An invalid role is rejected."""
        from conftest import login

        login(client, "admin")
        resp = client.post(
            "/api/users",
            json={"username": "newbie", "password": "Passw0rd!", "role": "wizard"},
        )
        assert resp.status_code == 400

    def test_create_user_short_password_400(self, client):
        """A short password is rejected."""
        from conftest import login

        login(client, "admin")
        resp = client.post("/api/users", json={"username": "newbie", "password": "short", "role": "viewer"})
        assert resp.status_code == 400

    def test_update_role_and_demote_self_400(self, client):
        """Role updates work; self-demotion is refused."""
        from conftest import login
        from app.models import User

        login(client, "admin")
        newbie = User.query.filter_by(username="viewer").one()
        resp = client.put(f"/api/users/{newbie.id}", json={"role": "editor"})
        assert resp.status_code == 200
        assert User.query.get(newbie.id).role == "editor"

        admin = User.query.filter_by(username="admin").one()
        resp = client.put(f"/api/users/{admin.id}", json={"role": "viewer"})
        assert resp.status_code == 400

    def test_cannot_delete_last_admin(self, client):
        """The last active admin cannot be deleted."""
        from conftest import login
        from app.models import User

        login(client, "admin")
        admin = User.query.filter_by(username="admin").one()
        assert client.delete(f"/api/users/{admin.id}").status_code == 400

    def test_cannot_delete_self(self, client):
        """A user cannot delete their own account."""
        from conftest import login
        from app.models import User

        login(client, "admin")
        admin = User.query.filter_by(username="admin").one()
        user = User.query.filter_by(username="viewer").one()
        # admin is the only admin; deleting self is blocked first
        resp = client.delete(f"/api/users/{admin.id}")
        assert resp.status_code == 400
        login(client, "editor")
        viewer = User.query.filter_by(username="viewer").one()
        assert client.delete(f"/api/users/{viewer.id}").status_code == 403

    def test_users_require_admin(self, client):
        """Non-admins are denied the user API."""
        from conftest import login

        login(client, "editor")
        assert client.get("/api/users").status_code == 403
        assert client.post("/api/users", json={"username": "x1", "password": "Passw0rd!", "role": "viewer"}).status_code == 403


class TestChangeOwnPassword:
    """Self-service password change."""
    def test_change_password_flow(self, client, db_session):
        """The correct current password updates the password."""
        from conftest import login
        from app.models import User

        login(client, "viewer")
        resp = client.post(
            "/settings/password",
            data={
                "current_password": "Test123!",
                "new_password": "NewPass456!",
                "confirm_password": "NewPass456!",
            },
        )
        assert resp.status_code == 302
        user = User.query.filter_by(username="viewer").one()
        assert user.check_password("NewPass456!")

    def test_wrong_current_password_rejected(self, client, db_session):
        """A wrong current password is rejected."""
        from conftest import login
        from app.models import User

        login(client, "viewer")
        client.post(
            "/settings/password",
            data={
                "current_password": "Wrong123!",
                "new_password": "NewPass456!",
                "confirm_password": "NewPass456!",
            },
        )
        user = User.query.filter_by(username="viewer").one()
        assert user.check_password("Test123!")

    def test_password_change_page_reachable_by_all_roles(self, app):
        """All roles can open the password change page."""
        from conftest import login

        for role in ("admin", "editor", "viewer"):
            c = app.test_client()
            login(c, role)
            resp = c.get("/settings/password")
            assert resp.status_code == 200
