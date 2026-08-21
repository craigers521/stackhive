"""Application configuration.

Maps environment variables onto Flask application config and carries the
default values for system settings persisted in the database.
"""
import base64
import os

from sqlalchemy.pool import StaticPool


def _int(name, default):
    """Read an integer environment variable, falling back to a default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def _bool(name, default):
    """Read a boolean environment variable (1/true/yes/on), falling back to a default."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    """Base Flask configuration sourced from the environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:////var/lib/stackhive/stackhive.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    NETBOX_URL = os.environ.get("NETBOX_URL", "http://localhost:8080/netbox")
    GITLAB_URL = os.environ.get("GITLAB_URL", "http://localhost:8080/gitlab")
    GITLAB_PROJECT_ID = os.environ.get("GITLAB_PROJECT_ID", "")
    GITLAB_SHARED_SECRET = os.environ.get("GITLAB_SHARED_SECRET", "")
    GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:8080/grafana")
    ZTP_BASE_URL = os.environ.get("ZTP_BASE_URL", "http://stackhive.local")
    MERAKI_API_BASE = os.environ.get("MERAKI_DASHBOARD_URL", "https://api.meraki.com/api/v1")
    MERAKI_ORG_ID = os.environ.get("MERAKI_ORG_ID", "")
    MERAKI_ORGANIZATION_ID = os.environ.get("MERAKI_ORGANIZATION_ID", MERAKI_ORG_ID)

    REFRESH_INTERVAL = _int("REFRESH_INTERVAL", 60)
    SYNC_INTERVAL = _int("SYNC_INTERVAL", 300)
    REFRESH_ENABLED = _bool("REFRESH_ENABLED", "true")

    ANSIBLE_REPO_PATH = os.environ.get("ANSIBLE_REPO_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ansible"))
    ANSIBLE_PLAYBOOK_CMD = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
    ANSIBLE_PREVIEW_TIMEOUT = _int("PREVIEW_TIMEOUT", 30)
    ANSIBLE_VERBOSE = _bool("ANSIBLE_VERBOSE", "false")

    GIT_WORKING_BRANCH = os.environ.get("GIT_WORKING_BRANCH", "working")
    GIT_PRODUCTION_BRANCH = os.environ.get("GIT_PRODUCTION_BRANCH", "main")
    GITLAB_WEBHOOK_TOKEN = os.environ.get("GITLAB_WEBHOOK_TOKEN", "")

    INFLUXDB_RETENTION_DAYS = _int("INFLUXDB_RETENTION_DAYS", 14)
    DRIFT_CHECK_ENABLED = _bool("DRIFT_CHECK_ENABLED", "true")

    DEVICE_DASHBOARD_UID = os.environ.get("DEVICE_DASHBOARD_UID", "stackhive-device-health")
    INFRA_DASHBOARD_UID = os.environ.get("INFRA_DASHBOARD_UID", "stackhive-infrastructure")

    CACHE_TYPE = "SimpleCache"
    CACHE_DEFAULT_TIMEOUT = 300

    HTTP_TIMEOUT = 10
    HTTP_RETRIES = 3


class TestingConfig(Config):
    """Test configuration using an isolated in-memory SQLite database."""

    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    ENCRYPTION_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()
    REFRESH_ENABLED = False
    SECRET_KEY = "test-secret-key"
    GIT_STRICT = False
    GITLAB_WEBHOOK_TOKEN = "test-webhook-token"
