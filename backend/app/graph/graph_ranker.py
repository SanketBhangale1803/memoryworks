"""Evidence ranking for graph retrieval.

Hybrid scoring over project knowledge chunks: lexical term overlap weighted
by frequency, a strong boost for chunks tied to the routed service, a boost
for operational source types, and an overview path for repository-level
questions. Confidence is normalized against the best-scoring chunk so it
reflects relative support within this retrieval, not an absolute claim.

This module is the single ranking implementation used by both the ArcadeDB
store and the in-memory test store, and it is the seam where an
embedding-based scorer can be added without changing response contracts.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from math import log
from typing import TYPE_CHECKING, Any

import numpy as np

from app.core.config import settings
from app.retrieval.intents import ownership_scope
from app.retrieval.semantic import get_reranker, get_semantic_provider

from .base import GraphEvidence

if TYPE_CHECKING:
    from .project_index import PreparedChunks

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}")
STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "why",
    "how",
    "what",
    "runbook",
    "this",
    "that",
    "service",
}
OVERVIEW_TERMS = {
    "about",
    "application",
    "describe",
    "does",
    "overview",
    "project",
    "purpose",
    "repo",
    "repository",
    "work",
}
OVERVIEW_PHRASES = (
    "what is this",
    "what does this",
    "what is the project",
    "what is the service",
    "what does it do",
    "tell me about",
    "project overview",
    "service overview",
    "describe this",
)

SLACK_CONVERSATION_RE = re.compile(
    r"\b(?:say|says|said|message|messages|conversation|conversations|thread|threads|"
    r"discuss|discussed|discussion|summarize|summarise|summary)\b"
)
SLACK_CATCHUP_RE = re.compile(
    r"\b(?:missed|unread)\s+(?:chat|chats|message|messages|conversation|conversations)\b"
    r"|\b(?:chat|chats|slack|channel)\s+(?:updates?|digest|recap|summary)\b"
    r"|\b(?:updates?|digest|recap|summary)\s+(?:from|in|on|of)\s+"
    r"(?:chat|chats|slack|channels?)\b"
    r"|\bcatch\s+me\s+up\b.{0,48}\b(?:chat|chats|messages?|slack|channels?)\b"
)
SLACK_AUTHORING_RE = re.compile(
    r"\b(?:draft|write|prepare|create|post|publish|send)\b.{0,48}\bslack\b"
)
SLACK_IMPLEMENTATION_RE = re.compile(
    r"\b(?:api|client|connector|endpoint|function|class|implementation|implemented|"
    r"oauth|sdk|source code)\b"
)

INTENT_PHRASES = {
    "configuration_locator": (),
    "repository_profile": (),
    "slack_messages": (
        "what slack messages",
        "which slack messages",
        "show slack messages",
        "messages do i have",
        "messages from slack",
        "what was said in slack",
        "summarize slack",
        "slack conversation",
    ),
    "decision_rationale": (
        "why did the team choose",
        "why was this chosen",
        "what decision was made",
        "what was decided",
        "architecture decision",
        "decision rationale",
    ),
    "approval_requirement": (
        "what must be approved",
        "what requires approval",
        "who must approve",
        "approval before",
    ),
    "change_impact": (
        "which repositories could be affected",
        "which repos could be affected",
        "what could be affected",
        "if i change this api",
        "blast radius",
    ),
    "tech_stack": (
        "tech stack",
        "technology stack",
        "technologies used",
        "frameworks used",
        "what is this built with",
        "what does this use",
    ),
    "architecture": (
        "architecture",
        "how is this structured",
        "system design",
        "components",
    ),
    "setup": (
        "how do i run",
        "how to run",
        "run this locally",
        "local setup",
        "getting started",
        "install",
    ),
    "repository_locator": (
        "which repository contains",
        "which repo contains",
        "where is the authentication implementation",
        "where is auth implemented",
        "where is authentication implemented",
        "what repository implements",
        "what repo implements",
    ),
    "repository_identity": (
        "name of this repo",
        "name of the repo",
        "repository name",
        "what repo",
    ),
    "commit_history": (
        "last commit",
        "latest commit",
        "most recent commit",
        "newest commit",
        "recent commits",
        "commit history",
    ),
    "recent_changes": (
        "what changed recently",
        "what changed in this repo",
        "recent changes",
        "latest changes",
        "what was changed",
        "what has changed",
    ),
    "ownership": (
        "who owns",
        "repo owner",
        "repository owner",
        "owner",
        "ownership",
        "maintainer",
        "who committed",
        "who commited",
        "most recent commit",
        "latest commit",
    ),
    "procedure_validity": (
        "procedure still valid",
        "procedure is still valid",
        "runbook still valid",
        "runbook is still valid",
        "deployment procedure still valid",
        "is this procedure valid",
        "is the current procedure valid",
    ),
}

CONCEPT_TERMS = {
    "configuration_locator": {"config", "configured", "defined", "environment", "setting"},
    "repository_profile": {
        "authentication",
        "authorization",
        "dependencies",
        "entry",
        "overview",
    },
    "slack_messages": {"channel", "conversation", "message", "messages", "slack", "thread"},
    "decision_rationale": {
        "adr",
        "because",
        "chose",
        "decision",
        "decided",
        "rationale",
        "selected",
        "tradeoff",
    },
    "approval_requirement": {
        "approval",
        "approve",
        "approved",
        "owner",
        "policy",
        "production",
        "restart",
    },
    "change_impact": {
        "api",
        "affected",
        "blast",
        "dependency",
        "endpoint",
        "impact",
        "repository",
    },
    "tech_stack": {
        "dependencies",
        "devdependencies",
        "framework",
        "language",
        "package",
        "runtime",
        "vite",
        "react",
        "typescript",
        "javascript",
        "python",
        "docker",
    },
    "architecture": {
        "architecture",
        "component",
        "depends",
        "dependency",
        "docker",
        "module",
        "service",
        "workflow",
    },
    "setup": {"build", "dev", "install", "make", "npm", "run", "setup", "start"},
    "repository_locator": {
        "auth",
        "authentication",
        "authorization",
        "jwt",
        "login",
        "middleware",
        "oauth",
        "session",
    },
    "repository_identity": {"full_name", "name", "repo", "repository"},
    "commit_history": {"author", "commit", "committed", "history", "latest", "message", "sha"},
    "recent_changes": {"change", "changed", "commit", "latest", "message", "recent"},
    "ownership": {
        "author",
        "commit",
        "committed",
        "maintainer",
        "owner",
        "ownership",
        "team",
    },
    "procedure_validity": {
        "assertion",
        "contradicted",
        "deployment",
        "procedure",
        "runbook",
        "stale",
        "valid",
        "verified",
    },
}

CANONICAL_SOURCE_BOOSTS = {
    "configuration_locator": {},
    "repository_profile": {},
    "slack_messages": {},
    "decision_rationale": {},
    "approval_requirement": {},
    "change_impact": {},
    "tech_stack": {
        "package.json": 28,
        "pyproject.toml": 28,
        "requirements.txt": 26,
        "cargo.toml": 28,
        "go.mod": 28,
        "pom.xml": 28,
        "build.gradle": 26,
        "vite.config.js": 18,
        "vite.config.ts": 18,
        "dockerfile": 15,
        "docker-compose.yml": 16,
        "docker-compose.yaml": 16,
        "readme.md": 12,
    },
    "architecture": {
        "architecture.md": 26,
        "docker-compose.yml": 22,
        "docker-compose.yaml": 22,
        "readme.md": 16,
        "package.json": 12,
    },
    "setup": {
        "readme.md": 24,
        "makefile": 20,
        "package.json": 18,
        "docker-compose.yml": 16,
        "docker-compose.yaml": 16,
    },
    "repository_identity": {"repository-metadata": 36, "readme.md": 4},
    "commit_history": {},
    "recent_changes": {},
    "repository_locator": {},
    "ownership": {
        "repository-metadata": 50,
        "codeowners": 30,
        ".github/codeowners": 30,
        "readme.md": 8,
    },
    "procedure_validity": {},
}


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text) if token.lower() not in STOP]


def _json_value(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _source_freshness(value: Any) -> float:
    if value in (None, ""):
        return 0.35
    try:
        if isinstance(value, int | float) or str(value).replace(".", "", 1).isdigit():
            stamp = datetime.fromtimestamp(float(value), UTC)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if not stamp.tzinfo:
                stamp = stamp.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - stamp).total_seconds() / 86400.0)
        return math.exp(-age_days / 120.0)
    except (TypeError, ValueError, OSError):
        return 0.35


def _overview_intent(query: str) -> bool:
    lowered = " ".join(query.lower().replace("@runbook", "").split())
    terms = set(_tokens(lowered))
    return any(phrase in lowered for phrase in OVERVIEW_PHRASES) or (
        bool(terms) and terms <= OVERVIEW_TERMS
    )


def _slack_message_intent(lowered: str) -> bool:
    """Recognize requests for Slack content without routing Slack code questions here."""
    if SLACK_AUTHORING_RE.search(lowered):
        return False
    if SLACK_IMPLEMENTATION_RE.search(lowered):
        return False
    if SLACK_CATCHUP_RE.search(lowered):
        return True
    return bool(re.search(r"\bslack\b", lowered) and SLACK_CONVERSATION_RE.search(lowered))


def query_intent(query: str) -> str:
    lowered = " ".join(query.casefold().replace("@runbook", "").split())
    # A small typo-tolerant guard for the common "name/nae of this repo"
    # question. This avoids sending an identity question through generic code
    # sentence extraction merely because one character was omitted.
    tokens = set(_tokens(lowered))
    profile_facets = int(
        bool(re.search(r"\b(?:explain|describe)\s+what\s+[\w.-]+\s+does\b", lowered))
    ) + sum(
        marker in lowered
        for marker in (
            "what it does",
            "authentication",
            "authorization",
            "entry points",
            "external dependencies",
        )
    )
    if profile_facets >= 2 and any(
        phrase in lowered
        for phrase in (
            "connected github repositories",
            "connected repositories",
            "across repositories",
            "repository profile",
        )
    ):
        return "repository_profile"
    if _slack_message_intent(lowered):
        return "slack_messages"
    has_technical_anchor = bool(
        re.search(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", query) or re.search(r"`[^`]{3,120}`", query)
    )
    if has_technical_anchor and (
        re.search(
            r"\b(where|which file|which repository|which repo)\b.*\b(configur|defined|set|used)",
            lowered,
        )
        or any(term in lowered for term in ("trace ", "flow through", "from configuration"))
    ):
        return "configuration_locator"
    if ("repo" in tokens or "repository" in tokens) and {
        "name",
        "named",
        "nae",
    }.intersection(tokens):
        return "repository_identity"
    ownership_kind = ownership_scope(lowered)
    if ownership_kind == "repository":
        return "ownership"
    if ownership_kind == "entity":
        return "entity_ownership"
    for intent, phrases in INTENT_PHRASES.items():
        if any(phrase in lowered for phrase in phrases):
            return intent
    return "overview" if _overview_intent(query) else "general"


def rank_records(
    records: list[dict[str, Any]], query: str, service_name: str | None, limit: int
) -> list[GraphEvidence]:
    """Rank raw chunk records (prepared on the spot; see rank_prepared)."""
    from .project_index import PreparedChunks

    return rank_prepared(PreparedChunks.build(records, _tokens), query, service_name, limit)


def rank_prepared(
    prepared: PreparedChunks, query: str, service_name: str | None, limit: int
) -> list[GraphEvidence]:
    """Rank a project's prepared chunks (app.graph.project_index) for one query."""
    intent = query_intent(query)
    rows: list[int] = list(range(len(prepared)))
    if intent == "slack_messages":
        # Enforce the evidence class before top-k selection. Filtering after
        # ranking lets connector implementation files consume the candidate
        # budget and can silently omit real messages from a busy repository.
        rows = [
            i for i in rows if prepared.records[i].get("source_type") in {"slack", "slack_export"}
        ]
    elif intent in {"commit_history", "recent_changes"}:
        # Temporal repository questions must never be answered by a random code
        # sentence that happens to contain words such as "change" or "error".
        rows = [
            i
            for i in rows
            if prepared.records[i].get("source_type") in {"github_commit", "repository_metadata"}
        ]
    query_terms = Counter(_tokens(query))
    semantic_provider = get_semantic_provider()
    query_vector = semantic_provider.embed_query(query)
    concept_terms = CONCEPT_TERMS.get(intent, set())
    technical_anchors = set(re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", query))
    overview = intent == "overview"
    # Document frequency over the rows being ranked, as before: the prepared
    # figure covers the whole project, so a filtered intent counts its own.
    document_frequency = (
        prepared.document_frequency
        if len(rows) == len(prepared)
        else Counter(term for i in rows for term in prepared.terms[i])
    )
    total_documents = max(1, len(rows))
    # Every chunk's similarity in one product; _cosine was a dot product of
    # normalised vectors that is 0 when the widths differ.
    similarity = np.zeros(len(prepared), dtype=np.float32)
    if query_vector and prepared.vectors.shape[1] == len(query_vector):
        similarity = prepared.vectors @ np.asarray(query_vector, dtype=np.float32)
    ranked: list[GraphEvidence] = []
    for i in rows:
        record = prepared.records[i]
        haystack_terms = prepared.terms[i]
        matched = [term for term in query_terms if term in haystack_terms]
        lexical = sum(
            (1 + min(haystack_terms[term], 4))
            * (1 + log((total_documents + 1) / (document_frequency[term] + 1)))
            * weight
            for term, weight in query_terms.items()
            if term in haystack_terms
        )
        concept_matches = sorted(concept_terms.intersection(haystack_terms))
        concept_score = min(12.0, len(concept_matches) * 2.4)
        services = prepared.services[i]
        service_boost = (
            10
            if service_name and service_name.lower() in {str(value).lower() for value in services}
            else 0
        )
        if intent == "slack_messages" and record.get("source_type") in {
            "slack",
            "slack_export",
        }:
            source_boost = 36
        else:
            source_boost = (
                2
                if (lexical or service_boost)
                and record.get("source_type")
                in {"github_issue", "pull_request", "slack", "incident", "log"}
                else 0
            )
        metadata = dict(prepared.metadata[i])
        overview_score = 0
        if overview and not lexical and not service_boost:
            title = str(record.get("source_title", "")).lower()
            chunk_index = int(metadata.get("chunk_index", 0))
            if title in {"readme.md", "readme", "readme.txt"}:
                overview_score = max(12, 22 - chunk_index)
            elif title in {"package.json", "pyproject.toml", "cargo.toml", "go.mod"}:
                overview_score = 12
            elif title.endswith(("deployment.md", "architecture.md", "overview.md")):
                overview_score = 10
            elif record.get("source_type") == "repo_file" and title.endswith(".md"):
                overview_score = 6
        title = str(record.get("source_title", "")).casefold()
        canonical_boost = CANONICAL_SOURCE_BOOSTS.get(intent, {}).get(title, 0)
        if overview and title in {"readme.md", "readme", "readme.txt"}:
            # Repository identity lives at the beginning of the README. Later
            # operational sections may match more query words, but they must
            # not displace the introductory purpose paragraph.
            chunk_index = int(metadata.get("chunk_index", 0))
            canonical_boost += max(0, 18 - chunk_index * 3)
        if record.get("source_type") == "repository_metadata" and intent in {
            "repository_identity",
            "ownership",
            "overview",
            "commit_history",
            "recent_changes",
        }:
            canonical_boost = max(canonical_boost, 36 if intent != "overview" else 12)
        if record.get("source_type") == "github_commit" and intent in {
            "commit_history",
            "recent_changes",
        }:
            canonical_boost = max(canonical_boost, 40)
        if intent == "repository_locator" and record.get("source_type") == "repo_file":
            implementation_path_terms = {
                "auth",
                "authentication",
                "authorization",
                "jwt",
                "login",
                "middleware",
                "oauth",
                "security",
                "session",
            }
            path_tokens = set(_tokens(title.replace("/", " ").replace("_", " ")))
            if path_tokens.intersection(implementation_path_terms):
                canonical_boost = max(canonical_boost, 24)
        if (
            not canonical_boost
            and intent in {"tech_stack", "architecture"}
            and title.endswith((".js", ".jsx", ".ts", ".tsx", ".py", ".go", ".rs", ".java"))
        ):
            canonical_boost = 3
        exact_score = (
            lexical
            + concept_score
            + service_boost
            + source_boost
            + overview_score
            + canonical_boost
        )
        indexed_terms = prepared.search_terms[i]
        compressed_overlap = len(set(query_terms).intersection(indexed_terms)) / max(
            1, len(set(query_terms))
        )
        dense = (
            max(0.0, float(similarity[i]))
            if prepared.vector_ok[i] and prepared.models[i] == semantic_provider.model_name
            else 0.0
        )
        memory = metadata.get("memory") or {}
        memory_fast = float(memory.get("fast_weight") or 0.0)
        memory_slow = float(memory.get("slow_weight") or 0.0)
        freshness = _source_freshness(metadata.get("source_updated_at"))
        vector_weight = 24.0 if semantic_provider.semantic else 8.0
        compressed_score = vector_weight * dense + 5.0 * compressed_overlap
        temporal_score = 2.2 * memory_fast + 2.8 * memory_slow + 1.5 * freshness
        # Time and consolidation can reorder relevant evidence, but can never
        # make an unrelated chunk relevant on their own.
        dense_floor = 0.28 if semantic_provider.semantic else 0.12
        if exact_score <= 0 and dense < dense_floor and compressed_overlap <= 0:
            continue
        score = exact_score + compressed_score + temporal_score
        metadata["matched_terms"] = (
            matched or concept_matches or (["project_overview"] if overview_score else [])
        )
        metadata["retrieval_intent"] = intent
        metadata["score_components"] = {
            "lexical": round(lexical, 3),
            "concept": round(concept_score, 3),
            "service": service_boost,
            "source_authority": canonical_boost + overview_score + source_boost,
            "compressed_global": round(compressed_score, 3),
            "semantic_similarity": round(dense, 4),
            "temporal_memory": round(temporal_score, 3),
        }
        lane_scores = {
            "sparse_exact": round(exact_score, 3),
            "compressed_global": round(compressed_score, 3),
            "temporal_memory": round(temporal_score, 3),
        }
        metadata["retrieval_lanes"] = lane_scores
        metadata["primary_lane"] = max(lane_scores, key=lane_scores.get)
        metadata["memory_state"] = memory.get("state", "unreinforced")
        ranked.append(
            GraphEvidence(
                chunk_id=record["id"],
                text=record.get("text", ""),
                source_type=record.get("source_type", "unknown"),
                source_title=record.get("source_title", "Untitled source"),
                source_url=record.get("source_url", ""),
                service_names=list(services or []),
                metadata=metadata,
                score=float(score),
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    reranker = get_reranker()
    rerank_candidates = ranked[: max(1, settings.runbook_semantic_candidate_limit)]
    if reranker and rerank_candidates:
        rerank_scores = reranker.score(
            query,
            [f"{item.source_title}\n{item.text}" for item in rerank_candidates],
        )
        for evidence, rerank_score in zip(rerank_candidates, rerank_scores, strict=True):
            evidence.score += 28.0 * rerank_score
            evidence.metadata["rerank_score"] = round(rerank_score, 4)
            evidence.metadata["reranker_model"] = reranker.model_name
            evidence.metadata["retrieval_lanes"]["cross_encoder"] = round(28.0 * rerank_score, 3)
            evidence.metadata["primary_lane"] = max(
                evidence.metadata["retrieval_lanes"],
                key=evidence.metadata["retrieval_lanes"].get,
            )
        ranked.sort(key=lambda item: item.score, reverse=True)
    for evidence in ranked:
        components = evidence.metadata.get("score_components") or {}
        matched = set(evidence.metadata.get("matched_terms") or [])
        coverage = len(matched.intersection(query_terms)) / max(1, len(query_terms))
        authority = min(1.0, float(components.get("source_authority") or 0.0) / 30.0)
        # Cross-encoders trained on prose routinely under-score JSON manifests
        # even when the manifest is the canonical answer to a structured
        # question. Preserve the raw model score, but also record an auditable
        # structural fit derived from the query intent, source type and matched
        # concepts. This is evidence fitness, not a cosmetic confidence boost.
        title = evidence.source_title.casefold()
        canonical_limit = max(CANONICAL_SOURCE_BOOSTS.get(intent, {}).values(), default=0)
        canonical_fit = (
            min(
                1.0,
                CANONICAL_SOURCE_BOOSTS.get(intent, {}).get(title, 0) / max(1, canonical_limit),
            )
            if canonical_limit
            else 0.0
        )
        concept_fit = min(1.0, len(matched.intersection(concept_terms)) / 3.0)
        overview_fit = (
            1.0
            if overview
            and title in {"readme.md", "readme", "readme.txt"}
            and int(evidence.metadata.get("chunk_index") or 0) == 0
            else 0.0
        )
        anchor_fit = (
            sum(
                anchor.casefold() in f"{evidence.source_title}\n{evidence.text}".casefold()
                for anchor in technical_anchors
            )
            / len(technical_anchors)
            if technical_anchors and intent in {"configuration_locator", "change_impact"}
            else 0.0
        )
        source_type_fit = (
            1.0
            if intent == "slack_messages" and evidence.source_type in {"slack", "slack_export"}
            else (
                1.0
                if intent in {"commit_history", "recent_changes"}
                and evidence.source_type == "github_commit"
                else 0.0
            )
        )
        structural_fit = max(canonical_fit, concept_fit, overview_fit, anchor_fit, source_type_fit)
        evidence.metadata["structural_relevance"] = round(structural_fit, 4)
        coverage = max(coverage, structural_fit)
        exact_strength = min(1.0, float(evidence.metadata["retrieval_lanes"]["sparse_exact"]) / 24)
        compressed_strength = min(
            1.0, float(evidence.metadata["retrieval_lanes"]["compressed_global"]) / 8
        )
        temporal_strength = min(
            1.0, float(evidence.metadata["retrieval_lanes"]["temporal_memory"]) / 5
        )
        if semantic_provider.semantic:
            dense = float(components.get("semantic_similarity") or 0.0)
            dense_strength = max(0.0, min(1.0, (dense - 0.25) / 0.45))
            raw_rerank = float(evidence.metadata.get("rerank_score") or 0.0)
            rerank_strength = max(raw_rerank, 0.9 * structural_fit)
            evidence.metadata["retrieval_confidence"] = round(
                min(
                    0.97,
                    0.04
                    + 0.12 * coverage
                    + 0.13 * authority
                    + 0.10 * exact_strength
                    + 0.38 * dense_strength
                    + 0.28 * rerank_strength
                    + 0.02 * temporal_strength,
                ),
                3,
            )
        else:
            evidence.metadata["retrieval_confidence"] = round(
                min(
                    0.95,
                    0.10
                    + 0.30 * coverage
                    + 0.30 * authority
                    + 0.22 * exact_strength
                    + 0.05 * compressed_strength
                    + 0.03 * temporal_strength,
                ),
                3,
            )
    # Avoid a context pack dominated by many chunks from one large file. The
    # first pass maximizes source diversity; the second fills remaining slots.
    selected: list[GraphEvidence] = []
    seen_sources: set[tuple[str, str]] = set()
    for evidence in ranked:
        identity = (evidence.source_type, evidence.source_title)
        if identity in seen_sources:
            continue
        selected.append(evidence)
        seen_sources.add(identity)
        if len(selected) >= limit:
            return selected
    for evidence in ranked:
        if evidence not in selected:
            selected.append(evidence)
        if len(selected) >= limit:
            break
    return selected
