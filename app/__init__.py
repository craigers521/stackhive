"""Flask application factory.

Wires extensions, blueprints, error handlers, RBAC user loading, structured
logging, and the background refresh loop into a single ``create_app()``.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .config import Config, TestingConfig
from .extensions import cache, db, login_manager, migrate


class AppError(HTTPException):
    """Application error carrying a user-facing detail message."""

    def __init__(self, status, error, details=None, headers=None):
        """Set the HTTP status, API error name, and optional details."""
        super().__init__(f"{status} {error}")
        self.code = status
        self.error = error
        self.details = details
        if headers:
            self.headers = headers

    def get_body(self, description=None):
        """Build the JSON error body consumed by API clients."""
        body = {"error": self.error}
        if self.details:
            body["details"] = self.details
        return body


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON objects (audit NFR, plan).

    Every record becomes one machine-parseable line with UTC timestamp,
    level, logger and message; exceptions ride in an ``exception`` field.
    Example output:
        {"ts": "2026-08-19T10:15:02.481Z", "level": "INFO", "logger": "app.audit",
         "message": "api_login_success user=admin ip=10.0.0.1"}
    """

    def format(self, record):
        """Format one record as a single-line JSON object (see class docstring)."""
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(app):
    """Configure structured JSON logging to stdout with per-area loggers.

    All application loggers emit one-JSON-object-per-line records via
    ``JsonLogFormatter``; the ``app.audit`` logger carries the auth and
    administrative action events (logins, user management, settings changes,
    credential rotation, approvals, RBAC failures) required by the plan.
    """
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger("app")
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    for name in ("app.audit", "app.blueprints"):
        child = logging.getLogger(name)
        child.setLevel(level)
        child.propagate = True


def _error_response(code):
    """Build the standardized error body for a status code."""
    names = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }
    body = {"error": names.get(code, "Error")}
    if isinstance(getattr(request, "context_error", None), AppError):
        if request.context_error.details:
            body["details"] = request.context_error.details
    return jsonify(body), code


def register_error_handlers(app):
    """Register global error handlers (JSON for /api, Bootstrap HTML otherwise)."""

    def is_api():
        """True when the current request targets the JSON API."""
        return request.path.startswith("/api/")

    @app.errorhandler(AppError)
    def handle_app_error(err):
        """Render an AppError as JSON (API) or a Bootstrap error page (HTML)."""
        body = {"error": err.error}
        if err.details:
            body["details"] = err.details
        if is_api() or request.accept_mimetypes.best == "application/json":
            return jsonify(body), err.code
        template_map = {403: "403", 404: "404", 500: "500", 503: "503"}
        code = str(err.code) if str(err.code) in template_map else "404"
        return (
            render_template(f"errors/{code}.html", status=err.code, message=err.details or err.error),
            err.code,
        )

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        """Render Flask HTTPException (404, 405, ...) as JSON or HTML."""
        if is_api():
            return _error_response(err.code)
        template_map = {403: "403", 404: "404", 500: "500", 503: "503"}
        code = str(err.code) if str(err.code) in template_map else "404"
        return (
            render_template(
                f"errors/{code}.html", status=err.code, message=err.description or err.name
            ),
            err.code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected(err):
        """Log unexpected errors and return a generic 500 response."""
        logging.getLogger("app").exception("unhandled_error path=%s", request.path)
        if is_api() or request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "Internal Server Error"}), 500
        return render_template("errors/500.html", status=500, message="Unexpected server error"), 500


def register_blueprints(app):
    """Import every blueprint module and attach it to the app."""
    from .routes import (
        auth,
        dashboard,
        inventory,
        profiles,
        deployments,
        monitoring,
        onboarding,
        settings,
        ztp,
    )

    for module in (auth, dashboard, inventory, profiles, deployments, monitoring, onboarding, settings, ztp):
        app.register_blueprint(module.bp)


def register_health(app):
    """Expose GET /api/health for Traefik and operators (public endpoint)."""
    from .services import health as health_service

    @app.route("/api/health")
    def health():
        """GET /api/health: public liveness probe for Traefik and operators."""
        return health_service.check_health(app)


