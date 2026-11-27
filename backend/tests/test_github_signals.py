from __future__ import annotations

import contextlib
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.auth.app_auth import create_dev_session
from app.auth.vault import OAuthTokenVault
from app.connectors.github import client as github_client
from app.connectors.github.client import GitHubConnector
from app.connectors.github.signals import normalize_terminal
from app.connectors.registry import ConnectorRegistry
from app.connectors.sync import ConnectorRateLimiter, SyncEngine
from app.core.database import connect, new_id, row, rows, utcnow

FIXTURE = json.loads(Path(__file__).parent.joinpath("fixtures/github_signals.json").read_text())
REPO = FIXTURE["repository"]


def _no_network(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("network must not be used in pure normalization")

    monkeypatch.setattr("httpx.request", _boom)
    monkeypatch.setattr("httpx.post", _boom)
    with contextlib.suppress(Exception):
        monkeypatch.setattr("app.retrieval.pipeline.generate_grounded_json", _boom, raising=False)


def test_normalize_workflow_and_check_failure_success(monkeypatch):
    _no_network(monkeypatch)
    failed = next(
        w
        for w in FIXTURE["workflows"]
        if w["id"] == 101 and w["run_attempt"] == 1 and w["updated_at"] == "2026-10-08T10:00:00Z"
    )
    (rec,) = normalize_terminal("workflow", failed, repository=REPO, target="default")
    assert rec.resource_type == "signal"
    assert rec.metadata["state"] == "failure"
    assert rec.metadata["severity"] == "error"
    assert rec.metadata["kind"] == "ci_failure"
    assert rec.metadata["lane"] == {"producer": "workflow", "target": "default", "workflow_id": "7"}
    assert rec.source_url == "https://github.com/acme/api/actions/runs/101"
    assert rec.metadata["repository"] == "acme/api"
    assert rec.metadata["repo_id"] == 42
    assert rec.version == rec.metadata["observation_key"]

    green = next(w for w in FIXTURE["workflows"] if w["id"] == 102)
    (grec,) = normalize_terminal("workflow", green, repository=REPO, target="default")
    assert grec.metadata["state"] == "success"
    assert grec.metadata["severity"] == "info"

    check_fail = FIXTURE["checks"][0]
    (crec,) = normalize_terminal("check", check_fail, repository=REPO, target="default")
    assert crec.metadata["state"] == "failure"
    assert crec.metadata["lane"]["producer"] == "check"
    assert crec.source_url == "https://github.com/acme/api/runs/9001"

    check_ok = FIXTURE["checks"][1]
    (cok,) = normalize_terminal("check", check_ok, repository=REPO, target="default")
    assert cok.metadata["state"] == "success"


def test_workflow_check_and_pr_lanes_are_distinct(monkeypatch):
    _no_network(monkeypatch)
    from app.orgops.signals import signal_fingerprint

    wf = next(w for w in FIXTURE["workflows"] if w["id"] == 101 and w["run_attempt"] == 1)
    (wrec,) = normalize_terminal("workflow", wf, repository=REPO, target="default")
    ck = FIXTURE["checks"][0]
    (crec,) = normalize_terminal("check", ck, repository=REPO, target="default")
    assert wrec.metadata["lane"] != crec.metadata["lane"]
    fp_w = signal_fingerprint(
        workspace_id="w",
        project_id="p",
        source="github",
        kind="ci_failure",
        subject="acme/api",
        lane=wrec.metadata["lane"],
        source_ids=["a"],
    )
    fp_c = signal_fingerprint(
        workspace_id="w",
        project_id="p",
        source="github",
        kind="ci_failure",
        subject="acme/api",
        lane=crec.metadata["lane"],
        source_ids=["a"],
    )
    assert fp_w != fp_c
    pr_payload = {
        "pull_request": {
            "number": 11,
            "state": "open",
            "draft": False,
            "created_at": "2026-09-20T10:00:00Z",
            "updated_at": "2026-09-25T10:00:00Z",
        },
        "reviews": [],
    }
    (prec,) = normalize_terminal(
        "pr_review", pr_payload, repository=REPO, target="pr:11", now="2026-10-08T10:00:00Z"
    )
    assert prec.metadata["lane"] != wrec.metadata["lane"]
    assert prec.metadata["lane"] != crec.metadata["lane"]


def test_provider_order_is_execution_order(graph, monkeypatch):
    _no_network(monkeypatch)
    from app.core.database import connect, new_id, utcnow

    wid = new_id("wsp")
    pid = new_id("prj")
    now = utcnow()
    with connect() as c:
        c.execute(
            "INSERT INTO workspaces VALUES (?,?,?,?,?)", (wid, "ws", f"s-{wid[-4:]}", now, now)
        )
        c.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)", (pid, "p", "acme/api", "ready", now, now)
        )
        c.execute("INSERT INTO workspace_projects VALUES (?,?)", (wid, pid))
    first = next(w for w in FIXTURE["workflows"] if w["id"] == 101 and w["run_attempt"] == 1)
    rerun = next(w for w in FIXTURE["workflows"] if w["id"] == 101 and w["run_attempt"] == 2)
    (r1,) = normalize_terminal("workflow", first, repository=REPO, target="default")
    (r2,) = normalize_terminal("workflow", rerun, repository=REPO, target="default")
    ctx = {"workspace_id": wid, "project_id": pid, "provider": "github", "resource_id": "acme/api"}
    from app.audit import AuditService
    from app.connectors.application import MemoryWorksSyncApplier
    from app.hcag_adapter import HCAGAdapter
    from app.ingestion.service import IngestionService

    applier = MemoryWorksSyncApplier(
        IngestionService(graph, HCAGAdapter(graph), AuditService()), graph
    )
    a1 = applier(r1, ctx)
    a2 = applier(r2, ctx)
    assert a1["issue_id"] == a2["issue_id"]
    from app.core.database import row

    issue = row("SELECT * FROM live_issues WHERE id=?", (a1["issue_id"],))
    assert issue["occurrences"] == 2


