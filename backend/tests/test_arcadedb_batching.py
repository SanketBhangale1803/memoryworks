"""ArcadeDB writes are batched, edge lookups are indexed, edge listing is scoped.

Measured on ArcadeDB 26.5.1 with 120k existing edges: a 75-chunk document took
8.5 s and 309 requests to ingest, and an answer spent ~9 s listing edges. With
batching and the edge_key index the same document takes 0.5 s and 11 requests,
and listing walks out from the project's indexed vertices.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.graph.arcadedb_store import ArcadeDBGraphStore
from app.graph.migrations import schema_commands
from app.graph.schema import EDGE_TYPES


class FakeClient:
    def __init__(self, query_results: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.commands: list[tuple[str, dict[str, Any], str]] = []
        self.query_results = query_results or {}

    def command(self, command: str, params: dict[str, Any] | None = None, language: str = "sql"):
        self.commands.append((command, dict(params or {}), language))
        return []

    def query(self, command: str, params: dict[str, Any] | None = None, language: str = "sql"):
        for marker, result in self.query_results.items():
            if marker in command:
                return result
        return []


def test_writes_outside_a_batch_are_sent_at_once():
    client = FakeClient()
    store = ArcadeDBGraphStore(client)
    store.upsert_chunk({"id": "c1", "project_id": "p", "text": "x"})
    assert len(client.commands) == 1
    assert client.commands[0][0].startswith("UPDATE KnowledgeChunk")


def test_a_batch_sends_one_transaction_with_renamed_parameters():
    client = FakeClient()
    store = ArcadeDBGraphStore(client)
    with store.batch():
        store.upsert_chunk({"id": "c1", "project_id": "p", "text": "first"})
        store.upsert_chunk({"id": "c2", "project_id": "p", "text": "second"})
        store.link("CHUNK_DERIVED_FROM", "KnowledgeChunk", "c1", "KnowledgeItem", "i1")
        assert client.commands == []

    assert len(client.commands) == 1
    script, params, language = client.commands[0]
    assert language == "sqlscript"
    assert script.startswith("BEGIN;") and script.rstrip().endswith("COMMIT RETRY 5;")
    # Each statement's parameters are its own, so two chunks never collide.
    assert params["s0_id"] == "c1" and params["s1_id"] == "c2"
    assert params["s0_text"] == "first" and params["s1_text"] == "second"
    assert ":s1_text" in script and ":text" not in script
    assert params["s2_edge_key"] == "CHUNK_DERIVED_FROM:c1:i1"


def test_a_batch_writes_each_edge_once():
    client = FakeClient()
    store = ArcadeDBGraphStore(client)
    with store.batch():
        for _ in range(5):
            store.link("PROJECT_HAS_SERVICE", "Project", "p", "Service", "p:api")
    script = client.commands[0][0]
    assert script.count("CREATE EDGE PROJECT_HAS_SERVICE") == 1


def test_a_large_batch_commits_in_pieces(monkeypatch):
    monkeypatch.setattr(ArcadeDBGraphStore, "BATCH_SIZE", 3)
    client = FakeClient()
    store = ArcadeDBGraphStore(client)
    with store.batch():
        for index in range(7):
            store.upsert_chunk({"id": f"c{index}", "project_id": "p"})
    assert len(client.commands) == 3  # 3 + 3 + 1


def test_buffered_writes_are_still_sent_when_the_block_fails():
    client = FakeClient()
    store = ArcadeDBGraphStore(client)
    with pytest.raises(RuntimeError), store.batch():
        store.upsert_chunk({"id": "c1", "project_id": "p"})
        raise RuntimeError("extraction failed")
    assert len(client.commands) == 1


def test_a_nested_batch_joins_the_outer_one():
    client = FakeClient()
    store = ArcadeDBGraphStore(client)
    with store.batch():
        store.upsert_chunk({"id": "c1", "project_id": "p"})
        with store.batch():
            store.upsert_chunk({"id": "c2", "project_id": "p"})
        assert client.commands == []
    assert len(client.commands) == 1


def test_edge_listing_walks_out_from_indexed_vertices_and_dedupes():
    edge = {"rid": "#40:1", "relationship": "CHUNK_DERIVED_FROM", "from_id": "c1", "to_id": "i1"}
    client = FakeClient(
        {
            "FROM KnowledgeChunk WHERE project_id": [edge],
            # The same edge, reached again from the item at its other end.
            "FROM KnowledgeItem WHERE project_id": [edge],
        }
    )
    store = ArcadeDBGraphStore(client)
    edges = store.list_edges("p", limit=100)
    assert edges == [{"relationship": "CHUNK_DERIVED_FROM", "from_id": "c1", "to_id": "i1"}]
    assert store.list_edges("p", "NOT_AN_EDGE") == []


def test_every_edge_type_gets_an_edge_key_index():
    commands = schema_commands()
    for edge_type in EDGE_TYPES:
        assert f"CREATE INDEX IF NOT EXISTS ON {edge_type} (edge_key) NOTUNIQUE" in commands
    assert "CREATE INDEX IF NOT EXISTS ON KnowledgeChunk (item_id) NOTUNIQUE" in commands
    assert "CREATE INDEX IF NOT EXISTS ON KnowledgeItem (source_id) NOTUNIQUE" in commands
