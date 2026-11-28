"""Automatic briefings end to end: the CLI and the GitHub check against the real API.

These drive the shipped SDK code (``python_sdk/src/orgmemory``) through the
FastAPI app in-process, so the briefing is served by the unchanged
``POST /api/briefings`` route and ``briefing.build()``, and the ledger rows are
real. Only GitHub is faked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.audit import AuditService
from app.auth.api_keys import create_api_key
from app.auth.app_auth import create_dev_session, create_workspace
from app.core.database import connect
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.main import app

SDK = Path(__file__).resolve().parents[2] / "python_sdk" / "src"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from orgmemory import cli, github_check  # noqa: E402
from orgmemory.client import MemoryWorks  # noqa: E402


@pytest.fixture
def workspace(graph):
    owner = create_dev_session("check-owner@example.com", "Check Owner")
    space = create_workspace("Check Workspace", owner["token"])
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Payments")
    with connect() as conn:
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", (space["id"], project_id))
    key = create_api_key("GitHub check", space["id"], owner["user"]["id"])
    return {"project_id": project_id, "key": key["api_key"], "ingestion": ingestion}


def _seed_payments_incident(workspace) -> None:
    workspace["ingestion"].ingest_item(
        workspace["project_id"],
        "document",
        "Postmortem: duplicate charges",
        "The payments-service retry wrapper failed after a processor timeout and charged "
        "customers twice. Every retry of a card authorization must carry the idempotency key.",
        source_id="doc:postmortem-duplicate-charges",
    )


def _api(key: str) -> MemoryWorks:
    # The TestClient's transport runs the real ASGI app in-process.
    return MemoryWorks(
        base_url="http://testserver", api_key=key, transport=TestClient(app)._transport
    )


def _ledger_rows(surface: str) -> list[dict]:
    with connect() as conn:
        found = conn.execute(
            "SELECT id, surface, query FROM context_events WHERE surface=?", (surface,)
        ).fetchall()
    return [dict(zip(("id", "surface", "query"), row, strict=True)) for row in found]


class FakeGitHub:
    def __init__(self) -> None:
        self.check_runs: dict[int, dict] = {}
        self.comments: dict[int, dict] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path.endswith("/pulls/12/files"):
            return httpx.Response(
                200,
                json=[{"filename": "payments/authorize.py", "additions": 12, "deletions": 4}],
            )
        if "/commits/" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            runs = [r for r in self.check_runs.values() if r["head_sha"] == sha]
            return httpx.Response(200, json={"total_count": len(runs), "check_runs": runs})
        if path.endswith("/check-runs") and method == "POST":
            run = {**json.loads(request.content), "id": len(self.check_runs) + 1}
            self.check_runs[run["id"]] = run
            return httpx.Response(201, json=run)
        if "/check-runs/" in path and method == "PATCH":
            run = self.check_runs[int(path.rsplit("/", 1)[1])]
            run.update(json.loads(request.content))
            return httpx.Response(200, json=run)
        if path.endswith("/issues/12/comments") and method == "GET":
            return httpx.Response(200, json=list(self.comments.values()))
        if path.endswith("/issues/12/comments") and method == "POST":
            comment = {**json.loads(request.content), "id": 500 + len(self.comments)}
            self.comments[comment["id"]] = comment
            return httpx.Response(201, json=comment)
        if "/issues/comments/" in path and method == "PATCH":
            comment = self.comments[int(path.rsplit("/", 1)[1])]
            comment.update(json.loads(request.content))
            return httpx.Response(200, json=comment)
        return httpx.Response(404, json={"message": "Not Found"})

    def api(self) -> github_check.GitHubAPI:
        return github_check.GitHubAPI("ghs_fixture", transport=httpx.MockTransport(self.handler))


def _pull_request_event(sha: str = "c" * 40) -> dict:
    return {
        "action": "synchronize",
        "repository": {"full_name": "acme/checkout"},
        "pull_request": {
            "number": 12,
            "title": "Add a retry to card authorization",
            "body": "Retries a timed-out processor call once.",
            "html_url": "https://github.com/acme/checkout/pull/12",
            "head": {"sha": sha},
            "base": {"ref": "main"},
        },
    }


def test_fixture_pull_request_serves_a_real_briefing_and_posts_its_verdict(workspace):
    _seed_payments_incident(workspace)
    gh = FakeGitHub()

    with _api(workspace["key"]) as client:
        result = github_check.run(
            event=_pull_request_event(),
            github=gh.api(),
            memoryworks=client,
            service="payments-service",
            project_id=workspace["project_id"],
        )

    assert result.briefing_id
    assert result.briefing["verdict"] in {"proceed", "proceed_with_context", "requires_approval"}
    (row,) = _ledger_rows("github_check")
    assert row["id"] == result.briefing_id
    assert "Pull request #12 in acme/checkout" in row["query"]
    (run,) = gh.check_runs.values()
    summary = run["output"]["summary"]
    assert f"verdict: `{result.briefing['verdict']}`" in summary
    assert f"briefing_id: `{result.briefing_id}`" in summary
    cited = [
        item["memory_id"]
        for group in ("must_read", "constraints", "prior_incidents", "blast_radius", "procedures")
        for item in result.briefing[group]
    ]
    assert cited
    assert all(memory_id in summary for memory_id in cited)
    (comment,) = gh.comments.values()
    assert result.briefing_id in comment["body"]


def test_the_same_pull_request_sha_twice_opens_one_ledger_row_and_one_check(workspace):
    _seed_payments_incident(workspace)
    gh = FakeGitHub()

    with _api(workspace["key"]) as client:
        first = github_check.run(
            event=_pull_request_event(),
            github=gh.api(),
            memoryworks=client,
            service="payments-service",
            project_id=workspace["project_id"],
        )
        second = github_check.run(
            event=_pull_request_event(),
            github=gh.api(),
            memoryworks=client,
            service="payments-service",
            project_id=workspace["project_id"],
        )

    assert second.reused is True
    assert second.briefing_id == first.briefing_id
    assert len(_ledger_rows("github_check")) == 1
    assert len(gh.check_runs) == 1
    assert len(gh.comments) == 1


def test_an_empty_briefing_fails_the_check_as_unavailable_not_no_memory(workspace):
    gh = FakeGitHub()

    with _api(workspace["key"]) as client:
        result = github_check.run(
            event=_pull_request_event(),
            github=gh.api(),
            memoryworks=client,
            service="payments-service",
            project_id=workspace["project_id"],
        )

    # The route cannot yet tell an empty index from a failed one, so the check
    # must not tell a reviewer that company memory holds nothing.
    assert result.briefing["verdict"] == "no_memory"
    assert result.state == "unavailable"
    assert gh.check_runs[1]["conclusion"] == "failure"
    assert gh.check_runs[1]["output"]["title"].startswith("Briefing unavailable")


def test_a_rejected_api_key_fails_the_check_as_unavailable(workspace):
    gh = FakeGitHub()

    with _api("rbk_not_a_real_key") as client:
        result = github_check.run(event=_pull_request_event(), github=gh.api(), memoryworks=client)

    assert result.state == "unavailable"
    assert "401" in result.error
    assert gh.check_runs[1]["conclusion"] == "failure"
    assert _ledger_rows("github_check") == []


def test_cli_brief_and_outcome_round_trip_through_the_live_api(workspace, monkeypatch, capsys):
    _seed_payments_incident(workspace)
    monkeypatch.setattr(cli, "MemoryWorks", lambda **_: _api(workspace["key"]))

    assert (
        cli.main(
            [
                "brief",
                "add a retry to card authorization",
                "--service",
                "payments-service",
                "--project-id",
                workspace["project_id"],
                "--json",
            ]
        )
        == 0
    )
    briefing = json.loads(capsys.readouterr().out)
    assert briefing["briefing_id"]
    (row,) = _ledger_rows("cli")
    assert row["id"] == briefing["briefing_id"]

    assert (
        cli.main(
            [
                "outcome",
                briefing["briefing_id"],
                "edit_applied",
                "--outcome",
                "succeeded",
                "--reason",
                "Local tests passed; not a production observation.",
                "--detail",
                '{"verification_scope": "local"}',
                "--json",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["recorded"] is True
    assert receipt["outcome"]["outcome"] == "succeeded"

    exported = TestClient(app).get(
        "/api/outcomes/export?labelled_only=true&limit=10",
        headers={"Authorization": f"Bearer {workspace['key']}"},
    )
    (record,) = [
        r for r in exported.json()["records"] if r["context_event_id"] == briefing["briefing_id"]
    ]
    assert record["label"] == "succeeded"
    assert record["actions"][0]["action_type"] == "edit_applied"


def test_cli_outcome_for_an_unknown_briefing_fails(workspace, monkeypatch, capsys):
    monkeypatch.setattr(cli, "MemoryWorks", lambda **_: _api(workspace["key"]))

    code = cli.main(["outcome", "ctx_missing", "edit_applied", "--outcome", "failed"])

    assert code == 1
    assert "Unknown briefing" in capsys.readouterr().err
