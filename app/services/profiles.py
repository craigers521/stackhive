"""Profile business logic: CRUD, validation, Git-backed YAML persistence."""
import logging

import yaml
from jinja2 import Environment, TemplateSyntaxError

from ..extensions import db
from ..models import (
    ConfigurationProfile,
    ConfigurationTemplate,
    ConfigurationVariable,
    Device,
    DeviceType,
    InterfaceTemplate,
    validate_profile_name,
    validate_variable_key,
)

logger = logging.getLogger(__name__)

MAX_TEMPLATES = 20
MAX_TEMPLATE_BYTES = 51_200
_jinja_env = Environment()


class ProfilesError(Exception):
    """Validation failure (surfaced as 400)."""


class ProfilesConflict(ProfilesError):
    """Conflict condition (surfaced as 409)."""


def git_enabled(app):
    """Whether commits must succeed against GitLab (vs local-only fallback)."""
    return bool(app.config.get("GIT_STRICT", True))


def _git_client(app):
    """GitLab client for this app, or None when unconfigured (GIT_STRICT raises)."""
    from . import gitlab

    try:
        return gitlab.make_client(app)
    except Exception as exc:
        if git_enabled(app):
            raise
        logger.warning("gitlab_unconfigured using_local_only_fallback reason=%s", exc)
        return None


def _working_branch(app):
    """The Git working branch taken from settings."""
    from . import settings

    return settings.get_setting(app, "git_working_branch") or "working"


