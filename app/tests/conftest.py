"""Shared test fixtures: app factory with in-memory SQLite, seeded data, login."""
import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Device, DeviceType, User

PASSWORD = "Test123!"


def _make_user(username, role):
    """Create a seeded user with the shared test password."""
    user = User(username=username, email=f"{username}@example.com", role=role)
    user.set_password(PASSWORD)
    db.session.add(user)
    return user


@pytest.fixture
def app():
    """Application with in-memory SQLite and schema created; isolated per test."""
    application = create_app(TestingConfig)
    with application.app_context():
        db.create_all()
        _make_user("admin", "admin")
        _make_user("editor", "editor")
        _make_user("viewer", "viewer")
        device_type = DeviceType(
            netbox_id=10,
            manufacturer="cisco",
            model="C9300-48P",
            part_number="C9300-48P",
            interface_count=52,
            interface_types={"GigabitEthernet": 48, "TenGigabitEthernet": 4},
            management_interfaces=["MgmtEth0/RP0/cpu0"],
            uplink_slots=4,
        )
        db.session.add(device_type)
        db.session.flush()
        devices = [
            Device(
                netbox_id=101,
                hostname="sw-access-01",
                serial_number="FCW2345A001",
                mac_address="aa:bb:cc:dd:ee:01",
                mgmt_ip="10.0.1.11/24",
                role="access-switch",
                site="HQ",
                device_type_id=device_type.id,
                monitoring_status="up",
                config_status="deployed",
            ),
            Device(
                netbox_id=102,
                hostname="sw-access-02",
                serial_number="FCW2345A002",
                mac_address="aa:bb:cc:dd:ee:02",
                mgmt_ip="10.0.1.12/24",
                role="access-switch",
                site="HQ",
                device_type_id=device_type.id,
                monitoring_status="unknown",
                config_status="pending",
            ),
            Device(
                netbox_id=103,
                hostname="sw-core-01",
                serial_number="FCW2345C001",
                mac_address="aa:bb:cc:dd:ee:03",
                mgmt_ip="10.0.1.1/24",
                role="core-switch",
                site="DC",
                device_type_id=device_type.id,
                monitoring_status="down",
                config_status="deployed",
            ),
        ]
        db.session.add_all(devices)
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client against the seeded app (not logged in)."""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """The app's SQLAlchemy session, exposed for direct assertions."""
    return db.session


def login(client, username, password=PASSWORD):
    """Log in via the JSON API; returns the response."""
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def logged_client(client_fixture_or_app, username):
    """Return a test client already authenticated as the given user."""
    app = client_fixture_or_app
    test_client = app.test_client()
    login(test_client, username)
    return test_client
