from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.graph.base import GraphEvidence

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.@/{}/:-]{1,}")
STOP_WORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "any",
    "anything",
    "are",
    "as",
    "at",
    "be",
    "can",
    "do",
    "does",
    "explain",
    "for",
    "from",
    "give",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "our",
    "please",
    "repo",
    "repos",
    "repositories",
    "repository",
    "should",
    "show",
    "that",
    "the",
    "there",
    "this",
    "to",
    "using",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}

# This is a domain vocabulary, not a question whitelist. It expands concepts
# into implementation language so the same question works across frameworks.
CONCEPTS: dict[str, set[str]] = {
    "api": {
        "api",
        "body",
        "controller",
        "endpoint",
        "endpoints",
        "fastapi",
        "flask",
        "http",
        "payload",
        "request",
        "response",
        "route",
        "router",
    },
    "authentication": {
        "auth",
        "authenticate",
        "authenticated",
        "authenticates",
        "authentication",
        "authorization",
        "identity",
        "jwt",
        "login",
        "middleware",
        "oauth",
        "passport",
        "session",
        "sso",
    },
    "architecture": {
        "architecture",
        "component",
        "dependency",
        "depends",
        "module",
        "queue",
        "service",
        "storage",
        "system",
        "workflow",
    },
    "data": {
        "collection",
        "data",
        "database",
        "datastore",
        "migration",
        "model",
        "mongodb",
        "mongoose",
        "mysql",
        "persisted",
        "persistence",
        "postgres",
        "redis",
        "schema",
        "table",
    },
    "configuration": {
        "config",
        "configuration",
        "configured",
        "connection",
        "environment",
        "setting",
        "uri",
        "variable",
    },
    "setup": {
        "build",
        "compose",
        "docker",
        "install",
        "makefile",
        "package.json",
        "requirements",
        "run",
        "setup",
        "start",
    },
    "change": {
        "change",
        "changed",
        "commit",
        "current",
        "diff",
        "history",
        "latest",
        "previous",
        "replaced",
        "update",
        "updated",
    },
    # Interface work had no concept at all, so a question about the background
    # colour expanded only through "change" and came back with commit history.
    # These tokens are what a stylesheet or component actually contains.
    "interface": {
        "background",
        "bg",
        "border",
        "button",
        "color",
        "colour",
        "component",
        "css",
        "font",
        "layout",
        "margin",
        "padding",
        "palette",
        "render",
        "scss",
        "style",
        "styles",
        "stylesheet",
        "theme",
        "ui",
    },
    "diagnostic": {
        "because",
        "cause",
        "caused",
        "error",
        "exhausted",
        "expired",
        "failed",
        "failing",
        "failure",
        "incident",
        "outage",
        "refused",
        "root",
        "timeout",
        "unreachable",
    },
    "organization": {
        "approval",
        "codeowners",
        "convention",
        "decision",
        "maintainer",
        "owner",
        "ownership",
        "policy",
        "responsible",
        "team",
    },
    "risk": {
        "approval",
        "blocked",
        "blocker",
        "duplicate",
        "gate",
        "launch",
        "missing",
        "production",
        "readiness",
        "required",
        "risk",
        "rollback",
        "unresolved",
    },
}


# Words that frame a question without naming what it is about. In "are there any
# payments set up in the repos", everything except "payments" is framing — and
# framing words match almost every chunk ("setup", "repos"), which is how a
# question about payments used to be answered with quoted lines about setup.
FRAMING_TERMS = {
    "actually",
    "anywhere",
    "app",
    "apps",
    "code",
    "codebase",
    "codebases",
    "currently",
    "exist",
    "existing",
    "exists",
    "feature",
    "features",
    "file",
    "files",
    "find",
    "found",
    "handled",
    "implement",
    "implementation",
    "implemented",
    "integrated",
    "integration",
    "kind",
    "list",
    "locally",
    "logic",
    "project",
    "projects",
    "recent",
    "recently",
    "set",
    "some",
    "something",
    "still",
    "support",
    "supported",
    "tell",
    "thing",
    "things",
    "today",
    "use",
    "used",
    "uses",
    "work",
    "working",
    "works",
    "you",
    "your",
}


