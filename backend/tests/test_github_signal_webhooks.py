"""GitHub signal webhooks: signed receipt, workspace fan-out, and input guards."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes as api_routes
from app.auth.app_auth import create_dev_session
from app.auth.vault import OAuthTokenVault
from app.connectors.github import client as github_client
from app.connectors.github.client import GitHubConnector
from app.connectors.registry import ConnectorRegistry
from app.connectors.sync import SyncEngine
from app.core.config import settings
from app.core.database import connect, new_id, row, rows, utcnow
from app.main import app

SECRET = "test-webhook-secret"


class _FakeHTTP:
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
    return base + "?" + "&".join(sorted(part for part in query.split("&") if part))


class FakeGitHub:
    def __init__(self):
        self.routes: dict = {}
        self.requests: list = []

    def add(self, method, path, payload, status=200, headers=None):
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


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    return SECRET


@pytest.fixture
def fake_github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr("app.connectors.github.client.httpx.request", fake)
    monkeypatch.setattr(
        github_client._SIGNAL_LIMITER, "try_acquire", lambda key, requests, window: 0.0
    )
    return fake


@pytest.fixture
def frozen_clock(monkeypatch):
    state = {"now": datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)}

    def _now():
        return state["now"].isoformat(timespec="microseconds")

    monkeypatch.setattr("app.orgops.signals.utcnow", _now)
    return state


def _bind_project(workspace_id: str, repository: str = "acme/api", name: str = "API") -> str:
    project_id = new_id("prj")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (project_id, name, repository, "ready", now, now),
        )
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", (workspace_id, project_id))
    return project_id


def _workspace(email: str):
    session = create_dev_session(email, "Webhook Owner")
    workspace_id = session["user"]["active_workspace_id"]
    user_id = session["user"]["id"]
    project_id = _bind_project(workspace_id)
    vault = OAuthTokenVault(workspace_id, user_id)
    vault.save("github", "42", "octo", "delegated-token")
    return session, workspace_id, user_id, project_id


def _engine(graph):
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


@pytest.fixture
def engine(graph, monkeypatch):
    engine = _engine(graph)
    monkeypatch.setattr(api_routes, "connector_sync", engine)
    return engine


def _post_webhook(client, path, event_type, payload, delivery="delivery-1", secret=SECRET):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        path,
        content=body,
        headers={
            "x-hub-signature-256": signature,
            "x-github-delivery": delivery,
            "x-github-event": event_type,
            "content-type": "application/json",
        },
    )


def _guard_operational_path(monkeypatch):
    """Operational webhooks must never reach memory, LLM, or ingestion code."""

    def _boom(*args, **kwargs):
        raise AssertionError("operational webhook must not call model/change/ingestion")

    monkeypatch.setattr(api_routes.change_intelligence, "observe", _boom)
    monkeypatch.setattr(api_routes.change_intelligence, "process", _boom)
    monkeypatch.setattr(api_routes, "RepositoryIngestor", _boom)


def _make_due():
    with connect() as conn:
        conn.execute(
            "UPDATE connector_sync_jobs SET next_attempt_at=? WHERE status IN ('queued','retrying')",
            (utcnow(),),
        )


def _drain(engine, workspace_id, cap=400):
    for _ in range(cap):
        _make_due()
        if engine.run_once(limit=10) == 0:
            break
    pending = [
        job
        for job in engine.list(workspace_id)
        if job["status"] in ("queued", "retrying", "running")
    ]
    assert not pending, f"snapshot did not converge: {pending}"


REPO = {"id": 42, "full_name": "acme/api", "default_branch": "main"}


def _serve_snapshot(fake: FakeGitHub, default_sha="sha-default-1", workflows=(), checks=()):
    fake.add("GET", "/repos/acme/api/commits?per_page=100", [])
    fake.add("GET", "/repos/acme/api", dict(REPO))
    fake.add(
        "GET", "/repos/acme/api/branches/main", {"name": "main", "commit": {"sha": default_sha}}
    )
    fake.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [])
    fake.add(
        "GET",
        f"/repos/acme/api/commits/{default_sha}/check-runs?filter=all&per_page=100&page=1",
        {"total_count": len(checks), "check_runs": list(checks)},
    )
    fake.add(
        "GET",
        f"/repos/acme/api/actions/runs?head_sha={default_sha}&per_page=100&page=1",
        {"total_count": len(workflows), "workflow_runs": list(workflows)},
    )
    fake.add("GET", "/repos/acme/api/deployments?per_page=100&page=1", [])


def _workflow(run_id, attempt, conclusion, sha, updated):
    return {
        "id": run_id,
        "workflow_id": 7,
        "run_attempt": attempt,
        "head_sha": sha,
        "head_branch": "main",
        "created_at": "2026-10-08T09:55:00Z",
        "updated_at": updated,
        "status": "completed",
        "conclusion": conclusion,
    }


def _check(check_id, conclusion, sha):
    return {
        "id": check_id,
        "name": "build",
        "app": {"id": 11},
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": "2026-10-08T09:55:00Z",
        "completed_at": "2026-10-08T10:00:00Z",
    }


def test_signed_operational_webhook_queues_without_llm(
    graph, engine, fake_github, frozen_clock, webhook_secret, monkeypatch
):
    _guard_operational_path(monkeypatch)
    session, workspace_id, _, project_id = _workspace("op-queue@example.com")
    check = _check(9001, "failure", "sha-default-1")
    _serve_snapshot(fake_github, checks=())
    fake_github.add("GET", "/repos/acme/api/check-runs/9001", dict(check))

    client = TestClient(app)
    response = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        {
            "action": "completed",
            "repository": dict(REPO),
            "check_run": {
                "id": 9001,
                "head_sha": "sha-default-1",
                "status": "completed",
                "conclusion": "failure",
                "check_suite": {"head_branch": "main"},
                "pull_requests": [],
            },
        },
        delivery="delivery-check-1",
    )
    assert response.status_code == 202
    receipt = response.json()
    assert receipt["accepted"] is True
    assert receipt["replayed"] is False
    assert receipt["delivery_id"] == "delivery-check-1"
    assert receipt["records_applied"] == 0

    jobs = [job for job in engine.list(workspace_id) if (job["cursor"].get("signals_only") is True)]
    assert len(jobs) == 1
    assert jobs[0]["project_id"] == project_id
    hints = jobs[0]["cursor"]["signals"]["hints"]
    assert hints == [{"kind": "check", "id": 9001, "branch": "main", "sha": "sha-default-1"}]

    _drain(engine, workspace_id)
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "open"
    assert issues[0]["kind"] == "ci_failure"


def test_webhook_and_poll_share_observation_identity(
    graph, engine, fake_github, frozen_clock, webhook_secret, monkeypatch
):
    _guard_operational_path(monkeypatch)
    _, workspace_id, user_id, project_id = _workspace("identity@example.com")
    red_attempt_1 = _workflow(101, 1, "failure", "sha-default-1", "2026-10-08T10:00:00Z")
    red_attempt_2 = _workflow(101, 2, "failure", "sha-default-1", "2026-10-08T10:01:00Z")
    _serve_snapshot(fake_github, workflows=[red_attempt_1])
    fake_github.add("GET", "/repos/acme/api/actions/runs/101", dict(red_attempt_2))

    client = TestClient(app)
    # Poll-only snapshot first: attempt 1 opens the issue.
    engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="identity-poll-1",
    )
    _drain(engine, workspace_id)
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["occurrences"] == 1

    # Webhook hint for the rerun attempt: same issue, second occurrence.
    response = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "workflow_run",
        {
            "action": "completed",
            "repository": dict(REPO),
            "workflow_run": {
                "id": 101,
                "workflow_id": 7,
                "run_attempt": 2,
                "head_sha": "sha-default-1",
                "head_branch": "main",
                "created_at": "2026-10-08T09:55:00Z",
                "updated_at": "2026-10-08T10:01:00Z",
                "status": "completed",
                "conclusion": "failure",
                "pull_requests": [],
            },
        },
        delivery="delivery-workflow-2",
    )
    assert response.status_code == 202
    _drain(engine, workspace_id)
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["occurrences"] == 2

    # A later poll-only snapshot of the same rerun is a duplicate refresh.
    frozen_clock["now"] += timedelta(hours=1)
    fake_github.routes.clear()
    _serve_snapshot(fake_github, workflows=[red_attempt_2])
    engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api", "last_commit_sha": "sha-default-1"},
        idempotency_key="identity-poll-2",
    )
    _drain(engine, workspace_id)
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["occurrences"] == 2


def test_webhook_project_fanout_is_workspace_bound(
    graph, engine, fake_github, frozen_clock, webhook_secret, monkeypatch
):
    _guard_operational_path(monkeypatch)
    _, workspace_id, user_id, first_project = _workspace("fanout@example.com")
    second_project = _bind_project(workspace_id, name="Second API")
    _serve_snapshot(fake_github)
    fake_github.add(
        "GET",
        "/repos/acme/api/deployments/5001",
        {
            "id": 5001,
            "environment": "production",
            "task": "deploy",
            "sha": "sha-default-1",
            "created_at": "2026-10-08T09:00:00Z",
        },
    )

    client = TestClient(app)
    response = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "deployment_status",
        {
            "repository": dict(REPO),
            "deployment": {"id": 5001},
            "deployment_status": {"id": 6001, "state": "failure"},
        },
        delivery="delivery-deploy-1",
    )
    assert response.status_code == 202
    jobs = sorted(
        (job for job in engine.list(workspace_id) if job["cursor"].get("signals_only") is True),
        key=lambda job: job["project_id"],
    )
    assert [job["project_id"] for job in jobs] == sorted([first_project, second_project])
    assert {job["cursor"]["signals"]["hints"][0]["id"] for job in jobs} == {5001}

    # An unknown repository cannot gain a project from old sync jobs: the
    # historical-job fallback is never consulted for GitHub.
    with connect() as conn:
        conn.execute(
            """INSERT INTO connector_sync_jobs
            (id,workspace_id,user_id,provider,resource_id,project_id,cursor_json,
             status,attempts,max_attempts,next_attempt_at,last_error,idempotency_key,
             created_at,updated_at,completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (
                "sync_legacy_1",
                workspace_id,
                user_id,
                "github",
                "unknown/repo",
                second_project,
                json.dumps({"repository": "unknown/repo"}),
                "succeeded",
                0,
                6,
                utcnow(),
                "",
                "legacy-key-1",
                utcnow(),
                utcnow(),
            ),
        )
    bad = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        {
            "repository": {"id": 7, "full_name": "unknown/repo", "default_branch": "main"},
            "check_run": {"id": 1},
        },
        delivery="delivery-unknown-1",
    )
    assert bad.status_code == 400
    assert (
        len([job for job in engine.list(workspace_id) if job["cursor"].get("signals_only") is True])
        == 2
    )


