from __future__ import annotations

import json
import re
from typing import Any

from app.core.database import connect, decode, new_id, row, rows, utcnow
from app.governance import ScopeService
from app.graph.base import GraphStore
from app.memory.extraction import extract_atomic_memories

MEMORY_TYPES = {
    "fact",
    "decision",
    "procedure",
    "policy",
    "preference",
    "convention",
    "incident",
    "ownership",
    "config",
    "dependency",
    "open_question",
}
RELATIONSHIPS = {"UPDATES", "EXTENDS", "DERIVES", "CONTRADICTS", "SUPPORTS"}

# Kinds that record something that happened rather than something that holds.
# A second incident on the same service is another incident, not a correction
# of the first, so these never supersede one another.
EVENT_KINDS = {"incident"}

# "Supersedes ADR-009", "replaces RFC 12". Decision records name what they
# retire far more reliably than their extracted sentences share a subject.
_SUPERSEDES_RE = re.compile(
    r"\b(?i:supersedes|replaces|deprecates|obsoletes)\s+([A-Z]{2,6})[-\s]?0*(\d{1,5})\b"
)
_RECORD_ID_RE = re.compile(r"\b([A-Z]{2,6})[-\s]?0*(\d{1,5})\b")


class CompanyMemoryService:
    """Durable, source-backed atomic company memory.

    Extraction is deliberately conservative: declarative source statements are
    kept; low-signal prose remains available as chunks but is not promoted.
    """

    def __init__(self, graph: GraphStore):
        self.graph = graph
        self.scopes = ScopeService()

    def extract_memory_units(
        self,
        project_id: str,
        item_id: str,
        source_id: str,
        source_type: str,
        title: str,
        content: str,
        metadata: dict[str, Any],
        chunk_ids: list[str],
        historical: bool = False,
    ) -> list[dict[str, Any]]:
        candidates = self._candidates(title, content, source_type, metadata)
        return [
            self.create(
                project_id,
                kind,
                subject,
                statement,
                [source_id],
                confidence,
                self._scope(project_id, metadata, statement),
                item_id,
                chunk_ids,
                historical=historical,
            )
            for kind, subject, statement, confidence in candidates[:40]
        ]

    def create(
        self,
        project_id: str,
        kind: str,
        subject: str,
        content: str,
        source_ids: list[str],
        confidence: float,
        scope: dict[str, str],
        item_id: str = "",
        chunk_ids: list[str] | None = None,
        memory_id: str = "",
        historical: bool = False,
    ) -> dict[str, Any]:
        """Store one memory, superseding the current same-kind, same-subject one.

        A `historical` memory comes from a record something newer already
        supersedes. It is stored as no longer current and never displaces the
        memory that is, whatever order the two records were ingested in.
        """
        now = utcnow()
        memory_id = memory_id or new_id("mem")
        workspace = row(
            "SELECT workspace_id FROM workspace_projects WHERE project_id=?", (project_id,)
        )
        # Only a statement of the same kind can be a newer version of this one.
        # Subjects are short ("ledger-service"), so without the kind an incident
        # about a service would retire the record of who owns it.
        previous = row(
            "SELECT * FROM memory_units WHERE project_id=? AND type=? AND lower(subject)=lower(?) AND is_latest=1 ORDER BY updated_at DESC LIMIT 1",
            (project_id, kind, subject),
        )
        if previous and previous["content"].casefold() == content.casefold():
            existing_sources = json.loads(previous.get("source_ids_json") or "[]")
            merged_sources = list(dict.fromkeys([*existing_sources, *source_ids]))
            with connect() as conn:
                conn.execute(
                    "UPDATE memory_units SET source_ids_json=?,confidence=max(confidence,?),updated_at=? WHERE id=?",
                    (json.dumps(merged_sources), confidence, now, previous["id"]),
                )
            for source_id in source_ids:
                self.scopes.bind_memory_from_source(project_id, previous["id"], source_id)
            return self.get(previous["id"]) or {}
        relationship = ""
        if previous and (kind in EVENT_KINDS or historical):
            previous = None
        if previous and previous["content"].casefold() != content.casefold():
            relationship = self._relationship(previous["content"], content)
        with connect() as conn:
            conn.execute(
                "INSERT INTO memory_units VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    (workspace or {}).get("workspace_id", ""),
                    project_id,
                    kind,
                    subject,
                    content,
                    json.dumps(scope),
                    json.dumps(source_ids),
                    confidence,
                    now,
                    now if historical else None,
                    0 if historical else 1,
                    now,
                    now,
                ),
            )
            if previous and relationship == "UPDATES":
                conn.execute(
                    "UPDATE memory_units SET is_latest=0,valid_to=?,updated_at=? WHERE id=?",
                    (now, now, previous["id"]),
                )
            if previous and relationship:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_relationships VALUES (?,?,?,?,?,?)",
                    (new_id("mrel"), project_id, memory_id, previous["id"], relationship, now),
                )
        payload = self.get(memory_id) or {}
        self.graph.upsert_node("MemoryUnit", {**payload, "scope": scope, "source_ids": source_ids})
        for source_id in source_ids:
            source_type_node = self._source_node_type(source_id, item_id)
            self.graph.link(
                "MEMORY_DERIVED_FROM_SOURCE",
                "MemoryUnit",
                memory_id,
                source_type_node,
                source_id if source_type_node != "KnowledgeItem" else item_id,
            )
            self.scopes.bind_memory_from_source(project_id, memory_id, source_id)
        for chunk_id in chunk_ids or []:
            self.graph.link(
                "MEMORY_DERIVED_FROM_CHUNK", "MemoryUnit", memory_id, "KnowledgeChunk", chunk_id
            )
        if previous and relationship:
            self.graph.link(relationship, "MemoryUnit", memory_id, "MemoryUnit", previous["id"])
        return payload

    def list(
        self,
        project_id: str | None = None,
        *,
        latest: bool | None = None,
        kind: str = "",
        limit: int = 500,
        allowed_team_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if latest is not None:
            clauses.append("is_latest=?")
            params.append(int(latest))
        if kind:
            clauses.append("type=?")
            params.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        records = [
            decode(item)
            for item in rows(
                f"SELECT * FROM memory_units{where} ORDER BY updated_at DESC LIMIT ?",
                (*params, limit),
            )
        ]
        if not project_id:
            return records
        visible_ids = self.scopes.visible_memory_ids(project_id, allowed_team_ids)
        return (
            records
            if visible_ids is None
            else [item for item in records if item["id"] in visible_ids]
        )

    def get(self, memory_id: str) -> dict[str, Any] | None:
        item = row("SELECT * FROM memory_units WHERE id=?", (memory_id,))
        return decode(item) if item else None

    def retire_source_memories(self, project_id: str, source_id: str) -> list[str]:
        current = [
            item
            for item in self.list(project_id, latest=True, limit=10_000)
            if source_id in item.get("source_ids", [])
        ]
        if not current:
            return []
        now = utcnow()
        with connect() as conn:
            for memory in current:
                remaining_sources = [
                    value for value in memory.get("source_ids", []) if value != source_id
                ]
                if remaining_sources:
                    conn.execute(
                        "UPDATE memory_units SET source_ids_json=?,updated_at=? WHERE id=?",
                        (json.dumps(remaining_sources), now, memory["id"]),
                    )
                else:
                    conn.execute(
                        """UPDATE memory_units SET is_latest=0,valid_to=?,updated_at=?
                        WHERE id=?""",
                        (now, now, memory["id"]),
                    )
        retired: list[str] = []
        for memory in current:
            refreshed = self.get(memory["id"]) or {}
            self.graph.upsert_node(
                "MemoryUnit",
                {
                    **refreshed,
                    "scope": refreshed.get("scope", {}),
                    "source_ids": refreshed.get("source_ids", []),
                },
            )
            if not refreshed.get("is_latest"):
                retired.append(memory["id"])
        return retired

    def superseded_by(self, project_id: str, source_id: str, title: str) -> str:
        """The source of an already-ingested record that supersedes this one, if any.

        Checked before a record's memories are written, so a record that
        arrives after its replacement is stored as history from the start
        instead of briefly displacing what is current.
        """
        own_keys = _record_keys(title)
        if not own_keys:
            return ""
        for item in rows(
            """SELECT source_id, content FROM knowledge_items WHERE project_id=? AND source_id<>?
            AND (lower(content) LIKE '%supersedes%' OR lower(content) LIKE '%replaces%'
              OR lower(content) LIKE '%deprecates%' OR lower(content) LIKE '%obsoletes%')
            ORDER BY created_at DESC""",
            (project_id, source_id),
        ):
            if own_keys & {_record_key(*m) for m in _SUPERSEDES_RE.findall(item["content"])}:
                return str(item["source_id"])
        return ""

    def apply_supersession(
        self,
        project_id: str,
        source_id: str,
        content: str,
        memory_units: list[dict[str, Any]],
        superseded_by: str = "",
    ) -> list[str]:
        """Connect a record to the records it supersedes, or to its replacement.

        A source that says "Supersedes ADR-009" retires ADR-009's current
        memories. A source that was itself already superseded (its memories
        were written as history) is linked to the memory that replaced it.
        Returns the ids of the memories retired.
        """
        retired: list[str] = []
        successor = _first_lasting(memory_units)
        referenced = {_record_key(*match) for match in _SUPERSEDES_RE.findall(content)}
        if referenced:
            for item in rows(
                "SELECT DISTINCT source_id, source_title FROM knowledge_items WHERE project_id=? AND source_id<>?",
                (project_id, source_id),
            ):
                if referenced & _record_keys(item["source_title"]):
                    retired += self._supersede_source(project_id, item["source_id"], successor)
        if superseded_by:
            newer = _first_lasting(
                [
                    unit
                    for unit in self.list(project_id, latest=True, limit=10_000)
                    if superseded_by in unit.get("source_ids", [])
                ]
            )
            self._link_updates(project_id, newer, [unit["id"] for unit in memory_units])
        return retired

    def _supersede_source(self, project_id: str, source_id: str, successor: str) -> list[str]:
        retired = self.retire_source_memories(project_id, source_id)
        self._link_updates(project_id, successor, retired)
        return retired

    def _link_updates(self, project_id: str, successor: str, older: list[str]) -> None:
        if not successor or not older:
            return
        now = utcnow()
        with connect() as conn:
            for old_id in older:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_relationships VALUES (?,?,?,?,?,?)",
                    (new_id("mrel"), project_id, successor, old_id, "UPDATES", now),
                )
        for old_id in older:
            self.graph.link("UPDATES", "MemoryUnit", successor, "MemoryUnit", old_id)

    def relationships(
        self, project_id: str, relationship: str = "", allowed_team_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if relationship:
            records = rows(
                "SELECT * FROM memory_relationships WHERE project_id=? AND relationship=? ORDER BY created_at DESC",
                (project_id, relationship),
            )
        else:
            records = rows(
                "SELECT * FROM memory_relationships WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
        visible_ids = self.scopes.visible_memory_ids(project_id, allowed_team_ids)
        if visible_ids is None:
            return records
        return [
            item
            for item in records
            if item["from_memory_id"] in visible_ids and item["to_memory_id"] in visible_ids
        ]

    def profile(
        self,
        project_id: str,
        profile_type: str = "project",
        name: str = "",
        allowed_team_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        project = row("SELECT * FROM projects WHERE id=?", (project_id,)) or {}
        current = self.list(project_id, latest=True, limit=1000, allowed_team_ids=allowed_team_ids)
        if name:
            key = name.casefold()
            current = [
                m
                for m in current
                if key in json.dumps(m.get("scope", {})).casefold()
                or key in m["subject"].casefold()
                or key in m["content"].casefold()
            ]
        grouped = {kind: [m for m in current if m["type"] == kind] for kind in MEMORY_TYPES}
        sources = sorted({source for memory in current for source in memory.get("source_ids", [])})
        return {
            "profile_type": profile_type,
            "project_id": project_id,
            "name": name or project.get("name", ""),
            "assembled_from": "current_memory_units",
            "current_facts": grouped["fact"] + grouped["config"],
            "decisions": grouped["decision"],
            "policies": grouped["policy"],
            "procedures": grouped["procedure"],
            "dependencies": grouped["dependency"],
            "owners": grouped["ownership"],
            "incidents": grouped["incident"],
            "recent_updates": self.relationships(project_id, "UPDATES", allowed_team_ids),
            "conflicts": self.relationships(project_id, "CONTRADICTS", allowed_team_ids),
            "sources": sources,
        }

    @staticmethod
    def _candidates(
        title: str,
        content: str,
        source_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[tuple[str, str, str, float]]:
        return extract_atomic_memories(title, content, source_type, metadata or {})

    @staticmethod
    def _relationship(old: str, new: str) -> str:
        def neg(value: str) -> bool:
            return bool(
                re.search(
                    r"\b(no|not|never|instead|replaced|changed|migrated)\b",
                    value.casefold(),
                )
            )

        if neg(old) != neg(new) or re.search(
            r"\b(instead|replaced|migrated from|no longer)\b", new.casefold()
        ):
            return "CONTRADICTS"
        return "UPDATES"

    @staticmethod
    def _scope(project_id: str, metadata: dict[str, Any], text: str) -> dict[str, str]:
        services = metadata.get("services") or []
        service = next(
            (s for s in services if s.casefold() in text.casefold()),
            services[0] if services else "",
        )
        return {
            "company": "",
            "project": project_id,
            "repo": str(metadata.get("repository") or ""),
            "service": service,
            "person": str(metadata.get("user") or metadata.get("owner") or ""),
        }

    def _source_node_type(self, source_id: str, item_id: str) -> str:
        for node_type in ("File", "Issue", "PullRequest", "SlackMessage", "EvidenceSource"):
            if any(n.get("id") == source_id for n in self.graph.list_nodes("", node_type, 100000)):
                return node_type
        return "KnowledgeItem" if item_id else "EvidenceSource"


def _first_lasting(units: list[dict[str, Any]]) -> str:
    """The first memory that can stand as a successor; events cannot."""
    return next((unit["id"] for unit in units if unit.get("type") not in EVENT_KINDS), "")


def _record_key(prefix: str, number: str) -> str:
    return f"{prefix.upper()}-{int(number)}"


def _record_keys(text: str) -> set[str]:
    return {_record_key(*match) for match in _RECORD_ID_RE.findall(text or "")}
