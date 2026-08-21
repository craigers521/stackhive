"""Role-based access control decorators built on Flask-Login.

The role hierarchy is flat with ordering viewer < editor < admin; a route
declares the minimum roles allowed via ``role_required`` or one of the
convenience decorators. All denials are audit-logged.
"""
import logging
from functools import wraps

from flask import abort, request
from flask_login import current_user

from .models import Role

audit_logger = logging.getLogger("app.audit")


def _deny(f, allowed_roles):
    """Log the RBAC denial and abort with 401 (anonymous) or 403 (wrong role)."""
    if not current_user.is_authenticated:
        audit_logger.warning(
            "rbac_denied endpoint=%s ip=%s reason=unauthenticated path=%s",
            getattr(f, "__name__", "?"), request.remote_addr, request.path,
        )
        abort(401)
    if current_user.role not in allowed_roles:
        audit_logger.warning(
            "rbac_denied endpoint=%s user=%s role=%s required=%s path=%s",
            getattr(f, "__name__", "?"), current_user.username, current_user.role,
            ",".join(allowed_roles), request.path,
        )
        abort(403)


def role_required(*roles):
    """Require the current user to hold one of the given roles.

    Accepts ``Role`` members or plain strings. Anonymous users get 401,
    authenticated users with an insufficient role get 403.
    """
    allowed = [r.value if isinstance(r, Role) else str(r) for r in roles]

    def decorator(f):
        """Wrap a view so the allowed-roles check runs before every call."""
        @wraps(f)
        def wrapped(*args, **kwargs):
            """Enforce the role check (audit-log + 401/403), then delegate to the view."""
            _deny(f, allowed)
            return f(*args, **kwargs)

        return wrapped

    return decorator


def viewer_required(f):
    """Allow any authenticated role (viewer and above)."""
    return role_required(Role.VIEWER, Role.EDITOR, Role.ADMIN)(f)


def editor_required(f):
    """Allow editor and admin roles."""
    return role_required(Role.EDITOR, Role.ADMIN)(f)


def admin_required(f):
    """Allow the admin role only."""
    return role_required(Role.ADMIN)(f)
