"""GitLab CE REST client: commits, merge requests, pipelines.

Commits use the repository commits API with ``start_branch`` anchoring; a
rejected commit (remote moved) triggers one rebase-style retry against the
fresh branch tip, and a second failure surfaces as ``GitLabConflict`` (409).
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)


class GitLabError(Exception):
    """Base error for GitLab integration failures."""

    def __init__(self, message, status=None):
        """Keep the error message plus the upstream status when known."""
        super().__init__(message)
        self.status = status


class GitLabUnavailable(GitLabError):
    """GitLab unreachable or auth failed (surfaced as 503)."""


class GitLabNotFound(GitLabError):
    """Object missing in GitLab (surfaced as 404)."""


class GitLabConflict(GitLabError):
    """Merge/conflict condition (surfaced as 409)."""


class GitLabClient:
    """Thin client over the GitLab REST API (PRIVATE-TOKEN auth)."""

    def __init__(self, base_url, token, project_id, timeout=10, retries=3):
        """Store endpoint, token, project id, timeout, retries; prepare the session."""
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.project_id = project_id
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"PRIVATE-TOKEN": token})

    def _request(self, method, path, json_body=None, params=None, _retry=True):
        """Perform a request with 5xx retry/backoff and standard error mapping."""
        last = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json_body,
                    params=params,
                    timeout=self.timeout,
                )
                if resp.status_code in (401, 403):
                    logger.error("gitlab_auth_failed path=%s", path)
                    raise GitLabUnavailable(f"GitLab authentication failed ({resp.status_code})")
                if resp.status_code == 404:
                    raise GitLabNotFound(f"NotFound in GitLab: {path}")
                if 500 <= resp.status_code < 600:
                    last = GitLabUnavailable(f"GitLab server error {resp.status_code}")
                    if attempt < self.retries:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    raise last
                return resp
            except requests.RequestException as exc:
                if isinstance(exc, (GitLabError,)):
                    if isinstance(exc, (GitLabUnavailable, GitLabNotFound)):
                        raise
                    last = exc
                else:
                    last = GitLabUnavailable(f"GitLab unavailable: {exc}")
                if _retry and attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                if isinstance(last, (GitLabUnavailable, GitLabNotFound, GitLabConflict, GitLabError)):
                    raise last
                raise last

    # -- repository --------------------------------------------------------

    def latest_branch_sha(self, branch):
        """Return the current tip SHA of a branch."""
        resp = self._request(
            "GET", f"/api/v4/projects/{self.project_id}/repository/branches/{requests.utils.quote(branch)}"
        )
        resp.raise_for_status()
        return resp.json()["commit"]["id"]

    def _file_exists(self, file_path, ref):
        """Return True when the file exists on the ref."""
        try:
            self._request(
                "GET",
                f"/api/v4/projects/{self.project_id}/repository/files/{requests.utils.quote(file_path, safe='')}",
                params={"ref": ref},
                _retry=False,
            )
        except GitLabNotFound:
            return False
        return True

    def commit_files(self, branch, message, files, start_branch=None):
        """Commit a batch of file changes in one atomic API commit.

        ``files`` maps repo-relative path to content; ``None`` deletes the
        file. Existence is probed per path to pick create/update actions.
        """
        actions = []
        for file_path, content in files.items():
            if content is None:
                if self._file_exists(file_path, branch):
                    actions.append({"action": "delete", "file_path": file_path})
                continue
            action = "update" if self._file_exists(file_path, branch) else "create"
            actions.append({"action": action, "file_path": file_path, "content": content})
        if not actions:
            return self.latest_branch_sha(branch)
        body = {"branch": branch, "message": message, "actions": actions}
        if start_branch:
            body["start_branch"] = start_branch
        resp = self._request(
            "POST", f"/api/v4/projects/{self.project_id}/repository/commits", json_body=body, _retry=False
        )
        if resp.status_code in (400, 409, 422):
            detail = ""
            try:
                detail = str(resp.json())
            except ValueError:
                detail = resp.text[:200]
            raise GitLabError(f"commit rejected ({resp.status_code}): {detail}", status=resp.status_code)
        resp.raise_for_status()
        data = resp.json()
        return data.get("id")

    def push_with_rebase(self, branch, message, files):
        """Commit to a shared branch with one rebase retry on push rejection.

        Anchors the commit to the current branch tip; if the remote tip moved
        (concurrent editor), refetch the tip and retry once. A second failure
        raises ``GitLabConflict`` for the user to resolve.
        """
        base = self.latest_branch_sha(branch)
        try:
            return self.commit_files(branch, message, files, start_branch=base)
        except GitLabError as first_error:
            fresh = self.latest_branch_sha(branch)
            if fresh == base:
                raise GitLabConflict(f"rebase conflict on {branch}: {first_error}") from first_error
            try:
                return self.commit_files(branch, message, files, start_branch=fresh)
            except GitLabError as second_error:
                raise GitLabConflict(f"rebase conflict on {branch}: {second_error}") from second_error

    # -- merge requests ------------------------------------------------------

    def create_merge_request(self, source_branch, target_branch, title, description=""):
        """Open an MR from the working branch to the production branch."""
        resp = self._request(
            "POST",
            f"/api/v4/projects/{self.project_id}/merge_requests",
            json_body={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
                "remove_source_branch": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data

    def create_branch(self, branch, ref):
        """Create a heads branch from the given ref (SHA or branch name)."""
        resp = self._request(
            "POST",
            f"/api/v4/projects/{self.project_id}/repository/branches",
            json_body={"branch": branch, "ref": ref},
        )
        if resp.status_code in (400, 409):
            raise GitLabConflict(f"branch already exists or invalid ref: {branch}")
        resp.raise_for_status()
        return resp.json()

    def list_merge_requests(self, source_branch=None, state="all"):
        """List MRs, optionally filtered by source branch."""
        params = {"state": state, "per_page": 10}
        if source_branch:
            params["source_branch"] = source_branch
        resp = self._request("GET", f"/api/v4/projects/{self.project_id}/merge_requests", params=params)
        resp.raise_for_status()
        return resp.json()

    def merge_merge_request(self, mr_iid):
        """Merge an MR (admin approval gate)."""
        resp = self._request(
            "POST",
            f"/api/v4/projects/{self.project_id}/merge_requests/{mr_iid}/merge",
            json_body={"should_remove_source_branch": False},
        )
        if resp.status_code == 422:
            raise GitLabConflict("merge request cannot be merged (conflict or pipeline failing)")
        resp.raise_for_status()
        return resp.json()

    def get_merge_request(self, mr_iid):
        """Fetch MR state for status tracking."""
        resp = self._request("GET", f"/api/v4/projects/{self.project_id}/merge_requests/{mr_iid}")
        resp.raise_for_status()
        return resp.json()

    # -- pipelines -----------------------------------------------------------

    def get_pipeline(self, pipeline_id):
        """Pipeline status object for a pipeline id."""
        resp = self._request("GET", f"/api/v4/projects/{self.project_id}/pipelines/{pipeline_id}")
        resp.raise_for_status()
        return resp.json()

    def get_pipeline_jobs(self, pipeline_id):
        """Jobs of a pipeline (error reporting)."""
        resp = self._request("GET", f"/api/v4/projects/{self.project_id}/pipelines/{pipeline_id}/jobs")
        resp.raise_for_status()
        return resp.json()

    def list_pipelines(self, ref=None, per_page=10):
        """Recent pipelines, optionally filtered by branch ref."""
        params = {"per_page": per_page}
        if ref:
            params["ref"] = ref
        resp = self._request("GET", f"/api/v4/projects/{self.project_id}/pipelines", params=params)
        resp.raise_for_status()
        return resp.json()


INFLIGHT_DEPLOYMENT_STATUSES = ("pending", "approved", "running")
RUNNING_PIPELINE_STATES = ("created", "pending", "preparing", "running")


def _failed_jobs_suffix(client, pipeline_id):
    """Failed job names for a pipeline error message ('' when unknown)."""
    if not pipeline_id:
        return ""
    try:
        jobs = client.get_pipeline_jobs(pipeline_id)
    except GitLabError:
        return ""
    names = [job.get("name") for job in jobs or [] if job.get("status") == "failed"]
    return f" (failed jobs: {', '.join(names)})" if names else ""


def _resolve_pipeline(client, record, app):
    """Best-effort pipeline object for an in-flight deployment record.

    Resolution order: known ``pipeline_id`` → the open MR for the record's
    deploy branch (its latest pipeline) →, once merged, the newest pipeline
    on the production branch. Returns None when nothing is found yet.
    """
    if record.pipeline_id:
        return client.get_pipeline(record.pipeline_id)
    branch = (record.git_branch or "").strip()
    if not branch:
        return None
    from . import settings as settings_service

    try:
        mrs = client.list_merge_requests(source_branch=branch, state="all")
    except GitLabError:
        mrs = []
    for mr in (mrs or [])[:1]:
        pipeline = mr.get("pipeline")
        if isinstance(pipeline, dict) and pipeline.get("id"):
            return pipeline
        if mr.get("state") == "merged":
            ref = settings_service.get_setting(app, "git_production_branch") or "main"
            pipelines = client.list_pipelines(ref=ref, per_page=5)
            if pipelines:
                return pipelines[0]
    return None


def sync_inflight_pipelines(app):
    """Poll GitLab to reconcile in-flight deployment records (webhook fallback).

    Records whose pipeline webhook was missed (GitLab briefly down, runner
    misconfiguration) would otherwise sit in ``pending``/``approved``/
    ``running`` indefinitely. This uses the existing ``get_pipeline`` /
    ``get_pipeline_jobs`` client calls to apply the same status transitions
    the webhook performs — including per-device finalize and the
    ``Device.config_status`` updates — and is driven from the background
    refresh loop. Returns the number of records whose state changed.
    """
    from datetime import datetime, timezone

    from ..extensions import db
    from ..models import DeploymentRecord

    try:
        client = make_client(app)
    except Exception as exc:  # noqa: BLE001 - GitLab absent means nothing to poll
        logger.warning("pipeline_poll_skipped reason=%s", exc)
        return 0

    records = DeploymentRecord.query.filter(
        DeploymentRecord.status.in_(INFLIGHT_DEPLOYMENT_STATUSES)
    ).all()
    changed = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for record in records:
        try:
            pipeline = _resolve_pipeline(client, record, app)
        except GitLabError as exc:
            logger.warning("pipeline_poll_failed id=%s reason=%s", record.id, exc)
            continue
        if pipeline is None:
            continue
        status = pipeline.get("status")
        pipeline_id = pipeline.get("id")
        web_url = pipeline.get("web_url")
        try:
            if status in RUNNING_PIPELINE_STATES and record.status in ("pending", "approved"):
                record.status = "running"
                if pipeline_id:
                    record.pipeline_id = pipeline_id
                if web_url:
                    record.pipeline_url = web_url
                record.pipeline_status = status
                changed += 1
            elif status == "success" and record.status != "success":
                record.status = "success"
                record.completed_at = now
                record.pipeline_status = status
                if pipeline_id:
                    record.pipeline_id = pipeline_id
                if web_url:
                    record.pipeline_url = web_url
                _finalize_devices(record, success=True, now=now)
                changed += 1
            elif status in ("failed", "canceled", "skipped") and record.status not in ("success", "cancelled"):
                record.status = "failed" if status == "failed" else "cancelled"
                record.completed_at = now
                record.pipeline_status = status
                record.error_message = (
                    f"pipeline {pipeline_id} {status}" + _failed_jobs_suffix(client, pipeline_id)
                )
                _finalize_devices(record, success=False, now=now)
                changed += 1
            if changed:
                db.session.flush()
        except Exception as exc:  # noqa: BLE001 - never let one record stall the poll
            logger.warning("pipeline_poll_update_failed id=%s reason=%s", record.id, exc)
            db.session.rollback()
    if changed:
        db.session.commit()
        logger.info("pipeline_poll_reconciled count=%d", changed)
    return changed


def _finalize_devices(record, success, now, device_results=None):
    """Propagate a pipeline outcome to per-device rows (reuses the webhook logic)."""
    from ..routes import deployments as deployments_routes

    deployments_routes._finalize_devices(record, success, now, device_results)


def make_client(app):
    """Build a configured GitLabClient from app settings/credentials."""
    from . import credential, settings

    base_url = settings.get_setting(app, "gitlab_url") or app.config["GITLAB_URL"]
    token, _row = credential.resolve_token(app, "gitlab", "GITLAB_TOKEN")
    project_id = settings.get_setting(app, "gitlab_project_id") or app.config.get("GITLAB_PROJECT_ID", "")
    if not project_id:
        raise GitLabUnavailable("GITLAB_PROJECT_ID is not configured")
    return GitLabClient(
        base_url,
        token,
        project_id,
        timeout=app.config["HTTP_TIMEOUT"],
        retries=app.config["HTTP_RETRIES"],
    )
