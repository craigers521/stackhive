# Data Model — Network Operations Dashboard

**Feature**: `001-network-ops-dashboard`
**Source**: [spec.md](./spec.md)

---

## Overview

The system manages two distinct data domains:

| Domain | Source of Truth | Mutability | Persistence |
|---|---|---|---|
| **Immutable inventory** | NetBox (external) | Read-only local cache | SQLAlchemy DB (synced from NetBox) |
| **Configuration data** | StackHive (local) | Read-write | Git repo (YAML files) + SQLAlchemy metadata |

Configuration profiles, templates, and variables are authored in the web UI, persisted as static YAML files in a Git repository, and version-controlled through GitLab. The local SQLAlchemy database tracks device operational state, deployment history, and user accounts.

---

## Entity-Relationship Diagram

```
┌──────────────┐       ┌────────────────┐
│   Device Type│────1:*│     Device     │
└──────────────┘       └──────┬─────────┘
                              │ 1:*
                    ┌─────────┼──────────────────┐
                    │         │                   │
              ┌─────┴──┐ ┌────┴─────┐      ┌─────┴──────┐
              │ ZTP    │ │Device    │      │Deployment  │
              │Provision│ │Override  │      │Record      │
              └────────┘ └──────────┘      └─────┬──────┘
                                                  │
                                    ┌─────────────┼──────────────┐
                                    │             │              │
                            ┌───────┴────┐  ┌────┴─────┐   ┌────┴─────┐
                            │  Device    │  │  User    │   │Config    │
                            │ (target)   │  │          │   │Profile   │
                            └────────────┘  └──────────┘   └────┬─────┘
                                                                │
                                                  ┌─────────────┼──────────────┐
                                                  │             │              │
                                        ┌─────────┴──────┐ ┌────┴───────┐ ┌───┴──────────┐
                                        │Config Template │ │Interface   │ │Config        │
                                        │                │ │Template    │ │Variable      │
                                        └────────────────┘ └────────────┘ └──────────────┘
```

---

## Entities

> **Note**: The data model includes 12 entities. The `ServiceCredential` entity (Section 10) stores encrypted backend service tokens with `.env` fallback. `DeploymentDevice` (Section 6b) stores per-device outcomes for multi-device deployments. All other entities are unchanged from the spec.

### 1. Device

A network device with immutable attributes sourced from NetBox and local operational attributes tracking deployment and monitoring state.

**FR references**: FR-001, FR-002, FR-003, FR-020

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `netbox_id` | `Integer` | Unique, not null | NetBox | Foreign key to NetBox device record |
| `hostname` | `String(255)` | Unique, not null, DNS-compliant | NetBox | FQDN of the device |
| `serial_number` | `String(64)` | Not null | NetBox | Manufacturer serial number |
| `mac_address` | `String(17)` | Not null, MAC format | NetBox | Base MAC address |
| `mgmt_ip` | `String(45)` | Not null, IPv4/IPv6 | NetBox | Management interface IP |
| `role` | `String(128)` | Not null | NetBox | NetBox device role (e.g., `access-switch`) |
| `site` | `String(255)` | Not null | NetBox | Physical site/location name |
| `device_type_id` | `Integer` | FK → `DeviceType.id` | NetBox | Links to physical device model |
| `platform` | `String(64)` | Not null; default `cisco_iosxe` | NetBox | OS/platform identifier |
| `config_status` | `String(32)` | Not null; default `pending` | Local | Current configuration state |
| `last_deployment_id` | `Integer` | FK → `DeploymentRecord.id`, nullable | Local | Most recent deployment attempt |
| `monitoring_status` | `String(16)` | Default `unknown` | Local | Up/down/unknown from Grafana |
| `cloud_managed` | `Boolean` | Default `false` | Local | Flag for Meraki-cloud-managed devices |
| `stale` | `Boolean` | Default `false` | Local | No longer present in NetBox at last sync; excluded from new deployments |
| `last_netbox_sync` | `DateTime` | Nullable | Local | Timestamp of last NetBox sync |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `hostname`: Must be valid DNS name (RFC 1123); max 253 chars
- `mac_address`: Must match `^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$`
- `mgmt_ip`: Must be valid IPv4 or IPv6 address
- `config_status`: Must be one of the allowed state values (see transitions below)
- `platform`: Initial release restricts to `cisco_iosxe`; schema open for extension
- `cloud_managed`: When `true`, device is excluded from direct NETCONF deployment
- `stale`: Set when a sync finds the device missing from NetBox; stale devices are excluded from new deployments (409), displayed dimmed with a warning in the UI, and retain history/overrides; the flag clears automatically when the device reappears in a later sync

#### State Transitions (`config_status`)

