# Implementation Plan Quality Checklist: Network Operations Dashboard

**Purpose**: Cross-document validation — spec requirements vs plan vs data model vs API contracts
**Created**: 2025-08-18
**Feature**: [spec.md](./spec.md) | [plan.md](./plan.md) | [data-model.md](./data-model.md) | [contracts/](./contracts/)
**Reviewed**: 2025-08-18 — all 60 items resolved; see per-item notes

## Requirement Completeness

- [X] CHK001 - Is every Functional Requirement (FR-001 through FR-020) traceable to at least one artifact in the plan, data model, or API contracts? [Completeness]
  - **Verified**: FR-001–020 (incl. 004b, 015b, 018b–e) all map to data-model entities and/or REST endpoints; FR-010 via research §3 (NETCONF atomic commit); FR-019 via `Device.platform` field + `roles/iosxe` structure.

- [X] CHK002 - Are requirements for the Dashboard overview landing page (FR-018d: summary cards, recent deployments, pending approvals) reflected in both the API contracts and the data model? [Completeness, Spec §FR-018d]
  - **Verified**: `GET /api/dashboard` (device_health, recent_deployments, pending_approvals) in the REST contract; data-model indexes `device(config_status)` and `deployment_record(status)` support the summary cards.

- [X] CHK003 - Are requirements for the Settings page defined beyond credential management? The spec lists Settings as a top-level nav section (FR-018c), but the API contract only covers credential rotation and system config. Are other settings (e.g., refresh interval, Git branch names, ZTP base URL) fully documented? [Completeness, Spec §FR-018c]
  - **Verified**: Settings contract documents `refresh_interval`, `git_working_branch`, `git_production_branch`, `ztp_base_url`, `ansible_repo_path`, all service URLs — plus new `influxdb_retention_days` and `drift_check_enabled`.

- [X] CHK004 - Is the Meraki Dashboard API integration documented in a dedicated contract file? The plan includes a `meraki.py` service module and the ZTP contract references Meraki onboarding, but no `meraki-api.md` contract exists. [Gap]
  - **Resolved**: Meraki usage is limited to a single endpoint (post-boot verification `GET {meraki_api_base}/api/v1/devices/{serial}`), documented in ztp-contract §4 with auth, error handling, and non-fatal semantics. A dedicated `meraki-api.md` is not warranted for one endpoint; the client lives in `services/meraki.py`.

- [X] CHK005 - Are requirements defined for password recovery or account lockout? The spec defers SSO but includes local auth (FR-018b). The auth contract only covers login/logout. [Gap]
  - **Resolved (decision 2025-08-18)**: v1 scope = self-service password change (`PUT /api/auth/password`) + admin reset (`PUT /api/users/{user_id}/password`), both added to the REST contract and noted in data-model §11. No account lockout in v1 (SSO deferred; 3–10 internal users).

- [X] CHK006 - Is the mechanism for assigning profiles to devices explicitly documented? The spec mentions profiles target a device role (FR-004), but the data model lacks a device-to-profile assignment entity — is assignment implicit via role matching or explicit? [Ambiguity, Spec §FR-004, Data Model §ConfigurationProfile]
  - **Resolved**: Assignment is **implicit role-matching**, now documented in data-model §3 ("Assignment model"): a device's effective profile is the one active profile matching its role; REST `assigned_profiles` and preview "current assignment" derive from this rule.

- [X] CHK007 - Are requirements defined for bulk operations (e.g., applying a profile to all devices of a role, bulk device onboarding)? [Gap]
  - **Resolved**: Bulk deployment is covered — `POST /api/deployments` accepts a `device_ids` array (multi-device batch; all devices of a role can be targeted). Bulk ZTP onboarding is out of scope for v1 (per-device onboarding per spec US5; the API is repeatable per device).

