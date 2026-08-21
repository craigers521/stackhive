"""Configuration profile, template, interface template and variable models."""
import re

from app.extensions import db

from .mixins import TimestampMixin

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
VARIABLE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALUE_TYPES = ("string", "int", "bool", "list", "dict")


def validate_profile_name(name):
    """Return True if the profile name is slug-safe (alnum, hyphen, underscore)."""
    return bool(name) and bool(PROFILE_NAME_RE.match(name))


def validate_variable_key(key):
    """Return True if the key is a valid Ansible variable identifier."""
    return bool(key) and bool(VARIABLE_KEY_RE.match(key))


class ConfigurationProfile(TimestampMixin, db.Model):
    """A named collection of templates and variables bound to a device role.

    Maps to Ansible ``group_vars/<name>/``; at most one active profile may
    target a given device role.
    """

    __tablename__ = "configuration_profiles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    device_role = db.Column(db.String(128), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    git_path = db.Column(db.String(512), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.String(32), nullable=False, default="1")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    templates = db.relationship(
        "ConfigurationTemplate",
        backref="profile",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="ConfigurationTemplate.display_order",
    )
    interface_templates = db.relationship(
        "InterfaceTemplate",
        backref="profile",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="InterfaceTemplate.display_order",
    )
    variables = db.relationship(
        "ConfigurationVariable",
        foreign_keys="ConfigurationVariable.profile_id",
        backref="profile",
        lazy=True,
        cascade="all, delete-orphan",
    )
    deployments = db.relationship("DeploymentRecord", backref="profile", lazy=True)

    def to_list_dict(self):
        """Serialize the list-row object for GET /api/profiles."""
        return {
            "id": str(self.id),
            "name": self.name,
            "device_role": self.device_role,
            "templates": len(self.templates),
            "variables": len(self.variables),
            "updated_at": self.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.updated_at else None,
            "updated_by": (self.profile_updated_by.username if self.profile_updated_by else None),
        }

    @property
    def profile_updated_by(self):
        """The user who last edited this profile."""
        updated_by = db.session.get("User", self.updated_by_id) if self.updated_by_id else None
        return updated_by

    def to_detail_dict(self):
        """Serialize the full profile object for GET /api/profiles/{id}."""
        return {
            "id": str(self.id),
            "name": self.name,
            "device_role": self.device_role,
            "description": self.description,
            "is_active": self.is_active,
            "version": self.version,
            "templates": [
                {
                    "id": str(t.id),
                    "name": t.name,
                    "content": t.content,
                    "order": t.display_order,
                    "is_enabled": t.is_enabled,
                }
                for t in self.templates
            ],
            "variables": {v.key: v.value for v in self.variables},
            "variable_meta": {
                v.key: {"value_type": v.value_type, "description": v.description}
                for v in self.variables
            },
            "interface_mappings": [
                {
                    "id": str(it.id),
                    "name": it.name,
                    "interface_type": it.interface_type,
                    "interface_range": it.interface_range,
                    "content": it.content,
                    "order": it.display_order,
                    "is_enabled": it.is_enabled,
                }
                for it in self.interface_templates
            ],
            "updated_at": self.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.updated_at else None,
            "updated_by": (self.profile_updated_by.username if self.profile_updated_by else None),
        }

    def __repr__(self):
        """Human-readable representation with name and active flag."""
        return f"<ConfigurationProfile {self.name} ({self.device_role})>"


class ConfigurationTemplate(TimestampMixin, db.Model):
    """A Jinja2 fragment rendering one self-contained config section."""

    __tablename__ = "configuration_templates"
    __table_args__ = (
        db.UniqueConstraint("profile_id", "name", name="uq_template_profile_name"),
        db.Index("ix_template_profile_order", "profile_id", "display_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("configuration_profiles.id"), nullable=False
    )
    name = db.Column(db.String(128), nullable=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    content = db.Column(db.Text, nullable=False)
    git_path = db.Column(db.String(512), nullable=False)
    config_section = db.Column(db.String(64), nullable=True)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)


class InterfaceTemplate(TimestampMixin, db.Model):
    """Per-interface Jinja2 template bound to an interface type and range."""

    __tablename__ = "interface_templates"
    __table_args__ = (
        db.UniqueConstraint("profile_id", "name", name="uq_iface_template_profile_name"),
        db.Index("ix_iface_template_profile_order", "profile_id", "display_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("configuration_profiles.id"), nullable=False
    )
    name = db.Column(db.String(128), nullable=False)
    interface_type = db.Column(db.String(64), nullable=False)
    interface_range = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    git_path = db.Column(db.String(512), nullable=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)


class ConfigurationVariable(TimestampMixin, db.Model):
    """A key/value variable persisted as YAML in Git.

    Scope ``profile`` rows map to ``group_vars/<name>/vars.yml``; scope
    ``device`` rows map to ``host_vars/<hostname>.yml`` (DeviceOverride view).
    """

    __tablename__ = "configuration_variables"
    __table_args__ = (
        db.Index("ix_var_scope_profile_key", "scope", "profile_id", "key"),
        db.Index("ix_var_scope_device_key", "scope", "device_id", "key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=False)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("configuration_profiles.id"), nullable=True
    )
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=True)
    key = db.Column(db.String(255), nullable=False)
    value = db.Column(db.Text, nullable=False)
    value_type = db.Column(db.String(16), nullable=False, default="string")
    description = db.Column(db.String(512), nullable=True)
    git_path = db.Column(db.String(512), nullable=False)

    @staticmethod
    def validate(scope, profile_id, device_id, value_type, key):
        """Validate scope/ownership/type/key rules; return an error string or None."""
        if scope not in ("profile", "device"):
            return "scope must be 'profile' or 'device'"
        if scope == "profile":
            if profile_id is None:
                return "profile_id is required for profile-scope variables"
            if device_id is not None:
                return "device_id must be null for profile-scope variables"
        else:
            if device_id is None:
                return "device_id is required for device-scope variables"
            if profile_id is not None:
                return "profile_id must be null for device-scope variables"
        if value_type not in VALUE_TYPES:
            return "value_type must be one of string, int, bool, list, dict"
        if not validate_variable_key(key):
            return "key must be a valid Ansible variable name"
        return None