def register_user_loader(app):
    """Load the logged-in user for Flask-Login sessions."""
    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        """Flask-Login user loader: resolve the session user id to a User."""
        return db.session.get(User, int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        """401 handler: JSON for the API, redirect to login for pages."""
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return app.redirect("/auth/login?next=" + request.path)


def register_context_processor(app):
    """Expose shared helpers to all templates."""
    from .services import settings as settings_service

    @app.context_processor
    def inject_globals():
        """Template context processor injecting shared values into every page."""
        def tool_links():
            """External tool links (Grafana/NetBox/GitLab) for the navigation bar."""
            try:
                return {
                    "grafana": settings_service.get_setting(app, "grafana_url"),
                    "netbox": settings_service.get_setting(app, "netbox_url"),
                    "gitlab": settings_service.get_setting(app, "gitlab_url"),
                }
            except Exception:
                return {"grafana": "", "netbox": "", "gitlab": ""}

        return {"tool_links": tool_links(), "csrf_token": generate_csrf_token}


def generate_csrf_token():
    """Produce a CSRF token for HTML form templates."""
    from flask_wtf.csrf import generate_csrf

    return generate_csrf()


def register_csrf(app):
    """Enforce CSRF tokens on HTML form POSTs; JSON /api routes are exempt."""
    from flask_wtf.csrf import CSRFProtect

    _csrf = CSRFProtect()

    @app.before_request
    def _enforce_csrf():
        """Require a CSRF token on mutating non-API, non-ZTP requests."""
        if not app.config.get("WTF_CSRF_ENABLED", True):
            return
        if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
            return
        if request.path.startswith("/api/"):
            return
        if request.path.startswith("/ztp/"):
            return
        if request.headers.get("Content-Type", "").startswith("application/json"):
            token = request.headers.get("X-CSRFToken")
        else:
            token = request.form.get("csrf_token")
        if not token:
            app.logger.warning("csrf_token_missing %s %s", request.method, request.path)
            raise AppError(400, "Bad Request", "CSRF token missing")
        try:
            _csrf.validate_csrf_token(request, token)
        except AppError:
            raise
        except Exception:
            raise AppError(400, "Bad Request", "CSRF token invalid")


def start_refresh_thread(app):
    """Start the background status/sync loop unless disabled (e.g. tests).

    Single-instance across gunicorn workers: every worker's refresh thread
    contends for an exclusive advisory lock on
    ``<instance_path>/refresh.lock`` and only the lock holder executes work
    items. The lock is held for the thread's lifetime (released by the OS
    when the process exits), so status polling, NetBox sync, pipeline
    polling, and the daily jobs run exactly once fleet-wide; if the leader
    worker dies, a remaining worker takes over on its next wake (<=10s).
    """
    if not app.config.get("REFRESH_ENABLED", True):
        return None

    import fcntl
    import threading
    import time

    from .services import refresh

    stop = threading.Event()
    app.extensions["refresh_stop"] = stop
    os.makedirs(app.instance_path, exist_ok=True)
    lock_path = os.path.join(app.instance_path, "refresh.lock")

    def runner():
        """Refresh loop body: serialized work behind the cross-worker lock."""
        interval = int(app.config.get("REFRESH_INTERVAL", 60))
        last_sync = 0
        leader = False
        lock_fd = open(lock_path, "a+")
        while not stop.is_set():
            if not leader:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    leader = True
                    app.logger.info("refresh_leader_acquired pid=%d", os.getpid())
                except OSError:
                    stop.wait(min(10, interval))
                    continue
            started = time.monotonic()
            try:
                with app.app_context():
                    refresh.refresh_device_statuses(app)
                    if time.monotonic() - last_sync >= int(app.config.get("SYNC_INTERVAL", 300)):
                        refresh.refresh_inventory(app)
                        last_sync = time.monotonic()
                    refresh.refresh_deployment_pipelines(app)
                    refresh.refresh_ztp_onboarding(app)
                    refresh.refresh_ztp_artifact_cleanup(app)
                    refresh.refresh_drift_check(app)
            except Exception:
                app.logger.exception("refresh_loop_error")
            finally:
                try:
                    db.session.remove()
                except Exception:  # noqa: BLE001 - session cleanup best-effort
                    pass
            elapsed = time.monotonic() - started
            stop.wait(max(1.0, interval - elapsed))

    thread = threading.Thread(target=runner, name="stackhive-refresh", daemon=True)
    app.extensions["refresh_thread"] = thread
    thread.start()
    return thread


def create_app(config_object=None):
    """Create and configure the StackHive Flask application."""
    instance_path = os.environ.get(
        "STACKHIVE_INSTANCE", os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance")
    )
    os.makedirs(instance_path, exist_ok=True)
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        instance_path=instance_path,
    )
    app.config.from_object(config_object or Config)

    db.init_app(app)
    migrate.init_app(
        app, db, directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "alembic")
    )
    login_manager.init_app(app)
    cache.init_app(app, config={"CACHE_TYPE": app.config["CACHE_TYPE"],
                                "CACHE_DEFAULT_TIMEOUT": app.config["CACHE_DEFAULT_TIMEOUT"]})

    setup_logging(app)
    register_user_loader(app)
    register_context_processor(app)
    register_csrf(app)
    register_blueprints(app)
    register_health(app)
    register_error_handlers(app)

    from . import cli as cli_module

    for command in cli_module.ALL_COMMANDS:
        app.cli.add_command(command)

    app.extensions["refresh_thread"] = start_refresh_thread(app)
    return app
