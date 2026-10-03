from __future__ import annotations

import contextlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from app.audit import AuditService
from app.connectors.status import (
    PROVIDER_NAMES,
    is_connection_question,
    mentioned_providers,
    status_answer,
)
from app.core.config import settings
from app.core.database import decode, rows
from app.governance import ScopeService
from app.graph.base import GraphEvidence
from app.graph.graph_explainer import explain_paths
from app.graph.graph_ranker import query_intent
from app.hcag_adapter import HCAGAdapter
from app.hcag_adapter.models import RouteResult
from app.intelligence.correlation import correlate_changes
from app.intelligence.trust import trust_score
from app.llm import configured_model, model_runtime
from app.memory.beliefs import BeliefStore
from app.memory.brain import CompanyBrainService
from app.memory.change_intelligence import ChangeIntelligenceService
from app.memory.company import CompanyMemoryService
from app.outcomes import record_context
from app.swarm import ContextActivationSwarm

from . import continuity
from .clarify import clarification, clarification_answer
from .commits import CommitFetcher, commit_answer, commit_evidence, commit_references, find_commit
from .conversation import assistant_reply, general_knowledge_answer, is_company_question
from .deliberation import deliberate
from .handoff import build_handoff
from .hypotheses import extract_hypotheses
from .reasoner import (
    answer_intent,
    evidence_answer,
    stream_llm_answer,
    validate_grounded_answer,
)
from .universal import universal_evidence_answer

# Below this final confidence, the diagnostic hypothesis path engages (in addition
# to whenever the reasoner reports insufficient evidence). Prototype constant; can
# be promoted to settings once the loop is validated.
LOW_CONFIDENCE_THRESHOLD = 0.45


def _emit(
    on_event: Callable[[str, dict[str, Any]], None] | None, kind: str, payload: dict[str, Any]
) -> None:
    if not on_event:
        return
    # A broken listener must not break the answer it is narrating.
    with contextlib.suppress(Exception):
        on_event(kind, payload)


