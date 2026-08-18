# REST API Contract — Network Operations Dashboard

Flask-based REST API exposed by the dashboard application. All endpoints return JSON unless otherwise noted.

## Authentication

Endpoints require an authenticated session cookie (`session`) unless marked otherwise. Role requirements are noted per endpoint.

| Role    | Permissions                                                                 |
|---------|-----------------------------------------------------------------------------|
| Viewer  | Read-only access to inventory, monitoring, deployment history, onboarding   |
| Editor  | Viewer + create/edit profiles, templates, variables, trigger deployments    |
| Admin   | Editor + approve deployments, manage users, system configuration            |

---

## Dashboard

### GET /api/dashboard

Summary overview returned on the landing page.

**Role**: Viewer

**Query parameters**: none

**Response 200**:
| Field              | Type       | Description                                  |
|--------------------|------------|----------------------------------------------|
| device_health      | object     | `{ up: int, down: int, total: int }`         |
| recent_deployments | array[obj] | Last 5 deployment records                    |
| pending_approvals  | array[obj] | Merge requests awaiting admin approval       |

Each item in `recent_deployments` and `pending_approvals` follows the Deployment Record schema from the Deployments section.

---

## Inventory

### GET /api/devices

List all managed network devices.

**Role**: Viewer

**Query parameters**:
| Parameter | Type   | Required | Description                        |
|-----------|--------|----------|------------------------------------|
| role      | string | No       | Filter by NetBox device role       |
| type      | string | No       | Filter by device type/model        |
| site      | string | No       | Filter by site/location tag        |
| status    | string | No       | Filter by status: `up`, `down`, `unknown` |
| search    | string | No       | Free-text search across hostname, IP, serial |
| page      | int    | No       | Page number (default: 1)           |
| per_page  | int    | No       | Results per page (default: 25, max: 100) |

**Response 200**:
| Field          | Type      | Description                   |
|----------------|-----------|-------------------------------|
| devices        | array[obj] | Paginated device list       |
| total          | int       | Total matching device count   |
| page           | int       | Current page number           |
| per_page       | int       | Results per page              |

**Device object**:
| Field           | Type     | Description                              |
|-----------------|----------|------------------------------------------|
| id              | string   | NetBox device ID                         |
| hostname        | string   | Device FQDN                              |
| ip_address      | string   | Management IP address                    |
| role            | string   | NetBox device role name                  |
| device_type     | string   | Device model (e.g., C9300-48P)           |
| serial          | string   | Serial number                            |
| status          | string   | `up`, `down`, `unknown`                  |
| site            | string   | Site/location name                       |
| cloud_managed   | bool     | True if device is Meraki cloud-managed   |
| last_deployment | string   | ISO 8601 timestamp of last deployment, or null |
| tags            | array[str]| NetBox tags attached to the device       |

**Error responses**:
| Status | Description            |
|--------|------------------------|
| 401    | Not authenticated      |
| 503    | NetBox inventory unavailable |

### GET /api/devices/{id}

Get detail for a single device.

**Role**: Viewer

**Path parameters**:
| Parameter | Type   | Description      |
|-----------|--------|------------------|
| id        | string | NetBox device ID |

**Response 200** — Device detail object:
| Field              | Type       | Description                                |
|--------------------|------------|--------------------------------------------|
| id                 | string     | NetBox device ID                           |
| hostname           | string     | Device FQDN                                |
| ip_address         | string     | Management IP address                      |
| role               | string     | NetBox device role                         |
| device_type        | object     | `{ model: str, manufacturer: str, parts: array[obj] }` |
| serial             | string     | Serial number                              |
| mac_address        | string     | Base MAC address                           |
| status             | string     | `up`, `down`, `unknown`                    |
| site               | string     | Site name                                  |
| cloud_managed      | bool       | Meraki cloud-managed flag                  |
| last_deployment    | string     | ISO 8601 timestamp or null                 |
| tags               | array[str] | NetBox tags                                |
| interfaces         | array[obj] | Physical interface list (see below)        |
| assigned_profiles  | array[obj] | Profiles applied to this device            |
| overrides          | object     | Per-device variable overrides              |
| deployment_history | array[obj] | Recent deployment records for this device  |

