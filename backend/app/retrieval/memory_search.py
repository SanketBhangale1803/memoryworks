"""Rank company memory units against free text.

This is the relevance pass behind memory search, the WebMCP agent's search
tool, and pre-change briefings. It replaced substring matching, which counted
"the" and "on" as hits and matched "pay" inside "payments", so every query
returned most of memory and an empty result was almost impossible.

Scoring is lexical and deterministic: whole-word matches on lightly stemmed
tokens, weighted by how rare the word is across the memories being searched,
and by which field it hit. When a local cross-encoder is configured, the
lexical candidates are reranked by it and weak matches are dropped; when a
semantic embedding model is configured too, memories that share no words with
the query join the candidates first, and the cross-encoder decides whether
they stay. Embedding recall is never used without that check, because nearest
neighbours always exist and are often unrelated.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Function words, plus the verbs every intent starts with. They carry no
# information about which memory is relevant, and several of them ("the",
# "on", "to") appear inside nearly every stored sentence.
STOPWORDS = frozenset(
    """
    a about above after again all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few
    for from further had has have having he her here hers him his how i if in into is
    it its itself just me more most my no nor not now of off on once only or other our
    ours out over own same she should so some such than that the their theirs them then
    there these they this those through to too under until up very was we were what when
    where which while who whom why will with would you your yours
    add make change changes update updates please need want let get set new use using
    """.split()
)

# Where a term lands says how much it matters: a subject names what the memory
# is about, the service scope says where it applies, the body only mentions it.
FIELD_WEIGHTS = (("subject", 3.0), ("service", 2.0), ("type", 1.0), ("content", 1.0))

# A lexical hit this far below the best one is noise that shares a word with
# the query, not a second relevant memory.
RELATIVE_FLOOR = 0.3

# Cross-encoder logits below this are "not about the query" for the MS MARCO
# family of rerankers used here.
RERANK_FLOOR = 0.0


def stem(token: str) -> str:
    """Fold common English inflections so 'retries' meets 'retry'."""
    for suffix, replacement in (
        ("ies", "y"),
        ("ied", "y"),
        ("ing", ""),
        ("ed", ""),
        ("es", ""),
        ("s", ""),
    ):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)] + replacement
            break
    if token.endswith("e") and len(token) > 3:
        token = token[:-1]
    return token


def tokens(text: str) -> set[str]:
    return {stem(token) for token in _TOKEN_RE.findall(text.casefold())}


def query_terms(text: str) -> list[str]:
    """The informative terms of a query, stemmed, in first-seen order."""
    seen: dict[str, None] = {}
    for token in _TOKEN_RE.findall(text.casefold()):
        if token in STOPWORDS or len(token) < 2:
            continue
        seen.setdefault(stem(token), None)
    return list(seen)


def _fields(unit: dict[str, Any]) -> dict[str, set[str]]:
    scope = unit.get("scope") or {}
    return {
        "subject": tokens(str(unit.get("subject") or "")),
        "service": tokens(str(scope.get("service") or "")),
        "type": tokens(str(unit.get("type") or "")),
        "content": tokens(str(unit.get("content") or "")),
    }


def rank(
    units: Iterable[dict[str, Any]], query: str, limit: int
) -> list[tuple[dict[str, Any], float]]:
    """Return (unit, score) pairs for the units relevant to `query`, best first."""
    terms = query_terms(query)
    pool = list(units)
    if not terms or not pool:
        return []
    lexical = _lexical(pool, terms)
    return _rerank(query, pool, lexical, limit)


def _lexical(pool: list[dict[str, Any]], terms: list[str]) -> list[tuple[dict[str, Any], float]]:
    fields = [_fields(unit) for unit in pool]

    # Inverse document frequency over the memories being searched: a word most
    # of them contain ("service", "payments") says little about which one this
    # query is after.
    document_frequency = {
        term: sum(1 for f in fields if any(term in values for values in f.values()))
        for term in terms
    }
    idf = {
        term: math.log(1.0 + len(pool) / count)
        for term, count in document_frequency.items()
        if count
    }

    scored: list[tuple[dict[str, Any], float]] = []
    for unit, unit_fields in zip(pool, fields, strict=True):
        score = sum(
            idf[term] * weight
            for term in idf
            for field, weight in FIELD_WEIGHTS
            if term in unit_fields[field]
        )
        if score > 0:
            scored.append((unit, score))
    if not scored:
        return []
    scored.sort(key=lambda pair: (pair[1], str(pair[0].get("updated_at") or "")), reverse=True)
    best = scored[0][1]
    return [pair for pair in scored if pair[1] >= best * RELATIVE_FLOOR]


def _text(unit: dict[str, Any]) -> str:
    return f"{unit.get('subject') or ''}. {unit.get('content') or ''}"


def _rerank(
    query: str,
    pool: list[dict[str, Any]],
    lexical: list[tuple[dict[str, Any], float]],
    limit: int,
) -> list[tuple[dict[str, Any], float]]:
    from app.retrieval.semantic import get_reranker

    reranker = get_reranker()
    if reranker is None:
        return lexical[:limit]
    candidates = [unit for unit, _ in lexical[: max(limit * 3, 12)]]
    seen = {id(unit) for unit in candidates}
    candidates += [unit for unit in _semantic_neighbours(query, pool, k=12) if id(unit) not in seen]
    if not candidates:
        return []
    try:
        logits = reranker.logits(query, [_text(unit) for unit in candidates])
    except Exception:
        # A reranker that fails leaves the lexical order, which is still sound.
        return lexical[:limit]
    reranked = [
        (unit, round(logit, 3))
        for unit, logit in zip(candidates, logits, strict=True)
        if logit >= RERANK_FLOOR
    ]
    reranked.sort(key=lambda pair: pair[1], reverse=True)
    return reranked[:limit]


def _semantic_neighbours(query: str, pool: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    from app.retrieval.semantic import get_semantic_provider

    try:
        provider = get_semantic_provider()
        if not provider.semantic:
            return []
        query_vector = provider.embed_query(query)
        vectors = [_embedding(provider, _text(unit)) for unit in pool]
    except Exception:
        return []
    ranked = sorted(
        zip(pool, vectors, strict=True),
        key=lambda pair: _cosine(query_vector, pair[1]),
        reverse=True,
    )
    return [unit for unit, _ in ranked[:k]]


@lru_cache(maxsize=20_000)
def _cached_embedding(model_name: str, text: str, provider: Any) -> tuple[float, ...]:
    return tuple(provider.embed_text(text))


def _embedding(provider: Any, text: str) -> tuple[float, ...]:
    # Memory text is immutable per unit (a change makes a new unit), so the
    # text itself is a safe cache key and each unit is embedded once per process.
    return _cached_embedding(provider.model_name, text, provider)


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = list(left), list(right)
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / norm if norm else 0.0
