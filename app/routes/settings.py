"""Settings blueprint: system settings, credential rotation, user management."""
import logging
import re

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import AppError
from ..decorators import admin_required
from ..extensions import db
from ..models import User, Role
from ..services import credential, settings as settings_service

logger = logging.getLogger(__name__)
audit = logging.getLogger("app.audit")

bp = Blueprint("settings", __name__)

ROLES = ("viewer", "editor", "admin")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{2,64}$")
MIN_PASSWORD_LENGTH = 8
EDITABLE_SETTING_KEYS = [key for key in settings_service.SETTING_DEFAULTS]


def _user_dict(user):
    """Serialize a user for API responses."""
    return {
        "user_id": str(user.id),
        "username": user.username,
        "role": user.role,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if user.created_at else None,
    }


def _active_admin_count():
    """Number of users with the admin role that are currently active."""
    return User.query.filter_by(role=Role.ADMIN.value, is_active=True).count()


def _guard_deactivation(user):
    """Raise AppError when the given user may not be deactivated.

    Refuses self-deactivation and deactivation of the last active admin
    (data-model User constraint: "cannot deactivate last admin").
    """
    if user.id == current_user.id:
        raise AppError(400, "Bad Request", "you cannot deactivate your own account")
    if user.role == Role.ADMIN.value and _active_admin_count() <= 1:
        raise AppError(400, "Bad Request", "cannot deactivate the last active admin")


def _apply_user_change(user, role_value, active_value=None):
    """Apply a role and/or active-state change to a user with admin guards.

    ``active_value`` of None leaves the current state untouched; a boolean
    False transition on an active user runs the last-admin/self guards.
    """
    if role_value:
        if role_value not in ROLES:
            raise AppError(400, "Bad Request", "role must be viewer, editor, or admin")
        if user.id == current_user.id and role_value != Role.ADMIN.value:
            raise AppError(400, "Bad Request", "you cannot demote your own admin account")
        user.role = role_value
    if active_value is not None:
        new_active = bool(active_value)
        if not new_active and user.is_active:
            _guard_deactivation(user)
        user.is_active = new_active


def _validate_password(password):
    """Reject passwords shorter than the minimum length."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise AppError(400, "Bad Request", f"password must be at least {MIN_PASSWORD_LENGTH} characters")


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

@bp.get("/settings")
@admin_required
def index():
    """Admin settings page: system settings, credentials, users."""
    values = settings_service.public_settings(current_app)
    users = User.query.order_by(User.username).all()
    return render_template(
        "settings/index.html",
        settings=values,
        keys=EDITABLE_SETTING_KEYS,
        users=users,
        roles=ROLES,
        active_admins=_active_admin_count(),
    )


@bp.post("/settings")
@admin_required
def save():
    """Persist system settings from the admin form."""
    keys = []
    for key in EDITABLE_SETTING_KEYS:
        if key not in request.form:
            continue
        value = request.form.get(key) or ""
        try:
            settings_service.set_setting(current_app, key, value)
            keys.append(key)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("settings.index"))
    audit.info("settings_changed keys=%s by=%s", ",".join(keys) or "none", current_user.username)
    flash("Settings saved", "success")
    return redirect(url_for("settings.index"))


@bp.post("/settings/<service_name>/rotate")
@admin_required
def rotate(service_name):
    """Rotate a service credential from the admin form."""
    token = request.form.get("token") or ""
    base_url = request.form.get("base_url") or None
    try:
        credential.upsert_credential(current_app, service_name, token, base_url=base_url,
                                     rotated_by=current_user.username)
    except credential.CredentialError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("settings.index"))
    flash(f"{service_name} credential rotated", "success")
    return redirect(url_for("settings.index"))


@bp.get("/settings/password")
@login_required
def change_own_password():
    """Self-service password change page (all roles)."""
    return render_template("settings/password.html")


@bp.post("/settings/password")
@login_required
def change_own_password_submit():
    """POST /settings/password: self-service password change."""
    current_pwd = request.form.get("current_password") or ""
    new_pwd = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not login_manager_check(current_user, current_pwd):
        flash("Current password is incorrect", "danger")
        return redirect(url_for("settings.change_own_password"))
    _validate_password(new_pwd)
    if new_pwd != confirm:
        flash("New passwords do not match", "danger")
        return redirect(url_for("settings.change_own_password"))
    current_user.set_password(new_pwd)
    db.session.commit()
    flash("Password updated", "success")
    return redirect(url_for("settings.change_own_password"))


def login_manager_check(user, password):
    """Verify a user's password against the stored hash."""
    return user.check_password(password)


