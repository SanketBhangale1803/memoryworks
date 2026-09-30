from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import connect, decode, new_id, row, rows, utcnow
from app.graph.base import GraphStore


class CompanyBrainService:
    """Revision, impact, context, artifact, and skill lifecycle for MemoryWorks."""

    def __init__(self, graph: GraphStore):
        self.graph = graph

    def stable_source_id(
        self, project_id: str, source_type: str, title: str, requested: str | None = None
    ) -> str:
        if requested:
            return requested
        existing = row(
            """SELECT source_id FROM knowledge_items WHERE project_id=? AND source_type=?
            AND source_title=? ORDER BY created_at DESC LIMIT 1""",
            (project_id, source_type, title),
        )
        return existing["source_id"] if existing else new_id("src")

    def record_source_revision(
        self,
        project_id: str,
        source_id: str,
        source_type: str,
        title: str,
        content: str,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        duplicate = row(
            "SELECT * FROM source_revisions WHERE project_id=? AND source_id=? AND content_hash=?",
            (project_id, source_id, digest),
        )
        if duplicate:
            payload = decode(duplicate)
            self.graph.upsert_node(
                "Source",
                {
                    "id": source_id,
                    "project_id": project_id,
                    "source_type": source_type,
                    "title": title,
                },
            )
            self.graph.upsert_node("SourceRevision", payload)
            self.graph.link(
                "SOURCE_HAS_REVISION",
                "Source",
                source_id,
                "SourceRevision",
                payload["id"],
            )
            return payload, False
        previous = row(
            """SELECT * FROM source_revisions WHERE project_id=? AND source_id=?
            ORDER BY version DESC LIMIT 1""",
            (project_id, source_id),
        )
        revision_id = new_id("srev")
        version = int((previous or {}).get("version") or 0) + 1
        now = utcnow()
        with connect() as conn:
            if previous:
                conn.execute(
                    "UPDATE source_revisions SET status='superseded' WHERE id=?", (previous["id"],)
                )
            conn.execute(
                """INSERT INTO source_revisions
                (id,project_id,source_id,source_type,source_title,version,content_hash,content,
                 metadata_json,status,supersedes_revision_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id,
                    project_id,
                    source_id,
                    source_type,
                    title,
                    version,
                    digest,
                    content,
                    json.dumps(metadata),
                    "current",
                    (previous or {}).get("id"),
                    now,
                ),
            )
        payload = decode(row("SELECT * FROM source_revisions WHERE id=?", (revision_id,)) or {})
        self.graph.upsert_node(
            "Source",
            {
                "id": source_id,
                "project_id": project_id,
                "source_type": source_type,
                "title": title,
            },
        )
        self.graph.upsert_node("SourceRevision", payload)
        self.graph.link("SOURCE_HAS_REVISION", "Source", source_id, "SourceRevision", revision_id)
        if previous:
            self.graph.link(
                "REVISION_SUPERSEDES",
                "SourceRevision",
                revision_id,
                "SourceRevision",
                previous["id"],
            )
        return payload, True

    def current_source_memories(self, project_id: str, source_id: str) -> list[dict[str, Any]]:
        return [
            decode(item)
            for item in rows(
                "SELECT * FROM memory_units WHERE project_id=? AND is_latest=1 ORDER BY updated_at DESC",
                (project_id,),
            )
            if source_id in json.loads(item.get("source_ids_json") or "[]")
        ]

    def finalize_change_set(
        self,
        project_id: str,
        source_id: str,
        source_revision_id: str,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        actor: str = "ingestion",
    ) -> dict[str, Any]:
        before_ids = {item["id"] for item in before}
        after_ids = {item["id"] for item in after}
        added = sorted(after_ids - before_ids)
        after_subjects = {self._key(item["subject"]) for item in after}
        invalidated: list[str] = []
        now = utcnow()
        for memory in before:
            if memory["id"] in after_ids or self._key(memory["subject"]) in after_subjects:
                continue
            invalidated.append(memory["id"])
        if invalidated:
            placeholders = ",".join("?" for _ in invalidated)
            with connect() as conn:
                conn.execute(
                    f"UPDATE memory_units SET is_latest=0,valid_to=?,updated_at=? WHERE id IN ({placeholders})",
                    (now, now, *invalidated),
                )
            for memory_id in invalidated:
                memory = decode(row("SELECT * FROM memory_units WHERE id=?", (memory_id,)) or {})
                if memory:
                    self.graph.upsert_node(
                        "MemoryUnit",
                        {
                            **memory,
                            "scope": memory.get("scope", {}),
                            "source_ids": memory.get("source_ids", []),
                        },
                    )
        relationships = rows(
            """SELECT * FROM memory_relationships WHERE project_id=? AND from_memory_id IN
            ({})""".format(
                ",".join("?" for _ in added) or "''"
            ),
            (project_id, *added),
        )
        updated = [
            item["from_memory_id"] for item in relationships if item["relationship"] == "UPDATES"
        ]
        conflicts = [
            item["from_memory_id"]
            for item in relationships
            if item["relationship"] == "CONTRADICTS"
        ]
        change_set_id = new_id("chg")
        profiles = [f"project:{project_id}"]
        review_status = "needs_review" if conflicts else "automatic"
        with connect() as conn:
            conn.execute(
                """INSERT INTO memory_change_sets
                (id,project_id,source_id,source_revision_id,actor,added_json,updated_json,
                 invalidated_json,conflicts_json,affected_profiles_json,affected_artifacts_json,
                 affected_skills_json,review_status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    change_set_id,
                    project_id,
                    source_id,
                    source_revision_id,
                    actor,
                    json.dumps(added),
                    json.dumps(sorted(set(updated))),
                    json.dumps(invalidated),
                    json.dumps(sorted(set(conflicts))),
                    json.dumps(profiles),
                    "[]",
                    "[]",
                    review_status,
                    now,
                ),
            )
        affected = set(added + updated + invalidated + conflicts)
        affected.update(
            item["to_memory_id"]
            for item in relationships
            if item["relationship"] in {"UPDATES", "CONTRADICTS"}
        )
        affected_artifacts = self._invalidate_artifacts(
            project_id, source_id, affected, change_set_id
        )
        affected_skills = self._invalidate_skills(project_id, affected)
        with connect() as conn:
            conn.execute(
                """UPDATE memory_change_sets SET affected_artifacts_json=?,affected_skills_json=?
                WHERE id=?""",
                (json.dumps(affected_artifacts), json.dumps(affected_skills), change_set_id),
            )
        payload = self.change_set(change_set_id) or {}
        self.graph.upsert_node("MemoryChangeSet", payload)
        self.graph.link(
            "REVISION_PRODUCED_CHANGESET",
            "SourceRevision",
            source_revision_id,
            "MemoryChangeSet",
            change_set_id,
        )
        for memory_id in added:
            self.graph.link(
                "CHANGESET_ADDED_MEMORY", "MemoryChangeSet", change_set_id, "MemoryUnit", memory_id
            )
        for memory_id in invalidated:
            self.graph.link(
                "CHANGESET_INVALIDATED_MEMORY",
                "MemoryChangeSet",
                change_set_id,
                "MemoryUnit",
                memory_id,
            )
        return payload

    def list_revisions(self, project_id: str, source_id: str = "") -> list[dict[str, Any]]:
        if source_id:
            records = rows(
                "SELECT * FROM source_revisions WHERE project_id=? AND source_id=? ORDER BY version DESC",
                (project_id, source_id),
            )
        else:
            records = rows(
                "SELECT * FROM source_revisions WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
        return [decode(item) for item in records]

    def list_change_sets(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return [
            decode(item)
            for item in rows(
                "SELECT * FROM memory_change_sets WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        ]

    def change_set(self, change_set_id: str) -> dict[str, Any] | None:
        item = row("SELECT * FROM memory_change_sets WHERE id=?", (change_set_id,))
        return decode(item) if item else None

    def version_vector(
        self, project_id: str, source_ids: list[str] | None = None
    ) -> dict[str, int]:
        records = rows(
            """SELECT source_id,max(version) version FROM source_revisions
            WHERE project_id=? GROUP BY source_id""",
            (project_id,),
        )
        wanted = set(source_ids or [])
        return {
            item["source_id"]: int(item["version"])
            for item in records
            if not wanted or item["source_id"] in wanted
        }

    def create_context_envelope(
        self,
        project_id: str,
        query: str,
        task_type: str,
        principal_id: str,
        team_ids: list[str] | None,
        memories: list[dict[str, Any]],
        evidence_ids: list[str],
        target_entities: list[str],
        retrieval_trace: dict[str, Any],
        token_budget: int = 6000,
        *,
        evidence_source_ids: list[str] | None = None,
        compiled_context: dict[str, Any] | None = None,
        activation_run_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        envelope_id = new_id("ctx")
        source_ids = list(
            dict.fromkeys(
                [
                    *(
                        source_id
                        for memory in memories
                        for source_id in memory.get("source_ids", [])
                    ),
                    *(evidence_source_ids or []),
                ]
            )
        )
        vector = self.version_vector(project_id, source_ids)
        skill_ids = [
            item["id"]
            for item in rows(
                "SELECT id FROM skill_specs WHERE project_id=? AND status='current' ORDER BY version DESC",
                (project_id,),
            )
        ]
        now = utcnow()
        expires = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
        with connect() as conn:
            conn.execute(
                """INSERT INTO context_envelopes (
                  id,project_id,principal_id,query,task_type,authorized_team_ids_json,
                  target_entities_json,memory_ids_json,evidence_ids_json,skill_ids_json,
                  source_version_vector_json,retrieval_trace_json,compiled_context_json,
                  activation_run_ids_json,token_budget,expires_at,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    envelope_id,
                    project_id,
                    principal_id,
                    query,
                    task_type,
                    json.dumps(team_ids or []),
                    json.dumps(target_entities),
                    json.dumps([item["id"] for item in memories]),
                    json.dumps(evidence_ids),
                    json.dumps(skill_ids),
                    json.dumps(vector),
                    json.dumps(retrieval_trace),
                    json.dumps(compiled_context or {}),
                    json.dumps(activation_run_ids or []),
                    token_budget,
                    expires,
                    now,
                ),
            )
        payload = decode(row("SELECT * FROM context_envelopes WHERE id=?", (envelope_id,)) or {})
        self.graph.upsert_node("ContextEnvelope", payload)
        for memory in memories:
            self.graph.link(
                "CONTEXT_USED_MEMORY", "ContextEnvelope", envelope_id, "MemoryUnit", memory["id"]
            )
        return payload

    def save_artifact(
        self,
        project_id: str,
        name: str,
        artifact_type: str,
        content: str,
        source_ids: list[str],
        memory_ids: list[str],
        context_envelope_id: str = "",
    ) -> dict[str, Any]:
        existing = row(
            "SELECT * FROM artifacts WHERE project_id=? AND artifact_type=? AND name=?",
            (project_id, artifact_type, name),
        )
        artifact_id = existing["id"] if existing else new_id("art")
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        duplicate = row(
            "SELECT * FROM artifact_revisions WHERE artifact_id=? AND content_hash=?",
            (artifact_id, digest),
        )
        if duplicate:
            return self.artifact(artifact_id) or {}
        previous = row(
            "SELECT max(version) version FROM artifact_revisions WHERE artifact_id=?",
            (artifact_id,),
        )
        version = int((previous or {}).get("version") or 0) + 1
        revision_id = new_id("arev")
        now = utcnow()
        with connect() as conn:
            conn.execute(
                """INSERT INTO artifacts(id,project_id,name,artifact_type,current_revision_id,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                current_revision_id=excluded.current_revision_id,status='current',updated_at=excluded.updated_at""",
                (
                    artifact_id,
                    project_id,
                    name,
                    artifact_type,
                    revision_id,
                    "current",
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO artifact_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id,
                    artifact_id,
                    project_id,
                    version,
                    content,
                    digest,
                    json.dumps(source_ids),
                    json.dumps(memory_ids),
                    context_envelope_id or None,
                    "current",
                    now,
                ),
            )
        self.graph.upsert_node("Artifact", self.artifact(artifact_id) or {})
        self.graph.upsert_node(
            "ArtifactRevision",
            decode(row("SELECT * FROM artifact_revisions WHERE id=?", (revision_id,)) or {}),
        )
        self.graph.link(
            "ARTIFACT_HAS_REVISION", "Artifact", artifact_id, "ArtifactRevision", revision_id
        )
        for memory_id in memory_ids:
            self.graph.link(
                "ARTIFACT_DERIVED_FROM_MEMORY",
                "ArtifactRevision",
                revision_id,
                "MemoryUnit",
                memory_id,
            )
        return self.artifact(artifact_id) or {}

    def artifact(self, artifact_id: str) -> dict[str, Any] | None:
        item = row("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if not item:
            return None
        revisions = [
            decode(record)
            for record in rows(
                "SELECT * FROM artifact_revisions WHERE artifact_id=? ORDER BY version DESC",
                (artifact_id,),
            )
        ]
        impacts = rows(
            "SELECT * FROM artifact_impacts WHERE artifact_id=? ORDER BY created_at DESC",
            (artifact_id,),
        )
        return {**item, "revisions": revisions, "impacts": impacts}

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        return [
            self.artifact(item["id"]) or item
            for item in rows(
                "SELECT * FROM artifacts WHERE project_id=? ORDER BY updated_at DESC", (project_id,)
            )
        ]

    def compile_skill(
        self,
        project_id: str,
        name: str,
        memories: list[dict[str, Any]],
        team_id: str = "",
    ) -> dict[str, Any]:
        eligible = [
            item
            for item in memories
            if item.get("is_latest")
            and item.get("type") in {"procedure", "policy", "convention", "decision"}
            and float(item.get("confidence") or 0) >= 0.75
        ]
        if not eligible:
            raise ValueError("No current, high-confidence procedure or policy memories to compile")
        previous = row(
            "SELECT max(version) version FROM skill_specs WHERE project_id=? AND name=? AND team_id=?",
            (project_id, name, team_id),
        )
        version = int((previous or {}).get("version") or 0) + 1
        skill_id = new_id("skill")
        sources = list(
            dict.fromkeys(source for item in eligible for source in item.get("source_ids", []))
        )
        policies = [item for item in eligible if item["type"] == "policy"]
        steps = [item for item in eligible if item["type"] == "procedure"]
        spec = {
            "id": skill_id,
            "name": name,
            "version": version,
            "scope": {"project": project_id, "team": team_id},
            "triggers": [self._key(item["subject"]) for item in eligible],
            "inputs": [],
            "preconditions": [item["content"] for item in policies],
            "steps": [{"instruction": item["content"], "memory_id": item["id"]} for item in steps],
            "policies": [{"rule": item["content"], "memory_id": item["id"]} for item in policies],
            "tools": [],
            "approvals": [],
            "rollback": [],
            "evidence": sources,
            "generated_from": [item["id"] for item in eligible],
        }
        with connect() as conn:
            conn.execute(
                "UPDATE skill_specs SET status='superseded' WHERE project_id=? AND name=? AND team_id=? AND status='current'",
                (project_id, name, team_id),
            )
            conn.execute(
                "INSERT INTO skill_specs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    skill_id,
                    project_id,
                    name,
                    version,
                    team_id,
                    "current",
                    json.dumps(spec),
                    json.dumps([item["id"] for item in eligible]),
                    json.dumps(self.version_vector(project_id, sources)),
                    utcnow(),
                ),
            )
        payload = decode(row("SELECT * FROM skill_specs WHERE id=?", (skill_id,)) or {})
        self.graph.upsert_node("SkillSpec", payload)
        for memory in eligible:
            self.graph.link(
                "SKILL_DERIVED_FROM_MEMORY", "SkillSpec", skill_id, "MemoryUnit", memory["id"]
            )
        return payload

    def list_skills(self, project_id: str, status: str = "") -> list[dict[str, Any]]:
        records = (
            rows(
                "SELECT * FROM skill_specs WHERE project_id=? AND status=? ORDER BY created_at DESC",
                (project_id, status),
            )
            if status
            else rows(
                "SELECT * FROM skill_specs WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
        )
        return [decode(item) for item in records]

    def _invalidate_artifacts(
        self, project_id: str, source_id: str, memory_ids: set[str], change_set_id: str
    ) -> list[str]:
        affected: list[str] = []
        for revision in rows(
            """SELECT ar.* FROM artifact_revisions ar JOIN artifacts a ON a.current_revision_id=ar.id
            WHERE ar.project_id=?""",
            (project_id,),
        ):
            sources = set(json.loads(revision.get("source_ids_json") or "[]"))
            memories = set(json.loads(revision.get("memory_ids_json") or "[]"))
            if source_id not in sources and not memory_ids.intersection(memories):
                continue
            affected.append(revision["artifact_id"])
            with connect() as conn:
                conn.execute(
                    "UPDATE artifacts SET status='stale',updated_at=? WHERE id=?",
                    (utcnow(), revision["artifact_id"]),
                )
                conn.execute(
                    "INSERT INTO artifact_impacts VALUES (?,?,?,?,?,?,?)",
                    (
                        new_id("aimp"),
                        revision["artifact_id"],
                        revision["id"],
                        change_set_id,
                        "A supporting source or memory changed; regenerate or review this artifact.",
                        "needs_review",
                        utcnow(),
                    ),
                )
        return affected

    def _invalidate_skills(self, project_id: str, memory_ids: set[str]) -> list[str]:
        affected: list[str] = []
        for skill in rows(
            "SELECT * FROM skill_specs WHERE project_id=? AND status='current'", (project_id,)
        ):
            if not memory_ids.intersection(json.loads(skill.get("memory_ids_json") or "[]")):
                continue
            affected.append(skill["id"])
            with connect() as conn:
                conn.execute("UPDATE skill_specs SET status='stale' WHERE id=?", (skill["id"],))
        return affected

    @staticmethod
    def _key(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9_]+", value.casefold()))