def test_nonterminal_and_neutral_are_not_success(monkeypatch):
    _no_network(monkeypatch)
    pending = next(w for w in FIXTURE["workflows"] if w["id"] == 104)
    assert normalize_terminal("workflow", pending, repository=REPO, target="default") == ()
    neutral = next(w for w in FIXTURE["workflows"] if w["id"] == 103)
    assert normalize_terminal("workflow", neutral, repository=REPO, target="default") == ()
    for conclusion in ("neutral", "skipped", "stale"):
        payload = {
            "id": 200,
            "workflow_id": 7,
            "run_attempt": 1,
            "head_sha": "sha-default-1",
            "created_at": "2026-10-08T09:55:00Z",
            "updated_at": "2026-10-08T10:00:00Z",
            "status": "completed",
            "conclusion": conclusion,
        }
        assert normalize_terminal("workflow", payload, repository=REPO, target="default") == ()
    check_pending = {
        "id": 9003,
        "name": "build",
        "app": {"id": 11},
        "head_sha": "sha-default-1",
        "status": "in_progress",
        "conclusion": None,
        "started_at": "2026-10-08T09:55:00Z",
        "completed_at": "",
    }
    assert normalize_terminal("check", check_pending, repository=REPO, target="default") == ()


def test_pr_stalled_threshold_draft_and_review_states(monkeypatch):
    _no_network(monkeypatch)
    now = "2026-10-08T10:00:00Z"
    stalled = {
        "pull_request": {
            "number": 11,
            "state": "open",
            "draft": False,
            "created_at": "2026-09-20T10:00:00Z",
            "updated_at": "2026-09-25T10:00:00Z",
        },
        "reviews": [],
    }
    (rec,) = normalize_terminal("pr_review", stalled, repository=REPO, target="pr:11", now=now)
    assert rec.metadata["state"] == "failure"
    assert rec.metadata["severity"] == "warning"
    assert rec.metadata["kind"] == "pr_stalled"

    reviewed = {
        "pull_request": stalled["pull_request"],
        "reviews": FIXTURE["pr11"]["review_variants"]["commented"],
    }
    (r2,) = normalize_terminal("pr_review", reviewed, repository=REPO, target="pr:11", now=now)
    assert r2.metadata["state"] == "success"

    draft = {
        "pull_request": {**stalled["pull_request"], "draft": True},
        "reviews": [],
    }
    (r3,) = normalize_terminal("pr_review", draft, repository=REPO, target="pr:11", now=now)
    assert r3.metadata["state"] == "success"

    closed = {
        "pull_request": {**stalled["pull_request"], "state": "closed"},
        "reviews": [],
    }
    (r4,) = normalize_terminal("pr_review", closed, repository=REPO, target="pr:11", now=now)
    assert r4.metadata["state"] == "success"

    young = {
        "pull_request": {
            "number": 11,
            "state": "open",
            "draft": False,
            "created_at": "2026-10-06T10:00:00Z",
            "updated_at": "2026-10-06T10:00:00Z",
        },
        "reviews": [],
    }
    (r5,) = normalize_terminal("pr_review", young, repository=REPO, target="pr:11", now=now)
    assert r5.metadata["state"] == "success"

    # 7-day boundary: exactly 7 days old stalls.
    boundary = {
        "pull_request": {
            "number": 11,
            "state": "open",
            "draft": False,
            "created_at": "2026-10-01T10:00:00Z",
            "updated_at": "2026-10-01T10:00:00Z",
        },
        "reviews": [],
    }
    (rb,) = normalize_terminal(
        "pr_review", boundary, repository=REPO, target="pr:11", now="2026-10-08T10:00:00Z"
    )
    assert rb.metadata["state"] == "failure"

    pending = {
        "pull_request": stalled["pull_request"],
        "reviews": FIXTURE["pr11"]["review_variants"]["pending"],
    }
    (rp,) = normalize_terminal("pr_review", pending, repository=REPO, target="pr:11", now=now)
    assert rp.metadata["state"] == "failure"


def test_pr_observation_identity_is_daily_and_state_specific(monkeypatch):
    _no_network(monkeypatch)
    payload = {
        "pull_request": {
            "number": 11,
            "state": "open",
            "draft": False,
            "created_at": "2026-09-20T10:00:00Z",
            "updated_at": "2026-09-25T10:00:00Z",
        },
        "reviews": [],
    }
    (a,) = normalize_terminal(
        "pr_review", payload, repository=REPO, target="pr:11", now="2026-10-08T10:00:00Z"
    )
    (b,) = normalize_terminal(
        "pr_review", payload, repository=REPO, target="pr:11", now="2026-10-08T15:00:00Z"
    )
    assert a.metadata["observation_key"] == b.metadata["observation_key"]
    (c,) = normalize_terminal(
        "pr_review", payload, repository=REPO, target="pr:11", now="2026-10-09T10:00:00Z"
    )
    assert c.metadata["observation_key"] != a.metadata["observation_key"]
    reviewed = {
        "pull_request": payload["pull_request"],
        "reviews": FIXTURE["pr11"]["review_variants"]["commented"],
    }
    (d,) = normalize_terminal(
        "pr_review", reviewed, repository=REPO, target="pr:11", now="2026-10-08T10:00:00Z"
    )
    assert d.metadata["observation_key"] != a.metadata["observation_key"]


