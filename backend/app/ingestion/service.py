from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from app.audit import AuditService
from app.core.database import connect, new_id, row, utcnow
from app.governance import ScopeService
from app.graph.base import GraphStore
from app.hcag_adapter import HCAGAdapter
from app.hcag_adapter.memory_dynamics import reinforce_memory
from app.memory.brain import CompanyBrainService
from app.memory.company import CompanyMemoryService

from .extractors import chunk_document, extract_services, extract_signals
from .safety import sanitize_for_index, sanitize_metadata

SOURCE_VERTEX = {
    "repo_file": "File",
    "github_issue": "Issue",
    "pull_request": "PullRequest",
    "slack": "SlackMessage",
    "slack_export": "SlackMessage",
}
REPOSITORY_SOURCE_TYPES = {
    "github_commit",
    "github_issue",
    "pull_request",
    "repo_file",
    "repository_metadata",
}
SLACK_SOURCE_TYPES = {"slack", "slack_export"}
DOCUMENT_SOURCE_TYPES = {
    "doc",
    "document",
    "incident",
    "log",
    "report",
    "text",
    "upload",
    "web_page",
    "pdf",
    "spreadsheet",
    "presentation",
    "email",
    "email_export",
}
SUPPORTED_SOURCE_TYPES = REPOSITORY_SOURCE_TYPES | SLACK_SOURCE_TYPES | DOCUMENT_SOURCE_TYPES


