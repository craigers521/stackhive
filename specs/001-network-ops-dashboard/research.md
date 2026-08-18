# Technical Research: Network Operations Dashboard

**Branch**: `001-network-ops-dashboard` | **Date**: 2025-07-23 | **Status**: Phase 0

This document covers technology decisions for the StackHive network operations dashboard platform. Each topic includes the chosen approach, rationale, and alternatives evaluated.

---

## 1. Flask for Network Ops Dashboard

### Decision
Flask 3.x with Flask-SQLAlchemy (3.1+), Flask-Login (0.6+), Flask-WTF (1.2+) and server-rendered Bootstrap 5.

### Rationale
Flask is the right fit for this project's constraints and goals:

**Server-rendered Bootstrap pattern.** Flask's `render_template()` with Jinja2 produces complete HTML pages from the server. This aligns with the constitution's "minimal JavaScript" constraint. Bootstrap 5's CSS-only components (navbars, cards, tables, modals, accordions, tabs) provide a professional UI without client-side framework overhead. The persistent left-sidebar navigation is implemented with Bootstrap's grid system and CSS `position: sticky` — zero JavaScript required.

**Flask-SQLAlchemy.** Provides a mature ORM layer over SQLite (and transparently over PostgreSQL later). The `flask_sqlalchemy` extension integrates cleanly with Flask's application factory pattern. For a dashboard managing ~500 devices and a handful of profiles, SQLite's single-writer model is sufficient. The ORM models map directly to the key entities: Device, Profile, Template, DeploymentRecord, User.

**Flask-Login.** Handles session management, `@login_required` decorator, and user identity in request context. For a 3-role RBAC system with local authentication, Flask-Login's `UserMixin` provides `is_authenticated`, `is_active`, `is_anonymous`, and `get_id()` out of the box. Role checking is a thin decorator layer on top.

**Blueprint pattern for routing.** The six main sections (Inventory, Profiles, Deployments, Monitoring, Onboarding, Settings) each become a Flask Blueprint with its own routes, templates subdirectory, and error handlers. This provides clean module boundaries:

```python
# app/routes/inventory.py
inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')
@inventory_bp.route('/')
@login_required
def device_list():
    devices = Device.query.all()
    return render_template('inventory/list.html', devices=devices)
```

**HTMX for progressive enhancement.** Where dynamic behavior is needed (filtering devices, expanding sections, triggering deployments without full page reload), HTMX provides server-driven interactivity via `hx-get`, `hx-post`, `hx-swap` attributes. HTMX sends XHR requests to Flask routes that return HTML fragments, which are swapped into the DOM. This keeps all logic on the server while providing a responsive feel.

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Django** | Full-featured framework with built-in admin, ORM, and auth. Overly heavy for this use case — the admin panel is not needed, and the project constitution favors minimal dependencies. Django's MTV pattern adds cognitive overhead for a team that doesn't need its full feature set. |
| **FastAPI** | Excellent for JSON APIs but poor fit for server-rendered HTML. FastAPI's strength is async I/O and OpenAPI documentation — neither is a priority for a synchronous dashboard rendering Bootstrap pages. |
| **Streamlit/Gradio** | Rapid-prototyping Python UI frameworks. Too opinionated for a production dashboard, limited control over HTML/CSS, poor RBAC support, and not designed for persistent deployments. |
| **Flask + React SPA** | Would violate the "minimal JavaScript" constitution principle. Adds build pipeline, npm dependencies, and state management complexity. The value of client-side reactivity is not justified for data-table-heavy network ops pages. |
| **HTMX vs HTMX-free** | A pure form-submit reload approach would work but creates a sluggish experience for filtering and in-place actions. HTMX's 24KB payload is a minimal addition that dramatically improves UX without introducing SPA complexity. |

### Specific Recommendations
- Use the Flask application factory pattern with `create_app()` for testability
- Store Bootstrap 5 via CDN (jsDelivr) in the base template; no npm build step
- Use HTMX 2.x for: device list filtering, deployment status polling, collapsible sections
- Configure `FLASK_WTF_CSRF_ENABLED = True` and `SECRET_KEY` from environment variable
- Use `Flask-Caching` with simple in-memory backend for cached NetBox inventory queries (5-minute TTL)
- For pagination at 500 devices, use Flask-SQLAlchemy's `paginate()` with 50 items per page

---

## 2. SQLite vs PostgreSQL for Dashboard Database

### Decision
SQLite for initial deployment with migration path to PostgreSQL documented and tested.

### Rationale
**Why SQLite is appropriate now:**
- The dashboard serves 3-10 concurrent users writing to the database infrequently (profile edits, deployment records). SQLite's single-writer model handles this load with zero configuration.
- Data volume is modest: ~500 Device records, ~20 Profile records, ~1000 DeploymentRecord entries over a year. SQLite databases under 1GB perform well.
- Docker volume persistence is trivial: a single `sqlite:///app/storage.db` file mapped to a named volume. No separate database container, no connection pooling, no backup scripts.
- Flask-SQLAlchemy's `SQLALCHEMY_DATABASE_URI` accepts both `sqlite:///` and `postgresql://` URIs with the same model definitions. The ORM abstracts dialect differences for standard operations.

**When to migrate to PostgreSQL:**
- More than 20 concurrent writers (SQLite writer lock contention)
- Need for full-text search across configuration content
- Multi-instance deployment (SQLite is file-based; Postgres supports concurrent access from multiple app instances)
- Requirement for JSONB columns for flexible template variable storage

**Migration path:**
1. Install `psycopg2-binary` as optional dependency in `requirements.txt`
2. Change `SQLALCHEMY_DATABASE_URI` to `postgresql://user:pass@db:5432/stackhive`
3. Add `db` service to docker-compose with `postgres:16-alpine` image and named volume
4. Run `flask db upgrade` with Alembic — the same migration scripts work against both backends
5. For zero-downtime migration, use `sqlite3 .dump` to export SQL, import into Postgres, then flip the URI

**Alembic for migrations:**
Use `Flask-Migrate` (Alembic wrapper) from day one. Even with SQLite, schema changes during development benefit from versioned migration files. The migration scripts are SQL-standard enough that they run against both SQLite and PostgreSQL.

```python
# app/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()

# app/__init__.py
def create_app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///stackhive.db')
    db.init_app(app)
    migrate.init_app(app, db)
    return app
```

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **PostgreSQL from day one** | Adds a container, environment configuration, and connection management before the project needs it. The migration path from SQLite is well-documented, so deferring Postgres avoids premature infrastructure. |
| **MySQL/MariaDB** | No advantage over PostgreSQL for this use case. Postgres has better JSON support, more active Python ecosystem (psycopg2), and is the database used by NetBox and GitLab CE already in the stack. |
| **MongoDB** | Document database offers flexible schema but loses relational integrity for Device-Profile-Deployment relationships. The ORM investment with Flask-SQLAlchemy would be wasted. |
| **Redis** | Appropriate only as a caching layer, not as a primary database. No transactional guarantees, no relational queries. |

### Specific Recommendations
- Start with `sqlite:////var/lib/stackhive/stackhive.db` in a Docker named volume
- Install `psycopg2-binary` (dev) and `gunicorn` (prod) as optional dependencies
- Create an `app/alembic/` directory with initial migration on day one
- Use `DATABASE_URL` environment variable for backend switching
- Set `SQLALCHEMY_TRACK_MODIFICATIONS = False` to suppress Flask-SQLAlchemy warning
- For the dashboard's read-heavy workload, add `PRAGMA journal_mode=WAL` via SQLAlchemy event listener for better concurrent read performance