def _github_error(exc: Exception) -> str:
    """Why GitHub refused, in words — never a token or a raw response body."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in {401, 403}:
        return "the GitHub connection needs to be renewed"
    if status == 404:
        return "GitHub no longer has this commit, or the connection cannot see it"
    if "not connected" in str(exc).casefold():
        return "GitHub is not connected for this workspace"
    return "GitHub could not be reached"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _source_summary(evidence: list[GraphEvidence], limit: int = 3) -> str:
    """Where the passages came from, in words a person recognizes.

    Repository names when the search crossed repositories, file names otherwise.
    Scores, chunk ids and graph paths stay out: they are how the looking works,
    not what was found.
    """
    projects = list(
        dict.fromkeys(
            str(item.metadata.get("project_name") or "").strip()
            for item in evidence
            if str(item.metadata.get("project_name") or "").strip()
        )
    )
    names = (
        projects
        if len(projects) > 1
        else list(dict.fromkeys(item.source_title for item in evidence if item.source_title))
    )
    if not names:
        return ""
    shown = ", ".join(names[:limit])
    rest = len(names) - limit
    return f"From {shown}" + (f" and {rest} more" if rest > 0 else "")


def should_run_diagnostics(route: RouteResult, sufficient: bool, confidence: float) -> bool:
    """Gate the Stage 1 diagnostic path to the service-down failure class only."""
    if not (route.service_name and route.subdomain == "incident_response"):
        return False
    return (not sufficient) or confidence < LOW_CONFIDENCE_THRESHOLD


class RetrievalService:
    def __init__(self, hcag: HCAGAdapter, audit: AuditService | None = None):
        self.hcag = hcag
        self.audit = audit or AuditService()
        self.swarm = ContextActivationSwarm(hcag)

    def ask(
        self,
        project_id: str,
        query: str,
        workspace_project_ids: list[str] | None = None,
        *,
        principal: dict[str, Any] | None = None,
        allowed_team_ids: list[str] | None = None,
        token_budget: int = 6000,
        model_provider: str | None = None,
        surface: str = "api",
        scope: str = "auto",
        history: list[dict[str, Any]] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        commit_fetcher: CommitFetcher | None = None,
    ) -> dict[str, Any]:
        """Answer ``query`` from company memory.

        ``on_event`` turns the call into a narrated one: each stage is reported as
        a plain-language ``step`` as it starts, and a model-written answer arrives
        as ``text`` (and ``thinking``) pieces while it is being written. Without a
        listener the answer is deliberated across several candidates as before.
        Listener failures are swallowed — narrating must never break an answer.

        ``commit_fetcher(repository, sha)`` lets a question that names a known
        commit be answered with the commit's actual change, read live from GitHub.
        """

        def step(label: str, detail: str = "") -> None:
            _emit(on_event, "step", {"label": label, "detail": detail})

        # "hello" is not a retrieval failure. Conversational turns are answered
        # before any lane runs, so they never consume evidence or a model call.
        conversational = assistant_reply(query)
        if conversational:
            return self._unsourced_result(
                project_id,
                query,
                conversational,
                model_provider,
                principal=principal,
                surface=surface,
            )
        # A follow-up is bound to the thread before retrieval sees it. "Why is it
        # failing" must search for the subject the asker has in mind, and when
        # there is no such subject it must be asked about rather than guessed at:
        # every corpus contains an incident, so guessing always finds one.
        asked = query
        thread = continuity.resolve(query, history)
        if thread["dangling"]:
            return self._clarification_result(
                project_id,
                query,
                continuity.unresolved_reference(query),
                model_provider,
                principal=principal,
                surface=surface,
            )
        query = thread["query"]
        if thread.get("subject"):
            step("Reading this as a follow-up", f"About {thread['subject']}")
        route = self.hcag.route_query(project_id, query)
        memory_service = CompanyMemoryService(self.hcag.graph)
        scope_service = ScopeService()
        brain = CompanyBrainService(self.hcag.graph)
        current_memories = memory_service.list(
            project_id, latest=True, limit=500, allowed_team_ids=allowed_team_ids
        )
        answer_shape = answer_intent(query)
        source_sweep_intents = {"runtime_record", "slack_messages"}
        # "Why is the Drive connection failing?" is about the system's live
        # state, which no document records. Read the connection records instead.
        workspace_id = str((principal or {}).get("active_workspace_id") or "")
        status_grounding = None
        if workspace_id and is_connection_question(query):
            providers = mentioned_providers(query)
            step(
                "Checking live connection records",
                ", ".join(PROVIDER_NAMES.get(item, item) for item in providers)
                or "Every source",
            )
            status_grounding = status_answer(workspace_id, providers, query)
        # A question naming a commit is answered with that commit, before any
        # search: the SHA identifies it exactly, and a search for a hash finds noise.
        commit_grounding = (
            None
            if status_grounding
            else self._ground_from_commit(
                query,
                sorted({project_id, *(workspace_project_ids or [])}),
                commit_fetcher,
                step,
            )
        )
        memory_grounding = None
        if not commit_grounding and not status_grounding:
            step("Checking approved company memory")
            belief_grounding = (
                None
                if answer_shape in source_sweep_intents
                else self._ground_from_beliefs(project_id, query)
            )
            memory_grounding = belief_grounding or (
                None
                if answer_shape in source_sweep_intents
                else self._ground_from_memories(project_id, query, current_memories, memory_service)
            )
        if status_grounding:
            grounded, evidence, searched_projects = status_grounding, [], []
            selected_memories = []
            intent = "connection_status"
        elif commit_grounding:
            grounded, evidence, searched_projects = commit_grounding
            selected_memories = []
            intent = "commit_lookup"
        elif memory_grounding:
            grounded, evidence, selected_memories = memory_grounding
            project = rows("SELECT id,name,repository FROM projects WHERE id=?", (project_id,))
            searched_projects = [
                {
                    "project_id": item["id"],
                    "project_name": item["name"],
                    "repository": item["repository"],
                }
                for item in project
            ]
            intent = str(grounded.get("answer_kind") or "memory")
            step("Found approved memory that answers this", _plural(len(evidence), "record"))
        else:
            step(
                "Searching connected sources",
                "Every connected repository" if scope == "workspace" else "",
            )
            evidence, searched_projects = self._workspace_evidence(
                project_id,
                query,
                route,
                workspace_project_ids,
                # "workspace" is the default the chat sends: someone asking their
                # company a question is not thinking about which repository holds
                # the answer. "project" keeps the old hard boundary.
                force_workspace=scope == "workspace",
                pin_project=scope == "project",
                principal_id=str((principal or {}).get("id") or "anonymous"),
                allowed_team_ids=allowed_team_ids,
                token_budget=token_budget,
            )
            evidence = self._security_trim_evidence(
                evidence, scope_service, project_id, allowed_team_ids
            )
            step(
                f"Read {_plural(len(evidence), 'relevant passage')}",
                _source_summary(evidence),
            )
            # Structured repository questions are answered deterministically from
            # manifests and code evidence. Free-form operational questions may use
            # the configured LLM, with the extractive reasoner as the safe fallback.
            deterministic = evidence_answer(query, evidence)
            intent = answer_shape
            # Structured extractors remain precision optimizations. They are no
            # longer a whitelist: any unrecognized or insufficient shape falls
            # through to the same evidence-grounded synthesis path.
            if intent in {"general", "api_contract"} and not deterministic["sufficient"]:
                fallback = universal_evidence_answer(query, evidence)
                deterministic = fallback if fallback["sufficient"] else deterministic
            if intent in {"general", "overview", "slack_messages"}:
                grounded = (
                    self._synthesize(query, evidence, route, model_provider, on_event)
                    or deterministic
                )
            else:
                step("Pulling the answer straight from the sources")
                grounded = deterministic
            selected_memories = []
            if (
                not grounded["sufficient"]
                # A pinned project decides where to look first, not where to give
                # up: coming back empty because the guess was narrow is worse than
                # widening. Widening only affects the answer — build_handoff is
                # given the pin, so a task can never follow the search out of the
                # repository that was actually chosen.
                and len(workspace_project_ids or []) > 1
                and self._allow_automatic_workspace_fallback(query, intent)
                and len(searched_projects) <= 1
            ):
                step("Nothing conclusive here — widening to every connected repository")
                previous_compiled_context = route.plan.get("_compiled_context")
                previous_active_run = route.plan.get("_active_activation_run_id")
                expanded_evidence, expanded_projects = self._workspace_evidence(
                    project_id,
                    query,
                    route,
                    workspace_project_ids,
                    force_workspace=True,
                    principal_id=str((principal or {}).get("id") or "anonymous"),
                    allowed_team_ids=allowed_team_ids,
                    token_budget=token_budget,
                )
                expanded_evidence = self._security_trim_evidence(
                    expanded_evidence, scope_service, project_id, allowed_team_ids
                )
                expanded = evidence_answer(query, expanded_evidence)
                if intent == "general" and not expanded["sufficient"]:
                    universal = universal_evidence_answer(query, expanded_evidence)
                    expanded = universal if universal["sufficient"] else expanded
                if expanded["sufficient"]:
                    evidence = expanded_evidence
                    searched_projects = expanded_projects
                    grounded = (
                        self._synthesize(query, expanded_evidence, route, model_provider, on_event)
                        or expanded
                        if intent == "general"
                        else expanded
                    )
                else:
                    route.plan["_compiled_context"] = previous_compiled_context
                    route.plan["_active_activation_run_id"] = previous_active_run
        if memory_grounding:
            report = self.swarm.specialist_report(
                "current_truth_fast_path",
                "already-resolved belief or atomic-memory grounding",
                project_id,
                evidence,
                allowed_team_ids=allowed_team_ids,
            )
            activation = self.swarm.compile(
                project_id,
                query,
                [report],
                principal_id=str((principal or {}).get("id") or "anonymous"),
                token_budget=token_budget,
            )
            evidence = activation.evidence
            self._attach_activation(route, activation)
        step("Checking the answer against its sources")
        grounded = validate_grounded_answer(query, grounded, evidence)
        # Company memory holding nothing is only a refusal when the question was
        # about the company. A basic question the world already knows the answer
        # to gets answered — labelled as general knowledge, never as company truth.
        if (
            not grounded["sufficient"]
            and settings.org_memory_general_knowledge_enabled
            and not is_company_question(query)
        ):
            step("Not in company memory — answering from general knowledge")
            general = general_knowledge_answer(query, model_provider=model_provider)
            if general:
                grounded = general
                evidence = []
        if grounded["sufficient"]:
            supporting_ids = set(grounded.get("supporting_chunk_ids") or [])
            evidence = (
                [item for item in evidence if item.chunk_id in supporting_ids]
                if supporting_ids
                else self._answer_evidence(query, evidence)
            )
        trace = asdict(self.hcag.build_retrieval_trace(project_id, query, evidence, route=route))
        grouped_chunk_ids: dict[str, list[str]] = {}
        for item in evidence:
            source_project_id = str(item.metadata.get("project_id") or project_id)
            grouped_chunk_ids.setdefault(source_project_id, []).append(item.chunk_id)
        graph_paths: list[dict[str, Any]] = []
        for source_project_id, chunk_ids in grouped_chunk_ids.items():
            for path in self.hcag.graph.get_retrieval_trace(source_project_id, chunk_ids):
                graph_paths.append({"project_id": source_project_id, **path})
        for item in evidence:
            source_project_id = str(item.metadata.get("project_id") or project_id)
            for edge in item.metadata.get("graph_path_edges", []):
                graph_paths.append(
                    {
                        "project_id": source_project_id,
                        "evidence_chunk_id": item.chunk_id,
                        **edge,
                    }
                )
        trace["graph_paths"] = graph_paths
        trace["graph_path_explanations"] = explain_paths(trace.get("graph_paths", []))
        trace["universal_query_plan"] = next(
            (
                item.metadata["universal_query_plan"]
                for item in evidence
                if item.metadata.get("universal_query_plan")
            ),
            {},
        )
        trace["searched_projects"] = searched_projects
        named_target = next(
            (item for item in searched_projects if str(item.get("project_id") or "") != project_id),
            None,
        )
        workspace_scope = (
            self._should_expand_workspace(query, intent)
            or bool(named_target)
            or len(searched_projects) > 1
        )
        trace["scope_mode"] = "workspace" if workspace_scope else "project"
        if named_target and len(searched_projects) == 1:
            trace["scope_reason"] = (
                f"The named repository was resolved to {named_target['project_name']} before retrieval."
            )
        elif workspace_scope:
            trace["scope_reason"] = "The question explicitly requested cross-repository evidence."
        else:
            trace["scope_reason"] = "The active repository was kept as a hard retrieval boundary."
        trace["source_projects"] = list(
            dict.fromkeys(
                str(item.metadata.get("project_name") or item.metadata.get("project_id") or "")
                for item in evidence
            )
        )
        trace["authorized_team_ids"] = allowed_team_ids or []
        trace["security_trimmed"] = allowed_team_ids is not None
        trace["context_selection_policy"] = (
            "authorized scope ∩ task relevance ∩ current memory ∩ graph neighborhood ∩ token budget"
        )
        citations = self._citations(evidence) if grounded["sufficient"] else []
        service_counts = Counter(name for item in evidence[:8] for name in item.service_names)
        services = sorted(
            name
            for name, count in service_counts.items()
            if count >= 2 or name == route.service_name
        )
        answer_scope = str(grounded.get("answer_scope") or "company_memory")
        confidence = (
            trace["confidence"] if grounded["sufficient"] else min(trace["confidence"], 0.25)
        )
        if answer_scope in {"general_knowledge", "system_state"}:
            # Graph confidence measures evidence support, and there is none here
            # by construction. Report the trust the general-knowledge lane claims.
            confidence = float(grounded.get("trust_score", {}).get("score") or 0.5)
        runbooks = self._suggested_runbooks(project_id, query, services)
        related = (
            self._related_sources(evidence)
            if grounded["sufficient"]
            else {"files": [], "issues": [], "pull_requests": [], "slack_messages": []}
        )
        trust = (
            grounded.get("trust_score") or trust_score(project_id, evidence)
            if grounded["sufficient"]
            else {
                "score": 0.0,
                "level": "none",
                "reason": "No sufficiently supported answer, so no trust is asserted.",
                "factors": {},
                "contradictions": [],
            }
        )
        trust = self._calibrate_trust(trust, confidence)
        result = {
            "answer": grounded["answer"],
            "answer_sufficient": bool(grounded["sufficient"]),
            "likely_cause": grounded["likely_cause"],
            "confidence": confidence,
            "trust_score": trust,
            "related_services": services,
            "related_files": related["files"],
            "related_issues": related["issues"],
            "related_pull_requests": related["pull_requests"],
            "related_slack_messages": related["slack_messages"],
            "evidence": citations,
            "retrieval_trace": trace,
            "suggested_runbooks": runbooks,
            "safe_actions": grounded.get("safe_actions", []),
            "approval_required": grounded.get("approval_required", []),
            "answer_kind": intent,
            "answer_scope": answer_scope,
            # How many sources were actually looked through. A plain count, so a
            # chat can say "I searched 19 sources and found nothing" without
            # reaching into the retrieval trace — which is mechanics, and stays
            # out of the answer surface.
            "searched_sources": len(searched_projects),
            "diagnostic": route.subdomain == "incident_response",
            "model": model_runtime(
                model_provider,
                str(grounded.get("_model_provider") or "") or None,
            ),
        }
        if grounded.get("_deliberation"):
            result["deliberation"] = grounded["_deliberation"]
        handoff = build_handoff(
            query,
            grounded,
            evidence,
            pinned_project_id=project_id if scope == "project" else "",
        )
        if handoff:
            result["handoff"] = handoff
        # Only a request that would actually change something is worth stopping for.
        # A pinned project has already answered "which repository", so that half is
        # dropped — asking again would be the assistant ignoring the reply it was
        # given. "What should it become" survives pinning, because choosing a
        # repository says nothing about what the change is.
        ambiguity = clarification(query, evidence, actionable=bool(handoff))
        if ambiguity and scope == "project" and ambiguity["reason"] != "missing_target":
            ambiguity = None
        if ambiguity:
            result.pop("handoff", None)
            result["clarification"] = ambiguity
            result["answer"] = clarification_answer(ambiguity)["answer"]
            result["answer_scope"] = "clarification"
            result["answer_kind"] = "clarification"
        if not grounded["sufficient"]:
            selected_memories = []
        elif not selected_memories:
            evidence_source_ids = {str(item.metadata.get("source_id") or "") for item in evidence}
            selected_memories = [
                memory
                for memory in current_memories
                if evidence_source_ids.intersection(memory.get("source_ids", []))
            ]
        selected_memories = self._filter_memories_for_answer(intent, selected_memories)
        result.update(
            {
                "memory_profile_used": f"project:{project_id}",
                "memory_units": selected_memories,
                "related_entities": sorted(set(services)),
                "updates": memory_service.relationships(project_id, "UPDATES"),
                "conflicts": memory_service.relationships(
                    project_id, "CONTRADICTS", allowed_team_ids
                ),
            }
        )
        result["updates"] = memory_service.relationships(project_id, "UPDATES", allowed_team_ids)
        if grounded.get("memory_layer") == "belief_ledger":
            belief_store = BeliefStore(self.hcag.graph)
            result["updates"] = belief_store.relationships(project_id, "UPDATES")
            result["conflicts"] = belief_store.relationships(project_id, "CONTRADICTS")
            result["invalidations"] = belief_store.relationships(project_id, "INVALIDATES")
            result["semantic_change"] = grounded.get("semantic_change", {})
        envelope = brain.create_context_envelope(
            project_id,
            query,
            intent,
            str((principal or {}).get("id") or "anonymous"),
            allowed_team_ids,
            selected_memories,
            [item.chunk_id for item in evidence],
            sorted(set(services)),
            trace,
            token_budget,
            evidence_source_ids=list(
                dict.fromkeys(
                    [
                        *(
                            str(item.metadata.get("source_id") or "")
                            for item in evidence
                            if item.metadata.get("source_id")
                        ),
                        *list((route.plan.get("_compiled_context") or {}).get("source_ids", [])),
                    ]
                )
            ),
            compiled_context=route.plan.get("_compiled_context") or {},
            activation_run_ids=[
                str(item.get("run_id") or "")
                for item in route.plan.get("_swarm_runs", [])
                if item.get("run_id")
            ],
        )
        result["context_envelope"] = envelope
        if route.subdomain == "incident_response" and evidence:
            correlation = correlate_changes(project_id, evidence, route.service_name)
            if correlation["suspects"]:
                result["change_correlation"] = correlation
        if should_run_diagnostics(route, grounded["sufficient"], confidence):
            hypotheses = extract_hypotheses(
                self.hcag.graph, project_id, route.service_name, evidence
            )
            result["hypotheses"] = hypotheses
            self.audit.record(
                "diagnostic.hypotheses_generated",
                query,
                project_id,
                payload={
                    "count": len(hypotheses),
                    "hypothesis_ids": [item["id"] for item in hypotheses],
                    "confidence": confidence,
                },
            )
        self.audit.record(
            "query.answered",
            query,
            project_id,
            payload={
                "confidence": confidence,
                "evidence_ids": [item.chunk_id for item in evidence],
                "answer": grounded["answer"],
            },
        )
        # The served half of the outcome loop. The id goes back to the caller so
        # whatever happens next — a handoff pasted into an editor, a rejected
        # answer, a merged PR — can be attributed to the context that caused it.
        # Recorded as the person typed it, not as retrieval rewrote it: the
        # training corpus should reflect how people really ask.
        result["context_event_id"] = record_context(
            project_id=project_id,
            query=asked,
            result=result,
            workspace_id=str((principal or {}).get("active_workspace_id") or ""),
            principal_id=str((principal or {}).get("id") or ""),
            surface=surface,
        )
        if thread["subject"]:
            # Surfaced so the chat can show what the follow-up was bound to, and
            # so a wrong binding is visible rather than silent.
            result["resolved_subject"] = thread["subject"]
        return result

    @staticmethod
    def _ground_from_commit(
        query: str,
        project_ids: list[str],
        fetcher: CommitFetcher | None,
        step: Callable[..., None],
    ) -> tuple[dict[str, Any], list[GraphEvidence], list[dict[str, Any]]] | None:
        for prefix in commit_references(query):
            record = find_commit(prefix, project_ids)
            if not record:
                continue
            project = rows(
                "SELECT id,name,repository FROM projects WHERE id=?", (record["project_id"],)
            )
            name = str(project[0]["name"]) if project else record["slug"]
            step(f"Found commit {record['sha'][:12]}", f"In {record['slug']}")
            fetched: dict[str, Any] | None = None
            error = ""
            if fetcher:
                step("Fetching the change from GitHub")
                try:
                    fetched = fetcher(record["slug"], record["sha"])
                except Exception as exc:  # noqa: BLE001 - the stored record still answers
                    error = _github_error(exc)
            else:
                error = "GitHub is not available to this caller"
            grounded = commit_answer(record, fetched, error)
            searched = [
                {
                    "project_id": record["project_id"],
                    "project_name": name,
                    "repository": project[0]["repository"] if project else "",
                }
            ]
            return grounded, [commit_evidence(record, name)], searched
        return None

    def _synthesize(
        self,
        query: str,
        evidence: list[GraphEvidence],
        route: RouteResult,
        model_provider: str | None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """Grounded synthesis, deliberated across several readings of the question.

        With a listener the answer is written once and streamed instead: someone
        watching words appear needs the answer they see to be the answer they get,
        which a judge choosing between hidden candidates afterwards cannot give.
        """
        if on_event:
            model = configured_model(model_provider)
            if not model:
                return None
            # A second synthesis (after widening the search) replaces the first
            # draft rather than appending to it.
            _emit(on_event, "draft_reset", {})
            _emit(
                on_event,
                "step",
                {"label": "Writing the answer", "detail": f"{model.label} · {model.model}"},
            )
            return stream_llm_answer(
                query,
                evidence,
                compiled_context=route.plan.get("_compiled_context"),
                model_provider=model_provider,
                on_text=lambda text: _emit(on_event, "text", {"text": text}),
                on_reasoning=lambda text: _emit(on_event, "thinking", {"text": text}),
            )
        return deliberate(
            query,
            evidence,
            compiled_context=route.plan.get("_compiled_context"),
            model_provider=model_provider,
            candidate_count=settings.org_memory_answer_candidates,
            judge_enabled=settings.org_memory_answer_judge_enabled,
            deadline_seconds=settings.org_memory_answer_deadline_seconds,
        )

    def _clarification_result(
        self,
        project_id: str,
        query: str,
        ambiguity: dict[str, Any],
        model_provider: str | None,
        *,
        principal: dict[str, Any] | None = None,
        surface: str = "api",
    ) -> dict[str, Any]:
        """A question asked back, shaped like any other answer.

        Reuses the unsourced path because a clarification legitimately cites
        nothing — there is no evidence behind "what does 'it' mean", and pretending
        otherwise would put sources under a sentence they did not support.
        """
        result = self._unsourced_result(
            project_id,
            query,
            clarification_answer(ambiguity),
            model_provider,
            principal=principal,
            surface=surface,
        )
        result["clarification"] = ambiguity
        result["answer_scope"] = "clarification"
        result["answer_kind"] = "clarification"
        return result

    def _unsourced_result(
        self,
        project_id: str,
        query: str,
        grounded: dict[str, Any],
        model_provider: str | None,
        *,
        principal: dict[str, Any] | None = None,
        surface: str = "api",
    ) -> dict[str, Any]:
        """Full response shape for answers that legitimately cite nothing.

        Conversational turns still return every field a caller expects, so no
        client needs a second code path for them.
        """
        trust = grounded.get("trust_score") or {}
        self.audit.record(
            "query.answered",
            query,
            project_id,
            payload={
                "confidence": float(trust.get("score") or 0.0),
                "evidence_ids": [],
                "answer": grounded["answer"],
                "answer_scope": grounded.get("answer_scope"),
            },
        )
        result = {
            "answer": grounded["answer"],
            "answer_sufficient": True,
            "likely_cause": grounded["likely_cause"],
            "confidence": float(trust.get("score") or 0.0),
            "trust_score": trust,
            "related_services": [],
            "related_files": [],
            "related_issues": [],
            "related_pull_requests": [],
            "related_slack_messages": [],
            "evidence": [],
            "retrieval_trace": {
                "query": query,
                "engine": "conversation",
                "confidence": float(trust.get("score") or 0.0),
                "graph_paths": [],
                "searched_projects": [],
                "scope_mode": "none",
                "scope_reason": "No retrieval was needed to answer this.",
            },
            "suggested_runbooks": [],
            "safe_actions": [],
            "approval_required": [],
            "answer_kind": "conversation",
            "answer_scope": str(grounded.get("answer_scope") or "assistant"),
            "diagnostic": False,
            "model": model_runtime(
                model_provider, str(grounded.get("_model_provider") or "") or None
            ),
            "memory_profile_used": f"project:{project_id}",
            "memory_units": [],
            "related_entities": [],
            "updates": [],
            "conflicts": [],
            "context_envelope": {},
        }
        result["context_event_id"] = record_context(
            project_id=project_id,
            query=query,
            result=result,
            workspace_id=str((principal or {}).get("active_workspace_id") or ""),
            principal_id=str((principal or {}).get("id") or ""),
            surface=surface,
        )
        return result

    def _ground_from_beliefs(
        self, project_id: str, query: str
    ) -> tuple[dict[str, Any], list[GraphEvidence], list[dict[str, Any]]] | None:
        """Answer semantic change questions from the append-only belief ledger.

        Commit metadata may establish chronology, but it cannot displace a
        directly matching old→new belief chain when the question asks what a
        source change meant.
        """
        lowered = " ".join(query.casefold().replace("-", " ").split())
        change_markers = (
            "what changed about",
            "what became true",
            "true before",
            "true now",
            "belief was invalidated",
            "belief invalidated",
            "affected files",
            "agent should do differently",
            "coding agent should do differently",
            "semantic change",
        )
        if not any(marker in lowered for marker in change_markers):
            return None

        events = [
            decode(item)
            for item in rows(
                """SELECT * FROM semantic_change_events WHERE project_id=? AND status='ready'
                ORDER BY completed_at DESC,created_at DESC LIMIT 50""",
                (project_id,),
            )
        ]
        if not events:
            return None

        stop = {
            "about",
            "affected",
            "agent",
            "before",
            "belief",
            "changed",
            "cite",
            "coding",
            "commit",
            "differently",
            "explain",
            "files",
            "invalidated",
            "latest",
            "should",
            "source",
            "true",
            "what",
            "which",
            "with",
        }
        query_terms = {
            token for token in re.findall(r"[a-z][a-z0-9_]{2,}", lowered) if token not in stop
        }
        service = ChangeIntelligenceService(self.hcag.graph)
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for position, event in enumerate(events):
            detail = service.detail(event["id"]) or event
            searchable = json.dumps(
                {
                    "interpretation": detail.get("interpretation", {}),
                    "result": detail.get("result", {}),
                    "beliefs": detail.get("beliefs", []),
                    "repository": detail.get("repository", ""),
                }
            ).casefold()
            overlap = sum(term in searchable for term in query_terms)
            claim_match = sum(
                1
                for belief in detail.get("beliefs", [])
                if any(term in str(belief.get("claim") or "").casefold() for term in query_terms)
            )
            score = overlap + claim_match * 3
            if score:
                candidates.append((score, f"{9999 - position:04d}", detail))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        detail = candidates[0][2]
        return self._semantic_change_grounding(detail)

    @staticmethod
    def _semantic_change_grounding(
        event: dict[str, Any],
    ) -> tuple[dict[str, Any], list[GraphEvidence], list[dict[str, Any]]]:
        result = event.get("result") or {}
        beliefs = {belief["id"]: belief for belief in event.get("beliefs", [])}
        relationships = result.get("relationships") or []
        citation = f"[Commit {(event.get('commit_sha') or event['delivery_id'])[:8]}]"
        sections = [
            "**Relevant semantic change**",
            (
                f"The latest recorded change relevant to this question is commit "
                f"`{(event.get('commit_sha') or event['delivery_id'])[:8]}`. {citation}"
            ),
        ]
        transitions: list[str] = []
        related_ids: set[str] = set()
        for relationship in relationships:
            previous = beliefs.get(relationship.get("from_belief_id"))
            current = beliefs.get(relationship.get("to_belief_id"))
            if not previous or not current:
                continue
            related_ids.update((previous["id"], current["id"]))
            kind = relationship.get("relationship")
            if kind == "UPDATES":
                transitions.append(
                    f"- **Before:** {previous['current_value']}\n"
                    f"  **Now:** {current['current_value']} {citation}"
                )
            elif kind == "INVALIDATES":
                transitions.append(
                    f"- **Invalidated belief:** {previous['current_value']} "
                    f"The current ledger state is: {current['current_value']} {citation}"
                )
            else:
                transitions.append(
                    f"- **Conflict:** {previous['current_value']} versus "
                    f"{current['current_value']} {citation}"
                )
        if transitions:
            sections.extend(("\n**Belief history**", "\n".join(transitions)))

        created_ids = set(result.get("created_belief_ids") or []) - related_ids
        created = [beliefs[value] for value in created_ids if value in beliefs]
        if created:
            sections.extend(
                (
                    "\n**New facts**",
                    "\n".join(f"- {belief['current_value']} {citation}" for belief in created),
                )
            )
        areas = result.get("affected_areas") or []
        if areas:
            sections.extend(
                (
                    "\n**Affected files**",
                    ", ".join(f"`{area}`" for area in areas) + f". {citation}",
                )
            )
        implications = result.get("agent_implications") or []
        if implications:
            sections.extend(
                (
                    "\n**What an AI coding agent should do differently**",
                    "\n".join(f"- {value} {citation}" for value in implications),
                )
            )

        selected = []
        for belief in beliefs.values():
            if belief["id"] not in related_ids | created_ids:
                continue
            selected.append(
                {
                    **belief,
                    "type": "belief",
                    "subject": belief["claim"],
                    "content": belief["current_value"],
                    "source_ids": [
                        source.get("source_id", "")
                        for source in belief.get("supporting_sources", [])
                        if source.get("source_id")
                    ],
                }
            )
        confidence = max((float(item.get("confidence") or 0) for item in selected), default=0.0)
        source_title = f"Commit {(event.get('commit_sha') or event['delivery_id'])[:8]}: semantic belief change"
        evidence_text = "\n".join(
            [
                str(result.get("summary") or "Semantic company-memory change."),
                *(f"{item['claim']}: {item['current_value']}" for item in selected),
                *(f"Affected file: {area}" for area in areas),
                *(f"Agent implication: {value}" for value in implications),
            ]
        )
        evidence = [
            GraphEvidence(
                chunk_id=f"semantic-change:{event['id']}",
                text=evidence_text,
                source_type=event.get("event_type") or "github_commit",
                source_title=source_title,
                source_url=event.get("source_url") or "",
                metadata={
                    "source_id": event["delivery_id"],
                    "project_id": event["project_id"],
                    "repository": event.get("repository") or "",
                    "commit_sha": event.get("commit_sha") or "",
                    "retrieval_confidence": confidence,
                    "primary_lane": "belief_ledger",
                    "semantic_change_event_id": event["id"],
                },
                score=confidence * 100,
            )
        ]
        grounded = {
            "answer": "\n".join(sections),
            "likely_cause": "Not applicable — this is a semantic company-memory change.",
            "safe_actions": implications,
            "approval_required": [],
            "sufficient": bool(selected and event.get("source_url")),
            "supporting_chunk_ids": [evidence[0].chunk_id],
            "answer_kind": "semantic_change",
            "memory_layer": "belief_ledger",
            "semantic_change": {
                "event_id": event["id"],
                "commit_sha": event.get("commit_sha") or "",
                "affected_areas": areas,
                "agent_implications": implications,
                "relationships": relationships,
            },
        }
        return grounded, evidence, selected

    @staticmethod
    def _filter_memories_for_answer(
        intent: str, memories: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Keep the response's matching memories aligned with its answer facet."""

        if intent in {"ownership", "entity_ownership"}:
            return [memory for memory in memories if memory.get("type") == "ownership"]
        if intent in {"commit_history", "recent_changes"}:
            return [
                memory
                for memory in memories
                if re.search(
                    r"\b(?:commit|committed|changed|change)\b",
                    f"{memory.get('subject', '')} {memory.get('content', '')}",
                    re.I,
                )
            ]
        if intent == "runtime_record":
            return []
        return memories

    @staticmethod
    def _security_trim_evidence(
        evidence: list[GraphEvidence],
        scope_service: ScopeService,
        project_id: str,
        allowed_team_ids: list[str] | None,
    ) -> list[GraphEvidence]:
        if allowed_team_ids is None:
            return evidence
        grouped: dict[str, set[str]] = {}
        for item in evidence:
            source_project = str(item.metadata.get("project_id") or project_id)
            source_id = str(item.metadata.get("source_id") or "")
            if source_id:
                grouped.setdefault(source_project, set()).add(source_id)
        visible: dict[str, set[str]] = {
            source_project: scope_service.visible_source_ids(
                source_project, source_ids, allowed_team_ids
            )
            for source_project, source_ids in grouped.items()
        }
        return [
            item
            for item in evidence
            if not str(item.metadata.get("source_id") or "")
            or str(item.metadata.get("source_id") or "")
            in visible.get(str(item.metadata.get("project_id") or project_id), set())
        ]

    def _ground_from_memories(
        self,
        project_id: str,
        query: str,
        memories: list[dict[str, Any]],
        memory_service: CompanyMemoryService,
    ) -> tuple[dict[str, Any], list[GraphEvidence], list[dict[str, Any]]] | None:
        """Prefer atomic memory for direct organizational-memory questions.

        This gate is intentionally lexical and type-aware. A weak semantic hit
        may not displace a directly matching current policy or decision.
        """
        if answer_intent(query) in {
            "agent_briefing",
            "api_contract",
            "commit_history",
            "ownership",
            "recent_changes",
            "repository_identity",
            "runtime_record",
        }:
            return None
        lowered = " ".join(query.casefold().replace("-", " ").split())
        memory_question = any(
            marker in lowered
            for marker in (
                "current",
                "policy",
                "decision",
                "decided",
                "who owns",
                "ownership",
                "depends on",
                "dependency",
                "what changed",
                "conflict",
                "convention",
                "what does the company know",
                "what should the ai remember",
            )
        )
        if not memory_question or not memories:
            return None
        stop = {
            "what",
            "which",
            "should",
            "agent",
            "follow",
            "source",
            "current",
            "company",
            "memory",
            "about",
            "does",
            "know",
            "have",
            "that",
            "this",
            "with",
            "from",
            "and",
            "the",
            "was",
            "were",
            "are",
            "for",
            "recently",
            "changed",
        }
        query_terms = {
            token for token in re.findall(r"[a-z][a-z0-9_]{2,}", lowered) if token not in stop
        }
        type_markers = {
            "policy": ("policy", "must", "allowed", "prohibited"),
            "decision": ("decision", "decided", "chose", "changed"),
            "ownership": ("owner", "owns", "ownership", "responsible"),
            "dependency": ("depend", "dependency", "requires"),
            "convention": ("convention", "usually", "standard"),
            "config": ("configuration", "config", "setting"),
        }
        ranked: list[tuple[float, dict[str, Any]]] = []
        for memory in memories:
            searchable = " ".join(
                f"{memory.get('subject', '')} {memory.get('content', '')}".casefold()
                .replace("-", " ")
                .split()
            )
            overlap = sum(term in searchable for term in query_terms)
            type_bonus = (
                3
                if any(marker in lowered for marker in type_markers.get(memory["type"], ()))
                else 0
            )
            # A type word alone (for example "policy") must not select an
            # unrelated memory when the question names a subject. This was the
            # source of cross-topic answers such as media policy appearing in
            # an employee-identity question.
            subject_terms = query_terms - {
                "policy",
                "decision",
                "decided",
                "owner",
                "ownership",
                "dependency",
                "configuration",
                "config",
                "convention",
            }
            if subject_terms and not any(term in searchable for term in subject_terms):
                continue
            score = overlap * 2 + type_bonus + float(memory.get("confidence") or 0)
            if score >= 2.75:
                ranked.append((score, memory))
        ranked.sort(key=lambda item: (item[0], item[1].get("confidence", 0)), reverse=True)
        selected = [memory for _, memory in ranked[:8]]
        if not selected:
            return None

        selected_ids = {memory["id"] for memory in selected}
        relationships = [
            relation
            for relation in memory_service.relationships(project_id)
            if relation["from_memory_id"] in selected_ids
            or relation["to_memory_id"] in selected_ids
        ]
        source_ids = list(
            dict.fromkeys(
                source_id for memory in selected for source_id in memory.get("source_ids", [])
            )
        )
        source_rows = (
            rows(
                f"SELECT * FROM knowledge_items WHERE project_id=? AND source_id IN ({','.join('?' for _ in source_ids)})",
                (project_id, *source_ids),
            )
            if source_ids
            else []
        )
        sources = {item["source_id"]: item for item in source_rows}
        evidence: list[GraphEvidence] = []
        for memory in selected:
            for source_id in memory.get("source_ids", []):
                source = sources.get(source_id)
                if not source:
                    continue
                metadata = json.loads(source.get("metadata_json") or "{}")
                metadata.update(
                    {
                        "source_id": source_id,
                        "project_id": project_id,
                        "retrieval_confidence": float(memory.get("confidence") or 0),
                        "primary_lane": "atomic_memory",
                        "memory_id": memory["id"],
                        "memory_type": memory["type"],
                    }
                )
                service_name = str((memory.get("scope") or {}).get("service") or "")
                evidence.append(
                    GraphEvidence(
                        chunk_id=memory["id"],
                        text=source["content"],
                        source_type=source["source_type"],
                        source_title=source["source_title"],
                        source_url=source["source_url"],
                        service_names=[service_name] if service_name else [],
                        metadata=metadata,
                        score=float(memory.get("confidence") or 0) * 100,
                    )
                )

        title_by_source = {
            source_id: (sources.get(source_id) or {}).get("source_title", source_id)
            for source_id in source_ids
        }
        statements: list[str] = []
        for memory in selected:
            titles = list(
                dict.fromkeys(
                    title_by_source.get(source_id, source_id)
                    for source_id in memory.get("source_ids", [])
                )
            )
            citation = " ".join(f"[{title}]" for title in titles)
            statements.append(
                f"- **{memory['type'].replace('_', ' ').title()}:** {memory['content']} {citation}".strip()
            )
        changes = []
        if relationships:
            by_id = {memory["id"]: memory for memory in memories}
            for relation in relationships:
                newer = by_id.get(relation["from_memory_id"])
                older = memory_service.get(relation["to_memory_id"])
                if newer and older:
                    changes.append(
                        f"- **{relation['relationship'].title()}:** “{newer['content']}” "
                        f"relates to the older memory “{older['content']}”."
                    )
        # The change section is only worth showing when something changed, or when
        # the question asked about change. Otherwise "who owns billing" comes back
        # carrying a paragraph about relationships nobody asked for.
        asks_about_change = bool(
            re.search(
                r"\b(chang\w*|updat\w*|new|latest|recent\w*|differ\w*|contradict\w*)\b",
                query,
                re.I,
            )
        )
        sections = ["\n".join(statements)]
        if changes:
            sections.append("**What changed**\n" + "\n".join(changes))
        elif asks_about_change:
            sections.append(
                "**What changed**\n- Nothing recorded. No source shows this was "
                "updated or contradicted."
            )
        answer = "\n\n".join(sections)
        grounded = {
            "answer": answer,
            "likely_cause": "Not applicable — this is a company memory question.",
            "safe_actions": [],
            "approval_required": [],
            "sufficient": bool(evidence),
            "supporting_chunk_ids": [item.chunk_id for item in evidence],
        }
        return grounded, evidence, selected

    @staticmethod
    def _answer_evidence(query: str, evidence: list[GraphEvidence]) -> list[GraphEvidence]:
        """Keep citations aligned with the evidence class used by the answer."""
        intent = query_intent(query)
        if intent in {"ownership", "repository_identity"}:
            metadata = [item for item in evidence if item.source_type == "repository_metadata"]
            return metadata or evidence
        if intent in {"commit_history", "recent_changes"}:
            commits = [item for item in evidence if item.source_type == "github_commit"]
            return commits or evidence
        if intent == "setup":
            canonical = {
                "package.json",
                "makefile",
                "docker-compose.yml",
                "docker-compose.yaml",
                "compose.yml",
                "compose.yaml",
            }
            return [
                item
                for item in evidence
                if item.source_title.casefold() in canonical
                or item.source_title.casefold().startswith("readme")
                or item.source_title.casefold().endswith(("setup.md", "development.md"))
            ]
        if intent == "overview":
            preferred = [
                item
                for item in evidence
                if item.source_title.casefold().startswith("readme")
                or item.source_type == "repository_metadata"
            ]
            return preferred or evidence
        return evidence

    def _workspace_evidence(
        self,
        project_id: str,
        query: str,
        route: RouteResult,
        workspace_project_ids: list[str] | None,
        *,
        force_workspace: bool = False,
        pin_project: bool = False,
        principal_id: str = "anonymous",
        allowed_team_ids: list[str] | None = None,
        token_budget: int = 6000,
    ) -> tuple[list[GraphEvidence], list[dict[str, str]]]:
        """Search repository-local facts locally and operational questions workspace-wide."""
        all_project_ids = list(dict.fromkeys([project_id, *(workspace_project_ids or [])]))
        intent = query_intent(query)
        answer_shape = answer_intent(query)
        project_records = {
            item["id"]: item
            for item in rows(
                f"SELECT id,name,repository FROM projects WHERE id IN ({','.join('?' for _ in all_project_ids)})",
                tuple(all_project_ids),
            )
        }
        named_project_ids = self._named_project_ids(query, project_records)
        if pin_project:
            # An explicit choice, so it outranks both the router's guess and a
            # repository name mentioned in passing.
            project_ids = [project_id]
        elif named_project_ids:
            project_ids = named_project_ids
        elif force_workspace or self._should_expand_workspace(query, intent):
            project_ids = all_project_ids
        else:
            project_ids = [project_id]
        reports = []
        searched: list[dict[str, str]] = []
        for candidate_id in project_ids:
            project = project_records.get(candidate_id)
            if not project:
                continue
            searched.append(
                {
                    "project_id": candidate_id,
                    "project_name": project["name"],
                    "repository": project["repository"],
                }
            )
            candidate_reports = self.swarm.collect_project(
                candidate_id,
                query,
                route,
                allowed_team_ids=allowed_team_ids,
            )
            extras: list[tuple[str, str, list[GraphEvidence]]] = []
            if answer_shape == "api_contract":
                extras.append(
                    (
                        "contract_specialist",
                        "route and request-schema extraction",
                        self._api_contract_evidence(candidate_id),
                    )
                )
            if intent in {"repository_profile", "agent_briefing"}:
                extras.append(
                    (
                        "repository_cartographer",
                        "repository facet and architecture extraction",
                        self._repository_profile_evidence(candidate_id),
                    )
                )
            if intent in {"commit_history", "recent_changes"}:
                extras.append(
                    (
                        "change_historian",
                        "high-recall commit and recent-change retrieval",
                        self.hcag.graph.retrieve_context(
                            candidate_id, query, service_name=None, limit=50
                        ),
                    )
                )
            if intent == "procedure_validity":
                extras.append(
                    (
                        "procedure_validator",
                        "operational assertion validity retrieval",
                        self._assertion_evidence(candidate_id, project, query),
                    )
                )
            project_metadata = {
                "project_id": candidate_id,
                "project_name": project["name"],
                "repository": project["repository"],
                "current_project": candidate_id == project_id,
            }
            for report in candidate_reports:
                for item in report.candidates:
                    item.metadata.update(project_metadata)
            reports.extend(candidate_reports)
            for agent, role, candidate_evidence in extras:
                for item in candidate_evidence:
                    item.metadata.update(project_metadata)
                reports.append(
                    self.swarm.specialist_report(
                        agent,
                        role,
                        candidate_id,
                        candidate_evidence,
                        allowed_team_ids=allowed_team_ids,
                    )
                )
        evidence = [
            item for report in reports if report.status == "complete" for item in report.candidates
        ]
        if answer_shape == "api_contract":
            evidence = [item for item in evidence if item.source_type in {"repo_file", "doc"}]
        elif intent in {"repository_profile", "agent_briefing"}:
            evidence = [item for item in evidence if self._profile_source(item)]
        elif intent == "slack_messages":
            # A request for indexed Slack content may never be answered from
            # connector implementation code that merely contains Slack terms.
            evidence = [item for item in evidence if item.source_type in {"slack", "slack_export"}]
        evidence.sort(
            key=lambda item: item.score * (1.04 if item.metadata.get("current_project") else 1.0),
            reverse=True,
        )
        limit = 32 if intent in {"repository_profile", "agent_briefing"} else 16
        constrained = self._apply_query_constraints(query, evidence)
        allowed_ids = {item.chunk_id for item in constrained}
        reports = [
            replace(
                report,
                candidates=[item for item in report.candidates if item.chunk_id in allowed_ids],
            )
            for report in reports
        ]
        activation = self.swarm.compile(
            project_id,
            query,
            reports,
            principal_id=principal_id,
            token_budget=token_budget,
            max_items=limit,
        )
        self._attach_activation(route, activation)
        return activation.evidence, searched

    @staticmethod
    def _attach_activation(route: RouteResult, activation: Any) -> None:
        route.plan.setdefault("_swarm_runs", []).append(activation.trace)
        route.plan["_compiled_context"] = activation.compiled_context
        route.plan["_active_activation_run_id"] = activation.run_id

    @staticmethod
    def _allow_automatic_workspace_fallback(query: str, intent: str) -> bool:
        lowered = " ".join(query.casefold().split())
        if any(
            marker in lowered
            for marker in (
                "this repo",
                "this repository",
                "active repo",
                "active repository",
                "current repo",
                "current repository",
                "this project",
                "this architecture",
                "this service",
                "this codebase",
                "this application",
            )
        ):
            return False
        return intent not in {
            "api_contract",
            "commit_history",
            "recent_changes",
            "repository_identity",
            "runtime_record",
            "setup",
        }

    def _api_contract_evidence(self, project_id: str) -> list[GraphEvidence]:
        """Load route-defining sources directly instead of ranking error prose above contracts."""
        records = rows(
            """SELECT * FROM knowledge_items WHERE project_id=? AND (
              content LIKE '%/api/%' OR content LIKE '%router.get(%'
              OR content LIKE '%router.post(%' OR content LIKE '%router.put(%'
              OR content LIKE '%router.patch(%' OR content LIKE '%router.delete(%'
              OR content LIKE '%app.get(%' OR content LIKE '%app.post(%'
              OR content LIKE '%.route(%' OR content LIKE '%Mapping(%'
              OR content LIKE '%HandleFunc(%' OR content LIKE '%Methods(%'
              OR content LIKE '%<form%method=%'
            )
            ORDER BY CASE
              WHEN lower(source_title)='backend/server.py' THEN 0
              WHEN lower(source_title) LIKE 'readme%' THEN 1
              WHEN lower(source_title) LIKE 'routes/%' THEN 2
              WHEN lower(source_title) LIKE 'views/%' THEN 3
              ELSE 4 END, created_at DESC LIMIT 20""",
            (project_id,),
        )
        evidence: list[GraphEvidence] = []
        for index, record in enumerate(records):
            title = str(record.get("source_title") or "")
            normalized = title.casefold().replace("\\", "/")
            if (
                normalized.startswith(("tests/", "test/", "fixtures/", "node_modules/"))
                or "/tests/" in normalized
                or normalized.rsplit("/", 1)[-1].startswith("test_")
            ):
                continue
            metadata = json.loads(record.get("metadata_json") or "{}")
            confidence = max(0.82, 0.98 - index * 0.025)
            evidence.append(
                GraphEvidence(
                    chunk_id=f"api-contract:{record['id']}",
                    text=self._api_contract_excerpt(str(record.get("content") or "")),
                    source_type=record.get("source_type") or "repo_file",
                    source_title=title,
                    source_url=record.get("source_url") or "",
                    metadata={
                        **metadata,
                        "item_id": record["id"],
                        "source_id": record.get("source_id") or "",
                        "project_id": project_id,
                        "retrieval_confidence": confidence,
                        "primary_lane": "api_contract",
                    },
                    score=confidence * 100,
                )
            )
        return evidence

    @staticmethod
    def _api_contract_excerpt(content: str) -> str:
        lines = content.splitlines()
        selected: set[int] = set()
        for index, line in enumerate(lines):
            if (
                "/api/" not in line
                and "API_BASE" not in line
                and not re.search(
                    r"""\b(?:router|app)\.(?:get|post|put|patch|delete)\s*\(\s*["']/""",
                    line,
                    re.I,
                )
                and not re.search(r"@\w+\.route\s*\(", line, re.I)
                and not re.search(r"@(?:Get|Post|Put|Patch|Delete)Mapping\s*\(", line, re.I)
                and "HandleFunc(" not in line
                and not re.search(r"<form\b", line, re.I)
                and not re.search(r"\bdo_(?:GET|POST|PUT|PATCH|DELETE)\b", line)
            ):
                continue
            selected.update(range(max(0, index - 16), min(len(lines), index + 22)))
        return "\n".join(lines[index] for index in sorted(selected))[:24000]

    @staticmethod
    def _contains_route_contract(content: str) -> bool:
        return bool(
            "/api/" in content
            or re.search(
                r"""\b(?:router|app)\.(?:get|post|put|patch|delete)\s*\(\s*["']/""",
                content,
                re.I,
            )
            or re.search(r"@\w+\.route\s*\(", content, re.I)
            or re.search(r"@(?:Get|Post|Put|Patch|Delete)Mapping\s*\(", content, re.I)
            or "HandleFunc(" in content
            or re.search(r"<form\b[^>]*\bmethod\s*=", content, re.I)
        )

    def _repository_profile_evidence(self, project_id: str) -> list[GraphEvidence]:
        """Retrieve each requested repository facet independently, then merge."""
        queries = (
            "what is this service about project overview README",
            "where is authentication implemented authorization middleware OAuth JWT session",
            "what is the technology stack external dependencies package.json",
            "application entry point startup package.json app.js routes",
            "services architecture docker compose dependencies requirements pyproject",
            "ownership maintained by responsible for team owner",
            "decisions decided chose policy convention architecture",
            "recent updates changed replaced migrated no longer conflicts",
        )
        merged: dict[str, GraphEvidence] = {}
        for subquery in queries:
            for item in self.hcag.graph.retrieve_context(
                project_id, subquery, service_name=None, limit=10
            ):
                current = merged.get(item.chunk_id)
                if current is None or item.score > current.score:
                    merged[item.chunk_id] = item
        return list(merged.values())

    @staticmethod
    def _named_project_ids(query: str, project_records: dict[str, dict[str, Any]]) -> list[str]:
        """Resolve explicit repository names before semantic chunk ranking."""
        cleaned_query = re.sub(r"^\s*@runbook\b", "", query, flags=re.IGNORECASE).strip()
        normalized_query = re.sub(r"[^a-z0-9]+", "", cleaned_query.casefold())
        matches: list[str] = []
        for project_id, project in project_records.items():
            repository = str(project.get("repository") or "").removesuffix(".git").rstrip("/")
            aliases = {
                str(project.get("name") or "").split("/")[-1],
                repository.split("/")[-1],
            }
            normalized_aliases = {
                re.sub(r"[^a-z0-9]+", "", alias.casefold())
                for alias in aliases
                if len(re.sub(r"[^a-z0-9]+", "", alias.casefold())) >= 4
                and re.sub(r"[^a-z0-9]+", "", alias.casefold()) != "runbook"
            }
            # People type names loosely — "SAP-AI-PR" for a repo named
            # "SAP-AI-PRs". Matching the singular/plural variants keeps a
            # trailing "s" from turning a named space into an abstention.
            alias_variants = {
                variant
                for alias in normalized_aliases
                for variant in (alias, alias.rstrip("s"))
                if len(variant) >= 4
            }
            if any(variant and variant in normalized_query for variant in alias_variants):
                matches.append(project_id)
        return matches

    @staticmethod
    def _profile_source(item: GraphEvidence) -> bool:
        if item.source_type not in {"repo_file", "repository_metadata"}:
            return False
        title = item.source_title.casefold().replace("\\", "/")
        return not (
            title.startswith(
                ("tests/", "test/", "demo_data/", "examples/", "fixtures/", "node_modules/")
            )
            or any(
                segment in title
                for segment in ("/tests/", "/fixtures/", "/examples/", "/node_modules/")
            )
            or title.rsplit("/", 1)[-1].startswith("test_")
        )

    @staticmethod
    def _should_expand_workspace(query: str, intent: str) -> bool:
        if intent in {"repository_locator", "repository_profile", "change_impact"}:
            return True
        lowered = " ".join(query.casefold().split())
        # Configuration names, API paths and service identifiers commonly
        # appear in repository-local questions. They are query constraints,
        # not authorization to fan out across the workspace. A named connected
        # repository is resolved separately by `_named_project_ids`.
        has_technical_anchor = bool(
            re.search(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", query)
            or re.search(r"\b[a-z][\w-]*(?:_service|-service)\b", lowered)
        )
        if has_technical_anchor and any(
            term in lowered
            for term in ("failing", "failure", "fix", "incident", "outage", "timeout")
        ):
            # Troubleshooting deliberately searches connected repositories for
            # an internal resolution, while descriptive/configuration tracing
            # remains inside the selected repository.
            return True
        return any(
            phrase in lowered
            for phrase in (
                "other repositories",
                "other repos",
                "another repository",
                "another repo",
                "all repositories",
                "all repos",
                "company repositories",
                "company-wide",
                "across the company",
                "across repositories",
                "entire workspace",
                "organization repositories",
                "connected github repositories",
                "connected repositories",
                "every connected github repository",
                "every connected repository",
            )
        )

    @staticmethod
    def _calibrate_trust(trust: dict[str, Any], confidence: float) -> dict[str, Any]:
        """A source cannot be presented as high trust when query fit is weak."""
        calibrated = dict(trust)
        score = round(min(float(calibrated.get("score") or 0.0), confidence), 3)
        calibrated["score"] = score
        calibrated["level"] = (
            "high"
            if score >= 0.75
            else "medium" if score >= 0.5 else "low" if score > 0 else "none"
        )
        if score < float(trust.get("score") or 0.0):
            calibrated["reason"] = (
                f"{trust.get('reason', '').rstrip()} Trust is capped by {confidence:.0%} retrieval confidence."
            ).strip()
        return calibrated

    @staticmethod
    def _apply_query_constraints(query: str, evidence: list[GraphEvidence]) -> list[GraphEvidence]:
        """Require exact technical anchors before broad semantic similarity."""
        anchors = set(re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", query))
        anchors.update(re.findall(r"\b[a-z][\w-]*(?:_service|-service)\b", query.casefold()))
        anchors.update(re.findall(r"/(?:api/)?[a-zA-Z0-9_./{}:-]+", query))
        anchors.update(
            value.strip() for value in re.findall(r"`([^`]{3,120})`", query) if value.strip()
        )
        if not anchors:
            return evidence
        constrained: list[GraphEvidence] = []
        for item in evidence:
            haystack = (
                f"{item.source_title}\n{item.text}\n{' '.join(item.service_names)}".casefold()
            )
            if not any(anchor.casefold() in haystack for anchor in anchors):
                continue
            title = item.source_title.casefold().replace("\\", "/")
            if not item.metadata.get("current_project") and (
                title.startswith(("tests/", "test/", "demo_data/", "examples/", "fixtures/"))
                or "/tests/" in title
                or "/fixtures/" in title
                or title.rsplit("/", 1)[-1].startswith("test_")
            ):
                continue
            constrained.append(item)
        return constrained

    @staticmethod
    def _assertion_evidence(
        project_id: str, project: dict[str, Any], query: str
    ) -> list[GraphEvidence]:
        records = rows(
            "SELECT * FROM operational_assertions WHERE project_id=? ORDER BY updated_at DESC",
            (project_id,),
        )
        valid_runbook_ids = {
            item["id"] for item in rows("SELECT id FROM runbooks WHERE project_id=?", (project_id,))
        }
        records = [
            record
            for record in records
            if record.get("subject_type") != "runbook_step"
            or any(
                runbook_id in valid_runbook_ids
                for runbook_id in json.loads(record.get("affected_runbook_ids_json") or "[]")
            )
        ]
        query_terms = {
            term
            for term in query.casefold().replace("_", " ").split()
            if term
            not in {
                "is",
                "the",
                "current",
                "still",
                "valid",
                "procedure",
                "runbook",
            }
        }
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            searchable = (
                f"{record['title']} {record['claim']} {record['environment_scope']}".casefold()
            )
            overlap = sum(term.strip("?.,") in searchable for term in query_terms)
            production = int(
                "deployment" in query.casefold() and record.get("environment_scope") == "production"
            )
            scored.append((overlap + production * 2, record))
        scored.sort(key=lambda value: value[0], reverse=True)
        if scored and scored[0][0] > 0:
            selected = [record for score, record in scored if score == scored[0][0]][:8]
        else:
            selected = [record for _, record in scored[:8]]
        evidence: list[GraphEvidence] = []
        for record in selected:
            try:
                provenance = json.loads(record.get("evidence_json") or "[]")
            except json.JSONDecodeError:
                provenance = []
            source_url = next(
                (
                    str(item.get("provenance") or "")
                    for item in provenance
                    if item.get("provenance")
                ),
                project.get("repository") or "",
            )
            status = str(record.get("status") or "proposed")
            confidence = {
                "verified": 0.88,
                "possibly_stale": 0.9,
                "stale": 0.94,
                "contradicted": 0.96,
                "proposed": 0.72,
            }.get(status, 0.6)
            evidence.append(
                GraphEvidence(
                    chunk_id=f"assertion:{record['id']}",
                    text=(
                        f"Claim: {record['claim']}\nStatus: {status}\n"
                        f"Last verified: {record.get('last_verified_at') or 'never'}\n"
                        f"Verification owner: {record.get('verification_owner') or 'unknown'}\n"
                        f"Review reason: {record.get('verification_reason') or 'none recorded'}"
                    ),
                    source_type="operational_assertion",
                    source_title=record["title"],
                    source_url=source_url,
                    metadata={
                        **record,
                        "project_id": project_id,
                        "project_name": project["name"],
                        "repository": project["repository"],
                        "retrieval_confidence": confidence,
                        "retrieval_intent": "procedure_validity",
                    },
                    score=confidence * 100,
                )
            )
        return evidence

    @staticmethod
    def _citation(item: GraphEvidence) -> dict[str, Any]:
        snippet = item.text.replace("\n", " ").strip()
        confidence = float(item.metadata.get("retrieval_confidence", 0.0) or 0.0)
        return {
            "chunk_id": item.chunk_id,
            "source_type": item.source_type,
            "source_title": item.source_title,
            "source_url": item.source_url,
            "source_id": item.metadata.get("source_id", ""),
            "snippet": snippet[:420] + ("…" if len(snippet) > 420 else ""),
            "exact_chunk": item.text,
            "confidence": confidence,
            "relevance": confidence,
            "semantic_similarity": (item.metadata.get("score_components") or {}).get(
                "semantic_similarity", 0.0
            ),
            "rerank_score": item.metadata.get("rerank_score", 0.0),
            "structural_relevance": item.metadata.get("structural_relevance", 0.0),
            "reranker_model": item.metadata.get("reranker_model", ""),
            "line_start": item.metadata.get("line_start"),
            "line_end": item.metadata.get("line_end"),
            "section": item.metadata.get("section", ""),
            "commit_sha": item.metadata.get("commit_sha", ""),
            "project_id": item.metadata.get("project_id", ""),
            "project_name": item.metadata.get("project_name", ""),
            "repository": item.metadata.get("repository", ""),
        }

    @classmethod
    def _citations(cls, evidence: list[GraphEvidence]) -> list[dict[str, Any]]:
        citations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            identity = (item.source_title, item.source_url)
            if identity in seen:
                continue
            seen.add(identity)
            citations.append(cls._citation(item))
            if len(citations) == 8:
                break
        return citations

    @staticmethod
    def _related_sources(evidence: list[GraphEvidence]) -> dict[str, list[dict[str, str]]]:
        """Group retrieved evidence by source type into related files/issues/PRs.

        Strictly derived from the ranked evidence so nothing is asserted that the
        retrieval pass did not actually surface.
        """
        buckets: dict[str, list[dict[str, str]]] = {
            "files": [],
            "issues": [],
            "pull_requests": [],
            "slack_messages": [],
        }
        keys = {
            "repo_file": "files",
            "github_issue": "issues",
            "pull_request": "pull_requests",
            "slack": "slack_messages",
            "slack_export": "slack_messages",
        }
        seen: set[tuple[str, str]] = set()
        for item in evidence:
            bucket = keys.get(item.source_type)
            if not bucket:
                continue
            identity = (bucket, item.source_title)
            if identity in seen:
                continue
            seen.add(identity)
            buckets[bucket].append({"title": item.source_title, "url": item.source_url})
        return {key: value[:6] for key, value in buckets.items()}

    @staticmethod
    def _suggested_runbooks(project_id: str, query: str, services: list[str]) -> list[str]:
        records = rows(
            "SELECT runbook_key,payload_json FROM runbooks WHERE project_id=?", (project_id,)
        )
        terms = set(query.lower().replace("_", " ").split()) | {
            service.lower() for service in services
        }
        terms -= {"runbook", "service", "failing", "failure", "this", "that", "why", "what", "how"}
        suggestions: list[str] = []
        for record in records:
            searchable = record["payload_json"].lower().replace("_", " ")
            if any(term in searchable for term in terms if len(term) > 3):
                suggestions.append(record["runbook_key"])
        return suggestions[:5]
