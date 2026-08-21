# StackHive — Network Operations Dashboard

A single-host, containerized operations dashboard for Cisco IOS-XE (and Meraki-cloud)
switch fleets. It provides a unified UI over device inventory (NetBox), Git-backed
configuration profiles with automatic commits (GitLab CE), preview → approval →
atomic NETCONF deployments (Ansible + GitLab CI), day-0 ZTP/Meraki onboarding, and
Grafana/InfluxDB/Telegraf telemetry with pre-provisioned dashboards.

Built with Python first (Flask, SQLAlchemy), Bootstrap 5 + minimal JS on the front
end, and Ansible for all device configuration. Only open-source dependencies.

## Architecture

```
                    ┌──────────────┐
   Browser ───────► │    traefik   │  /            → StackHive dashboard (Flask)
                    │  (reverse    │  /netbox      → NetBox
                    │   proxy)     │  /grafana     → Grafana
                    │              │  /gitlab      → GitLab CE
                    └──────┬───────┘  */ztp/*      → ZTP public endpoints (rate limited)
                           │
   ┌───────────────────────┼──────────────────────────────────────────────┐
   │                stackhive Docker network                              │
   │                                                                      │
   │  app (Flask UI + API + ZTP) ──► NetBox REST      (inventory source)  │
   │        │                        GitLab REST      (vars repo, MRs,    │
   │        ├──► Ansible subprocess   pipelines)     preview = deploy     │
   │        │   (preview.yml)                              │              │
   │        ├──► Grafana API     (alert states → up/down, │              │
   │        └──► InfluxDB/Telegraf ◄── MDT gRPC telemetry ┘               │
   │                                (IOS-XE switches)  gitlab-ci/         │
   │                              .proto schemas        .gitlab-ci.yml    │
   │                                                            (validate │
   └──────────────────────────────────────────────────────────►│ preview  │
                                                                │ approve │
        devices (NETCONF merge, atomic) ◄── deploy job ─────────┘deploy   │
                                                                          │ verify)
```

- **Inventory** is sourced from NetBox (REST, paginated, cached) and mirrored into the
  app's own SQLite/Postgres DB via a periodic refresh thread + manual sync.
- **Configuration state** lives in a GitLab repository: `group_vars/<profile>/vars.yml`,
  `host_vars/<hostname>.yml`, Jinja2 templates. Every UI change is a commit (`profile: …`,
  `override: …` push-rebased, conflicts surface as 409). The remote GitLab project is the
  **authoritative variable store** that both the preview subprocess and the CI pipeline read
  at deploy time (it is also where every change is versioned, diffed, and reviewed). This is
  an intentional refinement of the plan's "bind-mounted monorepo `ansible/`" layout: the
  monorepo `ansible/` tree still ships the playbooks, the `iosxe` role, and the inventory
  (bind-mounted into the app and runner containers), while per-profile variables — owned by
  many operators at once — round-trip through Git so concurrent edits conflict cleanly.
  `scripts/generate-host-vars.py` is the offline bootstrap helper that writes `host_vars/`
  files into a local checkout of that repository before the first app-driven commit.
- **Deployments** render the iosxe Ansible role in exactly the same way for preview and
  apply (same playbooks, vars, role), then require an **admin approval** that merges the
  `working → production` MR; GitLab Runner executes the pipeline
  (validate → preview → approve (manual) → deploy (NETCONF merge, atomic) → verify (read-back) →
  notify webhook), and the pipeline webhook updates the UI.
- **Monitoring**: IOS-XE switches stream MDT gRPC telemetry (protobuf schemas under
  `monitoring/protobuf/`) to Telegraf → InfluxDB; Grafana dashboards are
  file-provisioned (device-health, infrastructure, interfaces). Device up/down is
  derived from Grafana alert state, with DB fallback when Grafana is unreachable.
- **ZTP / onboarding**: public, unauthenticated, rate-limited endpoints at
  `/ztp/<serial>.txt` (boot script), `/ztp/<serial>.cfg` (rendered day-0 config),
  `/ztp/<serial>/startup-config.conf`, `/ztp/<serial>/image-list.txt`. Meraki onboarding
  renders cloud bootstrap configs and flags the device `cloud_managed` (excluded from
  direct NETCONF deploys).