def test_legacy_operational_ambiguous_workspace_is_409(graph, engine, webhook_secret, monkeypatch):
    _guard_operational_path(monkeypatch)
    first = create_dev_session("ambig-one@example.com", "First Owner")
    first_workspace = first["user"]["active_workspace_id"]
    _bind_project(first_workspace)
    second = create_dev_session("ambig-two@example.com", "Second Owner")
    from app.auth.app_auth import create_workspace

    second_workspace = create_workspace("Second workspace", second["token"])["id"]
    _bind_project(second_workspace)
    client = TestClient(app)
    payload = {
        "repository": dict(REPO),
        "check_run": {"id": 9001},
    }
    response = _post_webhook(client, "/api/webhooks/github", "check_run", payload)
    assert response.status_code == 409

    missing = _post_webhook(
        client,
        "/api/webhooks/github",
        "check_run",
        {"repository": {"id": 9, "full_name": "nobody/here"}, "check_run": {"id": 1}},
    )
    assert missing.status_code == 404


def test_bad_signature_and_changed_payload_replay_fail(graph, engine, webhook_secret, monkeypatch):
    _guard_operational_path(monkeypatch)
    _, workspace_id, _, _ = _workspace("replay@example.com")
    client = TestClient(app)
    payload = {
        "repository": dict(REPO),
        "check_run": {"id": 9001},
    }
    forged = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        payload,
        delivery="delivery-forged-1",
        secret="wrong-secret",
    )
    assert forged.status_code == 401
    legacy_forged = _post_webhook(
        client, "/api/webhooks/github", "check_run", payload, secret="wrong-secret"
    )
    assert legacy_forged.status_code == 401

    first = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        payload,
        delivery="delivery-replay-1",
    )
    assert first.status_code == 202
    changed = dict(payload, check_run={"id": 9002})
    second = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        changed,
        delivery="delivery-replay-1",
    )
    assert second.status_code == 400


