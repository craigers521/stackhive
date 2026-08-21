# Quickstart Validation Guide

## Prerequisites

- Docker and Docker Compose installed (Docker Desktop 4.27+ or equivalent)
- **Minimum 8 GB RAM** (GitLab CE 2GB, NetBox 1GB, Grafana/InfluxDB 1GB, dashboard/traefik/telegraf 512MB, remainder for Ansible job containers)
- **1 GB swap** required on host (GitLab CE requires swap for memory-constrained operation)
- **40 GB disk space minimum, 100 GB+ recommended** (GitLab CE ~10GB, NetBox ~2GB, image layers ~5GB, InfluxDB/data ~20GB+ with 14-day MDT headroom for 500 devices)
- Available ports: 80, 443, 8080 (dev)
- Network access to managed devices (for integration tests)
- DNS or `/etc/hosts` entries:
  ```
  127.0.0.1 stackhive.local gitlab.stackhive.local
  ```

## Setup Commands

```bash
# 1. Clone and enter the project
git clone <repo-url> stackhive && cd stackhive

# 2. Configure host swap (required for GitLab CE)
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl vm.swappiness=10

# 3. Create environment file from template
cp .env.example .env

# 4. Edit .env with your credentials
#    Required variables in .env:
#    =========================================
#    # Flask Dashboard
#    SECRET_KEY=change-me-to-random-string
#    ENCRYPTION_KEY=fernet-key-generate-with-python
#    DATABASE_URL=sqlite:////var/lib/stackhive/stackhive.db
#
#    # NetBox (set after NetBox starts)
#    NETBOX_URL=http://localhost:8080/netbox
#    NETBOX_TOKEN=
#
#    # GitLab CE
#    GITLAB_ROOT_PASSWORD=
#    GITLAB_URL=http://localhost:8080/gitlab
#    GITLAB_TOKEN=
#    GITLAB_SHARED_SECRET=webhook-secret-for-pipeline-callbacks
#
#    # Grafana
#    GRAFANA_ADMIN_PASSWORD=
#    GRAFANA_URL=http://localhost:8080/grafana
#    GRAFANA_TOKEN=
#
#    # InfluxDB
#    INFLUXDB_TOKEN=
#    INFLUXDB_ORG=stackhive
#    INFLUXDB_BUCKET=telemetry
#    INFLUXDB_RETENTION_DAYS=14
#
#    # Meraki (optional, for Meraki onboarding)
#    MERAKI_API_KEY=
#    MERAKI_DASHBOARD_URL=https://api.meraki.com/api/v1
#
#    # Ansible
#    ANSIBLE_NETCONF_USER=
#    ANSIBLE_NETCONF_PASSWORD=
#    ANSIBLE_ENABLE_PASSWORD=
#    =========================================

# 5. Start all services (includes NetBox via compose include)
docker compose up -d

# 6. Wait for services to initialize (first boot takes 3-5 minutes)
docker compose logs -f --tail=50

# 7. Create initial NetBox token (after NetBox starts)
#    Log into NetBox at http://localhost:8080/netbox/
#    Go to User > Tokens > Generate Token (read + write scope)
#    Copy token to .env as NETBOX_TOKEN

# 8. Register GitLab Runner (after GitLab starts)
#    Get registration token from GitLab: Admin > Instances > Runners
#    Or via API:
curl --head --request GET --header "PRIVATE-TOKEN: $GITLAB_ROOT_PASSWORD" \
  "http://localhost:8080/gitlab/api/v4/application/settings/runner_registration_token"
#    Then register:
docker compose exec gitlab-runner gitlab-runner register \
  --url http://gitlab:8090/gitlab \
  --token <registration-token> \
  --executor docker \
  --docker-image "python:3.12-slim" \
  --docker-volumes "/var/run/docker.sock:/var/run/docker.sock" \
  --docker-volumes "/host/ansible:/ansible:ro" \
  --network-mode "host" \
  --tag-list "ansible,netconf" \
  --run-untagged="false"

# 9. Create the initial admin account
docker compose exec app flask create-admin \
  --username admin \
  --email admin@stackhive.local \
  --password "ChangeMe123!" \
  --role admin

# 10. Seed service credentials (stored encrypted in DB)
docker compose exec app flask seed-credentials \
  --netbox-token "$NETBOX_TOKEN" \
  --gitlab-token "$GITLAB_ROOT_TOKEN" \
  --grafana-token "$GRAFANA_ADMIN_PASSWORD"
```

## Validation Scenarios

### Scenario 1: Service Stack Health Check

Verify all containers are running and healthy.

