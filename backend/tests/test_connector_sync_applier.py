import pytest

from app.audit import AuditService
from app.connectors.application import MemoryWorksSyncApplier
from app.connectors.base import SyncOperation, SyncRecord
from app.core.database import row
from app.hcag_adapter import HCAGAdapter
from app.ingestion.service import IngestionService


def _record(record_id: str, resource_type: str, **metadata) -> SyncRecord:
    return SyncRecord(
        id=record_id,
        resource_type=resource_type,
        operation=SyncOperation.UPSERT,
        version="2026-10-05T12:00:00Z",
        title=f"{record_id} title",
        content="The search cluster stays on the current major version until analyzers migrate.",
        source_url=f"https://example.test/{record_id}",
        metadata=metadata,
    )


# Every connector but Slack used to be refused here ("Unsupported memory
# source"), because the applier passed the provider name as the source type.
@pytest.mark.parametrize(
    ("provider", "record", "expected"),
    [
        (
            "google_drive",
            _record(
                "gdrive-file:doc1", "document", mime_type="application/vnd.google-apps.document"
            ),
            "document",
        ),
        (
            "google_drive",
            _record("gdrive-file:pdf1", "document", mime_type="application/pdf"),
            "pdf",
        ),
        (
            "google_drive",
            _record(
                "gdrive-file:sheet1",
                "document",
                mime_type="application/vnd.google-apps.spreadsheet",
            ),
            "spreadsheet",
        ),
        ("notion", _record("notion-page:1", "page"), "document"),
        ("teams", _record("teams-message:1", "channel_message"), "text"),
        ("slack", _record("slack-message:1", "message"), "slack"),
        ("remote_mcp", _record("remote:1", "incident"), "incident"),
        ("rest_pull", _record("rest:1", "record"), "document"),
    ],
)
def test_connector_records_are_ingested_with_a_supported_source_type(
    graph, provider, record, expected
):
    ingestion = IngestionService(graph, HCAGAdapter(graph), AuditService())
    project_id = ingestion.create_project("Platform")
    applier = MemoryWorksSyncApplier(ingestion, graph)

    result = applier(
        record, {"project_id": project_id, "provider": provider, "user_id": "user_test"}
    )

    assert result["status"] == "upserted"
    stored = row(
        "SELECT source_type FROM knowledge_items WHERE project_id=? AND source_id=?",
        (project_id, result["source_id"]),
    )
    assert stored["source_type"] == expected