def test_deployment_failure_success_and_pending(monkeypatch):
    _no_network(monkeypatch)
    dep = FIXTURE["deployments"][0]
    fail_status = FIXTURE["deployment_statuses"][0]
    (frec,) = normalize_terminal(
        "deployment",
        {"deployment": dep, "deployment_status": fail_status},
        repository=REPO,
        target="default",
    )
    assert frec.metadata["state"] == "failure"
    assert frec.metadata["kind"] == "deploy"
    assert frec.source_url == "https://github.com/acme/api/deployments"

    ok_status = FIXTURE["deployment_statuses"][1]
    (srec,) = normalize_terminal(
        "deployment",
        {"deployment": dep, "deployment_status": ok_status},
        repository=REPO,
        target="default",
    )
    assert srec.metadata["state"] == "success"

    pending = FIXTURE["deployment_statuses"][2]
    assert (
        normalize_terminal(
            "deployment",
            {"deployment": dep, "deployment_status": pending},
            repository=REPO,
            target="default",
        )
        == ()
    )
    inactive = {"id": 6004, "state": "inactive", "created_at": "2026-10-08T09:30:00Z"}
    assert (
        normalize_terminal(
            "deployment",
            {"deployment": dep, "deployment_status": inactive},
            repository=REPO,
            target="default",
        )
        == ()
    )


def test_signal_metadata_has_no_secrets_or_untrusted_urls(monkeypatch):
    _no_network(monkeypatch)
    probes = FIXTURE["redaction_probes"]
    wf = next(w for w in FIXTURE["workflows"] if w["id"] == 101 and w["run_attempt"] == 1)
    wf = {**wf, "head_sha": "sha-default-1", "html_url": probes["url_with_query"]}
    (rec,) = normalize_terminal("workflow", wf, repository=REPO, target="default")
    blob = json.dumps(rec.metadata)
    assert "foo=bar" not in blob
    assert "#fragment" not in blob
    assert probes["fake_github_token"] not in blob
    assert probes["fake_openai_key"] not in blob
    # Details are allowlisted and sanitized.
    assert set(rec.metadata["details"].keys()) <= {
        "producer",
        "target",
        "workflow_id",
        "app_id",
        "check_name",
        "environment",
        "task",
        "head_sha",
        "conclusion",
        "pr_number",
        "age_days",
        "label",
    }
    assert rec.source_url == "https://github.com/acme/api/actions/runs/101"
    assert "?" not in rec.source_url
    # Invalid repo fails before helper emits anything.
    with pytest.raises(ValueError):
        normalize_terminal("workflow", wf, repository={"id": "bad"}, target="default")
    # Missing ids/timestamps emit no record.
    assert normalize_terminal("workflow", {"id": 1}, repository=REPO, target="default") == ()


# ---------------------------------------------------------------------------
# Step 6: resumable polling with real request pacing/backoff.
# ---------------------------------------------------------------------------


class _FakeHTTP:
    """Minimal httpx response stand-in with status, headers, and JSON."""

    def __init__(self, payload, status=200, headers=None):
        self._payload = copy.deepcopy(payload)
        self.status_code = status
        self.headers = {str(k).casefold(): str(v) for k, v in dict(headers or {}).items()}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"fake github {self.status_code}", request=None, response=self
            )

    def json(self):
        return copy.deepcopy(self._payload)


def _normalize_path(url: str) -> str:
    path = str(url)
    if "://" in path:
        path = path.split("://", 1)[1]
        path = "/" + path.split("/", 1)[1] if "/" in path else "/"
    base, _, query = path.partition("?")
    if not query:
        return base
    params = sorted(partition for partition in query.split("&") if partition)
    return base + "?" + "&".join(params)


class FakeGitHub:
    """Canned GitHub API routes; anything unmatched fails the test."""

    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.requests: list[tuple[str, str]] = []

    def add(self, method: str, path: str, payload, status: int = 200, headers=None):
        self.routes[(method.upper(), _normalize_path(path))] = (payload, status, headers or {})
        return self

    def __call__(self, method, url, headers=None, **kwargs):
        path = _normalize_path(url)
        self.requests.append((str(method).upper(), path))
        key = (str(method).upper(), path)
        if key not in self.routes:
            raise AssertionError(f"unmatched GitHub request {method} {path}")
        payload, status, response_headers = self.routes[key]
        return _FakeHTTP(payload, status, response_headers)

    def request_count(self) -> int:
        return len(self.requests)


@pytest.fixture
def fake_github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr("app.connectors.github.client.httpx.request", fake)
    # Isolate process-local pacing; throttle behavior has dedicated tests.
    monkeypatch.setattr(
        github_client._SIGNAL_LIMITER, "try_acquire", lambda key, requests, window: 0.0
    )
    return fake


@pytest.fixture
def frozen_clock(monkeypatch):
    """Deterministic collected_at/expiry; advance manually between snapshots."""
    state = {"now": datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)}

    def _now():
        return state["now"].isoformat(timespec="microseconds")

    monkeypatch.setattr("app.orgops.signals.utcnow", _now)
    return state


def _signal_workspace(email: str):
    session = create_dev_session(email, "Signal Owner")
    workspace_id = session["user"]["active_workspace_id"]
    user_id = session["user"]["id"]
    project_id = new_id("prj")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (project_id, "API project", "acme/api", "ready", now, now),
        )
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", (workspace_id, project_id))
    vault = OAuthTokenVault(workspace_id, user_id)
    vault.save("github", "42", "octo", "delegated-token")
    return workspace_id, user_id, project_id


