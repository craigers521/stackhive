# Feature Specification: Network Operations Dashboard

**Feature Branch**: `001-network-ops-dashboard`

**Created**: 2025-07-23

**Status**: Draft

**Input**: User description: "I want to build a front-end web UI for all the network management, config management and monitoring tools I use to tie them together in a cohesive and user friendly way. All services will be containerized via docker containers and deployable with a single compose file. We will use traefik as a proxy to control direct access to each containers UI via path routes from the front end. The services will consist of ansible for pushing configurations to devices. Configurations will be compiled from multiple modular jinja templates and pushed ideally via netconf so we can control atomic config commits. Inventory management will be handled by netbox with clear device role tagging so we can use dynamic inventory plugin into ansible. The bulk of config variables will live in ansible netbox will only house immutable data like device names and serial numbers as well as the meta tagging. For monitoring i would like to use TIG stack (telegraf, influx and grafana) and we will be configuring model driven telemetry based on netconf-yang as a part of our config templates. I would like some canned grafana dashboard not only for the network devices we will be monitoring but also for monitoring the docker containers as well. All config data, playbooks, persistent data will be source controlled in a local self-managed gitlab community edition instance. We will use gitlab for pipeline deployment jobs as well so we will need a runner on the stack. For device onboarding we will use cisco ZTP scripts, so we need to host a path to the script as well as day 0 configurations. We are primarily working with cisco iosxe catalyst switches but i want to leave the possibility open to be vendor agnostic as well as have some services for onboarding switches to meraki. The webui should allow users to create profiles based on device roles/types, so we will need some understand of the physical interfaces on that device. Ideally in the inventory we can attach a device type, maybe sourced from netbox data, so that we can understand when we apply the profile to the device and attach something like an interface template we know how many interfaces to apply that to. Same with modular uplinks. Thats the high level idea: config templates, applied via ansible, with netbox for inventory mgmt, gitlab for source control and pipelines, and TIG stack for monitoring, with ZTP/onboarding services. Be sure to ask lots of questions as we flesh this out. I prefer the webui to be flask and any backend database services to use mongodb. the webui should use bootstrap for css and little to no javascript, this is for supportability reasons."

## Clarifications

### Session 2025-07-23

- Q: When a configuration profile is applied to a device, should it replace the device's entire running configuration or merge into the existing configuration? → A: Merge/overlay — snippets merge into existing config via NETCONF, preserving unrelated sections
- Q: How should the web UI handle concurrent edits to the same configuration profile? → A: Optimistic locking via Git — merge conflicts detected and resolved in GitLab
- Q: Can an individual device override or supplement the configuration profile assigned to its role? → A: Yes — profiles provide baseline defaults; per-device overrides take precedence at render time
- Q: Should the web UI support multiple user roles with different permissions? → A: Three roles — Viewer (read-only), Editor (create/edit profiles), Admin (approve deployments, manage users)
- Q: Should the web UI display real-time device status or on-demand snapshots? → A: Scheduled refresh with simple up/down status sourced from Grafana; detailed monitoring via cross-links to Grafana; telemetry uses dial-out with its own periodic cycles

### Session 2025-07-23 (UX & Navigation)

- Q: How should the main navigation structure be organized? → A: Persistent left sidebar using Bootstrap grid — always-visible sections, zero JavaScript, maximizes horizontal space for data tables
- Q: What top-level sections should appear in the main sidebar navigation? → A: Inventory, Profiles, Deployments, Monitoring, Onboarding, Settings — plus a Tools sub-menu for cross-launching to Grafana, NetBox, and GitLab via Traefik path routing
- Q: Should cross-launch links to external tools open in the same tab or a new tab? → A: New browser tab — preserves dashboard context while operator references external tools
- Q: Should the device detail page use stacked sections or tabs? → A: Stacked sections on one page with collapsible sections for interfaces, monitoring, and deployment history
- Q: Should the login landing page be a dashboard overview or the inventory list? → A: Dashboard overview with summary cards, recent deployments, device health snapshot, and pending approvals

