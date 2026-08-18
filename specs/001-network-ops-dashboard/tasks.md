# Tasks: Network Operations Dashboard

**Input**: Design documents from `/specs/001-network-ops-dashboard/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Included — plan.md explicitly specifies the test stack (pytest, pytest-flask, pytest-cov) and Ansible Molecule for role testing, and the planned structure includes `app/tests/` and `ansible/tests/molecule/`.

**Organization**: Tasks are grouped by user story (P1 → P2 → P3) to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths from the project structure in plan.md

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project directory structure per plan.md at repo root: `app/` (models, routes, services, templates, static, tests), `ansible/` (inventory, group_vars, host_vars, roles, tests), `gitlab-ci/`, `monitoring/` (telegraf, grafana, protobuf), `traefik/`, `scripts/`
- [ ] T002 Initialize Flask app factory in `app/__init__.py` with `create_app()` (blueprint registration, extension init, error handler wiring) and `app/config.py` mapping env vars (SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL default `sqlite:////var/lib/stackhive/stackhive.db`) to app config with SQLALCHEMY_TRACK_MODIFICATIONS=False
- [ ] T003 [P] Write pinned `app/requirements.txt` (Flask 3.x, Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Migrate, requests, cryptography, ncclient, gunicorn, psycopg2-binary dev, pytest, pytest-flask, pytest-cov) and multi-stage `app/Dockerfile` (python:3.12-slim builder → gunicorn production, non-root user)
- [ ] T004 [P] Write `docker-compose.yml` (services: app, traefik, gitlab, gitlab-runner, telegraf, influxdb, grafana; `include: docker-compose.netbox.yml`; named volumes stackhive-db, gitlab-data, gitlab-etc, gitlab-logs, influxdb-data, grafana-data, runner-config; `stackhive` bridge network; bind mounts `./ansible:/app/ansible:rw` for app and `./ansible:/ansible:ro` for runner; health checks; resource limits; `restart: unless-stopped`) and `docker-compose.netbox.yml` (netbox, netbox-worker, netbox-postgres with healthcheck, netbox-redis with healthcheck)
- [ ] T005 [P] Create `.env.example` with documented descriptions for all variables (SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL, NETBOX_URL, NETBOX_TOKEN, GITLAB_URL, GITLAB_ROOT_PASSWORD, GITLAB_TOKEN, GITLAB_SHARED_SECRET, GRAFANA_URL, GRAFANA_ADMIN_PASSWORD, GRAFANA_TOKEN, INFLUXDB_TOKEN, INFLUXDB_ORG, INFLUXDB_BUCKET, MERAKI_API_KEY, MERAKI_DASHBOARD_URL, ANSIBLE_NETCONF_USER, ANSIBLE_NETCONF_PASSWORD, ANSIBLE_ENABLE_PASSWORD) and update `.gitignore` for `.env`, `docker-compose.override.yml`, generated configs
- [ ] T006 [P] Write `traefik/traefik.yml` (Docker provider on `stackhive` network, web/websecure entrypoints, API dashboard, log level) and `traefik/dynamic.yml` (StripPrefix middleware for netbox and ztp, catch-all routers with priority: dashboard=1, proxied services=10+, ztp-public=15)
- [ ] T007 [P] Write `monitoring/telegraf/telegraf.conf` with inputs `grpc_listener_v2` (Cisco IOS-XE descriptors from `/etc/telegraf/protos/cisco_ios_xe/*.proto`), docker (container_name_include stackhive-*), cpu, mem, disk, diskio, system, net and `outputs.influxdb_v2` (influxdb:8086, org stackhive, bucket telemetry, `$INFLUX_TOKEN`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T008 Create all SQLAlchemy models in `app/models/user.py` (User with Role enum viewer/editor/admin, permission helpers can_edit/can_admin, permission matrix from data-model.md section 11), `app/models/device_type.py` (DeviceType with interface_types JSON, interface_count, slot_config, uplink_slots, management_interfaces, unique manufacturer+model), `app/models/device.py` (Device with netbox_id unique, config_status state machine, monitoring_status, cloud_managed, last_netbox_sync; DeviceType FK), `app/models/profile.py` (ConfigurationProfile, ConfigurationTemplate, InterfaceTemplate, ConfigurationVariable with scope profile/device), `app/models/deployment.py` (DeploymentRecord immutable, status transitions, approved_by), `app/models/credential.py` (ServiceCredential) — including all indexes from data-model.md "Indexes" section
- [ ] T009 Create initial Alembic migration in `app/alembic/` via `flask db init` and `flask db migrate -m "initial schema"` covering all 11 entities from T008
- [ ] T010 [P] Implement Flask-Login integration in `app/extensions.py` (db, migrate, login_manager init) and `app/decorators.py` (`role_required(*roles)` parameterized decorator returning 401/403, plus `viewer_required`, `editor_required`, `admin_required` convenience decorators per research.md section 9)
- [ ] T011 [P] Implement auth blueprint `app/routes/auth.py` (login page GET/POST, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` per contracts/rest-api.md) and `app/templates/auth/login.html` with Bootstrap form and Werkzeug password verification
- [ ] T012 [P] Implement CLI commands in `app/cli.py`: `create-admin` (username, email, password, role), `create-user`, `seed-credentials` (upsert netbox/gitlab/grafana/meraki tokens Fernet-encrypted into ServiceCredential)
- [ ] T013 [P] Implement service credential resolution in `app/services/credential.py`: Fernet encrypt/decrypt with ENCRYPTION_KEY env var, resolution order active-DB-credential → .env fallback → error, and `redact()` helper for token display
- [ ] T014 [P] Implement base layout `app/templates/base.html` with persistent left sidebar (Bootstrap grid, position:sticky, zero JS) containing always-visible sections Inventory, Profiles, Deployments, Monitoring, Onboarding, Settings plus Tools sub-menu with new-tab (target="_blank") cross-launch links to Grafana, NetBox, GitLab; add `app/static/css/overrides.css`
- [ ] T015 [P] Implement global error handlers (HTML Bootstrap error pages and JSON `{error, details}` format per contracts/rest-api.md "Global Error Response Format") in `app/__init__.py` with `app/templates/errors/403.html`, `404.html`, `500.html`, `503.html`, and `GET /api/health` endpoint checking DB connectivity and backend reachability
- [ ] T016 Configure logging in `app/config.py` (structured console logging, per-blueprint loggers) and add audit logging of all RBAC check failures to the application log

**Checkpoint**: Foundation ready — app boots, login works, RBAC enforced, all models migrated, sidebar layout renders. User story implementation can now begin.

---

## Phase 3: User Story 1 — Browse and Manage Device Inventory (Priority: P1) 🎯 MVP

**Goal**: Single view of all managed devices with attributes, filtering, search, drill-down to stacked-section detail pages, and the Dashboard overview landing page with summary cards.

**Independent Test**: Load the device list page and verify devices appear with hostname, role, status, IP; filter by role; click a device and verify the detail page shows metadata, interfaces by type, and config status. Verify Dashboard overview shows health cards, recent deployments, pending approvals.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T017 [P] [US1] Write `app/tests/conftest.py` (app factory fixture with in-memory SQLite, seeded Device/DeviceType/User test data, login helper) and `app/tests/test_inventory.py` (list page renders 200 with seeded devices, filter by role/site/status/search works, pagination, detail page 200 with interfaces, 503 when NetBox client raises unavailable)
- [ ] T018 [P] [US1] Write NetBox client unit tests in `app/tests/test_netbox_client.py` (mocked requests for `/api/dcim/devices/`, `/api/dcim/devices/{id}/interfaces/`, `/api/dcim/device-types/`, `/api/dcim/device-roles/`, `/api/dcim/sites/` verifying field mapping and 401/403/5xx → 503 handling per contracts/netbox-api.md)

### Implementation for User Story 1

- [ ] T019 [US1] Implement NetBox REST API client in `app/services/netbox.py` (Token auth, device list with role/type/site/status/tag filters and pagination, device detail with nested refs, interfaces, device-types with `includes=interfaces`, device-roles, sites; 5-minute cache via Flask-Caching; error mapping per contracts/netbox-api.md)
- [ ] T020 [US1] Implement inventory sync logic invoked from `sync-inventory` command in `app/cli.py` (pull via T019 client, upsert Device records matched on netbox_id, populate DeviceType records with interface_types JSON, set last_netbox_sync, flag devices missing from NetBox as stale) and a periodic refresh thread at `refresh_interval`
- [ ] T021 [P] [US1] Implement inventory blueprint in `app/routes/inventory.py` (GET `/inventory` list with query param filters role/type/site/status/search and pagination 50/page) and `app/templates/inventory/list.html` (Bootstrap table: hostname, role badge, status up/down/unknown, mgmt IP, site, device type; filter controls; 503 banner when inventory source unavailable)
- [ ] T022 [US1] Implement device detail route in `app/routes/inventory.py` and `app/templates/inventory/detail.html` with stacked collapsible Bootstrap sections: metadata (serial, MAC, role, site, cloud_managed flag, config_status), physical interfaces organized by type (management, uplink, access) from DeviceType + NetBox interfaces, monitoring status section with Grafana deep-link, deployment history section
- [ ] T023 [P] [US1] Implement Dashboard overview in `app/routes/dashboard.py` (GET `/` as post-login landing page, redirect to login if unauthenticated) and `app/templates/dashboard/index.html` with summary cards: device health snapshot (up/down/total counting from config_status and monitoring_status), last 5 recent deployments, pending approval items per FR-018c/FR-018d

**Checkpoint**: User Story 1 fully functional — inventory browsable, filterable, detail pages work, dashboard overview renders. MVP demo ready.

---

## Phase 4: User Story 2 — Create and Manage Configuration Profiles (Priority: P1)

**Goal**: Create/edit profiles bound to device roles with modular Jinja2 template sections, interface templates driven by device type definitions, and variables persisted as static YAML (group_vars / host_vars) auto-committed to Git.

**Independent Test**: Create a new profile for a device role, add template sections and variables, define an interface template (system presents correct interface count/type from device type), associate role, and verify the profile appears in the list with correct metadata and YAML files exist in the working branch.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T024 [P] [US2] Write `app/tests/test_profiles.py` (create/edit/delete profile, template + variable persistence, Jinja2 syntax validation rejects bad content, name 409 conflict, viewer gets 403 on create/edit, device-scope override variable overrides profile variable on collision)
- [ ] T025 [P] [US2] Write GitLab client unit tests in `app/tests/test_gitlab_client.py` (mocked: commit files to working branch, push rejected → rebase, rebase conflict → 409, create MR working→main, merge MR, pipeline/job status lookup per contracts/gitlab-api.md)

### Implementation for User Story 2

- [ ] T026 [US2] Implement GitLab integration service in `app/services/gitlab.py` (local repo ops + GitLab REST with PRIVATE-TOKEN: write group_vars/host_vars YAML files via T013 credential, auto-commit to working branch with descriptive messages `profile: update <name> — <summary>` / `override: update <host> — <summary>`, push with rebase and 409 on merge conflict, create MR `Deploy <profile> to <devices>` with device list + diff preview in description, merge MR, poll pipeline/job status)
- [ ] T027 [US2] Implement profiles blueprint in `app/routes/profiles.py` (GET list with role/search filters, GET detail with templates/variables/interface mappings, POST create with name+device_role+initial templates+variables, PUT edit with full replacement of templates/variables/interface_mappings and optimistic locking on ConfigurationProfile.version, DELETE) with `@editor_required` on write routes
- [ ] T028 [US2] Implement Git-backed variable persistence behind the profiles blueprint: profile-scope variables → `group_vars/<profile_name>/vars.yml`, device-scope variables → `host_vars/<hostname>.yml` (YAML serialization respecting value_type string/int/bool/list/dict), auto-commit via T026, update profile `version` to commit SHA
- [ ] T029 [P] [US2] Implement profile UI templates `app/templates/profiles/list.html` (name, device_role, template/variable counts, updated_by), `app/templates/profiles/form.html` (name, device_role select, template section rows with Jinja2 editor + order, variable rows with key/value/type, interface mapping rows), `app/templates/profiles/detail.html` (all associated templates, variables, target roles; edit/delete actions)
- [ ] T030 [US2] Implement interface template editor in `app/routes/profiles.py` + `app/templates/profiles/` fragments: resolve DeviceType.interface_types for the profile's device role and present the correct count/type of physical interfaces per FR-006 (interface_range validation against device type count, `all` keyword); surface a clear error when the profile references a device type with no known interface definition (spec edge case)
- [ ] T031 [US2] Implement per-device override editor (FR-004b) as a section on the device detail page (links from `app/templates/inventory/detail.html`) that edits device-scope variables via the profiles service and persists to `host_vars/<hostname>.yml` via T028

**Checkpoint**: User Stories 1 AND 2 both work independently — inventory browsable; profiles authored end-to-end with YAML in Git and auto-commits.

---

## Phase 5: User Story 3 — Deploy Configuration Changes (Priority: P2)

**Goal**: Preview rendered config per device, auto-commit + MR with admin approval gate, CI/CD pipeline execution, atomic NETCONF merge commits, immutable deployment history with pipeline status visible in the UI.

**Independent Test**: Select a device, request a preview of a profile (rendered with overrides taking precedence), confirm the deployment, and verify the deployment record tracks the full lifecycle (commit → MR → approval → pipeline → deployed/failed) with no partial config on failure.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T032 [P] [US3] Write `app/tests/test_deployments.py` (preview endpoint returns rendered snippets + variables_used, deployment initiation creates record and MR (mocked), cloud_managed device → 409, approval is admin-only (editor → 403), webhook with valid/invalid X-GitLab-Token, DeploymentRecord immutability, Device.config_status transitions pending/deployed → deployed/failed)
- [ ] T033 [P] [US3] Write `app/tests/test_ansible_preview.py` (subprocess preview against a mock static inventory file renders expected config snippets, host_vars override value wins over group_vars, output stored on DeploymentRecord.preview_output)
- [ ] T034 [P] [US3] Write Molecule scenario in `ansible/tests/molecule/` (converge + verify for the iosxe role using a mock IOS-XE host: renders complete config from snippet templates, respects enabled_snippets, interface loop produces one block per managed interface)

### Implementation for User Story 3

- [ ] T035 [US3] Implement the iosxe Ansible role: `ansible/roles/iosxe/tasks/main.yml` (render enabled_snippets loop, assemble snippets, apply config via `cisco.ios.ios_config` with `ansible_connection: netconf` using merge semantics + candidate datastore atomic commit/rollback per research.md section 3), `defaults/main.yml` (DNS, NTP, default_sensor_paths, all default vars), `meta/main.yml`, `templates/aaa.j2`, `mdt.j2` (MDT dial-out per research.md section 7), `netconf.j2`, `vlan.j2`, `interfaces.j2` (managed_interfaces loop per research.md section 10), `routing.j2`, `bootstrap.j2` (day-0 minimal config), `filter_plugins/network_filters.py` (ip_to_cidr, generate_range)
- [ ] T036 [P] [US3] Implement playbooks and inventory: `ansible/site.yml` (live deploy, `deploy_mode=live`), `ansible/preview.yml` (dry-run `--check --diff` render, `deploy_mode=preview`), `ansible/verify.yml` (NETCONF `<get-config running>` read-back compared against rendered config), `ansible/inventory/netbox.yml` (netbox_community.netbox plugin config per research.md section 4: role groups, keyed tag groups, compose for ansible_host/netconf vars), `ansible/ansible.cfg` (inventory plugin path, strict_variables)
- [ ] T037 [US3] Implement Ansible runner service in `app/services/ansible.py` (subprocess `ansible-playbook preview.yml --check --diff -i inventory/netbox.yml --limit <host>`, capture rendered config + per-snippet diff, enforce preview timeout 30s, store preview_output/config_diff on the record; same playbooks/vars guarantee preview↔deploy parity)
- [ ] T038 [US3] Implement deployments blueprint in `app/routes/deployments.py`: `POST /api/deployments/preview` (Viewer, returns config + snippets with source template labels + variables_used), `POST /api/deployments` (Editor+, creates DeploymentRecord, excludes cloud_managed devices → 409, device unreachable → 422, auto-commits via T026 and opens MR), `GET /api/deployments` (paginated history with filters), `GET /api/deployments/{id}` (failure details incl. error_message, config_diff), `POST /api/deployments/{id}/approve` (`@admin_required`, merges MR, 409 if already approved)
- [ ] T039 [US3] Implement CI/CD pipeline `gitlab-ci/.gitlab-ci.yml` (stages validate → preview → approve (when: manual) → deploy → verify → notify; preview artifacts 1-week expiry; `target_devices`/`deploy_mode` variables; runner tags ansible,netconf) and webhook handler `POST /api/webhooks/gitlab/pipeline` in `app/routes/deployments.py` (validate X-GitLab-Token shared secret, update DeploymentRecord status/error_message per device, update Device.config_status and last_deployment)
- [ ] T040 [P] [US3] Implement deployment UI: `app/templates/deployments/list.html` (history table: timestamp, operator, devices, profile, status badge, pipeline link), `app/templates/deployments/detail.html` (pipeline status + approval state, MR/pipeline links, config diff, per-device results, failure message, Approve button for admins per FR-017), `app/templates/deployments/preview.html` (rendered full config + snippet breakdown with template names, variables_used table, Confirm/Reject actions)

**Checkpoint**: User Stories 1–3 functional — inventory, profiles, and the full preview → approve → pipeline → atomic NETCONF deploy → verify loop with complete history.

---

## Phase 6: User Story 4 — Monitor Device and System Health (Priority: P2)

**Goal**: TIG stack wired with MDT gRPC telemetry, provisioned Grafana dashboards for devices and infrastructure, simple up/down device status sourced from Grafana, infra service health, and 2-click deep-links to Grafana.

**Independent Test**: Navigate to the Monitoring section and verify device status table and infrastructure health display; open a device's Grafana deep-link (new tab); verify pre-provisioned dashboards (device-health, infrastructure, interfaces) exist in Grafana.

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T041 [P] [US4] Write `app/tests/test_monitoring.py` (mocked Grafana client: alert states OK/Alerting/Pending/missing → up/down/unknown mapping, service tag alerts → healthy/degraded/down, deep-link URL generation with var-hostname/var-ip and custom grafana_dashboard UID precedence, stale cache served when Grafana unreachable, 502 surfaced on 401/403)

### Implementation for User Story 4

- [ ] T042 [P] [US4] Write Grafana provisioning: `monitoring/grafana/provisioning/datasources.yml` (InfluxDB 2.0 datasource) and `monitoring/grafana/provisioning/dashboards.yml` (folder "StackHive" from file path); dashboard JSON `monitoring/grafana/dashboards/device-health.json` (per-device template variable hostname/ip: interface utilization, CPU, memory, errors from MDT measurements), `monitoring/grafana/dashboards/infrastructure.json` (container CPU/mem/disk, service status), `monitoring/grafana/dashboards/interfaces.json` (per-interface in/out, errors, CRC)
- [ ] T043 [P] [US4] Add Cisco IOS-XE gRPC protobuf schemas under `monitoring/protobuf/cisco_ios_xe/` (telemetry + grpc-gateway + IANA descriptor protos required by Telegraf `grpc_listener_v2` descriptors) and confirm `monitoring/telegraf/telegraf.conf` descriptor path resolves
- [ ] T044 [US4] Implement Grafana client in `app/services/grafana.py` (Bearer service-account auth via T013 credential, device status from `GET /api/alerts/` tagged `hostname:<device>` with status mapping per contracts/grafana-api.md, infra health from `service:<name>` alert tags, dashboard UID discovery via `GET /api/search?type=dash-db`, server-side stale cache, retry with backoff)
- [ ] T045 [US4] Implement monitoring blueprint `app/routes/monitoring.py` (GET `/monitoring` device status table: hostname, IP, up/down/unknown, last check, deep-link; infra health table: service, status, last check, deep-link) and templates `app/templates/monitoring/devices.html`, `app/templates/monitoring/infrastructure.html`; add Grafana deep-link (new tab, 2 clicks from inventory per SC-006) to the monitoring section of `app/templates/inventory/detail.html`
- [ ] T046 [US4] Wire scheduled device status refresh (background job at `refresh_interval` default 60s polling T044 client, updating `Device.monitoring_status` and `last_check`) and add Grafana entry with pre-provisioned dashboard UIDs to the Settings defaults

**Checkpoint**: All four P1/P2 stories independently functional — monitoring section displays real device/infra status with working deep-links.

---

## Phase 7: User Story 5 — Onboard New Network Devices (Priority: P3)

**Goal**: ZTP boot scripts and day-0 configs hosted unauthenticated at serial-based URLs, rendered from the iosxe role with a constrained playbook; Meraki cloud onboarding via ZTP with API-verified registration; devices transition to `onboarded`.

**Independent Test**: Define a ZTP provision for a device in the inventory; confirm `GET /ztp/{serial}.txt` and `/ztp/{serial}.cfg` return the script and rendered day-0 config without authentication; verify a Meraki onboarding produces a cloud-config and flags the device `cloud_managed`.

### Tests for User Story 5

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T047 [P] [US5] Write `app/tests/test_ztp.py` (`GET /ztp/{serial}.txt` returns 200 unauthenticated with `source` command referencing `.cfg`, `GET /ztp/{serial}.cfg` returns rendered day-0 config, unknown serial → 404, rate limiting on unauthenticated access, Meraki provision `.cfg` contains mdt controller commands, provision status transitions pending → generated → delivered → completed and device `config_status` → `onboarded`, failed/timeout states)

### Implementation for User Story 5

- [ ] T048 [US5] Implement ZTP playbook `ansible/ztp.yml` (constrained task set reusing iosxe role templates — bootstrap.j2 + minimal group_vars/host_vars with device-specific data; renders day-0 config plus `.txt` boot script content; no device connection required) per data-model.md "ZTP Provision" flow
- [ ] T049 [US5] Implement ZTP blueprint in `app/routes/ztp.py` serving `GET /ztp/{serial}.txt` (boot script with `source http://{host}/ztp/{serial}.cfg`) and `GET /ztp/{serial}.cfg` (stored day-0 config) without authentication per contracts/ztp-contract.md, with rate limiting on unauthenticated access and 404 for unknown serials; mark provision `delivered` on fetch
- [ ] T050 [US5] Implement onboarding blueprint in `app/routes/onboarding.py` (GET `/api/onboarding/ztp` pending list, POST `/api/onboarding/ztp` — render via T048 ztp.yml, generate script, persist ZTPProvision with url fields, status pending → generated) and provision lifecycle handlers (delivered on fetch, completed on post-boot telemetry → `Device.config_status = onboarded`, failed/timeout with error_message) per data-model.md state transitions
- [ ] T051 [P] [US5] Implement Meraki client in `app/services/meraki.py` (X-Cisco-Meraki-Key auth, `GET {base}/api/v1/devices/{serial}` to verify onboarding) and `POST /api/onboarding/meraki` route in `app/routes/onboarding.py` (generate Meraki day-0 config with license key/organization id/network id/dashboard url per contracts/ztp-contract.md, create ZTPProvision is_meraki=true, set `Device.cloud_managed=true` so the device is excluded from direct NETCONF deployment per FR-015)
- [ ] T052 [US5] Implement onboarding UI `app/templates/onboarding/index.html` (pending ZTP device table: hostname, serial, ztp_url, config_url, status, is_meraki badge; Meraki-cloud flag) and `app/templates/onboarding/provision_form.html` (device select filtered to pending, profile select, Meraki option with network_id field)

**Checkpoint**: All user stories independently functional — new devices can be ZTP-onboarded (local or Meraki) and appear as `onboarded` in inventory.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T053 [P] Implement settings blueprint `app/routes/settings.py` and `app/templates/settings/index.html` + `app/templates/settings/users.html` (GET/PUT `/api/settings` with redacted tokens per contracts/rest-api.md, `PUT /api/settings/credentials/{service_name}` token rotation via T013, user management GET/POST/PUT/DELETE `/api/users` with last-admin deactivation protection) — all `@admin_required`
- [ ] T054 [P] Write `scripts/sync-inventory.sh` (manual NetBox sync trigger that execs the app CLI command) and `scripts/generate-host-vars.py` (CLI helper generating host_vars YAML from a device/profile spec)
- [ ] T055 Validate single-command deployment (SC-008): `docker compose up -d` (with include) brings up all services with correct startup order (health checks), Traefik serves `/`, `/netbox`, `/grafana`, `/ztp` and GitLab on subdomain, GitLab Runner registration succeeds, `GET /api/health` returns 200
- [ ] T056 [P] Security hardening pass: verify credentials never written to files (env vars only), CSRF protection on all form POSTs, Fernet key handling via ENCRYPTION_KEY only, ZTP endpoint rate limiting, RBAC audit log entries present, 403 pages rendered for unauthorized role attempts
- [ ] T057 [P] Performance pass against plan.md goals: inventory page < 2s for 500 devices (paginate + cache hot queries), preview generation < 30s/device, NetBox full sync < 10s, deployment visible status feedback < 2 min (SC-003)
- [ ] T058 [P] Documentation: `README.md` at repo root (architecture overview, single-command deploy, service entry points per quickstart.md, minimum resource requirements 8GB RAM/1GB swap/20GB disk, `.env` reference) and docstrings per constitution principle V
- [ ] T059 Run quickstart.md validation scenarios 1–8 end-to-end (service health, inventory sync, profile creation, preview + override precedence, deployment flow, monitoring, ZTP, RBAC) and fix gaps; verify SC-001 through SC-008 hold

---

## Dependencies & Execution Order

### Dependency Graph

```
Phase 1 (Setup)
    └── Phase 2 (Foundational) ── blocks ALL user stories
            ├── US1 (P1) ──┐
            ├── US2 (P1) ──┤
            ├── US3 (P2) ──┼── (US3 reuses US1 Device model + US2 profiles/Git)
            ├── US4 (P2) ──┤
            └── US5 (P3) ──┘  (US5 reuses US3 iosxe role/playbooks)
                          └── Phase 8 (Polish)
```

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Stories (Phases 3–7)**: All depend on Foundational completion
  - US1 and US2 (both P1) can proceed in parallel once Foundational is done
  - US3 depends on US1 (Device records) and US2 (profiles, Git service) being available for a meaningful end-to-end test, but its core (role, playbooks, preview service) can be built in parallel
  - US4 depends on US1 (device list for status join)
  - US5 depends on US3's iosxe role and templates (bootstrap.j2, mdt.j2) — T048 reuses T035/T036
- **Polish (Phase 8)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies beyond Foundational
- **User Story 2 (P1)**: No dependencies beyond Foundational (shares base layout, User model from Phase 2)
- **User Story 3 (P2)**: Uses US1 Device model and US2 profile/Git services for full E2E preview/deploy; independently buildable
- **User Story 4 (P2)**: Uses US1 device list to join status; Grafana client independent
- **User Story 5 (P3)**: Reuses US3 Ansible role (`ansible/roles/iosxe/`) and playbooks for ZTP rendering

### Within Each User Story

- Tests (written first, failing) → services/clients → models/routes → UI templates → integration
- Services before endpoints that call them (e.g., T019 before T021/T022; T026 before T027/T028; T035/T036 before T037/T039; T044 before T045)
- Each story is independently testable at its checkpoint

### Parallel Opportunities

- Phase 1: T003, T004, T005, T006, T007 all [P] — one person on T001/T002, others on config files
- Phase 2: T010–T016 all [P] (models + migration T008/T009 first)
- US1: T017/T018 tests in parallel; T021 and T023 (blueprint/UI vs dashboard) in parallel after T019/T020
- US2: T024/T025 tests in parallel; T027+T028 (routes+git persistence) with T029/T030/T031 (UI) in parallel
- US3: T032/T033/T034 tests in parallel; T035 (role) and T036 (playbooks) in parallel; T040 UI in parallel with T038/T039
- US4: T041 test, T042 provisioning, T043 protobuf all [P] with T044 client; T045 after T044
- US5: T047 test, T048 playbook, T051 Meraki in parallel; T049/T050 sequential
- Cross-team: Developer A on US1, Developer B on US2, Developer C on Ansible role/playbooks (T035/T036) simultaneously after Phase 2

---

## Parallel Example: User Story 3

```bash
# Launch all tests for User Story 3 together (failing first):
Task: T032 "app/tests/test_deployments.py"
Task: T033 "app/tests/test_ansible_preview.py"
Task: T034 "ansible/tests/molecule/ scenario"

# Launch the two independent Ansible artifacts together:
Task: T035 "ansible/roles/iosxe/ role (tasks, defaults, templates, filters)"
Task: T036 "ansible/site.yml, preview.yml, verify.yml, inventory/netbox.yml"

# Then in parallel:
Task: T037 "app/services/ansible.py"
Task: T040 "deployment UI templates"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Run `app/tests/test_inventory.py`; manually verify inventory list, filters, detail page, dashboard overview
5. Deploy/demo the MVP

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. Add User Story 1 (P1) → test independently → demo (MVP!)
3. Add User Story 2 (P1) → test independently → demo (profiles + Git workflow)
4. Add User Story 3 (P2) → test independently → demo (full deploy loop)
5. Add User Story 4 (P2) → test independently → demo (monitoring)
6. Add User Story 5 (P3) → test independently → demo (ZTP + Meraki onboarding)
7. Polish phase → run all quickstart scenarios

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 (Flask inventory + NetBox client)
   - Developer B: US2 (profiles + GitLab service)
   - Developer C: US3 Ansible artifacts (role, playbooks, Molecule)
   - Developer D: US4 (Grafana/TIG) once US1 device models land
3. US5 starts after US3's role/playbooks exist
4. Each story integrates at its checkpoint without breaking previous stories

---

## Notes

- **Task totals**: 59 tasks (7 Setup, 9 Foundational, 7 US1, 8 US2, 9 US3, 6 US4, 6 US5, 7 Polish)
- `[P]` tasks = different files, no dependencies on incomplete tasks
- `[USn]` labels map tasks to specific user stories for traceability
- Each user story is independently completable and testable at its checkpoint
- Verify tests fail before implementing (TDD order within each story phase)
- Commit after each task or logical group
- Constitution constraints: open-source deps only, Python-first, minimal JS (Bootstrap 5 + HTMX only), containerized services, docstrings on all functions
- Edge cases covered: NetBox unavailable (T019/T021 503), unknown device type in profile (T030), device unreachable mid-deploy (T038 no partial apply), merge conflicts (T026 409), ZTP boot failure (T050 failed state), Viewer write attempts (T024/T032 403)
