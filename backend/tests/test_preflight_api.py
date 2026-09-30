"""The public briefing-to-outcome contract used by MCP and SDK clients."""

from fastapi.testclient import TestClient

from app.audit import AuditService
from app.auth.api_keys import create_api_key
from app.auth.app_auth import create_dev_session, create_workspace
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

    closed = client.post(
        "/api/briefings/outcome",
        headers=headers,
        json={
            "briefing_id": briefing["briefing_id"],
            "action": "plan_changed",
            "outcome": "succeeded",
            "target": "fixture/payments#1",
            "surface": "contract_test",
            "reason": "The test workflow completed.",
        },
    )

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
