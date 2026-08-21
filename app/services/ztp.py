"""ZTP artifact generation (day-0 config + boot script) via the ZTP playbook.

Renders the constrained ZTP task set (bootstrap + netconf snippets) with the
same role/templates as deployments, using a minimal single-host workspace so
preview output is byte-identical to what the device will receive.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile

import yaml

logger = logging.getLogger(__name__)

ANSIBLE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ansible"))


class ZtpError(Exception):
    """ZTP generation failure."""


def ztp_script_for(serial, ztp_base_url):
    """Boot script served at /ztp/<serial>.txt."""
    base = (ztp_base_url or "").rstrip("/")
    return f"source {base}/ztp/{serial}.cfg\n"


def _ztp_workspace(device, profile, ztp_base_url):
    """Workspace for the ztp.yml playbook: group vars + minimal host vars."""
    from . import profiles as profiles_service

    ws = tempfile.mkdtemp(prefix="stackhive-ztp-")
    group_dir = os.path.join(ws, "group_vars", profile.name)
    host_dir = os.path.join(ws, "host_vars")
    template_dir = os.path.join(ws, "templates", profile.name)
    for path in (group_dir, host_dir, template_dir):
        os.makedirs(path, exist_ok=True)

    group_vars = {v.key: v for v in profile.variables}
    with open(os.path.join(group_dir, "vars.yml"), "w") as fh:
        fh.write(profiles_service._vars_file_content(group_vars))

    for template in profile.templates or []:
        if not template.is_enabled:
            continue
        with open(os.path.join(template_dir, f"{template.name}.j2"), "w") as fh:
            fh.write(template.content)

    host_vars = {
        "ansible_host": device.mgmt_ip.split("/")[0] if device.mgmt_ip else device.hostname,
        "ansible_connection": "local",
        "stackhive_profile": profile.name,
    }
    # Device-specific overrides win over profile vars in the ZTP render too.
    for row in device.overrides:
        if row.value_type == "string":
            host_vars[row.key] = row.value
        else:
            try:
                host_vars[row.key] = yaml.safe_load(row.value)
            except yaml.YAMLError:
                host_vars[row.key] = row.value
    with open(os.path.join(host_dir, f"{device.hostname}.yml"), "w") as fh:
        yaml.safe_dump(host_vars, fh, default_flow_style=False)

    inventory = {
        "all": {
            "children": {
                profile.name: {
                    "hosts": {
                        device.hostname: {
                            "ansible_host": host_vars["ansible_host"],
                            "ansible_connection": "local",
                            "stackhive_profile": profile.name,
                            "iosxe_profile_templates_dir": template_dir,
                        }
                    }
                }
            }
        }
    }
    with open(os.path.join(ws, "inventory.yml"), "w") as fh:
        yaml.safe_dump(inventory, fh, default_flow_style=False)
    return ws


def generate_ztp_app(app, device, profile):
    """Run the ZTP preview render; returns the day-0 configuration text."""
    ws = _ztp_workspace(device, profile, app.config.get("ZTP_BASE_URL", ""))
    env = dict(os.environ)
    env["ANSIBLE_CONFIG"] = os.path.join(ANSIBLE_DIR, "ansible.cfg")
    env.setdefault("ANSIBLE_LOCAL_TEMP", os.path.join(ws, ".ansible_tmp"))
    cmd = [
        app.config.get("ANSIBLE_PLAYBOOK_CMD", "ansible-playbook"),
        "ztp.yml",
        "-i", os.path.join(ws, "inventory.yml"),
        "--limit", device.hostname,
        "-e", "ztp_deploy_mode=preview",
        "-e", f"iosxe_output_dir={ws}/preview_out/{device.hostname}",
    ]
    timeout = int(app.config.get("ANSIBLE_PREVIEW_TIMEOUT", 30))
    try:
        proc = subprocess.run(
            cmd,
            cwd=ANSIBLE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ZtpError(f"ztp render timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise ZtpError("ansible-playbook executable not found") from exc
    artifact_path = os.path.join(ws, "preview_out", device.hostname, "ztp.json")
    try:
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-4000:]
            raise ZtpError(f"ztp render failed (rc={proc.returncode}): {tail}")
        with open(artifact_path) as fh:
            artifact = json.load(fh)
        return artifact.get("config", "")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


MERAKI_DEFAULT_DASHBOARD_URL = "https://n2c.meraki.com"


def resolve_meraki_inputs(app, dashboard_url=None):
    """Return (api_key, organization_id, dashboard_url) for a Meraki day-0 config.

    The Meraki API key and organization id are mandatory: without them the
    day-0 config cannot register the device with the Meraki dashboard.

    Args:
        app: Flask app (credential store + config access).
        dashboard_url: Optional dashboard URL override from the request.

    Returns:
        Tuple of (api_key, organization_id, dashboard_url).

    Raises:
        ZtpError: when the API key or organization id is not configured.
    """
    from . import credential

    try:
        token, _row = credential.resolve_token(app, "meraki", "MERAKI_API_KEY")
    except credential.CredentialError as exc:
        raise ZtpError(f"Meraki API key is not configured: {exc}") from exc
    organization_id = (
        app.config.get("MERAKI_ORGANIZATION_ID") or app.config.get("MERAKI_ORG_ID") or ""
    ).strip()
    if not organization_id:
        raise ZtpError("MERAKI_ORGANIZATION_ID is not configured; required for the Meraki day-0 config")
    return token, organization_id, (dashboard_url or "").strip() or MERAKI_DEFAULT_DASHBOARD_URL


def meraki_day0_block(api_key, organization_id, network_id, dashboard_url):
    """Meraki cloud onboarding commands appended to a Meraki day-0 config.

    Follows contracts/ztp-contract.md: after first boot the device registers
    with the Meraki dashboard via these mdt controller commands.
    """
    return f"""
!
! Meraki cloud onboarding
mdt controller name meraki
mdt controller meraki
  license key {api_key}
  organization id {organization_id}
  network id {network_id}
  dashboard url {dashboard_url}
! Ensure outbound HTTPS/DNS egress is available on the management interface.
!"""
