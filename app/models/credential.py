"""Service credential model: Fernet-encrypted backend tokens with env fallback."""
from app.extensions import db

from .mixins import TimestampMixin

SERVICE_NAMES = ("netbox", "gitlab", "grafana", "meraki")


class ServiceCredential(TimestampMixin, db.Model):
    """An encrypted API token for an external service.

    Resolution order at runtime: active DB credential first, then the `.env`
    variable named by ``env_key``, then an error marking the service
    unreachable.
    """

    __tablename__ = "service_credentials"
    __table_args__ = (
        db.Index("ix_service_credential_active", "is_active", "service_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    service_name = db.Column(db.String(64), unique=True, nullable=False, index=True)
    token_encrypted = db.Column(db.Text, nullable=False)
    base_url = db.Column(db.String(512), nullable=True)
    env_key = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    rotated_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self, token):
        """Serialize with a caller-supplied plain (already redacted) token."""
        return {
            "service_name": self.service_name,
            "token": token,
            "base_url": self.base_url,
            "env_key": self.env_key,
            "is_active": self.is_active,
            "rotated_at": self.rotated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.rotated_at else None,
        }

    def __repr__(self):
        """Human-readable representation naming the service."""
        return f"<ServiceCredential {self.service_name}>"