def _signal_engine(graph):
    from app.audit import AuditService
    from app.connectors.application import MemoryWorksSyncApplier
    from app.hcag_adapter import HCAGAdapter
    from app.ingestion.service import IngestionService

    registry = ConnectorRegistry()
    registry.register(lambda vault: GitHubConnector(vault), source="test", persist=False)
    applier = MemoryWorksSyncApplier(
        IngestionService(graph, HCAGAdapter(graph), AuditService()), graph
    )
    return SyncEngine(applier, registry=registry)


def _make_due():
    with connect() as conn:
        conn.execute(
            "UPDATE connector_sync_jobs SET next_attempt_at=? WHERE status IN ('queued','retrying')",
            (utcnow(),),
        )


def _drain(engine, workspace_id, cap=400):
    batches = 0
    for _ in range(cap):
        _make_due()
        if engine.run_once(limit=10) == 0:
            break
        batches += 1
    pending = [
        job
        for job in engine.list(workspace_id)
        if job["status"] in ("queued", "retrying", "running")
    ]
    assert not pending, f"snapshot did not converge: {pending}"
    return batches


def _serve_repo(fake: FakeGitHub, default_sha: str = "sha-default-1"):
    fake.add("GET", "/repos/acme/api", dict(REPO))
    fake.add(
        "GET",
        "/repos/acme/api/branches/main",
        {"name": "main", "commit": {"sha": default_sha}},
    )
    return fake


def _serve_empty_inventory(fake: FakeGitHub, sha: str = "sha-default-1"):
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [])
    fake.add(
        "GET",
        f"/repos/acme/api/commits/{sha}/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 0, "check_runs": []},
    )
    fake.add(
        "GET",
        f"/repos/acme/api/actions/runs?head_sha={sha}&per_page=100&page=1",
        {"total_count": 0, "workflow_runs": []},
    )
    fake.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])
    return fake


def _workflow_run(run_id, attempt, conclusion, sha, updated, status="completed"):
    return {
        "id": run_id,
        "workflow_id": 7,
        "run_attempt": attempt,
        "head_sha": sha,
        "head_branch": "main",
        "created_at": "2026-10-08T09:55:00Z",
        "updated_at": updated,
        "status": status,
        "conclusion": conclusion,
    }


def test_try_acquire_reserves_without_sleeping(monkeypatch):
    limiter = ConnectorRateLimiter()
    assert limiter.try_acquire("k", 2, 60) == 0.0
    assert limiter.try_acquire("k", 2, 60) == 0.0
    delay = limiter.try_acquire("k", 2, 60)
    assert delay > 0
    # Blocking acquire compatibility is intact: it waits out the window.
    now = {"value": 1000.0}
    monkeypatch.setattr("app.connectors.sync.time.monotonic", lambda: now["value"])
    limited = ConnectorRateLimiter()
    assert limited.try_acquire("burst", 1, 10) == 0.0
    assert limited.try_acquire("burst", 1, 10) == pytest.approx(10.0)
    now["value"] += 11.0
    assert limited.try_acquire("burst", 1, 10) == 0.0


def test_recorded_red_workflow_then_green_resolves_issue(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("red-green@example.com")
    engine = _signal_engine(graph)
    fake = fake_github
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    _serve_repo(fake, "sha-default-1")
    _serve_empty_inventory(fake, "sha-default-1")
    fake.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-1&per_page=100&page=1",
        {
            "total_count": 1,
            "workflow_runs": [
                _workflow_run(101, 1, "failure", "sha-default-1", "2026-10-08T10:00:00Z")
            ],
        },
    )

    red = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="snapshot-red-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (red["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "open"
    assert issues[0]["occurrences"] == 1
    assert issues[0]["kind"] == "ci_failure"
    assert issues[0]["subject"] == "acme/api"
    # No network beyond the recorded transport and no model calls happened.
    assert fake.request_count() > 0

    frozen_clock["now"] += timedelta(hours=1)
    fake.routes.clear()
    fake.requests.clear()
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    _serve_repo(fake, "sha-default-2")
    _serve_empty_inventory(fake, "sha-default-2")
    fake.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-2&per_page=100&page=1",
        {
            "total_count": 1,
            "workflow_runs": [
                _workflow_run(102, 1, "success", "sha-default-2", "2026-10-08T11:00:00Z")
            ],
        },
    )

    green = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api", "last_commit_sha": "sha-default-2"},
        idempotency_key="snapshot-green-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (green["id"],))["status"] == (
        "succeeded"
    )
    resolved = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(resolved) == 1
    assert resolved[0]["status"] == "resolved"
    assert resolved[0]["occurrences"] == 1
    assert resolved[0]["id"] == issues[0]["id"]


def test_unchanged_sha_rerun_is_polled(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("rerun@example.com")
    engine = _signal_engine(graph)
    fake = fake_github
    fake.add(
        "GET",
        "/repos/acme/api/commits?per_page=100",
        [{"sha": "sha-default-1", "commit": {"message": "same", "author": {}}}],
    )
    _serve_repo(fake, "sha-default-1")
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [])
    fake.add(
        "GET",
        "/repos/acme/api/commits/sha-default-1/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 0, "check_runs": []},
    )
    fake.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-1&per_page=100&page=1",
        {
            "total_count": 1,
            "workflow_runs": [
                _workflow_run(101, 1, "failure", "sha-default-1", "2026-10-08T10:00:00Z")
            ],
        },
    )
    fake.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])

    job = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api", "last_commit_sha": "sha-default-1"},
        idempotency_key="rerun-unchanged-sha",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (job["id"],))["status"] == (
        "succeeded"
    )
    # No commit records were new, but operational polling still ran and opened
    # the CI issue.
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "open"
    assert fake.request_count() > 2


