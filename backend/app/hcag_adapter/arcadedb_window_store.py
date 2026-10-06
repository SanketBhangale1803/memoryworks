"""Persist HCAG context windows in Runbook's graph store.

Every routed ingestion updates a per-project `ContextWindow` vertex with
its domain/subdomain and a running item count, so the Repo Graph page can
show which context windows exist and how much knowledge each holds. This
uses the same `GraphStore` interface as the rest of the product (ArcadeDB
in production, in-memory in tests).
"""

from __future__ import annotations

from typing import Any

from app.core.database import row, utcnow
from app.graph.base import GraphStore


def canonical_window_id(project_id: str, domain: str, subdomain: str) -> str:
    return f"win:{project_id}:{domain}.{subdomain}".replace(" ", "_").lower()


class ArcadeDBWindowStore:
    def __init__(self, graph: GraphStore):
        self.graph = graph

    @staticmethod
    def _item_count(project_id: str, domain: str, subdomain: str) -> int:
        result = row(
            "SELECT COUNT(*) value FROM knowledge_items WHERE project_id=? "
            "AND json_extract(metadata_json, '$.hcag.domain')=? "
            "AND json_extract(metadata_json, '$.hcag.subdomain')=?",
            (project_id, domain, subdomain),
        )
        return int((result or {}).get("value") or 0)

    def ensure_window(self, project_id: str, domain: str, subdomain: str) -> str:
        window_id = canonical_window_id(project_id, domain, subdomain)
        item_count = self._item_count(project_id, domain, subdomain)
        self.graph.upsert_node(
            "ContextWindow",
            {
                "id": window_id,
                "project_id": project_id,
                "domain": domain,
                "subdomain": subdomain,
                "name": f"{domain}.{subdomain}",
                "item_count": item_count,
                "updated_at": utcnow(),
            },
        )
        return window_id

    def record_item(self, project_id: str, domain: str, subdomain: str) -> str:
        return self.ensure_window(project_id, domain, subdomain)

    def record_chunk(
        self,
        project_id: str,
        domain: str,
        subdomain: str,
        chunk_id: str,
        window_id: str | None = None,
    ) -> str:
        """Attach an evidence chunk to its durable HCAG context window.

        Context windows used to be decorative counters. This edge makes the
        memory boundary traversable in ArcadeDB and lets retrieval traces and
        the graph UI show the exact evidence contained by each window.

        Pass ``window_id`` from an earlier call to skip refreshing the window:
        a document's chunks share a few windows, and refreshing one counts items
        across the whole project.
        """
        window_id = window_id or self.ensure_window(project_id, domain, subdomain)
        self.graph.link(
            "CONTEXT_WINDOW_CONTAINS_CHUNK",
            "ContextWindow",
            window_id,
            "KnowledgeChunk",
            chunk_id,
        )
        return window_id

    def list_windows(self, project_id: str) -> list[dict[str, Any]]:
        return self.graph.list_nodes(project_id, "ContextWindow", 200)
