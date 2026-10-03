"""Answers that are correct without company evidence.

The retrieval stack is evidence-first by design: if the graph holds nothing, it
withholds. That is right for "why is checkout failing" and wrong for "hello" or
"what is a race condition" — refusing those makes the product feel broken rather
than careful. This module handles the two cases where withholding is the wrong
answer, and nothing else:

* **assistant** — greetings, thanks, and questions about MemoryWorks itself. Answered
  deterministically so they work with no model key configured.
* **general knowledge** — questions that are not about this company at all, used
  only after company retrieval has already come back empty.

Company questions never reach here while evidence exists, so the evidence
contract in :mod:`app.retrieval.reasoner` is unaffected.
"""

from __future__ import annotations

import re
from typing import Any

from app.llm import generate_grounded_json

# Every pattern is matched against the *whole* normalized query. Prefix matching
# is not safe here: "what is this repository about" opens like an identity
# question and is a real retrieval question, and answering it conversationally
# would silently swallow it.
GREETING_RE = re.compile(
    r"(?:hi|hii+|hey+|hello+|yo|sup|howdy|hiya|namaste|"
    r"good (?:morning|afternoon|evening|day))"
    r"(?: (?:there|team|orgmemory|everyone|folks|all))?",
)
THANKS_RE = re.compile(
    r"(?:thanks|thank you|thanks a lot|thank you so much|thx|ty|cheers|"
    r"appreciate it|appreciated|nice|great|cool|awesome|perfect|got it)"
    r"(?: (?:orgmemory|so much|a lot))?",
)
FAREWELL_RE = re.compile(r"(?:bye|goodbye|see you|see ya|later|good ?night|gn)(?: orgmemory)?")
WELLBEING_RE = re.compile(
    r"(?:how are (?:you|u)(?: doing)?|hows it going|how is it going|whats up|sup man)",
)
IDENTITY_RE = re.compile(
    r"(?:who|what) (?:are|is) (?:you|orgmemory)|introduce yourself|"
    r"tell me about (?:yourself|orgmemory)",
)
CAPABILITY_RE = re.compile(
    r"what can (?:you|orgmemory) do(?: for me)?|what do you do|"
    r"how do (?:you|i) (?:work|use (?:you|this|orgmemory))|help|"
    r"what are you for|how does (?:this|orgmemory) work",
)

# Tokens that mean "this question is about *our* company", so a general-knowledge
# answer would be a fabrication rather than a helpful fallback.
COMPANY_MARKERS = (
    "our ",
    "we ",
    "us ",
    "my team",
    "the team",
    "this repo",
    "this repository",
    "this service",
    "this project",
    "this codebase",
    "our codebase",
    # "Are there any payments in the repos?" is about the connected code, not
    # about repositories in general — "what is a repository" stays general.
    "the repo",
    "my repo",
    "these repo",
    "those repo",
    "connected repo",
    "codebase",
    "internal",
    "runbook",
    "on-call",
    "oncall",
    "incident",
    "postmortem",
    "deploy",
    "deployment",
    "staging",
    "production",
    "prod ",
    "pull request",
    "commit",
    "slack",
    "jira",
    "confluence",
    "sprint",
    "who owns",
    "who is responsible",
    "changelog",
    "release note",
)
COMPANY_TOKEN_RE = re.compile(
    r"@\w+"  # mentions
    r"|#[\w-]+"  # channels
    r"|\b(?:PR|MR)\s*#?\d+\b"  # pull request references
    r"|\b[A-Z]{2,}-\d+\b"  # ticket keys
    r"|\b[\w-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|yml|yaml|json|toml|md)\b"  # files
    r"|\b\w+_service\b"
    r"|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b",  # ENV_VARS
)

ASSISTANT_CAUSE = "Not applicable — this is a conversational reply, not a memory lookup."
GENERAL_CAUSE = "Not applicable — this answer comes from general knowledge, not company memory."

_INTRO = (
    "I'm MemoryWorks — your company's brain. I read the systems your company already "
    "works in (code, conversations, documents, and decisions), keep what is current, "
    "and answer questions from that memory with the sources attached."
)