**Interface object** (within `interfaces`):
| Field       | Type    | Description                          |
|-------------|---------|--------------------------------------|
| name        | string  | Interface name (e.g., GigabitEthernet1/0/1) |
| type        | string  | Interface type (ethernet, management, etc.) |
| slot        | int     | Slot number                          |
| port        | int     | Port number                          |
| enabled     | bool    | Whether interface is administratively up |
| description | string  | Configured description               |

**Error responses**:
| Status | Description              |
|--------|--------------------------|
| 401    | Not authenticated        |
| 404    | Device not found         |
| 503    | NetBox inventory unavailable |

---

## Profiles

Profiles are configuration collections targeted at a device role. Each profile maps to Ansible `group_vars`.

### GET /api/profiles

List all configuration profiles.

**Role**: Viewer

**Query parameters**:
| Parameter | Type   | Required | Description                      |
|-----------|--------|----------|----------------------------------|
| role      | string | No       | Filter by target device role     |
| search    | string | No       | Search by profile name           |

**Response 200**:
| Field     | Type      | Description              |
|-----------|-----------|--------------------------|
| profiles  | array[obj] | Profile list            |

**Profile list object**:
| Field          | Type      | Description                          |
|----------------|-----------|--------------------------------------|
| id             | string    | Profile UUID                         |
| name           | string    | Profile name                         |
| device_role    | string    | Target NetBox device role            |
| templates      | int       | Number of templates in the profile   |
| variables      | int       | Number of variables in the profile   |
| updated_at     | string    | ISO 8601 last-modified timestamp     |
| updated_by     | string    | Username of last modifier            |

### GET /api/profiles/{id}

Get a single profile with full detail.

**Role**: Viewer

**Response 200** — Full profile object:
| Field          | Type       | Description                            |
|----------------|------------|----------------------------------------|
| id             | string     | Profile UUID                           |
| name           | string     | Profile name                           |
| device_role    | string     | Target NetBox device role              |
| templates      | array[obj] | Template objects (see below)           |
| variables      | object     | Key-value variable map                 |
| interface_mappings | array[obj] | Interface template assignments       |
| updated_at     | string     | ISO 8601 last-modified timestamp       |
| updated_by     | string     | Username of last modifier              |

**Template object**:
| Field       | Type    | Description                              |
|-------------|---------|------------------------------------------|
| id          | string  | Template UUID                            |
| name        | string  | Template section name (e.g., "vlans")    |
| content     | string  | Jinja2 template source                   |
| order       | int     | Render order priority                    |

**Interface mapping object**:
| Field         | Type      | Description                              |
|---------------|-----------|------------------------------------------|
| template_id   | string    | Referenced template UUID                 |
| interface_names | array[str] | Interfaces this template applies to    |

**Error responses**:
| Status | Description         |
|--------|---------------------|
| 401    | Not authenticated   |
| 404    | Profile not found   |

### POST /api/profiles

Create a new configuration profile.

**Role**: Editor

**Request body**:
| Field          | Type    | Required | Description                        |
|----------------|---------|----------|------------------------------------|
| name           | string  | Yes      | Profile name                       |
| device_role    | string  | Yes      | Target NetBox device role          |
| templates      | array   | No       | Initial templates (empty list ok)  |
| variables      | object  | No       | Initial variables (empty object ok)|

Each item in `templates`:
| Field   | Type   | Required | Description              |
|---------|--------|----------|--------------------------|
| name    | string | Yes      | Template section name    |
| content | string | Yes      | Jinja2 template source   |
| order   | int    | No       | Render order (default: 0)|

**Response 201** — Created profile object (same schema as GET /api/profiles/{id}).

**Error responses**:
| Status | Description                    |
|--------|--------------------------------|
| 400    | Invalid request body           |
| 401    | Not authenticated              |
| 403    | Insufficient role (need Editor)|
| 409    | Profile name already exists    |

### PUT /api/profiles/{id}

Update an existing profile.

**Role**: Editor

**Path parameters**: `id` — Profile UUID

**Request body**:
| Field          | Type     | Required | Description                          |
|----------------|----------|----------|--------------------------------------|
| name           | string   | No       | New profile name                     |
| device_role    | string   | No       | New target device role               |
| templates      | array    | No       | Full replacement of templates list   |
| variables      | object   | No       | Full replacement of variables        |
| interface_mappings | array | No     | Full replacement of interface mappings |

