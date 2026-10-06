"""Answer a question several independent ways, then judge which one fits.

A single synthesis pass optimizes for the question's wording. The same evidence
often supports a better answer at a different altitude: the asker who types "why
is checkout failing" may want the cause, the fix, or the honest list of what the
evidence does not establish. Rather than guess once, this module runs the
grounded synthesis under several lenses in parallel and then asks a judge which
candidate actually answers what was asked.

Every candidate is produced under the same evidence contract as the single-pass
path, so judging can only pick between grounded answers — it can never introduce
an ungrounded one. When no model is configured, when the feature is disabled, or
when the judge call fails, this degrades to exactly the previous behaviour.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

from app.graph.base import GraphEvidence
from app.llm import carry_budget, generate_grounded_json

from .reasoner import llm_answer

# Lenses are deliberately different *readings of the question*, not different
# writing styles: two candidates that differ only in tone give the judge nothing
# to choose between.
LENSES: list[dict[str, str]] = [
    {
        "id": "direct",
        "instruction": (
            "Answer the question as literally and directly as the evidence allows. "
            "State the facts that answer it and stop."
        ),
    },
    {
        "id": "causal",
        "instruction": (
            "Answer by explaining the causal chain the evidence supports: what "
            "changed, what it affected, and why that produces what was asked about."
        ),
    },
    {
        "id": "procedural",
        "instruction": (
            "Answer in terms of what the asker should do next, grounded in the "
            "evidence: the concrete steps, checks, or files involved."
        ),
    },
    {
        "id": "skeptical",
        "instruction": (
            "Answer conservatively. State only what the evidence firmly establishes "
            "and name explicitly what it does not, so nothing is over-claimed."
        ),
    },
    {
        "id": "synthesis",
        "instruction": (
            "Answer by connecting evidence across different sources into the single "
            "coherent picture they collectively describe."
        ),
    },
]

MAX_CANDIDATES = len(LENSES)
# Judges read a summary, not whole answers: a long candidate should not win on
# length alone, and the prompt stays inside a small context.
JUDGE_EXCERPT_CHARS = 1200


def deliberate(
    query: str,
    evidence: list[GraphEvidence],
    *,
    compiled_context: dict[str, Any] | None = None,
    model_provider: str | None = None,
    candidate_count: int = 5,
    judge_enabled: bool = True,
    deadline_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Return the best grounded answer among several, or None when none succeed."""

    count = max(1, min(int(candidate_count or 1), MAX_CANDIDATES))
    if count == 1:
        return llm_answer(
            query,
            evidence,
            compiled_context=compiled_context,
            model_provider=model_provider,
        )

    lenses = LENSES[:count]
    pool = ThreadPoolExecutor(max_workers=count)
    futures = [
        (
            lens["id"],
            # Each candidate's call counts against the answer's model budget.
            pool.submit(
                carry_budget(llm_answer),
                query,
                evidence,
                compiled_context=compiled_context,
                model_provider=model_provider,
                lens=lens["instruction"],
            ),
        )
        for lens in lenses
    ]
    # The asker is waiting on the slowest candidate, so the pass has a deadline:
    # whatever finished in time is judged, and stragglers are abandoned (their
    # threads run out on their own; nothing waits for them).
    wait([future for _, future in futures], timeout=deadline_seconds)
    pool.shutdown(wait=False, cancel_futures=True)
    produced = [
        (lens_id, future.result())
        for lens_id, future in futures
        if future.done() and not future.cancelled() and future.exception() is None
    ]

    candidates = [(lens_id, answer) for lens_id, answer in produced if answer]
    if not candidates:
        return None

    # A candidate that withheld is evidence about the question, not a usable
    # answer — keep those out of the running unless every candidate withheld.
    usable = [item for item in candidates if item[1].get("sufficient")] or candidates

    chosen_index = 0
    judgement: dict[str, Any] = {}
    if judge_enabled and len(usable) > 1:
        chosen_index, judgement = _judge(query, usable, model_provider)

    lens_id, winner = usable[chosen_index]
    winner = dict(winner)
    winner["_deliberation"] = {
        "candidate_count": len(candidates),
        "considered": len(usable),
        "selected_lens": lens_id,
        "judged": bool(judgement),
        "reason": str(judgement.get("reason") or "")
        or "Selected without a judge pass — no alternative candidate was usable.",
        "candidates": [
            {
                "lens": item_lens,
                "sufficient": bool(item.get("sufficient")),
                "selected": index == chosen_index,
                "excerpt": str(item.get("answer") or "")[:280],
            }
            for index, (item_lens, item) in enumerate(usable)
        ],
    }
    return winner


def _judge(
    query: str,
    candidates: list[tuple[str, dict[str, Any]]],
    model_provider: str | None,
) -> tuple[int, dict[str, Any]]:
    """Pick the candidate that best answers the asker's actual need."""

    listing = "\n\n".join(
        f"[{index + 1}]\n{str(answer.get('answer') or '')[:JUDGE_EXCERPT_CHARS]}"
        for index, (_, answer) in enumerate(candidates)
    )
    prompt = (
        "You are selecting between candidate answers to one question. Every "
        "candidate was written from the same company evidence, so do not judge on "
        "tone or length. Pick the one that best answers what the asker actually "
        "needs: it must be accurate, must not over-claim beyond the evidence, and "
        "must address the question that was asked rather than an adjacent one. "
        "Return JSON with choice (the 1-based number of the best candidate) and "
        "reason (one sentence explaining the choice).\n"
        f"Question: {query}\n\nCandidates:\n{listing}"
    )
    try:
        generated = generate_grounded_json(prompt, model_provider)
    except Exception:
        return 0, {}
    if not generated:
        return 0, {}
    result, _ = generated
    raw_choice = result.get("choice")
    try:
        choice = int(raw_choice)
    except (TypeError, ValueError):
        return 0, {}
    if not 1 <= choice <= len(candidates):
        return 0, {}
    return choice - 1, {"reason": str(result.get("reason") or "").strip()}
