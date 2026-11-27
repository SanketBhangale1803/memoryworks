from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from app.connectors.github.signals import normalize_terminal

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