```bash
# All containers should be "up" and healthy
docker compose ps

# Expected services:
#   app, traefik, netbox, netbox-worker, netbox-postgres, netbox-redis
#   gitlab, gitlab-runner
#   telegraf, influxdb, grafana
```

```bash
# Verify Traefik dashboard is reachable
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080

# Verify the Flask app health endpoint
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health

# Expected: 200 for both
```

```bash
# Check each service log for startup completion
docker compose logs app      | grep "Running on"
docker compose logs netbox    | grep "started"
docker compose logs gitlab    | grep "Ruby"
docker compose logs grafana   | grep "Server listening"
docker compose logs influxdb  | grep "Listening"
docker compose logs telegraf  | grep "Agent started"
docker compose logs ztp-server | grep "Running on"
docker compose logs traefik   | grep "Server created"
```

**Expected outcome**: All 9+ services report running with no error loops in logs.

---

### Scenario 2: Device Inventory Sync

Add a test device in NetBox and verify it appears in the dashboard inventory.

```bash
# 1. Add a test device via the NetBox API (container-internal)
docker compose exec netbox-postgres psql -U postgres -d netbox -c \
  "SELECT 1;"  # confirm DB is up

# 2. Create the device role if it does not exist
curl -s -X POST "http://localhost:8080/netbox/api/dcim/device-roles/" \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "access-switch", "slug": "access-switch", "color": "ffff00", "vm_role": false}'

# 3. Create a device type (use an existing one or skip if already present)
#    For validation, reuse a stock Cisco C9200 type from NetBox if loaded.

# 4. Create a test device
curl -s -X POST "http://localhost:8080/netbox/api/dcim/devices/" \
  -H "Authorization: Token $NETBOX_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "sw-access-01",
    "device_role": "access-switch",
    "device_type": <device-type-id>,
    "site": <site-id>,
    "platform": "cisco_ios_xe"
  }'

# 5. Trigger an inventory sync from the dashboard
curl -s -X POST "http://localhost:8080/api/inventory/sync" \
  -H "Authorization: Bearer <dashboard-api-token>"

# 6. Verify device appears in the dashboard inventory
curl -s "http://localhost:8080/api/inventory/devices" \
  -H "Authorization: Bearer <dashboard-api-token>" | python3 -m json.tool
```

**Expected outcome**: `sw-access-01` appears in the API response with attributes hostname, role (`access-switch`), IP address, status, and device type.

---

### Scenario 3: Configuration Profile Creation

Create a profile for the `access-switch` device role.

```bash
# 1. Create a new configuration profile via the API
curl -s -X POST "http://localhost:8080/api/profiles/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "access-switch-base",
    "device_role": "access-switch",
    "description": "Baseline config for access layer switches"
  }'

# 2. Add a template section (e.g., VLANs)
curl -s -X POST "http://localhost:8080/api/profiles/access-switch-base/sections/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "vlans",
    "template": "vlan.j2",
    "order": 1
  }'

# 3. Add configuration variables
curl -s -X POST "http://localhost:8080/api/profiles/access-switch-base/variables/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "vlan_management",
    "value": "100",
    "description": "Management VLAN ID"
  }'

# 4. Verify the profile appears in the list
curl -s "http://localhost:8080/api/profiles/" \
  -H "Authorization: Bearer <dashboard-api-token>" | python3 -m json.tool
```

**Expected outcome**: Profile `access-switch-base` is listed with its sections and variables. The corresponding `group_vars/access-switch.yml` file is created in the ansible-config volume with the variable data.

---

### Scenario 4: Configuration Preview

Generate a config preview for `sw-access-01` and verify snippet merging.

```bash
# 1. Request a config preview for the device
curl -s -X POST "http://localhost:8080/api/devices/sw-access-01/preview/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{"profile": "access-switch-base"}' | python3 -m json.tool
```

**Expected outcome**:

- The response contains rendered configuration snippets (VLANs, interfaces, AAA, MDT, etc.)
- Each snippet is labeled with its source template
- Snippets are independent and would merge (not replace) into existing device config
- Device-specific overrides (if any host_vars exist) take precedence over profile defaults

```bash
# 2. Verify a device override takes precedence
curl -s -X POST "http://localhost:8080/api/devices/sw-access-01/overrides/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{"key": "vlan_management", "value": "200"}'

# Re-run preview - vlan_management should now be 200
curl -s -X POST "http://localhost:8080/api/devices/sw-access-01/preview/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{"profile": "access-switch-base"}' | grep "vlan 200"
```

**Expected outcome**: The override variable (`vlan_management: 200`) supersedes the profile default (`100`) in the rendered preview.

---

### Scenario 5: Deployment Flow

