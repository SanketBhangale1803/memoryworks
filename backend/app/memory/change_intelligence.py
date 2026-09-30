from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.database import connect, decode, new_id, row, rows, utcnow
from app.graph.base import GraphStore

from .authority import AuthorityResolver
from .beliefs import BeliefStore


class ClaimDelta(BaseModel):
    claim: str
    previous_value: str | None = None
    current_value: str
    confidence: float = Field(ge=0, le=1)
    affected_areas: list[str] = Field(default_factory=list)
    agent_implication: str = ""
    scope: dict[str, str] = Field(default_factory=dict)
    authority_tier: str = ""


class ExtractedClaims(BaseModel):
    summary: str
    added: list[ClaimDelta] = Field(default_factory=list)
    removed: list[ClaimDelta] = Field(default_factory=list)
    modified: list[ClaimDelta] = Field(default_factory=list)
    affected_areas: list[str] = Field(default_factory=list)
    agent_implications: list[str] = Field(default_factory=list)


ChangeProvider = Callable[[str, dict[str, Any]], ExtractedClaims | dict[str, Any]]


def interpret_diff(
    diff: str,
    context: dict[str, Any],
    llm: ChangeProvider | None = None,
) -> ExtractedClaims:
    """Interpret a source diff without storage side effects.

    Passing ``llm`` makes the function fully mockable. Without one, MemoryWorks
    selects the configured structured-output provider and falls back to a
    conservative deterministic interpreter when no model is configured.
    """
    if not diff.strip():
        return ExtractedClaims(summary="No textual changes were available to interpret.")
    provider = llm or _configured_provider()
    output = provider(diff, context)
    return output if isinstance(output, ExtractedClaims) else ExtractedClaims.model_validate(output)


def _configured_provider() -> ChangeProvider:
    provider = settings.org_memory_change_interpreter_provider.casefold()
    if provider == "openai" or (provider == "auto" and settings.openai_api_key):
        return OpenAIStructuredChangeProvider()
    return deterministic_change_provider


class OpenAIStructuredChangeProvider:
    def __call__(self, diff: str, context: dict[str, Any]) -> ExtractedClaims:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for structured change interpretation")
        from openai import OpenAI

        schema = ExtractedClaims.model_json_schema()
        prompt = (
            "You extract company-memory changes from a source diff. Use only facts explicitly "
            "present in the diff and supplied context. Do not infer owners, policies, systems, "
            "or intent that are not evidenced. Return JSON matching this schema exactly:\n"
            f"{json.dumps(schema)}\n\nContext:\n{json.dumps(context)[:12000]}\n\n"
            f"Diff:\n{diff[:60000]}"
        )
        response = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
            model=settings.openai_model,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        return ExtractedClaims.model_validate(json.loads(content))