- [X] CHK008 - Is the health check endpoint (`/api/health`) documented in the REST API contract? It is referenced in quickstart.md and the plan but absent from contracts/rest-api.md. [Gap]
  - **Resolved**: `GET /api/health` (public probe used by Traefik healthcheck middleware, returns `status` + per-service up/down) added to the REST contract; quickstart already validates it.

- [X] CHK009 - Are data migration requirements documented for the SQLite-to-PostgreSQL path? The plan mentions the migration path but no migration strategy, data backup, or zero-downtime requirements are specified. [Gap, Plan §SQLite vs PostgreSQL]
  - **Verified**: Research §2 documents the full strategy — Alembic migrations from day one, `DATABASE_URL` switch, `postgres:16-alpine` compose service, and `sqlite3 .dump` export/import for zero-downtime cutover. Backup is covered under CHK044.

- [X] CHK010 - Is the `Flask-Caching` dependency documented consistently? The research recommends it for NetBox inventory caching, but it does not appear in the plan's Primary Dependencies list. [Consistency, Research §1]
  - **Resolved**: `Flask-Caching` (in-memory, 5-minute TTL per research §1) added to the plan's Primary Dependencies, matching the `cache` init in `extensions.py`.

## Document Consistency

- [X] CHK011 - Is the encryption algorithm for service credentials consistent across documents? The data model specifies AES-256 (ServiceCredential §Validation Rules), while the plan and research specify Fernet encryption. [Conflict, Data Model §10 vs Plan §Structure Decisions 9]
  - **Resolved**: Fernet is canonical (plan Structure Decision 9, data-flow diagram, quickstart `ENCRYPTION_KEY`). The stray "AES-256" field description in data-model §10 was corrected to Fernet.

- [X] CHK012 - Is the Git branch strategy consistent across documents? The data model lists three branches (`main`, `staging`, `working/<user>/<description>`), while the gitlab-api contract lists only two (`main`, `working`). [Conflict, Data Model §Git Branch Strategy vs GitLab Contract §Branch Strategy]
  - **Resolved**: Two branches canonical — `main` + shared `working` — per the spec's singular "working branch", the GitLab contract, and the `git_production_branch`/`git_working_branch` settings. The `staging` row and per-user branch pattern were removed from the data model; `DeploymentRecord.git_branch` examples fixed.

- [X] CHK013 - Are DeploymentRecord status values consistent between the data model and the REST API contract? The data model defines `pending`, `in_progress`, `deployed`, `failed`, `cancelled`, `approved`. The API contract defines `pending`, `running`, `success`, `failed`. [Conflict, Data Model §6 vs REST Contract §Deployments]
  - **Resolved (decision 2025-08-18)**: REST model wins — one `DeploymentRecord` per deployment (batch) with a new `DeploymentDevice` child entity for per-device outcomes. Canonical statuses `pending`, `approved`, `running`, `success`, `failed`, `cancelled` applied in both data model §6/6b and the REST contract.

- [X] CHK014 - Is the device identifier strategy consistent? The REST API uses string UUIDs (`id: string`), while the data model uses `netbox_id: Integer`. Are UUIDs generated locally or derived from NetBox IDs? [Ambiguity, REST Contract §Inventory vs Data Model §Device]
  - **Resolved**: REST device `id` is the **NetBox device ID** (numeric, JSON-serialized as string) — not a locally generated UUID; local DB PKs are internal only. Documented in REST Global Conventions.

- [X] CHK015 - Is the ZTP blueprint authentication model consistent? The plan states ZTP routes are "served unauthenticated," while the research notes ZTP files should rate-limit unauthenticated access. Are rate-limiting requirements specified? [Ambiguity, Plan §Structure Decisions 4]
  - **Resolved**: Unauthenticated ZTP serving is intentional (devices cannot authenticate at boot); rate limit is now specified: **10 requests/minute per client IP → `429` with `Retry-After`** (ztp-contract §3, REST Global Conventions).