def _open_pr(number: int, young: bool = True) -> dict:
    created = "2026-10-07T10:00:00Z" if young else "2026-09-20T10:00:00Z"
    return {
        "number": number,
        "state": "open",
        "draft": False,
        "created_at": created,
        "updated_at": created,
        "head": {"sha": f"sha-pr-{number}", "repo": {"id": 42, "full_name": "acme/api"}},
        "base": {"repo": {"id": 42, "full_name": "acme/api"}},
        "merge_commit_sha": None,
    }


def test_resume_after_101_checks_and_101_prs(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("resume@example.com")
    fake = fake_github
    _serve_repo(fake, "sha-default-1")

    prs = [_open_pr(n) for n in range(1, 102)]
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", prs[:100])
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=2", prs[100:])
    for pr in prs:
        number = pr["number"]
        fake.add("GET", f"/repos/acme/api/pulls/{number}", dict(pr))
        fake.add(
            "GET",
            f"/repos/acme/api/pulls/{number}/reviews?per_page=100&page=1",
            [],
        )
        fake.add(
            "GET",
            f"/repos/acme/api/commits/sha-pr-{number}/check-runs?filter=all&per_page=100&page=1",
            {"total_count": 0, "check_runs": []},
        )
        fake.add(
            "GET",
            f"/repos/acme/api/actions/runs?head_sha=sha-pr-{number}&per_page=100&page=1",
            {"total_count": 0, "workflow_runs": []},
        )

    checks_page_1 = [
        {
            "id": 9000 + i,
            "name": "build",
            "app": {"id": 11},
            "head_sha": "sha-default-1",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-10-08T09:55:00Z",
            "completed_at": "2026-10-08T10:00:00Z",
        }
        for i in range(1, 101)
    ]
    checks_page_2 = [
        {
            "id": 9101,
            "name": "build",
            "app": {"id": 11},
            "head_sha": "sha-default-1",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-10-08T09:55:00Z",
            "completed_at": "2026-10-08T10:01:00Z",
        }
    ]
    fake.add(
        "GET",
        "/repos/acme/api/commits/sha-default-1/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 101, "check_runs": checks_page_1},
    )
    fake.add(
        "GET",
        "/repos/acme/api/commits/sha-default-1/check-runs?filter=all&per_page=100&page=2",
        {"total_count": 101, "check_runs": checks_page_2},
    )
    fake.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-1&per_page=100&page=1",
        {"total_count": 0, "workflow_runs": []},
    )
    fake.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])

    vault = OAuthTokenVault(workspace_id, user_id)
    connector = GitHubConnector(vault)
    cursor: dict = {"repository": "acme/api", "signals_only": True}
    collected: list = []
    seen_before_interrupt = 0
    batches = 0
    interrupted_cursor: dict | None = None
    while True:
        batch = connector.poll(cursor, now="2026-10-08T12:00:00.000000+00:00")
        batches += 1
        collected.extend(batch.records)
        cursor = batch.next_cursor
        if interrupted_cursor is None and batches == 8:
            interrupted_cursor = copy.deepcopy(cursor)
            seen_before_interrupt = len(collected)
        if not batch.has_more:
            break
        assert batches < 600
    assert batches > 20
    assert interrupted_cursor is not None
    assert seen_before_interrupt > 0

    # Replaying the interrupted page yields the same page again (idempotent).
    replay = connector.poll(interrupted_cursor, now="2026-10-08T12:00:00.000000+00:00")
    assert replay.has_more
    replay_ids = [record.id for record in replay.records]

    # Apply everything, including the replayed page, through the real applier.
    from app.audit import AuditService
    from app.connectors.application import MemoryWorksSyncApplier
    from app.hcag_adapter import HCAGAdapter
    from app.ingestion.service import IngestionService

    applier = MemoryWorksSyncApplier(
        IngestionService(graph, HCAGAdapter(graph), AuditService()), graph
    )
    context = {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "provider": "github",
        "resource_id": "acme/api",
    }
    for record in list(collected) + list(replay.records):
        result = applier(record, context)
        assert result["status"] == "signal_recorded"
    check_issues = rows(
        "SELECT * FROM live_issues WHERE workspace_id=? AND kind='ci_failure'",
        (workspace_id,),
    )
    assert len(check_issues) == 1
    # All 101 same-lane check failures cluster; the replayed page did not
    # double-count.
    assert check_issues[0]["occurrences"] == 101
    assert replay_ids, "expected the replayed page to carry records"


