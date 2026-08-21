"""Mixin providing UTC created_at / updated_at columns."""
from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    """Return the current UTC time as a naive datetime (stored as UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` UTC timestamps to a model."""

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