- [X] CHK016 - Is the "minimal JavaScript" constraint (FR-018) consistent with the research's HTMX recommendation? HTMX is a JavaScript library — is its scope and footprint quantified against the constraint? [Consistency, Spec §FR-018 vs Research §1]
  - **Resolved**: HTMX 2.x (~24 KB) is the single JS dependency; footprint quantified in research §1 against FR-018 (HTMX vs HTMX-free evaluation); now listed in the plan's Primary Dependencies alongside CSS-only Bootstrap.

- [X] CHK017 - Is the credential storage approach consistent? The plan states "Credentials never stored in files — environment variables only," yet both the plan and data model describe `.env` fallback for credentials. Is `.env` considered an "environment variable" or a "file"? [Ambiguity, Plan §Constraints vs Data Model §10]
  - **Resolved**: Plan constraint reworded — credentials are never committed to version control or stored in plaintext in the config repo; initial bootstrap uses environment variables or a **gitignored** `.env`; at runtime tokens resolve from the Fernet-encrypted DB credential store with `.env` fallback (consistent with data-model §10 Resolution Order and quickstart).

- [X] CHK018 - Are the ZTP provision status values consistent between the data model and the REST API? The data model defines `pending`, `generated`, `delivered`, `completed`, `cancelled`, `failed`. The API contract uses `pending`, `provisioned`, `onboarded`. [Conflict, Data Model §8 vs REST Contract §Onboarding]
  - **Resolved**: Canonical ZTP statuses `pending`, `generated`, `delivered`, `onboarded`, `failed`, `cancelled` — data model `completed` renamed to `onboarded`; REST aligned; `delivered` defined as a logged artifact fetch by the ZTP HTTP handler.

- [X] CHK019 - Is the monitoring refresh interval's default value specified? The NetBox contract mentions `refresh_interval` defaulting to 60 seconds, the Grafana contract references the same setting, but is the default documented in the Settings contract? [Gap, NetBox Contract §Sync Behavior]
  - **Resolved**: `refresh_interval` default (60 s) is now stated in the REST `GET /api/settings` field table.

- [X] CHK020 - Do the API contracts align with the User entity's permission matrix? Cross-check each endpoint's role requirement against the data model's Permission Matrix table. [Consistency, Data Model §11 vs All Contracts]
  - **Verified**: Every endpoint role cross-checked against the matrix — all consistent. Clarification added: the Viewer row now explicitly includes read access to profiles (reference data) in addition to inventory/monitoring/deployments/onboarding; all write endpoints enforce 403.

## Acceptance Criteria Quality

- [X] CHK021 - Can Success Criteria SC-001 ("within 3 clicks") be objectively verified? Is the click path from login to device detail page defined? [Measurability, Spec §SC-001]
  - **Verified**: Click path — Dashboard (auto-landing after login) → Inventory (click 1) → device row (click 2) = device detail page; 2 ≤ 3 clicks.

- [X] CHK022 - Can Success Criteria SC-002 ("under 5 minutes") be objectively measured? What constitutes "from scratch" — creating a profile with zero templates, or a functional profile with at least one template? [Measurability, Spec §SC-002]
  - **Verified**: Measurable — "from scratch" = a *functional* profile (name + device role + ≥1 template + variables) created through the UI from an empty state, timed end-to-end against a device role that already exists in NetBox.

- [X] CHK023 - Are performance goals in the plan measurable against the Success Criteria? The plan lists specific targets (dashboard load < 2s, preview < 30s, etc.) — are these traceable to specific SC items or are they independent requirements? [Traceability, Plan §Technical Context]
  - **Resolved**: Traceability annotated in the plan — deployment < 2 min = SC-003; preview < 30 s supports SC-003; dashboard < 2 s and sync < 10 s are documented as independent NFRs (not tied to any SC, which is permissible).