def test_fork_and_merge_sha_are_scoped_to_base_pr(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("fork@example.com")
    _serve_repo(fake_github, "sha-default-1")
    pr = {
        "number": 11,
        "state": "open",
        "draft": False,
        "created_at": "2026-10-07T10:00:00Z",
        "updated_at": "2026-10-07T10:00:00Z",
        "head": {"sha": "sha-fork-head", "repo": {"id": 99, "full_name": "fork/api"}},
        "base": {"repo": {"id": 42, "full_name": "acme/api"}},
        "merge_commit_sha": "sha-merge-1",
    }
    fake_github.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [pr])
    fake_github.add("GET", "/repos/acme/api/pulls/11", dict(pr))
    fake_github.add("GET", "/repos/acme/api/pulls/11/reviews?per_page=100&page=1", [])
    fork_check = {
        "id": 9201,
        "name": "build",
        "app": {"id": 11},
        "head_sha": "sha-fork-head",
        "status": "completed",
        "conclusion": "failure",
        "started_at": "2026-10-08T09:55:00Z",
        "completed_at": "2026-10-08T10:00:00Z",
    }
    merge_check = dict(fork_check, id=9202, head_sha="sha-merge-1")
    fake_github.add(
        "GET",
        "/repos/fork/api/commits/sha-fork-head/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 1, "check_runs": [fork_check]},
    )
    fake_github.add(
        "GET",
        "/repos/acme/api/commits/sha-merge-1/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 1, "check_runs": [merge_check]},
    )
    fake_github.add(
        "GET",
        "/repos/fork/api/actions/runs?head_sha=sha-fork-head&per_page=100&page=1",
        {"total_count": 0, "workflow_runs": []},
    )
    fake_github.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-merge-1&per_page=100&page=1",
        {"total_count": 0, "workflow_runs": []},
    )
    fake_github.add(
        "GET",
        "/repos/acme/api/commits/sha-default-1/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 0, "check_runs": []},
    )
    fake_github.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-1&per_page=100&page=1",
        {"total_count": 0, "workflow_runs": []},
    )
    fake_github.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])

    vault = OAuthTokenVault(workspace_id, user_id)
    connector = GitHubConnector(vault)
    cursor: dict = {"repository": "acme/api", "signals_only": True}
    collected: list = []
    while True:
        batch = connector.poll(cursor, now="2026-10-08T12:00:00.000000+00:00")
        collected.extend(batch.records)
        cursor = batch.next_cursor
        if not batch.has_more:
            break
    paths = [path for _, path in fake_github.requests]
    fork_paths = [path for path in paths if path.startswith("/repos/fork/api/")]
    # The fork head SHA is read against its verified head repo; the merge
    # SHA stays on the base repo.
    assert fork_paths == [
        "/repos/fork/api/commits/sha-fork-head/check-runs?filter=all&page=1&per_page=100",
        "/repos/fork/api/actions/runs?head_sha=sha-fork-head&page=1&per_page=100",
    ]
    assert "/repos/acme/api/commits/sha-merge-1/check-runs?filter=all&page=1&per_page=100" in paths

    from app.audit import AuditService
    from app.connectors.application import MemoryWorksSyncApplier
    from app.hcag_adapter import HCAGAdapter
    from app.ingestion.service import IngestionService

    applier = MemoryWorksSyncApplier(
        IngestionService(graph, HCAGAdapter(graph), AuditService()), graph
    )
    context = {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "provider": "github",
        "resource_id": "acme/api",
    }
    for record in collected:
        if record.metadata.get("kind") == "ci_failure":
            assert record.metadata["pr_number"] == 11
            assert applier(record, context)["status"] == "signal_recorded"
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["occurrences"] == 2
    assert issues[0]["subject"] == "acme/api"


def test_pr_close_and_review_resolve(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("pr-stall@example.com")
    engine = _signal_engine(graph)
    fake = fake_github

    stalled = {
        "number": 11,
        "state": "open",
        "draft": False,
        "created_at": "2026-09-20T10:00:00Z",
        "updated_at": "2026-09-25T10:00:00Z",
        "head": {"sha": "sha-pr-head", "repo": {"id": 42, "full_name": "acme/api"}},
        "base": {"repo": {"id": 42, "full_name": "acme/api"}},
        "merge_commit_sha": "sha-merge-1",
    }

    def serve_cycle(pr_state: dict, now_iso: str):
        fake.routes.clear()
        fake.requests.clear()
        fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
        _serve_repo(fake, "sha-default-1")
        fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [pr_state])
        fake.add("GET", "/repos/acme/api/pulls/11", dict(pr_state))
        fake.add(
            "GET",
            "/repos/acme/api/pulls/11/reviews?per_page=100&page=1",
            pr_state.get("_reviews", []),
        )
        for sha in ("sha-default-1", "sha-pr-head", "sha-merge-1"):
            fake.add(
                "GET",
                f"/repos/acme/api/commits/{sha}/check-runs?filter=all&per_page=100&page=1",
                {"total_count": 0, "check_runs": []},
            )
            fake.add(
                "GET",
                f"/repos/acme/api/actions/runs?head_sha={sha}&per_page=100&page=1",
                {"total_count": 0, "workflow_runs": []},
            )
        fake.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])

    serve_cycle(dict(stalled), "2026-10-08T12:00:00.000000+00:00")
    first = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="pr-stall-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (first["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "open"
    assert issues[0]["kind"] == "pr_stalled"

    frozen_clock["now"] += timedelta(hours=2)
    reviewed = dict(
        stalled, _reviews=[{"id": 1, "state": "COMMENTED", "submitted_at": "2026-10-08T13:00:00Z"}]
    )
    serve_cycle(reviewed, "2026-10-08T14:00:00.000000+00:00")
    second = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api", "last_commit_sha": "sha-default-1"},
        idempotency_key="pr-reviewed-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (second["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "resolved"

    frozen_clock["now"] += timedelta(hours=2)
    closed = dict(stalled, state="closed")
    serve_cycle(closed, "2026-10-08T16:00:00.000000+00:00")
    # A closed PR is absent from the open-PR inventory; the verified-PR hint
    # reconciles it so the stalled issue resolves.
    fake.routes[
        ("GET", _normalize_path("/repos/acme/api/pulls?state=open&per_page=100&page=1"))
    ] = (
        [],
        200,
        {},
    )
    third = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={
            "repository": "acme/api",
            "last_commit_sha": "sha-default-1",
            "signals_only": True,
            "signals": {"version": 1, "hints": [{"kind": "pr", "number": 11}]},
        },
        idempotency_key="pr-closed-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (third["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "resolved"
    assert issues[0]["occurrences"] == 1