def deterministic_change_provider(diff: str, context: dict[str, Any]) -> ExtractedClaims:
    """Conservative local interpreter for demos, tests, and no-key deployments."""
    files = _changed_files(diff)
    removed_lines = _change_lines(diff, "-")
    added_lines = _change_lines(diff, "+")
    removed_text = "\n".join(removed_lines)
    added_text = "\n".join(added_lines)
    lowered_removed = removed_text.casefold()
    lowered_added = added_text.casefold()
    scope = {
        key: str(value) for key, value in (context.get("scope") or {}).items() if value is not None
    }
    added: list[ClaimDelta] = []
    removed: list[ClaimDelta] = []
    modified: list[ClaimDelta] = []
    implications: list[str] = []

    local_fixture_removed = bool(
        re.search(r"\b(local|fixture|mock)\w*\b", lowered_removed)
        and re.search(r"\b(employee|identity|directory|user)\w*\b", lowered_removed)
    )
    provider = ""
    if re.search(r"\b(entra\w*|azure\s*ad|microsoft\s*identity)\b", lowered_added):
        provider = "Microsoft Entra ID"
    elif "okta" in lowered_added:
        provider = "Okta"
    elif "auth0" in lowered_added:
        provider = "Auth0"
    uses_sso = bool(re.search(r"\b(sso\w*|\w*claims?|id[_-]?token)\b", lowered_added))
    if local_fixture_removed and provider:
        previous = "Employee identity comes from a local employee directory fixture."
        current = f"Employee identity comes from {provider}"
        if uses_sso:
            current += " using authenticated SSO claims"
        current += "."
        implication = (
            f"Use {provider} identity claims for employee lookup; do not add employees to the "
            "removed local fixture."
        )
        added.append(
            ClaimDelta(
                claim="identity provider",
                current_value=f"The application's identity provider is {provider}.",
                confidence=0.97,
                affected_areas=files,
                agent_implication=implication,
                scope=scope,
                authority_tier="current_code_config",
            )
        )
        modified.append(
            ClaimDelta(
                claim="employee identity source",
                previous_value=previous,
                current_value=current,
                confidence=0.97,
                affected_areas=files,
                agent_implication=implication,
                scope=scope,
                authority_tier="current_code_config",
            )
        )
        removed.append(
            ClaimDelta(
                claim="local employee directory fixture",
                previous_value="The application uses a local employee directory fixture.",
                current_value="The local employee directory fixture is no longer authoritative.",
                confidence=0.96,
                affected_areas=files,
                agent_implication=implication,
                scope=scope,
                authority_tier="current_code_config",
            )
        )
        implications.append(implication)

    removed_assignments = _assignments(removed_lines)
    added_assignments = _assignments(added_lines)
    for key in sorted(set(removed_assignments).intersection(added_assignments)):
        if removed_assignments[key] == added_assignments[key]:
            continue
        if (
            local_fixture_removed
            and provider
            and any(marker in key for marker in ("employee", "identity", "directory", "entra"))
        ):
            continue
        if any(item.claim == f"{key} configuration" for item in modified):
            continue
        modified.append(
            ClaimDelta(
                claim=f"{key} configuration",
                previous_value=removed_assignments[key],
                current_value=added_assignments[key],
                confidence=0.86,
                affected_areas=files,
                agent_implication=f"Use the current {key} configuration before modifying this area.",
                scope=scope,
                authority_tier="current_code_config",
            )
        )

    change_count = len(added) + len(removed) + len(modified)
    summary = (
        f"Interpreted {change_count} source-backed belief change"
        f"{'s' if change_count != 1 else ''} across {len(files)} file"
        f"{'s' if len(files) != 1 else ''}."
        if change_count
        else "The diff changed source text, but no atomic company belief met the confidence threshold."
    )
    return ExtractedClaims(
        summary=summary,
        added=added,
        removed=removed,
        modified=modified,
        affected_areas=files,
        agent_implications=list(dict.fromkeys(implications)),
    )


