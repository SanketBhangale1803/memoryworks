from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphEvidence:
    chunk_id: str
    text: str
    source_type: str
    source_title: str
    source_url: str
    service_names: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    graph_paths: list[str] = field(default_factory=list)


class GraphStore(ABC):
    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group the writes made inside the block. Stores that commit each write
        cheaply (the in-memory one) need do nothing; see ArcadeDBGraphStore."""
        yield

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def upsert_project(self, project: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_repository(self, repository: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_service(self, service: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_file(self, file: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_issue(self, issue: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_pull_request(self, pull_request: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_slack_channel(self, channel: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_slack_message(self, message: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_evidence_source(self, source: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_runbook_step(self, step: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_agent_action(self, action: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_operational_assertion(self, assertion: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_change_impact(self, impact: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_node(self, node_type: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def clear_repository_knowledge(self, project_id: str) -> None: ...

    @abstractmethod
    def delete_source_knowledge(
        self, project_id: str, source_id: str, source_type: str = ""
    ) -> int: ...

    @abstractmethod
    def delete_project_nodes(self, project_id: str, node_types: list[str]) -> int: ...

    @abstractmethod
    def delete_project(self, project_id: str) -> int: ...

    @abstractmethod
    def upsert_knowledge_item(self, item: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_chunk(self, chunk: dict[str, Any]) -> None: ...

    @abstractmethod
    def upsert_runbook(self, runbook: dict[str, Any]) -> None: ...

    @abstractmethod
    def link(
        self, edge_type: str, from_type: str, from_id: str, to_type: str, to_id: str
    ) -> None: ...

    def link_chunk_to_service(self, chunk_id: str, service_id: str) -> None:
        self.link("CHUNK_REFERENCES_SERVICE", "KnowledgeChunk", chunk_id, "Service", service_id)

    def link_runbook_to_sources(self, runbook_id: str, source_ids: list[str]) -> None:
        for source_id in source_ids:
            self.link("RUNBOOK_USES_SOURCE", "Runbook", runbook_id, "KnowledgeItem", source_id)

    def link_file_to_chunk(self, file_id: str, chunk_id: str) -> None:
        self.link("FILE_HAS_CHUNK", "File", file_id, "KnowledgeChunk", chunk_id)
        self.link("CHUNK_DERIVED_FROM_FILE", "KnowledgeChunk", chunk_id, "File", file_id)

    @abstractmethod
    def retrieve_context(
        self, project_id: str, query: str, service_name: str | None = None, limit: int = 12
    ) -> list[GraphEvidence]: ...

    @abstractmethod
    def traverse_context(
        self,
        project_id: str,
        query: str,
        *,
        max_hops: int = 3,
        limit: int = 12,
    ) -> list[GraphEvidence]:
        """Retrieve evidence reached through bounded, query-seeded graph traversal."""
        ...

    @abstractmethod
    def get_retrieval_trace(
        self, project_id: str, chunk_ids: list[str]
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def node_counts(self, project_id: str | None = None) -> dict[str, int]: ...

    @abstractmethod
    def edge_counts(self, project_id: str | None = None) -> dict[str, int]: ...

    @abstractmethod
    def graph_summary(self, project_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def list_nodes(
        self, project_id: str, node_type: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def list_edges(
        self, project_id: str, edge_type: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def service_graph(self, project_id: str, service_name: str) -> dict[str, Any]: ...

    @abstractmethod
    def file_graph(self, project_id: str, path: str) -> dict[str, Any]: ...

    @abstractmethod
    def reset(self) -> None: ...
