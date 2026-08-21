"""Profiles blueprint: HTML pages and REST API for configuration profiles
and per-device variable overrides (host_vars)."""
import logging

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from .. import AppError
from ..decorators import editor_required, viewer_required
from ..models import ConfigurationProfile, Device, DeviceType
from ..services import drift
from ..services import profiles as profiles_service

logger = logging.getLogger(__name__)

bp = Blueprint("profiles", __name__)

TEMPLATE_SLOTS = 5
VARIABLE_SLOTS = 10
INTERFACE_SLOTS = 3


def _iso(value):
    """Format a datetime as ISO-8601 UTC, or None."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _username(profile, attr):
    """Best-effort username for an operator id."""
    user = getattr(profile, attr)
    return user.username if user is not None else None


def _template_obj(t):
    """Serialize a template row for API responses."""
    return {
        "id": str(t.id),
        "name": t.name,
        "content": t.content,
        "order": t.display_order,
        "config_section": t.config_section,
        "is_enabled": t.is_enabled,
    }


def _mapping_obj(it):
    """Serialize an interface-template row for API responses."""
    return {
        "template_id": str(it.id),
        "name": it.name,
        "interface_type": it.interface_type,
        "interface_range": it.interface_range,
        "interface_names": _expand_range(it.interface_type, it.interface_range),
        "content": it.content,
        "order": it.display_order,
        "is_enabled": it.is_enabled,
    }


def _expand_range(interface_type, interface_range):
    """Expand a range expression (all / 1-48 / 1,3,5) into interface names."""
    text = str(interface_range or "all").strip()
    if text == "all":
        return []
    indices = []
    for part in (p.strip() for p in text.split(",") if p.strip()):
        if "-" in part:
            start, _, end = part.partition("-")
            indices.extend(range(int(start), int(end) + 1))
        else:
            indices.append(int(part))
    return [f"{interface_type}{n}" for n in sorted(set(indices))]


def _profile_list_item(p):
    """Serialize a profile for list endpoints."""
    return {
        "id": str(p.id),
        "name": p.name,
        "device_role": p.device_role,
        "is_active": p.is_active,
        "templates": len(p.templates),
        "variables": len(p.variables),
        "updated_at": _iso(p.updated_at),
        "updated_by": _username(p, "updated_by"),
    }


def _profile_full(p):
    """Serialize a profile with nested rows for detail endpoints."""
    return {
        "id": str(p.id),
        "name": p.name,
        "device_role": p.device_role,
        "description": p.description,
        "is_active": p.is_active,
        "version": p.version,
        "templates": [_template_obj(t) for t in sorted(p.templates, key=lambda t: t.display_order)],
        "variables": {v.key: (profiles_service._safe_parse(v) if v.value_type != "string" else v.value) for v in p.variables},
        "interface_mappings": [_mapping_obj(it) for it in sorted(p.interface_templates, key=lambda it: it.display_order)],
        "created_at": _iso(p.created_at),
        "updated_at": _iso(p.updated_at),
        "created_by": _username(p, "created_by"),
        "updated_by": _username(p, "updated_by"),
    }


def _role_options():
    """Distinct device roles present in the inventory."""
    roles = {r for (r,) in Device.query.with_entities(Device.role).distinct().all() if r}
    roles.update(t for (t,) in ConfigurationProfile.query.with_entities(ConfigurationProfile.device_role).distinct().all() if t)
    return sorted(roles)


def _service_error(exc):
    """Map a ProfileServiceError onto the equivalent AppError."""
    status = 409 if isinstance(exc, profiles_service.ProfilesConflict) else 400
    name = "Conflict" if status == 409 else "Bad Request"
    return AppError(status, name, str(exc))


def _interface_payload(device_role=None):
    """Known interface types/counts presented in the form (FR-006).

    For an existing role this mirrors what ``validate_interface_mapping``
    enforces; with no role (new profile) it shows the union of every synced
    device type so the operator sees the available layouts.
    """
    counts = profiles_service.role_interface_counts(device_role)
    return [{"type": iface_type, "count": counts[iface_type]} for iface_type in sorted(counts)]


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

@bp.get("/profiles")
@viewer_required
def list():
    """HTML profile list with role filter."""
    role = request.args.get("role") or ""
    query = ConfigurationProfile.query.order_by(ConfigurationProfile.name)
    profiles = [p for p in query.all() if not role or p.device_role == role]
    return render_template("profiles/list.html", profiles=profiles, role=role, role_options=_role_options())


def _form_slots(profile=None):
    """Flatten profile rows into fixed slots for the form template."""
    templates = [None] * TEMPLATE_SLOTS
    variables = [None] * VARIABLE_SLOTS
    interfaces = [None] * INTERFACE_SLOTS
    if profile is not None:
        for i, t in enumerate(sorted(profile.templates, key=lambda t: t.display_order)):
            if i < TEMPLATE_SLOTS:
                templates[i] = {"name": t.name, "content": t.content, "order": t.display_order}
        for i, v in enumerate(sorted(profile.variables, key=lambda v: v.key)):
            if i < VARIABLE_SLOTS:
                variables[i] = {"key": v.key, "value": v.value if v.value_type == "string" else _yaml_text(v), "type": v.value_type}
        for i, it in enumerate(sorted(profile.interface_templates, key=lambda it: it.display_order)):
            if i < INTERFACE_SLOTS:
                interfaces[i] = {
                    "name": it.name,
                    "interface_type": it.interface_type,
                    "interface_range": it.interface_range,
                    "content": it.content,
                    "git_path": it.git_path,
                    "original_name": it.name,
                }
    return templates, variables, interfaces


def _yaml_text(row):
    """YAML text of the profile variables (form preview)."""
    value = profiles_service._safe_parse(row)
    return value if isinstance(value, str) else profiles_service.dump_yaml(value)


def _parse_form(form):
    """Build the service data dict from HTML form slots."""
    templates = []
    for i in range(TEMPLATE_SLOTS):
        name = (form.get(f"t_name_{i}") or "").strip()
        if not name:
            continue
        templates.append(
            {
                "name": name,
                "content": form.get(f"t_content_{i}") or "",
                "order": int(form.get(f"t_order_{i}") or 0),
            }
        )
    variables = {}
    for i in range(VARIABLE_SLOTS):
        key = (form.get(f"v_key_{i}") or "").strip()
        if key:
            variables[key] = form.get(f"v_value_{i}") or ""
    mappings = []
    for i in range(INTERFACE_SLOTS):
        name = (form.get(f"i_name_{i}") or "").strip()
        if not name:
            continue
        mappings.append(
            {
                "name": name,
                "interface_type": (form.get(f"i_type_{i}") or "").strip(),
                "interface_range": (form.get(f"i_range_{i}") or "all").strip(),
                "content": form.get(f"i_content_{i}") or "",
                "order": i,
            }
        )
    return {
        "name": (form.get("name") or "").strip(),
        # The role dropdown wins when a known role is selected; the free-text
        # field applies only for "— new role —" (both are visible without JS).
        "device_role": (form.get("device_role_select") or form.get("device_role") or "").strip(),
        "description": (form.get("description") or "").strip() or None,
        "templates": templates,
        "variables": variables,
        "interface_mappings": mappings,
    }


def _deleted_paths(profile, data):
    """Git paths of rows removed by the update so they are deleted from the repo."""
    new_template_names = {t["name"] for t in data.get("templates") or []}
    deleted = {f"templates/{profile.name}/{t.name}.j2" for t in profile.templates if t.name not in new_template_names}
    new_iface_names = {m["name"] for m in data.get("interface_mappings") or []}
    deleted |= {f"templates/{profile.name}/interfaces/{it.name}.j2" for it in profile.interface_templates if it.name not in new_iface_names}
    return [path for path in deleted]


@bp.get("/profiles/new")
@editor_required
def new():
    """GET /profiles/new: the blank profile form."""
    templates, variables, interfaces = _form_slots()
    return render_template(
        "profiles/form.html",
        profile=None,
        templates=templates,
        variables=variables,
        interfaces=interfaces,
        role_options=_role_options(),
        interface_options=_interface_payload(None),
        interface_role=None,
        errors=None,
    )


@bp.post("/profiles")
@editor_required
def create():
    """POST /profiles: create from the HTML form and redirect."""
    from flask_login import current_user

    data = _parse_form(request.form)
    try:
        profile = profiles_service.create_profile(current_app, current_user, data)
    except profiles_service.ProfilesError as exc:
        return _render_form_error(data, exc, "Create")
    flash(f"Profile '{profile.name}' created", "success")
    return redirect(url_for("profiles.detail", pid=profile.id))


@bp.get("/profiles/<int:pid>")
@viewer_required
def detail(pid):
    """GET /profiles/<id>: the profile detail page."""
    profile = ConfigurationProfile.query.get_or_404(pid)
    effective = {}
    device_count = Device.query.filter_by(role=profile.device_role).count()
    return render_template(
        "profiles/detail.html",
        profile=profile,
        templates=sorted(profile.templates, key=lambda t: t.display_order),
        variables=sorted(profile.variables, key=lambda v: v.key),
        interfaces=sorted(profile.interface_templates, key=lambda it: it.display_order),
        role_options=_role_options(),
        role_device_count=device_count,
    )


@bp.get("/profiles/<int:pid>/edit")
@editor_required
def edit(pid):
    """GET /profiles/<id>/edit: the prefilled profile form."""
    profile = ConfigurationProfile.query.get_or_404(pid)
    templates, variables, interfaces = _form_slots(profile)
    return render_template(
        "profiles/form.html",
        profile=profile,
        templates=templates,
        variables=variables,
        interfaces=interfaces,
        role_options=_role_options(),
        interface_options=_interface_payload(profile.device_role),
        interface_role=profile.device_role,
        errors=None,
    )


@bp.post("/profiles/<int:pid>")
@editor_required
def update(pid):
    """POST /profiles/<id>: update from the HTML form and redirect."""
    from flask_login import current_user

    profile = ConfigurationProfile.query.get_or_404(pid)
    data = _parse_form(request.form)
    data["_deleted_git_paths"] = _deleted_paths(profile, data)
    try:
        profile = profiles_service.update_profile(current_app, current_user, pid, data)
    except profiles_service.ProfilesError as exc:
        return _render_form_error(data, exc, "Edit", pid=pid)
    flash(f"Profile '{profile.name}' updated", "success")
    return redirect(url_for("profiles.detail", pid=profile.id))


@bp.post("/profiles/<int:pid>/delete")
@editor_required
def delete(pid):
    """POST /profiles/<id>/delete: delete from the HTML form."""
    profile = ConfigurationProfile.query.get_or_404(pid)
    try:
        profiles_service.delete_profile(current_app, pid)
    except profiles_service.ProfilesError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Profile '{profile.name}' deleted", "success")
    return redirect(url_for("profiles.list"))


def _render_form_error(data, exc, heading, pid=None):
    """Re-render the profile form with validation errors."""
    profile = None
    if pid is not None:
        profile = ConfigurationProfile.query.get(pid)
    templates, variables, interfaces = _form_slots(profile)
    # Re-populate slots from the submitted form so user input is preserved.
    for i, t in enumerate(data.get("templates") or []):
        if i < TEMPLATE_SLOTS:
            templates[i] = t
    for i, (key, value) in enumerate((data.get("variables") or {}).items()):
        if i < VARIABLE_SLOTS:
            variables[i] = {"key": key, "value": value, "type": "string"}
    for i, m in enumerate(data.get("interface_mappings") or []):
        if i < INTERFACE_SLOTS:
            interfaces[i] = m
    device_role = profile.device_role if profile is not None else None
    return (
        render_template(
            "profiles/form.html",
            profile=profile,
            templates=templates,
            variables=variables,
            interfaces=interfaces,
            role_options=_role_options(),
            interface_options=_interface_payload(device_role),
            interface_role=device_role,
            errors=[str(exc)],
        ),
        400,
    )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@bp.get("/api/profiles")
@viewer_required
def api_profiles():
    """GET /api/profiles: list with role and search filters."""
    query = ConfigurationProfile.query
    role = request.args.get("role")
    search = request.args.get("search")
    if role:
        query = query.filter(ConfigurationProfile.device_role == role)
    if search:
        like = f"%{search}%"
        query = query.filter(ConfigurationProfile.name.ilike(like))
    return jsonify({"profiles": [_profile_list_item(p) for p in query.order_by(ConfigurationProfile.name).all()]})


@bp.get("/api/profiles/<int:pid>")
@viewer_required
def api_profile_get(pid):
    """GET /api/profiles/<id>: one profile with nested rows."""
    profile = ConfigurationProfile.query.get_or_404(pid)
    return jsonify(_profile_full(profile))


@bp.post("/api/profiles")
@editor_required
def api_profile_create():
    """POST /api/profiles: create a profile (201) from the nested payload."""
    from flask_login import current_user

    data = request.get_json(silent=True) or {}
    try:
        profile = profiles_service.create_profile(current_app, current_user, data)
    except profiles_service.ProfilesError as exc:
        raise _service_error(exc)
    return jsonify(_profile_full(profile)), 201


@bp.put("/api/profiles/<int:pid>")
@editor_required
def api_profile_update(pid):
    """PUT /api/profiles/<id>: replace the profile payload; 409 on a stale version."""
    from flask_login import current_user

    profile = ConfigurationProfile.query.get(pid)
    if profile is None:
        raise AppError(404, "Not Found", "Profile not found")
    data = request.get_json(silent=True) or {}
    data.setdefault("_deleted_git_paths", _deleted_paths(profile, data))
    try:
        profile = profiles_service.update_profile(current_app, current_user, pid, data)
    except LookupError as exc:
        raise AppError(404, "Not Found", str(exc))
    except profiles_service.ProfilesError as exc:
        raise _service_error(exc)
    return jsonify(_profile_full(profile))


@bp.delete("/api/profiles/<int:pid>")
@editor_required
def api_profile_delete(pid):
    """DELETE /api/profiles/<id>: remove the profile."""
    from flask_login import current_user

    profile = ConfigurationProfile.query.get(pid)
    if profile is None:
        raise AppError(404, "Not Found", "Profile not found")
    try:
        profiles_service.delete_profile(current_app, pid)
    except profiles_service.ProfilesError as exc:
        raise _service_error(exc)
    return "", 204


@bp.get("/api/devices/<int:netbox_id>/overrides")
@viewer_required
def api_overrides_get(netbox_id):
    """GET /api/devices/<id>/overrides: the device variable overrides."""
    device = Device.query.filter_by(netbox_id=netbox_id).first_or_404()
    variables, updated = profiles_service.device_overrides(device)
    return jsonify({"variables": variables, "updated_at": _iso(updated)})


@bp.put("/api/devices/<int:netbox_id>/overrides")
@editor_required
def api_overrides_put(netbox_id):
    """PUT /api/devices/<id>/overrides: replace the device variable overrides."""
    device = Device.query.filter_by(netbox_id=netbox_id).first_or_404()
    data = request.get_json(silent=True) or {}
    variables = data.get("variables")
    if not isinstance(variables, dict):
        raise AppError(400, "Bad Request", "variables must be an object")
    try:
        profiles_service.save_device_overrides(current_app, device, variables, message=f"override: update {device.hostname}")
    except profiles_service.ProfilesError as exc:
        raise _service_error(exc)
    returned, updated = profiles_service.device_overrides(device)
    return jsonify({"variables": returned, "updated_at": _iso(updated)})