**Response 200** — Updated profile object.

**Error responses**:
| Status | Description                         |
|--------|-------------------------------------|
| 400    | Invalid request body                |
| 401    | Not authenticated                   |
| 403    | Insufficient role                   |
| 404    | Profile not found                   |
| 409    | Git merge conflict on underlying files |

### DELETE /api/profiles/{id}

Delete a configuration profile.

**Role**: Editor

**Response 204** — No content.

**Error responses**:
| Status | Description              |
|--------|--------------------------|
| 401    | Not authenticated        |
| 403    | Insufficient role        |
| 404    | Profile not found        |

---

## Device Overrides

Per-device variable overrides that supplement profile defaults. Maps to Ansible `host_vars`.

### GET /api/devices/{device_id}/overrides

Get overrides for a device.

**Role**: Viewer

**Response 200**:
| Field       | Type   | Description                    |
|-------------|--------|--------------------------------|
| variables   | object | Key-value override variables   |
| updated_at  | string | ISO 8601 last-modified time    |

### PUT /api/devices/{device_id}/overrides

Create or replace overrides for a device.

**Role**: Editor

**Request body**:
| Field     | Type   | Required | Description                    |
|-----------|--------|----------|--------------------------------|
| variables | object | Yes      | Key-value override variables   |

**Response 200** — Updated override object.

**Error responses**:
| Status | Description                  |
|--------|------------------------------|
| 400    | Invalid request body         |
| 401    | Not authenticated            |
| 403    | Insufficient role            |
| 404    | Device not found             |

---

## Deployments

### POST /api/deployments/preview

Generate a configuration preview for a device without committing.

**Role**: Viewer

**Request body**:
| Field         | Type    | Required | Description                     |
|---------------|---------|----------|---------------------------------|
| device_id     | string  | Yes      | NetBox device ID                |
| profile_id    | string  | No       | Specific profile to preview; null uses current assignment |

**Response 200**:
| Field          | Type    | Description                               |
|----------------|---------|-------------------------------------------|
| device_id      | string  | NetBox device ID                          |
| hostname       | string  | Device hostname                           |
| profile_name   | string  | Profile used for rendering                |
| config         | string  | Full rendered configuration output        |
| snippets       | array[obj]| Individual snippet breakdown (see below) |
| variables_used | object  | Effective variables after merge (profile + overrides) |

**Snippet object**:
| Field   | Type   | Description                          |
|---------|--------|--------------------------------------|
| name    | string | Template section name                |
| content | string | Rendered config snippet              |

**Error responses**:
| Status | Description                   |
|--------|-------------------------------|
| 400    | Invalid request body          |
| 401    | Not authenticated             |
| 404    | Device or profile not found   |

### POST /api/deployments

Trigger a configuration deployment to one or more devices.

**Role**: Editor

**Request body**:
| Field         | Type     | Required | Description                              |
|---------------|----------|----------|------------------------------------------|
| device_ids    | array[str]| Yes     | Target NetBox device IDs                 |
| profile_id    | string   | Yes      | Profile to deploy                        |
| message       | string   | No       | Descriptive commit message               |

**Response 202** — Accepted deployment:
| Field           | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| deployment_id   | string  | Deployment record UUID                   |
| status          | string  | `pending`, `running`, `success`, `failed`|
| device_ids      | array[str]| Target device IDs                      |
| profile_id      | string  | Deployed profile UUID                    |
| triggered_by    | string  | Username                                 |
| triggered_at    | string  | ISO 8601 timestamp                       |
| git_commit_sha  | string  | GitLab commit SHA (once auto-committed)  |
| pipeline_id     | int     | GitLab pipeline ID (once triggered)      |

**Error responses**:
| Status | Description                      |
|--------|----------------------------------|
| 400    | Invalid request body             |
| 401    | Not authenticated                |
| 403    | Insufficient role                |
| 409    | Device is cloud-managed (Meraki) |
| 422    | Validation failed (e.g., device unreachable) |

### GET /api/deployments

List deployment history.

**Role**: Viewer

**Query parameters**:
| Parameter  | Type   | Required | Description                       |
|------------|--------|----------|-----------------------------------|
| device_id  | string | No       | Filter by device                  |
| profile_id | string | No       | Filter by profile                 |
| status     | string | No       | Filter by result: `success`, `failed` |
| page       | int    | No       | Page number                       |
| per_page   | int    | No       | Results per page                  |

