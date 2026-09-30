from __future__ import annotations

from typing import Any

from app.core.database import connect, row
from app.graph.base import GraphStore
from app.ingestion.safety import sanitize_for_index
from app.ingestion.service import IngestionService
from app.memory import CompanyMemoryService

from .base import SyncOperation, SyncRecord


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
            str(context["provider"]),
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