Initiate a deployment and verify the Git + CI/CD pipeline integration.

```bash
# 1. Initiate a deployment to sw-access-01
curl -s -X POST "http://localhost:8080/api/deployments/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "devices": ["sw-access-01"],
    "profile": "access-switch-base"
  }' | python3 -m json.tool

# 2. Verify a Git commit was created on the working branch
docker compose exec gitlab git -C /var/opt/gitlab/git-data/repositories/root/stackhive-config.git log --oneline -3

# Expected: A commit message like "deploy: apply access-switch-base to sw-access-01"

# 3. Check for a GitLab merge request to production branch
curl -s "http://localhost:8080/gitlab/api/v4/projects/root%2Fstackhive-config/merge_requests?state=opened" \
  -H "PRIVATE-TOKEN: $GITLAB_ROOT_TOKEN" | python3 -m json.tool

# 4. Verify a pipeline was triggered
curl -s "http://localhost:8080/gitlab/api/v4/projects/root%2Fstackhive-config/pipelines?ref=working" \
  -H "PRIVATE-TOKEN: $GITLAB_ROOT_TOKEN" | python3 -m json.tool

# 5. Check deployment record in dashboard history
curl -s "http://localhost:8080/api/deployments/" \
  -H "Authorization: Bearer <dashboard-api-token>" | python3 -m json.tool
```

**Expected outcome**:

- A commit exists on the `working` branch with a descriptive message
- An open merge request targets the `production` branch
- A GitLab pipeline is running or completed on the MR
- A deployment record appears in the dashboard with timestamp, operator, target device, profile, and status

---

### Scenario 6: Monitoring Integration

Verify Grafana dashboards are accessible and device status is reflected.

```bash
# 1. Access Grafana dashboard (should redirect to login)
curl -s -o /dev/null -w "%{http_code}" "http://localhost:8080/grafana/"
# Expected: 200 or 302 to login

# 2. Verify pre-provisioned dashboards exist via Grafana API
curl -s "http://localhost:8080/grafana/api/search?query=&limit=20" \
  -u "admin:$GRAFANA_ADMIN_PASSWORD" | python3 -m json.tool

# Expected: "device-health", "infrastructure", "interfaces" dashboards listed

# 3. Check that device status appears in the dashboard inventory
curl -s "http://localhost:8080/api/inventory/devices" \
  -H "Authorization: Bearer <dashboard-api-token>" | \
  python3 -c "import sys,json; devices=json.load(sys.stdin); print([d['status'] for d in devices])"

# 4. Verify deep-link from device detail to Grafana
curl -s "http://localhost:8080/api/devices/sw-access-01/monitoring-link/" \
  -H "Authorization: Bearer <dashboard-api-token>"
# Expected: URL pointing to Grafana dashboard with device variable set
```

**Expected outcome**: Three dashboards are provisioned. Device detail pages include a deep-link to Grafana filtered to the specific device. Infrastructure dashboard shows container metrics from Telegraf.

---

### Scenario 7: ZTP Provisioning

Create a ZTP provision for a test device and verify hosted files.

```bash
# 1. Create a ZTP provision for a new device
curl -s -X POST "http://localhost:8080/api/ztp/provisions/" \
  -H "Authorization: Bearer <dashboard-api-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "device_serial": "FCW2345N123",
    "profile": "access-switch-base",
    "boot_server": "stackhive.local"
  }'

# 2. Verify the ZTP script is served at the expected URL
curl -s "http://localhost:8080/ztp/script.py" | head -20
# Expected: A Python ZTP script that fetches config by serial number

# 3. Verify the day-0 config is hosted for the device serial
curl -s "http://localhost:8080/ztp/config/FCW2345N123" | head -20
# Expected: Rendered bootstrap configuration (hostname, management IP, AAA, etc.)
```

**Expected outcome**: The ZTP script is a valid Python file at `/ztp/script.py`. The day-0 config at `/ztp/config/<serial>` contains a minimal bootstrap configuration rendered from the profile templates with device-specific variables.

---

### Scenario 8: RBAC Enforcement

Test role-based access control across the three roles.

