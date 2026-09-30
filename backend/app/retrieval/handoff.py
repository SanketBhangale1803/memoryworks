"""Package an answer as a task an editor agent can start from.

When someone asks MemoryWorks to fix, patch, or review something, the useful
output is not prose — it is a scoped instruction plus the minimum context needed
to act, in a form that can be pasted into Cursor, Copilot, Claude Code, or sent
through MCP. Handing over the entire evidence pack defeats the point: the value
is deciding which few sources actually matter for the requested change.

Built deterministically from evidence already selected for the answer, so it
never costs an extra model call and never introduces an unsourced claim.
"""

from __future__ import annotations

import re
from typing import Any

from app.graph.base import GraphEvidence
from app.skills import as_prompt_section, matches

ACTION_RE = re.compile(
    r"\b(fix|patch|repair|resolve|debug|implement|add|change|update|refactor|migrate|"
    r"revert|rollback|merge|review|write|remove|delete|rename|upgrade|bump|"
    # Verbs people actually use for interface changes.
    r"restyle|redesign|adjust|tweak|replace|resize|realign|align|centre|center|"
    r"increase|decrease|move|set|make)\b",
    re.I,
)
CODE_SUBJECT_RE = re.compile(
    r"\b(bug|error|failure|failing|broken|crash|exception|regression|test|tests|pr|"
    r"pull request|branch|endpoint|function|class|module|config|configuration|"
    r"dependency|import|type|schema|migration|"
    # Interface work is code work. Without this vocabulary the whole frontend of
    # every product is invisible to the handoff, because "change the background
    # colour" names no function, module, or file.
    r"colour|color|background|bg|style|styles|styling|stylesheet|css|theme|layout|"
    r"font|spacing|padding|margin|button|header|footer|nav|navbar|sidebar|modal|"
    r"page|screen|view|component|ui|icon|logo|animation|responsive|dark mode|"
    r"light mode|copy|label|placeholder)\b",
    re.I,
)
# Naming an editor or coding agent *is* the delegation. "Ask cursor to change X"
# is a handoff request even when X names nothing the other patterns recognise.
EDITOR_RE = re.compile(
    r"\b(cursor|copilot|claude code|codex|windsurf|cline|aider|vs ?code|editor)\b",
    re.I,
)
CODE_SOURCE_TYPES = {
    "repo_file",
    "github_file",
    "github_commit",
    "github_pull_request",
    "github_issue",
    "code",
    "repository_metadata",
}
FILE_PATH_RE = re.compile(
    r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|go|rs|java|rb|yml|yaml|json|toml|sql|"
    # Web and native sources. Their absence made a request naming globals.css read
    # as if it named no file at all.
    r"css|scss|sass|less|html|htm|vue|svelte|c|h|cpp|hpp|cs|php|swift|kt|sh|tf)\b"
)
# Metadata "path" is only trusted when it actually looks like one. Some source
# types carry a human title there, and "Checkout 502 incident" is not a file.
PATHLIKE_RE = re.compile(r"^[\w./-]+\.[A-Za-z0-9]{1,10}$")

MAX_CONTEXT_ITEMS = 4
EXCERPT_CHARS = 700


def build_handoff(
    query: str,
    grounded: dict[str, Any],
    evidence: list[GraphEvidence],
    *,
    pinned_project_id: str = "",
) -> dict[str, Any] | None:
    """Return an editor-ready task envelope, or None when the ask is not actionable.

    Requires both an actionable request and code-shaped evidence: "why did we
    choose Postgres" is answered, not handed to an editor.

    `pinned_project_id` is the repository the asker explicitly chose. Evidence may
    legitimately be wider than that — the answer is allowed to draw on the whole
    workspace — but the task must not be. A handoff becomes a real commit, so
    aiming it at a repository nobody selected is worse than not offering one.
    """
    if not grounded.get("sufficient") or not evidence:
        return None
    if not _is_actionable(query):
        return None

    # What qualifies is context that locates the work in code — a code source, or
    # any source that names a file. An incident report naming checkout.py is
    # exactly the context an editor agent needs; its source type is beside the point.
    code_evidence = [item for item in evidence if _locates_code(item)]
    if not code_evidence:
        return None

    # An explicit choice is a boundary, not a hint. If widening the search was
    # what turned up the code, the answer keeps that context but the task does
    # not: better to offer no handoff than to commit to the wrong checkout.
    if pinned_project_id:
        code_evidence = [
            item
            for item in code_evidence
            if str(item.metadata.get("project_id") or "") == pinned_project_id
        ]
        if not code_evidence:
            return None

    # Retrieval searches the whole workspace, so evidence can span repositories.
    # A task is applied to exactly one checkout, so mixing them produces a handoff
    # naming files that do not exist in the repository being edited. Keep only the
    # best-supported project's evidence.
    code_evidence, project_id, repository = _single_project(code_evidence)

    context = [
        {
            "title": item.source_title,
            "source_type": item.source_type,
            "url": item.source_url,
            "excerpt": _excerpt(item.text),
        }
        for item in code_evidence[:MAX_CONTEXT_ITEMS]
    ]
    files = _files(code_evidence)
    steps = [str(step) for step in grounded.get("safe_actions", []) if str(step).strip()][:6]
    cause = str(grounded.get("likely_cause") or "").strip()
    # The librarian's job: decide which few precedents are worth putting on the
    # desk. Only skills that have already worked more than once qualify.
    precedents = matches(project_id, query) if project_id else []

    return {
        "target": "editor",
        # Which checkout this task belongs to. Execution uses these rather than
        # whichever project the asker happened to have selected.
        "project_id": project_id,
        "repository": repository,
        "title": _title(query),
        "task": _task_line(query),
        "why": cause if cause and not cause.startswith("Not applicable") else "",
        "steps": steps,
        "files": files,
        "context": context,
        "approval_required": [
            str(item) for item in grounded.get("approval_required", []) if str(item).strip()
        ][:6],
        # Surfaced so the chat can show what precedent was applied, and so the
        # run can report back whether following it actually worked.
        "precedents": [
            {
                "id": item["id"],
                "trigger": item["trigger"],
                "successes": item["successes"],
                "confidence": item["confidence"],
            }
            for item in precedents
        ],
        "skill_ids": [item["id"] for item in precedents],
        "prompt": _prompt(query, cause, steps, files, context, precedents),
    }