---

## 3. NETCONF Patterns for Atomic Config Commits on IOS-XE

### Decision
Use `ncclient` Python library with `<edit-config>` using `<merge>` operation into the `running` datastore, with capabilities discovery at session init and structured error handling.

### Rationale
**Merge vs Replace:**
The spec requires that snippets merge into existing config without replacing unrelated sections. NETCONF's `<merge>` operation inserts new nodes and updates existing matching nodes without deleting absent nodes. This is the correct default for overlay-style config management.

```xml
<!-- NETCONF edit-config with merge -->
<edit-config xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <target>
    <running/>
  </target>
  <config>
    <native xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-native">
      <interface>
        <GigabitEthernet>
          <name>1/0/1</name>
          <description>UPLINK-to-Core</description>
          <mode>
            <network></network>
          </mode>
        </GigabitEthernet>
      </interface>
    </native>
  </config>
</edit-config>
```

Use `<replace>` only for sections where full replacement is intentional (e.g., replacing the entire `router bgp` block). `<replace>` deletes nodes not present in the payload — dangerous for partial snippets.

**Capabilities discovery:**
On NETCONF session establishment, the device sends a `<capabilities>` list. Parse this to:
- Confirm `urn:ietf:params:netconf:capability:candidate:1.0` is present (required for atomic commit)
- Check YANG model support: `Cisco-IOS-XE-*` namespaces
- Determine if `validate` capability is available for pre-commit syntax checking

```python
import ncclient

with ncclient.manager.connect(
    host=hostname, port=830,
    username=user, password=password,
    hostkey_verify=False,
    device_params={'name': 'iosxe'}
) as m:
    caps = m.server_capabilities
    if 'urn:ietf:params:netconf:capability:candidate:1.0' not in caps:
        raise RuntimeError(f"{hostname}: candidate datastore not supported")
```

**Atomic commit pattern with candidate datastore:**
For IOS-XE, the candidate datastore provides atomic commit semantics:
1. Edit the `candidate` datastore with config snippets
2. Optionally validate with `<validate>` RPC
3. Commit with `<commit>` RPC — all-or-nothing
4. If commit fails, the running config is untouched

```python
# Atomic commit with candidate datastore
m.edit_config(target='candidate', config=config_xml)
try:
    m.validate(source='candidate')
except ncclient.operations.rpc.RPCError as e:
    log.error(f"Validation failed on {hostname}: {e}")
    return False

try:
    m.commit()
except ncclient.operations.rpc.RPCError as e:
    log.error(f"Commit failed on {hostname}: {e}")
    return False
```

**Error handling strategy:**
- `<rpc-error>` with `error-type="protocol"`: session-level issues (auth, timeout) — retry with backoff
- `<rpc-error>` with `error-type="application"`: YANG model violations (invalid value, missing mandatory leaf) — log and abort; do not retry
- `<rpc-error>` with `error-type="processing"`: device resource issues (insufficient memory) — log and abort
- Connection errors (`ncclient.transport.session.SessionListenThread`): transient network issues — retry with exponential backoff up to 3 attempts

**Ansible integration:**
The `cisco.ios.ios_config` Ansible module uses `netconf` transport when `ansible_network_os: ios` and `ansible_connection: netconf`. This handles session management, capability negotiation, and error decoding. For the project's architecture where Ansible drives config deployment:

```yaml
# In ansible/roles/iosxe/tasks/main.yml
- name: Apply config snippet via NETCONF
  cisco.ios.ios_config:
    parents: "{{ item.parents }}"
    config: "{{ item.lines }}"
    save_when: never  # we control commit explicitly
  loop: "{{ config_snippets }}"
  vars:
    ansible_connection: netconf
```

However, for true atomicity across multiple snippets, compose the full YANG XML payload in Python and push it as a single `<edit-config>` — the Ansible module applies snippets sequentially, which is not atomic across snippets.

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **EOS Config Engine (ECE) / NX-OS atomic commit** | IOS-XE does not have an equivalent to Arista's ECE or NX-OS's atomic commit plugin. The candidate datastore is the closest mechanism available. |
| **NAPALM merge_config** | NAPALM wraps multiple providers but adds abstraction overhead. For IOS-XE specifically, `ncclient` provides direct NETCONF access with full capability control. NAPALM is useful as a fallback for non-NETCONF devices. |
| **Netmiko (SSH-based)** | Pushes config line-by-line over SSH. Not atomic — partial applies are possible if the device rejects a line mid-push. Used only as fallback for devices without NETCONF support. |
| **RESTCONF** | IOS-XE supports RESTCONF as an alternative to NETCONF. RESTCONF uses HTTP PUT/PATCH on YANG-modeled resources. Simpler for scripting but less mature tooling than NETCONF. Deferred to vendor-agnostic phase. |

### Specific Recommendations
- Use `ncclient` 0.6.2+ for direct NETCONF operations from the Flask app (deployment trigger service)
- For Ansible-driven deployments, prefer `ansible_connection: netconf` with `cisco.ios.ios_config`
- Compose multi-snippet configs into a single YANG XML payload for atomicity
- Implement capability caching: store discovered capabilities per device in NetBox custom fields
- Always use `<merge>` for individual snippet application; reserve `<replace>` for full-section replacement with explicit operator confirmation
- Implement a config preview step: render Jinja2 templates, convert to YANG XML, but do not send — display to operator before commit
- For rollback: before committing, fetch current config with `<get-config source="running"/>`, store hash in DeploymentRecord. On rollback, diff and generate reverse merge payload.

---

## 4. NetBox Dynamic Inventory Plugin for Ansible

### Decision
Use the official `netbox-community.netbox` Ansible collection's inventory plugin with device role and tag-based grouping.

### Rationale
The NetBox inventory plugin queries the NetBox REST API at playbook runtime to build Ansible inventory. This ensures the inventory always reflects NetBox's source of truth without manual sync.

**How it works:**
The plugin reads a YAML configuration file that specifies the NetBox URL, token, and query filters. At runtime, it issues API calls to `/api/dcim/devices/`, groups devices by configurable attributes, and populates host variables from NetBox custom fields.

**Configuration (`ansible/inventory/netbox.yml`):**
```yaml
plugin: netbox_community.netbox_netbox.netbox
strict: true
url: http://netbox:8080
token: "{{ env['NETBOX_TOKEN'] }}"
valid_endpoint: /api/status/

# Group devices by NetBox device role
groups:
  access_switches: "role.slug == 'access-switch'"
  core_switches: "role.slug == 'core-switch'"
  distribution_switches: "role.slug == 'dist-switch'"

# Group by tags for additional categorization
keyed_groups:
  - key: tags
    separator: ""
    prefix: "tag"

# Map NetBox fields to Ansible variables
compose:
  - ansible_host = primary_ip.address.split('/')[0]
  - ansible_network_os = "ios"
  - ansible_connection = "netconf"
  - ansible_netconf_port = 830
  - device_role = role.slug
  - device_type = device_type.model
  - site_name = site.name
  - serial_number = serial
  - tenant = tenant.name | default('')

# Flatten nested objects for easier access
flatten_spec:
  - role
  - device_type
  - site
  - tenant
```