**Response 200**:
| Field            | Type      | Description                  |
|------------------|-----------|------------------------------|
| deployments      | array[obj] | Deployment records          |
| total            | int       | Total count                  |
| page             | int       | Current page                 |

**Deployment record object**:
| Field             | Type     | Description                             |
|-------------------|----------|-----------------------------------------|
| deployment_id     | string   | UUID                                    |
| status            | string   | `pending`, `running`, `success`, `failed`|
| device_ids        | array[str]| Target devices                         |
| profile_id        | string   | Profile UUID                            |
| profile_name      | string   | Profile name                            |
| triggered_by      | string   | Username                                |
| triggered_at      | string   | ISO 8601 timestamp                      |
| completed_at      | string   | ISO 8601 timestamp or null              |
| git_commit_sha    | string   | GitLab commit SHA                       |
| pipeline_id       | int      | GitLab pipeline ID                      |
| message           | string   | Deployment result/error message         |
| config_diff       | string   | Diff of config sections added/modified/removed |

### GET /api/deployments/{deployment_id}

Get a single deployment record.

**Role**: Viewer

**Response 200** — Deployment record object (same schema as above).

**Error responses**:
| Status | Description               |
|--------|---------------------------|
| 404    | Deployment not found      |

### POST /api/deployments/{deployment_id}/approve

Approve a deployment (merge request merge). Admin-only gate.

**Role**: Admin

**Response 200**:
| Field      | Type    | Description                     |
|------------|---------|---------------------------------|
| status     | string  | `approved`                      |
| pipeline_id| int     | Triggered GitLab pipeline ID    |

**Error responses**:
| Status | Description                    |
|--------|--------------------------------|
| 401    | Not authenticated              |
| 403    | Insufficient role (need Admin) |
| 404    | Deployment not found           |
| 409    | Deployment already approved    |

---

## Monitoring

### GET /api/monitoring/devices

Get device status summary sourced from Grafana.

**Role**: Viewer

**Query parameters**:
| Parameter | Type   | Required | Description                |
|-----------|--------|----------|----------------------------|
| role      | string | No       | Filter by device role      |
| site      | string | No       | Filter by site             |

**Response 200**:
| Field        | Type      | Description                    |
|--------------|-----------|--------------------------------|
| devices      | array[obj] | Device status entries         |

**Device status entry**:
| Field         | Type    | Description                            |
|---------------|---------|----------------------------------------|
| hostname      | string  | Device hostname                        |
| ip_address    | string  | Management IP                          |
| status        | string  | `up`, `down`, `unknown`                |
| last_check    | string  | ISO 8601 timestamp of last status check|
| grafana_url   | string  | Deep-link URL to device Grafana dashboard |

**Error responses**:
| Status | Description                  |
|--------|------------------------------|
| 401    | Not authenticated            |
| 502    | Grafana API unavailable      |

### GET /api/monitoring/infrastructure

Get infrastructure service health.

**Role**: Viewer

**Response 200**:
| Field               | Type      | Description                         |
|---------------------|-----------|-------------------------------------|
| services            | array[obj] | Service health entries             |

**Service health entry**:
| Field         | Type    | Description                           |
|---------------|---------|---------------------------------------|
| name          | string  | Service name (e.g., `netbox`, `gitlab`) |
| status        | string  | `healthy`, `degraded`, `down`         |
| last_check    | string  | ISO 8601 timestamp                    |
| grafana_url   | string  | Deep-link to infrastructure dashboard |

---

## Onboarding

### GET /api/onboarding/ztp

List devices pending ZTP provisioning.

**Role**: Viewer

**Response 200**:
| Field       | Type      | Description                    |
|-------------|-----------|--------------------------------|
| devices     | array[obj] | Pending ZTP device entries   |

**Pending ZTP entry**:
| Field         | Type    | Description                           |
|---------------|---------|---------------------------------------|
| device_id     | string  | NetBox device ID                      |
| hostname      | string  | Planned hostname                      |
| serial        | string  | Device serial number                  |
| ztp_url       | string  | URL to ZTP boot script                |
| config_url    | string  | URL to day-0 configuration            |
| status        | string  | `pending`, `provisioned`, `onboarded` |
| is_meraki     | bool    | Whether this is a Meraki onboarding   |