def _single_project(
    evidence: list[GraphEvidence],
) -> tuple[list[GraphEvidence], str, str]:
    """Narrow evidence to one repository — the one the ranking most supports.

    Ties are broken by rank rather than count, so a single strong hit in the
    right repository beats several weak ones in a neighbouring project.
    """
    weights: dict[str, float] = {}
    for position, item in enumerate(evidence):
        key = str(item.metadata.get("project_id") or "")
        weights[key] = weights.get(key, 0.0) + 1.0 / (position + 1)
    if len(weights) <= 1:
        first = evidence[0].metadata if evidence else {}
        return evidence, str(first.get("project_id") or ""), str(first.get("repository") or "")

    winner = max(weights, key=lambda key: weights[key])
    kept = [item for item in evidence if str(item.metadata.get("project_id") or "") == winner]
    repository = next(
        (
            str(item.metadata.get("repository") or "")
            for item in kept
            if item.metadata.get("repository")
        ),
        "",
    )
    return kept, winner, repository


def _locates_code(item: GraphEvidence) -> bool:
    if item.source_type in CODE_SOURCE_TYPES:
        return True
    if _metadata_path(item):
        return True
    return bool(FILE_PATH_RE.search(item.source_title) or FILE_PATH_RE.search(item.text))


def _metadata_path(item: GraphEvidence) -> str:
    path = str(item.metadata.get("path") or item.metadata.get("file_path") or "").strip()
    return path if PATHLIKE_RE.match(path) else ""


def _is_actionable(query: str) -> bool:
    """An action verb plus something that anchors it to the codebase.

    The verb alone is not enough — "update the on-call rota" is not code work.
    The anchor can be a code subject, a file path, or an explicitly named editor.
    """
    cleaned = query.replace("@orgmemory", " ").replace("@runbook", " ")
    if not ACTION_RE.search(cleaned):
        return False
    return bool(
        CODE_SUBJECT_RE.search(cleaned) or FILE_PATH_RE.search(cleaned) or EDITOR_RE.search(cleaned)
    )


def _title(query: str) -> str:
    cleaned = " ".join(query.replace("@orgmemory", " ").replace("@runbook", " ").split())
    return cleaned[:90] + ("…" if len(cleaned) > 90 else "")


def _task_line(query: str) -> str:
    cleaned = " ".join(query.replace("@orgmemory", " ").replace("@runbook", " ").split())
    return cleaned.rstrip("?.! ") or "Apply the change described below."


def _files(evidence: list[GraphEvidence]) -> list[str]:
    found: list[str] = []
    for item in evidence:
        metadata_path = _metadata_path(item)
        paths = (
            [metadata_path]
            if metadata_path
            else FILE_PATH_RE.findall(item.source_title) or FILE_PATH_RE.findall(item.text)
        )
        for path in paths:
            if path and path not in found:
                found.append(path)
    return found[:8]


def _excerpt(text: str) -> str:
    cleaned = text.strip()
    return cleaned[:EXCERPT_CHARS] + ("\n…" if len(cleaned) > EXCERPT_CHARS else "")


def _prompt(
    query: str,
    cause: str,
    steps: list[str],
    files: list[str],
    context: list[dict[str, Any]],
    precedents: list[dict[str, Any]] | None = None,
) -> str:
    """A single pasteable block: the instruction first, then only what supports it."""
    parts = [f"Task: {_task_line(query)}"]
    if precedents:
        parts.append(as_prompt_section(precedents))
    if cause and not cause.startswith("Not applicable"):
        parts.append(f"\nWhat the company's memory says is going on:\n{cause}")
    if steps:
        rendered = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
        parts.append(f"\nSteps established from company sources:\n{rendered}")
    if files:
        parts.append("\nFiles involved:\n" + "\n".join(f"- {path}" for path in files))
    if context:
        blocks = "\n\n".join(
            f"--- {item['title']} ({item['source_type']})"
            + (f"\n{item['url']}" if item["url"] else "")
            + f"\n{item['excerpt']}"
            for item in context
        )
        parts.append(f"\nRelevant company context:\n{blocks}")
    parts.append(
        "\nWork only from the context above. If something needed is missing, say what "
        "is missing instead of assuming it."
    )
    return "\n".join(parts)