class ChangeReconciler:
    def __init__(self, graph: GraphStore):
        self.graph = graph
        self.beliefs = BeliefStore(graph)
        self.authority = AuthorityResolver(self.beliefs)

    def reconcile(
        self,
        project_id: str,
        extracted: ExtractedClaims,
        source: dict[str, Any],
        default_scope: dict[str, str],
    ) -> dict[str, Any]:
        created: list[str] = []
        updated: list[str] = []
        invalidated: list[str] = []
        conflicts: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []

        for change in extracted.added:
            scope = {**default_scope, **change.scope}
            current = self.beliefs.get_current(project_id, change.claim, scope)
            candidate = self.beliefs.create(
                project_id,
                change.claim,
                change.current_value,
                confidence=change.confidence,
                scope=scope,
                authority_tier=change.authority_tier
                or AuthorityResolver.infer_tier(
                    str(source.get("type") or ""),
                    str((source.get("metadata") or {}).get("title") or ""),
                    source.get("metadata") or {},
                ),
                source=source,
            )
            created.append(candidate["id"])
            if current and current["id"] != candidate["id"]:
                resolution = self.authority.reconcile(current["id"], candidate["id"])
                conflicts.append(self._compact_resolution(resolution))
                relationships.append(resolution["relationship"])

        for change in extracted.modified:
            scope = {**default_scope, **change.scope}
            current = self.beliefs.get_current(project_id, change.claim, scope)
            tier = change.authority_tier or AuthorityResolver.infer_tier(
                str(source.get("type") or ""),
                str((source.get("metadata") or {}).get("title") or ""),
                source.get("metadata") or {},
            )
            if current:
                if (
                    current["current_value"].strip().casefold()
                    == change.current_value.strip().casefold()
                ):
                    # A different delivery may repeat the same semantic change.
                    # Attach the new provenance without creating a self-edge or
                    # pretending that company truth changed again.
                    self.beliefs.create(
                        project_id,
                        change.claim,
                        change.current_value,
                        confidence=max(change.confidence, float(current["confidence"])),
                        scope=scope,
                        authority_tier=tier,
                        source=source,
                    )
                    continue
                result = self.beliefs.update(
                    current["id"],
                    change.current_value,
                    relationship="UPDATES",
                    source=source,
                    confidence=change.confidence,
                    authority_tier=tier,
                    metadata={"agent_implication": change.agent_implication},
                )
                updated.append(result["current"]["id"])
                relationships.append(result["relationship"])
            else:
                belief = self.beliefs.create(
                    project_id,
                    change.claim,
                    change.current_value,
                    previous_value=change.previous_value,
                    confidence=change.confidence,
                    scope=scope,
                    authority_tier=tier,
                    source=source,
                )
                created.append(belief["id"])

        for change in extracted.removed:
            scope = {**default_scope, **change.scope}
            current = self.beliefs.get_current(project_id, change.claim, scope)
            if current:
                result = self.beliefs.update(
                    current["id"],
                    change.current_value,
                    relationship="INVALIDATES",
                    source=source,
                    confidence=change.confidence,
                    authority_tier=change.authority_tier or current["authority_tier"],
                    metadata={"agent_implication": change.agent_implication},
                )
                invalidated.append(current["id"])
                relationships.append(result["relationship"])
            else:
                belief = self.beliefs.create(
                    project_id,
                    change.claim,
                    change.current_value,
                    previous_value=change.previous_value,
                    confidence=change.confidence,
                    scope=scope,
                    authority_tier=change.authority_tier or "inferred_memory",
                    source=source,
                    status="invalidated",
                )
                invalidated.append(belief["id"])

        return {
            "created_belief_ids": list(dict.fromkeys(created)),
            "updated_belief_ids": list(dict.fromkeys(updated)),
            "invalidated_belief_ids": list(dict.fromkeys(invalidated)),
            "relationships": relationships,
            "conflicts": conflicts,
        }

    @staticmethod
    def _compact_resolution(value: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": value["outcome"],
            "winner_id": (value.get("winner") or {}).get("id", ""),
            "loser_id": (value.get("loser") or {}).get("id", ""),
            "requires_human_review": value["requires_human_review"],
            "intention_vs_reality": value["intention_vs_reality"],
            "reason": value["reason"],
            "explanation": value["explanation"],
        }


