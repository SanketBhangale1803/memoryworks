"""Record the triple: context served → action taken → outcome observed.

Every competitor can ingest the same Slack and GitHub. What none of them holds is
the record of *which context actually produced correct action inside this
company*. That record is only obtainable by instrumenting the loop while it runs,
it compounds per-customer, and it is what a judge or reranker can later be
fine-tuned on. So the ledger is written from day one, before anything consumes it.

Three rules shape this module:

* **Never break an answer.** Instrumentation is secondary to the product. Every
  write is best-effort; a failure here degrades the corpus, never the response.
* **Link, don't duplicate.** A context row stores what a training example needs
  (the question, the answer, the candidates, the evidence ids) and points at the
  envelope for the rest.
* **Outcomes are a closed vocabulary, actions are open.** Reward depends on the
  outcome, so it must be comparable across rows; the set of things a person or
  agent might *do* with an answer is not knowable in advance.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.database import connect, new_id, rows, utcnow

# Closed vocabulary: reward is derived from these, so they have to mean the same
# thing in every row for the corpus to be trainable.
OUTCOMES = ("succeeded", "failed", "partial", "abandoned", "unknown")

# What an outcome is worth as a training signal. `partial` is deliberately closer
# to success than failure: the context was usable, just incomplete.
REWARDS: dict[str, float] = {
    "succeeded": 1.0,
    "partial": 0.5,
    "unknown": 0.0,
    "abandoned": -0.25,
    "failed": -1.0,
}

# Actions that imply the answer was used, for the acceptance-rate statistic. An
# open vocabulary still needs a few known-good members to aggregate on.
POSITIVE_ACTIONS = {
    "accepted",
    "handoff_copied",
    "handoff_dispatched",
    "edit_applied",
    "pr_opened",
    "pr_merged",
    "runbook_run",
}
NEGATIVE_ACTIONS = {"rejected", "dismissed", "ignored"}

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def record_context(
    *,
    project_id: str,
    query: str,
    result: dict[str, Any],
    workspace_id: str = "",
    principal_id: str = "",
    surface: str = "api",
) -> str:
    """Log the context served for one answer. Returns its id, or "" if not logged.

    Called from the answer path, so it swallows every error: a broken ledger must
    never turn a good answer into a 500.
    """
    try:
        deliberation = result.get("deliberation") or {}
        handoff = result.get("handoff") or {}
        evidence = result.get("evidence") or []
        trust = result.get("trust_score") or {}
        envelope = result.get("context_envelope") or {}
        event_id = new_id("ctx")
        now = utcnow()
        with connect() as conn:
            conn.execute(
                "INSERT INTO context_events ("
                "id, workspace_id, project_id, principal_id, surface, query, answer,"
                "answer_scope, answer_kind, answer_sufficient, model_provider,"
                "selected_lens, candidate_count, judged, candidates_json,"
                "evidence_ids_json, evidence_count, source_ids_json, confidence,"
                "trust_score, context_envelope_id, handoff_offered, handoff_files_json,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    workspace_id,
                    project_id,
                    principal_id,
                    surface,
                    query,
                    str(result.get("answer") or ""),
                    str(result.get("answer_scope") or ""),
                    str(result.get("answer_kind") or ""),
                    1 if result.get("answer_sufficient") else 0,
                    str((result.get("model") or {}).get("used") or ""),
                    str(deliberation.get("selected_lens") or ""),
                    int(deliberation.get("candidate_count") or 0),
                    1 if deliberation.get("judged") else 0,
                    json.dumps(deliberation.get("candidates") or []),
                    # Answers cite chunks; briefings cite memory units. Either id is
                    # what an outcome has to be traced back to.
                    json.dumps(
                        [
                            item.get("chunk_id") or item.get("memory_id")
                            for item in evidence
                            if item.get("chunk_id") or item.get("memory_id")
                        ]
                    ),
                    len(evidence),
                    json.dumps(
                        sorted(
                            {
                                str(item.get("source_id"))
                                for item in evidence
                                if item.get("source_id")
                            }
                        )
                    ),
                    float(result.get("confidence") or 0.0),
                    float(trust.get("score") or 0.0),
                    str(envelope.get("id") or ""),
                    1 if handoff else 0,
                    json.dumps(handoff.get("files") or []),
                    now,
                ),
            )
        return event_id
    except Exception:
        # Deliberately silent. The caller is mid-answer and has nothing useful to
        # do with a logging failure.
        return ""


def record_action(
    *,
    context_event_id: str,
    action_type: str,
    workspace_id: str = "",
    project_id: str = "",
    actor: str = "",
    surface: str = "",
    target: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log what was done with a served context. Raises if the context is unknown."""
    context = _context_row(context_event_id, workspace_id)
    action_id = new_id("act")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO action_events ("
            "id, context_event_id, workspace_id, project_id, action_type, actor,"
            "surface, target, detail_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                action_id,
                context_event_id,
                workspace_id or context["workspace_id"],
                project_id or context["project_id"],
                _slug(action_type),
                actor,
                surface,
                target,
                json.dumps(detail or {}),
                now,
            ),
        )
    return {
        "id": action_id,
        "context_event_id": context_event_id,
        "action_type": _slug(action_type),
        "created_at": now,
    }