- [X] CHK024 - Is the "pixel-perfect parity" claim for preview-vs-deployment quantified? The plan states the preview subprocess guarantees parity — how is parity validated? [Measurability, Plan §Structure Decisions 3]
  - **Resolved**: Parity defined in plan Structure Decision 3 — preview and deployment execute the *same* playbooks/roles/vars (preview mode), so input parity is structural; collection pins (`ansible/collections/requirements.yml`) keep rendering identical; realized parity is validated by the post-deploy `verify.yml` (NETCONF `get-config` read-back compared against the render).

- [X] CHK025 - Can Success Criteria SC-008 ("single compose command") be verified with the current compose structure? The plan uses `include` for NetBox — does `docker compose up` satisfy "single command"? [Measurability, Spec §SC-008]
  - **Verified**: `docker compose up -d` at the repo root starts all services including NetBox via the compose `include` directive (Compose v2.24+, per research §8); quickstart step 5 confirms.

## Scenario Coverage

- [X] CHK026 - Are requirements defined for concurrent deployments to the same device? What happens when two operators trigger deployments targeting overlapping devices? [Gap]
  - **Resolved**: Concurrency guard — a target device may have at most one deployment with status `pending`/`approved`/`running`; a new deployment overlapping an in-flight device is rejected with 409 (data-model §6 validation, REST `POST /api/deployments` errors).

- [X] CHK027 - Are requirements defined for deployment to a partially reachable device set (e.g., 3 of 5 devices succeed, 2 fail)? Is partial deployment status tracked and reported? [Gap]
  - **Resolved**: Per-device outcomes stored in `DeploymentDevice` rows; the deployment record is `failed` if any device fails (success requires all); `error_message` names failed devices; REST records expose a per-device `devices` array for the UI breakdown.

- [X] CHK028 - Are requirements defined for ZTP boot failures where the device cannot reach the ZTP server? Is a timeout/retry policy specified? [Gap, Spec §Edge Cases]
  - **Resolved**: No server-side auto-fail timeout — the Cisco ZTP loader retries fetches automatically; the provision stays `generated`/`delivered`, fetch attempts are application-logged, and the operator marks the provision `failed` (with error detail) or `cancelled` (ztp-contract Boot Failure Policy, data-model §8).

- [X] CHK029 - Are requirements defined for the scenario where NetBox device types lack interface data? The spec lists this as an edge case — is a fallback or error message specified? [Gap, Spec §Edge Cases]
  - **Resolved**: Fallback = locally maintained `DeviceType` records (data-model §2 supports NetBox/Local sourcing) + new Device Types API (`GET /api/device-types` list/detail for Viewer, `PUT /api/device-types/{id}` for Admin) noted in the netbox contract.

- [X] CHK030 - Are requirements defined for handling configuration conflicts when multiple profiles target the same config section on a device? The spec lists this edge case — is resolution logic documented? [Gap, Spec §Edge Cases]
  - **Resolved (decision 2025-08-18)**: **One active profile per device role** is enforced (409 on a second active profile for the same role), so cross-profile section conflicts are impossible; per-device customization is via device overrides only (existing host_vars > group_vars precedence).

- [X] CHK031 - Are requirements defined for Viewer attempting write operations? The spec lists this edge case — do all API contracts enforce 403 responses consistently? [Coverage, Spec §Edge Cases]
  - **Verified**: All write endpoints list 403 in their error tables; the `role_required` decorator returns 403 and logs role-check failures (research §9); quickstart Scenario 8 validates 403 behavior per role.

- [X] CHK032 - Are requirements defined for the zero-state scenario: no devices in inventory, no profiles created, no deployments recorded? Are empty-state UI requirements specified? [Gap]
  - **Resolved**: Empty-state UX requirement added (plan §Non-Functional Decisions): every list/dashboard view renders a friendly empty state with a next-action CTA; no broken layouts at zero data.

- [X] CHK033 - Are requirements defined for what happens when a device is deleted from NetBox but still has local deployment records or profile assignments? [Gap]
  - **Resolved**: `stale` boolean added to `Device` — set when a sync finds the device missing from NetBox; stale devices are excluded from new deployments (409), displayed dimmed with a warning, retain history/overrides, and the flag clears when the device reappears (data-model §1, REST device objects).