```
pending → deployed → modified → deployed
    │           │
    │           └→ failed
    │
    └→ onboarded (via ZTP completion)
```

| Transition | Trigger | Description |
|---|---|---|
| `pending` → `onboarded` | ZTP provision completes | Device completed day-0 boot, ready for profile assignment |
| `pending`/`onboarded` → `deployed` | Successful deployment | Profile successfully merged to device via NETCONF |
| `deployed` → `failed` | Deployment error | NETCONF commit failed; config rolled back on device |
| `deployed` → `modified` | Drift detected | Device running config diverged from last deployed render (on-demand drift check or nightly background drift job — see Drift Detection below) |
| `failed`/`modified` → `deployed` | Successful redeployment | Profile re-applied and committed atomically |

#### Drift Detection

`modified` is set by either:

- **On-demand**: an operator (Editor+) triggers `POST /api/devices/{id}/drift-check` — the dashboard fetches the running config via NETCONF `get-config` and compares it to the last deployed render.
- **Background**: a nightly job (02:00 local, controlled by the `drift_check_enabled` setting, default on) performs the same comparison for all non-stale, non-cloud-managed devices in `deployed`; divergent devices are marked `modified` and shown with their diff on the device detail page.

There is no continuous polling in v1.

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `DeviceType` | many-to-one | Physical device model definition |
| has many | `DeploymentDevice` | one-to-many | Per-device deployment outcomes (via deployment batches) |
| has one | `ZTPProvision` | one-to-zero-or-one | Pending day-0 boot config |
| has many | `DeviceOverride` | one-to-many | Per-device variable overrides |

---

### 2. Device Type

Physical characteristics of a device model: interface counts, types, and slot configurations. Sourced from NetBox or maintained locally.

**FR references**: FR-003, FR-006

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `netbox_id` | `Integer` | Unique, nullable | NetBox | Foreign key to NetBox device type |
| `manufacturer` | `String(128)` | Not null | NetBox/Local | e.g., `cisco` |
| `model` | `String(255)` | Not null | NetBox/Local | e.g., `C9300-48P` |
| `part_number` | `String(64)` | Nullable | NetBox | Manufacturer part number |
| `interface_count` | `Integer` | Not null, ≥ 0 | NetBox/Local | Total physical interfaces |
| `interface_types` | `JSON` | Not null | Local | Map of interface type → count (e.g., `{"GigabitEthernet": 48, "TenGigabitEthernet": 4}`) |
| `slot_config` | `JSON` | Nullable | Local | Modular slot definitions (e.g., NEM module configs) |
| `uplink_slots` | `Integer` | Default 0 | Local | Count of available uplink slots |
| `management_interfaces` | `JSON` | Nullable | Local | Management port definitions (e.g., `["MgmtEth0/RP0/cpu0"]`) |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `interface_types`: JSON object with string keys (interface type names) and integer values (≥ 0)
- `interface_count`: Must equal `sum(interface_types.values())` if `interface_types` is provided
- `slot_config`: If present, must be valid JSON array of slot definitions
- `manufacturer` + `model`: Combined unique constraint (a manufacturer-model pair is unique)

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| has many | `Device` | one-to-many | Devices of this model |

---

### 3. Configuration Profile

A named collection of templates, variables, and interface mappings targeted at a specific device role. Provides baseline configuration defaults stored as Ansible `group_vars`.

> **Assignment model**: Profiles are not explicitly attached to devices. A device's effective profile is the *one active profile* whose `device_role` matches the device's NetBox role (implicit role-matching; at most one active profile per role). Per-device customization comes from device overrides (`host_vars`). The REST `assigned_profiles` field and preview "current assignment" derive from this rule.

**FR references**: FR-004, FR-007, FR-008, FR-016

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `name` | `String(128)` | Unique, not null, slug-safe | Local | Human-readable profile name |
| `device_role` | `String(128)` | Not null | Local | Target NetBox device role (e.g., `access-switch`) |
| `description` | `Text` | Nullable | Local | Profile purpose and scope |
| `git_path` | `String(512)` | Not null | Local | Path to `group_vars/<name>/` in Git repo |
| `is_active` | `Boolean` | Default `true` | Local | Whether the profile is available for deployment |
| `version` | `String(32)` | Default `1` | Local | Git commit hash or version tag (optimistic lock) |
| `created_by_id` | `Integer` | FK → `User.id`, not null | Local | Creator of the profile |
| `updated_by_id` | `Integer` | FK → `User.id`, nullable | Local | Last editor of the profile |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `name`: Alphanumeric, hyphens, underscores only; max 128 chars; used as Ansible group_vars directory name
- `device_role`: Must correspond to an existing NetBox device role (validated at sync time)
- `git_path`: Must match pattern `group_vars/.+`; must be unique across profiles
- `version`: Updated on every Git commit; used for optimistic locking on concurrent edits
- `is_active`: Inactive profiles cannot be targeted in new deployments (409), remain in history and deployment records, and can be re-activated when no other active profile occupies the role
- `device_role`: At most one profile with `is_active=true` per device role — a second active profile for the same role is rejected (409); this prevents conflicting profiles from targeting the same config sections

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| has many | `ConfigurationTemplate` | one-to-many | Template fragments in this profile |
| has many | `InterfaceTemplate` | one-to-many | Interface-level templates in this profile |
| has many | `ConfigurationVariable` | one-to-many | Group-level variables (group_vars) |
| has many | `DeploymentRecord` | one-to-many | Deployments that used this profile |

