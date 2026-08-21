"""Effective system settings: database values override environment defaults."""
import logging

from . import credential
from ..extensions import db
from ..models import SystemSetting

logger = logging.getLogger(__name__)
audit = logging.getLogger("app.audit")

SETTING_DEFAULTS = {
    "netbox_url": ("NETBOX_URL", "http://localhost:8080/netbox"),
    "gitlab_url": ("GITLAB_URL", "http://localhost:8080/gitlab"),
    "gitlab_project_id": ("GITLAB_PROJECT_ID", ""),
    "grafana_url": ("GRAFANA_URL", "http://localhost:8080/grafana"),
    "ztp_base_url": ("ZTP_BASE_URL", "http://stackhive.local"),
    "meraki_api_base": ("MERAKI_DASHBOARD_URL", "https://api.meraki.com/api/v1"),
    "git_working_branch": ("GIT_WORKING_BRANCH", "working"),
    "git_production_branch": ("GIT_PRODUCTION_BRANCH", "main"),
    "refresh_interval": ("REFRESH_INTERVAL", "60"),
    "influxdb_retention_days": ("INFLUXDB_RETENTION_DAYS", "14"),
    "drift_check_enabled": ("DRIFT_CHECK_ENABLED", "true"),
    "device_dashboard_uid": ("DEVICE_DASHBOARD_UID", "stackhive-device-health"),
    "infra_dashboard_uid": ("INFRA_DASHBOARD_UID", "stackhive-infrastructure"),
    "ansible_repo_path": ("ANSIBLE_REPO_PATH", ""),
}

INT_SETTINGS = {"refresh_interval": int, "influxdb_retention_days": int}
BOOL_SETTINGS = {"drift_check_enabled": bool}


def get_setting(app, key):
    """Return the effective value for a setting key (DB override, else env, else default)."""
    entry = SETTING_DEFAULTS.get(key)
    if entry is None:
        return None
    env_key, default = entry
    row = SystemSetting.query.filter_by(key=key).first()
    raw = row.value if row is not None else (app.config.get(env_key) or default)
    converter = INT_SETTINGS.get(key)
    if converter is not None:
        try:
            return converter(raw)
        except (TypeError, ValueError):
            return converter(default)
    if key in BOOL_SETTINGS:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw


def set_setting(app, key, value):
    """Persist a setting value; unknown keys raise ValueError."""
    if key not in SETTING_DEFAULTS:
        raise ValueError(f"Unknown setting: {key}")
    if key in INT_SETTINGS:
        value = int(value)
    if key in BOOL_SETTINGS:
        value = str(value).strip().lower() in ("1", "true", "yes", "on")
    row = SystemSetting.query.filter_by(key=key).first()
    if row is None:
        row = SystemSetting(key=key, value=str(value))
    else:
        row.value = str(value)
    db.session.add(row)
    db.session.commit()
    # Administrative action: structured JSON audit event (plan: audit & logging).
    audit.info("setting_updated key=%s value=%s", key, str(value))
    return row


def public_settings(app):
    """Build the GET /api/settings payload with redacted tokens and URLs."""
    values = {}
    for key in SETTING_DEFAULTS:
        values[key] = get_setting(app, key)
    values["netbox_token"] = _redacted(app, "netbox", "NETBOX_TOKEN")
    values["gitlab_token"] = _redacted(app, "gitlab", "GITLAB_TOKEN")
    values["grafana_token"] = _redacted(app, "grafana", "GRAFANA_TOKEN")
    values["meraki_api_key"] = _redacted(app, "meraki", "MERAKI_API_KEY")
    return values


def _redacted(app, service_name, env_key):
    """Return the redacted token for a service, or '' when none is configured."""
    try:
        token, _row = credential.resolve_token(app, service_name, env_key)
    except credential.CredentialError:
        return ""
    return credential.redact(token)
