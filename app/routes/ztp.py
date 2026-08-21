"""ZTP blueprint: public boot-script and day-0 config serving for new devices.

Devices fetch these over HTTP at first boot without authentication; access is
rate-limited to 10 requests/minute per client IP (a normal boot fetches 2-5
files). A successful fetch of the script/config transitions the provision to
`delivered`.
"""
import fcntl
import json
import logging
import os
import time

from flask import Blueprint, current_app, make_response, request

from ..services import settings as settings_service

logger = logging.getLogger(__name__)

bp = Blueprint("ztp", __name__)

RATE_LIMIT_PER_MINUTE = 10
_WINDOW_SECONDS = 60
RATE_STATE_NAME = "ztp_rate_limit.json"


class RateLedger:
    """Per-IP ZTP request ledger shared across all app workers.

    The ZTP contract's 10 req/min budget must hold process-wide, not
    per-process: with two gunicorn workers, a plain in-memory counter would
    double the effective limit. The ledger therefore persists hit timestamps
    as JSON in the app instance directory, guarded by an exclusive ``flock``
    so concurrent workers see one counter. If the instance directory
    cannot be used (read-only mounts, tests), it degrades to in-memory
    state so serving never breaks. ``clear()`` empties both layers.
    """

    def __init__(self):
        """Start with an empty in-memory ledger."""
        self._mem = {}

    def clear(self):
        """Empty the in-memory ledger and drop any persisted state file."""
        self._mem = {}
        state_path, _lock_path = self._state_path()
        if state_path:
            try:
                os.remove(state_path)
            except OSError:
                pass

    @staticmethod
    def _state_path():
        """Shared (state, lock) file paths, or (None, None) when no app context."""
        try:
            base = os.path.join(current_app.instance_path, RATE_STATE_NAME)
            return base, base + ".lock"
        except RuntimeError:
            return None, None

    def _prune(self, state, now):
        """Drop IP entries whose hits all fell out of the window."""
        return {ip: [t for t in hits if now - t < _WINDOW_SECONDS] for ip, hits in state.items()}

    def hit(self, ip):
        """Record one request from ``ip``; True when the budget is exceeded."""
        now = time.time()
        state_path, lock_path = self._state_path()
        if state_path is None:
            hits = [t for t in self._mem.get(ip, []) if now - t < _WINDOW_SECONDS]
            limited = len(hits) >= RATE_LIMIT_PER_MINUTE
            if not limited:
                hits.append(now)
            self._mem = self._prune(self._mem, now)
            self._mem[ip] = hits
            return limited
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(lock_path, "a+") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            try:
                try:
                    with open(state_path, "r", encoding="utf-8") as fh:
                        state = json.load(fh)
                    if not isinstance(state, dict):
                        state = {}
                except (OSError, ValueError):
                    state = {}
                state = self._prune(state, now)
                hits = [t for t in state.get(ip, []) if now - t < _WINDOW_SECONDS]
                limited = len(hits) >= RATE_LIMIT_PER_MINUTE
                if not limited:
                    hits.append(now)
                state[ip] = hits
                tmp_path = f"{state_path}.{os.getpid()}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    json.dump(state, fh)
                os.replace(tmp_path, state_path)
                return limited
            finally:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)


_rate_hits = RateLedger()


def _client_ip():
    """Best-effort client IP (X-Forwarded-For first entry, else remote addr)."""
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _rate_limited(ip):
    """Return True when the IP exceeds the shared per-minute budget."""
    return _rate_hits.hit(ip)


def _provision_for(serial):
    """The ZTP provision for a serial number, or None."""
    from ..models import Device

    device = Device.query.filter_by(serial_number=serial).first()
    if device is None:
        return None
    return device.ztp_provision


def _mark_delivered(provision):
    """Transit pending/generated -> delivered on artifact fetch (idempotent)."""
    from ..extensions import db

    if provision.status in ("pending", "generated") and provision.set_status("delivered"):
        db.session.commit()
        logger.info("ztp_delivered serial=%s", provision.device.serial_number)


def _serve(provision, body, content_type):
    """Build the artifact response and mark the provision delivered."""
    response = make_response(body, 200)
    response.headers["Content-Type"] = f"{content_type}; charset=utf-8"
    response.headers["Cache-Control"] = "no-store"
    if provision is not None:
        _mark_delivered(provision)
    return response


def _rate_limited_response():
    """429 response carrying the Retry-After window."""
    response = make_response("rate limit exceeded; retry later", 429)
    response.headers["Retry-After"] = str(_WINDOW_SECONDS)
    return response


def _not_found():
    """Plain-text 404 for unknown serials."""
    response = make_response("no ZTP provision for this serial number", 404)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    return response


@bp.get("/ztp/<serial>.txt")
def script(serial):
    """ZTP boot script: sources the day-0 config by serial."""
    if _rate_limited(_client_ip()):
        return _rate_limited_response()
    from ..services import ztp as ztp_service

    provision = _provision_for(serial)
    if provision is None:
        return _not_found()
    base = settings_service.get_setting(current_app, "ztp_base_url")
    return _serve(provision, ztp_service.ztp_script_for(serial, base), "text/plain")


@bp.get("/ztp/<serial>.cfg")
def day0_config(serial):
    """Day-0 configuration rendered from the assigned profile."""
    if _rate_limited(_client_ip()):
        return _rate_limited_response()
    provision = _provision_for(serial)
    if provision is None:
        return _not_found()
    return _serve(provision, provision.config_content, "text/plain")


@bp.get("/ztp/<serial>/startup-config.conf")
def startup_config(serial):
    """Full startup config fallback endpoint."""
    if _rate_limited(_client_ip()):
        return _rate_limited_response()
    provision = _provision_for(serial)
    if provision is None:
        return _not_found()
    return _serve(provision, provision.config_content, "text/plain")


@bp.get("/ztp/<serial>/image-list.txt")
def image_list(serial):
    """IOS image download list (empty unless an upgrade is scheduled)."""
    if _rate_limited(_client_ip()):
        return _rate_limited_response()
    provision = _provision_for(serial)
    if provision is None:
        return _not_found()
    return _serve(provision, "# no image upgrades scheduled\n", "text/plain")
