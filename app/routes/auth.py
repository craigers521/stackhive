"""Authentication blueprint: login/logout for HTML pages and JSON API."""
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from ..decorators import viewer_required
from ..extensions import db
from ..models import User
from .. import AppError

logger = logging.getLogger(__name__)
audit = logging.getLogger("app.audit")

bp = Blueprint("auth", __name__)


def _find_user(username):
    """Look up an active user by username for login."""
    return User.query.filter_by(username=username).first()


@bp.route("/auth/login", methods=["GET", "POST"])
def login_page():
    """Server-rendered login form; redirects to the dashboard on success."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = _find_user(username)
        if user is not None and user.is_active and user.check_password(password):
            login_user(user)
            user.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
            db.session.commit()
            audit.info("login_success user=%s ip=%s", user.username, request.remote_addr)
            next_url = request.args.get("next") or url_for("dashboard.index")
            return redirect(next_url)
        audit.warning("login_failure user=%s ip=%s", username, request.remote_addr)
        error = "Invalid username or password"
    return render_template("auth/login.html", error=error), (200 if error else 200)


@bp.post("/api/auth/login")
def api_login():
    """JSON login endpoint per contracts/rest-api.md."""
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return AppError(400, "Bad Request", "username and password are required")
    user = _find_user(username)
    if user is None or not user.is_active or not user.check_password(password):
        audit.warning("api_login_failure user=%s ip=%s", username, request.remote_addr)
        return jsonify({"error": "Unauthorized"}), 401
    login_user(user)
    user.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    audit.info("api_login_success user=%s ip=%s", user.username, request.remote_addr)
    return jsonify({"user_id": str(user.id), "username": user.username, "role": user.role}), 200


@bp.post("/api/auth/logout")
def api_logout():
    """End the current session (any authenticated user)."""
    if current_user.is_authenticated:
        audit.info("logout user=%s", current_user.username)
    logout_user()
    return "", 204


@bp.post("/auth/logout")
def login_logout():
    """HTML session end; returns to the login page."""
    if current_user.is_authenticated:
        audit.info("logout user=%s", current_user.username)
    logout_user()
    return redirect(url_for("auth.login_page"))


@bp.get("/api/auth/me")
@viewer_required
def me():
    """Return the current authenticated user."""
    return jsonify(
        {
            "user_id": str(current_user.id),
            "username": current_user.username,
            "role": current_user.role,
        }
    ), 200


@bp.put("/api/auth/password")
@viewer_required
def change_password():
    """Self-service password change; validates the current password first."""
    data = request.get_json(silent=True) or {}
    current = data.get("current_password")
    new = data.get("new_password")
    if not current or not new:
        return jsonify({"error": "Bad Request", "details": "current_password and new_password are required"})
    if not current_user.check_password(current):
        return jsonify({"error": "Bad Request", "details": "current password is incorrect"})
    if len(new) < 8:
        return jsonify({"error": "Bad Request", "details": "new password must be at least 8 characters"})
    current_user.set_password(new)
    db.session.commit()
    audit.info("password_changed user=%s", current_user.username)
    return "", 204
