"""A prepared, cached view of one project's chunks, for ranking and traversal.

Answering one question used to load every chunk of a project — text, metadata,
and its embedding as JSON — from ArcadeDB 10-15 times (once per query variant,
per lane, per candidate project), re-tokenise every chunk and re-parse every
embedding into Python floats on each pass. Measured locally with 6,000 chunks
across three repositories: ~600 requests and 350-430 MB transferred per
question; in production the same path exhausted the server's memory.

Here a project's chunks are loaded once, in pages, and prepared once: tokens,
document frequencies, and the embeddings as one float32 matrix. Every ranking
call in a question, and later questions, reuse it. An entry is keyed by the
project's knowledge-item count and newest timestamp in SQLite, so a worker's
import (another process) is picked up on the next question, and expires after
``TTL_SECONDS`` as a backstop for in-place re-indexing. The cache holds at most
``GRAPH_INDEX_MAX_CHUNKS`` chunks, least recently used first out, and only one build per
project runs at a time.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ~15 KB per chunk in memory once prepared (text, tokens, metadata, a 384-d
# float32 vector), so the default cap holds about 600 MB. Set it with
# GRAPH_INDEX_MAX_CHUNKS to suit the server's memory.
TTL_SECONDS = 600


def _json_value(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


@dataclass
class PreparedChunks:
    """Chunks with everything ranking needs computed once."""

    records: list[dict[str, Any]]  # each chunk without its embedding
    terms: list[Counter]
    document_frequency: Counter
    services: list[list[Any]]
    metadata: list[dict[str, Any]]
    search_terms: list[set[str]]
    vectors: np.ndarray  # float32, one row per chunk; zero rows where unusable
    vector_ok: np.ndarray  # bool: row holds an embedding of the matrix's width
    models: list[str]
    # traversal's view of the project, filled on first use
    graph: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None = None
    graph_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def build(
        cls, records: list[dict[str, Any]], tokens: Callable[[str], list[str]]
    ) -> PreparedChunks:
        # Each embedding becomes float32 as it is parsed. Parsing them all to
        # Python floats first peaked at ~2 GB for 48k chunks.
        raw_vectors: list[np.ndarray | None] = []
        for record in records:
            parsed = _json_value(record.get("embedding"), [])
            try:
                vector = np.asarray(parsed, dtype=np.float32) if parsed else None
            except (TypeError, ValueError):
                vector = None
            raw_vectors.append(vector if vector is not None and vector.ndim == 1 else None)
        lengths = Counter(len(vector) for vector in raw_vectors if vector is not None)
        width = lengths.most_common(1)[0][0] if lengths else 0
        vectors = np.zeros((len(records), width), dtype=np.float32)
        vector_ok = np.zeros(len(records), dtype=bool)
        for row, vector in enumerate(raw_vectors):
            if vector is not None and len(vector) == width:
                vectors[row] = vector
                vector_ok[row] = True
        del raw_vectors
        terms = [
            Counter(tokens(f"{record.get('source_title', '')} {record.get('text', '')}"))
            for record in records
        ]
        return cls(
            records=[{k: v for k, v in record.items() if k != "embedding"} for record in records],
            terms=terms,
            document_frequency=Counter(term for counter in terms for term in counter),
            services=[_json_value(record.get("service_names"), []) for record in records],
            metadata=[_json_value(record.get("metadata_json"), {}) for record in records],
            search_terms=[set(_json_value(record.get("search_terms"), [])) for record in records],
            vectors=vectors,
            vector_ok=vector_ok,
            models=[str(record.get("embedding_model") or "") for record in records],
        )

    def __len__(self) -> int:
        return len(self.records)


@dataclass
class _Entry:
    version: tuple[Any, ...]
    built_at: float
    chunks: PreparedChunks


def project_version(project_id: str) -> tuple[Any, ...]:
    """Changes whenever a source in the project is added, re-imported, or removed."""
    from app.core.database import row

    found = row(
        "SELECT COUNT(*) AS n, MAX(created_at) AS newest FROM knowledge_items WHERE project_id=?",
        (project_id,),
    )
    return (int((found or {}).get("n") or 0), str((found or {}).get("newest") or ""))


class ProjectIndexCache:
    def __init__(self, max_chunks: int | None = None, ttl_seconds: float = TTL_SECONDS) -> None:
        from app.core.config import settings

        self.max_chunks = max_chunks if max_chunks is not None else settings.graph_index_max_chunks
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._building: dict[str, threading.Lock] = {}

    def get(
        self,
        project_id: str,
        load: Callable[[], list[dict[str, Any]]],
        tokens: Callable[[str], list[str]],
    ) -> PreparedChunks:
        version = project_version(project_id)
        cached = self._fresh(project_id, version)
        if cached:
            return cached
        with self._lock:
            stale = self._entries.get(project_id)
        if stale:
            # Stale while revalidating: an import changed the project, so keep
            # answering from the previous index while the new one is built.
            self._rebuild_in_background(project_id, load, tokens)
            return stale.chunks
        return self._build(project_id, version, load, tokens)

    def _build(
        self,
        project_id: str,
        version: tuple[Any, ...],
        load: Callable[[], list[dict[str, Any]]],
        tokens: Callable[[str], list[str]],
    ) -> PreparedChunks:
        with self._lock:
            build_lock = self._building.setdefault(project_id, threading.Lock())
        with build_lock:
            # another caller may have built it while this one waited
            cached = self._fresh(project_id, version)
            if cached:
                return cached
            chunks = PreparedChunks.build(load(), tokens)
            with self._lock:
                self._entries[project_id] = _Entry(version, time.monotonic(), chunks)
                self._entries.move_to_end(project_id)
                self._evict()
            return chunks

    def _rebuild_in_background(
        self,
        project_id: str,
        load: Callable[[], list[dict[str, Any]]],
        tokens: Callable[[str], list[str]],
    ) -> None:
        with self._lock:
            build_lock = self._building.setdefault(project_id, threading.Lock())
        if build_lock.locked():
            return  # already rebuilding

        def rebuild() -> None:
            with suppress(Exception):  # on failure the stale index keeps serving
                self._build(project_id, project_version(project_id), load, tokens)

        threading.Thread(target=rebuild, daemon=True, name=f"index-{project_id}").start()

    def _fresh(self, project_id: str, version: tuple[Any, ...]) -> PreparedChunks | None:
        with self._lock:
            entry = self._entries.get(project_id)
            if (
                entry
                and entry.version == version
                and time.monotonic() - entry.built_at < self.ttl_seconds
            ):
                self._entries.move_to_end(project_id)
                return entry.chunks
        return None

    def _evict(self) -> None:
        total = sum(len(entry.chunks) for entry in self._entries.values())
        while total > self.max_chunks and len(self._entries) > 1:
            _, oldest = self._entries.popitem(last=False)
            total -= len(oldest.chunks)

    def invalidate(self, project_id: str | None = None) -> None:
        with self._lock:
            if project_id is None:
                self._entries.clear()
            else:
                self._entries.pop(project_id, None)


project_indexes = ProjectIndexCache()