**Device role and tag mapping:**
- **Device roles** in NetBox (`device.role`) map directly to Ansible groups, which then map to `group_vars/` directories. A device with role `access-switch` gets variables from `group_vars/access_switches/`.
- **Tags** in NetBox provide secondary grouping (e.g., `mdt-enabled`, `ztp-pending`, `meraki-cloud`). Use `keyed_groups` to create `tag_mdt_enabled`, `tag_ztp_pending` Ansible groups.
- **Custom fields** in NetBox store mutable metadata (e.g., `config_profile`, `last_deployed`). These are exposed as host variables via `compose`.

**Connection variable injection:**
The plugin sets `ansible_host`, `ansible_network_os`, and `ansible_connection` from NetBox data. Device credentials are never stored in NetBox — they come from Ansible Vault or environment variables.

```yaml
# ansible/inventory/netbox.yml (credential separation)
compose:
  - ansible_user = env.get('ANSIBLE_NETCONF_USER', 'admin')
  - ansible_password = env.get('ANSIBLE_NETCONF_PASSWORD', '')
  - ansible_become_password = env.get('ANSIBLE_ENABLE_PASSWORD', '')
```

**Performance considerations:**
For 500 devices, the full inventory query takes ~2-5 seconds against a local NetBox instance. Use `--limit` to target subsets during development. Cache the inventory with `cache_plugin: jsonfile` for repeated playbook runs in the same session.

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Static inventory file** | The spec explicitly requires dynamic inventory. Static files drift from NetBox and require manual sync scripts. |
| **Custom Python inventory script** | Possible but reinvents the wheel. The official plugin handles authentication, pagination, field mapping, and grouping. |
| **Ansible Tower/AWX dynamic sync** | AWX has built-in NetBox integration but introduces a heavy control plane. GitLab CI runs Ansible directly without AWX. |
| **NetBox as Ansible host_vars source only** | Could use NetBox API only for variable lookup inside playbooks while maintaining static inventory. Loses automatic device discovery and grouping. |

### Specific Recommendations
- Install collection: `ansible-galaxy collection install netbox_community.netbox_netbox`
- Pin to a specific version in `collections/requirements.yml` for reproducibility
- Use `strict: true` to fail the playbook if inventory queries return errors (NetBox down, bad token)
- Define device roles in NetBox with consistent slugs: `access-switch`, `core-switch`, `dist-switch`
- Use NetBox custom fields for: `config_profile` (links to StackHive profile), `ansible_managed` (boolean flag), `last_config_push` (datetime)
- Store NETBOX_TOKEN in docker-compose `.env` file, never in version control
- Add a `netbox_health` check in the Flask app that pings `/api/status/` to detect inventory unavailability (edge case from spec)
- For the Flask dashboard's inventory view, query NetBox API directly (not through Ansible) and cache results with 5-minute TTL

---

## 5. GitLab CI/CD Pipeline Patterns for Config Approval Workflows

### Decision
Branch-based workflow with merge request (MR) pipelines, manual approval gates via GitLab's `when: manual` and protected branches, and a shared GitLab Runner in Docker mode.

### Rationale
**Branch model:**
- `main`: production config branch — protected, only mergeable via approved MR
- `working`: default development branch where the Flask app auto-commits changes
- Feature branches (`profile/update-access-vlans`, etc.) for complex multi-device changes

**Pipeline stages in `.gitlab-ci.yml`:**
```yaml
stages:
  - validate
  - preview
  - approve
  - deploy
  - verify

# Stage 1: Validate syntax and structure
validate_configs:
  stage: validate
  script:
    - ansible-playbook --syntax-check -i inventory/netbox.yml site.yml --limit "{{ target_devices }}"
    - python scripts/validate_templates.py
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "working"

# Stage 2: Generate config preview (no push)
preview_configs:
  stage: preview
  script:
    - ansible-playbook -i inventory/netbox.yml site.yml --check --diff
      --limit "{{ target_devices }}" -e "deploy_mode=preview"
  artifacts:
    paths:
      - preview/*.cfg
    expire_in: 1 week
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

# Stage 3: Manual approval gate
approve_deployment:
  stage: approve
  script:
    - echo "Deployment approved by $CI_MERGE_REQUEST_APPROVER"
  when: manual
  allow_failure: false
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

# Stage 4: Deploy to devices
deploy_configs:
  stage: deploy
  script:
    - ansible-playbook -i inventory/netbox.yml site.yml
      --limit "{{ target_devices }}" -e "deploy_mode=live"
  environment:
    name: production
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

# Stage 5: Post-deploy verification
verify_configs:
  stage: verify
  script:
    - ansible-playbook -i inventory/netbox.yml playbooks/verify.yml
      --limit "{{ target_devices }}"
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

**Merge request workflow:**
1. Flask app auto-commits profile changes to `working` branch
2. Editor creates MR from `working` to `main` (or uses a scheduled auto-MR)
3. Pipeline runs validate and preview stages automatically
4. Admin reviews diff and clicks "Approve" on the manual gate
5. Deploy and verify stages execute
6. MR merges to `main` on success

**Protected branch rules:**
- `main`: push protection enabled, merge only, requires approval from Admin role
- `working`: push allowed for Editor and Admin roles
- Use GitLab's `Require approval before merging` with minimum 1 approval from a member with Admin role

**Runner configuration:**
```yaml
# docker-compose.yml service
gitlab-runner:
  image: gitlab/gitlab-runner:v17.0
  container_name: stackhive-runner
  volumes:
    - runner-config:/etc/gitlab-runner
    - /var/run/docker.sock:/var/run/docker.sock  # Docker executor
    - ./ansible:/ansible:ro                       # Shared templates, roles, vars
  network_mode: host                              # Direct access to managed devices
  depends_on:
    gitlab:
      condition: service_healthy
```

The runner uses Docker executor with socket mount. Job containers spawned by the runner inherit:
- **Docker socket**: For nested container operations
- **Network access**: `network_mode: host` gives direct reachability to managed devices
- **Ansible bind mount**: `/ansible` directory (read-only) with templates, roles, vars, playbooks

**Runner registration:**
```bash
docker compose exec gitlab-runner gitlab-runner register \
  --url http://gitlab:8090 \
  --token <registration-token> \
  --executor docker \
  --docker-image "python:3.12-slim" \
  --docker-volumes "/var/run/docker.sock:/var/run/docker.sock" \
  --docker-volumes "/host/ansible:/ansible:ro" \
  --network-mode "host" \
  --tag-list "ansible,netconf" \
  --run-untagged="false"
