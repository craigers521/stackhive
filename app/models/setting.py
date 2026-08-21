"""System setting key/value store backing GET/PUT /api/settings."""
from app.extensions import db

from .mixins import TimestampMixin


class SystemSetting(TimestampMixin, db.Model):
    """A persistent system setting; database values override env defaults."""

    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False)

    def __repr__(self):
        """Human-readable representation with the setting key."""
        return f"<SystemSetting {self.key}>"