def test_retryable_receipt_retries_idempotent_enqueue(graph, engine, webhook_secret, monkeypatch):
    _guard_operational_path(monkeypatch)
    _, workspace_id, _, project_id = _workspace("retry@example.com")
    client = TestClient(app)
    payload = {
        "repository": dict(REPO),
        "check_run": {"id": 9001},
    }
    first = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        payload,
        delivery="delivery-retry-1",
    )
    assert first.status_code == 202
    assert first.json()["accepted"] is True
    key = "webhook:delivery-retry-1:" + project_id
    assert (
        row("SELECT COUNT(*) AS n FROM connector_sync_jobs WHERE idempotency_key=?", (key,))["n"]
        == 1
    )

    with connect() as conn:
        conn.execute(
            "UPDATE connector_webhook_deliveries SET status='retryable' WHERE delivery_id=?",
            ("delivery-retry-1",),
        )
    second = _post_webhook(
        client,
        f"/api/webhooks/github/{workspace_id}",
        "check_run",
        payload,
        delivery="delivery-retry-1",
    )
    assert second.status_code == 202
    assert second.json()["accepted"] is True
    assert second.json()["replayed"] is False
    assert (
        row("SELECT COUNT(*) AS n FROM connector_sync_jobs WHERE idempotency_key=?", (key,))["n"]
        == 1
    )
    assert (
        row(
            "SELECT status FROM connector_webhook_deliveries WHERE delivery_id=?",
            ("delivery-retry-1",),
        )["status"]
        == "succeeded"
    )