---

### 4. Configuration Template

A modular Jinja template fragment producing a self-contained configuration snippet (e.g., VLANs, routing, QoS). Snippets merge into device config rather than replacing it.

**FR references**: FR-005, FR-010

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `profile_id` | `Integer` | FK → `ConfigurationProfile.id`, not null | Local | Parent profile |
| `name` | `String(128)` | Not null | Local | Template fragment name (e.g., `vlans`, `routing`) |
| `display_order` | `Integer` | Not null; default 0 | Local | Render order within profile (lower = first) |
| `content` | `Text` | Not null | Local | Jinja2 template source code |
| `git_path` | `String(512)` | Not null | Local | Path to template file in Git repo |
| `config_section` | `String(64)` | Nullable | Local | NETCONF config section target (e.g., `native/vlan`) |
| `is_enabled` | `Boolean` | Default `true` | Local | Whether this template is active in renders |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `name`: Unique within a profile (no duplicate template names per profile)
- `content`: Must be valid Jinja2 syntax (validated on save); maximum 51,200 bytes per template; at most 20 templates per profile
- `content`: `{% include %}` references must resolve at save time; circular include chains are rejected with 400
- `config_section`: If provided, must be a valid NETCONF YANG path
- `display_order`: Must be unique within a profile; renumbered on reorder
- `git_path`: Must match pattern `templates/<profile_name>/<name>.j2`

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `ConfigurationProfile` | many-to-one | Parent profile collection |
| referenced by | `DeploymentRecord` | many-to-many | Which templates were included in a deployment |

---

### 5. Interface Template

A template applied to a set of physical interfaces on a device, defining per-interface configuration (access port, trunk, uplink settings).

**FR references**: FR-006

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `profile_id` | `Integer` | FK → `ConfigurationProfile.id`, not null | Local | Parent profile |
| `name` | `String(128)` | Not null | Local | Template name (e.g., `access-ports`, `uplinks`) |
| `interface_type` | `String(64)` | Not null | Local | Target interface type (e.g., `GigabitEthernet`, `TenGigabitEthernet`) |
| `interface_range` | `String(255)` | Not null | Local | Interface selection pattern (e.g., `1-48`, `1,3,5`, `all`) |
| `content` | `Text` | Not null | Local | Jinja2 template for per-interface config |
| `git_path` | `String(512)` | Not null | Local | Path to interface template in Git repo |
| `display_order` | `Integer` | Not null; default 0 | Local | Render order within profile |
| `is_enabled` | `Boolean` | Default `true` | Local | Whether this template is active |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `interface_type`: Must exist in the `interface_types` of at least one `DeviceType` compatible with the profile's `device_role`
- `interface_range`: Must be a valid interface range expression; validated against the device type's interface count for that type
- `name`: Unique within a profile
- `content`: Must be valid Jinja2 syntax; may reference `{{ interface_name }}` loop variable
- `interface_range` with value `all`: Applies to all interfaces of the specified type

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `ConfigurationProfile` | many-to-one | Parent profile collection |
| references | `DeviceType` | many-to-one (implicit) | Interface types validated against device type definitions |

---

### 6. Deployment Record

An immutable log entry capturing a configuration deployment event: who triggered it, the applied profile, target devices, timing, and outcome. **One record per deployment (batch)**; per-device outcomes are stored in `DeploymentDevice` (Section 6b).

