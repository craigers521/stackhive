"""Deployment record and per-device outcome models."""
import re

from app.extensions import db

from .mixins import TimestampMixin

DEPLOYMENT_STATUSES = ("pending", "approved", "running", "success", "failed", "cancelled")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_commit_sha(sha):
    """Return True if the value is a valid 40-character hex git SHA."""
    return bool(sha) and bool(_SHA_RE.match(sha))


class DeploymentRecord(TimestampMixin, db.Model):
    """An immutable log of one deployment batch.

    One record per deployment; per-device outcomes live in DeploymentDevice.
    Status moves only through the defined transitions and records are never
    rewritten in place (corrections are new records).
    """

    __tablename__ = "deployment_records"

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(
        db.Integer, db.ForeignKey("configuration_profiles.id"), nullable=False
    )
    device_count = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    git_commit_sha = db.Column(db.String(40), nullable=False)
    git_branch = db.Column(db.String(128), nullable=False, default="working")
    preview_output = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    config_diff = db.Column(db.Text, nullable=True)
    pipeline_id = db.Column(db.Integer, nullable=True)
    pipeline_url = db.Column(db.String(512), nullable=True)
    pipeline_status = db.Column(db.String(32), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    started_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    devices = db.relationship(
        "DeploymentDevice",
        backref="deployment",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        """Serialize for API responses (history rows and detail view)."""
        profile = self.profile
        return {
            "deployment_id": str(self.id),
            "status": self.status,
            "device_ids": [str(d.device.netbox_id) for d in self.devices]
            or self._device_ids_fallback(),
            "profile_id": str(self.profile_id) if self.profile_id else None,
            "profile_name": (profile.name if profile else None),
            "devices": [d.to_dict() for d in self.devices],
            "triggered_by": (self.operator.username if self.operator else None),
            "triggered_at": (self.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.started_at else None),
            "completed_at": (self.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.completed_at else None),
            "git_commit_sha": self.git_commit_sha,
            "pipeline_id": self.pipeline_id,
            "pipeline_url": self.pipeline_url,
            "message": self.error_message,
            "config_diff": self.config_diff,
        }

    def _device_ids_fallback(self):
        """Device ids from the deployment device rows (kept for clarity)."""
        return [str(d.device.netbox_id) for d in self.devices]

    def __repr__(self):
        """Human-readable representation with id and status."""
        return f"<DeploymentRecord {self.id} ({self.status})>"


class DeploymentDevice(TimestampMixin, db.Model):
    """Per-device outcome row within a multi-device deployment batch."""

    __tablename__ = "deployment_devices"
    __table_args__ = (
        db.UniqueConstraint("deployment_id", "device_id", name="uq_deployment_device"),
        db.Index("ix_deployment_device_device", "device_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    deployment_id = db.Column(
        db.Integer, db.ForeignKey("deployment_records.id"), nullable=False
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("devices.id"), nullable=False
    )
    device = db.relationship("Device", lazy=True)
    status = db.Column(db.String(16), nullable=False, default="success")
    message = db.Column(db.Text, nullable=True)
    config_diff = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        """Serialize a per-device result entry."""
        device = self.device
        return {
            "hostname": (device.hostname if device else None),
            "status": self.status,
            "message": self.message,
            "diff": self.config_diff,
        }

    def __repr__(self):
        """Human-readable representation with deployment and device ids."""
        return f"<DeploymentDevice {self.deployment_id}->{self.device_id} {self.status}>"


ZTP_STATUSES = ("pending", "generated", "delivered", "onboarded", "failed", "cancelled")

# legal transitions for ZTPProvision.status
ZTP_STATUS_TRANSITIONS = {
    "pending": {"generated", "cancelled"},
    "generated": {"delivered", "failed", "cancelled"},
    "delivered": {"onboarded", "failed", "cancelled"},
}


class ZTPProvision(TimestampMixin, db.Model):
    """Day-0 boot config and ZTP script for a device pending onboarding."""

    __tablename__ = "ztp_provisions"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(
        db.Integer, db.ForeignKey("devices.id"), unique=True, nullable=False, index=True
    )
    config_content = db.Column(db.Text, nullable=False)
    script_content = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(512), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    is_meraki = db.Column(db.Boolean, nullable=False, default=False)
    git_path = db.Column(db.String(512), nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    artifact_purged = db.Column(db.Boolean, nullable=False, default=False)

    def set_status(self, new_status):
        """Transition status, returning False on an illegal transition."""
        if new_status not in ZTP_STATUS_TRANSITIONS.get(self.status, set()):
            return False
        self.status = new_status
        return True

    def to_dict(self):
        """Serialize for the pending ZTP list and creation responses."""
        device = self.device
        return {
            "device_id": (str(device.netbox_id) if device else None),
            "hostname": (device.hostname if device else None),
            "serial": (device.serial_number if device else None),
            "ztp_url": f"{self.url}.txt" if self.url else None,
            "config_url": f"{self.url}.cfg" if self.url else None,
            "status": self.status,
            "is_meraki": self.is_meraki,
        }

    def __repr__(self):
        """Human-readable representation with device id and status."""
        return f"<ZTPProvision {self.device_id} ({self.status})>"