def record_outcome(
    *,
    context_event_id: str,
    outcome: str,
    workspace_id: str = "",
    project_id: str = "",
    action_event_id: str = "",
    signal: str = "human",
    reason: str = "",
    detail: dict[str, Any] | None = None,
    observed_at: str = "",
) -> dict[str, Any]:
    """Log what happened after the action. Raises if the context is unknown."""
    context = _context_row(context_event_id, workspace_id)
    normalized = _slug(outcome)
    if normalized not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    outcome_id = new_id("out")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO outcome_events ("
            "id, context_event_id, action_event_id, workspace_id, project_id, outcome,"
            "signal, reason, detail_json, observed_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                outcome_id,
                context_event_id,
                action_event_id or None,
                workspace_id or context["workspace_id"],
                project_id or context["project_id"],
                normalized,
                signal,
                reason,
                json.dumps(detail or {}),
                observed_at or now,
                now,
            ),
        )
    return {
        "id": outcome_id,
        "context_event_id": context_event_id,
        "outcome": normalized,
        "reward": REWARDS[normalized],
        "created_at": now,
    }


def stats(workspace_id: str, project_id: str = "") -> dict[str, Any]:
    """Aggregate the loop's current state — including how much of it is still open.

    `closed_rate` is the number worth watching early on: a corpus is only as
    useful as the fraction of served contexts whose outcome is actually known.
    """
    where = "workspace_id=?"
    params: tuple[Any, ...] = (workspace_id,)
    if project_id:
        where += " AND project_id=?"
        params += (project_id,)

    contexts = rows(f"SELECT * FROM context_events WHERE {where}", params)
    if not contexts:
        return {
            "contexts": 0,
            "actions": 0,
            "outcomes": 0,
            "closed_rate": 0.0,
            "success_rate": 0.0,
            "by_lens": [],
            "by_scope": [],
            "trainable_examples": 0,
        }

    ids = {item["id"] for item in contexts}
    actions = [
        item for item in rows("SELECT * FROM action_events") if item["context_event_id"] in ids
    ]
    outcomes = [
        item for item in rows("SELECT * FROM outcome_events") if item["context_event_id"] in ids
    ]

    # Last outcome per context wins: an outcome can be revised (CI passes, then
    # the change is reverted) and the most recent observation is the true label.
    latest: dict[str, dict[str, Any]] = {}
    for item in sorted(outcomes, key=lambda record: record["created_at"]):
        latest[item["context_event_id"]] = item

    judged = [item for item in latest.values() if item["outcome"] != "unknown"]
    wins = [item for item in judged if item["outcome"] in ("succeeded", "partial")]

    return {
        "contexts": len(contexts),
        "actions": len(actions),
        "outcomes": len(outcomes),
        "closed_rate": round(len(latest) / len(contexts), 4),
        "success_rate": round(len(wins) / len(judged), 4) if judged else 0.0,
        "acceptance_rate": _acceptance_rate(actions),
        "by_lens": _breakdown(contexts, latest, "selected_lens"),
        "by_scope": _breakdown(contexts, latest, "answer_scope"),
        "by_model": _breakdown(contexts, latest, "model_provider"),
        "trainable_examples": sum(
            1
            for item in contexts
            if item["id"] in latest
            and latest[item["id"]]["outcome"] != "unknown"
            and int(item["candidate_count"] or 0) > 1
        ),
    }