**FR references**: FR-011, FR-017

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `profile_id` | `Integer` | FK → `ConfigurationProfile.id`, not null | Local | Profile deployed |
| `device_count` | `Integer` | Not null, ≥ 1 | Local | Number of target devices (see `DeploymentDevice`) |
| `user_id` | `Integer` | FK → `User.id`, not null | Local | Operator who triggered deployment |
| `status` | `String(32)` | Not null | Local | Deployment outcome |
| `git_commit_sha` | `String(40)` | Not null | Local | Git commit hash of the deployed config |
| `git_branch` | `String(128)` | Not null | Local | Git branch deployed from (e.g., `main`) |
| `preview_output` | `Text` | Nullable | Local | Generated config preview (for FR-009) |
| `error_message` | `Text` | Nullable | Local | Error details on failure; lists failed devices for partial failures |
| `config_diff` | `Text` | Nullable | Local | Aggregate sections added/modified/removed by merge across devices |
| `pipeline_url` | `String(512)` | Nullable | Local | Link to GitLab CI/CD pipeline |
| `pipeline_status` | `String(32)` | Nullable | Local | CI/CD pipeline status |
| `approved_by_id` | `Integer` | FK → `User.id`, nullable | Local | Admin who approved (if approval gate used) |
| `started_at` | `DateTime` | Not null | Local | Deployment start timestamp |
| `completed_at` | `DateTime` | Nullable | Local | Deployment completion timestamp |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |

#### Validation Rules

- `status`: Must be one of `pending`, `approved`, `running`, `success`, `failed`, `cancelled` (see state transitions)
- `git_commit_sha`: Must be a valid 40-character hex SHA
- Concurrency: a target device may have at most one deployment with status `pending`, `approved`, or `running`; a new deployment overlapping an in-flight device is rejected (409)
- Partial success: a multi-device deployment in which any device fails is recorded `failed`; per-device outcomes live in `DeploymentDevice` rows; `error_message` names the failed devices
- Retention: deployment records are kept indefinitely (immutable audit trail)
- `completed_at`: Must be ≥ `started_at`
- `error_message`: Required when `status` is `failed`
- `approved_by_id`: Required when `git_branch` is `main` (production branch requires approval)
- Record is immutable after creation (no updates; corrections are new records)

#### State Transitions (`status`)

```
pending → approved → running → success
    │            │           │
    │            │           └→ failed
    │            └→ cancelled
    └→ cancelled
```

| Transition | Trigger | Description |
|---|---|---|
| `pending` → `approved` | Admin approves MR | Merge request to production branch approved |
| `pending` → `cancelled` | User cancels | Operator aborts deployment before execution |
| `approved` → `running` | Pipeline starts | CI/CD job begins execution |
| `running` → `success` | All NETCONF commits succeed | Config applied atomically to all target devices |
| `running` → `failed` | NETCONF/pipeline/device error | Per-device atomic rollback; per-device outcomes recorded |

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `ConfigurationProfile` | many-to-one | Profile that was deployed |
| has many | `DeploymentDevice` | one-to-many | Per-device outcomes for this deployment |
| belongs to | `User` (operator) | many-to-one | Who triggered the deployment |
| belongs to | `User` (approver) | many-to-zero-or-one | Who approved (if applicable) |

---

### 6b. Deployment Device

Per-device outcome row for a multi-device deployment. Populated from the GitLab pipeline webhook's per-device results; lets a single `DeploymentRecord` represent a batch while retaining per-device success/failure, diffs, and errors.

**FR references**: FR-011

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `deployment_id` | `Integer` | FK → `DeploymentRecord.id`, not null | Local | Parent deployment |
| `device_id` | `Integer` | FK → `Device.id`, not null | Local | Target device |
| `status` | `String(16)` | Not null; enum `success`, `failed` | Local | Per-device outcome |
| `message` | `Text` | Nullable | Local | Error or success detail |
| `config_diff` | `Text` | Nullable | Local | Sections added/modified/removed on this device |
| `started_at` | `DateTime` | Not null | Local | Per-device start time |
| `completed_at` | `DateTime` | Nullable | Local | Per-device completion time |

#### Validation Rules

- `deployment_id` + `device_id`: Unique constraint (one outcome row per device per deployment)
- `status` = `failed`: `message` is required

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `DeploymentRecord` | many-to-one | Parent deployment |
| belongs to | `Device` | many-to-one | Target device |

---

### 7. Configuration Variable

Key-value data used in Jinja templates to generate device-specific configurations. Stored as static YAML files in Git: profile-level variables map to `group_vars`, device-level overrides map to `host_vars`.

**FR references**: FR-007, FR-008, FR-004b

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `scope` | `String(16)` | Not null; enum `profile`, `device` | Local | Variable scope level |
| `profile_id` | `Integer` | FK → `ConfigurationProfile.id`; required when scope=`profile` | Local | Parent profile for group_vars |
| `device_id` | `Integer` | FK → `Device.id`; required when scope=`device` | Local | Target device for host_vars |
| `key` | `String(255)` | Not null | Local | Variable name (Ansible variable identifier) |
| `value` | `Text` | Not null | Local | Variable value (YAML-serializable) |
| `value_type` | `String(16)` | Not null; enum `string`, `int`, `bool`, `list`, `dict` | Local | YAML type for proper serialization |
| `description` | `String(512)` | Nullable | Local | Variable purpose documentation |
| `git_path` | `String(512)` | Not null | Local | Path to YAML file in Git repo |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `scope` = `profile`: `profile_id` required, `device_id` must be null
- `scope` = `device`: `device_id` required, `profile_id` must be null
- `key`: Valid Ansible variable name (alphanumeric + underscores; no spaces)
- `value`: Must be valid YAML for the declared `value_type`
- `value_type` = `list`: `value` is a YAML sequence string
- `value_type` = `dict`: `value` is a YAML mapping string
- Precedence: `device` scope variables override `profile` scope on key collision at render time

