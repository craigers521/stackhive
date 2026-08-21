# Implementation Plan: Network Operations Dashboard

**Branch**: `001-network-ops-dashboard` | **Date**: 2025-07-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-network-ops-dashboard/spec.md`

## Summary

A containerized network operations dashboard providing a unified web UI for device inventory, configuration management via Ansible/NETCONF, monitoring via TIG stack, and ZTP-based device onboarding. The platform integrates GitLab CE for version control and CI/CD, NetBox for inventory management, and Traefik as a reverse proxy. The web UI uses Flask with Bootstrap and minimal JavaScript, enforcing a persistent sidebar navigation pattern. Configuration profiles map to Ansible group_vars/host_vars with Jinja2 templates rendered by a single Ansible role.

## Technical Context

**Language/Version**: Python 3.12+ (Flask 3.x for web UI, Ansible 2.20+ for automation)

**Primary Dependencies**:
- Flask 3.x with Flask-SQLAlchemy, Flask-Login, Flask-WTF
- Bootstrap 5 (CSS-only, minimal JS per FR-018)
- HTMX 2.x (~24 KB; the single client-side JS dependency used for progressive enhancement)
- Flask-Caching (in-memory backend; 5-minute TTL for NetBox/Grafana queries)
- ansible-core 2.20+ with cisco.ios, ansible.netcommon collections
- Jinja2 (Ansible native templating)
- netmiko/napalm for device connectivity (fallback)
- requests for REST API integrations (NetBox, GitLab, Grafana, Meraki)
- Python Meraki SDK (cisco.meraki collection)

**Storage**:
- SQLite (initial deployment) via Flask-SQLAlchemy for dashboard data
- Service credential tokens stored in DB (encrypted) with `.env` fallback
- InfluxDB for telemetry data (MDT, host metrics, container metrics)
- NetBox PostgreSQL for inventory data (separate compose, included via `include`)
- Persistent Docker volumes for all stateful services
- Bind mount of `ansible/` directory shared between Flask app and GitLab runner containers

**Testing**: pytest with pytest-flask, pytest-cov; Ansible Molecule for role testing

**Target Platform**: Linux server running Docker Compose (Linux/WSL2/macOS for development); on-premise deployment

**Project Type**: Web application with integrated infrastructure services (monorepo docker-compose deployment)

**Performance Goals**:
- Dashboard page load < 2s for inventory of up to 500 devices (independent NFR — SC-001 measures clicks, not latency)
- Configuration preview generation < 30s per device (supports SC-003: deploy with visible status feedback within 2 minutes)
- Deployment to single device < 2 minutes (SC-003)
- NetBox inventory sync < 10s for full refresh (independent NFR — inventory freshness UX)

**Constraints**:
- Minimal client-side JavaScript (FR-018: server-rendered Bootstrap; HTMX 2.x at ~24 KB is the sole JS dependency)
- Atomic NETCONF commits only (FR-010: no partial config). All v1 managed devices must support NETCONF/YANG (spec assumption); non-NETCONF devices are rejected at deployment (422) — netmiko/napalm SSH fallback is reserved for the vendor-agnostic phase
- Zero external SSO dependency for initial release (local auth only)
- Single compose file deployment (SC-008)
- Credentials are never committed to version control or stored in plaintext in the config repo. Initial bootstrap uses environment variables or a gitignored `.env`; at runtime tokens resolve from the encrypted (Fernet) DB credential store, with `.env` as bootstrap fallback
- WCAG 2.1 AA baseline: keyboard operability, semantic landmarks, labeled controls, 4.5:1 contrast (Bootstrap 5 defaults cover most)
- Timestamps are UTC (ISO 8601) and the UI is English-only in v1 (no localization)
- HTTP over the trusted LAN is acceptable in v1; Traefik TLS termination (self-signed or ACME resolver) is supported but not required for the initial release

**Scale/Scope**: Initial deployment targets 50-500 managed devices; single-admin + editor team of 3-10 users; three RBAC roles

**Resource Requirements**:
- **RAM**: Minimum 8 GB (GitLab CE 2GB tuned, NetBox 1GB, Grafana/InfluxDB 1GB, dashboard/traefik/telegraf 512MB, remainder for Ansible job containers)
- **Swap**: 1 GB required on host (GitLab CE)
- **Disk**: 40 GB minimum, 100 GB+ recommended for sustained 500-device telemetry (GitLab ~10GB, NetBox ~2GB, image layers ~5GB, InfluxDB ~20GB+ with 14-day retention)
- **Network**: Runner container has direct access to managed device network (NETCONF port 830, SSH port 22)

## Constitution Check

*GATE: No formal constitution file found. Proceeding with self-assessed gates.*

**Self-Assessed Gates**:
- **Simplicity**: Server-rendered Flask with Bootstrap avoids frontend complexity. No SPA, no build pipeline for UI.
- **Security**: Credentials via environment variables; generated configs git-ignored; NETCONF atomic commits prevent partial state.
- **Supportability**: Persistent sidebar, stacked sections, collapsible panels — designed for operators on large monitors.
- **Vendor Agnostropy**: Template architecture uses device-type-abstracted Jinja2; Ansible roles structured per vendor pattern.

**Gate Status**: PASS — all principles aligned with spec requirements.

## Project Structure

### Documentation (this feature)

```
specs/001-network-ops-dashboard/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```
stackhive/
├── docker-compose.yml              # Main compose: dashboard, runner, TIG, traefik
├── docker-compose.override.yml     # Local dev overrides (gitignored)
├── docker-compose.netbox.yml       # NetBox compose (included via docker-compose.yml `include`)
├── .env.example                    # Environment variable template with descriptions
├── .gitignore
│
├── app/                            # Flask dashboard application
│   ├── Dockerfile                  # Multi-stage: builder → gunicorn production
│   ├── requirements.txt            # Python dependencies (Flask, ncclient, requests, etc.)
│   ├── __init__.py                 # App factory with create_app()
│   ├── config.py                   # Env var mapping: .env → app config → DB credential store
│   ├── extensions.py               # DB, login_manager, cache, migrate init
│   ├── cli.py                      # Flask CLI commands (create-admin, sync-inventory, etc.)
│   ├── models/                     # SQLAlchemy models
│   │   ├── device.py
│   │   ├── profile.py
│   │   ├── deployment.py
│   │   ├── user.py
│   │   ├── device_type.py
│   │   └── credential.py           # ServiceCredential: encrypted tokens for backends
│   ├── routes/                     # Blueprints
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── inventory.py
│   │   ├── profiles.py
│   │   ├── deployments.py
│   │   ├── monitoring.py
│   │   ├── onboarding.py
│   │   ├── settings.py
│   │   └── ztp.py                  # ZTP blueprint: serves /ztp/{serial}.{txt,cfg} unauthenticated
│   ├── services/                   # Business logic
│   │   ├── netbox.py               # NetBox API client
│   │   ├── gitlab.py               # GitLab API client
│   │   ├── grafana.py              # Grafana API client
│   │   ├── ansible.py              # Ansible subprocess wrapper for preview/deploy
│   │   ├── meraki.py               # Meraki API client
│   │   └── credential.py           # Token rotation, encryption, .env fallback
│   ├── templates/                  # Jinja2 HTML templates (Bootstrap)
│   │   ├── base.html               # Base layout with sidebar
│   │   ├── components/             # Reusable template fragments
│   │   ├── dashboard/
│   │   ├── inventory/
│   │   ├── profiles/
│   │   ├── deployments/
│   │   ├── monitoring/
│   │   ├── onboarding/
│   │   ├── settings/
│   │   └── ztp/                    # ZTP script templates (parameterized ztp_script.j2)
│   ├── static/                     # Minimal CSS overrides
│   │   └── css/
│   └── tests/
│       ├── conftest.py
│       ├── test_inventory.py
│       ├── test_profiles.py
│       ├── test_deployments.py
│       └── test_ansible_preview.py # Verify Ansible subprocess rendering
│
├── ansible/                        # Ansible automation — bind-mounted to app + runner
│   ├── ansible.cfg
│   ├── collections/
│   │   └── requirements.yml        # Pin: netbox_community, cisco.ios, ansible.netcommon
│   ├── site.yml                    # Main playbook: apply config profiles via NETCONF
│   ├── preview.yml                 # Dry-run playbook: --check --diff for preview rendering
│   ├── ztp.yml                     # Minimal ZTP bootstrap playbook (constrained task set)
│   ├── verify.yml                  # Post-deploy verification: get-config vs rendered
│   ├── inventory/
│   │   └── netbox.yml              # NetBox dynamic inventory plugin config
│   ├── group_vars/                 # Profile-level defaults (source-controlled)
│   │   └── *.yml
│   ├── host_vars/                  # Per-device overrides (source-controlled)
│   │   └── *.yml
│   ├── roles/
│   │   └── iosxe/                  # Single role for IOS-XE devices
│   │       ├── tasks/
│   │       │   └── main.yml        # Common boilerplate + snippet assembly
│   │       ├── defaults/
│   │       │   └── main.yml        # Default variables (DNS, NTP, etc.)
│   │       ├── templates/          # Jinja2 config templates
│   │       │   ├── aaa.j2
│   │       │   ├── mdt.j2          # Model-driven telemetry subscription
│   │       │   ├── interfaces.j2
│   │       │   ├── vlan.j2
│   │       │   ├── routing.j2
│   │       │   ├── netconf.j2
│   │       │   └── bootstrap.j2    # Day-0 minimal config (ZTP)
│   │       ├── filter_plugins/     # Custom Jinja2 filters (ip_to_cidr, etc.)
│   │       │   └── network_filters.py
│   │       └── meta/
│   │           └── main.yml
│   └── tests/
│       └── molecule/               # Molecule test scenarios
│
├── gitlab-ci/                      # CI/CD pipeline (committed in ansible repo)
│   └── .gitlab-ci.yml              # Pipeline: validate → preview → approve → deploy → verify
│
├── monitoring/                     # TIG stack configuration
│   ├── telegraf/
│   │   └── telegraf.conf           # Inputs: docker, procfs, kernel, grpc_listener_v2
│   ├── grafana/
│   │   ├── dashboards/             # Pre-configured dashboard JSON
│   │   │   ├── device-health.json
│   │   │   ├── infrastructure.json
│   │   │   └── interfaces.json
│   │   └── provisioning/           # Auto-provision datasources/dashboards
│   │       ├── datasources.yml     # InfluxDB datasource
│   │       └── dashboards.yml      # Dashboard folder provisioning
│   └── protobuf/                   # Cisco IOS-XE gRPC protobuf schemas
│       └── cisco_ios_xe/
│
├── traefik/                        # Traefik configuration
│   ├── traefik.yml                 # Static: providers, entrypoints, API, log level
│   └── dynamic.yml                 # Dynamic: middlewares, catch-all routers
│
└── scripts/                        # Utility scripts
    ├── sync-inventory.sh           # Manual NetBox sync trigger
    └── generate-host-vars.py       # CLI helper for host_vars generation
```

