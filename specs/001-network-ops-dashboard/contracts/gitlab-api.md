# GitLab CE API Integration Contract

The dashboard uses a self-managed GitLab CE instance for version control of configuration data and CI/CD pipeline orchestration.

## Authentication

The dashboard authenticates to GitLab using a personal access token or project access token configured in system settings.

| Header            | Value format                     |
|-------------------|----------------------------------|
| PRIVATE-TOKEN     | `<gitlab_access_token>`          |

The token must have `api` scope and be authorized on the ansible vars project.

## Base URL

Configured via `gitlab_url` in system settings. The dashboard accesses both the REST API (`/api/v4/`) and the Git repository directly (local clone or git protocol).

## Runner Execution Model

The GitLab Runner uses the Docker executor with the Docker socket mounted. The runner has direct network access to managed devices for NETCONF pushes. The `ansible/` directory is bind-mounted read-only into both the Flask app and runner job containers.

### Runner Container Config

```yaml
gitlab-runner:
  image: gitlab/gitlab-runner:v17.0
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - ./ansible:/ansible:ro
    - runner-config:/etc/gitlab-runner
  network_mode: host  # Direct device network access
```

### Job Container Config

Each CI job runs in a sibling container spawned by the runner:

```yaml
# .gitlab-ci.yml
deploy_configs:
  image: python:3.12-slim
  variables:
    ANSIBLE_CONFIG: /ansible/ansible.cfg
  script:
    - pip install ansible-core cisco.ios ansible.netcommon netbox_community.netbox
    - ansible-playbook -i /ansible/inventory/netbox.yml /ansible/site.yml
      --limit "$TARGET_DEVICES" -e "deploy_mode=live"
```

The job container inherits:
- **Docker socket**: For spawning nested containers if needed
- **Network access**: Same network namespace as runner (reaches managed devices)
- **Ansible bind mount**: `/ansible` directory with templates, roles, vars, playbooks

---

## Repository Structure

The ansible variables repository follows this layout:

```
ansible-vars/
  group_vars/
    <device_role>/    # One directory per NetBox device role
      vars.yml        # Profile-level default variables
  host_vars/
    <hostname>/       # One directory per device
      vars.yml        # Per-device override variables
  roles/
    network_config/   # Single Ansible role
      tasks/
      templates/
      ztp/            # ZTP-specific template subset
  playbooks/
    deploy.yml        # Main deployment playbook
    ztp.yml           # ZTP bootstrap playbook
  .gitlab-ci.yml      # CI/CD pipeline definition
```

- `group_vars/<role>/vars.yml` maps to a configuration profile. The dashboard writes profile variables to these files.
- `host_vars/<hostname>/vars.yml` maps to per-device overrides. The dashboard writes device overrides to these files.
- Changes to either directory are auto-committed to the working branch.

---

## Branch Strategy

| Branch            | Purpose                                                      |
|-------------------|--------------------------------------------------------------|
| `main`            | Production branch — approved, deployed configurations        |
| `working`         | Working branch — auto-committed changes from the dashboard   |

Configurable via `git_production_branch` and `git_working_branch` in system settings.

### Auto-Commit Workflow

When an Editor modifies a profile or device override via the dashboard:

1. The dashboard updates the corresponding YAML file in the local clone.
2. A git commit is created on the `working` branch with a descriptive message:
   - Profile edit: `profile: update <profile_name> — <summary of change>`
   - Override edit: `override: update <hostname> — <summary of change>`
3. The commit is pushed to the GitLab `working` branch.

**Error handling**:
- If the push is rejected (remote has new commits), the dashboard attempts a rebase.
- If a merge conflict occurs during rebase, the dashboard surfaces a 409 Conflict to the user with details.
- The user (or admin) must resolve the conflict via GitLab's merge request interface.

### Merge Request Trigger

When an Editor submits a deployment:

1. A merge request is created from `working` -> `main`.
2. The MR description includes:
   - List of affected devices
   - Profile(s) being deployed
   - Configuration diff preview
3. The MR triggers the CI/CD pipeline defined in `.gitlab-ci.yml`.
4. An Admin must approve and merge the MR to proceed.

---

## GitLab API Endpoints Used

### Creating Merge Requests