#### Git Mapping

| Scope | Git path pattern | Ansible mapping |
|---|---|---|
| `profile` | `group_vars/<profile_name>/vars.yml` | Role-level defaults for all devices with this profile |
| `device` | `host_vars/<hostname>.yml` | Device-specific overrides; take precedence over group_vars |

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to (scope=profile) | `ConfigurationProfile` | many-to-one | Profile-level default variables |
| belongs to (scope=device) | `Device` | many-to-one | Device override variables |

---

### 8. ZTP Provision

Day-0 boot configuration and script for a device pending its initial deployment. Generated by the same Ansible role using a minimal variable set.

**FR references**: FR-014, FR-015

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `device_id` | `Integer` | FK → `Device.id`, unique, not null | Local | Target device for ZTP |
| `config_content` | `Text` | Not null | Local | Generated day-0 config text |
| `script_content` | `Text` | Not null | Local | ZTP boot script (e.g., `ztp.conf`) |
| `url` | `String(512)` | Not null | Local | HTTP URL where ZTP artifacts are hosted |
| `status` | `String(32)` | Not null; default `pending` | Local | Provisioning state |
| `is_meraki` | `Boolean` | Default `false` | Local | Whether ZTP targets Meraki cloud onboarding |
| `git_path` | `String(512)` | Not null | Local | Path to ZTP files in Git repo |
| `error_message` | `Text` | Nullable | Local | Failure details |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `config_content`: Must be valid IOS-XE boot config (or Meraki ZTP commands when `is_meraki`)
- `script_content`: Must be valid ZTP script format
- `url`: Must be a valid HTTP(S) URL; accessible from target device network
- `device_id`: Device must not already have a completed ZTP provision
- `status`: Must be one of allowed values

#### State Transitions (`status`)

```
pending → generated → delivered → onboarded
    │             │
    │             └→ failed
    │
    └→ cancelled
```

| Transition | Trigger | Description |
|---|---|---|
| `pending` → `generated` | UI action / API call | Day-0 config and script rendered from templates |
| `generated` → `delivered` | Device fetches ZTP URL | ZTP HTTP handler logs the artifact fetch |
| `delivered` → `onboarded` | Device reports successful boot | Device is up (Grafana); `Device.config_status` → `onboarded` |
| `generated`/`delivered` → `failed` | Operator marks failure | Boot failed (see failure policy); error logged |
| `pending` → `cancelled` | User cancels | Provision aborted before generation |

**Boot failure policy**: No server-side auto-fail timeout — the Cisco ZTP loader retries fetches automatically. A device that cannot reach the ZTP server leaves the provision in `generated`/`delivered`; fetch attempts are logged in the application log, and the operator marks the provision `failed` (with error detail) or `cancelled`.

**Artifact cleanup**: A daily job removes served artifacts for provisions in terminal states (`onboarded`, `failed`, `cancelled`) older than 30 days; DB records are retained indefinitely.

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `Device` | many-to-one | Target device for ZTP |

---

### 9. Device Override

Per-device configuration variables and template overrides that supplement a device's inherited role profile. Overrides take precedence over profile defaults at render time and map to Ansible `host_vars`.

**FR references**: FR-004b, FR-008

> **Note**: `DeviceOverride` is **not a separate table**. It is a logical view over `ConfigurationVariable` rows with `scope='device'` (one row per variable; `profile_id` is NULL per Section 7). The API exposes a device's full override set as a single object (`GET/PUT /api/devices/{id}/overrides`); a PUT replaces the whole row set. There is no per-profile context in storage — precedence is scope-based (device overrides profile on key collision at render time).
>
> Conceptual attributes (not separately persisted): the target device, the set of override key-values, and the git path `host_vars/<hostname>.yml` (see Git Mapping in Section 7).

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| belongs to | `Device` | many-to-one | Device receiving overrides (via `ConfigurationVariable.device_id`) |

---

### 10. Service Credential

Encrypted backend service authentication token with `.env` fallback support.

**FR references**: FR-016, FR-017 (system integration)