### Session 2025-07-23 (Ansible & ZTP Structure)

- Q: Should each configuration profile map to an Ansible role, or use group_vars/host_vars with a single role? → A: Single Ansible role with common boilerplate tasks; profiles drive group_vars for role-level defaults; device overrides drive host_vars. Note: "device role" (NetBox categorization) is distinct from "Ansible role" (automation tasks/templates)
- Q: Should Ansible host_vars be static YAML files in Git or generated dynamically at runtime? → A: Static YAML files in Git — web UI writes group_vars and host_vars to the repository; GitLab tracks changes, diffs, and approvals
- Q: Should NetBox inventory be dynamic at Ansible runtime or synced to a static file? → A: Dynamic — Ansible invokes NetBox inventory plugin at runtime to discover devices; ensures source-of-truth consistency
- Q: Should the web UI auto-commit changes to Git, or require manual user commits? → A: Auto-commit to a working branch with descriptive messages; user merges to production branch when ready, triggering approval pipeline
- Q: Should ZTP day-0 config use the same Ansible role or a separate playbook? → A: Separate minimal ZTP playbook that reuses the same Jinja templates with a constrained task set for day-0 bootstrap

### User Story 1 - Browse and Manage Device Inventory (Priority: P1)

As a network operator, I want to see all managed network devices in a single view so I can quickly identify devices, their roles, status, and key attributes without logging into separate systems.

**Why this priority**: Inventory visibility is the foundation for every other operation. Without knowing what devices exist and their current state, no other feature is usable.

**Independent Test**: Can be fully tested by loading the device list page, verifying devices appear with correct attributes, and confirming navigation to individual device detail views.

**Acceptance Scenarios**:

1. **Given** I have just logged in, **When** I land on the Dashboard overview, **Then** I see summary cards showing device health, recent deployments, and pending approvals
2. **Given** I am on the Dashboard overview, **When** I navigate to the Inventory section from the sidebar, **Then** I see a table of all devices showing hostname, role, status, and IP address
3. **Given** I am viewing the device list, **When** I filter by device role, **Then** only devices matching that role are displayed
4. **Given** I am viewing the device list, **When** I click on a device name, **Then** I see a detail page with device metadata, interface information, and current configuration status
5. **Given** I am on a device detail page, **When** I view the interface summary, **Then** I can see all physical interfaces organized by type (management, uplink, access, etc.)

---

### User Story 2 - Create and Manage Configuration Profiles (Priority: P1)

As a network engineer, I want to create configuration profiles based on device roles so I can apply consistent configurations to groups of devices by attaching interface templates and configuration variables.

**Why this priority**: Profiles are the core mechanism for managing configurations at scale. This is the primary value proposition of the tool.

**Independent Test**: Can be fully tested by creating a new profile, adding template sections and variables, associating it with a device role, and verifying the profile appears in the list with correct metadata.

**Acceptance Scenarios**:

1. **Given** I am on the profiles page, **When** I create a new profile, **Then** I can specify a name, associate it with a device role, and define configuration sections
2. **Given** I have a device type with known interface counts, **When** I create an interface template within a profile, **Then** the system presents the correct number of interfaces to configure based on the device type
3. **Given** I have an existing profile, **When** I edit the profile, **Then** I can modify template sections, variables, and interface mappings
4. **Given** I have profiles defined, **When** I view a profile detail page, **Then** I can see all associated templates, variables, and which device roles the profile targets

---

### User Story 3 - Deploy Configuration Changes (Priority: P2)

As a network engineer, I want to apply configuration profiles to devices from the web UI so I can push changes with confidence, knowing the commit is atomic and can be rolled back if needed.

**Why this priority**: Deploying changes is the critical action that delivers value from the profiles and templates. It must be safe and traceable.

**Independent Test**: Can be fully tested by selecting a device or group of devices, applying a profile, confirming the deployment completes, and verifying the configuration appears on the target device.

