"""User account model with RBAC roles."""
from enum import Enum

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

from .mixins import TimestampMixin


class Role(Enum):
    """Permission levels, ordered viewer < editor < admin."""

    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class User(UserMixin, TimestampMixin, db.Model):
    """An authenticated platform user with a single assigned role."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(128), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False, default=Role.VIEWER.value, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_login = db.Column(db.DateTime, nullable=True)

    created_profiles = db.relationship(
        "ConfigurationProfile",
        foreign_keys="ConfigurationProfile.created_by_id",
        backref="created_by",
        lazy=True,
    )
    deployments = db.relationship(
        "DeploymentRecord",
        foreign_keys="DeploymentRecord.user_id",
        backref="operator",
        lazy=True,
    )
    approvals = db.relationship(
        "DeploymentRecord",
        foreign_keys="DeploymentRecord.approved_by_id",
        backref="approver",
        lazy=True,
    )

    def set_password(self, password):
        """Hash and store the given plaintext password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify a plaintext password against the stored hash."""
        return check_password_hash(self.password_hash, password)

    @property
    def role_enum(self):
        """Return the current role as a ``Role`` enum member."""
        return Role(self.role)

    def has_role(self, *roles):
        """Return True if the user holds one of the given roles."""
        return self.role in [r.value if isinstance(r, Role) else r for r in roles]

    def can_view(self):
        """All authenticated roles can view."""
        return self.is_authenticated

    def can_edit(self):
        """Editors and admins can modify profiles, variables and deployments."""
        return self.has_role(Role.EDITOR, Role.ADMIN)

    def can_admin(self):
        """Only admins can approve deployments and manage the system."""
        return self.has_role(Role.ADMIN)

    def to_dict(self):
        """Serialize the user for API responses (never includes the hash)."""
        return {
            "user_id": str(self.id),
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.created_at else None,
        }

    def __repr__(self):
        """Human-readable representation with username and role."""
        return f"<User {self.username} ({self.role})>"