#### Fields

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Internal primary key |
| `service_name` | `String(64)` | Unique, not null | Service identifier (`netbox`, `gitlab`, `grafana`, `meraki`) |
| `token_encrypted` | `Text` | Not null | Fernet-encrypted API token (cryptography library) |
| `base_url` | `String(512)` | Nullable | Service base URL override |
| `env_key` | `String(128)` | Not null | Corresponding `.env` variable name (fallback) |
| `is_active` | `Boolean` | Default `true` | Whether this credential is current |
| `rotated_at` | `DateTime` | Nullable | Last rotation timestamp |
| `created_at` | `DateTime` | Not null, server default | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Last modification timestamp |

#### Validation Rules

- `service_name`: Must be one of `netbox`, `gitlab`, `grafana`, `meraki`
- `token_encrypted`: Encrypted via Fernet (cryptography library); key from `ENCRYPTION_KEY` env var
- `env_key`: Maps to `.env` variable (e.g., `NETBOX_TOKEN`, `GITLAB_TOKEN`)
- At least one Admin user must exist before credential rotation is allowed

#### Resolution Order

1. Active DB credential (`is_active=true`) — preferred source
2. `.env` variable (`env_key`) — fallback for initial bootstrap
3. Error if neither available — service marked as unreachable

#### Relationships

None (standalone lookup table keyed by `service_name`).

---

### 11. User

Authenticated platform user with an assigned role.

**FR references**: FR-018b

#### Fields

| Field | Type | Constraints | Source | Description |
|---|---|---|---|---|
| `id` | `Integer` | PK, auto-increment | Local | Internal primary key |
| `username` | `String(128)` | Unique, not null | Local | Login username |
| `email` | `String(255)` | Unique, not null | Local | User email address |
| `password_hash` | `String(255)` | Not null | Local | Bcrypt-hashed password |
| `role` | `String(32)` | Not null; enum `viewer`, `editor`, `admin` | Local | Permission level |
| `is_active` | `Boolean` | Default `true` | Local | Account active status |
| `last_login` | `DateTime` | Nullable | Local | Last successful login timestamp |
| `created_at` | `DateTime` | Not null, server default | Local | Record creation timestamp |
| `updated_at` | `DateTime` | Not null, auto-update | Local | Last modification timestamp |

#### Validation Rules

- `username`: Alphanumeric and underscores; 3–128 chars
- `email`: Valid email format
- `password_hash`: Must be valid bcrypt hash (min 60 chars)
- `role`: Must be one of `viewer`, `editor`, `admin`
- At least one `admin` user must exist (cannot deactivate last admin)
- Password lifecycle: self-service change (`PUT /api/auth/password`) and admin reset (`PUT /api/users/{user_id}/password`); no lockout policy in v1 (local auth, 3-10 internal users; SSO deferred)

#### Permission Matrix

| Action | Viewer | Editor | Admin |
|---|---|---|---|
| View inventory | ✓ | ✓ | ✓ |
| View monitoring | ✓ | ✓ | ✓ |
| Create/edit profiles | ✗ | ✓ | ✓ |
| Create/edit templates | ✗ | ✓ | ✓ |
| Create/edit variables | ✗ | ✓ | ✓ |
| Initiate deployment | ✗ | ✓ | ✓ |
| Approve deployment | ✗ | ✗ | ✓ |
| Manage users and roles | ✗ | ✗ | ✓ |
| System configuration | ✗ | ✗ | ✓ |

#### Relationships

| Relationship | Target | Multiplicity | Description |
|---|---|---|---|
| has many | `ConfigurationProfile` (created_by) | one-to-many | Profiles created by this user |
| has many | `ConfigurationProfile` (updated_by) | one-to-many | Profiles last edited by this user |
| has many | `DeploymentRecord` (operator) | one-to-many | Deployments triggered by this user |
| has many | `DeploymentRecord` (approver) | one-to-many | Deployments approved by this user |

---

## Data Flow

### Service Credential Resolution

```
┌─────────────┐    1. Lookup active credential    ┌──────────────────┐
│  Service     │─────────────────────────────────→│ ServiceCredential│
│  (e.g. API)  │                                  │ (DB, encrypted)  │
└─────────────┘                                  └────────┬─────────┘
                                                          │
                                    not found ────────────┤
                                                          │ found
                                          ┌───────────────┤
                                          │               │
                                          ▼               ▼
                                  ┌──────────────┐  ┌──────────────┐
                                  │  .env var    │  │  Decrypt     │
                                  │  (fallback)  │  │  (Fernet)    │
                                  └──────────────┘  └──────┬───────┘
                                                           │
                                                           ▼
                                                  ┌────────────────┐
                                                  │  Use token for │
                                                  │  API call      │
                                                  └────────────────┘
```

### Ansible Bind-Mount Model