### POST /api/onboarding/ztp

Create a ZTP provisioning record for a device.

**Role**: Editor

**Request body**:
| Field         | Type    | Required | Description                          |
|---------------|---------|----------|--------------------------------------|
| device_id     | string  | Yes      | NetBox device ID                     |
| serial        | string  | Yes      | Device serial number                 |
| hostname      | string  | Yes      | Planned hostname                     |
| profile_id    | string  | Yes      | Profile to use for day-0 config      |
| is_meraki     | bool    | No       | Flag for Meraki onboarding (default: false) |

**Response 201**:
| Field         | Type    | Description                         |
|---------------|---------|-------------------------------------|
| device_id     | string  | NetBox device ID                    |
| serial        | string  | Serial number                       |
| ztp_url       | string  | Hosted ZTP script URL               |
| config_url    | string  | Hosted day-0 config URL             |
| status        | string  | `pending`                           |

### POST /api/onboarding/meraki

Initiate Meraki cloud onboarding for a device.

**Role**: Editor

**Request body**:
| Field        | Type    | Required | Description                        |
|--------------|---------|----------|------------------------------------|
| device_id    | string  | Yes      | NetBox device ID                   |
| serial       | string  | Yes      | Device serial number               |
| hostname     | string  | Yes      | Planned hostname                   |
| network_id   | string  | Yes      | Target Meraki network ID           |
| dashboard_url| string  | No       | Meraki dashboard URL override      |

**Response 201**:
| Field         | Type    | Description                          |
|---------------|---------|--------------------------------------|
| device_id     | string  | NetBox device ID                     |
| serial        | string  | Serial number                        |
| ztp_url       | string  | ZTP script URL with Meraki commands  |
| config_url    | string  | Day-0 config URL with Meraki API cmds|
| meraki_network_id | string | Target Meraki network             |
| status        | string  | `pending`                            |

---

## Authentication

### POST /api/auth/login

Authenticate a user.

**Role**: None (public)

**Request body**:
| Field    | Type   | Required | Description       |
|----------|--------|----------|-------------------|
| username | string | Yes      | Username          |
| password | string | Yes      | Plaintext password|

**Response 200**:
| Field    | Type    | Description                 |
|----------|---------|-----------------------------|
| user_id  | string  | User UUID                   |
| username | string  | Username                    |
| role     | string  | `viewer`, `editor`, `admin` |

Sets session cookie on success.

**Error responses**:
| Status | Description             |
|--------|-------------------------|
| 400    | Missing credentials     |
| 401    | Invalid credentials     |

### POST /api/auth/logout

End the current session.

**Role**: None (any authenticated user)

**Response 204** — No content. Clears session cookie.

### GET /api/auth/me

Get the current authenticated user.

**Role**: None (any authenticated user)

**Response 200**:
| Field    | Type    | Description                |
|----------|---------|----------------------------|
| user_id  | string  | User UUID                  |
| username | string  | Username                   |
| role     | string  | `viewer`, `editor`, `admin`|

---

## Users (Admin Only)

### GET /api/users

List all users.

**Role**: Admin

**Response 200**:
| Field  | Type      | Description     |
|--------|-----------|-----------------|
| users  | array[obj] | User objects  |

**User object**:
| Field    | Type    | Description                |
|----------|---------|----------------------------|
| user_id  | string  | User UUID                  |
| username | string  | Username                   |
| role     | string  | `viewer`, `editor`, `admin`|
| created_at | string | ISO 8601 creation time  |

### POST /api/users

Create a new user.

**Role**: Admin

**Request body**:
| Field    | Type    | Required | Description                     |
|----------|---------|----------|---------------------------------|
| username | string  | Yes      | Username                        |
| password | string  | Yes      | Initial password                |
| role     | string  | Yes      | `viewer`, `editor`, `admin`     |

**Response 201** — Created user object.

**Error responses**:
| Status | Description                |
|--------|----------------------------|
| 400    | Invalid request body       |
| 403    | Insufficient role          |
| 409    | Username already exists    |

### PUT /api/users/{user_id}

Update a user.

**Role**: Admin