def assistant_reply(query: str) -> dict[str, Any] | None:
    """Return a conversational answer, or None when this is a real question.

    Deterministic on purpose: a greeting must never depend on an API key being
    present, and must never spend a model call.
    """
    lowered = _normalize(query)
    if not lowered or len(lowered.split()) > 8:
        return None
    if GREETING_RE.fullmatch(lowered):
        return _reply(
            f"Hello. {_INTRO}\n\n"
            "Ask me something your company would know — an incident, a decision, an "
            "owner, a change — or ask a general question and I'll answer that too."
        )
    if THANKS_RE.fullmatch(lowered):
        return _reply("Anytime. Ask whenever you need company context.")
    if FAREWELL_RE.fullmatch(lowered):
        return _reply("See you. Your memory stays current while you're away.")
    if WELLBEING_RE.fullmatch(lowered):
        return _reply("Running well and connected to your memory. What do you need?")
    if IDENTITY_RE.fullmatch(lowered):
        return _reply(_INTRO)
    if CAPABILITY_RE.fullmatch(lowered):
        return _reply(
            f"{_INTRO}\n\n"
            "Practically, that means four things:\n"
            "- Answer questions about your company with the sources attached.\n"
            "- Explain what changed, who owns it, and what was decided.\n"
            "- Diagnose an incident from the evidence that already exists.\n"
            "- Hand a scoped task and its context to your editor when a fix is needed.\n\n"
            "Connect a source first, and every answer gets sharper."
        )
    return None


def is_company_question(query: str) -> bool:
    """True when the question is about this company specifically.

    Used to decide whether an empty retrieval should fall back to general
    knowledge or stay honest about missing company memory.
    """
    lowered = _normalize(query)
    if any(marker in f"{lowered} " for marker in COMPANY_MARKERS):
        return True
    return bool(COMPANY_TOKEN_RE.search(query))


def general_knowledge_answer(
    query: str,
    *,
    model_provider: str | None = None,
) -> dict[str, Any] | None:
    """Answer from the model's own knowledge, clearly labelled as such.

    Returns None when no model is configured or the model declines, so the
    caller falls back to the honest "not enough company memory" response.
    """
    prompt = (
        "You are MemoryWorks, a company brain. The question below is NOT about the "
        "user's company, and company memory holds nothing relevant to it. Answer it "
        "from your own general knowledge, accurately and concisely. Do not invent "
        "anything about the user's company, their code, or their team. If you are "
        "genuinely unsure of the fact, say so plainly. Return JSON with answer "
        "(string) and confident (boolean).\n"
        f"Question: {query}"
    )
    try:
        generated = generate_grounded_json(prompt, model_provider)
    except Exception:
        return None
    if not generated:
        return None
    result, provider = generated
    answer = str(result.get("answer") or "").strip()
    if not answer:
        return None
    return {
        "answer": answer,
        "likely_cause": GENERAL_CAUSE,
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "answer_scope": "general_knowledge",
        "supporting_chunk_ids": [],
        "_model_provider": provider.id,
        "trust_score": {
            "score": 0.6 if result.get("confident") else 0.4,
            "level": "general",
            "reason": (
                "Answered from the model's general knowledge. No company source "
                "supports this, so treat it as background, not company truth."
            ),
            "factors": {},
            "contradictions": [],
        },
    }


def _reply(answer: str) -> dict[str, Any]:
    return {
        "answer": answer,
        "likely_cause": ASSISTANT_CAUSE,
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "answer_scope": "assistant",
        "supporting_chunk_ids": [],
        "trust_score": {
            "score": 1.0,
            "level": "assistant",
            "reason": "Conversational reply about MemoryWorks itself.",
            "factors": {},
            "contradictions": [],
        },
    }


def _normalize(query: str) -> str:
    cleaned = query.casefold().replace("@runbook", " ").replace("@orgmemory", " ")
    # Apostrophes are dropped rather than split on, so "how's it going" stays one
    # phrase instead of becoming the two-token "how s it going".
    cleaned = cleaned.replace("'", "").replace("’", "")
    cleaned = re.sub(r"[^\w\s@#/.-]+", " ", cleaned)
    return " ".join(cleaned.split()).strip()
