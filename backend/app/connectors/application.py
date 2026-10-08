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
        # Signal records bypass memory and ledger ingestion entirely. Their
        # storage transaction owns project-aware idempotency.
        if record.resource_type == "signal":
            return self._apply_signal(record, context)
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

    def _apply_signal(self, record: SyncRecord, context: dict[str, Any]) -> dict[str, Any]:
        from app.core.database import row as _row
        from app.orgops.signals import SignalService

        provider = str(context.get("provider") or "")
        workspace_id = str(context.get("workspace_id") or "")
        project_id = str(context.get("project_id") or "")
        # Metadata must never supply project/team authorization.
        meta_project = record.metadata.get("project_id")
        meta_workspace = record.metadata.get("workspace_id")
        if meta_project and str(meta_project) != project_id:
            raise ValueError("signal project mismatch")
        if meta_workspace and str(meta_workspace) != workspace_id:
            raise ValueError("signal workspace mismatch")
        if not workspace_id:
            return {"status": "unassigned"}
        if not project_id:
            return {"status": "unassigned"}
        if record.operation != SyncOperation.UPSERT:
            raise ValueError("only UPSERT signal records are valid")
        meta = dict(record.metadata)
        if provider == "github":
            repo_slug = _normalize_signal_slug(str(meta.get("repository") or ""))
            resource_slug = _normalize_signal_slug(str(context.get("resource_id") or ""))
            if not repo_slug or repo_slug != resource_slug:
                raise ValueError("signal repository mismatch")
            try:
                repo_id = int(str(meta.get("repo_id") or meta.get("repository_id") or ""))
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid github repository id") from exc
            pr_number = meta.get("pr_number")
            pr_int: int | None = None
            if pr_number not in (None, ""):
                try:
                    pr_int = int(str(pr_number))
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid github PR number") from exc
            proj = _row("SELECT repository FROM projects WHERE id=?", (project_id,))
            if not proj:
                raise ValueError("unknown project")
            project_slug = _normalize_signal_slug(str(proj.get("repository") or ""))
            if not project_slug or project_slug != repo_slug:
                raise ValueError("signal project repository mismatch")
            binding = _row(
                "SELECT 1 AS ok FROM workspace_projects WHERE workspace_id=? AND project_id=?",
                (workspace_id, project_id),
            )
            if not binding:
                raise ValueError("signal project not in workspace")
            canonical = repo_slug.casefold()
            source_ids = [
                f"repository-metadata:{canonical}",
                f"github-repository:{repo_id}",
            ]
            if pr_int is not None:
                source_ids.append(f"pull:{canonical}:{pr_int}")
        else:
            # Only the GitHub adapter constructs trusted signal source ids today.
            raise ValueError("signal records are only accepted from the github connector")

        service = SignalService(self.memory)
        lane = meta.get("lane") or {}
        details = meta.get("details") or {}
        if not isinstance(lane, dict) or not isinstance(details, dict):
            raise ValueError("invalid signal lane/details")
        observed = service.observe(
            workspace_id=workspace_id,
            project_id=project_id,
            source=str(meta.get("source") or provider or "github"),
            kind=str(meta.get("kind") or ""),
            subject=str(meta.get("subject") or ""),
            lane={str(k): str(v) for k, v in lane.items()},
            severity=str(meta.get("severity") or "error"),
            state=str(meta.get("state") or ""),
            observed_at=str(meta.get("observed_at") or ""),
            generation_at=str(meta.get("generation_at") or ""),
            generation_id=int(meta.get("generation_id") or 0),
            attempt=int(meta.get("attempt") or 1),
            observation_key=str(meta.get("observation_key") or ""),
            source_ids=source_ids,
            evidence_url=str(meta.get("evidence_url") or record.source_url or ""),
            details={str(k): v for k, v in details.items()},
            ttl_seconds=int(meta.get("ttl_seconds") or 86400),
        )
        return {**observed, "status": "signal_recorded"}


def _normalize_signal_slug(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "github.com" in text:
        from urllib.parse import urlparse

        path = urlparse(text).path.strip("/").removesuffix(".git")
        return path.casefold()
    return text.strip("/").casefold()