# ---------------------------------------------------------------------------
# REST API — settings
# ---------------------------------------------------------------------------

@bp.get("/api/settings")
@admin_required
def api_get_settings():
    """GET /api/settings: public settings with tokens redacted."""
    return jsonify(settings_service.public_settings(current_app))


@bp.put("/api/settings")
@admin_required
def api_update_settings():
    """PUT /api/settings: update editable settings; 400 on unknown keys."""
    data = request.get_json(silent=True) or {}
    updated = {}
    for key, value in data.items():
        if key not in settings_service.SETTING_DEFAULTS:
            raise AppError(400, "Bad Request", f"unknown setting: {key}")
        if key.endswith("_token") or key.endswith("_key"):
            continue  # credentials rotate via the dedicated endpoint
        try:
            settings_service.set_setting(current_app, key, value)
            updated[key] = settings_service.get_setting(current_app, key)
        except (ValueError, TypeError) as exc:
            raise AppError(400, "Bad Request", f"invalid value for {key}: {exc}")
    return jsonify(updated)


@bp.put("/api/settings/credentials/<service_name>")
@admin_required
def api_rotate_credential(service_name):
    """PUT /api/settings/credentials/<svc>: rotate a service token."""
    data = request.get_json(silent=True) or {}
    token = data.get("token") or ""
    base_url = data.get("base_url") or None
    if not token:
        raise AppError(400, "Bad Request", "token is required")
    try:
        credential.upsert_credential(current_app, service_name, token, base_url=base_url,
                                     rotated_by=current_user.username)
    except credential.CredentialError as exc:
        if "Unknown service" in str(exc):
            raise AppError(404, "Not Found", str(exc))
        raise AppError(400, "Bad Request", str(exc))
    return jsonify({"service": service_name, "token": credential.redact(token)})


# ---------------------------------------------------------------------------
# REST API — users
# ---------------------------------------------------------------------------

@bp.get("/api/users")
@admin_required
def api_users():
    """GET /api/users: list all users."""
    return jsonify({"users": [_user_dict(u) for u in User.query.order_by(User.username).all()]})


@bp.post("/api/users")
@admin_required
def api_user_create():
    """POST /api/users: create a user (201)."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or "").strip()
    if not _USERNAME_RE.match(username):
        raise AppError(400, "Bad Request", "username must be 2-64 chars of [a-zA-Z0-9._-]")
    if role not in ROLES:
        raise AppError(400, "Bad Request", "role must be viewer, editor, or admin")
    _validate_password(password)
    if User.query.filter_by(username=username).first():
        raise AppError(409, "Conflict", "username already exists")
    user = User(username=username, email=f"{username}@stackhive.local", role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    audit.info("user_created username=%s role=%s by=%s", username, role, current_user.username)
    return jsonify(_user_dict(user)), 201


@bp.put("/api/users/<int:user_id>")
@admin_required
def api_user_update(user_id):
    """PUT /api/users/<id>: change role and/or active state.

    Self-demotion is refused; deactivation is refused for the user
    themselves and for the last active admin.
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise AppError(404, "Not Found", "user not found")
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip()
    active = data.get("is_active")
    _apply_user_change(user, role, active)
    db.session.add(user)
    db.session.commit()
    parts = []
    if role:
        parts.append(f"role={role}")
    if "is_active" in data:
        parts.append(f"is_active={bool(user.is_active)}")
    if parts:
        audit.info("user_updated username=%s %s by=%s", user.username, " ".join(parts), current_user.username)
    return jsonify(_user_dict(user))