- [X] CHK034 - Are requirements defined for handling GitLab merge conflicts during auto-commit? The plan mentions rebase attempts — what happens if rebase also fails? [Gap, Plan §GitLab Contract]
  - **Resolved**: Any rebase/push failure — conflict **or otherwise** — surfaces a 409 Conflict to the user with git error details; the user (or admin) resolves via the GitLab MR interface and retries (gitlab-api Auto-Commit Workflow).

- [X] CHK035 - Are requirements defined for ZTP artifact cleanup? When does a completed ZTP provision's hosted files get removed? [Gap]
  - **Resolved**: A daily cleanup job removes served artifacts for provisions in terminal states (`onboarded`/`failed`/`cancelled`) older than 30 days; DB records are retained indefinitely (ztp-contract Artifact Cleanup, data-model §8).

## Edge Case Coverage

- [X] CHK036 - Are requirements defined for handling very large configuration renders? Is there a maximum template size or snippet count limit? [Gap]
  - **Resolved**: Limits defined — 51,200-byte maximum per template and at most 20 templates per profile (data-model §4); `strict_variables` makes undefined-variable renders fail fast.

- [X] CHK037 - Are requirements defined for circular or recursive template variable references? [Gap]
  - **Resolved**: Save-time template validation resolves `{% include %}` references; circular include chains are rejected with 400 (data-model §4). Variable "references" are Ansible variable lookups, which cannot be self-recursive.

- [X] CHK038 - Are requirements defined for device hostname collisions during inventory sync? [Gap]
  - **Resolved**: Sync upserts by `netbox_id` (not hostname); if two NetBox devices share a hostname, the later record is skipped, the conflict is logged and surfaced in a sync-report badge, and the operator renames one device in NetBox and re-syncs (data-model Sync Flow, netbox-api Sync Behavior).

- [X] CHK039 - Are requirements defined for the scenario where the GitLab Runner loses network access to managed devices mid-deployment? [Gap]
  - **Resolved**: Mid-deployment connectivity loss fails only the affected host's Ansible task; other hosts continue; the notify webhook reports per-device results; the verify stage flags unverified devices; the operator re-runs a targeted deployment for failed hosts (gitlab-api CI/CD Pipeline section).

- [X] CHK040 - Are requirements defined for handling InfluxDB data retention — when telemetry data expires, how does the dashboard reflect stale monitoring data? [Gap]
  - **Resolved**: InfluxDB bucket retention defaults to 14 days (configurable via `influxdb_retention_days`); a device with no recent metrics (last-seen beyond 5× `refresh_interval`) displays status `unknown` with a `last_seen` timestamp (grafana-api Refresh Behavior).

## Non-Functional Requirements

- [X] CHK041 - Are rate limiting requirements specified for any API endpoint? The ZTP endpoint is unauthenticated — is rate limiting documented? [Gap]
  - **Resolved**: Authenticated endpoints are not rate-limited (internal tool, 3–10 users); unauthenticated ZTP file routes are limited to 10 requests/minute per client IP → 429 (REST Global Conventions, ztp-contract §3).

- [X] CHK042 - Are API pagination limits documented consistently? The REST contract specifies `per_page` max of 100 for devices — is this consistent across all list endpoints? [Consistency, REST Contract]
  - **Resolved**: Pagination standardized — `page`/`per_page` (default 25, max 100) on the two unbounded endpoints (`/api/devices`, `/api/deployments`); all other list endpoints return their full bounded set, documented in REST Global Conventions.

- [X] CHK043 - Is an API versioning strategy documented? The contracts define endpoints without version prefixes — is backward compatibility addressed? [Gap]
  - **Resolved**: v1 endpoints carry no version prefix (`/api` = v1); changes within v1 are additive-only; breaking changes introduce `/api/v2` (REST Global Conventions).