class IngestionService:
    def __init__(self, graph: GraphStore, hcag: HCAGAdapter, audit: AuditService | None = None):
        self.graph = graph
        self.hcag = hcag
        self.audit = audit or AuditService()
        self.memory = CompanyMemoryService(graph)
        self.brain = CompanyBrainService(graph)
        self.scopes = ScopeService()

    def create_project(self, name: str, repository: str = "", project_id: str | None = None) -> str:
        existing = (
            row("SELECT id,created_at FROM projects WHERE repository=?", (repository,))
            if repository and not project_id
            else None
        )
        project_id = project_id or (existing["id"] if existing else new_id("prj"))
        now = utcnow()
        created_at = existing["created_at"] if existing else now
        with connect() as conn:
            conn.execute(
                """INSERT INTO projects(id,name,repository,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                repository=excluded.repository,status=excluded.status,updated_at=excluded.updated_at""",
                (project_id, name, repository, "ready", created_at, now),
            )
        self.graph.upsert_project(
            {
                "id": project_id,
                "name": name,
                "repository": repository,
                "created_at": created_at,
                "updated_at": now,
            }
        )
        if repository:
            repository_id = f"repo:{project_id}"
            self.graph.upsert_repository(
                {
                    "id": repository_id,
                    "project_id": project_id,
                    "url": repository,
                    "name": Path(repository).name,
                }
            )
            self.graph.link("PROJECT_HAS_REPO", "Project", project_id, "Repository", repository_id)
        event = "project.reindexed" if existing else "project.created"
        summary = f"Re-indexed project {name}" if existing else f"Created project {name}"
        self.audit.record(event, summary, project_id)
        return project_id

    def ingest_item(
        self,
        project_id: str,
        source_type: str,
        title: str,
        content: str,
        source_url: str = "",
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = sanitize_metadata(dict(metadata or {}))
        source_type = source_type.casefold().strip()
        self._validate_source_provenance(
            project_id, source_type, source_url, source_id or "", metadata
        )
        metadata["source_family"] = (
            "repository"
            if source_type in REPOSITORY_SOURCE_TYPES
            else "slack"
            if source_type in SLACK_SOURCE_TYPES
            else "document"
        )
        content, redaction_count = sanitize_for_index(content, source_url or title)
        metadata["secret_redactions"] = redaction_count
        item_id = new_id("item")
        source_id = self.brain.stable_source_id(project_id, source_type, title, source_id)
        foreign_source = row(
            "SELECT project_id FROM knowledge_items WHERE source_id=? AND project_id<>? LIMIT 1",
            (source_id, project_id),
        )
        if foreign_source:
            source_id = f"{project_id}:{source_id}"
        before_memories = self.brain.current_source_memories(project_id, source_id)
        source_revision, revision_created = self.brain.record_source_revision(
            project_id, source_id, source_type, title, content, metadata
        )
        team_ids = [str(value) for value in metadata.get("team_ids", []) if value]
        self.scopes.bind_source(project_id, source_id, team_ids)
        services = extract_services(content, source_url)
        signals = extract_signals(content)
        route = self.hcag.ingest_knowledge_item(
            {"project_id": project_id, "source_title": title, "content": content}
        )
        metadata.update({"services": services, "signals": signals, "hcag": route})
        now = utcnow()
        with connect() as conn:
            conn.execute(
                "INSERT INTO knowledge_items VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    project_id,
                    source_type,
                    source_id,
                    title,
                    source_url,
                    content,
                    json.dumps(metadata),
                    now,
                ),
            )
        # Count the item only after it is durable so context-window totals remain
        # correct across process restarts and repeated repository ingestion.
        with contextlib.suppress(Exception):
            self.hcag.window_store.record_item(project_id, route["domain"], route["subdomain"])
        vertex_type = SOURCE_VERTEX.get(source_type, "EvidenceSource")
        source_payload = {
            "id": source_id,
            "project_id": project_id,
            "title": title,
            "url": source_url,
            "source_type": source_type,
            "path": metadata.get("path", ""),
            "repository": metadata.get("repository", ""),
            "owner": metadata.get("owner", ""),
            "commit_sha": metadata.get("commit_sha", ""),
            "source_updated_at": metadata.get("source_updated_at", ""),
        }
        if vertex_type == "File":
            self.graph.upsert_file(source_payload)
        elif vertex_type == "Issue":
            self.graph.upsert_issue(source_payload)
        elif vertex_type == "PullRequest":
            self.graph.upsert_pull_request(source_payload)
        elif vertex_type == "SlackMessage":
            self.graph.upsert_slack_message(source_payload)
        else:
            self.graph.upsert_evidence_source(source_payload)
        item_payload = {
            "id": item_id,
            "project_id": project_id,
            "source_id": source_id,
            "source_type": source_type,
            "source_title": title,
            "source_url": source_url,
            "content": content,
            "metadata_json": json.dumps(metadata),
            "created_at": now,
        }
        self.graph.upsert_knowledge_item(item_payload)
        self.graph.link(
            "KNOWLEDGE_ITEM_DERIVED_FROM",
            "KnowledgeItem",
            item_id,
            vertex_type,
            source_id,
        )
        chunks = chunk_document(content)
        chunk_ids: list[str] = []
        for index, document_chunk in enumerate(chunks):
            text = document_chunk.text
            chunk_id = new_id("chunk")
            chunk_ids.append(chunk_id)
            chunk_services = [
                service
                for service in services
                if service.replace("_", " ") in text.lower().replace("_", " ")
            ] or services
            chunk_signals = extract_signals(text)
            contextual_text = "\n".join(
                value
                for value in (
                    f"Source: {title}",
                    f"Section: {document_chunk.section}" if document_chunk.section else "",
                    text,
                )
                if value
            )
            retrieval_features = self.hcag.index_chunk(contextual_text)
            memory = reinforce_memory(
                project_id,
                source_id,
                retrieval_features["content_hash"],
                str(metadata.get("source_updated_at") or now),
            )
            chunk_payload = {
                "id": chunk_id,
                "project_id": project_id,
                "item_id": item_id,
                "source_id": source_id,
                "text": text,
                "source_type": source_type,
                "source_title": title,
                "source_url": self._chunk_url(
                    source_url, document_chunk.line_start, document_chunk.line_end
                ),
                "service_names": chunk_services,
                "context_window": route["context_window"],
                "domain": route["domain"],
                "subdomain": route["subdomain"],
                "content_hash": retrieval_features["content_hash"],
                "search_terms": retrieval_features["search_terms"],
                "embedding": retrieval_features.get("embedding", []),
                "embedding_model": retrieval_features.get("embedding_model", ""),
                "embedding_version": retrieval_features.get("embedding_version", 0),
                "metadata_json": json.dumps(
                    {
                        "chunk_index": index,
                        "line_start": document_chunk.line_start,
                        "line_end": document_chunk.line_end,
                        "token_count": document_chunk.token_count,
                        "section": document_chunk.section,
                        "item_id": item_id,
                        "source_id": source_id,
                        "repository": metadata.get("repository", ""),
                        "path": metadata.get("path", ""),
                        "owner": metadata.get("owner", ""),
                        "signals": chunk_signals,
                        "hcag": route,
                        "source_version": metadata.get("source_version", ""),
                        "commit_sha": metadata.get("commit_sha", ""),
                        "source_updated_at": metadata.get("source_updated_at", ""),
                        "channel_id": metadata.get("channel_id", ""),
                        "channel_name": metadata.get("channel_name", ""),
                        "user": metadata.get("user", ""),
                        "timestamp": metadata.get("timestamp", ""),
                        "memory": memory,
                    }
                ),
            }
            self.graph.upsert_chunk(chunk_payload)
            self.graph.link(
                "CHUNK_DERIVED_FROM", "KnowledgeChunk", chunk_id, "KnowledgeItem", item_id
            )
            self.hcag.window_store.record_chunk(
                project_id, route["domain"], route["subdomain"], chunk_id
            )
            if source_type == "repo_file":
                self.graph.link_file_to_chunk(source_id, chunk_id)
            for service in chunk_services:
                service_id = f"{project_id}:{service}"
                self.graph.upsert_service(
                    {"id": service_id, "project_id": project_id, "name": service}
                )
                self.graph.link("PROJECT_HAS_SERVICE", "Project", project_id, "Service", service_id)
                self.graph.link_chunk_to_service(chunk_id, service_id)
        superseded_by = self.memory.superseded_by(project_id, source_id, title)
        memory_units = self.memory.extract_memory_units(
            project_id,
            item_id,
            source_id,
            source_type,
            title,
            content,
            metadata,
            chunk_ids,
            historical=bool(superseded_by),
        )
        self.memory.apply_supersession(
            project_id, source_id, content, memory_units, superseded_by=superseded_by
        )
        change_set = (
            self.brain.finalize_change_set(
                project_id,
                source_id,
                source_revision["id"],
                before_memories,
                memory_units,
                str(metadata.get("actor") or "ingestion"),
            )
            if revision_created or metadata.get("force_memory_reconcile")
            else None
        )
        artifact = None
        artifact_type = str(metadata.get("artifact_type") or "")
        artifact_name = str(metadata.get("artifact_name") or title)
        if artifact_type or source_type == "report":
            artifact = self.brain.save_artifact(
                project_id,
                artifact_name,
                artifact_type or "report",
                content,
                [source_id],
                [item["id"] for item in memory_units],
            )
        self.audit.record(
            "knowledge.ingested",
            f"Ingested {title}",
            project_id,
            payload={
                "item_id": item_id,
                "source_type": source_type,
                "chunks": len(chunks),
                "services": services,
                "memory_units": len(memory_units),
                "source_revision_id": source_revision["id"],
                "change_set_id": (change_set or {}).get("id", ""),
            },
        )
        return {
            "item_id": item_id,
            "chunks_created": len(chunks),
            "services": services,
            "signals": signals,
            "memory_units_created": len(memory_units),
            "memory_unit_ids": [item["id"] for item in memory_units],
            "source_id": source_id,
            "source_revision": source_revision,
            "change_set": change_set,
            "unchanged": not revision_created,
            "artifact": artifact,
        }

    @staticmethod
    def _repository_slug(value: str) -> str:
        normalized = value.strip().removesuffix(".git").rstrip("/")
        match = re.search(r"github\.com[/:]([^/]+/[^/#?]+)", normalized, re.I)
        if match:
            return match.group(1).casefold()
        return normalized.casefold() if re.fullmatch(r"[^/\s]+/[^/\s]+", normalized) else ""

    def _validate_source_provenance(
        self,
        project_id: str,
        source_type: str,
        source_url: str,
        source_id: str,
        metadata: dict[str, Any],
    ) -> None:
        project = row("SELECT repository FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("Project not found")
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise ValueError(
                "Unsupported memory source. OrgMemory accepts repository evidence, "
                "uploaded documents, and Slack messages."
            )
        if source_id.startswith("file:prj_") and not source_id.startswith(f"file:{project_id}:"):
            raise ValueError("Repository file belongs to a different OrgMemory project")
        if source_type not in REPOSITORY_SOURCE_TYPES:
            return
        project_slug = self._repository_slug(str(project.get("repository") or ""))
        claimed_slug = self._repository_slug(str(metadata.get("repository") or source_url or ""))
        if project_slug and claimed_slug and project_slug != claimed_slug:
            raise ValueError(
                f"Source repository {claimed_slug} does not match project repository {project_slug}"
            )

    @staticmethod
    def _chunk_url(source_url: str, line_start: int, line_end: int) -> str:
        if "github.com/" in source_url and "/blob/" in source_url:
            return f"{source_url.split('#', 1)[0]}#L{line_start}-L{line_end}"
        return source_url