@bp.delete("/api/users/<int:user_id>")
@admin_required
def api_user_delete(user_id):
    """DELETE /api/users/<id>: delete a user (guards: self, last admin)."""
    user = db.session.get(User, user_id)
    if user is None:
        raise AppError(404, "Not Found", "user not found")
    if user.id == current_user.id:
        raise AppError(400, "Bad Request", "you cannot delete your own account")
    if user.role == Role.ADMIN.value and _active_admin_count() <= 1:
        raise AppError(400, "Bad Request", "cannot delete the last active admin")
    db.session.delete(user)
    db.session.commit()
    audit.info("user_deleted username=%s by=%s", user.username, current_user.username)
    return "", 204


@bp.put("/api/users/<int:user_id>/password")
@admin_required
def api_user_password(user_id):
    """PUT /api/users/<id>/password: admin password reset."""
    user = db.session.get(User, user_id)
    if user is None:
        raise AppError(404, "Not Found", "user not found")
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    _validate_password(password)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    audit.info("user_password_reset username=%s by=%s", user.username, current_user.username)
    return jsonify(_user_dict(user))


# ---------------------------------------------------------------------------
# REST API — users (HTML forms)
# ---------------------------------------------------------------------------

@bp.post("/settings/users")
@admin_required
def user_create_form():
    """POST /settings/users: create a user from the settings table form.

    Form-encoded equivalent of ``POST /api/users`` (same validation); the
    response is a redirect with a flash message instead of JSON.
    """
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    role = (request.form.get("role") or "").strip()
    try:
        if not _USERNAME_RE.match(username):
            raise AppError(400, "Bad Request", "username must be 2-64 chars of [a-zA-Z0-9._-]")
        if role not in ROLES:
            raise AppError(400, "Bad Request", "role must be viewer, editor, or admin")
        _validate_password(password)
        if User.query.filter_by(username=username).first():
            raise AppError(409, "Conflict", "username already exists")
    except AppError as exc:
        flash(exc.details or exc.name, "danger")
        return redirect(url_for("settings.index"))
    user = User(username=username, email=f"{username}@stackhive.local", role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    audit.info("user_created username=%s role=%s by=%s (ui)", username, role, current_user.username)
    flash(f"User {username} created", "success")
    return redirect(url_for("settings.index"))


@bp.post("/settings/users/<int:user_id>")
@admin_required
def user_update_form(user_id):
    """POST /settings/users/<id>: save role/active state from the user table.

    Server-rendered form behind the users table (no client-side JS): the
    same guards as ``PUT /api/users/<id>`` apply (self-demotion, self- and
    last-admin deactivation).
    """
    user = db.session.get(User, user_id)
    if user is None:
        flash("User not found", "danger")
        return redirect(url_for("settings.index"))
    changed = []
    try:
        role = (request.form.get("role") or "").strip()
        if role and role != user.role:
            _apply_user_change(user, role, None)
            changed.append(f"role={role}")
        if "is_active" in request.form:
            new_active = request.form.get("is_active") == "1"
            _apply_user_change(user, "", new_active)
            changed.append(f"is_active={new_active}")
    except AppError as exc:
        db.session.rollback()
        flash(exc.details or exc.name, "danger")
        return redirect(url_for("settings.index"))
    db.session.add(user)
    db.session.commit()
    if changed:
        audit.info("user_updated username=%s %s by=%s (ui)", user.username, " ".join(changed), current_user.username)
    flash(f"User {user.username} updated" if changed else "No changes", "success")
    return redirect(url_for("settings.index"))