**Endpoint**: `POST /api/v4/projects/{project_id}/merge_requests`

| Parameter         | Value                             |
|-------------------|-----------------------------------|
| source_branch     | `working` (from settings)         |
| target_branch     | `main` (from settings)            |
| title             | `Deploy <profile> to <devices>`  |
| description       | Auto-generated deployment summary |
| remove_source_branch | `false`                       |

**Response fields consumed**:

| GitLab field   | Dashboard mapping    | Description                     |
|----------------|----------------------|---------------------------------|
| iid            | (stored)             | MR IID                          |
| web_url        | (stored)             | Link to MR in GitLab UI         |
| state          | (tracked)            | `opened`, `locked`, `merged`    |

### Merging (Approving) Merge Requests

**Endpoint**: `POST /api/v4/projects/{project_id}/merge_requests/{mr_iid}/merge`

Called by Admin when approving a deployment.

| Parameter              | Value   |
|------------------------|---------|
| should_remove_source_branch | `false` |

**Response fields consumed**:

| GitLab field   | Dashboard mapping    |
|----------------|----------------------|
| merged         | Deployment status    |
| sha            | Merge commit SHA     |

### Pipeline Status

**Endpoint**: `GET /api/v4/projects/{project_id}/pipelines/{pipeline_id}`

Used to poll deployment pipeline progress and display status in the dashboard.

**Response fields consumed**:

| GitLab field   | Dashboard mapping    | Description                       |
|----------------|----------------------|-----------------------------------|
| id             | `pipeline_id`        | Pipeline ID                       |
| status         | `status`             | `pending`, `running`, `success`, `failed`, `canceled` |
| ref            | (stored)             | Source branch                     |
| sha            | (stored)             | Commit SHA                        |
| web_url        | (stored)             | Link to pipeline in GitLab UI     |
| created_at     | (stored)             | Pipeline start time               |
| updated_at     | (stored)             | Pipeline last update time         |

**Endpoint**: `GET /api/v4/projects/{project_id}/pipelines/?ref={branch}&per_page=10`

Used to list recent pipelines for deployment history.

### Pipeline Jobs

**Endpoint**: `GET /api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs`

Used to get individual job details for deployment error reporting.

**Response fields consumed**:

| GitLab field   | Dashboard mapping    |
|----------------|----------------------|
| name           | Job name             |
| status         | Job status           |
| failure_reason | Error cause          |
| trace_url      | Log URL              |
| staged_on       | Execution timing     |

---

## CI/CD Pipeline Definition

The `.gitlab-ci.yml` in the repository defines at minimum:

| Stage         | Job                  | Description                                  |
|---------------|----------------------|----------------------------------------------|
| validate      | `validate-yaml`      | Syntax check on group_vars and host_vars     |
| render        | `render-configs`     | Dry-run Ansible template rendering           |
| deploy        | `deploy-configs`     | Execute Ansible playbook via NETCONF         |
| notify        | `notify-status`      | Webhook back to dashboard with results       |

### Webhook Callback

After pipeline completion, the `deploy` or `notify` job sends a webhook POST to the dashboard:

**Endpoint**: `POST /api/webhooks/gitlab/pipeline`

**Request body**:
| Field         | Type   | Description                              |
|---------------|--------|------------------------------------------|
| pipeline_id   | int    | GitLab pipeline ID                       |
| status        | string | `success` or `failed`                    |
| commit_sha    | string | Commit SHA                               |
| deployed_at   | string | ISO 8601 deployment completion time      |
| devices       | array[obj]| Per-device deployment results         |

Per-device result:
| Field     | Type    | Description                           |
|-----------|---------|---------------------------------------|
| hostname  | string  | Device hostname                       |
| status    | string  | `success`, `failed`                   |
| message   | string  | Error or success detail               |
| diff      | string  | Config diff (sections added/removed)  |

---

## Error Handling

| GitLab HTTP status | Dashboard behavior                            |
|--------------------|-----------------------------------------------|
| 401                | Log error, surface 503 to user                |
| 403                | Log error, surface 503 to user                |
| 404                | Log error, surface 404 for repo operations    |
| 405                | Pipeline already completed                    |
| 422                | Merge conflict — surface 409 with details     |
| 5xx                | Retry with backoff, surface 503 after 3 retries |
