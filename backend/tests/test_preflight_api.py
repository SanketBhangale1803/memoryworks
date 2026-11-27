"""The public briefing-to-outcome contract used by MCP and SDK clients."""

import hmac
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.audit import AuditService
from app.auth.api_keys import create_api_key
from app.auth.app_auth import create_dev_session, create_workspace
from app.core.config import settings
from app.core.database import connect
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.main import app


def test_workspace_client_can_open_and_close_a_briefing_ledger_row(graph):
    owner = create_dev_session("preflight-owner@example.com", "Preflight Owner")
    workspace = create_workspace("Preflight Workspace", owner["token"])
    project_id = IngestionService(graph, HCAGAdapter(graph), AuditService()).create_project(
        "Payments"
    )
    with connect() as conn:
        conn.execute(
            "INSERT INTO workspace_projects VALUES (?,?)",
            (workspace["id"], project_id),
        )
    key = create_api_key("Preflight client", workspace["id"], owner["user"]["id"])
    headers = {"Authorization": f"Bearer {key['api_key']}"}
    client = TestClient(app)

    served = client.post(
        "/api/briefings",
        headers=headers,
        json={
            "task": "raise the payments worker limit",
            "service": "payments",
            "project_id": project_id,
            "surface": "contract_test",
        },
    )

    assert served.status_code == 200
    briefing = served.json()
    assert briefing["briefing_id"]
    assert briefing["verdict"] in {
        "no_memory",
        "proceed",
        "proceed_with_context",
        "requires_approval",
    }

    report = {
        "briefing_id": briefing["briefing_id"],
        "action": "plan_changed",
        "outcome": "succeeded",
        "target": "fixture/payments#1",
        "surface": "contract_test",
        "reason": "The test workflow completed.",
        "idempotency_key": "report-1",
    }
    closed = client.post("/api/briefings/outcome", headers=headers, json=report)
    retry = client.post("/api/briefings/outcome", headers=headers, json=report)
    assert retry.json() == closed.json()

    assert closed.status_code == 200
    assert closed.json()["briefing_id"] == briefing["briefing_id"]
    assert closed.json()["recorded"] is True

    exported = client.get(
        "/api/outcomes/export?labelled_only=true&limit=10",
        headers=headers,
    )
    assert exported.status_code == 200
    matching = [
        record
        for record in exported.json()["records"]
        if record["context_event_id"] == briefing["briefing_id"]
    ]
    assert len(matching) == 1
    assert matching[0]["label"] == "succeeded"
    assert matching[0]["actions"][0]["action_type"] == "plan_changed"


def test_signed_github_workflow_closes_the_latest_matching_briefing(graph, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "fixture-secret")
    monkeypatch.setattr(settings, "background_work_enabled", True)
    owner = create_dev_session("github-outcomes@example.com", "GitHub Outcomes")
    workspace = create_workspace("GitHub Outcomes", owner["token"])
    repository = "fixture/payments"
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Payments", f"https://github.com/{repository}")
    another = ingestion.create_project("Billing", "https://github.com/fixture/billing")
    other_workspace = create_workspace("Other Workspace", owner["token"])
    with connect() as conn:
        conn.executemany(
            "INSERT INTO workspace_projects VALUES (?,?)",
            ((workspace["id"], project_id), (workspace["id"], another)),
        )
    key = create_api_key("GitHub fixture", workspace["id"], owner["user"]["id"])
    headers = {"Authorization": f"Bearer {key['api_key']}"}
    client = TestClient(app)
    sha = "a" * 40

    def brief(project=project_id, revision=sha, surface="github_check", pr=42):
        response = client.post(
            "/api/briefings",
            headers=headers,
            json={
                "task": "Update the worker configuration",
                "project_id": project,
                "surface": surface,
                "github_sha": revision,
                "github_pr_number": pr,
            },
        )
        assert response.status_code == 200
        return response.json()["briefing_id"]

    older = brief()
    matching = brief()
    wrong_surface = brief(surface="mcp")
    wrong_repo = brief(project=another)
    wrong_sha = brief(revision="b" * 40)
    wrong_pr = brief(pr=43)

    # A context associated with an unrelated workspace is never eligible.
    from app.outcomes import record_context

    foreign = record_context(
        workspace_id=other_workspace["id"],
        project_id=project_id,
        query="Other workspace briefing",
        result={"answer_scope": "briefing"},
        surface="github_check",
        github_repository=repository,
        github_sha=sha,
        github_pr_number=42,
    )
    manual = client.post(
        "/api/briefings/outcome",
        headers=headers,
        json={"briefing_id": matching, "action": "edit_applied", "outcome": "unknown"},
    )
    assert manual.status_code == 200

    observed = datetime.now(UTC) + timedelta(seconds=1)

    def deliver(conclusion, attempt, delivery, *, revision=sha, signed=True, offset=None):
        body = json.dumps(
            {
                "action": "completed",
                "repository": {"id": 90, "full_name": repository, "default_branch": "main"},
                "workflow_run": {
                    "id": 123,
                    "workflow_id": 456,
                    "run_attempt": attempt,
                    "status": "completed",
                    "conclusion": conclusion,
                    "head_sha": revision,
                    "created_at": observed.isoformat(),
                    "updated_at": (
                        observed + timedelta(seconds=attempt if offset is None else offset)
                    ).isoformat(),
                    "pull_requests": [{"number": 42}],
                },
            }
        ).encode()
        signature = hmac.digest(b"fixture-secret", body, "sha256").hex() if signed else "invalid"
        return client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "X-Hub-Signature-256": f"sha256={signature}",
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": delivery,
            },
        )

    def export():
        response = client.get("/api/outcomes/export?labelled_only=true", headers=headers)
        assert response.status_code == 200
        return {item["context_event_id"]: item for item in response.json()["records"]}

    assert deliver("success", 1, "bad-signature", signed=False).status_code == 401
    assert deliver("neutral", 1, "neutral").status_code == 202
    assert deliver("failure", 1, "unmatched", revision="c" * 40).status_code == 202
    assert export() == {}
    assert deliver("success", 1, "success").status_code == 202
    assert export()[matching]["label"] == "succeeded"
    assert deliver("success", 1, "redelivery").status_code == 202
    assert deliver("failure", 2, "failure").status_code == 202
    # A late, distinct delivery with an older provider timestamp is retained.
    assert deliver("success", 3, "out-of-order", offset=0).status_code == 202
    labelled = export()
    assert list(labelled) == [matching]
    record = labelled[matching]
    assert record["label"] == "failed"
    assert [item["signal"] for item in record["outcomes"]] == ["agent", "ci", "ci", "ci"]
    assert (
        "https://github.com/fixture/payments/actions/runs/123" in record["outcomes"][-1]["reason"]
    )
    stats = client.get("/api/outcomes/stats", headers=headers).json()
    assert stats["closed_rate"] == 0.1667
    assert stats["success_rate"] == 0
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_events").fetchone()[0] == 7
        untouched = {older, wrong_surface, wrong_repo, wrong_sha, wrong_pr, foreign}
        assert not untouched.intersection(
            row["context_event_id"]
            for row in conn.execute("SELECT context_event_id FROM outcome_events")
        )
        action_keys = [
            row["idempotency_key"]
            for row in conn.execute("SELECT idempotency_key FROM action_events ORDER BY created_at")
        ]
        outcome_keys = [
            row["idempotency_key"]
            for row in conn.execute(
                "SELECT idempotency_key FROM outcome_events ORDER BY created_at"
            )
        ]
    assert action_keys == outcome_keys
    assert len(set(outcome_keys)) == 4