def export_training_records(
    workspace_id: str,
    *,
    project_id: str = "",
    labelled_only: bool = True,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Emit the labelled corpus: one record per served context.

    `judge_example` is the payload that matters first — it is exactly the input
    the deliberation judge sees, plus the lens it picked and whether that choice
    worked out. That is a supervised training set for replacing the judge call
    with a small fine-tuned model.
    """
    where = "workspace_id=?"
    params: tuple[Any, ...] = (workspace_id,)
    if project_id:
        where += " AND project_id=?"
        params += (project_id,)

    contexts = rows(
        f"SELECT * FROM context_events WHERE {where} ORDER BY created_at DESC LIMIT ?",
        (*params, max(1, min(int(limit), 10_000))),
    )
    if not contexts:
        return []

    ids = {item["id"] for item in contexts}
    actions_by_context: dict[str, list[dict[str, Any]]] = {}
    for item in rows("SELECT * FROM action_events ORDER BY created_at"):
        if item["context_event_id"] in ids:
            actions_by_context.setdefault(item["context_event_id"], []).append(item)

    outcomes_by_context: dict[str, list[dict[str, Any]]] = {}
    for item in rows("SELECT * FROM outcome_events ORDER BY created_at"):
        if item["context_event_id"] in ids:
            outcomes_by_context.setdefault(item["context_event_id"], []).append(item)

    records = []
    for context in contexts:
        context_outcomes = outcomes_by_context.get(context["id"], [])
        label = context_outcomes[-1]["outcome"] if context_outcomes else "unknown"
        if labelled_only and label == "unknown":
            continue
        candidates = json.loads(context["candidates_json"] or "[]")
        record = {
            "context_event_id": context["id"],
            "project_id": context["project_id"],
            "surface": context["surface"],
            "query": context["query"],
            "answer": context["answer"],
            "answer_scope": context["answer_scope"],
            "answer_kind": context["answer_kind"],
            "model_provider": context["model_provider"],
            "evidence_ids": json.loads(context["evidence_ids_json"] or "[]"),
            "evidence_count": context["evidence_count"],
            "source_ids": json.loads(context["source_ids_json"] or "[]"),
            "confidence": context["confidence"],
            "trust_score": context["trust_score"],
            "handoff_offered": bool(context["handoff_offered"]),
            "handoff_files": json.loads(context["handoff_files_json"] or "[]"),
            "actions": [
                {
                    "action_type": item["action_type"],
                    "target": item["target"],
                    "surface": item["surface"],
                    "created_at": item["created_at"],
                }
                for item in actions_by_context.get(context["id"], [])
            ],
            "outcomes": [
                {
                    "outcome": item["outcome"],
                    "signal": item["signal"],
                    "reason": item["reason"],
                    "observed_at": item["observed_at"],
                }
                for item in context_outcomes
            ],
            "label": label,
            "reward": REWARDS.get(label, 0.0),
            "created_at": context["created_at"],
        }
        if len(candidates) > 1:
            record["judge_example"] = {
                "query": context["query"],
                "candidates": candidates,
                "selected_lens": context["selected_lens"],
                "judged": bool(context["judged"]),
                "label": label,
                "reward": REWARDS.get(label, 0.0),
            }
        records.append(record)
    return records


def _context_row(context_event_id: str, workspace_id: str) -> dict[str, Any]:
    found = rows(
        "SELECT id, workspace_id, project_id FROM context_events WHERE id=?",
        (context_event_id,),
    )
    if not found:
        raise LookupError("Unknown context event")
    context = found[0]
    # Workspace check happens here rather than at the route so that every writer
    # of this ledger gets it, including future non-HTTP callers.
    if workspace_id and context["workspace_id"] and context["workspace_id"] != workspace_id:
        raise LookupError("Unknown context event")
    return context


def _acceptance_rate(actions: list[dict[str, Any]]) -> float:
    decided = [
        item
        for item in actions
        if item["action_type"] in POSITIVE_ACTIONS or item["action_type"] in NEGATIVE_ACTIONS
    ]
    if not decided:
        return 0.0
    positive = sum(1 for item in decided if item["action_type"] in POSITIVE_ACTIONS)
    return round(positive / len(decided), 4)


def _breakdown(
    contexts: list[dict[str, Any]],
    latest: dict[str, dict[str, Any]],
    field: str,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = {}
    for context in contexts:
        key = str(context.get(field) or "—")
        bucket = buckets.setdefault(key, {"served": 0, "judged": 0, "wins": 0})
        bucket["served"] += 1
        outcome = latest.get(context["id"])
        if not outcome or outcome["outcome"] == "unknown":
            continue
        bucket["judged"] += 1
        if outcome["outcome"] in ("succeeded", "partial"):
            bucket["wins"] += 1
    return sorted(
        (
            {
                field: key,
                "served": value["served"],
                "judged": value["judged"],
                "success_rate": (
                    round(value["wins"] / value["judged"], 4) if value["judged"] else 0.0
                ),
            }
            for key, value in buckets.items()
        ),
        key=lambda item: item["served"],
        reverse=True,
    )


def _slug(value: str) -> str:
    return _SLUG_RE.sub("_", str(value or "").strip().lower()).strip("_")