# How many lines from a mention of the subject a line may sit and still count as
# being about it. "payment_service failed." / "Root cause: the pool was exhausted."
# is one answer across two lines; a setup step thirty lines below a marketing
# sentence that happens to say "payments" is not.
SUBJECT_RADIUS = 3


def subject_terms(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """The query terms that name what the question is about.

    A term belongs to the subject when it is neither framing nor part of a concept
    vocabulary: concept words ("setup", "config", "error") describe the *kind* of
    answer wanted and are expanded separately, while the subject is what that
    answer has to be about.
    """
    vocabulary = set().union(*CONCEPTS.values())
    return tuple(
        token
        for token in tokens
        if len(token) >= 4 and token not in FRAMING_TERMS and not (_term_forms(token) & vocabulary)
    )


@dataclass(frozen=True)
class UniversalQueryPlan:
    original: str
    tokens: tuple[str, ...]
    technical_anchors: tuple[str, ...]
    facets: tuple[str, ...]
    retrieval_queries: tuple[str, ...]

    def trace(self) -> dict[str, Any]:
        return {
            "tokens": list(self.tokens),
            "technical_anchors": list(self.technical_anchors),
            "facets": list(self.facets),
            "retrieval_queries": list(self.retrieval_queries),
        }


def plan_query(query: str) -> UniversalQueryPlan:
    cleaned = " ".join(re.sub(r"^\s*@(runbook|orgmemory)\b", "", query, flags=re.I).strip().split())
    raw_tokens = [
        token.strip("./:{}-").casefold()
        for token in TOKEN_RE.findall(cleaned)
        if token.strip("./:{}-")
    ]
    tokens = tuple(dict.fromkeys(token for token in raw_tokens if token not in STOP_WORDS))
    anchors = tuple(
        dict.fromkeys(
            token.strip("./:{}-")
            for token in TOKEN_RE.findall(cleaned)
            if token.strip("./:{}-")
            if (
                "_" in token
                or "/" in token
                or "." in token
                or "-" in token
                or any(character.isupper() for character in token[1:])
            )
        )
    )
    token_set = set(tokens)
    facets = tuple(name for name, vocabulary in CONCEPTS.items() if token_set & vocabulary)

    variants: list[str] = [cleaned]
    clauses = re.split(r"[?;]+|\b(?:and also|as well as|also|plus)\b", cleaned, flags=re.I)
    variants.extend(clause.strip(" ,.") for clause in clauses if len(TOKEN_RE.findall(clause)) >= 2)
    if tokens:
        variants.append(" ".join(tokens))
    for facet in facets:
        expansion = list(tokens)
        expansion.extend(sorted(CONCEPTS[facet] - token_set)[:12])
        variants.append(" ".join(dict.fromkeys(expansion)))

    unique: list[str] = []
    normalized: set[str] = set()
    for variant in variants:
        key = " ".join(variant.casefold().split())
        if not key or key in normalized:
            continue
        normalized.add(key)
        unique.append(variant[:800])
    return UniversalQueryPlan(
        original=cleaned,
        tokens=tokens,
        technical_anchors=anchors,
        facets=facets,
        retrieval_queries=tuple(unique[:8]),
    )


def _candidate_lines(text: str, near: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Answerable lines of ``text``, each with the source line it was cut from.

    With ``near``, only lines close to a mention of one of those terms are kept.
    """
    output: list[tuple[str, str]] = []
    raw_lines = text.splitlines()
    allowed: set[int] | None = None
    if near:
        allowed = set()
        for index, raw_line in enumerate(raw_lines):
            lowered = raw_line.casefold()
            if any(_matches(term, lowered) for term in near):
                allowed.update(range(index - SUBJECT_RADIUS, index + SUBJECT_RADIUS + 1))
    for index, raw_line in enumerate(raw_lines):
        if allowed is not None and index not in allowed:
            continue
        line = raw_line.strip(" \t-*#>")
        if len(line) < 8 or line in {"```", "---"} or not _answerable_line(line):
            continue
        if "{" in line and len(line) < 300:
            window = [line]
            balance = line.count("{") - line.count("}")
            for following in raw_lines[index + 1 : index + 10]:
                value = following.strip(" \t-*#>")
                if value:
                    window.append(value)
                    balance += value.count("{") - value.count("}")
                if balance <= 0 or sum(len(part) for part in window) >= 650:
                    break
            combined = " ".join(window)[:700]
            if len(combined) >= 12:
                output.append((combined, line))
        pieces = re.split(r"(?<=[.!?])\s+", line)
        output.extend((piece.strip(), line) for piece in pieces if 8 <= len(piece.strip()) <= 700)
    return output


def _answerable_line(line: str) -> bool:
    value = line.strip()
    lowered = value.casefold()
    if any(
        marker in value
        for marker in ("=>", "className=", "write_json(", "setError(", "});", "</", "/>")
    ):
        return False
    if re.match(
        r"^(?:if|elif|else|for|while|return|raise|def|class|const|let|var|"
        r"function|import|from|export)\b",
        value,
        re.I,
    ):
        return False
    if re.match(r"^[\w-]+\s*:\s*[^:]+;?$", value):
        return False
    # A quoted key or list element is a data literal lifted out of source code
    # ('"setup": {"build", ...}' or '"company repositories",'), never a statement.
    if re.match(r"""^(["'])[^"']{1,120}\1\s*[:,\]]""", value):
        return False
    if lowered.startswith(("open the vite url", "http://", "https://")):
        return False
    if _is_machine_identifier(value):
        return False
    return not bool(re.search(r"[;{}]\s*$", value))


# A labelled field whose value is an identifier rather than a statement: a URL, a
# commit hash, an ISO timestamp. Ingested commit and repository records are full
# of these, and they rank well because they are short and contain query terms.
#
# They are never an answer. Someone asking "why is it failing" and receiving
# "Commit SHA: 77fde75f… / Committed at: 2026-07-25T23:07:01Z" has been handed
# the machinery instead of the answer — strings and numbers that mean nothing
# without the system that produced them. The underlying chunk stays available as
# a citation; it just stops being quoted as prose.
_MACHINE_VALUE_RE = re.compile(
    r"^[A-Za-z][A-Za-z ]{0,30}:\s*(?:"
    r"https?://"  # URL: …, Latest commit URL: …
    r"|[0-9a-f]{7,40}\s*$"  # Commit SHA: 77fde75f…
    r"|\d{4}-\d{2}-\d{2}[T ]"  # Committed at: 2026-07-25T23:07:01Z
    r"|[\w.-]+@[\w.-]+\s*$"  # Author email: …
    r")",
    re.IGNORECASE,
)


def _is_machine_identifier(value: str) -> bool:
    return bool(_MACHINE_VALUE_RE.match(value.strip()))


def _term_forms(term: str) -> set[str]:
    forms = {term}
    if len(term) > 4 and term.endswith("ies"):
        forms.add(term[:-3] + "y")
    if len(term) > 4 and term.endswith("s") and not term.endswith("ss"):
        forms.add(term[:-1])
    if len(term) > 5 and term.endswith("ed"):
        forms.update({term[:-2], term[:-1]})
    if len(term) > 6 and term.endswith("ing"):
        forms.update({term[:-3], term[:-3] + "e"})
    return {form for form in forms if len(form) >= 3}


def _matches(term: str, text: str) -> bool:
    return any(form in text for form in _term_forms(term))


def universal_evidence_answer(query: str, evidence: list[GraphEvidence]) -> dict[str, Any]:
    """Answer an unrecognized question without adding another intent branch."""
    if not evidence:
        return _insufficient()
    plan = plan_query(query)
    query_terms = set(plan.tokens)
    expansion_terms = set(query_terms)
    for facet in plan.facets:
        expansion_terms.update(CONCEPTS[facet])
    anchors = {value.casefold() for value in plan.technical_anchors}
    required_facet_terms = CONCEPTS["risk"] if "risk" in plan.facets else set()
    subject = subject_terms(plan.tokens)
    # What the question asks *about* its subject: the other words it used, plus
    # the vocabulary of the concepts those words activated.
    asked_terms = expansion_terms - set(subject)

    ranked: list[tuple[float, str, GraphEvidence]] = []
    for item in evidence:
        title = item.source_title.casefold()
        item_text = item.text.casefold()
        item_direct = any(
            _matches(term, item_text) or _matches(term, title) for term in query_terms
        )
        # A source whose title names the subject is about it throughout; any
        # other source only near the lines that mention it.
        titled = any(_matches(term, title) for term in subject)
        near = () if titled else subject
        for line, source_line in _candidate_lines(item.text, near):
            lowered = line.casefold()
            direct = sum(_matches(term, lowered) for term in query_terms)
            expanded = sum(_matches(term, lowered) for term in expansion_terms - query_terms)
            anchor_hits = sum(anchor in lowered or anchor in title for anchor in anchors)
            title_hits = sum(_matches(term, title) for term in query_terms)
            if not item_direct and anchor_hits == 0 and title_hits == 0:
                continue
            if direct == 0 and expanded == 0 and anchor_hits == 0 and title_hits == 0:
                continue
            if required_facet_terms and not any(
                _matches(term, lowered) for term in required_facet_terms
            ):
                continue
            # Naming the subject is not enough when the question also asks
            # something about it: "payments setup" is not answered by a line
            # that only mentions payments in passing. The whole source line is
            # checked, because the sentence next to a fix often names the symptom.
            if (
                subject
                and asked_terms
                and not titled
                and not any(_matches(term, source_line.casefold()) for term in asked_terms)
            ):
                continue
            source_quality = min(float(item.score or 0) / 40.0, 2.5)
            score = direct * 3.0 + min(expanded, 3) * 0.8 + anchor_hits * 5 + title_hits * 2
            score += source_quality
            ranked.append((score, line, item))
    ranked.sort(key=lambda value: value[0], reverse=True)
    if not ranked or ranked[0][0] < 3.0:
        missing = [
            term
            for term in subject
            if not any(
                _matches(term, item.text.casefold()) or _matches(term, item.source_title.casefold())
                for item in evidence
            )
        ]
        if missing and len(missing) == len(subject) <= 2:
            # "Is there any X?" with no X anywhere in the searched sources has an
            # answer, and it is not "I can't tell": say what was looked for. The
            # standard sentence stays first because callers detect a refusal by it.
            return _insufficient(
                "I do not have enough company memory to answer this confidently. "
                f"Nothing in the searched sources mentions {' or '.join(missing)}."
            )
        return _insufficient()

    findings: list[tuple[str, GraphEvidence]] = []
    seen_lines: set[str] = set()
    source_counts: dict[str, int] = {}
    for _, line, item in ranked:
        normalized = " ".join(line.casefold().split())
        if normalized in seen_lines or source_counts.get(item.chunk_id, 0) >= 2:
            continue
        seen_lines.add(normalized)
        source_counts[item.chunk_id] = source_counts.get(item.chunk_id, 0) + 1
        findings.append((line, item))
        if len(findings) >= 5:
            break

    answer_lines = [
        (
            "**Launch risks supported by current company memory**"
            if "risk" in plan.facets
            else "**Answer from current company memory**"
        )
    ]
    for line, item in findings:
        answer_lines.append(f"- {line} [{item.source_title}]")
    diagnostic = "diagnostic" in plan.facets
    return {
        "answer": "\n".join(answer_lines),
        "likely_cause": (
            findings[0][0] if diagnostic else "Not applicable — no failure was asked about."
        ),
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "supporting_chunk_ids": list(dict.fromkeys(item.chunk_id for _, item in findings)),
        "query_plan": plan.trace(),
    }


def _insufficient(
    answer: str = "I do not have enough company memory to answer this confidently.",
) -> dict[str, Any]:
    return {
        "answer": answer,
        "likely_cause": "Insufficient evidence",
        "safe_actions": [],
        "approval_required": [],
        "sufficient": False,
    }