def test_closed_pr_webhook_resolves_stall(
    graph, engine, fake_github, frozen_clock, webhook_secret, monkeypatch
):
    _, workspace_id, _, project_id = _workspace("close-stall@example.com")
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
    _serve_snapshot(fake_github)
    fake_github.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [dict(stalled)])
    fake_github.add("GET", "/repos/acme/api/pulls/11", dict(stalled))
    fake_github.add("GET", "/repos/acme/api/pulls/11/reviews?per_page=100&page=1", [])
    for sha in ("sha-pr-head", "sha-merge-1"):
        fake_github.add(
            "GET",
            f"/repos/acme/api/commits/{sha}/check-runs?filter=all&per_page=100&page=1",
            {"total_count": 0, "check_runs": []},
        )
        fake_github.add(
            "GET",
            f"/repos/acme/api/actions/runs?head_sha={sha}&per_page=100&page=1",
            {"total_count": 0, "workflow_runs": []},
        )
    user_id = row("SELECT user_id FROM oauth_token_grants LIMIT 1")["user_id"]
    engine.enqueue(
        "github",
        workspace_id,
        user_id,
        "acme/api",
        project_id=project_id,
        cursor={"repository": "acme/api"},
        idempotency_key="stall-first-1",
    )
    _drain(engine, workspace_id)
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "open"

    captured: dict = {}

    def _capture(kind, payload, **kwargs):
        captured["kind"] = kind
        captured["payload"] = payload
        return {"queued": True}

    monkeypatch.setattr("app.jobs.enqueue", _capture)
    ingested: list = []
    monkeypatch.setattr(
        api_routes, "RepositoryIngestor", lambda *args, **kwargs: _IngestStub(ingested)
    )
    monkeypatch.setattr(
        api_routes,
        "github_diff",
        lambda payload, connector: (
            {"diff": "x"},
            {"commit_sha": "sha-pr-head", "repository": "acme/api"},
        ),
    )
    processed: list = []
    monkeypatch.setattr(
        api_routes.change_intelligence, "process", lambda *args: processed.append(args)
    )

    client = TestClient(app)
    closed_payload = {
        "action": "closed",
        "repository": dict(REPO),
        "pull_request": dict(stalled, state="closed", number=11),
    }
    response = _post_webhook(
        client, "/api/webhooks/github", "pull_request", closed_payload, delivery="delivery-close-1"
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["replayed"] is False
    assert captured["kind"] == "github.webhook"
    assert captured["payload"]["pr_hint"] == 11

    api_routes._process_github_webhook_event(
        captured["payload"]["event_id"],
        captured["payload"]["payload"],
        captured["payload"]["workspace_id"],
        captured["payload"]["pr_hint"],
    )
    assert ingested and processed

    frozen_clock["now"] += timedelta(hours=1)
    fake_github.routes.clear()
    _serve_snapshot(fake_github)
    fake_github.add("GET", "/repos/acme/api/pulls?state=open&per_page=100&page=1", [])
    fake_github.add("GET", "/repos/acme/api/pulls/11", dict(stalled, state="closed"))
    _drain(engine, workspace_id)
    issues = rows("SELECT * FROM live_issues WHERE workspace_id=?", (workspace_id,))
    assert len(issues) == 1
    assert issues[0]["status"] == "resolved"


class _IngestStub:
    def __init__(self, ingested):
        self._ingested = ingested

    def ingest(self, *args):
        self._ingested.append(args)
        return {}


def test_sync_rejects_repository_mismatch_and_discards_forged_hints(graph, engine, webhook_secret):
    session, workspace_id, _, project_id = _workspace("sync-guard@example.com")
    other_project = _bind_project(workspace_id, repository="other/repo", name="Other")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {session['token']}"}

    mismatch = client.post(
        "/api/connectors/github/sync",
        json={
            "resource_id": "acme/api",
            "project_id": project_id,
            "cursor": {"repository": "evil/other"},
        },
        headers=headers,
    )
    assert mismatch.status_code == 400

    wrong_project = client.post(
        "/api/connectors/github/sync",
        json={
            "resource_id": "acme/api",
            "project_id": other_project,
            "cursor": {"repository": "acme/api"},
        },
        headers=headers,
    )
    assert wrong_project.status_code == 400

    forged = client.post(
        "/api/connectors/github/sync",
        json={
            "resource_id": "acme/api",
            "project_id": project_id,
            "cursor": {
                "repository": "acme/api",
                "signals": {"version": 1, "hints": [{"kind": "pr", "number": 99}]},
                "signals_only": True,
                "hints": [{"kind": "pr", "number": 99}],
            },
            "idempotency_key": "forged-1",
        },
        headers=headers,
    )
    assert forged.status_code == 200
    stored = engine.get(forged.json()["id"])
    assert stored["cursor"]["repository"] == "acme/api"
    assert "signals" not in stored["cursor"]
    assert "signals_only" not in stored["cursor"]
    assert "hints" not in stored["cursor"]

    filled = client.post(
        "/api/connectors/github/sync",
        json={"resource_id": "acme/api", "project_id": project_id, "cursor": {}},
        headers=headers,
    )
    assert filled.status_code == 200
    assert engine.get(filled.json()["id"])["cursor"]["repository"] == "acme/api"


def test_legacy_push_keeps_change_response(graph, engine, webhook_secret, monkeypatch):
    _, workspace_id, _, project_id = _workspace("push-legacy@example.com")
    observed: list = []

    def _observe(*args):
        observed.append(args)
        return {"id": "evt-1"}, True

    monkeypatch.setattr(api_routes.change_intelligence, "observe", _observe)
    enqueued: list = []

    def _capture(kind, payload, **kwargs):
        enqueued.append((kind, payload))
        return {"queued": True}

    monkeypatch.setattr("app.jobs.enqueue", _capture)
    client = TestClient(app)
    response = _post_webhook(
        client,
        "/api/webhooks/github",
        "push",
        {"repository": dict(REPO), "after": "sha-push-1"},
        delivery="delivery-push-1",
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["replayed"] is False
    assert observed
    assert observed[0][0] == project_id
    assert enqueued and enqueued[0][0] == "github.webhook"
    assert enqueued[0][1]["workspace_id"] == workspace_id
    # Push keeps the change path: no signals_only sync job is enqueued here.
    assert not [
        job for job in engine.list(workspace_id) if job["cursor"].get("signals_only") is True
    ]