**Acceptance Scenarios**:

1. **Given** I have a configuration profile and target devices, **When** I initiate a deployment, **Then** I see a preview of the generated configuration before it is applied
2. **Given** I confirm a deployment, **When** the configuration is pushed to a device, **Then** the change is committed atomically and the deployment status reflects success or failure
3. **Given** a deployment has completed, **When** I view deployment history, **Then** I can see all past deployments with timestamps, target devices, applied profiles, and results
4. **Given** a deployment failed, **When** I view the failure details, **Then** I see the error message and the device configuration was not partially applied

---

### User Story 4 - Monitor Device and System Health (Priority: P2)

As a network operator, I want to view monitoring dashboards for both network devices and the infrastructure hosting this platform so I can detect issues quickly from a single place.

**Why this priority**: Monitoring is essential for operational awareness but can be deferred behind core config management functionality.

**Independent Test**: Can be fully tested by navigating to the monitoring section, viewing pre-configured dashboards for devices, and confirming infrastructure health metrics are displayed.

**Acceptance Scenarios**:

1. **Given** network devices are configured to send telemetry data, **When** I navigate to the monitoring section, **Then** I see dashboards with device-specific metrics (interface utilization, CPU, memory, errors)
2. **Given** the platform infrastructure is being monitored, **When** I view the infrastructure dashboard, **Then** I see container health metrics, disk usage, and service status
3. **Given** I am on a device detail page, **When** I view monitoring links, **Then** I can navigate directly to that device's monitoring dashboard

---

### User Story 5 - Onboard New Network Devices (Priority: P3)

As a network engineer, I want to provision new devices through zero-touch onboarding so that devices can be deployed to sites and automatically receive their initial configuration before I connect them manually.

**Why this priority**: ZTP is valuable for site deployments but not needed for day-to-day operations of already-managed devices.

**Independent Test**: Can be fully tested by defining a ZTP configuration for a device in the inventory, confirming the ZTP script and day-0 config are accessible at the expected URL, and verifying a device would receive the correct boot configuration.

**Acceptance Scenarios**:

1. **Given** I have a device in the inventory marked for ZTP provisioning, **When** I create a day-0 configuration for it, **Then** the ZTP service hosts the configuration at the expected URL for that device
2. **Given** a device has completed ZTP boot, **When** I view the device in inventory, **Then** the device shows as onboarded and available for profile-based configuration
3. **Given** I want to onboard a new device to Meraki cloud management, **When** I initiate the Meraki onboarding workflow, **Then** the device receives ZTP configuration with Meraki API commands and appears in inventory flagged as cloud-managed

---

### Edge Cases