- [X] CHK044 - Are data backup and recovery requirements documented? The plan describes Docker volumes but no backup schedule, retention policy, or recovery procedure. [Gap]
  - **Resolved**: Backup & recovery defined (plan §Non-Functional Decisions) — daily cron tar of all named volumes, retention 7 daily + 4 weekly, documented recovery procedure (recreate volume, untar, `docker compose up -d`); GitLab CE rake backups as secondary safety net.

- [X] CHK045 - Are logging and audit requirements specified beyond deployment history? Are user actions (login, profile edits, settings changes) logged for audit trail? [Gap]
  - **Resolved**: Audit split by domain (plan §Non-Functional Decisions) — configuration changes are audited by Git itself (auto-commits with author + descriptive message, full history/diffs in GitLab); auth and admin actions (login success/failure, user management, settings changes, credential rotation, approvals) are emitted as structured JSON logs; role-check failures are logged (research §9).

- [X] CHK046 - Are accessibility requirements defined? The spec targets network operators — are WCAG or keyboard navigation requirements specified? [Gap]
  - **Resolved**: WCAG 2.1 AA baseline added to plan Constraints — keyboard operability, semantic landmarks, labeled controls, 4.5:1 contrast (Bootstrap 5 defaults cover most; server-rendered pages keep it achievable without JS frameworks).

- [X] CHK047 - Is TLS/HTTPS requirement scope defined? The research discusses Traefik TLS for production — is TLS mandatory or optional for the initial release? [Ambiguity, Research §6]
  - **Resolved**: v1 = HTTP over the trusted LAN is acceptable; Traefik TLS termination (self-signed or ACME resolver) is supported out-of-box but **not mandatory** for the initial release (plan Constraints, research §6).

- [X] CHK048 - Are requirements defined for localization or timezone handling? Timestamps in deployment records, device status — what timezone convention is used? [Gap]
  - **Resolved**: All timestamps are stored and displayed in UTC (ISO 8601, `Z` suffix); UI is English-only in v1, no localization (plan Constraints, REST Global Conventions).

## Dependencies & Assumptions

- [X] CHK049 - Is the assumption of NetBox availability validated with failover requirements? The spec lists "NetBox unavailable" as an edge case — what is the graceful degradation behavior? [Assumption, Spec §Edge Cases]
  - **Verified**: Defined — NetBox unreachable: dashboard serves the last cached inventory, surfaces 503 for live queries; deployments are blocked with a clear error (strict inventory plugin). No active/active failover is required (netbox-api Sync Behavior, REST 503 errors, research `netbox_health`).

- [X] CHK050 - Is the assumption of NETCONF/YANG support on all managed devices documented as a hard requirement? What is the fallback for non-NETCONF devices? [Assumption, Spec §Assumptions]
  - **Resolved**: v1 makes NETCONF/YANG a **hard requirement** for all managed devices (spec assumption holds for IOS-XE initial release); non-NETCONF devices are rejected at deployment (422); the netmiko/napalm SSH fallback is reserved for the vendor-agnostic phase (plan Constraints).

- [X] CHK051 - Are external service timeout and retry requirements documented consistently across all integration contracts? Do NetBox, GitLab, Grafana, and Meraki contracts define the same timeout and retry semantics? [Consistency, All Contracts §Error Handling]
  - **Resolved**: Standardized across all integration clients — **10 s connection timeout; 3 retries with exponential backoff on 5xx/timeouts** — with per-service surface behavior (503 vs stale-cache). Notes added to NetBox, Grafana, and Meraki (ztp-contract) error-handling sections.

- [X] CHK052 - Is the assumption of "3-10 concurrent users" validated against SQLite's single-writer constraint? Are write-contention scenarios addressed? [Assumption, Plan §Scale/Scope vs Research §2]
  - **Verified**: Validated — writes are infrequent (profile edits, deployment record creation) from 3–10 users; SQLite single-writer model handles this with WAL journal mode (research §2); migration path to PostgreSQL triggers at >20 concurrent writers.

