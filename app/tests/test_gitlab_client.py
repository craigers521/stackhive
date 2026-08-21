"""GitLab client unit tests: commit, rebase retry, conflicts, MRs, pipelines."""
import pytest

from app.services.gitlab import (
    GitLabClient,
    GitLabConflict,
    GitLabError,
)


class FakeResponse:
    """Minimal requests.Response double with status and body."""
    def __init__(self, status_code, json_body=None, text=""):
        """Store the status code and response body."""
        self.status_code = status_code
        self._json = json_body
        self.text = text or (str(json_body) if json_body is not None else "")

    def json(self):
        """Parse and return the stored body as JSON."""
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        """Raise on 4xx/5xx status codes."""
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Programmable stand-in for requests.Session; keyed by (method, url)."""

    def __init__(self, script):
        # script: list of (method, url_substr, response) matched in order
        """requests.Session double returning queued responses."""
        self.script = list(script)
        self.headers = {}
        self.calls = []

    def request(self, method, url, json=None, params=None, timeout=None):
        """Record the request and return the next queued response."""
        self.calls.append({"method": method, "url": url, "json": json, "params": params, "timeout": timeout})
        for i, (m, needle, resp) in enumerate(self.script):
            if m == method and needle in url:
                self.script.pop(i)
                return resp
        return FakeResponse(200, {})

    def get(self, url, params=None, timeout=None):
        """GET passthrough to the recorded request."""
        return self.request("GET", url, params=params, timeout=timeout)

    def post(self, url, json=None, params=None, timeout=None):
        """POST passthrough to the recorded request."""
        return self.request("POST", url, json=json, params=params, timeout=timeout)


def make_client(script):
    """Build a GitLabClient over the fake session."""
    client = GitLabClient("http://gitlab.test", "tok", 7, timeout=2, retries=0)
    client.session = FakeSession(script)
    return client


def test_commit_files_creates_and_updates():
    """commit_files creates new paths and updates existing ones."""
    client = make_client(
        [
            ("GET", "/repository/files/group_vars%2Fleaf%2Fvars.yml", FakeResponse(404)),
            ("GET", "/repository/files/group_vars%2Fleaf%2Fdels.yml", FakeResponse(200, {})),
            ("POST", "/repository/commits", FakeResponse(200, {"id": "abc123"})),
        ]
    )
    sha = client.commit_files("working", "profile: update leaf", {"group_vars/leaf/vars.yml": "k: v", "group_vars/leaf/dels.yml": None})
    assert sha == "abc123"
    commit = [c for c in client.session.calls if c["method"] == "POST"][0]
    actions = {a["file_path"]: a["action"] for a in commit["json"]["actions"]}
    assert actions == {"group_vars/leaf/vars.yml": "create", "group_vars/leaf/dels.yml": "delete"}
    assert commit["json"]["message"] == "profile: update leaf"


def test_push_rejected_triggers_rebase_retry():
    """A rejected push triggers one rebase-and-retry cycle."""
    tip_1 = {"commit": {"id": "sha-one"}}
    tip_2 = {"commit": {"id": "sha-two"}}
    client = make_client(
        [
            ("GET", "/repository/branches/working", FakeResponse(200, tip_1)),
            ("POST", "/repository/commits", FakeResponse(409, {"message": "ref lock"})),
            ("GET", "/repository/branches/working", FakeResponse(200, tip_2)),
            ("POST", "/repository/commits", FakeResponse(200, {"id": "retry-sha"})),
        ]
    )
    sha = client.push_with_rebase("working", "msg", {"f.yml": "x"})
    assert sha == "retry-sha"
    commits = [c for c in client.session.calls if c["method"] == "POST"]
    assert commits[0]["json"]["start_branch"] == "sha-one"
    assert commits[1]["json"]["start_branch"] == "sha-two"


def test_push_rejected_twice_raises_gitlab_conflict():
    """Two rejected pushes raise GitLabConflict."""
    client = make_client(
        [
            ("GET", "/repository/branches/working", FakeResponse(200, {"commit": {"id": "sha-one"}})),
            ("POST", "/repository/commits", FakeResponse(409, {"message": "ref lock"})),
            ("GET", "/repository/branches/working", FakeResponse(200, {"commit": {"id": "sha-one"}})),
        ]
    )
    with pytest.raises(GitLabConflict) as exc:
        client.push_with_rebase("working", "msg", {"f.yml": "x"})
    assert "rebase conflict" in str(exc.value)


def test_push_rejected_with_unknown_error_raises_conflict():
    """A non-409 rejection also surfaces as GitLabConflict."""
    client = make_client(
        [
            ("GET", "/repository/branches/working", FakeResponse(200, {"commit": {"id": "sha-one"}})),
            ("POST", "/repository/commits", FakeResponse(400, {"message": "file too large"})),
            ("GET", "/repository/branches/working", FakeResponse(200, {"commit": {"id": "sha-one"}})),
        ]
    )
    with pytest.raises(GitLabConflict):
        client.push_with_rebase("working", "msg", {"f.yml": "x"})


def test_create_merge_request_working_to_main():
    """Merge requests target the production branch."""
    client = make_client(
        [("POST", "/merge_requests", FakeResponse(201, {"iid": 42, "web_url": "http://gitlab.test/mr/42"}))]
    )
    mr = client.create_merge_request("working", "main", "Deploy leaf to sw-01, sw-02", "devices: sw-01 sw-02")
    assert mr["iid"] == 42
    body = client.session.calls[0]["json"]
    assert body["source_branch"] == "working"
    assert body["target_branch"] == "main"
    assert body["title"].startswith("Deploy")


def test_merge_merge_request_conflict_422():
    """A conflicting merge surfaces the GitLab failure."""
    client = make_client([("POST", "/merge_requests/42/merge", FakeResponse(422, {"message": "conflict"}))])
    with pytest.raises(GitLabConflict):
        client.merge_merge_request(42)


def test_merge_merge_request_success():
    """A clean merge returns the merge request."""
    client = make_client([("POST", "/merge_requests/42/merge", FakeResponse(200, {"state": "merged"}))])
    assert client.merge_merge_request(42)["state"] == "merged"


def test_pipeline_and_job_lookup():
    """Pipeline and job lookups map the API rows."""
    client = make_client(
        [
            ("GET", "/pipelines/99", FakeResponse(200, {"id": 99, "status": "failed", "ref": "main"})),
            ("GET", "/pipelines/99/jobs", FakeResponse(200, [{"id": 1, "name": "deploy", "status": "failed"}])),
        ]
    )
    pipeline = client.get_pipeline(99)
    jobs = client.get_pipeline_jobs(99)
    assert pipeline["status"] == "failed"
    assert jobs[0]["name"] == "deploy"


def test_list_pipelines_by_ref():
    """List pipelines filtered by ref."""
    client = make_client([("GET", "/pipelines", FakeResponse(200, [{"id": 5, "sha": "abc", "status": "success"}]))])
    pipelines = client.list_pipelines("main", per_page=5)
    assert pipelines[0]["id"] == 5
    assert client.session.calls[0]["params"] == {"per_page": 5, "ref": "main"}


def test_auth_failure_maps_to_unavailable():
    """401/403 responses map to GitLabUnavailable."""
    from app.services.gitlab import GitLabUnavailable

    client = make_client([("GET", "/repository/branches/working", FakeResponse(401))])
    with pytest.raises(GitLabUnavailable):
        client.latest_branch_sha("working")


def test_private_token_header_present():
    """Requests carry the PRIVATE-TOKEN header."""
    client = GitLabClient("http://gitlab.test", "secret", 7)
    assert client.session.headers["PRIVATE-TOKEN"] == "secret"