- What happens when a device becomes unreachable during configuration deployment?
- What happens when a configuration profile references a device type with no known interface definition?
- What happens when two configuration snippets from different profiles attempt to modify the same configuration section on a device?
- How does the system handle conflicting configurations when multiple profiles target the same device?
- What happens when the inventory source (NetBox) is unavailable?
- What happens when a ZTP boot fails or the script returns errors?
- What happens when a Viewer attempts to create or edit a configuration profile?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST display a browsable inventory of all managed network devices with hostname, IP address, role, status, and device type
- **FR-002**: System MUST support filtering and searching devices by role, type, site, and status
- **FR-003**: System MUST display detailed device information including physical interface layout, serial number, and role tags. The detail page uses stacked collapsible sections for interfaces, monitoring status, and deployment history
- **FR-004**: System MUST allow creation of configuration profiles associated with device roles. Profiles provide a baseline configuration that applies to all devices sharing a role
- **FR-004b**: System MUST support per-device configuration overrides that supplement or modify profile defaults. Device-specific variables are stored separately and merge with profile variables at render time
- **FR-005**: System MUST support modular, composable configuration templates that produce independent configuration snippets. Snippets merge into the existing device configuration rather than replacing it
- **FR-006**: System MUST present the correct number and type of physical interfaces when editing interface templates, based on the device type definition
- **FR-007**: System MUST allow users to define and manage configuration variables separate from device inventory data. Variables are persisted as static YAML files (group_vars for profile-level defaults, host_vars for device overrides) in the Git repository. Variables may be scoped to profiles (role-level defaults) or to individual devices (overrides)
- **FR-008**: System MUST support applying configuration profiles to individual devices or groups of devices. Applied snippets merge into the device's existing configuration. Device-specific overrides take precedence over profile defaults at render time
- **FR-009**: System MUST provide a configuration preview before deployment so users can review generated output
- **FR-010**: System MUST ensure atomic configuration commits via NETCONF so that each snippet either applies completely or is rolled back entirely. Unrelated existing configuration must remain unchanged
- **FR-011**: System MUST record deployment history including timestamp, operator, target device, applied profile, and result
- **FR-012**: System MUST display simple up/down device status in the web UI sourced from Grafana monitoring data. Detailed telemetry is accessed via cross-links that launch users directly to the relevant Grafana dashboard
- **FR-013**: System MUST display simple infrastructure service status in the web UI. Detailed container health metrics are accessed via cross-links that launch users directly to the relevant Grafana dashboard
- **FR-014**: System MUST support generation and hosting of ZTP boot scripts and day-0 configurations for new devices
- **FR-015**: System MUST support onboarding new devices to Meraki cloud management via ZTP with Meraki API commands. Onboarded Meraki devices MUST remain in the local inventory with a flag indicating they are cloud-managed and not directly configurable
- **FR-015b**: Brownfield migration of existing devices from local management to Meraki cloud is acknowledged as a future capability and is out of scope for the initial release
- **FR-016**: System MUST integrate with a version control system to track all configuration and template changes. Changes are auto-committed to a working branch with descriptive commit messages. Concurrent edits to profiles rely on Git's native merge conflict detection and resolution for conflict handling
- **FR-017**: System MUST support configuration deployment via CI/CD pipelines with review-and-approval gates. Pipeline status and approval state MUST be visible from the web UI. Changes are merged to the production branch via merge request, triggering the approval pipeline
- **FR-018**: System MUST present a clean, simple web interface optimized for supportability with minimal client-side scripting. The layout uses a persistent left sidebar for main navigation with Bootstrap grid, keeping all sections always visible and maximizing horizontal space for data tables
- **FR-018c**: System MUST provide six main navigation sections: Inventory, Profiles, Deployments, Monitoring, Onboarding, and Settings. The default landing page after login is a Dashboard overview
- **FR-018d**: Dashboard overview MUST display summary cards including device health snapshot, recent deployment activity, and pending approval items
- **FR-018e**: System MUST include a Tools sub-menu in the sidebar for cross-launching to external services (Grafana, NetBox, GitLab) via Traefik path-based routing. Cross-launch links open in new browser tabs to preserve dashboard context
- **FR-018b**: System MUST enforce role-based access control with three roles: Viewer (read-only inventory and monitoring access), Editor (create and edit profiles, templates, and variables), and Admin (approve deployments, manage users and roles, system configuration)
- **FR-019**: System MUST support Cisco IOS-XE as the initial target platform. The configuration template and deployment architecture MUST be structured to accommodate additional vendor platforms without redesign
- **FR-020**: System MUST synchronize device inventory from an external inventory management system, with immutable device data (serial numbers, hostnames, role tags) stored externally and mutable configuration data managed locally

### Key Entities