## Repository layout

| Path | Purpose |
|------|---------|
| `app/` | Flask application (models, routes, services, templates, tests). `app/Dockerfile` builds the production image. |
| `ansible/` | Ansible playbooks (`site.yml`, `preview.yml`, `verify.yml`, `ztp.yml`), `roles/iosxe/` (snippets: AAA, MDT, NETCONF, VLAN, routing, interfaces, bootstrap), Molecule tests. |
| `gitlab-ci/` | `.gitlab-ci.yml` pipeline + helpers mounted into the GitLab Runner. |
| `monitoring/` | Telegraf config, Grafana provisioning (datasources, dashboards, JSON dashboard sources), Cisco IOS-XE gRPC `.proto` schemas. |
| `traefik/` | Static + dynamic routing config (dashboard, `/netbox`, `/grafana`, `/gitlab`, public ZTP). |
| `scripts/` | `sync-inventory.sh` (manual NetBox sync via the app CLI) and `generate-host-vars.py` (host_vars YAML generator). |
| `specs/` | Specification, plan, contracts, and task tracking (speckit). |

## Prerequisites

- Docker and Docker Compose (Docker Desktop 4.27+ or equivalent)
- **8 GB RAM minimum** (GitLab CE 2 GB, NetBox 1 GB, Grafana/InfluxDB 1 GB, app/traefik/telegraf 512 MB, remainder for Ansible job containers)
- **1 GB swap** (GitLab CE requires it under memory pressure)
- **40 GB disk minimum, 100 GB+ recommended** (GitLab ~10 GB, NetBox ~2 GB, images ~5 GB,
  InfluxDB 14-day MDT headroom for 500 devices ~20 GB+; a smaller app-only footprint fits in ~20 GB)
- Ports 8080 (unified Traefik entry point) and 443 (TLS, reserved) free
- `/etc/hosts`: `127.0.0.1 stackhive.local`

## Single-command deployment

```bash
cp .env.example .env    # then edit with your credentials (see below)
docker compose up -d    # includes docker-compose.netbox.yml
```

First boot takes 3–5 minutes. Then:

```bash
# 1. NetBox: create a token (User → Tokens → Generate, read+write) → put in .env as NETBOX_TOKEN
# 2. GitLab Runner registration (Admin → Instances → Runners, or via API)
docker compose exec gitlab-runner gitlab-runner register \
  --url http://gitlab:8090/gitlab --token <registration-token> --executor docker \
  --docker-image "python:3.12-slim" \
  --docker-volumes "/var/run/docker.sock:/var/run/docker.sock" \
  --docker-volumes "/host/ansible:/ansible:ro" --network-mode "host" \
  --tag-list "ansible,netconf" --run-untagged="false"
# 3. Create the admin user
docker compose exec app flask create-admin --username admin --email admin@stackhive.local \
  --password "ChangeMe123!" --role admin
# 4. Seed service credentials (Fernet-encrypted at rest)
docker compose exec app flask seed-credentials \
  --netbox-token "$NETBOX_TOKEN" --gitlab-token "$GITLAB_TOKEN" --grafana-token "$GRAFANA_TOKEN"
```

Full first-boot checklist and validation scenarios: [specs/001-network-ops-dashboard/quickstart.md](specs/001-network-ops-dashboard/quickstart.md).

### Service entry points (via Traefik, host port 8080 in dev)

| URL | Service |
|-----|---------|
| `http://stackhive.local:8080/` | StackHive dashboard (Flask) |
| `http://stackhive.local:8080/netbox/` | NetBox |
| `http://stackhive.local:8080/grafana/` | Grafana |
| `http://stackhive.local:8080/gitlab/` | GitLab CE (path-aware `external_url`, no prefix strip) |
| `http://stackhive.local:8080/ztp/<serial>.txt` / `.cfg` | Public ZTP (no auth, rate-limited 10 req/min/IP) |
| `http://<host>:8080/api/health` | App health (checks DB + backends) |

## Roles

