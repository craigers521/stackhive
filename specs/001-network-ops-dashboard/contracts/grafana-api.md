# Grafana API Integration Contract

The dashboard reads device status and infrastructure health metrics from Grafana. Detailed telemetry and dashboards are accessed via deep-link URLs that open in new tabs.

## Authentication

The dashboard authenticates to Grafana using a service account API key configured in system settings.

| Header          | Value format                     |
|-----------------|----------------------------------|
| Authorization   | `Bearer <grafana_api_key>`       |

The API key must have `Viewer` role permissions on the relevant folders and dashboards.

## Base URL

Configured via `grafana_url` in system settings. All API paths are relative to this base.

---

## Device Status

The dashboard polls Grafana to determine simple up/down status for each network device. Status is cached and refreshed at the configured `refresh_interval`.

### Alert-Based Status Detection

**Endpoint**: `GET /api/alerts/`

The dashboard queries Grafana alerts to determine device status. Alerts are configured in Grafana with one alert rule per device (or per device role with annotations).

**Response fields consumed**:

| Grafana field   | Dashboard mapping    | Description                              |
|-----------------|----------------------|------------------------------------------|
| id              | (reference)          | Alert ID                                 |
| currentState    | (parsed)             | `OK`, `Alerting`, `Pending`              |
| tags            | (filtered)           | Tags include `hostname:<device>`         |
| annotations     | (parsed)             | Contains device metadata                 |

**Status mapping logic**:

| Alert state      | Dashboard `status` |
|------------------|---------------------|
| `OK`             | `up`               |
| `Alerting`       | `down`             |
| `Pending`        | `unknown`          |
| No alert found   | `unknown`          |

### Alternative: Data Source Health Check

**Endpoint**: `GET /api/datasources/proxy/id/<influxdb_id>/query?q=SHOW FIELD KEYS WITH MEASUREMENT="device_status"`

If alert-based detection is not configured, the dashboard can query InfluxDB directly through the Grafana proxy to check device ping/telemetry status.

---

## Infrastructure Health

Service health for the platform infrastructure (containers, volumes, host resources) is sourced from Grafana dashboards backed by container telemetry (e.g., Telegraf Docker plugin).

### Service Status via Alerting

**Endpoint**: `GET /api/alerts/?tag=service:<name>`

The dashboard queries for alerts tagged with service names to determine infrastructure status:

| Service name    | Tag value              |
|-----------------|------------------------|
| `netbox`        | `service:netbox`       |
| `gitlab`        | `service:gitlab`       |
| `grafana`       | `service:grafana`      |
| `influxdb`      | `service:influxdb`     |
| `telegraf`      | `service:telegraf`     |
| `ansible`       | `service:ansible`      |
| `traefik`       | `service:traefik`      |
| `dashboard`     | `service:dashboard`    |

**Status mapping**:

| Alert state      | Dashboard `status` |
|------------------|---------------------|
| `OK`             | `healthy`          |
| `Alerting`       | `down`             |
| No alert found   | `degraded`         |

---

## Dashboard Deep-Links

The dashboard generates deep-link URLs to Grafana dashboards for device-specific monitoring and infrastructure views. Links open in new browser tabs.

### Device Monitoring Dashboard URL

```
{grafana_url}/d/{dashboard_uid}?var-hostname={hostname}&var-ip={ip_address}
```

| Component           | Source                          |
|---------------------|---------------------------------|
| `grafana_url`       | System setting                  |
| `dashboard_uid`     | Configured in dashboard settings or NetBox custom field `grafana_dashboard` |
| `hostname`          | NetBox device `name`            |
| `ip_address`        | NetBox device primary IP        |

If a device has a custom `grafana_dashboard` value in NetBox custom fields, that UID takes precedence over the default.

### Infrastructure Dashboard URL

```
{grafana_url}/d/{dashboard_uid}
```

The infrastructure dashboard UID is a fixed value configured in system settings (e.g., `container-infrastructure`).

---

## Dashboard List (Optional Discovery)

**Endpoint**: `GET /api/search?type=dash-db`

Used at startup to discover available dashboard UIDs and map them to link targets. The dashboard caches this mapping.

**Response fields consumed**:

| Grafana field   | Dashboard mapping    |
|-----------------|----------------------|
| uid             | Stored for link gen  |
| title           | Display name         |
| uri             | Internal reference   |

---

## Refresh Behavior

- Device status is polled from Grafana every `refresh_interval` seconds (default: 60).
- On dashboard page load, the front-end triggers a fresh status fetch.
- Status results are cached server-side; stale cache is served if Grafana is unreachable.

## Error Handling

| Grafana HTTP status | Dashboard behavior                            |
|---------------------|-----------------------------------------------|
| 401                 | Log error, surface 502 to user                |
| 403                 | Log error, surface 502 to user                |
| 404                 | No alerts configured — all devices show `unknown` |
| 5xx                 | Retry with backoff, serve stale cache         |
| Connection timeout  | Serve stale cache, log warning                |