- [X] CHK053 - Are resource requirements (8 GB RAM, 20 GB disk) validated as minimums for the initial device count? What is the scaling curve for 500 devices? [Measurability, Plan §Resource Requirements]
  - **Resolved (decision 2025-08-18)**: Disk budget corrected — 40 GB minimum, **100 GB+ recommended** for sustained 500-device MDT (InfluxDB was under-budgeted at ~3 GB); InfluxDB retention defaults to 14 days (configurable); Quickstart Scenario 9 adds a pilot sizing measurement (10 devices, 1 week, extrapolate).

- [X] CHK054 - Is the assumption that "device types and interface layouts can be determined from NetBox" validated? What if NetBox lacks detailed interface data for a device model? [Assumption, Spec §Assumptions]
  - **Verified**: NetBox device types carry interface templates for standard models; where data is missing, the documented fallback is locally maintained `DeviceType` records via `PUT /api/device-types` (same fix as CHK029).

## Ambiguities & Conflicts

- [X] CHK055 - Is the term "configuration preview" used consistently? In the plan it referred to Ansible `--check --diff` output. In the API contract it referred to rendered config text. Are these the same artifact? [Ambiguity, Plan §Structure Decisions 3 vs REST Contract §Deployments/Preview]
  - **Resolved**: Terminology aligned — "configuration preview" = the **full rendered configuration** (assembled snippets with merged variables) in both plan and REST; the plan's `--check --diff` phrasing was clarified as the preview-mode execution mechanism; realized device-level parity is validated via `verify.yml` read-back (plan Structure Decision 3, REST Global Conventions).

- [X] CHK056 - Is the relationship between `ConfigurationVariable` and `DeviceOverride` entities clear? The data model says DeviceOverride is "logically represented by ConfigurationVariable records with scope=device" — is this a single-table design or two separate tables? [Ambiguity, Data Model §7 vs §9]
  - **Resolved**: **Single table** is canonical — `ConfigurationVariable` rows with `scope='device'` (`profile_id` NULL per §7). Data-model §9 was rewritten as a logical view over that row set (the former separate field table/JSON blob conflicted with §7 and was removed); the API exposes a device's override set as one object.

- [X] CHK057 - Is the meaning of `config_status` state `modified` clear? The data model defines it as "External config change detected" — how is external drift detected? Is this an automated check or manual flag? [Ambiguity, Data Model §Device State Transitions]
  - **Resolved (decision 2025-08-18)**: Both — on-demand per-device check (`POST /api/devices/{id}/drift-check`, Editor+) **plus** a nightly background job over deployed devices (`drift_check_enabled` setting, default on, 02:00 local); both compare NETCONF `get-config` against the last deployed render (data-model §1 Drift Detection).

- [X] CHK058 - Is the scope of "modular, composable configuration templates" bounded? Can templates reference other templates? Is there a maximum nesting depth? [Ambiguity, Spec §FR-005]
  - **Resolved**: Bounded — templates are self-contained snippets; cross-template composition only via Jinja `{% include %}`; circular include chains rejected at save time (400), which bounds nesting; template count (≤20) and size (≤51 KB) limits apply (data-model §4, research §10).

- [X] CHK059 - Is the data retention policy for DeploymentRecords defined? How long are deployment records kept? Is there an archival or cleanup policy? [Gap]
  - **Resolved**: DeploymentRecords are retained **indefinitely** — they are the immutable audit trail and grow slowly (one record per deployment batch; per-device rows are small). No archival policy needed at v1 scale.

- [X] CHK060 - Is the behavior of inactive profiles (`is_active=false`) fully specified? Can they still be referenced by existing deployment records? Can they be reactivated? [Ambiguity, Data Model §ConfigurationProfile]
  - **Resolved**: Inactive profiles cannot be targeted in new deployments (409), remain in history and existing deployment records, and can be re-activated when no other active profile occupies the role (data-model §3 validation rules).