| Capability | viewer | editor | admin |
|------------|:------:|:------:|:-----:|
| Browse inventory / profiles / deployments / monitoring | ✓ | ✓ | ✓ |
| Create/update profiles, device overrides, ZTP provisions | — | ✓ | ✓ |
| Initiate deployments (preview + commit + MR) | — | ✓ | ✓ |
| Approve deployments (merge MR) | — | — | ✓ |
| Settings, credentials, user management | — | — | ✓ |

## `.env` reference

All variables and their meanings are documented in [.env.example](.env.example).
Highlights:

- `SECRET_KEY` — Flask session signing key (random hex).
- `ENCRYPTION_KEY` — Fernet key for stored service credentials
  (`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
- `DATABASE_URL` — default `sqlite:////var/lib/stackhive/stackhive.db` (volume-backed).
- `NETBOX_URL` / `NETBOX_TOKEN` — inventory source.
- `GITLAB_URL` / `GITLAB_TOKEN` / `GITLAB_PROJECT_ID` / `GITLAB_SHARED_SECRET` —
  vars repository, MR automation, and pipeline-webhook shared secret.
- `GRAFANA_URL` / `GRAFANA_TOKEN` — service-account token (Viewer) for alert-state reads.
- `INFLUXDB_TOKEN` / `INFLUXDB_ORG` / `INFLUXDB_BUCKET` / `INFLUXDB_RETENTION_DAYS` — telemetry.
- `MERAKI_API_KEY` / `MERAKI_DASHBOARD_URL` / `MERAKI_ORG_ID` — optional Meraki cloud onboarding.
- `ANSIBLE_NETCONF_USER` / `ANSIBLE_NETCONF_PASSWORD` / `ANSIBLE_ENABLE_PASSWORD` — device connectivity.
- `ZTP_BASE_URL` — externally reachable origin for ZTP artifacts.

Credentials are stored once, Fernet-encrypted, in the app database
(`flask seed-credentials` or Settings → Credentials); `.env` values act as fallback and are
never written to files.

## Operations

### Manual inventory sync

```bash
./scripts/sync-inventory.sh          # docker-compose exec app flask sync-inventory
# or via API/UI: POST /api/inventory/sync (editor+)
```

### Generating host_vars from a spec

```bash
./scripts/generate-host-vars.py spec.yml --out ansible/host_vars/
```

### Development (outside containers)

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements.txt
.venv/bin/python -m pytest app/tests        # full app test suite
cd ansible/tests/molecule && molecule test  # iosxe role scenario (converge + verify)
```

### Reset

```bash
docker compose down -v
rm -rf data/*
cp .env.example .env && docker compose up -d
```

### Backups

Daily archive of every named volume to `backups/`, keeping 7 daily + 4 weekly
per volume. Schedule it with cron (adjust the interpreter and repo path):

```bash
# /etc/cron.d/stackhive-backup
0 2 * * * root /usr/bin/python3 /opt/stackhive/scripts/backup_volumes.py \
    --backup-dir /opt/stackhive/backups >> /var/log/stackhive-backup.log 2>&1
```

Options: `--volumes a,b` (subset), `--weekly` (force a weekly stamp), `--dry-run`.
The first run pulls the minimal `busybox:1.36` helper image used to tar each
volume. GitLab CE's built-in rake backups remain a secondary safety net for
GitLab data.

### Recovery

Restore a single volume (example: `stackhive-db`) from a daily archive:

```bash
docker compose stop app
docker volume rm stackhive-db
docker run --rm -v /opt/stackhive/backups:/src -v stackhive-db:/data busybox \
    tar xzf /src/stackhive-stackhive-db-<YYYY-MM-DD>.tar.gz -C /data
docker compose up -d
```

For a full recovery, recreate each volume from its latest archive the same way,
then `docker compose up -d`. Prefer the newest archive for each volume; weekly
archives (named `...-<YYYY>-W<week>.tar.gz`) are the fallback when a daily is
missing.

## Performance targets

Inventory page < 2 s at 500 devices (pagination + caching), config preview < 30 s/device
(enforced subprocess timeout), full NetBox sync < 10 s, and deployment status reachable in
the UI < 2 min after initiation. See the plan for details.