- **Device**: Represents a network device with immutable attributes (hostname, serial number, MAC, role tags, device type) sourced from the inventory system and operational attributes (current config status, last deployment time, monitoring status, cloud-managed flag)
- **Device Type**: Defines the physical characteristics of a device model including interface count, interface types, slot configurations, and modular uplink options. May be sourced from inventory system data.
- **Configuration Profile**: A named collection of templates, variables, and interface mappings targeted at a specific device role. Profiles provide baseline configuration defaults stored as Ansible group_vars. Profiles are reusable across multiple devices sharing the same role. Note: "device role" is a NetBox categorization concept, distinct from the Ansible role (the automation task/templating engine)
- **Configuration Template**: A modular template fragment that produces a self-contained configuration snippet (e.g., VLANs, routing, interfaces, QoS). Snippets merge into the device's existing configuration rather than replacing it. Templates are rendered by a single Ansible role that applies common boilerplate tasks (AAA, MDT, etc.)
- **Interface Template**: A template applied to a set of physical interfaces on a device, defining configuration that should be applied to each matching interface (e.g., access port settings, trunk settings, uplink settings)
- **Deployment Record**: An immutable log entry capturing a configuration deployment event: who triggered it, which templates were deployed as snippets, to which devices, when, and the outcome. Records which config sections were added, modified, or removed by the merge operation
- **Configuration Variable**: Key-value data used in templates to generate device-specific configurations. Stored separately from immutable inventory data. Variables at the profile level map to Ansible group_vars (role-level defaults). Variables at the device level map to Ansible host_vars (overrides that take precedence at render time)
- **ZTP Provision**: The day-0 boot configuration and script hosted for a device pending its initial deployment and first boot. ZTP config is generated by the same Ansible role using minimal group_vars and host_vars for day-0 bootstrap
- **Device Override**: Per-device configuration variables or template overrides that supplement a device's inherited role profile. Overrides take precedence over profile defaults at render time and map to Ansible host_vars. Managed separately from profile group_vars data
- **User Role**: Permission level assigned to authenticated users. Three roles exist: Viewer (read-only inventory and monitoring), Editor (create/edit profiles, templates, and variables), and Admin (approve deployments, manage users and roles, system configuration)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Network operators can view the full device inventory and drill into a specific device's detail page within 3 clicks
- **SC-002**: Engineers can create a new configuration profile from scratch in under 5 minutes for a known device role
- **SC-003**: Configuration deployment from profile to device completes with visible status feedback within 2 minutes for a single device
- **SC-004**: 100% of configuration changes are tracked in version control with full history and rollback capability
- **SC-005**: Zero partial configuration commits — each configuration snippet either merges completely or is rolled back entirely, leaving unrelated existing config unchanged
- **SC-006**: Operators can navigate from the device inventory to a device's monitoring dashboard in 2 clicks or fewer
- **SC-007**: New devices can be provisioned via ZTP with day-0 configurations prepared and accessible before physical deployment
- **SC-008**: All services are deployable with a single compose command and accessible through a unified entry point

## Assumptions

- The platform will be deployed on-premise in a controlled network environment with access to managed devices
- An external inventory management system (NetBox) is available and properly configured as the source of truth for immutable device inventory data
- GitLab CE is available for version control and CI/CD pipeline orchestration
- Network devices support NETCONF/YANG for configuration management and model-driven telemetry
- Device types and their interface layouts can be determined from NetBox device type data or a local type database
- Primary use case is Cisco IOS-XE Catalyst switches; support for other vendors is deferred to later phases
- Users accessing the UI are authenticated through a local user database managed within the platform. Enterprise SSO integration is deferred to a later phase
- Users are assigned one of three roles: Viewer, Editor, or Admin. Role assignments are managed by Admin users
- The platform admin has access to manage Docker containers and the compose deployment
- Configuration variable data volume is manageable within a single database instance for initial deployment
- Model-driven telemetry uses dial-out configuration with its own periodic cycles; the web UI does not collect telemetry directly but sources status data from Grafana
- Configuration profiles map to Ansible group_vars for role-level defaults; device overrides map to host_vars. A single Ansible role handles common boilerplate tasks (AAA, MDT, ZTP bootstrap). ZTP day-0 configuration uses a separate minimal playbook that reuses the same Jinja templates with a constrained task set. The term "device role" refers to NetBox categorization and is distinct from the Ansible role concept