```bash
# 1. Create test users (or use the dashboard UI)
docker compose exec app flask create-user \
  --username viewer --email viewer@test.local --password "Test123!" --role viewer

docker compose exec app flask create-user \
  --username editor --email editor@test.local --password "Test123!" --role editor

# 2. Obtain auth tokens for each role
VIEWER_TOKEN=$(curl -s -X POST "http://localhost:8080/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "viewer", "password": "Test123!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

EDITOR_TOKEN=$(curl -s -X POST "http://localhost:8080/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "editor", "password": "Test123!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 3. Viewer: can read inventory
curl -s -o /dev/null -w "%{http_code}" "http://localhost:8080/api/inventory/devices" \
  -H "Authorization: Bearer $VIEWER_TOKEN"
# Expected: 200

# 4. Viewer: cannot create profiles
curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/api/profiles/" \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "test", "device_role": "access-switch"}'
# Expected: 403

# 5. Editor: can create profiles
curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/api/profiles/" \
  -H "Authorization: Bearer $EDITOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "editor-test", "device_role": "access-switch"}'
# Expected: 201

# 6. Editor: cannot approve deployments (Admin-only)
curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/api/deployments/1/approve/" \
  -H "Authorization: Bearer $EDITOR_TOKEN"
# Expected: 403

# 7. Admin: can approve deployments
ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8080/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "ChangeMe123!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/api/deployments/1/approve/" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Expected: 200
```

**Expected outcome**: Viewer is read-only on all write endpoints (403). Editor can create and edit profiles but cannot approve deployments (403). Admin has full access including deployment approval (200).

---

### Scenario 9: Telemetry Sizing Pilot

Before finalizing production disk sizing, measure real InfluxDB growth with a small fleet:

```bash
# After ~10 representative devices have sent MDT telemetry for ~1 week:
docker compose exec influxdb influx usage --org stackhive
```

**Expected outcome**: Record GB/week and extrapolate to 500 devices; adjust `INFLUXDB_RETENTION_DAYS` (default 14) and host disk (100 GB+ recommended) accordingly.

---

## Troubleshooting

### Common Issues

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| GitLab container won't start | Insufficient RAM or disk | Allocate at least 4 GB RAM to Docker; verify 10 GB free disk space |
| NetBox API returns 401 | Token mismatch in `.env` | Verify `NETBOX_TOKEN` matches the token created in NetBox admin UI |
| Inventory sync fails | NetBox container not ready | Wait for NetBox to fully initialize (`docker compose logs netbox`); retry sync |
| Grafana dashboards missing | Provisioning path misconfigured | Check `monitoring/grafana/provisioning/` files exist and are volume-mounted |
| Preview generation hangs | Ansible role not found | Verify `ansible/roles/iosxe/` structure matches the plan |
| ZTP config not served | ZTP provision not created | Run Scenario 7 step 1 before accessing ZTP URLs |
| 403 on all API calls | Expired or missing token | Re-authenticate via `/api/auth/login` |
| Traefik routes not working | Docker network mismatch | Ensure all containers are on the same Docker network (`stackhive`) |
| GitLab Runner jobs fail | Runner not registered or Docker socket missing | Check `docker compose logs gitlab-runner`; verify `/var/run/docker.sock` is mounted |
| Ansible preview hangs | Ansible subprocess can't reach NetBox | Verify `NETBOX_TOKEN` in .env; check NetBox is reachable from app container |
| 403 on backend API calls | Expired or missing service credential | Check `docker compose logs app` for credential errors; re-seed via Settings UI |
| Preview differs from deploy | Ansible version mismatch | Ensure app container and runner jobs use the same Ansible collection versions |
| GitLab OOM kills | Insufficient RAM or swap | Verify 1GB swap exists (`swapon --show`); check `vm.swappiness=10` |

### Log Locations

```bash
# Flask dashboard app (includes ZTP blueprint routes)
docker compose logs app

# NetBox (Python/Django)
docker compose logs netbox
docker compose logs netbox-worker

# GitLab (all-in-one)
docker compose logs gitlab

# GitLab Runner
docker compose logs gitlab-runner

# Grafana
docker compose logs grafana

# InfluxDB
docker compose logs influxdb

# Telegraf
docker compose logs telegraf

# Traefik
docker compose logs traefik
```

### Data and Volume Paths

| Service | Volume Mount | Purpose |
|---------|-------------|---------|
| Flask Dashboard | `stackhive-db:/var/lib/stackhive` | SQLite database, credentials |
| GitLab | `gitlab-data:/var/opt/gitlab` | GitLab config, repos, database |
| NetBox | `netbox-postgres-data:/var/lib/postgresql/data` | NetBox PostgreSQL data |
| InfluxDB | `influxdb-data:/var/lib/influxdb` | Telemetry time-series data |
| Grafana | `grafana-data:/var/lib/grafana` | Grafana dashboards, datasources |
| Ansible | `./ansible:/app/ansible:rw` (bind mount) | Templates, roles, vars, playbooks |

### Reset

To wipe all state and start fresh:

```bash
docker compose down -v
rm -rf data/*
cp .env.example .env
# Re-edit .env, then:
docker compose up -d
```