class ChangeIntelligenceService:
    STAGES = ("observed", "interpreting", "reconciling", "activating", "ready")

    def __init__(self, graph: GraphStore):
        self.graph = graph
        self.reconciler = ChangeReconciler(graph)

    def observe(
        self,
        project_id: str,
        delivery_id: str,
        event_type: str,
        repository: str,
        commit_sha: str,
        source_url: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        existing = row("SELECT * FROM semantic_change_events WHERE delivery_id=?", (delivery_id,))
        if existing:
            return decode(existing), False
        event_id = new_id("sce")
        now = utcnow()
        with connect() as conn:
            conn.execute(
                """INSERT INTO semantic_change_events
                (id,project_id,delivery_id,event_type,repository,commit_sha,source_url,status,
                 payload_json,interpretation_json,result_json,error,created_at,updated_at,completed_at)
                VALUES (?,?,?,?,?,?,?,'observed',?,'{}','{}','',?,?,NULL)""",
                (
                    event_id,
                    project_id,
                    delivery_id,
                    event_type,
                    repository,
                    commit_sha,
                    source_url,
                    json.dumps(payload),
                    now,
                    now,
                ),
            )
        event = self.get(event_id) or {}
        self.graph.upsert_node("SemanticChangeEvent", event)
        return event, True

    def process(
        self,
        event_id: str,
        diff: str,
        context: dict[str, Any],
        llm: ChangeProvider | None = None,
    ) -> dict[str, Any]:
        event = self.get(event_id)
        if not event:
            raise ValueError("Semantic change event not found")
        if event["status"] == "ready":
            return event
        stage_history = list((event.get("result") or {}).get("stage_history") or [])
        try:
            stage_history.append(self._set_status(event_id, "interpreting"))
            extracted = interpret_diff(diff, context, llm=llm)
            self._set_interpretation(event_id, extracted.model_dump())
            stage_history.append(self._set_status(event_id, "reconciling"))
            source = {
                "type": event["event_type"],
                "id": event["delivery_id"],
                "timestamp": event["created_at"],
                "confidence": 1.0,
                "metadata": {
                    "title": event["commit_sha"] or event["delivery_id"],
                    "repository": event["repository"],
                    "source_url": event["source_url"],
                    "affected_areas": extracted.affected_areas,
                },
            }
            scope = {
                "project": event["project_id"],
                "repo": event["repository"],
                **(context.get("scope") or {}),
            }
            reconciled = self.reconciler.reconcile(event["project_id"], extracted, source, scope)
            stage_history.append(self._set_status(event_id, "activating"))
            affected_profiles = [f"project:{event['project_id']}"]
            affected_profiles.extend(
                f"service:{value}"
                for value in {
                    change.scope.get("service", "")
                    for change in [
                        *extracted.added,
                        *extracted.modified,
                        *extracted.removed,
                    ]
                }
                if value
            )
            result = {
                **reconciled,
                "summary": extracted.summary,
                "affected_areas": extracted.affected_areas,
                "agent_implications": extracted.agent_implications,
                "affected_profiles": list(dict.fromkeys(affected_profiles)),
                "stage_history": stage_history,
                "counts": {
                    "added": len(reconciled["created_belief_ids"]),
                    "updated": len(reconciled["updated_belief_ids"]),
                    "invalidated": len(reconciled["invalidated_belief_ids"]),
                    "conflicts": len(reconciled["conflicts"]),
                },
            }
            stage_history.append({"stage": "ready", "at": utcnow()})
            result["stage_history"] = stage_history
            self._complete(event_id, result)
            for belief_id in {
                *reconciled["created_belief_ids"],
                *reconciled["updated_belief_ids"],
                *reconciled["invalidated_belief_ids"],
            }:
                self.graph.link(
                    "CHANGE_EVENT_CREATED_BELIEF",
                    "SemanticChangeEvent",
                    event_id,
                    "Belief",
                    belief_id,
                )
            return self.detail(event_id) or {}
        except Exception as exc:
            self._fail(event_id, str(exc))
            raise

    def list(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return [
            decode(item)
            for item in rows(
                """SELECT * FROM semantic_change_events WHERE project_id=?
                ORDER BY created_at DESC LIMIT ?""",
                (project_id, limit),
            )
        ]

    def get(self, event_id: str) -> dict[str, Any] | None:
        item = row("SELECT * FROM semantic_change_events WHERE id=?", (event_id,))
        return decode(item) if item else None

    def detail(self, event_id: str) -> dict[str, Any] | None:
        event = self.get(event_id)
        if not event:
            return None
        result = event.get("result") or {}
        relationships = result.get("relationships", [])
        relationship_ids = [
            value
            for relationship in relationships
            for value in (
                relationship.get("from_belief_id"),
                relationship.get("to_belief_id"),
            )
            if value
        ]
        ids = list(
            dict.fromkeys(
                [
                    *result.get("created_belief_ids", []),
                    *result.get("updated_belief_ids", []),
                    *result.get("invalidated_belief_ids", []),
                    *relationship_ids,
                ]
            )
        )
        beliefs = [self.reconciler.beliefs.get(value) for value in ids]
        return {**event, "beliefs": [value for value in beliefs if value]}

    def _set_status(self, event_id: str, status: str) -> dict[str, str]:
        at = utcnow()
        with connect() as conn:
            conn.execute(
                "UPDATE semantic_change_events SET status=?,updated_at=? WHERE id=?",
                (status, at, event_id),
            )
        return {"stage": status, "at": at}

    def _set_interpretation(self, event_id: str, interpretation: dict[str, Any]) -> None:
        with connect() as conn:
            conn.execute(
                "UPDATE semantic_change_events SET interpretation_json=?,updated_at=? WHERE id=?",
                (json.dumps(interpretation), utcnow(), event_id),
            )

    def _complete(self, event_id: str, result: dict[str, Any]) -> None:
        now = utcnow()
        with connect() as conn:
            conn.execute(
                """UPDATE semantic_change_events SET status='ready',result_json=?,error='',
                updated_at=?,completed_at=? WHERE id=?""",
                (json.dumps(result), now, now, event_id),
            )
        self.graph.upsert_node("SemanticChangeEvent", self.get(event_id) or {})

    def _fail(self, event_id: str, error: str) -> None:
        with connect() as conn:
            conn.execute(
                "UPDATE semantic_change_events SET status='failed',error=?,updated_at=? WHERE id=?",
                (error[:2000], utcnow(), event_id),
            )

    def fail(self, event_id: str, error: str) -> None:
        """Record an asynchronous processing failure without exposing storage internals."""
        self._fail(event_id, error)


def github_diff(payload: dict[str, Any], connector: Any) -> tuple[str, dict[str, Any]]:
    repository = str((payload.get("repository") or {}).get("full_name") or "")
    if not repository:
        raise ValueError("GitHub event does not identify a repository")
    if payload.get("pull_request"):
        number = int(payload["pull_request"]["number"])
        files = connector.pull_request_files(repository, number)
        commit_sha = str((payload["pull_request"].get("head") or {}).get("sha") or "")
    else:
        before, after = str(payload.get("before") or ""), str(payload.get("after") or "")
        comparison = connector.compare(repository, before, after)
        files = comparison.get("files") or []
        commit_sha = after
    diff = "\n".join(
        "\n".join(
            (
                f"diff --git a/{item.get('filename', '')} b/{item.get('filename', '')}",
                f"--- a/{item.get('previous_filename') or item.get('filename', '')}",
                f"+++ b/{item.get('filename', '')}",
                str(item.get("patch") or ""),
            )
        )
        for item in files
        if item.get("patch")
    )
    return diff, {"repository": repository, "commit_sha": commit_sha, "files": files}


def _changed_files(diff: str) -> list[str]:
    files = re.findall(r"^\+\+\+\s+b/(.+)$", diff, flags=re.MULTILINE)
    return list(dict.fromkeys(value.strip() for value in files if value.strip() != "/dev/null"))


def _change_lines(diff: str, prefix: Literal["+", "-"]) -> list[str]:
    blocked = ("+++", "---")
    return [
        line[1:].strip()
        for line in diff.splitlines()
        if line.startswith(prefix) and not line.startswith(blocked) and line[1:].strip()
    ]


def _assignments(lines: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for line in lines:
        match = re.search(r"(?:const|let|var)?\s*([A-Za-z_][\w.-]{2,})\s*[:=]\s*([^;,#}]+)", line)
        if match:
            output[match.group(1).casefold()] = match.group(2).strip().strip("'\"")
    return output
