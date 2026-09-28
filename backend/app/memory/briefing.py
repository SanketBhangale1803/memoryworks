"""Pre-action briefings: what this company knows before an agent changes anything.

Every other retrieval surface in OrgMemory answers a *question*. This one answers
an *intent* — "I am about to do X to service Y" — which is a different shape of
request and needs a different shape of answer. A question wants the best passage;
an intent wants the constraints it is about to violate.

Three properties make this trustworthy enough to gate real work on:

* **Deterministic.** No model runs here. A briefing that changes its verdict
  between two identical calls is not a control, and an agent about to restart a
  production pool needs a control. Everything returned is a stored memory with an
  id a person can open.
* **Recall over precision.** Missing a prior incident is far more expensive than
  showing one extra. The kind-scoped pulls deliberately return the service's
  history even when the task text does not overlap with it.
* **Never silently empty.** When memory holds nothing, the verdict says so
  explicitly rather than returning a confident-looking empty brief, because
  "nothing found" and "nothing to worry about" are opposite instructions.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.ingestion.extractors import extract_services

# Verbs that change the world. Their presence in an intent does not make it
# forbidden — it makes it something a person should have agreed to first, which
# is exactly the boundary the WebMCP tool surface is built around.
CONSEQUENTIAL_VERBS: dict[str, str] = {
    "deploy": "deploying",
    "release": "releasing",
    "rollback": "rolling back",
    "roll back": "rolling back",
    "restart": "restarting",
    "reboot": "restarting",
    "scale": "changing capacity",
    "migrate": "running a migration",
    "migration": "running a migration",
    "drop": "dropping data",
    "delete": "deleting data",
    "truncate": "deleting data",
    "purge": "deleting data",
    "revoke": "revoking access",
    "rotate": "rotating credentials",
    "disable": "disabling a component",
    "shut down": "shutting a component down",
    "merge": "merging code",
    "push": "publishing code",
    "force": "forcing an override",
    "failover": "triggering a failover",
    "reindex": "reindexing",
    "backfill": "running a backfill",
    # Changing a limit is the specific move that caused the outage in most
    # capacity incidents, so the words for it belong here even though they sound
    # milder than "delete". Deliberately absent: "change" and "update", which
    # match almost any sentence and would make every verdict the same one.
    "raise": "raising a limit",
    "increase": "raising a limit",
    "bump": "raising a limit",
    "lower": "lowering a limit",
    "decrease": "lowering a limit",
    "enable": "turning something on",
    "turn off": "turning something off",
    "turn on": "turning something on",
}

# Kinds pulled for the service regardless of task wording. These are the layers
# that constrain a change rather than describe it.
CONSTRAINT_KINDS = ("decision", "policy", "convention")
HISTORY_KINDS = ("incident",)
STRUCTURE_KINDS = ("dependency", "ownership")
PROCEDURE_KINDS = ("procedure",)

VERDICTS = (
    "no_memory",
    "proceed",
    "proceed_with_context",
    "requires_approval",
)

_WORD_RE = re.compile(r"[^a-z0-9]+")

# Service names are the join key between an intent and the memory graph, so they
# are worth extracting from free text rather than demanding as a parameter an
# agent may not have.
_SERVICE_HINT_RE = re.compile(
    r"\b(?:service|repo|repository|component|app|application)\s+([a-z0-9][a-z0-9._\-/]{1,63})\b",
    re.IGNORECASE,
)


def build(
    *,
    task: str,
    service: str,
    project_id: str,
    search: Callable[..., dict[str, Any]],
    list_by_kind: Callable[[str, str], list[dict[str, Any]]],
    precedents: Callable[[str], list[dict[str, Any]]],
    limit: int = 6,
) -> dict[str, Any]:
    """Assemble one briefing from already-authorized retrieval callables.

    The callables are injected rather than imported so this module never decides
    what a caller is allowed to see. `search` and `list_by_kind` are expected to
    be permission-trimmed by the time they get here; this function only chooses
    what to ask them for and how to read the result.
    """
    resolved_service = (service or _infer_service(task)).strip()
    intent = _consequential_intent(task)

    relevant = _search(search, task, project_id, limit=limit * 2)

    # The kind-scoped pulls only run when a component is named. Without one they
    # return every decision and incident in the workspace, and presenting another
    # team's postmortem as "this has gone wrong before" is worse than returning
    # less: the agent cannot tell the difference, and acts on it. With no service
    # the briefing falls back to relevance alone and says so in open_questions.
    scoped = bool(resolved_service)
    constraints = (
        _gather(list_by_kind, CONSTRAINT_KINDS, resolved_service, project_id, limit)
        if scoped
        else []
    )
    history = (
        _gather(list_by_kind, HISTORY_KINDS, resolved_service, project_id, limit) if scoped else []
    )
    structure = (
        _gather(list_by_kind, STRUCTURE_KINDS, resolved_service, project_id, limit)
        if scoped
        else []
    )
    procedures = (
        _gather(list_by_kind, PROCEDURE_KINDS, resolved_service, project_id, limit)
        if scoped
        else []
    )

    # The relevance pass matches wording, not ownership, so on its own it put a
    # payments postmortem in front of an agent tuning notification-worker
    # because both mention retries. With a named service, a hit has to be about
    # that service, about one it is recorded as depending on or serving, or not
    # about any particular service at all.
    if scoped:
        reach = _reach(resolved_service, structure)
        relevant = [unit for unit in relevant if _within(unit, reach)]

    # The task-relevant hits carry the strongest signal, so they lead the
    # must-read list; the kind pulls fill in what the wording missed.
    must_read = _dedupe(
        [*relevant[:limit], *constraints[:2], *history[:2]],
        limit=limit,
    )

    # The verdict reasons over everything found, before any of it is assigned to
    # a display group — a policy does not stop constraining a change because it
    # happened to rank high enough to lead the briefing.
    all_constraints = _dedupe(constraints, limit=limit)
    all_prior = _dedupe(history, limit=limit)
    approvals = _approval_reasons(intent, all_constraints, all_prior, resolved_service)
    known = bool(must_read or all_constraints or all_prior or structure or procedures)
    verdict = _verdict(
        known=known, approvals=approvals, constraints=all_constraints, prior=all_prior
    )

    # Each memory then appears in exactly one group. Repeating a record under
    # both "read this first" and "decisions that constrain it" made one decision
    # look like two findings, and cost an agent tokens to discover it had read
    # the same row twice. must_read leads; the groups hold the remainder.
    lead = {str(unit.get("id")) for unit in must_read}
    shown_constraints = _without(all_constraints, lead)
    shown_prior = _without(all_prior, lead)
    shown_structure = _without(_dedupe(structure, limit=limit), lead)
    shown_procedures = _without(_dedupe(procedures, limit=limit), lead)

    return {
        "task": task,
        "service": resolved_service or None,
        "project_id": project_id or None,
        "verdict": verdict,
        "headline": _headline(verdict, resolved_service, all_prior, all_constraints),
        "consequential_action": intent or None,
        "must_read": [
            _cite(unit, "Directly matches what you are about to do") for unit in must_read
        ],
        "constraints": [
            _cite(unit, "A recorded decision or policy this change has to respect")
            for unit in shown_constraints
        ],
        "prior_incidents": [
            _cite(unit, "This has gone wrong before in a way that looks like this")
            for unit in shown_prior
        ],
        "blast_radius": [
            _cite(unit, "Connected component that a change here can reach")
            for unit in shown_structure
        ],
        "procedures": [
            _cite(unit, "An established procedure exists — follow it instead of improvising")
            for unit in shown_procedures
        ],
        "precedents": _precedents(precedents, task),
        "requires_approval": approvals,
        # Safe actions read the full procedure and structure sets: advice must not
        # change because a record was promoted into the lead list.
        "safe_actions": _safe_actions(procedures, structure, resolved_service),
        "open_questions": _open_questions(known, resolved_service, intent),
        "memory_count": len(
            lead
            | {
                str(unit.get("id"))
                for unit in [
                    *shown_constraints,
                    *shown_prior,
                    *shown_structure,
                    *shown_procedures,
                ]
            }
        ),
    }


def _search(
    search: Callable[..., dict[str, Any]], task: str, project_id: str, limit: int
) -> list[dict[str, Any]]:
    if not _tokens(task):
        return []
    try:
        return list(search(task, project_id=project_id, limit=limit).get("results") or [])
    except Exception:
        # A briefing missing its relevance pass is degraded but still useful; a
        # briefing that raises leaves the agent with nothing at all.
        return []


def _gather(
    list_by_kind: Callable[[str, str], list[dict[str, Any]]],
    kinds: tuple[str, ...],
    service: str,
    project_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for kind in kinds:
        try:
            units = list_by_kind(kind, project_id)
        except Exception:
            continue
        found.extend(_for_service(units, service))
    found.sort(key=lambda unit: str(unit.get("updated_at") or ""), reverse=True)
    return found[: limit * 2]


def _for_service(units: list[dict[str, Any]], service: str) -> list[dict[str, Any]]:
    """Filter to one named service.

    The match is deliberately wider than the `scope.service` field, because a
    memory that names the service in its subject or body is about that service
    whether or not the extractor tagged it. It is just as deliberately strict
    about the empty case: returning every unrelated policy in the workspace
    would put another team's rules in front of an agent as though they bound
    this change, and a briefing that cites the wrong constraint is worse than
    one that admits it found none.
    """
    if not service:
        return units
    needle = service.casefold()
    return [
        unit
        for unit in units
        if needle in str((unit.get("scope") or {}).get("service") or "").casefold()
        or needle in str(unit.get("subject") or "").casefold()
        or needle in str(unit.get("content") or "").casefold()
    ]


def _reach(service: str, structure: list[dict[str, Any]]) -> set[str]:
    """The named service plus every service a recorded dependency links it to."""
    reach = {service.casefold()}
    for unit in structure:
        if unit.get("type") != "dependency":
            continue
        text = f"{unit.get('subject') or ''} {unit.get('content') or ''}"
        named = {name.casefold() for name in extract_services(text)}
        if reach & named:
            reach |= named
    return reach


def _within(unit: dict[str, Any], reach: set[str]) -> bool:
    scoped_to = str((unit.get("scope") or {}).get("service") or "").casefold()
    if not scoped_to or scoped_to in reach:
        return True
    text = f"{unit.get('subject') or ''} {unit.get('content') or ''}".casefold()
    return any(name in text for name in reach)


def _dedupe(units: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for unit in units:
        key = str(unit.get("id") or unit.get("subject") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(unit)
        if len(unique) >= limit:
            break
    return unique


def _without(units: list[dict[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
    """Drop records already shown in a higher-priority group."""
    return [unit for unit in units if str(unit.get("id")) not in seen]


def _cite(unit: dict[str, Any], why: str) -> dict[str, Any]:
    return {
        "memory_id": unit.get("id"),
        "type": unit.get("type"),
        "subject": unit.get("subject"),
        "content": unit.get("content"),
        "service": (unit.get("scope") or {}).get("service") or None,
        "project_id": unit.get("project_id"),
        "project_name": unit.get("project_name") or None,
        "confidence": unit.get("confidence"),
        "sources": len(unit.get("source_ids") or []),
        "updated_at": unit.get("updated_at"),
        "why_it_matters": why,
    }


def _precedents(
    precedents: Callable[[str], list[dict[str, Any]]], task: str
) -> list[dict[str, Any]]:
    try:
        found = precedents(task)
    except Exception:
        return []
    return [
        {
            "skill_id": skill.get("id"),
            "name": skill.get("name"),
            "trigger": skill.get("trigger"),
            "steps": skill.get("steps") or [],
            "successes": skill.get("successes"),
            "confidence": skill.get("confidence"),
        }
        for skill in found[:3]
    ]


def _consequential_intent(task: str) -> str:
    lowered = task.casefold()
    for verb, described in CONSEQUENTIAL_VERBS.items():
        if re.search(rf"\b{re.escape(verb)}", lowered):
            return described
    return ""


def _approval_reasons(
    intent: str,
    constraints: list[dict[str, Any]],
    prior: list[dict[str, Any]],
    service: str,
) -> list[str]:
    reasons: list[str] = []
    if intent:
        reasons.append(f"This request involves {intent}. A person has to agree before it happens.")
    for unit in constraints[:3]:
        if unit.get("type") == "policy":
            reasons.append(
                f"Policy in memory constrains this: {str(unit.get('subject') or '')[:120]}"
            )
    if prior and intent:
        where = f" on {service}" if service else ""
        reasons.append(
            f"{len(prior)} prior incident(s){where} started from a change like this — "
            "confirm the mitigation before acting."
        )
    return reasons[:4]


def _verdict(
    *,
    known: bool,
    approvals: list[str],
    constraints: list[dict[str, Any]],
    prior: list[dict[str, Any]],
) -> str:
    if not known:
        return "no_memory"
    if approvals:
        return "requires_approval"
    if constraints or prior:
        return "proceed_with_context"
    return "proceed"


def _headline(
    verdict: str,
    service: str,
    prior: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> str:
    where = f" for {service}" if service else ""
    if verdict == "no_memory":
        return (
            f"Company memory holds nothing{where} about this yet. Treat this as unknown "
            "territory and confirm with a person before changing anything."
        )
    if verdict == "requires_approval":
        return (
            f"This changes production state{where}. Read the constraints below, then get an "
            "explicit human decision — OrgMemory will not approve it for you."
        )
    if verdict == "proceed_with_context":
        return (
            f"Safe to investigate{where}, but {len(prior)} prior incident(s) and "
            f"{len(constraints)} recorded decision(s) apply. Read them before you act."
        )
    return f"No recorded constraints{where}. Proceed, and report the outcome back."


def _safe_actions(
    procedures: list[dict[str, Any]],
    structure: list[dict[str, Any]],
    service: str,
) -> list[str]:
    actions: list[str] = []
    for unit in procedures[:2]:
        actions.append(f"Follow the remembered procedure: {str(unit.get('subject') or '')[:120]}")
    if structure:
        actions.append(
            "Check the connected components listed under blast_radius before making a change."
        )
    if service:
        actions.append(f"Read the current service context for {service} with its owners.")
    actions.append("Gather read-only evidence first; nothing here authorizes a write.")
    return actions[:4]


def _open_questions(known: bool, service: str, intent: str) -> list[str]:
    questions: list[str] = []
    if not known:
        questions.append(
            "Nothing in company memory covers this. Who owns it, and should it be recorded?"
        )
    if not service:
        questions.append(
            "No service was identified. Name the service to get its incident history and owners."
        )
    if intent and known:
        questions.append("Has the owning team agreed to this change, and when was it last done?")
    return questions[:3]


def _infer_service(task: str) -> str:
    match = _SERVICE_HINT_RE.search(task)
    return match.group(1) if match else ""


def _tokens(value: str) -> set[str]:
    return {token for token in _WORD_RE.split(value.casefold()) if len(token) > 2}