```
┌─────────────────────────────────────────────────────────────────┐
│                        Monorepo (ansible/)                      │
│                                                                 │
│  ├── ansible.cfg                                               │
│  ├── site.yml, preview.yml, ztp.yml                            │
│  ├── group_vars/<role>/vars.yml    ← Profile variables         │
│  ├── host_vars/<host>/vars.yml     ← Device overrides          │
│  └── roles/iosxe/templates/*.j2   ← Jinja2 config snippets    │
└──────────┬──────────────────────────────────────┬──────────────┘
           │ bind-mount (rw)                      │ bind-mount (ro)
           ▼                                      ▼
┌──────────────────┐                  ┌────────────────────────┐
│  Flask App       │                  │  GitLab Runner Jobs    │
│  (preview.yml)   │                  │  (site.yml, deploy)    │
│  - Writes vars   │                  │  - Reads vars          │
│  - Runs preview  │                  │  - Pushes via NETCONF  │
└──────────────────┘                  └────────────────────────┘
```

### Profile-to-Device Configuration Deployment

```
┌─────────────────────┐
│  Configuration      │
│  Profile            │
│  (group_vars/)      │
└────────┬────────────┘
         │ 1. Profile assigned to device role
         ▼
┌─────────────────────┐     ┌─────────────────────┐
│  Configuration      │     │  Device Override    │
│  Templates (.j2)    │     │  (host_vars/)       │
└────────┬────────────┘     └────────┬────────────┘
         │ 2. Render at deploy time  │
         └──────────┬────────────────┘
                    │ 3. Merge variables (host_vars > group_vars)
                    ▼
           ┌──────────────────┐
           │  Jinja2 Render   │
           │  (Ansible role)  │
           └────────┬─────────┘
                    │ 4. Preview generated config
                    ▼
           ┌──────────────────┐
           │  Config Preview  │◄─── User reviews (FR-009)
           └────────┬─────────┘
                    │ 5. User confirms deployment
                    ▼
           ┌──────────────────┐
           │  Git Commit      │─── Auto-commit to working branch
           └────────┬─────────┘
                    │ 6. MR to production branch
                    ▼
           ┌──────────────────┐
           │  Admin Approval  │─── FR-017: approval gate
           └────────┬─────────┘
                    │ 7. Merge to main → CI/CD pipeline
                    ▼
           ┌──────────────────┐
           │  NETCONF Commit  │─── Atomic apply or rollback (FR-010)
           └────────┬─────────┘
                    │ 8. Record result
                    ▼
           ┌──────────────────┐
           │  Deployment      │─── Immutable record (FR-011)
           │  Record          │
           └──────────────────┘
```

### Inventory Sync Flow

```
┌─────────────┐     periodic sync      ┌──────────────────┐
│    NetBox   │────────────────────────→│  Device records  │
│  (external) │                         │  (SQLAlchemy)    │
└─────────────┘                         └────────┬─────────┘
                                                 │
                    device_type lookup ──────────┘
                                                 ▼
                                          ┌──────────────────┐
                                          │  DeviceType      │
                                          │  records         │
                                          └──────────────────┘
```

NetBox is the source of truth for immutable attributes. The sync job:
1. Pulls device list via NetBox API
2. Upserts `Device` records (matches on `netbox_id`)
3. Populates `DeviceType` records from NetBox device types
4. Updates `last_netbox_sync` timestamp
5. Flags devices missing from NetBox as `stale` (excluded from new deployments; history retained)
6. Hostname collisions: sync upserts by `netbox_id`, not hostname. If two NetBox devices share a hostname (violating the local uniqueness constraint), the later record is skipped and the conflict is logged and surfaced in a sync-report badge; the operator renames one device in NetBox and re-syncs

### ZTP Onboarding Flow

```
┌─────────────┐    1. Create ZTP provision    ┌──────────────────┐
│   Device    │───────────────────────────────→│  ZTPProvision   │
│ (pending)   │                               │  (pending)      │
└─────────────┘                               └────────┬─────────┘
                                                       │ 2. Render day-0 config
                                                       ▼
                                              ┌──────────────────┐
                                              │  Templates +     │
                                              │  Minimal vars    │
                                              └────────┬─────────┘
                                                       │ 3. Host artifacts
                                                       ▼
                                              ┌──────────────────┐
                                              │  ZTPProvision    │
                                              │  (generated)     │
                                              │  → HTTP URL      │
                                              └────────┬─────────┘
                                                       │ 4. Device boots, fetches URL
                                                       ▼
                                              ┌──────────────────┐
                                              │  ZTPProvision    │
                                              │  (completed)     │
                                              └────────┬─────────┘
                                                       │ 5. Update device
                                                       ▼
┌─────────────┐    ← status = onboarded         ┌──────────────────┐
│   Device    │                                  │  Deployment      │
│ (onboarded) │                                  │  Record          │
└─────────────┘                                  └──────────────────┘
```