def validate_jinja(content, name="template"):
    """Raise ProfilesError when content is not valid Jinja2 syntax."""
    if not isinstance(content, str):
        raise ProfilesError(f"{name} content must be a string")
    if len(content.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise ProfilesError(f"{name} exceeds {MAX_TEMPLATE_BYTES} bytes")
    try:
        _jinja_env.parse(content)
    except TemplateSyntaxError as exc:
        raise ProfilesError(f"Invalid Jinja2 in {name}: line {exc.lineno}: {exc.message}") from exc


def _validate_templates(rows):
    """Validate the template row list (shape, names, syntax, counts)."""
    if not isinstance(rows, list):
        raise ProfilesError("templates must be an array")
    if len(rows) > MAX_TEMPLATES:
        raise ProfilesError(f"at most {MAX_TEMPLATES} templates per profile")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("name") or "content" not in row:
            raise ProfilesError("each template requires name and content")
        name = str(row["name"])
        if name in seen:
            raise ProfilesError(f"duplicate template name: {name}")
        seen.add(name)
        validate_jinja(row["content"], name=name)


def _validate_variable_rows(rows):
    """Validate the variable mapping (keys, types)."""
    if not isinstance(rows, dict):
        raise ProfilesError("variables must be an object of key/value pairs")
    for key in rows:
        if not validate_variable_key(str(key)):
            raise ProfilesError(f"invalid variable key: {key}")


def validate_interface_mapping(interface_type, interface_range, profile_device_role):
    """Validate an interface template against known device types for the role.

    Supports the ``all`` keyword and ``1-48`` / ``1,3,5`` ranges bounded by
    the type's interface count. Raises a clear ProfilesError when the role has
    no known interface definition (spec edge case).
    """
    if not interface_type or not interface_range:
        raise ProfilesError("interface_type and interface_range are required")
    counts = _role_interface_counts(profile_device_role)
    if not counts:
        raise ProfilesError(
            f"No known interface definitions for device role '{profile_device_role}'; "
            "sync inventory and maintain a DeviceType before defining interface templates"
        )
    if interface_type not in counts:
        raise ProfilesError(
            f"Interface type '{interface_type}' not found for role '{profile_device_role}'; "
            f"available: {', '.join(sorted(counts))}"
        )
    total = counts[interface_type]
    _validate_range(interface_range, total)


def _role_interface_counts(device_role):
    """Union of interface_types counts across device types used by this role."""
    type_ids = (
        Device.query.filter_by(role=device_role)
        .with_entities(Device.device_type_id)
        .distinct()
        .all()
    )
    counts = {}
    for (type_id,) in type_ids:
        if type_id is None:
            continue
        dt = db.session.get(DeviceType, type_id)
        if dt is None or not dt.interface_types:
            continue
        for iface_type, count in dt.interface_types.items():
            counts[iface_type] = counts.get(iface_type, 0) + count
    merged = {}
    for dt in DeviceType.query.all():
        if dt.interface_types:
            for iface_type, count in dt.interface_types.items():
                merged[iface_type] = max(merged.get(iface_type, 0), count)
    if counts:
        return counts
    return merged


def role_interface_counts(device_role=None):
    """Interface types and counts a role's device types define (FR-006).

    Returns an ``{interface_type: count}`` mapping. With a ``device_role`` it
    reflects the types assigned to devices of that role (falling back to every
    known DeviceType when the role has no typed devices yet); with ``None`` it
    returns the union across all known device types. Mirrors the counts used by
    ``validate_interface_mapping`` so the form presents exactly what the
    server-side validation enforces.

    Example:
        role_interface_counts("switch-leaf")
        -> {"GigabitEthernet": 48, "TenGigabitEthernet": 4}
    """
    if device_role:
        return _role_interface_counts(device_role)
    merged = {}
    for dt in DeviceType.query.all():
        if dt.interface_types:
            for iface_type, count in dt.interface_types.items():
                merged[iface_type] = max(merged.get(iface_type, 0), count)
    return merged


def _validate_range(interface_range, total):
    """Validate a range expression like all / 1-48 / 1,3,5 against the count."""
    text = str(interface_range).strip()
    if text == "all":
        return
    parts = [p.strip() for p in text.split(",") if p.strip()]
    indices = set()
    for part in parts:
        if "-" in part:
            start, _, end = part.partition("-")
            if not (start.isdigit() and end.isdigit()):
                raise ProfilesError(f"invalid range segment: {part}")
            start, end = int(start), int(end)
            if start > end:
                raise ProfilesError(f"invalid range segment: {part}")
            indices.update(range(start, end + 1))
        elif part.isdigit():
            indices.add(int(part))
        else:
            raise ProfilesError(f"invalid range segment: {part}")
    max_index = max(indices)
    if max_index > total:
        raise ProfilesError(
            f"Interface range '{interface_range}' exceeds the {total} available interfaces"
        )


def _vars_file_content(variables):
    """Render a vars mapping (key -> value row) as YAML file content."""
    if not variables:
        return "{}\n"
    out = []
    for key in sorted(variables):
        row = variables[key]
        out.append(f"{key}: {yaml_string(row)}")
    return "".join(f"{line}\n" for line in out)


def dump_yaml(value, flow=False):
    """safe_dump without the trailing '...' document-end marker (PyYAML >= 6)."""
    text = yaml.safe_dump(value, default_flow_style=flow).strip()
    if text == "..." or text.endswith("\n..."):
        text = text.replace("\n...", "").rstrip()
    return text


def yaml_string(row):
    """Format a single variable value inline with its declared YAML type."""
    raw = row.value
    if row.value_type == "int":
        return str(int(raw))
    if row.value_type == "bool":
        return "true" if _truthy(raw) else "false"
    if row.value_type in ("list", "dict"):
        parsed = yaml.safe_load(raw)
        return dump_yaml(parsed, flow=True)
    safe = str(raw).strip()
    if safe in ("", "true", "false", "null") or safe.replace(".", "", 1).replace("-", "", 1).isdigit():
        return f'"{safe}"'
    return safe


def _truthy(raw):
    """YAML-ish truthy check on a raw value string."""
    return str(raw).strip().lower() in ("true", "yes", "1", "on")


def _infer_value_type(value):
    """Infer a value_type from a raw API JSON value."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, (list, dict)):
        return "dict" if isinstance(value, dict) else "list"
    return "string"


def _rows_from_variables(variables):
    """Turn an API {key: value} map into validated row dicts."""
    _validate_variable_rows(variables)
    rows = []
    for key, value in variables.items():
        value_type = _infer_value_type(value)
        stored = value if isinstance(value, str) else dump_yaml(value)
        rows.append({"key": str(key), "value": stored, "value_type": value_type})
    return rows


def create_profile(app, user, data):
    """Create a profile with initial templates/variables; persist and commit to Git."""
    name = str(data.get("name") or "").strip()
    device_role = str(data.get("device_role") or "").strip()
    if not validate_profile_name(name):
        raise ProfilesError("name must be 1-128 chars of alphanumerics, hyphens or underscores")
    if not device_role:
        raise ProfilesError("device_role is required")
    if ConfigurationProfile.query.filter_by(name=name).first():
        raise ProfilesConflict(f"profile name already exists: {name}")
    active = (
        ConfigurationProfile.query.filter_by(device_role=device_role, is_active=True).first()
    )
    if active is not None:
        raise ProfilesConflict(
            f"an active profile already targets role '{device_role}': {active.name}"
        )
    templates = data.get("templates") or []
    variables = data.get("variables") or {}
    mappings = data.get("interface_mappings") or []
    _validate_templates(templates)
    _validate_variable_rows(variables)
    for row in mappings:
        if isinstance(row, dict) and row.get("interface_type") and row.get("interface_range"):
            validate_interface_mapping(str(row["interface_type"]), str(row["interface_range"]), device_role)

    profile = ConfigurationProfile(
        name=name,
        device_role=device_role,
        description=data.get("description"),
        git_path=f"group_vars/{name}/",
        created_by_id=user.id,
        updated_by_id=user.id,
    )
    db.session.add(profile)
    db.session.flush()
    _apply_templates(profile, templates)
    _apply_variables(profile, variables)
    _apply_interface_templates(profile, mappings)
    files = _profile_git_files(profile)
    _commit_profile_files(app, profile, f"profile: create {name}", files)
    profile.updated_by_id = user.id
    db.session.commit()
    return profile


def update_profile(app, user, profile_id, data):
    """Full-replacement update with optimistic locking on profile.version."""
    profile = db.session.get(ConfigurationProfile, profile_id)
    if profile is None:
        raise LookupError("profile not found")
    provided_version = data.get("version")
    if provided_version is not None and provided_version != profile.version:
        raise ProfilesConflict("profile was modified concurrently; reload and retry")

    name = str(data.get("name") or profile.name).strip()
    device_role = str(data.get("device_role") or profile.device_role).strip()
    if name != profile.name:
        if not validate_profile_name(name):
            raise ProfilesError("invalid profile name")
        if ConfigurationProfile.query.filter(ConfigurationProfile.name == name, ConfigurationProfile.id != profile.id).first():
            raise ProfilesConflict(f"profile name already exists: {name}")
    if device_role != profile.device_role:
        clash = (
            ConfigurationProfile.query.filter(
                ConfigurationProfile.device_role == device_role,
                ConfigurationProfile.is_active == True,  # noqa: E712
                ConfigurationProfile.id != profile.id,
            ).first()
        )
        if clash is not None:
            raise ProfilesConflict(
                f"an active profile already targets role '{device_role}': {clash.name}"
            )
        profile.device_role = device_role

    if "templates" in data:
        _validate_templates(data["templates"])
        _apply_templates(profile, data["templates"])
    if "variables" in data:
        _validate_variable_rows(data["variables"])
        _apply_variables(profile, data["variables"])
    if "interface_mappings" in data:
        _apply_interface_templates(profile, data["interface_mappings"])

    files = _profile_git_files(profile)
    deleted = data.get("_deleted_git_paths") or []
    for path in deleted:
        files.setdefault(path, None)
    _commit_profile_files(app, profile, f"profile: update {profile.name} — edited via dashboard", files)

    if name != profile.name:
        profile.name = name
        profile.git_path = f"group_vars/{name}/"
        for t in profile.templates:
            t.git_path = f"templates/{name}/{t.name}.j2"
        for it in profile.interface_templates:
            it.git_path = f"templates/{name}/interfaces/{it.name}.j2"
    profile.updated_by_id = user.id
    db.session.commit()
    return profile


def _commit_profile_files(app, profile, message, files):
    """Persist files in Git; update profile version to the commit SHA."""
    client = _git_client(app)
    if client is None:
        return
    try:
        sha = client.push_with_rebase(_working_branch(app), message, files)
    except Exception as exc:
        db.session.rollback()
        from . import gitlab

        if isinstance(exc, gitlab.GitLabConflict) or "rebase conflict" in str(exc):
            raise ProfilesConflict(str(exc)) from exc
        raise
    profile.version = sha or profile.version


def delete_profile(app, profile_id):
    """Delete a profile, its children and its Git files."""
    profile = db.session.get(ConfigurationProfile, profile_id)
    if profile is None:
        raise LookupError("profile not found")
    files = {path: None for path in _profile_git_paths(profile)}
    db.session.delete(profile)
    db.session.flush()
    client = _git_client(app)
    if client is not None and files:
        try:
            client.push_with_rebase(
                _working_branch(app), f"profile: delete {profile.name}", files
            )
        except Exception:
            db.session.rollback()
            raise
    db.session.commit()


def _apply_templates(profile, rows):
    """Replace the profile's template rows."""
    for template in list(profile.templates):
        db.session.delete(template)
    db.session.flush()
    for i, row in enumerate(rows):
        db.session.add(
            ConfigurationTemplate(
                profile_id=profile.id,
                name=str(row["name"]),
                display_order=int(row.get("order", i)),
                content=row["content"],
                git_path=f"templates/{profile.name}/{row['name']}.j2",
                config_section=row.get("config_section"),
                is_enabled=bool(row.get("is_enabled", True)),
            )
        )


def _apply_interface_templates(profile, rows):
    """Replace interface template rows with full validation (FR-006)."""
    if not isinstance(rows, list):
        raise ProfilesError("interface_mappings must be an array")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("name"):
            raise ProfilesError("each interface mapping requires a name")
        it_type = str(row.get("interface_type") or "")
        it_range = str(row.get("interface_range") or "")
        if not it_type:
            raise ProfilesError("interface_type is required on interface mappings")
        validate_interface_mapping(it_type, it_range or "all", profile.device_role)
        if row["name"] in seen:
            raise ProfilesError(f"duplicate interface template name: {row['name']}")
        seen.add(row["name"])
    for row in list(profile.interface_templates):
        db.session.delete(row)
    db.session.flush()
    for i, row in enumerate(rows):
        db.session.add(
            InterfaceTemplate(
                profile_id=profile.id,
                name=str(row["name"]),
                interface_type=str(row["interface_type"]),
                interface_range=str(row.get("interface_range") or "all"),
                content=str(row.get("content") or ""),
                git_path=f"templates/{profile.name}/interfaces/{row['name']}.j2",
                display_order=int(row.get("order", i)),
                is_enabled=bool(row.get("is_enabled", True)),
            )
        )
        validate_jinja(row.get("content") or "", name=str(row["name"]))


def _apply_variables(profile, variables):
    """Replace the profile's group_vars variables with the mapped set."""
    for row in list(profile.variables):
        db.session.delete(row)
    db.session.flush()
    for row in _rows_from_variables(variables):
        db.session.add(
            ConfigurationVariable(
                scope="profile",
                profile_id=profile.id,
                key=row["key"],
                value=row["value"],
                value_type=row["value_type"],
                git_path=f"group_vars/{profile.name}/vars.yml",
            )
        )


def _profile_git_paths(profile):
    """All Git paths belonging to a profile (vars, templates, interfaces)."""
    paths = [f"group_vars/{profile.name}/vars.yml"]
    for t in profile.templates:
        paths.append(f"templates/{profile.name}/{t.name}.j2")
    for it in profile.interface_templates:
        paths.append(f"templates/{profile.name}/interfaces/{it.name}.j2")
    return paths


def _profile_git_files(profile):
    """Build the full Git file map for a profile (None values delete)."""
    variables = {v.key: v for v in profile.variables}
    files = {f"group_vars/{profile.name}/vars.yml": _vars_file_content(variables)}
    for t in profile.templates:
        files[f"templates/{profile.name}/{t.name}.j2"] = t.content
    for it in profile.interface_templates:
        files[f"templates/{profile.name}/interfaces/{it.name}.j2"] = it.content
    return files


# ---------------------------------------------------------------------------
# Device overrides (host_vars) — FR-004b
# ---------------------------------------------------------------------------


def device_overrides(device):
    """Return {key: value-string} plus the last update time for a device."""
    rows = device.overrides
    if not rows:
        return {}, None
    updated = max(r.updated_at for r in rows)
    return {r.key: r.value for r in rows}, updated


def _override_rows_from_map(device, variables):
    """Build device-scope variable rows from an API {key: value} map."""
    _validate_variable_rows(variables)
    rows = []
    for key, value in variables.items():
        value_type = _infer_value_type(value)
        if value_type in ("list", "dict"):
            stored = dump_yaml(value)
        elif value_type == "bool":
            stored = "true" if value else "false"
        else:
            stored = str(value)
        rows.append(
            ConfigurationVariable(
                scope="device",
                device_id=device.id,
                key=str(key),
                value=stored,
                value_type=value_type,
                git_path=f"host_vars/{device.hostname}.yml",
            )
        )
    return rows


def _host_vars_content(device):
    """Render host_vars/<hostname>.yml from the device's override rows."""
    data = {r.key: r.value for r in device.overrides}
    if not data:
        return "{}\n"
    out = []
    parsed = {}
    for key, value in data.items():
        row = next(r for r in device.overrides if r.key == key)
        try:
            parsed[key] = _typed_value(row)
        except ProfilesError:
            parsed[key] = value
    dumped = yaml.safe_dump(parsed, default_flow_style=False, sort_keys=True)
    return dumped


def _typed_value(row):
    """Convert a stored variable into its typed Python value for YAML dump."""
    if row.value_type == "int":
        return int(row.value)
    if row.value_type == "bool":
        return _truthy(row.value)
    if row.value_type in ("list", "dict"):
        return yaml.safe_load(row.value)
    return row.value


def save_device_overrides(app, device, variables, message=None):
    """Replace device override rows and commit host_vars YAML to Git."""
    existing = list(device.overrides)
    for row in existing:
        db.session.delete(row)
    db.session.flush()
    device.overrides = _override_rows_from_map(device, variables)
    db.session.flush()
    host_file = f"host_vars/{device.hostname}.yml"
    files = {host_file: _host_vars_content(device)}
    client = _git_client(app)
    if client is not None:
        try:
            client.push_with_rebase(
                _working_branch(app),
                message or f"override: update {device.hostname}",
                files,
            )
        except Exception as exc:
            db.session.rollback()
            from . import gitlab

            if isinstance(exc, gitlab.GitLabConflict) or "rebase conflict" in str(exc):
                raise ProfilesConflict(str(exc)) from exc
            raise
    db.session.commit()
    return device


def effective_variables(app, device, profile):
    """Merged effective variable map: profile variables + device overrides."""
    merged = {}
    if profile is not None:
        for v in profile.variables:
            merged[v.key] = v.value if _infer_value_type_from_row(v) == "string" else _safe_parse(v)
    for row in device.overrides:
        if row.value_type == "string":
            merged[row.key] = row.value
        else:
            merged[row.key] = _safe_parse(row)
    return merged


def _infer_value_type_from_row(row):
    """The declared value type of a profile variable row."""
    return row.value_type


def _safe_parse(row):
    """Parse a variable row value into int, bool, list, or dict where possible."""
    try:
        if row.value_type == "int":
            return int(row.value)
        if row.value_type == "bool":
            return _truthy(row.value)
        if row.value_type in ("list", "dict"):
            return yaml.safe_load(row.value)
    except (ArithmeticError, ValueError, yaml.YAMLError):
        pass
    return row.value
