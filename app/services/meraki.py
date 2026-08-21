"""Meraki cloud dashboard API client (ZTP onboarding)."""
import logging
import time

import requests

logger = logging.getLogger(__name__)


class MerakiError(Exception):
    """Meraki API failure with the upstream status when known."""

    def __init__(self, message, status=None):
        """Keep the error message plus the upstream status when known."""
        super().__init__(message)
        self.status = status


class MerakiUnavailable(MerakiError):
    """Meraki API unreachable or not configured."""


class MerakiClient:
    """Thin client for the Meraki Dashboard API device endpoints."""

    def __init__(self, base_url, token, organization_id, timeout=10, retries=2):
        """Store endpoint, token, organization, timeout, retries; prepare the session."""
        self.base_url = (base_url or "https://api.meraki.com/api/v1").rstrip("/")
        self.token = token
        self.organization_id = organization_id
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"X-Cisco-Meraki-API-Key": token, "Content-Type": "application/json"})

    def _request(self, method, path, json_body=None):
        """One API call with retry/backoff and standardized error mapping."""
        url = f"{self.base_url}{path}"
        last = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(method, url, json=json_body, timeout=self.timeout)
                if resp.status_code in (401, 403):
                    raise MerakiUnavailable(f"Meraki authentication failed ({resp.status_code})")
                if 500 <= resp.status_code < 600:
                    last = MerakiUnavailable(f"Meraki server error {resp.status_code}")
                    if attempt < self.retries:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    raise last
                if resp.status_code == 404:
                    raise MerakiError(f"not found in Meraki: {path}", status=404)
                resp.raise_for_status()
                return resp.json() if resp.text else None
            except requests.RequestException as exc:
                if isinstance(exc, MerakiError):
                    if isinstance(exc, (MerakiUnavailable,)):
                        raise
                    last = exc
                else:
                    last = MerakiUnavailable(f"Meraki unavailable: {exc}")
                if attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise last
        raise last

    # -- device onboarding --------------------------------------------------

    def list_networks(self):
        """List the networks in the organization."""
        return self._request("GET", f"/organizations/{self.organization_id}/networks") or []

    def get_network(self, network_id):
        """Fetch one network by id."""
        return self._request("GET", f"/organizations/{self.organization_id}/networks/{network_id}")

    def find_device(self, serial):
        """Locate a device by serial; None when not yet present."""
        devices = self._request("GET", f"/organizations/{self.organization_id}/devices", ) or []
        page = devices if isinstance(devices, list) else devices.get("data", [])
        for device in page:
            if device.get("serialNumber") == serial:
                return device
        return None

    def add_device(self, serial, name=None, notes=None):
        """Reserve a new device in the organization (auto-onboarding)."""
        body = {"serial": serial}
        if name:
            body["name"] = name
        if notes:
            body["notes"] = notes
        return self._request("POST", f"/organizations/{self.organization_id}/devices", json_body=body)

    def update_device(self, serial, name=None, notes=None):
        """Update the name and/or notes of a reserved device."""
        body = {}
        if name:
            body["name"] = name
        if notes:
            body["notes"] = notes
        return self._request("PUT", f"/organizations/{self.organization_id}/devices/{serial}", json_body=body)


def make_meraki_client(app):
    """Build a configured MerakiClient from settings/credentials; raises when unconfigured."""
    from . import credential, settings

    base_url = settings.get_setting(app, "meraki_api_base")
    token, _row = credential.resolve_token(app, "meraki", "MERAKI_API_KEY")
    organization_id = app.config.get("MERAKI_ORGANIZATION_ID", "") or ""
    if not organization_id:
        raise MerakiUnavailable("MERAKI_ORGANIZATION_ID is not configured")
    return MerakiClient(base_url, token, organization_id, timeout=app.config["HTTP_TIMEOUT"])
