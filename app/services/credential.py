"""Fernet encryption and resolution of service credentials."""
import logging
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from ..extensions import db
from ..models import ServiceCredential

logger = logging.getLogger(__name__)
audit = logging.getLogger("app.audit")


class CredentialError(RuntimeError):
    """Credential operations failed (bad key, decrypt failure, missing source)."""


def get_fernet(app=None):
    """Build the Fernet instance from the ENCRYPTION_KEY env/config value."""
    key = None
    if app is not None:
        key = app.config.get("ENCRYPTION_KEY")
    if not key:
        key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise CredentialError("ENCRYPTION_KEY is not configured; cannot encrypt/decrypt tokens")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise CredentialError(f"ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def encrypt_token(app, token):
    """Encrypt a plaintext token with the configured Fernet key."""
    return get_fernet(app).encrypt(token.encode()).decode()


def decrypt_token(app, encrypted):
    """Decrypt a stored Fernet token; raises CredentialError on failure."""
    try:
        return get_fernet(app).decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise CredentialError(f"Failed to decrypt stored credential: {exc}") from exc


def redact(token):
    """Return a display-safe redaction of a token (``****`` plus last 4 chars)."""
    if not token:
        return ""
    if len(token) <= 4:
        return "****"
    return "****" + token[-4:]


def get_credential(app, service_name):
    """Return a ServiceCredential row for the service, or None."""
    return (
        ServiceCredential.query.filter_by(service_name=service_name, is_active=True)
        .order_by(ServiceCredential.updated_at.desc())
        .first()
    )


def resolve_token(app, service_name, env_key, default_value=None):
    """Resolve a service token: active DB credential, then env var, else error.

    ``default_value`` allows callers to pass an already-read env value (tests,
    config mapping); otherwise the process environment is consulted.
    """
    row = get_credential(app, service_name)
    if row is not None:
        try:
            return decrypt_token(app, row.token_encrypted), row
        except CredentialError:
            logger.error("credential_decrypt_failed service=%s", service_name)
    value = default_value if default_value is not None else os.environ.get(env_key)
    if value:
        return value, row
    raise CredentialError(
        f"No credential available for {service_name}; set an active DB "
        f"credential or the {env_key} environment variable"
    )


def get_base_url(app, service_name, default_url):
    """Resolve a service base URL: DB override, then the configured default."""
    row = get_credential(app, service_name)
    if row is not None and row.base_url:
        return row.base_url
    return default_url


def upsert_credential(app, service_name, token, base_url=None, rotated_by=None):
    """Create or rotate the active encrypted credential for a service.

    Emits a structured ``app.audit`` event; ``rotated_by`` carries the acting
    username when the caller has one (routes) — callers omitting it get an
    ``unspecified`` actor so the event still lands in the audit log.
    """
    if service_name not in ("netbox", "gitlab", "grafana", "meraki"):
        raise CredentialError(f"Unknown service name: {service_name}")
    row = ServiceCredential.query.filter_by(service_name=service_name).first()
    created = row is None
    encrypted = encrypt_token(app, token)
    if row is None:
        env_key = {
            "netbox": "NETBOX_TOKEN",
            "gitlab": "GITLAB_TOKEN",
            "grafana": "GRAFANA_TOKEN",
            "meraki": "MERAKI_API_KEY",
        }[service_name]
        row = ServiceCredential(
            service_name=service_name,
            token_encrypted=encrypted,
            base_url=base_url,
            env_key=env_key,
            is_active=True,
        )
    else:
        row.token_encrypted = encrypted
        if base_url is not None:
            row.base_url = base_url
        row.is_active = True
    row.rotated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.add(row)
    db.session.commit()
    audit.info(
        "credential_%s service=%s by=%s token=%s",
        "created" if created else "rotated",
        service_name,
        rotated_by or "unspecified",
        redact(token),
    )
    return row
