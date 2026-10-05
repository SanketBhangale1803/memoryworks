from __future__ import annotations

from typing import Any

from app.core.database import connect, row
from app.graph.base import GraphStore
from app.ingestion.safety import sanitize_for_index
from app.ingestion.service import SUPPORTED_SOURCE_TYPES, IngestionService
from app.memory import CompanyMemoryService

from .base import SyncOperation, SyncRecord

# Connector records name what they are (a Drive "document", a GitHub "commit"),
# not what ingestion accepts. Map them onto ingestion's source types; passing
# the provider name instead rejected every connector but Slack.
_RECORD_SOURCE_TYPES = {
    ("github", "commit"): "github_commit",
    ("github", "repository"): "repository_metadata",
    ("slack", "message"): "slack",
    ("teams", "channel_message"): "text",
    ("notion", "page"): "document",
    ("notion", "database_row"): "document",
}
_DRIVE_MIME_SOURCE_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.google-apps.spreadsheet": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "application/vnd.google-apps.presentation": "presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentation",
}


def record_source_type(provider: str, record: SyncRecord) -> str:
    """The ingestion source type for a connector record; unknown kinds are documents."""
    if provider == "google_drive":
        return _DRIVE_MIME_SOURCE_TYPES.get(str(record.metadata.get("mime_type") or ""), "document")
    mapped = _RECORD_SOURCE_TYPES.get((provider, record.resource_type))
    if mapped:
        return mapped
    if record.resource_type in SUPPORTED_SOURCE_TYPES:
        return record.resource_type
    return "document"


class MemoryWorksSyncApplier:
    """Maps normalized connector records into source revisions and current memory."""

    def __init__(self, ingestion: IngestionService, graph: GraphStore):
        self.ingestion = ingestion
        self.graph = graph
        self.memory = CompanyMemoryService(graph)

    def __call__(self, record: SyncRecord, context: dict[str, Any]) -> dict[str, Any] | None:
        project_id = str(context.get("project_id") or record.metadata.get("project_id") or "")
        if not project_id:
            # A connector may be connected before the user assigns the resource
            # to a project. The durable delivery remains recorded and can seed a
            # later project sync, but it must not leak into an arbitrary project.
            return {"status": "unassigned"}
        existing = row(
            "SELECT * FROM knowledge_items WHERE project_id=? AND source_id=? LIMIT 1",
            (project_id, record.id),
        )
        if record.operation == SyncOperation.DELETE:
            if not existing:
                return {"status": "already_deleted"}
            self.memory.retire_source_memories(project_id, record.id)
            self.graph.delete_source_knowledge(project_id, record.id, str(existing["source_type"]))
            with connect() as conn:
                conn.execute("DELETE FROM knowledge_items WHERE id=?", (existing["id"],))
            return {"status": "deleted", "source_id": record.id}

        sanitized, redactions = sanitize_for_index(
            record.content, record.source_url or record.title
        )
        if existing and existing["content"] == sanitized:
            return {"status": "unchanged", "source_id": record.id}
        if existing:
            self.memory.retire_source_memories(project_id, record.id)
            self.graph.delete_source_knowledge(project_id, record.id, str(existing["source_type"]))
            with connect() as conn:
                conn.execute("DELETE FROM knowledge_items WHERE id=?", (existing["id"],))
        result = self.ingestion.ingest_item(
            project_id,
            record_source_type(str(context["provider"]), record),
            record.title or record.id,
            sanitized,
            record.source_url,
            record.id,
            {
                **record.metadata,
                "connector_provider": context["provider"],
                "connector_resource_id": context.get("resource_id", ""),
                "source_version": record.version,
                "source_updated_at": record.updated_at,
                "secret_redactions": redactions,
                "trust": record.trust,
                "actor": context.get("user_id", "connector_sync"),
            },
        )
        return {"status": "upserted", "source_id": record.id, **result}
