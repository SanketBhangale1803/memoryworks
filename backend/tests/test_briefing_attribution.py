"""A briefing's ledger row names the memories it showed, so outcomes can be attributed."""

import json

from fastapi.testclient import TestClient

from app.audit import AuditService
from app.auth.api_keys import create_api_key
from app.auth.app_auth import create_dev_session, create_workspace
from app.core.database import connect
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.main import app


def test_briefing_ledger_row_records_the_memories_it_showed(graph):
    owner = create_dev_session("attribution-owner@example.com", "Attribution Owner")
    workspace = create_workspace("Attribution Workspace", owner["token"])
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Payments")
    with connect() as conn:
        conn.execute(
            "INSERT INTO workspace_projects VALUES (?,?)",
            (workspace["id"], project_id),
        )
    ingestion.ingest_item(
        project_id,
        "document",
        "Postmortem: duplicate charges",
        "The payments-service retry wrapper failed after a processor timeout and charged "
        "customers twice. Every retry of a card authorization must carry the idempotency key.",
        source_id="doc:postmortem-duplicate-charges",
    )
    key = create_api_key("Attribution client", workspace["id"], owner["user"]["id"])
    client = TestClient(app)

    briefing = client.post(
        "/api/briefings",
        headers={"Authorization": f"Bearer {key['api_key']}"},
        json={
            "task": "add a retry to card authorization",
            "service": "payments-service",
            "project_id": project_id,
        },
    ).json()

    shown = [
        item["memory_id"]
        for group in ("must_read", "constraints", "prior_incidents", "blast_radius", "procedures")
        for item in briefing[group]
    ]
    assert shown
    with connect() as conn:
        ledger = conn.execute(
            "SELECT evidence_ids_json, source_ids_json FROM context_events WHERE id=?",
            (briefing["briefing_id"],),
        ).fetchone()
    assert json.loads(ledger[0]) == shown
    assert json.loads(ledger[1]) == ["doc:postmortem-duplicate-charges"]
