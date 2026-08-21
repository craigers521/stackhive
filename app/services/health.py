"""Health checks for GET /api/health (DB + backend service reachability)."""
import logging

from flask import jsonify, current_app

logger = logging.getLogger(__name__)


def _check_db():
    """Return 'up' or 'down' for the local database."""
    from ..extensions import db
    from sqlalchemy import text

    try:
        db.session.execute(text("SELECT 1"))
        return "up"
    except Exception:
        return "down"


def _probe(url, headers=None, timeout=4):
    """HEAD/GET a URL and return 'up'/'down'; network errors map to 'down'."""
    import requests

    try:
        resp = requests.get(url, headers=headers or {}, timeout=timeout)
        return "up" if resp.status_code < 500 else "down"
    except requests.RequestException:
        return "down"


def check_health(app=None):
    """Build the /api/health payload; 503 only when the DB is unreachable."""
    app = app or current_app._get_current_object()
    from . import credential

    db_state = _check_db()
    services = {"db": db_state}

    netbox_url = app.config.get("NETBOX_URL", "")
    services["netbox"] = _probe(f"{netbox_url}/api/status/") if netbox_url else "down"

    gitlab_url = app.config.get("GITLAB_URL", "")
    gitlab_state = "down"
    if gitlab_url:
        try:
            token, _ = credential.resolve_token(app, "gitlab", "GITLAB_TOKEN")
            gitlab_state = _probe(f"{gitlab_url}/api/v4/version", headers={"PRIVATE-TOKEN": token})
        except credential.CredentialError:
            gitlab_state = "down"
    services["gitlab"] = gitlab_state

    grafana_url = app.config.get("GRAFANA_URL", "")
    grafana_state = "down"
    if grafana_url:
        try:
            token, _ = credential.resolve_token(app, "grafana", "GRAFANA_TOKEN")
            grafana_state = _probe(f"{grafana_url}/api/health", headers={"Authorization": f"Bearer {token}"})
        except credential.CredentialError:
            grafana_state = "down"
    services["grafana"] = grafana_state

    import os

    influx_url = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
    services["influxdb"] = _probe(f"{influx_url}/ping")

    status = "ok" if all(s == "up" for s in services.values()) else "degraded"
    if db_state == "down":
        return jsonify({"status": "unavailable", "services": services}), 503
    return jsonify({"status": status, "services": services}), 200
