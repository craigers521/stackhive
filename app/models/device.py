"""Device model: NetBox-sourced inventory plus local operational state."""
from app.extensions import db

from .mixins import TimestampMixin

CONFIG_STATUSES = ("pending", "onboarded", "deployed", "modified", "failed")
MONITORING_STATUSES = ("up", "down", "unknown")


# legal transitions for Device.config_status
CONFIG_STATUS_TRANSITIONS = {
    "pending": {"onboarded", "deployed"},
    "onboarded": {"deployed"},
    "deployed": {"failed", "modified", "deployed"},
    "failed": {"deployed"},
    "modified": {"deployed"},
}


class Device(TimestampMixin, db.Model):
    """A managed network device.

    Immutable attributes (hostname, serial, role, site, type) are synced from
    NetBox; local columns track deployment and monitoring state.
    """

    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    netbox_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    hostname = db.Column(db.String(255), unique=True, nullable=False, index=True)
    serial_number = db.Column(db.String(64), nullable=False, default="")
    mac_address = db.Column(db.String(17), nullable=False, default="")
    mgmt_ip = db.Column(db.String(45), nullable=False, default="")
    role = db.Column(db.String(128), nullable=False, index=True)
    site = db.Column(db.String(255), nullable=False, index=True)
    device_type_id = db.Column(db.Integer, db.ForeignKey("device_types.id"), nullable=True)
    platform = db.Column(db.String(64), nullable=False, default="cisco_iosxe")
    config_status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    last_deployment_id = db.Column(
        db.Integer, db.ForeignKey("deployment_records.id"), nullable=True
    )
    monitoring_status = db.Column(db.String(16), nullable=False, default="unknown")
    last_check = db.Column(db.DateTime, nullable=True)
    cloud_managed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    stale = db.Column(db.Boolean, nullable=False, default=False)
    last_netbox_sync = db.Column(db.DateTime, nullable=True)
    grafana_dashboard_uid = db.Column(db.String(128), nullable=True)
    tags = db.Column(db.JSON, nullable=True, default=list)

    last_deployment = db.relationship("DeploymentRecord", foreign_keys=[last_deployment_id])
    ztp_provision = db.relationship(
        "ZTPProvision",
        backref="device",
        uselist=False,
        lazy=True,
        cascade="all, delete-orphan",
    )
    overrides = db.relationship(
        "ConfigurationVariable",
        foreign_keys="ConfigurationVariable.device_id",
        backref="device",
        lazy=True,
    )

    def set_config_status(self, new_status):
        """Transition config_status, returning False on an illegal transition."""
        if new_status not in CONFIG_STATUS_TRANSITIONS.get(self.config_status, set()):
            return False
        self.config_status = new_status
        return True

    def to_dict(self, with_tags=True):
        """Serialize for API list responses."""
        data = {
            "id": str(self.netbox_id),
            "hostname": self.hostname,
            "ip_address": self.mgmt_ip,
            "role": self.role,
            "device_type": (self.device_type.model if self.device_type else ""),
            "serial": self.serial_number,
            "status": self.monitoring_status,
            "site": self.site,
            "cloud_managed": self.cloud_managed,
            "stale": self.stale,
            "last_deployment": (
                self.last_deployment.started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if self.last_deployment
                else None
            ),
            "tags": self.tags or [],
        }
        return data

    def to_detail_dict(self, interfaces=None, overrides=None, deployment_history=None,
                       assigned_profiles=None):
        """Serialize the full device detail object for GET /api/devices/{id}."""
        base = self.to_dict()
        base.update(
            {
                "device_type": (self.device_type.to_dict() if self.device_type else None),
                "mac_address": self.mac_address,
                "config_status": self.config_status,
                "interfaces": interfaces or [],
                "assigned_profiles": assigned_profiles or [],
                "overrides": overrides or {},
                "deployment_history": deployment_history or [],
            }
        )
        return base

    def __repr__(self):
        """Human-readable representation with hostname and status."""
        return f"<Device {self.hostname}>"