---

## Persistence Layers

### SQLAlchemy ORM (SQLite)

Stores relational metadata: devices, profiles, templates, deployment records, users, service credentials, and variable references. Used for querying, filtering, and displaying data in the web UI. Initial deployment uses SQLite; migration path to PostgreSQL documented in research.

### Service Credential Store

The `ServiceCredential` model stores encrypted API tokens for backend services (NetBox, GitLab, Grafana, Meraki). Tokens are encrypted using Fernet (cryptography library) with the encryption key sourced from `ENCRYPTION_KEY` in the environment. Resolution order: active DB credential → `.env` fallback → error.

### Git Repository

Stores configuration content as static files. The Git repo is the authoritative persistence layer for all configuration data:

```
repo-root/
├── group_vars/
│   ├── <profile_name>/
│   │   └── vars.yml          # ConfigurationVariable (scope=profile)
│   └── all/
│       └── vars.yml          # Global defaults
├── host_vars/
│   └── <hostname>.yml        # ConfigurationVariable (scope=device) / DeviceOverride
├── templates/
│   ├── <profile_name>/
│   │   ├── <template_name>.j2   # ConfigurationTemplate
│   │   └── interfaces/
│   │       └── <iface_template>.j2  # InterfaceTemplate
│   └── ztp/
│       └── bootstrap.j2        # ZTP provision templates
├── roles/
│   └── network_config/
│       └── tasks/              # Single Ansible role with common boilerplate
├── playbooks/
│   ├── deploy.yml              # Main deployment playbook
│   └── ztp_bootstrap.yml       # Separate minimal ZTP playbook
└── ztp_artifacts/
    └── <hostname>/
        ├── config.textf       # Day-0 config
        └── ztp.conf            # ZTP script
```

### Git Branch Strategy

| Branch | Purpose | Protection |
|---|---|---|
| `main` | Production configuration | Requires admin approval to merge (name configurable: `git_production_branch`) |
| `working` | Shared auto-commit working branch | Auto-committed by web UI on edits (name configurable: `git_working_branch`) |

No `staging` or per-user branches in v1: concurrent auto-commits to the shared `working` branch are serialized via commit + rebase; failures surface as 409 to the editing user (see GitLab contract).

---

## NetBox Integration

NetBox provides the external source of truth for immutable inventory data. The integration is read-only from StackHive's perspective.

| StackHive Field | NetBox API Endpoint | Sync Direction |
|---|---|---|
| `Device.hostname` | `/api/dcim/devices/` | NetBox → StackHive |
| `Device.serial_number` | `/api/dcim/devices/` | NetBox → StackHive |
| `Device.role` | `/api/dcim/device-roles/` | NetBox → StackHive |
| `Device.site` | `/api/dcim/sites/` | NetBox → StackHive |
| `Device.device_type_id` | `/api/dcim/device-types/` | NetBox → StackHive |
| `DeviceType.manufacturer` | `/api/dcim/manufacturers/` | NetBox → StackHive |
| `DeviceType.interface_types` | `/api/dcim/device-types/` (interfaces) | NetBox → StackHive |

NetBox device roles are used to filter and group devices in the UI (FR-002) and to associate profiles with device groups (FR-004). The `device_role` field on `ConfigurationProfile` must reference a role that exists in NetBox.

---

## Indexes

Recommended database indexes for query performance:

| Table | Columns | Purpose |
|---|---|---|
| `device` | `(role)` | Filter devices by role (FR-002) |
| `device` | `(site)` | Filter devices by site (FR-002) |
| `device` | `(config_status)` | Dashboard health summary (FR-018d) |
| `device` | `(cloud_managed)` | Exclude cloud devices from deployment |
| `configuration_profile` | `(device_role)` | Find profile for a device role |
| `configuration_template` | `(profile_id, display_order)` | Ordered template listing |
| `interface_template` | `(profile_id, display_order)` | Ordered interface template listing |
| `deployment_device` | `(device_id)` | Per-device deployment lookup across batches |
| `deployment_record` | `(started_at DESC)` | Recent-deployments ordering |
| `deployment_record` | `(status)` | Dashboard pending approvals |
| `configuration_variable` | `(scope, profile_id, key)` | Variable lookup by scope |
| `configuration_variable` | `(scope, device_id, key)` | Device override lookup |
| `ztp_provision` | `(device_id)` | ZTP status per device |
| `user` | `(username)` | Login lookup |
| `user` | `(role)` | Permission checks |
| `service_credential` | `(service_name)` | Token lookup by service |
| `service_credential` | `(is_active, service_name)` | Active credential fast path |