**Request body**:
| Field  | Type   | Required | Description                      |
|--------|--------|----------|----------------------------------|
| role   | string | No       | New role                         |

**Response 200** — Updated user object.

### DELETE /api/users/{user_id}

Delete a user.

**Role**: Admin

**Response 204** — No content.

**Error responses**:
| Status | Description              |
|--------|--------------------------|
| 403    | Insufficient role        |
| 404    | User not found           |

---

## Settings

### GET /api/settings

Get system configuration.

**Role**: Admin

**Response 200**:
| Field               | Type   | Description                           |
|---------------------|--------|---------------------------------------|
| netbox_url          | string | NetBox instance URL                   |
| netbox_token        | string | Redacted (`****...`)                  |
| gitlab_url          | string | GitLab instance URL                   |
| gitlab_token        | string | Redacted (`****...`)                  |
| grafana_url         | string | Grafana instance URL                  |
| grafana_token       | string | Redacted (`****...`)                  |
| ansible_repo_path   | string | Local path to ansible vars repo       |
| git_working_branch  | string | Git working branch name               |
| git_production_branch| string| Git production branch name            |
| ztp_base_url        | string | Base URL for ZTP script/config hosting|
| refresh_interval    | int    | Monitoring refresh interval in seconds|
| meraki_api_base     | string | Meraki dashboard API base URL         |
| meraki_api_key      | string | Redacted (`****...`)                  |

### PUT /api/settings

Update system configuration.

**Role**: Admin

**Request body**: Any subset of the fields from GET /api/settings (non-redacted values).

**Response 200** — Updated settings object.

**Error responses**:
| Status | Description              |
|--------|--------------------------|
| 400    | Invalid field value      |
| 403    | Insufficient role        |

### PUT /api/settings/credentials/{service_name}

Rotate a service credential token.

**Role**: Admin

**Path parameters**: `service_name` — One of `netbox`, `gitlab`, `grafana`, `meraki`

**Request body**:
| Field     | Type   | Required | Description                   |
|-----------|--------|----------|-------------------------------|
| token     | string | Yes      | New API token                 |
| base_url  | string | No       | Service base URL override     |

**Response 200** — Updated credential object (token redacted).

**Error responses**:
| Status | Description              |
|--------|--------------------------|
| 400    | Invalid token format     |
| 403    | Insufficient role        |
| 404    | Unknown service name     |

---

## Webhooks

### POST /api/webhooks/gitlab/pipeline

GitLab CI/CD pipeline completion callback. This endpoint receives deployment results from the GitLab pipeline's `notify` job.

**Role**: None (authenticated via shared secret header)

**Headers**:
| Header            | Value                     |
|-------------------|---------------------------|
| X-GitLab-Token    | Shared secret from settings|

**Request body**:
| Field         | Type      | Description                     |
|---------------|-----------|---------------------------------|
| pipeline_id   | int       | GitLab pipeline ID              |
| status        | string    | `success` or `failed`           |
| commit_sha    | string    | Commit SHA                      |
| deployed_at   | string    | ISO 8056 deployment time        |
| devices       | array[obj] | Per-device results            |

Per-device result:
| Field     | Type    | Description                       |
|-----------|---------|-----------------------------------|
| hostname  | string  | Device hostname                   |
| status    | string  | `success`, `failed`               |
| message   | string  | Error or success detail           |
| diff      | string  | Config diff (sections changed)    |

**Response 200** — Accepted.

**Error responses**:
| Status | Description                  |
|--------|------------------------------|
| 401    | Invalid or missing shared secret |
| 400    | Invalid request body         |

---

## Global Error Response Format

All error responses follow a consistent structure:

| Status | Response body                              |
|--------|--------------------------------------------|
| 400    | `{ "error": "Bad Request", "details": "..." }` |
| 401    | `{ "error": "Unauthorized" }`             |
| 403    | `{ "error": "Forbidden", "details": "..." }` |
| 404    | `{ "error": "Not Found", "details": "..." }` |
| 409    | `{ "error": "Conflict", "details": "..." }` |
| 422    | `{ "error": "Unprocessable", "details": "..." }` |
| 500    | `{ "error": "Internal Server Error" }`    |
| 502    | `{ "error": "Bad Gateway", "details": "..." }` |
| 503    | `{ "error": "Service Unavailable", "details": "..." }` |
