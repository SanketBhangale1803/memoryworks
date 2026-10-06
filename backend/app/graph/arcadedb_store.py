from __future__ import annotations

import json
import re
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

from .arcade_client import ArcadeClient
from .base import GraphEvidence, GraphStore
from .graph_ranker import rank_records
from .migrations import schema_commands
from .schema import EDGE_TYPES, PROJECT_SCOPED_VERTEX_TYPES, VERTEX_TYPES
from .traversal import evidence_from_hits, find_traversal_hits

__all__ = ["ArcadeDBGraphStore", "rank_records"]


# A parameter reference in a statement, renamed per statement when several are
# sent as one script so their parameters cannot collide.
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


class _WriteBatch:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.edge_keys: set[str] = set()


class ArcadeDBGraphStore(GraphStore):
    # Statements per transaction when writes are batched: large enough that a
    # document's chunks and edges commit in a few round trips, small enough that
    # one request stays a few megabytes even with embeddings in every chunk.
    BATCH_SIZE = 200

    def __init__(self, client: ArcadeClient | None = None):
        self.client = client or ArcadeClient()
        self._local = threading.local()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Send the writes made inside the block as a few transactions, not one each.

        Every upsert and edge used to be its own HTTP request and its own commit;
        a 300-chunk document made over a thousand. Inside a batch they are
        buffered and committed ``BATCH_SIZE`` at a time as one SQL script. Reads
        are not buffered, so code inside a batch must not read what it just
        wrote. Whatever was buffered is still sent if the block raises, as each
        write would have been before.
        """
        if getattr(self._local, "batch", None) is not None:
            yield  # nested: the outer batch sends everything
            return
        self._local.batch = _WriteBatch()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            pending, self._local.batch = self._local.batch, None
            if failed:
                with suppress(Exception):
                    self._flush(pending)
            else:
                self._flush(pending)

    def _write(self, command: str, params: dict[str, Any], language: str = "sql") -> None:
        pending = getattr(self._local, "batch", None)
        if pending is None:
            self.client.command(command, params, language=language)
            return
        pending.statements.append((command, params))
        if len(pending.statements) >= self.BATCH_SIZE:
            self._flush(pending)

    def _flush(self, pending: _WriteBatch) -> None:
        if not pending.statements:
            return
        lines = ["BEGIN;"]
        merged: dict[str, Any] = {}
        for index, (command, params) in enumerate(pending.statements):
            prefix = f"s{index}_"
            lines.append(_PARAM.sub(lambda m, p=prefix: f":{p}{m.group(1)}", command).strip())
            if not lines[-1].endswith(";"):
                lines[-1] += ";"
            merged.update({f"{prefix}{key}": value for key, value in params.items()})
        lines.append("COMMIT RETRY 5;")
        pending.statements = []
        self.client.command("\n".join(lines), merged, language="sqlscript")

    def health(self) -> dict[str, Any]:
        status = self.client.health()
        if status.get("connected"):
            try:
                status["node_counts"] = self.node_counts()
                status["edge_counts"] = self.edge_counts()
            except Exception as exc:
                status["count_error"] = str(exc)
        return status

    def initialize(self) -> None:
        self.client.ensure_database()
        for command in schema_commands():
            try:
                self.client.command(command)
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise

    def _upsert(self, vertex_type: str, payload: dict[str, Any]) -> None:
        safe = {
            key: json.dumps(value) if isinstance(value, dict | list) else value
            for key, value in payload.items()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        }
        if "id" not in safe:
            raise ValueError(f"{vertex_type} payload missing id")
        assignments = ", ".join(f"{key}=:{key}" for key in safe if key != "id")
        if assignments:
            self._write(f"UPDATE {vertex_type} SET {assignments} UPSERT WHERE id=:id", safe)
        else:
            self._write(f"UPDATE {vertex_type} SET id=:id UPSERT WHERE id=:id", safe)

    def upsert_node(self, node_type: str, payload: dict[str, Any]) -> None:
        if node_type not in VERTEX_TYPES:
            raise ValueError(f"Unsupported graph node type: {node_type}")
        self._upsert(node_type, payload)

    def upsert_project(self, project: dict[str, Any]) -> None:
        self._upsert("Project", project)

    def upsert_repository(self, repository: dict[str, Any]) -> None:
        self._upsert("Repository", repository)

    def upsert_service(self, service: dict[str, Any]) -> None:
        self._upsert("Service", service)

    def upsert_file(self, file: dict[str, Any]) -> None:
        self._upsert("File", file)

    def upsert_issue(self, issue: dict[str, Any]) -> None:
        self._upsert("Issue", issue)

    def upsert_pull_request(self, pull_request: dict[str, Any]) -> None:
        self._upsert("PullRequest", pull_request)

    def upsert_slack_channel(self, channel: dict[str, Any]) -> None:
        self._upsert("SlackChannel", channel)

    def upsert_slack_message(self, message: dict[str, Any]) -> None:
        self._upsert("SlackMessage", message)

    def upsert_evidence_source(self, source: dict[str, Any]) -> None:
        self._upsert("EvidenceSource", source)

    def upsert_runbook_step(self, step: dict[str, Any]) -> None:
        self._upsert("RunbookStep", step)

    def upsert_agent_action(self, action: dict[str, Any]) -> None:
        self._upsert("AgentAction", action)

    def upsert_operational_assertion(self, assertion: dict[str, Any]) -> None:
        self._upsert("OperationalAssertion", assertion)

    def upsert_change_impact(self, impact: dict[str, Any]) -> None:
        self._upsert("ChangeImpact", impact)

    def clear_repository_knowledge(self, project_id: str) -> None:
        for source_type in (
            "repo_file",
            "github_issue",
            "pull_request",
            "repository_metadata",
            "github_commit",
        ):
            self.client.command(
                "DELETE FROM KnowledgeChunk WHERE project_id=:project_id AND source_type=:source_type",
                {"project_id": project_id, "source_type": source_type},
            )
            self.client.command(
                "DELETE FROM KnowledgeItem WHERE project_id=:project_id AND source_type=:source_type",
                {"project_id": project_id, "source_type": source_type},
            )
        for vertex_type in ("File", "Issue", "PullRequest", "Commit"):
            self.client.command(
                f"DELETE FROM {vertex_type} WHERE project_id=:project_id",
                {"project_id": project_id},
            )
        self.client.command(
            "DELETE FROM EvidenceSource WHERE project_id=:project_id "
            "AND source_type='repository_metadata'",
            {"project_id": project_id},
        )
        for vertex_type in (
            "Directory",
            "Package",
            "Service",
            "EnvironmentVariable",
            "ConfigKey",
            "DockerService",
            "Workflow",
            "Job",
            "Step",
            "Script",
            "Command",
            "Endpoint",
            "Function",
            "Class",
            "Module",
            "Import",
            "Label",
            "User",
            "Comment",
            "LogLine",
            "ErrorPattern",
            "ContextWindow",
        ):
            self.client.command(
                f"DELETE FROM {vertex_type} WHERE project_id=:project_id",
                {"project_id": project_id},
            )

    def delete_source_knowledge(
        self, project_id: str, source_id: str, source_type: str = ""
    ) -> int:
        items = self.client.query(
            "SELECT id FROM KnowledgeItem WHERE project_id=:project_id AND source_id=:source_id",
            {"project_id": project_id, "source_id": source_id},
        )
        for item in items:
            self.client.command(
                "DELETE FROM KnowledgeChunk WHERE project_id=:project_id AND item_id=:item_id",
                {"project_id": project_id, "item_id": item["id"]},
            )
        self.client.command(
            "DELETE FROM KnowledgeItem WHERE project_id=:project_id AND source_id=:source_id",
            {"project_id": project_id, "source_id": source_id},
        )
        vertex_type = {
            "repo_file": "File",
            "github_issue": "Issue",
            "pull_request": "PullRequest",
        }.get(source_type, "EvidenceSource")
        self.client.command(
            f"DELETE FROM {vertex_type} WHERE project_id=:project_id AND id=:source_id",
            {"project_id": project_id, "source_id": source_id},
        )
        return len(items)

    def delete_project_nodes(self, project_id: str, node_types: list[str]) -> int:
        deleted = 0
        for node_type in node_types:
            if node_type not in VERTEX_TYPES or node_type == "Project":
                continue
            records = self.client.query(
                f"SELECT count(*) AS count FROM {node_type} WHERE project_id=:project_id",
                {"project_id": project_id},
            )
            deleted += int((records[0] if records else {}).get("count", 0) or 0)
            self.client.command(
                f"DELETE FROM {node_type} WHERE project_id=:project_id",
                {"project_id": project_id},
            )
        return deleted

    def delete_project(self, project_id: str) -> int:
        deleted = self.delete_project_nodes(
            project_id, [node_type for node_type in VERTEX_TYPES if node_type != "Project"]
        )
        records = self.client.query(
            "SELECT count(*) AS count FROM Project WHERE id=:project_id",
            {"project_id": project_id},
        )
        deleted += int((records[0] if records else {}).get("count", 0) or 0)
        self.client.command(
            "DELETE FROM Project WHERE id=:project_id",
            {"project_id": project_id},
        )
        return deleted

    def upsert_knowledge_item(self, item: dict[str, Any]) -> None:
        self._upsert("KnowledgeItem", item)

    def upsert_chunk(self, chunk: dict[str, Any]) -> None:
        self._upsert("KnowledgeChunk", chunk)

    def upsert_runbook(self, runbook: dict[str, Any]) -> None:
        self._upsert("Runbook", runbook)

    def link(self, edge_type: str, from_type: str, from_id: str, to_type: str, to_id: str) -> None:
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"Unsupported graph edge type: {edge_type}")
        edge_key = f"{edge_type}:{from_id}:{to_id}"
        pending = getattr(self._local, "batch", None)
        if pending is not None:
            # A document links each chunk to the same window and services;
            # within one batch, each edge is written once.
            if edge_key in pending.edge_keys:
                return
            pending.edge_keys.add(edge_key)
        script = (
            f"DELETE FROM {edge_type} WHERE edge_key=:edge_key; "
            f"CREATE EDGE {edge_type} FROM (SELECT FROM {from_type} WHERE id=:from_id) "
            f"TO (SELECT FROM {to_type} WHERE id=:to_id) SET edge_key=:edge_key;"
        )
        self._write(
            script,
            {"edge_key": edge_key, "from_id": from_id, "to_id": to_id},
            language="sqlscript",
        )

    def retrieve_context(
        self, project_id: str, query: str, service_name: str | None = None, limit: int = 12
    ) -> list[GraphEvidence]:
        records = self.client.query(
            "SELECT id,text,source_type,source_title,source_url,service_names,metadata_json,"
            "search_terms,embedding,embedding_model,embedding_version,content_hash,"
            "context_window,domain,subdomain "
            "FROM KnowledgeChunk WHERE project_id=:project_id",
            {"project_id": project_id},
        )
        return rank_records(records, query, service_name, limit)

    def traverse_context(
        self,
        project_id: str,
        query: str,
        *,
        max_hops: int = 3,
        limit: int = 12,
    ) -> list[GraphEvidence]:
        nodes = self.list_nodes(project_id, limit=100_000)
        for vertex_type in ("Language", "Dependency"):
            records = self.client.query(f"SELECT FROM {vertex_type} LIMIT 10000")
            nodes.extend({"node_type": vertex_type, **dict(record)} for record in records)
        edges = self.list_edges(project_id, limit=100_000)
        hits = find_traversal_hits(
            nodes,
            edges,
            query,
            max_hops=max_hops,
            limit=limit,
        )
        chunks = {
            str(node.get("id") or ""): node
            for node in nodes
            if node.get("node_type") == "KnowledgeChunk"
        }
        return evidence_from_hits(hits, chunks)

    def get_retrieval_trace(self, project_id: str, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        try:
            result = self.client.query(
                "MATCH (c:KnowledgeChunk)-[r]->(n) WHERE c.project_id=$project_id AND c.id IN $chunk_ids RETURN c.id AS chunk_id, type(r) AS relationship, n.id AS target_id",
                {"project_id": project_id, "chunk_ids": chunk_ids},
                language="cypher",
            )
            return result
        except Exception:
            return [
                {"chunk_id": chunk_id, "relationship": "PROJECT_MEMORY", "target_id": project_id}
                for chunk_id in chunk_ids
            ]

    def node_counts(self, project_id: str | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for vertex_type in VERTEX_TYPES:
            if project_id and vertex_type in PROJECT_SCOPED_VERTEX_TYPES:
                records = self.client.query(
                    f"SELECT count(*) AS count FROM {vertex_type} WHERE project_id=:project_id",
                    {"project_id": project_id},
                )
            elif project_id and vertex_type == "Project":
                records = self.client.query(
                    "SELECT count(*) AS count FROM Project WHERE id=:project_id",
                    {"project_id": project_id},
                )
            elif project_id:
                records = []
            else:
                records = self.client.query(f"SELECT count(*) AS count FROM {vertex_type}")
            value = int((records[0] if records else {}).get("count", 0) or 0)
            if value:
                counts[vertex_type] = value
        return counts

    def edge_counts(self, project_id: str | None = None) -> dict[str, int]:
        if project_id:
            edges = self.list_edges(project_id, limit=5000)
            counts = Counter(edge["relationship"] for edge in edges)
            return dict(sorted(counts.items()))
        counts: dict[str, int] = {}
        for edge_type in EDGE_TYPES:
            records = self.client.query(f"SELECT count(*) AS count FROM {edge_type}")
            value = int((records[0] if records else {}).get("count", 0) or 0)
            if value:
                counts[edge_type] = value
        return counts

    def graph_summary(self, project_id: str) -> dict[str, Any]:
        nodes = self.node_counts(project_id)
        edges = self.edge_counts(project_id)
        services = self.client.query(
            "SELECT name,id FROM Service WHERE project_id=:project_id ORDER BY name LIMIT 100",
            {"project_id": project_id},
        )
        files = self.client.query(
            "SELECT path,filename,language,size,summary FROM File WHERE project_id=:project_id ORDER BY path LIMIT 200",
            {"project_id": project_id},
        )
        return {
            "project_id": project_id,
            "node_counts": nodes,
            "edge_counts": edges,
            "total_nodes": sum(nodes.values()),
            "total_edges": sum(edges.values()),
            "services": services,
            "files": files,
        }

    def list_nodes(
        self, project_id: str, node_type: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        types = [node_type] if node_type else PROJECT_SCOPED_VERTEX_TYPES
        output: list[dict[str, Any]] = []
        for vertex_type in types:
            if vertex_type not in VERTEX_TYPES:
                continue
            if vertex_type == "Project":
                records = self.client.query(
                    "SELECT FROM Project WHERE id=:project_id LIMIT :limit",
                    {"project_id": project_id, "limit": limit},
                )
            else:
                records = self.client.query(
                    f"SELECT FROM {vertex_type} WHERE project_id=:project_id LIMIT :limit",
                    {"project_id": project_id, "limit": limit},
                )
            for record in records:
                record = dict(record)
                record["node_type"] = vertex_type
                output.append(record)
                if len(output) >= limit:
                    return output
        return output

    def list_edges(
        self, project_id: str, edge_type: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Edges touching the project's vertices (and the project itself).

        Walked out from the project's vertices, which are indexed by project_id.
        This used to MATCH every edge of every type and filter on either end,
        which no index can serve: ~95 full scans per call, and answers paid for
        every other workspace's graph.
        """
        if edge_type and edge_type not in EDGE_TYPES:
            return []
        expand = f"bothE('{edge_type}')" if edge_type else "bothE()"
        sources = [("Project", "id")] + [
            (vertex_type, "project_id")
            for vertex_type in PROJECT_SCOPED_VERTEX_TYPES
            if vertex_type != "Project"
        ]
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for vertex_type, key in sources:
            records = self.client.query(
                "SELECT @rid AS rid, @type AS relationship, @out.id AS from_id, "
                f"@in.id AS to_id FROM (SELECT expand({expand}) FROM {vertex_type} "
                f"WHERE {key}=:project_id) LIMIT :limit",
                # An edge between two project vertices is reached from both ends.
                {"project_id": project_id, "limit": 2 * (limit - len(output))},
            )
            for record in records:
                rid = str(record.get("rid") or "")
                if rid in seen:
                    continue
                seen.add(rid)
                output.append(
                    {
                        "relationship": record.get("relationship"),
                        "from_id": record.get("from_id"),
                        "to_id": record.get("to_id"),
                    }
                )
                if len(output) >= limit:
                    return output
        return output

    def service_graph(self, project_id: str, service_name: str) -> dict[str, Any]:
        service_id = f"{project_id}:{service_name}"
        nodes = [
            node
            for node in self.list_nodes(project_id, "Service", 200)
            if node.get("id") == service_id or node.get("name") == service_name
        ]
        edges = [
            edge
            for edge in self.list_edges(project_id, limit=1000)
            if service_id in {edge.get("from_id"), edge.get("to_id")}
        ]
        evidence = self.retrieve_context(project_id, service_name, service_name, limit=12)
        return {
            "project_id": project_id,
            "service_name": service_name,
            "nodes": nodes,
            "edges": edges,
            "evidence": [
                {
                    "chunk_id": item.chunk_id,
                    "source_type": item.source_type,
                    "source_title": item.source_title,
                    "source_url": item.source_url,
                    "snippet": item.text[:360],
                    "confidence": item.metadata.get("retrieval_confidence", 0),
                }
                for item in evidence
            ],
        }

    def file_graph(self, project_id: str, path: str) -> dict[str, Any]:
        file_id = f"file:{project_id}:{path}"
        nodes = [
            node
            for node in self.list_nodes(project_id, "File", 500)
            if node.get("id") == file_id or node.get("path") == path
        ]
        edges = [
            edge
            for edge in self.list_edges(project_id, limit=1000)
            if file_id in {edge.get("from_id"), edge.get("to_id")}
        ]
        return {"project_id": project_id, "path": path, "nodes": nodes, "edges": edges}

    def reset(self) -> None:
        for vertex_type in reversed(VERTEX_TYPES):
            self.client.command(f"DELETE FROM {vertex_type}")
