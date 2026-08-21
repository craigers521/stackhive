"""Ansible subprocess runner: config preview rendering (T037).

The Flask app renders a configuration preview by invoking the *same*
playbook/role/vars the GitLab runner uses for live deployment:

    ansible-playbook preview.yml -i <workspace>/inventory.yml --limit <host>

A per-render workspace mirrors the Git repo layout so Ansible's own
group_vars/host_vars precedence applies (host overrides group):

    ws/
      inventory.yml
      group_vars/<profile>/vars.yml     (profile variables)
      host_vars/<hostname>.yml          (device overrides)
      templates/<profile>/<snippet>.j2  (profile template overrides)

Artifacts are read back from preview_out/<host>/preview.json.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

ANSIBLE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ansible"))


class AnsibleError(Exception):
    """Base error for ansible invocation failures."""


class AnsiblePreviewError(AnsibleError):
    """Preview render failed (non-zero exit or missing artifact)."""

    def __init__(self, message, returncode=None, output=None):
        """Keep the error message plus the process returncode and output."""
        super().__init__(message)
        self.returncode = returncode
        self.output = output or ""


@dataclass
class PreviewResult:
    """A rendered preview: config text, snippet list, variables used."""
    hostname: str
    profile_name: str
    config: str
    snippets: list = field(default_factory=list)
    variables_used: dict = field(default_factory=dict)


def _range_indices(interface_range, total):
    """Expand an interface range expression (all / 1-48 / 1,3,5) to 1-based indices."""
    text = str(interface_range or "all").strip()
    if text == "all":
        return list(range(1, total + 1))
    indices = set()
    for part in (p.strip() for p in text.split(",") if p.strip()):
        if "-" in part:
            start, _, end = part.partition("-")
            indices.update(range(int(start), int(end) + 1))
        else:
            indices.add(int(part))
    return sorted(i for i in indices if 1 <= i <= total)


def interface_names_for(device, interface_type, interface_range):
    """Concrete interface names for type+range from the DeviceType layout."""
    dt = device.device_type
    if dt is None:
        return []
    types = dt.interface_types or {}
    if interface_type not in types:
        return []
    total = int(types[interface_type])
    return [f"{interface_type}{i}" for i in _range_indices(interface_range, total)]


def render_interface_blocks(app, device, profile, variables):
    """Render the profile's interface template rows per matched interface.

    Returns a list of pre-rendered config blocks (text) appended to the
    assembled candidate (iosxe_extra_config_blocks).
    """
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined, keep_trailing_newline=False)
    blocks = []
    for it in sorted(profile.interface_templates or [], key=lambda r: r.display_order):
        if not it.is_enabled or not it.content:
            continue
        names = interface_names_for(device, it.interface_type, it.interface_range)
        if not names:
            continue
        template = env.from_string(it.content)
        for name in names:
            try:
                rendered = template.render(**variables, interface_name=name)
            except Exception as exc:  # render error -> 422 upstream
                raise AnsiblePreviewError(f"interface template '{it.name}' failed for {name}: {exc}")
            rendered = rendered.strip()
            if rendered:
                blocks.append(rendered)
    return blocks


def _workspace(app, device, profile, interface_blocks):
    """Materialize the render workspace mirroring the Git repo layout."""
    from . import profiles as profiles_service

    ws = tempfile.mkdtemp(prefix="stackhive-render-")
    group_dir = os.path.join(ws, "group_vars", profile.name)
    host_dir = os.path.join(ws, "host_vars")
    template_dir = os.path.join(ws, "templates", profile.name)
    for path in (group_dir, host_dir, template_dir):
        os.makedirs(path, exist_ok=True)

    group_vars = {v.key: v for v in profile.variables}
    with open(os.path.join(group_dir, "vars.yml"), "w") as fh:
        fh.write(profiles_service._vars_file_content(group_vars))

    with open(os.path.join(host_dir, f"{device.hostname}.yml"), "w") as fh:
        fh.write(profiles_service._host_vars_content(device))

    for template in profile.templates or []:
        if not template.is_enabled:
            continue
        with open(os.path.join(template_dir, f"{template.name}.j2"), "w") as fh:
            fh.write(template.content)

    inventory = {
        "all": {
            "children": {
                profile.name: {
                    "hosts": {
                        device.hostname: {
                            "ansible_host": device.mgmt_ip.split("/")[0] if device.mgmt_ip else device.hostname,
                            "ansible_connection": "local",
                            "stackhive_profile": profile.name,
                            "iosxe_profile_templates_dir": template_dir,
                            "iosxe_extra_config_blocks": interface_blocks,
                        }
                    }
                }
            }
        }
    }
    with open(os.path.join(ws, "inventory.yml"), "w") as fh:
        yaml.safe_dump(inventory, fh, default_flow_style=False)
    return ws


def _build_command(app, inventory, hostname, workspace):
    """Build the ansible-playbook argv for a preview render in the workspace."""
    cmd = [app.config.get("ANSIBLE_PLAYBOOK_CMD", "ansible-playbook")]
    cmd += ["preview.yml", "-i", inventory, "--limit", hostname]
    cmd += ["-e", f"iosxe_output_dir={workspace}/preview_out/{hostname}"]
    if app.config.get("ANSIBLE_VERBOSE"):
        cmd.append("-v")
    return cmd


def annotate_snippets(profile, snippets):
    """Shape snippet entries for the preview UI with source-template labels.

    Accepts both snippet shapes: artifact dicts (``{"name", "content"}``)
    from the iosxe role and plain name strings. The source label reflects
    resolution order: a profile template override wins when the profile has
    an enabled template with the same name; otherwise the iosxe role
    default template applies.
    """
    profile_names = set()
    if profile is not None:
        profile_names = {t.name for t in (profile.templates or []) if t.is_enabled}
    annotated = []
    for snippet in snippets or []:
        if isinstance(snippet, dict):
            name = snippet.get("name", "")
            content = snippet.get("content", "")
        else:
            name, content = str(snippet), ""
        if name in profile_names:
            source = f"profile override: {name}.j2"
        else:
            source = f"role default: {name}.j2"
        annotated.append({"name": name, "content": content, "source": source})
    return annotated


def render_preview(app, device, profile) -> PreviewResult:
    """Render the full candidate configuration for one device via ansible."""
    from . import profiles as profiles_service

    variables = profiles_service.effective_variables(app, device, profile)
    interface_blocks = render_interface_blocks(app, device, profile, variables)
    ws = _workspace(app, device, profile, interface_blocks)
    cmd = _build_command(app, os.path.join(ws, "inventory.yml"), device.hostname, ws)
    env = dict(os.environ)
    env["ANSIBLE_CONFIG"] = os.path.join(ANSIBLE_DIR, "ansible.cfg")
    env.setdefault("ANSIBLE_LOCAL_TEMP", os.path.join(ws, ".ansible_tmp"))
    timeout = int(app.config.get("ANSIBLE_PREVIEW_TIMEOUT", 30))
    logger.info("preview_render cmd=%s cwd=%s", " ".join(cmd), ANSIBLE_DIR)
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
        raise AnsiblePreviewError(f"ansible preview timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise AnsiblePreviewError("ansible-playbook executable not found") from exc

    artifact_path = os.path.join(ws, "preview_out", device.hostname, "preview.json")
    try:
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-4000:]
            raise AnsiblePreviewError(
                f"ansible preview failed (rc={proc.returncode})",
                returncode=proc.returncode,
                output=tail,
            )
        with open(artifact_path) as fh:
            artifact = json.load(fh)
        return PreviewResult(
            hostname=device.hostname,
            profile_name=profile.name,
            config=artifact.get("config", ""),
            snippets=artifact.get("snippets", []),
            variables_used=variables,
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)
