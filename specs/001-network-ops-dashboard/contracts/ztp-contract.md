# ZTP Provisioning Contract

The dashboard generates and hosts Cisco Zero Touch Provisioning (ZTP) boot scripts and day-0 configurations for new devices. The ZTP service is exposed through the dashboard's HTTP interface.

---

## URL Patterns

ZTP artifacts are served at fixed URL patterns that devices can reach via HTTP during their initial boot process.

### ZTP Boot Script

```
GET {ztp_base_url}/ztp/{serial}.txt
```

| Component     | Description                                  |
|---------------|----------------------------------------------|
| `ztp_base_url`| Configured in system settings (`ztp_base_url`)|
| `serial`      | Device serial number (URL-encoded)           |

This is the top-level script that the Cisco image loader fetches. It contains a sequence of `source` commands that download subsequent files.

**Example script content**:
```
source http://{host}/ztp/{serial}.cfg
```

### Day-0 Configuration

```
GET {ztp_base_url}/ztp/{serial}.cfg
```

The initial configuration file applied during first boot. Generated from the assigned profile using the ZTP Ansible playbook with a constrained task set.

**Content**: Standard IOS-XE configuration text containing bootstrap commands: hostname, management interface, AAA, NTP, and the minimal configuration needed to bring the device under management.

### Supporting Files

Additional files may be served during ZTP, following the same serial-based naming:

| URL Pattern                            | Description                            |
|----------------------------------------|----------------------------------------|
| `/ztp/{serial}/startup-config.conf`    | Full startup config (fallback)         |
| `/ztp/{serial}/image-list.txt`         | IOS image download list (if upgrading) |

---

## ZTP Provisioning Flow

### 1. Device Registration

An Editor creates a ZTP provisioning record via `POST /api/onboarding/ztp` (see REST API contract). The record includes:

| Field         | Description                          |
|---------------|--------------------------------------|
| device_id     | NetBox device ID (pre-registered)    |
| serial        | Device serial number                 |
| hostname      | Planned hostname                     |
| profile_id    | Profile to render for day-0 config   |
| is_meraki     | Whether this is a Meraki onboarding  |

### 2. Config Generation

Upon creation, the dashboard:

1. Renders the day-0 configuration by invoking the ZTP Ansible playbook in preview mode:
   - Uses the specified profile as `group_vars`
   - Creates a minimal `host_vars/<hostname>/vars.yml` with device-specific data
   - Runs the `ztp.yml` playbook with the constrained task set
2. Generates the `.txt` boot script referencing the `.cfg` URL
3. Stores both files in the served directory (or memory-backed store)

### 3. Artifact Hosting

Generated files are served from the dashboard process at the `/ztp/` path prefix. The ZTP files are accessible without authentication (devices are not authenticated at boot time).

**Security consideration**: ZTP files are identified by serial number. An attacker cannot guess valid serial numbers, but the endpoint **rate-limits unauthenticated access to 10 requests/minute per client IP**; excess requests receive `429` with `Retry-After`. A normal boot fetches 2–5 files.

### 4. Device Boot

When the device boots:

1. The image loader fetches `http://{host}/ztp/{serial}.txt`
2. The script sources `http://{host}/ztp/{serial}.cfg`
3. The day-0 config is applied
4. The device reboots with the bootstrap configuration
5. The device becomes reachable at its management IP

### 5. Post-Boot Status Update

Once the device is reachable and responds to telemetry/ping:

1. Grafana alerting transitions the device status from `unknown` to `up`
2. The ZTP provision record status updates to `onboarded`
3. The device appears in the inventory as available for profile-based configuration

**Status vocabulary**: `pending` → `generated` → `delivered` → `onboarded`, plus `failed` and `cancelled` (data model §8). `delivered` is set when the ZTP HTTP handler logs an artifact fetch.

**Boot failure policy (device unreachable / cannot reach the ZTP server)**: No server-side auto-fail timeout — the Cisco ZTP loader retries fetches automatically. The provision remains `generated`/`delivered`, fetch attempts are application-logged, and the operator marks the provision `failed` (with error detail) or `cancelled`.

**Artifact cleanup**: A daily job removes served artifacts for provisions in terminal states older than 30 days. DB records are retained.

---

## Meraki Onboarding ZTP Flow

Meraki onboarding uses the same ZTP URL patterns but generates configuration with Meraki-specific commands.

### 1. Meraki Provision Creation

An Editor creates a Meraki onboarding record via `POST /api/onboarding/meraki`. Additional fields:

| Field          | Description                            |
|----------------|----------------------------------------|
| network_id     | Target Meraki network ID               |
| dashboard_url  | Optional Meraki dashboard URL override |

### 2. Meraki Day-0 Config Generation

The generated `.cfg` file includes Meraki cloud onboarding commands:

```
! Meraki cloud onboarding
mdt controller name meraki
mdt controller meraki
  license key <api_key>
  organization id <org_id>
  network id <network_id>
  dashboard url <dashboard_url>
!
hostname <hostname>
!
interface GigabitEthernet1/0/1
 ip address <mgmt_ip> <mask>
 ip default-gateway <gateway>
!
ip name-server <dns>
ntp server <ntp_server>
```

### 3. Post-Boot Behavior

After the device boots with Meraki ZTP config:

1. The device registers with the Meraki dashboard
2. The device appears in Meraki cloud management
3. In the local inventory, the device is flagged with `cloud_managed: true`
4. The device is not directly configurable via NETCONF (Meraki manages config)
5. The ZTP provision record status updates to `onboarded`

### 4. Meraki API Integration

The dashboard may use the Meraki Dashboard API to verify onboarding:

**Endpoint**: `GET {meraki_api_base}/api/v1/devices/{serial}`

| Header            | Value                    |
|-------------------|--------------------------|
| X-Cisco-Meraki-Key| `<meraki_api_key>`       |

Used to confirm the device has appeared in the Meraki network and to retrieve cloud-assigned attributes.

**Error handling**: 10 s connection timeout; 3 retries with backoff on 5xx (standardized across integration clients). Verification failure is **non-fatal**: if the device has not appeared in Meraki yet, the provision stays `delivered` and subsequent checks retry; persistent failure logs a warning and flags the provision for operator review.

---

## File Storage

ZTP artifacts are stored in one of two modes:

| Mode       | Description                                    |
|------------|------------------------------------------------|
| Filesystem | Files written to a directory served by the HTTP process. Persists across restarts. |
| In-memory  | Files held in memory and served via route handler. Lost on restart; suitable for testing. |

The storage mode is determined by the `ztp_base_url` setting and the local `ansible_repo_path` configuration. In production, the filesystem mode is recommended.

## Error Responses

| Status | Description                                  |
|--------|----------------------------------------------|
| 404    | No ZTP provision found for this serial number|
| 500    | Config generation failed                     |
| 503    | ZTP service unavailable                      |