**Structure Decisions**:

1. **Monorepo with bind-mounted ansible/** — The `ansible/` directory is bind-mounted into both the Flask dashboard container and the GitLab runner containers. This means template edits, playbook changes, and variable updates are immediately visible to both the preview renderer and the CI/CD pipeline. All Ansible artifacts (templates, roles, vars, playbooks) are source-controlled in the monorepo.

2. **Ansible execution via GitLab Runner** — Config deployments execute inside Docker containers spawned by the GitLab Runner (Docker executor). The runner mounts the Docker socket (`/var/run/docker.sock`) and has direct network access to managed devices. The `ansible/` bind mount ensures the runner sees the same templates and variables as the Flask app.

3. **Preview via Ansible subprocess** — The Flask app invokes the preview-mode playbook as a subprocess using the same playbooks, roles, and variables that the runner uses. The "configuration preview" artifact is the **full rendered configuration** (assembled snippets with merged variables) — the same artifact class that deployment pushes — which guarantees input parity between preview and deployment. Rendered output is stored in the DB for reference, and the post-deploy `verify.yml` (NETCONF `get-config` read-back compared against the render) confirms realized parity. Ansible collections are pinned in `ansible/collections/requirements.yml` so the app container and runner jobs render identically. Performance (~5-10s per device) is acceptable for initial release.

4. **ZTP as integrated blueprint** — ZTP routes (`/ztp/{serial}.{txt,cfg}`) are served by the main Flask app without authentication. The ZTP blueprint shares the same `ansible/` templates and variable store as the rest of the application, rendering day-0 configs via the `ztp.yml` playbook.

5. **NetBox in separate compose** — NetBox runs in its own `docker-compose.netbox.yml` (netbox, netbox-worker, netbox-postgres, netbox-redis). The main compose includes it via the `include` directive for single-command startup while maintaining clean separation.

6. **Credential storage** — Backend service tokens (NetBox, GitLab, Grafana, Meraki) are stored encrypted in the `ServiceCredential` DB model. The Settings UI allows Admins to rotate tokens. `.env` values serve as fallback for initial bootstrapping before the DB is populated.

7. **Telegraf monitoring scope** — Telegraf collects host metrics (via `/proc` and `/sys` mounts), container metrics (via Docker socket), and device telemetry (via `grpc_listener_v2` for MDT dial-out from IOS-XE devices).

8. **Docker networking** — All services run on the `stackhive` bridge network for DNS-based service discovery. The GitLab Runner uses `network_mode: host` for direct reachability to managed devices on the network.

9. **Credential resolution** — Service tokens are stored encrypted in the `ServiceCredential` DB model (Fernet encryption). The Settings UI allows Admins to rotate tokens. `.env` values serve as fallback for initial bootstrapping. Encryption key sourced from `ENCRYPTION_KEY` environment variable.

## Non-Functional Decisions

- **Backup & recovery**: a daily cron job tars all named volumes (`stackhive-db`, `gitlab-data`, `gitlab-etc`, `gitlab-logs`, `influxdb-data`, `grafana-data`, `traefik-acme`, `runner-config`) into `backups/`; retention is 7 daily + 4 weekly. Recovery = recreate empty volume, untar latest archive, `docker compose up -d`. GitLab CE's built-in rake backups provide a secondary safety net for GitLab data.
- **Audit & logging**: configuration changes are audited by Git itself (auto-commits with author + descriptive message; full history and diffs in GitLab). Auth and administrative actions (login success/failure, user management, settings changes, credential rotation, deployment approvals) are emitted as structured JSON logs to stdout (Docker log-driver rotation). Role-check failures are logged (Research §9).
- **Empty-state UX**: every list/dashboard view renders a friendly empty state with a next-action CTA (Inventory: "Add devices in NetBox, then run inventory sync"; Profiles: "Create your first profile"; Deployments: "No deployments yet"; Onboarding: "Pick a device to start ZTP"). No broken layouts at zero data.
- **Drift detection (`modified` status)**: on-demand per-device check (`POST /api/devices/{id}/drift-check`, Editor+) plus a nightly background job over deployed devices (`drift_check_enabled` setting, default on, 02:00 local) — see Data Model §1.
- **ZTP failure & cleanup**: no server-side auto-fail timeout (Cisco loader retries); fetch attempts are logged; operators mark provisions `failed`/`cancelled`. A daily job removes served artifacts for terminal-state provisions older than 30 days — see Data Model §8 and the ZTP contract.
- **Telemetry sizing**: InfluxDB bucket retention defaults to 14 days (configurable via `influxdb_retention_days`); 10s counter / 30s CPU-memory intervals with `suppress-redundancy` on. A sizing pilot (Quickstart Scenario 9) measures real InfluxDB growth before GA.

## Complexity Tracking

No unjustified complexity violations identified. The architecture follows the spec's requirements directly without introducing unnecessary patterns.
