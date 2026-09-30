"""Typed response models for MemoryWorks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BriefingVerdict = Literal[
    "no_memory",
    "proceed",
    "proceed_with_context",
    "requires_approval",
]
OutcomeLabel = Literal["succeeded", "failed", "partial", "abandoned", "unknown"]


@dataclass(frozen=True, slots=True)
class Evidence:
    """One source-backed item used to answer a query."""

    chunk_id: str = ""
    source_type: str = ""
    source_title: str = ""
    source_url: str = ""
    source_id: str = ""
    snippet: str = ""
    exact_chunk: str = ""
    confidence: float = 0.0
    relevance: float = 0.0
    project_id: str = ""
    project_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Evidence:
        return cls(
            chunk_id=str(value.get("chunk_id") or ""),
            source_type=str(value.get("source_type") or ""),
            source_title=str(value.get("source_title") or ""),
            source_url=str(value.get("source_url") or ""),
            source_id=str(value.get("source_id") or ""),
            snippet=str(value.get("snippet") or ""),
            exact_chunk=str(value.get("exact_chunk") or ""),
            confidence=float(value.get("confidence") or 0.0),
            relevance=float(value.get("relevance") or 0.0),
            project_id=str(value.get("project_id") or ""),
            project_name=str(value.get("project_name") or ""),
            raw=dict(value),
        )


@dataclass(frozen=True, slots=True)
class ContextEnvelope:
    """The durable, scoped context assembled for a query."""

    id: str = ""
    project_id: str = ""
    principal_id: str = ""
    query: str = ""
    task_type: str = ""
    compiled_context: dict[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    activation_run_ids: tuple[str, ...] = ()
    token_budget: int = 0
    expires_at: str = ""
    created_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> ContextEnvelope:
        data = value or {}
        return cls(
            id=str(data.get("id") or ""),
            project_id=str(data.get("project_id") or ""),
            principal_id=str(data.get("principal_id") or ""),
            query=str(data.get("query") or ""),
            task_type=str(data.get("task_type") or ""),
            compiled_context=dict(data.get("compiled_context") or {}),
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            activation_run_ids=tuple(data.get("activation_run_ids") or ()),
            token_budget=int(data.get("token_budget") or 0),
            expires_at=str(data.get("expires_at") or ""),
            created_at=str(data.get("created_at") or ""),
            raw=dict(data),
        )


@dataclass(frozen=True, slots=True)
class AskResponse:
    """A grounded answer and all context needed to inspect or reuse it."""

    answer: str
    answer_sufficient: bool
    confidence: float
    evidence: tuple[Evidence, ...]
    context_envelope: ContextEnvelope
    trust_score: dict[str, Any] = field(default_factory=dict)
    retrieval_trace: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    related_services: tuple[str, ...] = ()
    memory_units: tuple[dict[str, Any], ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def compiled_context(self) -> dict[str, Any]:
        """Context ready to pass to a downstream model or agent."""

        return self.context_envelope.compiled_context

    @property
    def context_envelope_id(self) -> str:
        return self.context_envelope.id

    @property
    def swarm_run_ids(self) -> tuple[str, ...]:
        return self.context_envelope.activation_run_ids

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AskResponse:
        return cls(
            answer=str(value.get("answer") or ""),
            answer_sufficient=bool(value.get("answer_sufficient")),
            confidence=float(value.get("confidence") or 0.0),
            evidence=tuple(
                Evidence.from_dict(item)
                for item in value.get("evidence") or ()
                if isinstance(item, dict)
            ),
            context_envelope=ContextEnvelope.from_dict(value.get("context_envelope")),
            trust_score=dict(value.get("trust_score") or {}),
            retrieval_trace=dict(value.get("retrieval_trace") or {}),
            model=dict(value.get("model") or {}),
            related_services=tuple(value.get("related_services") or ()),
            memory_units=tuple(value.get("memory_units") or ()),
            raw=dict(value),
        )


@dataclass(frozen=True, slots=True)
class BriefingCitation:
    """One current, source-backed memory selected for a pre-action briefing."""

    memory_id: str
    type: str
    subject: str
    content: str
    why_it_matters: str
    service: str | None = None
    project_id: str = ""
    project_name: str | None = None
    confidence: float = 0.0
    sources: int = 0
    updated_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingCitation:
        return cls(
            memory_id=str(value.get("memory_id") or ""),
            type=str(value.get("type") or ""),
            subject=str(value.get("subject") or ""),
            content=str(value.get("content") or ""),
            why_it_matters=str(value.get("why_it_matters") or ""),
            service=(
                str(value["service"]) if value.get("service") is not None else None
            ),
            project_id=str(value.get("project_id") or ""),
            project_name=(
                str(value["project_name"])
                if value.get("project_name") is not None
                else None
            ),
            confidence=float(value.get("confidence") or 0.0),
            sources=int(value.get("sources") or 0),
            updated_at=str(value.get("updated_at") or ""),
            raw=dict(value),
        )


@dataclass(frozen=True, slots=True)
class BriefingPrecedent:
    """A previously verified approach relevant to the proposed action."""

    skill_id: str = ""
    name: str = ""
    trigger: str = ""
    steps: tuple[str, ...] = ()
    successes: int = 0
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingPrecedent:
        return cls(
            skill_id=str(value.get("skill_id") or ""),
            name=str(value.get("name") or ""),
            trigger=str(value.get("trigger") or ""),
            steps=tuple(str(item) for item in value.get("steps") or ()),
            successes=int(value.get("successes") or 0),
            confidence=float(value.get("confidence") or 0.0),
            raw=dict(value),
        )


@dataclass(frozen=True, slots=True)
class BriefingResponse:
    """Deterministic company context returned before a consequential action."""

    task: str
    verdict: BriefingVerdict
    headline: str
    briefing_id: str = ""
    service: str | None = None
    project_id: str | None = None
    consequential_action: str | None = None
    must_read: tuple[BriefingCitation, ...] = ()
    constraints: tuple[BriefingCitation, ...] = ()
    prior_incidents: tuple[BriefingCitation, ...] = ()
    blast_radius: tuple[BriefingCitation, ...] = ()
    procedures: tuple[BriefingCitation, ...] = ()
    precedents: tuple[BriefingPrecedent, ...] = ()
    requires_approval: tuple[str, ...] = ()
    safe_actions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    memory_count: int = 0
    failure_state: str = ""
    instrumentation_status: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingResponse:
        def citations(key: str) -> tuple[BriefingCitation, ...]:
            return tuple(
                BriefingCitation.from_dict(item)
                for item in value.get(key) or ()
                if isinstance(item, dict)
            )

        return cls(
            task=str(value.get("task") or ""),
            verdict=value.get("verdict") or "no_memory",
            headline=str(value.get("headline") or ""),
            briefing_id=str(value.get("briefing_id") or ""),
            service=(
                str(value["service"]) if value.get("service") is not None else None
            ),
            project_id=(
                str(value["project_id"])
                if value.get("project_id") is not None
                else None
            ),
            consequential_action=(
                str(value["consequential_action"])
                if value.get("consequential_action") is not None
                else None
            ),
            must_read=citations("must_read"),
            constraints=citations("constraints"),
            prior_incidents=citations("prior_incidents"),
            blast_radius=citations("blast_radius"),
            procedures=citations("procedures"),
            precedents=tuple(
                BriefingPrecedent.from_dict(item)
                for item in value.get("precedents") or ()
                if isinstance(item, dict)
            ),
            requires_approval=tuple(value.get("requires_approval") or ()),
            safe_actions=tuple(value.get("safe_actions") or ()),
            open_questions=tuple(value.get("open_questions") or ()),
            memory_count=int(value.get("memory_count") or 0),
            failure_state=str(value.get("failure_state") or ""),
            instrumentation_status=str(value.get("instrumentation_status") or ""),
            raw=dict(value),
        )


@dataclass(frozen=True, slots=True)
class BriefingOutcomeReceipt:
    """Receipt for the action and outcome appended to a briefing's ledger row."""

    briefing_id: str
    action_id: str
    action_type: str
    outcome_id: str
    outcome: OutcomeLabel
    reward: float
    recorded: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingOutcomeReceipt:
        action = value.get("action") or {}
        outcome = value.get("outcome") or {}
        return cls(
            briefing_id=str(value.get("briefing_id") or ""),
            action_id=str(action.get("id") or ""),
            action_type=str(action.get("action_type") or ""),
            outcome_id=str(outcome.get("id") or ""),
            outcome=outcome.get("outcome") or "unknown",
            reward=float(outcome.get("reward") or 0.0),
            recorded=bool(value.get("recorded")),
            raw=dict(value),
        )