```

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **GitLab Environments with deploy approvals** | GitLab's built-in deployment approvals are designed for app deployments, not config pushes. The manual `when: manual` gate is more flexible for per-deployment review. |
| **Ansible AWX/Tower** | Provides a full UI for job templates, inventory, and approvals. Adds a heavy container to the stack (Postgres, RabbitMQ, multiple workers). Over-engineered for a 3-10 user team. |
| **Linear pipeline without approval gate** | Skipping the approval gate violates the spec requirement for review-and-approval (FR-017). Even a manual click gate provides audit trail. |
| **Trigger-based pipeline from Flask** | Flask could trigger pipelines via GitLab API on deploy button click. This works well for ad-hoc deployments but the MR-based workflow provides better review and diff visibility. Use API triggers for emergency hotfix deployments. |
| **Git hooks for validation** | Pre-commit hooks on the runner catch syntax issues before merge. Complements CI validation but doesn't replace it (hooks only run on push, not on MR review). |

### Specific Recommendations
- Use `--check --diff` mode for Ansible preview stage to show what would change without applying
- Store pipeline artifacts (generated configs) with 1-week expiry for audit reference
- Implement a `deploy_mode` variable: `preview` renders but doesn't push; `live` executes NETCONF commits
- Add pipeline variables for `target_devices` (comma-separated hostnames) to limit scope
- Use GitLab's `environment` keyword to track deployment state and enable rollback UI
- Configure the runner with `pull_policy: if-not-present` to avoid rate-limiting on image pulls
- Add a `verify` playbook that reads back deployed config via NETCONF `<get-config>` and compares with rendered template
- For the Flask UI, query GitLab API to display pipeline status and approval state per MR

---

## 6. Traefik Path-Based Routing for Multi-Service Proxy

### Decision
Traefik 3.x with Docker provider, using `traefik.http.routers.*.rule=PathPrefix()` labels for path-based routing. The dashboard serves `/` directly; backend services are proxied under `/netbox/*`, `/gitlab/*`, `/grafana/*`, and `/ztp/*`.

### Rationale
**Path prefix routing with Traefik labels:**
Each Docker service declares its routing rules via container labels. Traefik's Docker provider reads these labels and configures the reverse proxy dynamically.

```yaml
# traefik/traefik.yml — Static configuration
api:
  dashboard: true
  insecure: true  # Enable only for lab; use TLS in production

entryPoints:
  web:
    address: ":80"
  websecure:
    address: ":443"

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false
    network: stackhive
  file:
    filename: "/etc/traefik/dynamic.yml"
    watch: true

log:
  level: INFO

# docker-compose.yml excerpts with Traefik labels
services:
  dashboard:
    image: stackhive-dashboard:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.dashboard.rule=PathPrefix(`/`)"
      - "traefik.http.routers.dashboard.priority=1"
      # Lower priority so /netbox, /grafana, etc. match first

  netbox:
    image: netboxcommunity/netbox:v4.0
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.netbox.rule=PathPrefix(`/netbox`)"
      - "traefik.http.routers.netbox.priority=10"
      - "traefik.http.middlewares.netbox-strip.stripprefix.prefixes=/netbox"
      - "traefik.http.routers.netbox.middlewares=netbox-strip"

  gitlab:
    image: gitlab/gitlab-ce:17.0-ce.0
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.gitlab.rule=Host(`gitlab.stackhive.local`)"
      - "traefik.http.routers.gitlab.priority=10"

  grafana:
    image: grafana/grafana:10.4
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.grafana.rule=PathPrefix(`/grafana`)"
      - "traefik.http.routers.grafana.priority=10"
```

```yaml
# traefik/dynamic.yml — Dynamic configuration
http:
  middlewares:
    health-check:
      healthcheck:
        path: /api/health
        interval: 30s

  # Catch-all routers for services not using Docker labels
  routers:
    ztp-public:
      rule: "PathPrefix(`/ztp`)"
      service: dashboard
      priority: 15
      # ZTP routes are served by the Flask app but bypass auth in the blueprint
```

**Priority ordering:**
Traefik evaluates routers by priority (higher wins). The dashboard at `/` uses `priority=1` as a catch-all. Specific paths (`/netbox`, `/gitlab`, `/grafana`) use `priority=10` so they match before the dashboard catches the request.

**StripPrefix middleware for NetBox:**
NetBox expects requests at `/`. The `StripPrefix` middleware removes `/netbox` from the URL path before forwarding to the NetBox container. Without this, NetBox sees `/netbox/api/...` and returns 404.

**GitLab sub-path challenge:**
GitLab CE is not designed to run under a sub-path. It hardcodes URLs in its configuration. Two approaches:
1. **Regexp rewrite:** Use Traefik's `ReplacePathRegex` middleware to strip `/gitlab` prefix. Requires setting `external_url` in GitLab config to include the sub-path.
2. **Subdomain routing (recommended for GitLab):** Route GitLab to `gitlab.<domain>` instead of `/gitlab`. This avoids URL rewriting issues entirely.

Given the constraint of a single entry point, use subdomain routing for GitLab and path routing for others:
```yaml
  gitlab:
    labels:
      - "traefik.http.routers.gitlab.rule=Host(`gitlab.stackhive.local`)"
      # No path prefix — GitLab serves at root of its subdomain
```

For local development, add entries to `/etc/hosts`:
```
127.0.0.1 stackhive.local gitlab.stackhive.local
```

**ZTP file server:**
```yaml
  ztp-server:
    labels:
      - "traefik.http.routers.ztp.rule=PathPrefix(`/ztp`)"
      - "traefik.http.middlewares.ztp-strip.stripprefix.prefixes=/ztp"
      - "traefik.http.routers.ztp.middlewares=ztp-strip"
```

**TLS termination:**
Traefik handles TLS at the edge. Use Traefik's internal CertResolver with Let's Encrypt for production, or a self-signed certificate for lab:
```yaml
# traefik/traefik.yml
certificatesResolvers:
  lab:
    acme:
      storage: /var/lib/traefik/acme.json
      tlsChallenge: {}
```

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Nginx as reverse proxy** | Nginx provides equivalent path-based routing with `location /prefix/ { proxy_pass ... }`. However, Traefik's Docker provider auto-discovers services from labels, eliminating manual config updates when services are added. |
| **Caddy** | Simple syntax, automatic HTTPS. Less mature Docker integration than Traefik. Fewer middleware options for edge cases (GitLab sub-path). |
| **Traefik with subdomain routing for all services** | Would work cleanly (`netbox.domain`, `grafana.domain`, etc.) but requires DNS configuration for each service. Path-based routing works with a single hostname. |
| **Port-based access (no proxy)** | Each service on a different port (8080, 8081, 8082...). No single entry point, no unified TLS, no authentication delegation. Violates SC-008. |

### Specific Recommendations
- Use Traefik 3.x (latest stable) with Docker provider enabled
- Set dashboard router priority to 1 as catch-all; all proxied services at priority 10+
- Apply `StripPrefix` middleware to NetBox and ZTP server
- Route GitLab on a subdomain to avoid URL rewriting complexity
- Configure Traefik entrypoint on port 80 (HTTP) for lab; add 443 with TLS for production
- Use `traefik.http.services.*.loadbalancer.server.port` to map to each container's internal port
- Add `traefik.http.routers.*.entrypoints=web` to bind to the correct entrypoint
- For the Flask app, set `SERVER_NAME` and `APPLICATION_ROOT` so Flask generates correct URLs for assets and redirects under the proxy

---

## 7. Model-Driven Telemetry on IOS-XE

### Decision
Configure MDT dial-out subscriptions via NETCONF/YANG with gRPC encoding, pushing data to Telegraf gRPC receiver, which forwards to InfluxDB.

### Rationale
**Subscription model:**
IOS-XE MDT uses a subscription-based model where the device pushes telemetry data to a receiver (dial-out) rather than the receiver polling (dial-in). This is more efficient for continuous monitoring and provides sub-second data resolution.

**Configuration via YANG model (`Cisco-IOS-XE-telemetry_model_driven.yang`):**
```python
# Generated NETCONF config for MDT dial-out
mdt_config = """
<telemetry-ietf xmlns="urn:ietf:params:xml:ns:yang:ietf-yang-telemetry">
  <subscription>
    <subscription-name>stackhive-default</subscription-name>
    <stream>gribi</stream>
    <sample-interval>10000</sample-interval>  <!-- 10 seconds -->
    <suppress-redundancy>sample</suppress-redundancy>
    <receiver>
      <address>telegraf</address>
      <port>9000</port>
      <protocol>grpc</protocol>
    </receiver>
    <sensor-path>
      <xpath>/ios-native:native/interface/GigabitEthernet</xpath>
    </sensor-path>
    <sensor-path>
      <xpath>/ios-native:native/process-cpu</xpath>
    </sensor-path>
    <sensor-path>
      <xpath>/ios-native:native/memory</xpath>
    </sensor-path>
  </subscription>
</telemetry-ietf>
"""
```

**Sensor paths for monitoring:**
The key sensor paths to subscribe to for device health dashboards:

| Sensor Path | Data Collected | Interval |
|---|---|---|
| `/ios-native:native/interface/GigabitEthernet` | Interface counters (in/out octets, packets, errors, CRC) | 10s |
| `/ios-native:native/interface/TenGigabitEthernet` | Same for 10G interfaces | 10s |
| `/ios-native:native/process-cpu` | CPU utilization (5s, 1min, 5min averages) | 30s |
| `/ios-native:native/memory/statistics` | Memory pool usage (processor, iomem) | 30s |
| `/ios-native:native/platform/qfp-utilization` | QFP utilization (Catalyst 9K) | 30s |
| `/ios-native:native/snmp/community` | SNMP community strings (config compliance) | on-change |
| `/ios-ldp:ldp/neighbors` | LDP neighbor state | 30s |

**Encoding format — gRPC vs UDP/JUNIPER:**
gRPC with protobuf encoding is the preferred format:
- Structured, typed data (no parsing ambiguity)
- Compression support (reduces bandwidth for high-frequency telemetry)
- TLS support for encrypted telemetry in transit
- Telegraf has a `grpc_listener_v2` input plugin with built-in Cisco IOS-XE protobuf schemas

UDP with `encoding_type juniper` is an alternative for environments where gRPC is blocked by firewalls, but requires custom parsing in Telegraf.

**Telegraf configuration:**
```toml
# === Input: Device MDT telemetry (gRPC) ===
[[inputs.grpc_listener_v2]]
  services = ['/telemetry_grpc.GrpcTelemetry/gNMICapability']
  services = ['/telemetry_grpc.GrpcTelemetry/gNMIStreamTelemetry']
  services = ['/telemetry_grpc.GrpcTelemetry/gNMIPoll']

[[inputs.grpc_listener_v2.descriptors]]
  file = '/etc/telegraf/protos/cisco_ios_xe/*.proto'

# === Input: Docker containers ===
[[inputs.docker]]
  endpoint = "unix:///var/run/docker.sock"
  container_name_include = ["stackhive-*"]
  perdevice = true
  total = false

# === Input: Host CPU ===
[[inputs.cpu]]
  percpu = true
  totalcpu = true
  collect_cpu_time = false
  report_active = false

# === Input: Host Memory ===
[[inputs.mem]]

# === Input: Host Disk ===
[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs", "devshm", "overlay", "aufs", "squashfs"]

# === Input: Host Disk I/O ===
[[inputs.diskio]]
  interval = "10s"

# === Input: Host System ===
[[inputs.system]]
  fields_exclude = ["load1"]

# === Input: Host Net (network interfaces) ===
[[inputs.net]]

# === Output: InfluxDB ===
[[outputs.influxdb_v2]]
  urls = ["http://influxdb:8086"]
  token = "$INFLUX_TOKEN"
  organization = "stackhive"
  bucket = "telemetry"
```

**Docker Compose mounts for Telegraf:**
```yaml
telegraf:
  image: telegraf:1.28
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro    # Container metrics
    - /proc:/host/proc:ro                              # Host CPU, processes
    - /sys:/host/sys:ro                                # Host memory, disk
    - /etc/telegraf/protos:/etc/telegraf/protos:ro     # Cisco protobuf schemas
    - ./monitoring/telegraf/telegraf.conf:/etc/telegraf/telegraf.conf:ro
  environment:
    - HOST_PROC=/host/proc
    - HOST_SYS=/host/sys
```

**Template-driven MDT config:**
The MDT subscription is generated from a Jinja2 template (`ansible/roles/iosxe/templates/mdt.j2`) that includes common sensor paths. Per-device customization (additional interfaces, custom intervals) comes from host_vars:

```jinja2
{# mdt.j2 #}
telemetry
 ietf subscription {{ mdt_subscription_name | default('stackhive-default') }}
  type publish
  sensor-path subscription
   {{ mdt_sensor_paths | default(default_sensor_paths) | join('\n   ') }}
  encode encoding
   proto
  destination
   host {{ mdt_receiver_host | default('telegraf') }}
   port grpc {{ mdt_receiver_port | default(9000) }}
```

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **SNMP polling** | Traditional approach but pull-based, limited to MIB-defined OIDs, no sub-minute resolution, and generates per-poll CPU overhead on the device. MDT is push-based and more efficient. |
| **dial-in (receiver-initiated)** | The receiver opens the connection to the device. Requires inbound connectivity to port 57400+ on each device, which is often blocked by firewall policy. Dial-out is easier to firewall. |
| **gNMI instead of MDT** | gNMI (Google Network Management Interface) is becoming the industry standard. IOS-XE supports gNMI but MDT is more mature on this platform with better YANG model coverage. gNMI is the target for the vendor-agnostic phase. |
| **Syslog for telemetry** | Syslog provides event-based data (interface flaps, auth failures) but not periodic metrics. Use syslog as a complement to MDT for event correlation, not as a replacement. |
| **RESTCONF monitoring** | IOS-XE RESTCONF can stream changes with `?subscription` parameter. Less efficient than gRPC for continuous telemetry. Better suited for configuration change monitoring. |

### Specific Recommendations
- Use gRPC encoding with protobuf schemas from Cisco's official `grpc-protobuf` release for IOS-XE
- Set sample interval to 10s for interface counters, 30s for CPU/memory
- Enable `suppress-redundancy sample` mode to reduce bandwidth — only sends changed values
- Download Cisco protobuf schemas and bundle with the Telegraf container image
- Configure MDT subscription as part of the standard profile template — every managed device sends telemetry
- In Grafana, create a "Device Health" dashboard template that auto-populates per device using InfluxDB tags
- Add a `verify` Ansible task that checks `show telemetry ietf subscription` on each device to confirm MDT is active
- For the Flask UI, source up/down status from Grafana API (query InfluxDB for last-seen timestamp) rather than polling devices directly

---

## 8. Docker Compose Patterns for Multi-Service Deployments

### Decision
Docker Compose v2.24+ with named volumes for persistent data, `depends_on` with condition health checks for startup ordering, and separate `docker-compose.override.yml` for development settings.

### Rationale
**Volume and bind-mount strategy:**
Stateful data uses named volumes. The `ansible/` directory uses a bind mount so template edits are immediately visible to both the Flask app (for preview) and the GitLab runner (for deployment).

```yaml
volumes:
  stackhive-db:           # Flask SQLite database
  gitlab-data:            # GitLab CE application data
  gitlab-etc:             # GitLab configuration
  gitlab-logs:            # GitLab logs
  influxdb-data:          # InfluxDB time series data
  grafana-data:           # Grafana dashboards, datasources, users
  traefik-acme:           # Traefik TLS certificates
  runner-config:          # GitLab Runner configuration

# Bind mounts (defined per service):
#  ./ansible:/ansible:ro       → GitLab runner job containers
#  ./ansible:/app/ansible:rw   → Flask app container
#  ./monitoring/telegraf/:/etc/telegraf/:ro → Telegraf config
#  ./monitoring/grafana/:/etc/grafana/provisioning/:ro → Grafana provisioning
#  ./traefik/:/etc/traefik/:ro → Traefik config
```

**Compose include for NetBox:**
```yaml
# docker-compose.yml
include:
  - docker-compose.netbox.yml
```

The NetBox compose (`docker-compose.netbox.yml`) defines its own volumes:
```yaml
# docker-compose.netbox.yml
volumes:
  netbox-postgres-data:
  netbox-redis-data:
  netbox-media:
```

Named volumes survive container recreation and are backed by Docker's volume driver. For backup, use `docker run --rm -v stackhive-db:/data -v $(pwd)/backups:/backup alpine tar czf /backup/stackhive-db.tar.gz -C /data .`.

**Docker network:**
All services on the default compose network (`stackhive`) for DNS-based service discovery. The GitLab Runner uses `network_mode: host` for direct device access.

```yaml
# docker-compose.yml
networks:
  default:
    name: stackhive
    driver: bridge
```

**Service dependencies with health checks:**
Use `depends_on` with `condition: service_healthy` to enforce startup ordering:

```yaml
services:
  netbox-postgres:
    image: postgres:16-alpine
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U netbox -d netbox"]
      interval: 5s
      timeout: 3s
      retries: 10

  netbox-redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  netbox:
    image: netboxcommunity/netbox:v4.0
    depends_on:
      netbox-postgres:
        condition: service_healthy
      netbox-redis:
        condition: service_healthy

  influxdb:
    image: influxdb:2.7
    healthcheck:
      test: ["CMD", "influx", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  telegraf:
    image: telegraf:1.28
    depends_on:
      influxdb:
        condition: service_healthy

  grafana:
    image: grafana/grafana:10.4
    depends_on:
      influxdb:
        condition: service_healthy

  gitlab:
    image: gitlab/gitlab-ce:17.0-ce.0
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8090/-/health"]
      interval: 15s
      timeout: 5s
      retries: 15
      start_period: 180s

  gitlab-runner:
    depends_on:
      gitlab:
        condition: service_healthy

   dashboard:
    image: stackhive-dashboard:latest
    build: ./app
    volumes:
      - stackhive-db:/var/lib/stackhive
      - ./ansible:/app/ansible:rw              # Ansible templates, vars, playbooks
      - ./monitoring:/app/monitoring:ro         # Grafana/Telegraf config access
    depends_on:
      - netbox
      - gitlab
    # Dashboard doesn't hard-block on NetBox/GitLab availability at startup
    # It handles service unavailability with graceful error pages

   telegraf:
    image: telegraf:1.28
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - ./monitoring/telegraf/telegraf.conf:/etc/telegraf/telegraf.conf:ro
      - ./monitoring/protobuf:/etc/telegraf/protos:ro
    environment:
      - HOST_PROC=/host/proc
      - HOST_SYS=/host/sys
    depends_on:
      influxdb:
        condition: service_healthy
```

**Startup ordering note:**
`depends_on` with health checks ensures the dependency is healthy before starting the dependent service. However, for the Flask dashboard, use `depends_on` without health conditions for NetBox and GitLab — the dashboard should start even if backends are temporarily slow, and handle unavailability at runtime with retry logic.

**Development override (`docker-compose.override.yml`):**
```yaml
services:
  dashboard:
    build: ./app
    volumes:
      - ./app:/app
    environment:
      - FLASK_DEBUG=1
      - FLASK_ENV=development

  gitlab:
    # Use faster image for dev; production uses full CE
    ports:
      - "8090:8090"  # Direct access for dev
```

The override file is `.gitignore`d and contains local-only settings.

**Resource limits:**
```yaml
  gitlab:
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: '2.0'
  netbox:
    deploy:
      resources:
        limits:
          memory: 1G
```

GitLab CE is resource-heavy (4GB RAM minimum). NetBox and Grafana are moderate. The Flask dashboard and ZTP server are lightweight (<256MB each).

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Kubernetes** | Over-engineered for a single-host, 8-service deployment. Compose handles service discovery via Docker network DNS, health checks, and logging without orchestration complexity. |
| **Bind mounts for all data** | Bind mounts require the host directory structure to exist and have correct permissions. Named volumes are self-managing and portable across hosts. |
| **Compose profiles for selective startup** | Docker Compose profiles (`profiles: ["monitoring"]`) allow starting subsets of services. Useful for development (e.g., `docker compose --profile monitoring up`). Recommended for optional services like the ZTP server. |
| **Docker Swarm** | Adds clustering capability but no value for single-host deployment. Swarm's service discovery and health checks are a subset of what Compose provides. |

### Specific Recommendations
- Use Docker Compose v2.24+ (included with Docker Desktop 4.27+)
- Pin all image tags to specific versions — never use `latest`
- Use Alpine-based images where available to reduce image size
- Configure `restart: unless-stopped` on all services for crash recovery
- Use a single Docker network (`driver: bridge`) for inter-service communication
- For backup automation, schedule `docker compose exec` commands via cron to tar named volumes
- Set `STOP_GRACE_PERIOD: 60s` for graceful shutdown of stateful services
- Use `docker compose up -d --remove-orphans` to clean up unused containers
- Add a `healthcheck` endpoint to the Flask dashboard (`/api/health`) that checks database connectivity and backend service reachability

---

## 9. Flask RBAC Implementation Patterns

### Decision
Custom decorator-based RBAC on top of Flask-Login, with a simple `Role` enum and `User.role` attribute. No Flask-Principal or third-party RBAC library.

### Rationale
**Why custom over Flask-Principal:**
The project has exactly 3 roles with a flat hierarchy (no role inheritance or permission matrices). Flask-Principal provides identity and permission abstractions that are unnecessary complexity:

```python
# app/models/user.py
from enum import Enum
from flask_login import UserMixin

class Role(Enum):
    VIEWER = 'viewer'
    EDITOR = 'editor'
    ADMIN = 'admin'

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum(Role), default=Role.VIEWER, nullable=False)

    # Role hierarchy checks
    def can_edit(self):
        return self.role in (Role.EDITOR, Role.ADMIN)

    def can_admin(self):
        return self.role == Role.ADMIN
```

**Decorator pattern:**
```python
# app/decorators.py
from functools import wraps
from flask import abort
from flask_login import current_user

def role_required(*roles):
    """Require current user to have one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator

# Convenience decorators
def viewer_required(f):
    return role_required(Role.VIEWER, Role.EDITOR, Role.ADMIN)(f)

def editor_required(f):
    return role_required(Role.EDITOR, Role.ADMIN)(f)

def admin_required(f):
    return role_required(Role.ADMIN)(f)
```

**Usage in blueprints:**
```python
# app/routes/profiles.py
@profiles_bp.route('/new', methods=['GET', 'POST'])
@login_required
@editor_required
def create_profile():
    # Only Editors and Admins can create profiles
    ...

@profiles_bp.route('/<int:id>/deploy')
@login_required
@admin_required
def deploy_profile(profile_id):
    # Only Admins can trigger deployments
    ...
```

**Role hierarchy enforcement:**
The hierarchy is: Admin > Editor > Viewer. The `role_required` decorator accepts a tuple of allowed roles. For hierarchical checks, pass all roles at or above the required level.

**UI rendering:**
Pass role information to templates for conditional UI rendering:
```jinja2
{# base.html sidebar #}
{% if current_user.can_edit() %}
  <li class="nav-item"><a class="nav-link" href="{{ url_for('profiles.list') }}">Profiles</a></li>
{% endif %}

{% if current_user.can_admin() %}
  <li class="nav-item"><a class="nav-link" href="{{ url_for('settings.index') }}">Settings</a></li>
{% endif %}
```

**Password hashing:**
Use Werkzeug's `generate_password_hash` and `check_password_hash` — already included with Flask:
```python
from werkzeug.security import generate_password_hash, check_password_hash

user.password_hash = generate_password_hash(password)
# Login:
check_password_hash(user.password_hash, form.password.data)
```

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Flask-Principal** | Provides signals-based identity and permission system. Over-engineered for 3 flat roles. The signal-based architecture adds indirection that makes debugging harder. |
| **Flask-Roleze** | Lightweight role management for Flask-Login. Adds a `RoleMixin` and role-checking helpers. Useful but introduces an extra dependency for a simple 3-role system. |
| **Flask-Security-Too** | Full-featured auth with registration, password reset, email confirmation, and RBAC. Massive scope for a 3-role internal tool. Adds database tables, email infrastructure, and configuration overhead. |
| **Casbin with Flask** | ABAC/RBAC enforcement engine with policy files. Excellent for complex permission matrices. Overkill for 3 roles with straightforward URL-based access control. |
| **Middleware-based auth** | A `before_request` hook on each blueprint could enforce roles. Decorators are more explicit and self-documenting at the route level. |

### Specific Recommendations
- Use the `Role` enum stored as a string in the database (SQLite-compatible)
- Implement `role_required` as a parameterized decorator accepting role tuples
- Add `current_user.can_edit()` and `current_user.can_admin()` convenience methods
- Use `@login_required` as the base decorator; role decorators assume authenticated user
- Return HTTP 403 with a Bootstrap-styled error page for unauthorized access
- Seed an initial Admin user via Flask CLI command (`flask create-admin`) on first startup
- Store password hashes with Werkzeug's `pbkdf2:sha256` method (default, 26 rounds)
- Log all role-check failures to the application log for audit trail
- For API endpoints (Grafana/NetBox proxying), enforce roles at the route level, not in the service layer

---

## 10. Modular Jinja2 Config Template Patterns

### Decision
Snippet-based template composition: each config section (VLANs, interfaces, routing, AAA, MDT) is a separate `.j2` file rendered independently and concatenated into a complete config payload. Ansible's `template` module renders each snippet, and a final task assembles them.

### Rationale
**Template structure:**
```
ansible/roles/iosxe/templates/
  aaa.j2           # AAA authentication, authorization, accounting
  mdt.j2           # Model-driven telemetry subscription
  netconf.j2       # NETCONF/YANG service configuration
  vlan.j2          # VLAN definitions and SVIs
  interfaces.j2    # Physical interface configurations
  routing.j2       # OSPF, BGP, static routes
  bootstrap.j2     # Day-0 minimal ZTP config
```

**Snippet rendering in Ansible:**
```yaml
# ansible/roles/iosxe/tasks/main.yml
- name: Render config snippets
  template:
    src: "{{ item }}"
    dest: "{{ role_path }}/templates/rendered/{{ inventory_hostname }}/{{ item | basename | regex_replace('\\.j2$', '.cfg') }}"
  loop: "{{ enabled_snippets | default(all_snippets) }}"
  vars:
    all_snippets:
      - aaa.j2
      - netconf.j2
      - mdt.j2
      - vlan.j2
      - interfaces.j2
      - routing.j2

- name: Assemble complete config
  assembly:
    snippets: "{{ lookup('fileglob', '{{ role_path }}/templates/rendered/{{ inventory_hostname }}/*.cfg') }}"
  # Custom assembly task or shell command to concatenate
```

**Variable precedence:**
The Ansible variable precedence order naturally handles the profile/override pattern:
1. `group_vars/all.yml` — global defaults (DNS, NTP, logging servers)
2. `group_vars/access_switches.yml` — profile-level defaults for the device role
3. `host_vars/sw-access-01.yml` — per-device overrides

```yaml
# group_vars/access_switches.yml (profile: access-switch)
vlan_id_data: 10
vlan_id_voice: 20
stp_mode: rapid-pvst
mdt_enabled: true
mdt_sample_interval: 10000

# host_vars/sw-access-01.yml (device override)
vlan_id_data: 100  # Override for this specific device
vlan_id_voice: 200
site_name: "Building-A"
```

**Interface template pattern:**
Interface configs are generated from a list of interface definitions in host_vars:

```jinja2
{# interfaces.j2 #}
{% for iface in managed_interfaces %}
interface {{ iface.name }}
{% if iface.description %} description {{ iface.description }}{% endif %}
{% if iface.mode == 'access' %}
 switchport mode access
 switchport access vlan {{ iface.access_vlan }}
{% elif iface.mode == 'trunk' %}
 switchport mode trunk
 switchport trunk allowed vlan {{ iface.trunk_vlans | default('all') }}
{% elif iface.mode == 'routed' %}
 no switchport
 ip address {{ iface.ip_address }} {{ iface.subnet_mask }}
{% endif %}
{% if iface.shutdown %} shutdown{% endif %}
!
{% endfor %}
```

```yaml
# host_vars/sw-access-01.yml
managed_interfaces:
  - name: GigabitEthernet1/0/1
    description: "UPLINK-to-DIST-01"
    mode: trunk
    trunk_vlans: "10,20,100,200"
  - name: GigabitEthernet1/0/2
    description: "UPLINK-to-DIST-02"
    mode: trunk
    trunk_vlans: "10,20,100,200"
  - name: GigabitEthernet1/0/3
    description: "ACCESS-Floor1-Data"
    mode: access
    access_vlan: 10
  - name: GigabitEthernet1/0/24
    description: "MGMT-Out-of-Band"
    mode: routed
    ip_address: "10.0.254.2"
    subnet_mask: "255.255.255.0"
```

**Snippet enable/disable control:**
Not all profiles need all snippets. Control which snippets are rendered via a profile-level variable:

```yaml
# group_vars/access_switches.yml
enabled_snippets:
  - aaa.j2
  - netconf.j2
  - mdt.j2
  - vlan.j2
  - interfaces.j2
  # routing.j2 not needed for access switches
```

**Jinja2 custom filters and tests:**
Register custom Jinja2 filters for common network operations:

```python
# ansible/roles/iosxe/filter_plugins/network_filters.py
def ip_to_cidr(ip, mask):
    """Convert IP and dotted mask to CIDR notation."""
    ...

def generate_range(start, end):
    """Generate a VLAN range string (e.g., '10-20,30,40-50')."""
    ...

class FilterModule:
    def filters(self):
        return {
            'ip_to_cidr': ip_to_cidr,
            'generate_range': generate_range,
        }
```

**Preview vs. deploy rendering:**
The same templates render in both modes. The Flask app invokes `ansible-playbook preview.yml --check --diff` as a subprocess for preview generation. This guarantees exact parity with the live deployment that runs `ansible-playbook site.yml` via the GitLab Runner. The `deploy_mode` variable controls behavior:
- `preview`: Ansible runs with `--check --diff`; rendered output captured and stored in DB
- `live`: Ansible executes NETCONF commits via the GitLab Runner job container

**Performance note:** Ansible subprocess preview takes ~5-10s per device. Acceptable for initial release. If user testing reveals unacceptable latency, alternatives include Flask-direct Jinja2 rendering (cached Ansible variable context) or a dedicated preview worker.

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Single monolithic template** | One `config.j2` file for the entire config. Hard to maintain, review, and reuse across device types. Every change requires reviewing the full config. |
| **Python-based config generation** | Build config in Python strings instead of Jinja2. Loses the visual template format that network engineers can read and validate. Ansible's native Jinja2 integration is superior. |
| **Nornir templating** | Nornir provides `template()` task with Jinja2. Would work but adds a Python framework layer when Ansible already handles templating, inventory, and execution. |
| **YANG-to-config compilers** | Tools like `pyangbind` generate config from YANG datastores. More type-safe but requires maintaining YANG bindings. Jinja2 is more flexible for IOS-XE's idiosyncratic config syntax. |
| **Ansible `assemble` module** | The `assemble` module concatenates fragment files from a source directory. Good for simple cases but doesn't support Jinja2 rendering of fragments. Our pattern renders each `.j2` to a `.cfg` then assembles. |

### Specific Recommendations
- Keep each snippet under 100 lines for readability and reviewability
- Use Jinja2 `{# comments #}` to document the purpose of each template section
- Store rendered configs in a temporary directory per device; clean up after deployment
- Add `strict_variables = True` in ansible.cfg to fail on undefined variables during rendering
- Use `ansible-playbook --check --diff` for config preview without device connection
- Implement a `config_lint` task that checks rendered output for common issues (duplicate VLANs, conflicting descriptions)
- For interface templates, use device type data from NetBox to pre-populate the interface list
- Support `{% include %}` for cross-template composition (e.g., `interfaces.j2` includes `interface_port_security.j2` fragment)
- Maintain a `defaults/main.yml` with sensible defaults for every variable used in templates

---

## 11. GitLab CE Resource-Constrained Configuration

### Decision
Run GitLab CE with aggressive resource tuning via `GITLAB_OMNIBUS_CONFIG` to operate within ~2GB RAM and ~10GB disk. Disable all monitoring components, reduce Puma to single-process mode, limit Sidekiq concurrency, and tune PostgreSQL/Redis memory.

### Rationale
GitLab CE defaults to 4GB+ RAM and 20GB+ disk. The official GitLab documentation confirms successful operation on 2GB RAM with 1GB swap. For a single-admin + editor team of 3-10 users managing config repositories (not application code), the reduced configuration is sufficient.

**`GITLAB_OMNIBUS_CONFIG` for ~2GB target:**
```ruby
external_url 'http://gitlab.stackhive.local'

# Puma: single-process mode (saves 100-400MB)
puma['worker_processes'] = 0
puma['min_threads'] = 1
puma['max_threads'] = 1
puma['exporter_enabled'] = false

# Sidekiq: reduce concurrency (saves ~100MB)
sidekiq['concurrency'] = 5
sidekiq['metrics_enabled'] = false

# PostgreSQL: reduce shared buffers (saves ~500MB)
postgresql['shared_buffers'] = '256MB'
postgresql['max_connections'] = 100

# Redis: cap memory
redis['maxmemory'] = '256mb'
redis['maxmemory_policy'] = 'allkeys-lru'

# Disable all monitoring (saves ~300MB)
prometheus_monitoring['enable'] = false
prometheus['enable'] = false
alertmanager['enable'] = false
gitlab_exporter['enable'] = false
node_exporter['enable'] = false
postgres_exporter['enable'] = false
redis_exporter['enable'] = false

# Disable unused components
registry['enable'] = false
gitlab_kas['enable'] = false

# CI/CD artifact retention
gitlab_rails['artifacts_keep_hours'] = 168  # 7 days
```

**Runner configuration:**
The GitLab Runner uses Docker executor with socket mount. The runner spawns sibling containers for each job, with the `ansible/` directory bind-mounted for template access:
```yaml
# docker-compose.yml excerpt
gitlab-runner:
  image: gitlab/gitlab-runner:v17.0
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - ./ansible:/ansible:ro        # Templates, roles, vars (read-only for jobs)
    - runner-config:/etc/gitlab-runner
  network_mode: host                # Direct access to managed devices
```

**Expected resource usage:**
- **RAM**: ~1.5-1.7GB of 2GB allocated (requires 1GB host swap)
- **Disk**: ~10GB base + repositories (vs 20GB+ default)
- **Trade-offs**: Slower concurrent request handling; background jobs queue at lower concurrency

### Alternatives Considered

| Alternative | Evaluation |
|---|---|
| **Full GitLab (4GB+)** | Default config provides better performance but exceeds resource budget for initial deployment. |
| **Gitea as alternative** | Lightweight Git server (~50MB RAM) but lacks built-in CI/CD pipeline with manual approval gates. Would require external CI runner. |
| **External GitLab (gitlab.com)** | Free tier available but exposes config data to external host. Violates on-premise requirement. |

### Specific Recommendations
- Set `shm_size: '256m'` on the GitLab container (required for proper operation)
- Configure 1GB swap on the host machine before starting GitLab
- Set `vm.swappiness=10` on the host for optimal memory management
- Document minimum server requirements: 8GB RAM total (2GB GitLab, 1GB NetBox, 1GB Grafana/Influx, 256MB dashboard, 256MB Traefik/Telegraf, remainder for Ansible job containers)
- Monitor disk usage; implement automated artifact cleanup via GitLab admin settings
- Use `docker-compose.override.yml` to restore full GitLab resources for staging/production

---

## Summary of Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Flask dashboard | Flask 3.x + Flask-SQLAlchemy + Flask-Login + Bootstrap 5 + HTMX 2.x |
| 2 | Database | SQLite initial, Alembic migrations, `DATABASE_URL` switch to PostgreSQL |
| 3 | NETCONF commits | `ncclient` with `<merge>` to candidate datastore, atomic `<commit>`, capability discovery |
| 4 | NetBox inventory | `netbox_community.netbox_netbox` plugin with role-based groups and compose mapping |
| 5 | GitLab CI/CD | MR pipelines with validate → preview → manual approve → deploy → verify stages |
| 6 | Traefik routing | `PathPrefix()` labels, priority ordering, StripPrefix for NetBox, subdomain for GitLab |
| 7 | MDT telemetry | Dial-out gRPC with protobuf to Telegraf `grpc_listener_v2`, InfluxDB storage |
| 8 | Docker Compose | Named volumes, `depends_on` with health checks, resource limits, override for dev |
| 9 | RBAC | Custom `role_required` decorator on Flask-Login, 3-role enum, no Flask-Principal |
| 10 | Jinja2 templates | Snippet-based `.j2` files, Ansible variable precedence for profile/override, preview mode |
| 11 | GitLab resource tuning | Omnibus config: Puma single-mode, Sidekiq=5, PG shared_buffers=256MB, all monitoring disabled |
