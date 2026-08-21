"""DeviceType model: physical model definitions and interface layout."""
from app.extensions import db

from .mixins import TimestampMixin


class DeviceType(TimestampMixin, db.Model):
    """A physical device model with interface counts and slot config.

    Sourced from NetBox via inventory sync, with local (admin-maintained)
    records as a fallback for models lacking NetBox interface data.
    """

    __tablename__ = "device_types"
    __table_args__ = (
        db.UniqueConstraint("manufacturer", "model", name="uq_device_type_manufacturer_model"),
    )

    id = db.Column(db.Integer, primary_key=True)
    netbox_id = db.Column(db.Integer, unique=True, nullable=True)
    manufacturer = db.Column(db.String(128), nullable=False)
    model = db.Column(db.String(255), nullable=False)
    part_number = db.Column(db.String(64), nullable=True)
    interface_count = db.Column(db.Integer, nullable=False, default=0)
    interface_types = db.Column(db.JSON, nullable=False, default=dict)
    slot_config = db.Column(db.JSON, nullable=True)
    uplink_slots = db.Column(db.Integer, nullable=False, default=0)
    management_interfaces = db.Column(db.JSON, nullable=True)

    devices = db.relationship("Device", backref="device_type", lazy=True)

    def to_dict(self):
        """Serialize for API responses."""
        return {
            "id": str(self.netbox_id if self.netbox_id else self.id),
            "manufacturer": self.manufacturer,
            "model": self.model,
            "part_number": self.part_number,
            "interface_count": self.interface_count,
            "interface_types": self.interface_types or {},
            "uplink_slots": self.uplink_slots,
            "management_interfaces": self.management_interfaces or [],
            "source": "netbox" if self.netbox_id else "local",
        }

    def __repr__(self):
        """Human-readable representation with model and count."""
        return f"<DeviceType {self.manufacturer}/{self.model}>"
