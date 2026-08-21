# NetBox API Integration Contract

The dashboard reads device inventory from a NetBox instance via its REST API. NetBox is the source of truth for immutable device data.

## Authentication

The dashboard authenticates to NetBox using a token-based approach configured in system settings.

| Header          | Value format                    |
|-----------------|---------------------------------|
| Authorization   | `Token <netbox_api_token>`      |

The token is stored in the dashboard settings (`netbox_token`) and must have at least `read` permissions on devices, device types, and locations.

## Base URL

Configured via `netbox_url` in system settings. All paths below are relative to this base.

---

## Device Inventory

### Retrieving Devices

**Endpoint**: `GET /api/dcim/devices/`

The dashboard queries this endpoint to populate the device inventory. Query parameters used for filtering:

| Parameter       | Description                          |
|-----------------|--------------------------------------|
| role            | Filter by device role slug           |
| device_type     | Filter by device type slug           |
| site            | Filter by site slug                  |
| tag              | Filter by tag slug                   |
| limit           | Page size (up to NetBox max)         |
| offset          | Pagination offset                    |

**Response fields consumed by the dashboard**:

| NetBox field        | Dashboard mapping          | Description                              |
|---------------------|----------------------------|------------------------------------------|
| id                  | `id`                       | NetBox device ID (used as primary key)   |
| name                | `hostname`                 | Device FQDN                              |
| device_type         | `device_type` (nested)     | Reference to device type                 |
| role                | `role` (nested)            | Reference to device role                 |
| serial              | `serial`                   | Serial number                            |
| mac_address         | `mac_address`              | Base MAC address                         |
| status              | (mapped)                   | Used to determine operational state      |
| site                | `site` (nested)            | Reference to site/location               |
| primary_ip          | `ip_address` (nested)      | Primary IP address object                |
| tags                | `tags`                     | Attached NetBox tags                     |
| custom_fields       | (parsed)                   | Custom fields for cloud-managed flag     |

**Custom fields expected on devices**:

| Custom field name   | Type   | Description                                    |
|---------------------|--------|------------------------------------------------|
| cloud_managed       | boolean| True if device is managed by Meraki cloud      |
| last_deployment     | date   | Last successful deployment timestamp           |
| grafana_dashboard   | text   | Optional: custom Grafana dashboard UID         |

### Device Detail

**Endpoint**: `GET /api/dcim/devices/{id}/`

Returns full device detail used for the device detail page. The dashboard also follows nested references for `device_type`, `role`, `site`, and `primary_ip`.

---

## Device Types

Device type information provides the physical interface layout for each device model.

### Retrieving Device Types

**Endpoint**: `GET /api/dcim/device-types/`

Used to look up device models and their interface definitions.

**Response fields consumed**:

| NetBox field   | Dashboard mapping    | Description                                |
|----------------|----------------------|--------------------------------------------|
| id             | (reference)          | Device type ID                             |
| model          | `device_type.model`  | Model name (e.g., `C9300-48P-4X`)          |
| manufacturer   | `device_type.manufacturer` | Vendor name (e.g., `Cisco`)         |
| part_id        | (stored)             | Part number                                |

### Interface Definitions

**Endpoint**: `GET /api/dcim/devices/{id}/interfaces/`

Returns all physical and virtual interfaces for a specific device instance. This is the authoritative source for the interface list shown on the device detail page.

**Response fields consumed**:

| NetBox field   | Dashboard mapping    | Description                                 |
|----------------|----------------------|---------------------------------------------|
| id             | (reference)          | Interface ID                                |
| name           | `name`               | Interface name (e.g., `GigabitEthernet1/0/1`)|
| type           | `type`               | Interface type slug (e.g., `1000base-t`)    |
| enabled        | `enabled`            | Administrative status                      |
| mgmt_only      | (flag)               | True for management interfaces              |
| label          | (stored)             | Physical label on the hardware              |
| description    | `description`        | Configured description                      |
| mode           | (stored)             | Access/trunk mode for switch ports          |

**Endpoint**: `GET /api/dcim/device-types/{id}/` with `includes=interfaces`

Returns the device type definition with its component interface templates. Used when creating profiles to determine how many interfaces of each type a device model has.

---

## Device Roles

**Endpoint**: `GET /api/dcim/device-roles/`

Returns all device roles. Roles are used to associate configuration profiles with groups of devices. The dashboard displays the role name on devices and allows filtering by role.

**Response fields consumed**:

| NetBox field   | Dashboard mapping    | Description                                  |
|----------------|----------------------|----------------------------------------------|
| id             | (reference)          | Role ID                                     |
| name           | `role`               | Role display name (e.g., `access-switch`)    |
| slug           | (stored)             | URL-safe identifier                          |
| color          | (stored)             | UI color for role tagging                    |

---

## Sites

**Endpoint**: `GET /api/dcim/sites/`

Returns site/location definitions. Used for site-based filtering in the inventory view.

**Response fields consumed**:

| NetBox field   | Dashboard mapping    | Description            |
|----------------|----------------------|------------------------|
| id             | (reference)          | Site ID                |
| name           | `site`               | Site display name      |
| slug           | (stored)             | URL-safe identifier    |

---

## Sync Behavior

- The dashboard polls NetBox at the configured `refresh_interval` (default: 60 seconds) to update device status.
- Full inventory refresh (devices, types, roles, sites) occurs on dashboard load and on-demand.
- When NetBox is unreachable, the dashboard caches the last successful response and surfaces a 503 error for live queries.
- The Ansible inventory plugin queries NetBox dynamically at playbook runtime; the dashboard does not maintain a static copy of inventory.
- Hostname collisions: sync upserts by NetBox ID. If two devices share a hostname (violating local uniqueness), the later record is skipped, the conflict is logged and surfaced in the sync-report UI; the operator renames one device in NetBox and re-syncs.
- Device types lacking NetBox interface template data fall back to locally maintained `DeviceType` records (admin-maintained via `PUT /api/device-types`), used for profile authoring.

## Error Handling

All integration clients share standardized external-call behavior: **10 s connection timeout; 3 retries with exponential backoff on 5xx/timeouts**; surface behavior is per-service as below.

| NetBox HTTP status | Dashboard behavior                          |
|--------------------|---------------------------------------------|
| 401                | Log error, surface 503 to user              |
| 403                | Log error, surface 503 to user              |
| 404                | Log error, surface 404 for device lookups   |
| 5xx                | Retry with backoff, surface 503 after 3 retries |
| Connection timeout | Surface 503 with "NetBox inventory unavailable" |
