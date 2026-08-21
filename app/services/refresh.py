"""Background refresh loop work items.

Each function is safe to call inside an application context; failures are
logged and never crash the refresh thread.
"""
import logging

logger = logging.getLogger(__name__)


def refresh_device_statuses(app):
    """Poll Grafana alert states and update Device.monitoring_status + last_check."""
    from datetime import datetime, timezone

    from ..models import Device
    from . import grafana

    try:
        statuses = grafana.get_device_statuses(app)
    except Exception as exc:
        logger.warning("status_refresh_skipped reason=%s", exc)
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    known = 0
    for device in Device.query.all():
        status = statuses.get(device.hostname, "unknown")
        if status != device.monitoring_status:
            device.monitoring_status = status
        if device.hostname in statuses:
            known += 1
        device.last_check = now

    from ..extensions import db
    db.session.commit()
    logger.info("status_refresh_complete devices=%d", known)


def refresh_inventory(app):
    """Re-sync the local device cache from NetBox."""
    from .netbox import sync_inventory

    try:
        report = sync_inventory(app)
        logger.info("inventory_refresh_complete %s", report)
    except Exception as exc:
        logger.warning("inventory_refresh_failed reason=%s", exc)


def refresh_deployment_pipelines(app):
    """Fallback poll: reconcile in-flight deployments against GitLab pipelines.

    Catches records whose pipeline webhook was missed so they do not stay
    ``running`` indefinitely (FR-017); a no-op when GitLab is unconfigured.
    """
    from . import gitlab

    try:
        gitlab.sync_inflight_pipelines(app)
    except Exception as exc:  # noqa: BLE001 - never crash the refresh thread
        logger.warning("pipeline_poll_failed reason=%s", exc)


def refresh_drift_check(app):
    """Nightly drift sweep: read back deployed devices, flag drift as modified.

    Interior to the function: setting gate (``drift_check_enabled``) and the
    02:00-local once-per-day fire schedule (T075).
    """
    from . import drift

    try:
        drift.nightly_drift_check(app)
    except Exception as exc:  # noqa: BLE001 - never crash the refresh thread
        logger.warning("drift_check_failed reason=%s", exc)


ZTP_TERMINAL_STATES = ("onboarded", "failed", "cancelled")
ZTP_ARTIFACT_RETENTION_DAYS = 30
_artifact_cleanup_last_run = None


def refresh_ztp_artifact_cleanup(app):
    """Daily purge of served artifacts for long-finished ZTP provisions.

    Provisions in a terminal state (onboarded/failed/cancelled) whose last
    change is older than 30 days have their git-hosted artifacts deleted
    (best-effort; DB content is cleared even when Git is unreachable) and
    their stored config/script content blanked. The provision row itself is
    kept for audit history per the data-model ZTP flow. Runs at most once per
    UTC day; the ``artifact_purged`` flag makes re-runs idempotent.
    """
    global _artifact_cleanup_last_run
    from datetime import datetime, timedelta, timezone

    from ..extensions import db
    from ..models import ZTPProvision
    from . import gitlab, settings as settings_service

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if _artifact_cleanup_last_run == now.date():
        return
    cutoff = now - timedelta(days=ZTP_ARTIFACT_RETENTION_DAYS)
    candidates = (
        ZTPProvision.query
        .filter(ZTPProvision.status.in_(ZTP_TERMINAL_STATES))
        .filter(ZTPProvision.updated_at < cutoff)
        .filter_by(artifact_purged=False)
        .all()
    )
    if candidates:
        client = None
        try:
            client = gitlab.make_client(app)
        except Exception as exc:
            logger.warning("ztp_cleanup_git_unavailable reason=%s (clearing DB content only)", exc)
        branch = settings_service.get_setting(app, "git_working_branch") or "working"
        purged = 0
        for provision in candidates:
            if client is not None and provision.git_path:
                serial = provision.device.serial_number if provision.device else str(provision.id)
                files = {
                    f"{provision.git_path}script.txt": None,
                    f"{provision.git_path}day-0.cfg": None,
                }
                try:
                    client.push_with_rebase(branch, f"ztp: purge {serial} terminal-state artifacts", files)
                except Exception as exc:
                    logger.warning("ztp_cleanup_git_failed id=%s git_path=%s reason=%s", provision.id, provision.git_path, exc)
            provision.config_content = ""
            provision.script_content = ""
            provision.artifact_purged = True
            purged += 1
        db.session.commit()
        logger.info("ztp_artifacts_purged count=%d", purged)
    _artifact_cleanup_last_run = now.date()


def refresh_ztp_onboarding(app):
    """Move delivered ZTP provisions to onboarded once their device reports up."""
    from ..models import Device, ZTPProvision

    onboarded = 0
    for provision in ZTPProvision.query.filter_by(status="delivered").all():
        if provision.device is not None and provision.device.monitoring_status == "up":
            if provision.set_status("onboarded"):
                if provision.device.set_config_status("onboarded"):
                    onboarded += 1
    if onboarded:
        from ..extensions import db

        db.session.commit()
        logger.info("ztp_onboarded count=%d", onboarded)