def _deployment_cycle(fake: FakeGitHub, statuses: list[dict]):
    fake.routes.clear()
    fake.requests.clear()
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    _serve_repo(fake, "sha-default-1")
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [])
    fake.add(
        "GET",
        "/repos/acme/api/commits/sha-default-1/check-runs?filter=all&per_page=100&page=1",
        {"total_count": 0, "check_runs": []},
    )
    fake.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-1&per_page=100&page=1",
        {"total_count": 0, "workflow_runs": []},
    )
    fake.add(
        "GET",
        "/repos/acme/api/deployments?per_page=100&page=1",
        [
            {
                "id": 5001,
                "environment": "production",
                "task": "deploy",
                "sha": "sha-default-1",
                "created_at": "2026-10-08T09:00:00Z",
            }
        ],
    )
    fake.add(
        "GET",
        "/repos/acme/api/deployments/5001/statuses?per_page=100&page=1",
        list(statuses),
    )


def test_deployment_polling_uses_latest_status(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("deploy@example.com")
    engine = _signal_engine(graph)
    fake = fake_github

    _deployment_cycle(
        fake,
        [
            {"id": 6001, "state": "failure", "created_at": "2026-10-08T09:10:00Z"},
            {"id": 6000, "state": "success", "created_at": "2026-10-08T09:05:00Z"},
        ],
    )
    first = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="deploy-fail-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (first["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["kind"] == "deploy"
    assert issues[0]["status"] == "open"
    signals_after_fail = row("SELECT COUNT(*) AS n FROM live_signals")["n"]

    # A newer nonterminal status supersedes the older terminal result: no
    # observation is emitted and the issue stays open without new signals.
    frozen_clock["now"] += timedelta(hours=1)
    _deployment_cycle(
        fake,
        [
            {"id": 6003, "state": "in_progress", "created_at": "2026-10-08T09:25:00Z"},
            {"id": 6001, "state": "failure", "created_at": "2026-10-08T09:10:00Z"},
        ],
    )
    second = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api", "last_commit_sha": "sha-default-1"},
        idempotency_key="deploy-pending-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (second["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "open"
    assert row("SELECT COUNT(*) AS n FROM live_signals")["n"] == signals_after_fail

    # The newest terminal success resolves the deployment issue.
    frozen_clock["now"] += timedelta(hours=1)
    _deployment_cycle(
        fake,
        [
            {"id": 6002, "state": "success", "created_at": "2026-10-08T09:20:00Z"},
            {"id": 6001, "state": "failure", "created_at": "2026-10-08T09:10:00Z"},
        ],
    )
    third = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api", "last_commit_sha": "sha-default-1"},
        idempotency_key="deploy-green-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (third["id"],))["status"] == (
        "succeeded"
    )
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "resolved"
    assert project_id


def test_throttle_keeps_cursor_and_attempts(graph, fake_github):
    workspace_id, user_id, project_id = _signal_workspace("throttle@example.com")
    engine = _signal_engine(graph)
    fake = fake_github
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    _serve_repo(fake, "sha-default-1")
    fake.add(
        "GET",
        "/repos/acme/api/branches/main",
        {"message": "API rate limit exceeded"},
        status=429,
        headers={"Retry-After": "5"},
    )

    job = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="throttle-1",
    )
    _make_due()
    assert engine.run_once(limit=10) == 1
    # The commit batch finished; capture the polling cursor, then run the
    # repo-metadata batch.
    _make_due()
    assert engine.run_once(limit=10) == 1
    middle = engine.get(job["id"])
    assert middle["status"] == "queued"
    assert middle["cursor"]["signals"]["phase"] == "branch"
    # The branch read is throttled: the batch keeps its cursor exactly,
    # stays queued, and consumes no sync retry attempt.
    _make_due()
    assert engine.run_once(limit=10) == 1
    stored = engine.get(job["id"])
    assert stored["status"] == "queued"
    assert stored["cursor"] == middle["cursor"]
    assert stored["cursor"]["signals"] == middle["cursor"]["signals"]
    assert row("SELECT attempts FROM connector_sync_jobs WHERE id=?", (job["id"],))["attempts"] == 0

    # The durable backoff honors Retry-After: not due yet, then retried.
    assert engine.run_once(limit=10) == 0
    _make_due()
    assert engine.run_once(limit=10) == 1
    assert engine.get(job["id"])["status"] == "queued"


def test_provider_rate_reset_and_local_limiter_throttle(graph, fake_github, monkeypatch):
    workspace_id, user_id, _ = _signal_workspace("throttle-headers@example.com")
    vault = OAuthTokenVault(workspace_id, user_id)
    connector = GitHubConnector(vault)
    fake = fake_github

    reset = 9_999_999_999
    fake.add(
        "GET",
        "/repos/acme/api",
        {"message": "primary rate limit"},
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)},
    )
    batch = connector.poll({"repository": "acme/api", "signals_only": True})
    assert batch.has_more
    assert batch.retry_after_seconds > 3600
    assert batch.next_cursor == {"repository": "acme/api", "signals_only": True}

    # Secondary-limit 403 without headers still backs off durably (60s).
    fake.routes.clear()
    fake.add(
        "GET",
        "/repos/acme/api",
        {"message": "You have exceeded a secondary rate limit"},
        status=403,
    )
    batch = connector.poll({"repository": "acme/api", "signals_only": True})
    assert batch.retry_after_seconds == 60

    # A positive local-limiter delay raises the same durable throttle with the
    # cursor unchanged.
    monkeypatch.setattr(
        github_client._SIGNAL_LIMITER, "try_acquire", lambda key, requests, window: 1.7
    )
    cursor = {"repository": "acme/api", "signals_only": True}
    batch = connector.poll(cursor)
    assert batch.has_more
    assert batch.retry_after_seconds == 2
    assert batch.records == ()
    assert batch.next_cursor == cursor


