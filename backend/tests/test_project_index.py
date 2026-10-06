"""Answers rank from a per-project index built once, not reloaded per question.

Measured on ArcadeDB 26.5.1 with 6,000 chunks across three repositories: a
workspace question made ~600 requests and moved 350-430 MB; with the index,
later questions make 3 requests and move none. Production's largest repository
held 48,084 chunks, reloaded 10-15 times per question.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.database import connect, utcnow
from app.graph.arcadedb_store import ArcadeDBGraphStore
from app.graph.graph_ranker import _tokens
from app.graph.project_index import ProjectIndexCache


def _chunk(chunk_id: str, text: str) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "text": text,
        "source_type": "doc",
        "source_title": chunk_id,
        "embedding": "[]",
    }


def _add_item(project_id: str, item_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id,name,repository,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (project_id, project_id, "", "active", utcnow(), utcnow()),
        )
        conn.execute(
            "INSERT INTO knowledge_items (id,project_id,source_type,source_id,source_title,"
            "source_url,content,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (item_id, project_id, "doc", item_id, item_id, "", "x", "{}", utcnow()),
        )


def test_a_project_is_loaded_once_until_it_changes(graph):
    cache = ProjectIndexCache(max_chunks=1000)
    _add_item("prj_a", "item_1")
    loads: list[int] = []

    def load() -> list[dict[str, Any]]:
        loads.append(1)
        return [_chunk(f"c{len(loads)}", "retry policy for the webhook sender")]

    first = cache.get("prj_a", load, _tokens)
    assert cache.get("prj_a", load, _tokens) is first
    assert len(loads) == 1

    # An import (here: a new knowledge item) changes the project. The next
    # question is answered from the previous index while the new one builds.
    _add_item("prj_a", "item_2")
    assert cache.get("prj_a", load, _tokens) is first
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and cache.get("prj_a", load, _tokens) is first:
        time.sleep(0.02)
    rebuilt = cache.get("prj_a", load, _tokens)
    assert rebuilt is not first and rebuilt.records[0]["id"] == "c2"


def test_the_least_recently_used_projects_leave_first(graph):
    cache = ProjectIndexCache(max_chunks=3)
    for name in ("prj_x", "prj_y", "prj_z"):
        _add_item(name, f"item_{name}")
        cache.get(name, lambda: [_chunk("a", "one"), _chunk("b", "two")], _tokens)
    # 2 chunks each with room for 3: only the newest project fits
    assert list(cache._entries) == ["prj_z"]


def test_chunks_are_loaded_in_pages_past_arcadedbs_row_cap(monkeypatch):
    class PagedClient:
        def __init__(self, total: int) -> None:
            self.total = total
            self.calls: list[dict[str, Any]] = []

        def query(self, command: str, params: dict[str, Any] | None = None, language: str = "sql"):
            params = params or {}
            self.calls.append(params)
            start, size = params["skip"], params["limit"]
            return [_chunk(f"c{i}", "x") for i in range(start, min(self.total, start + size))]

    monkeypatch.setattr(ArcadeDBGraphStore, "CHUNK_PAGE", 4)
    client = PagedClient(total=10)
    records = ArcadeDBGraphStore(client)._load_chunks("prj")

    assert [record["id"] for record in records] == [f"c{i}" for i in range(10)]
    assert [call["skip"] for call in client.calls] == [0, 4, 8]