def test_optional_forbidden_never_emits_success(graph, fake_github, frozen_clock):
    workspace_id, user_id, project_id = _signal_workspace("forbidden@example.com")
    engine = _signal_engine(graph)
    fake = fake_github
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    _serve_repo(fake, "sha-default-1")
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [])
    fake.add(
        "GET",
        "/repos/acme/api/commits/sha-default-1/check-runs?filter=all&per_page=100&page=1",
        {"message": "Resource not accessible by integration"},
        status=403,
    )
    fake.add(
        "GET",
        "/repos/acme/api/actions/runs?head_sha=sha-default-1&per_page=100&page=1",
        {
            "total_count": 1,
            "workflow_runs": [
                _workflow_run(101, 1, "failure", "sha-default-1", "2026-10-08T10:00:00Z")
            ],
        },
    )
    fake.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])

    job = engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="forbidden-1",
    )
    _drain(engine, workspace_id)
    assert row("SELECT status FROM connector_sync_jobs WHERE id=?", (job["id"],))["status"] == (
        "succeeded"
    )
    # The forbidden producer emitted no success; the workflow failure opened.
    kinds = rows("SELECT kind, status FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert kinds == [{"kind": "ci_failure", "status": "open"}]
    states = {item["state"] for item in rows("SELECT state FROM live_signals")}
    assert states == {"failure"}

    # The refusal is recorded as a sanitized cursor diagnostic mid-cycle.
    vault = OAuthTokenVault(workspace_id, user_id)
    connector = GitHubConnector(vault)
    cursor: dict = {"repository": "acme/api", "signals_only": True}
    for _ in range(4):
        batch = connector.poll(cursor, now="2026-10-08T12:00:00.000000+00:00")
        cursor = batch.next_cursor
        assert batch.has_more
    assert cursor["signals"]["diagnostics"] == {"checks:default:head": "forbidden"}


def test_one_data_request_per_poll_batch(graph, fake_github):
    workspace_id, user_id, _ = _signal_workspace("pacing@example.com")
    vault = OAuthTokenVault(workspace_id, user_id)
    connector = GitHubConnector(vault)
    fake = fake_github
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    _serve_repo(fake, "sha-default-1")
    fake.add(
        "GET",
        "/repos/acme/api/pulls?state=open&per_page=100&page=1",
        [_open_pr(11)],
    )
    fake.add("GET", "/repos/acme/api/pulls/11", _open_pr(11))
    fake.add("GET", "/repos/acme/api/pulls/11/reviews?per_page=100&page=1", [])
    for sha in ("sha-default-1", "sha-pr-11"):
        fake.add(
            "GET",
            f"/repos/acme/api/commits/{sha}/check-runs?filter=all&per_page=100&page=1",
            {"total_count": 0, "check_runs": []},
        )
        fake.add(
            "GET",
            f"/repos/acme/api/actions/runs?head_sha={sha}&per_page=100&page=1",
            {"total_count": 0, "workflow_runs": []},
        )
    fake.add(
        "GET",
        "/repos/acme/api/deployments?per_page=100&page=1",
        [
            {
                "id": 5001,
                "environment": "production",
                "task": "deploy",
                "sha": "sha-default-1",
                "created_at": "2026-10-08T09:00:00Z",
            }
        ],
    )
    fake.add(
        "GET",
        "/repos/acme/api/deployments/5001/statuses?per_page=100&page=1",
        [{"id": 6001, "state": "failure", "created_at": "2026-10-08T09:10:00Z"}],
    )

    cursor: dict = {"repository": "acme/api", "signals_only": True}
    per_batch: list[int] = []
    batches = 0
    while True:
        before = fake.request_count()
        batch = connector.poll(cursor, now="2026-10-08T12:00:00.000000+00:00")
        per_batch.append(fake.request_count() - before)
        batches += 1
        cursor = batch.next_cursor
        if not batch.has_more:
            break
        assert batches < 100
    assert batches > 5
    assert max(per_batch) <= 1
    assert sum(per_batch) == fake.request_count()
    assert "signals" not in cursor
    assert cursor["signals_last_completed_at"] == "2026-10-08T12:00:00.000000+00:00"


def test_old_cursor_starts_signal_snapshot(graph, fake_github):
    workspace_id, user_id, _ = _signal_workspace("old-cursor@example.com")
    vault = OAuthTokenVault(workspace_id, user_id)
    connector = GitHubConnector(vault)
    fake = fake_github
    fake.add("GET", "/repos/acme/api", dict(REPO))

    old_cursor = {
        "repository": "acme/api",
        "last_commit_sha": "sha-ancient",
        "synced_at": "2026-09-01T00:00:00+00:00",
        "signals_only": True,
    }
    batch = connector.poll(old_cursor)
    assert batch.has_more
    assert fake.requests[0] == ("GET", "/repos/acme/api")
    assert batch.next_cursor["signals"]["phase"] == "branch"
    assert batch.next_cursor["last_commit_sha"] == "sha-ancient"

    with pytest.raises(ValueError, match="unsupported signal cursor version"):
        connector.poll({"repository": "acme/api", "signals": {"version": 999}})
    with pytest.raises(ValueError, match="invalid github repository"):
        connector.poll({"repository": "not a slug?!"})


def test_unknown_cursor_version_raises_from_sync(graph, fake_github):
    workspace_id, user_id, _ = _signal_workspace("bad-version@example.com")
    vault = OAuthTokenVault(workspace_id, user_id)
    account = vault.account("github")
    connector = GitHubConnector(vault)
    with pytest.raises(ValueError, match="unsupported signal cursor version"):
        connector.sync(
            account,
            {"repository": "acme/api", "signals": {"version": 999}},
        )
