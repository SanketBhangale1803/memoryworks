"""Editor pre-tool hooks that serve a MemoryWorks briefing before an agent acts.

``orgmemory hook claude-code`` is a Claude Code ``PreToolUse`` command hook and
``orgmemory hook cursor`` is a Cursor ``beforeShellExecution`` hook. Both read
the editor's JSON event on stdin, decide whether the step is consequential, ask
the same ``POST /api/briefings`` endpoint that ``get_orgmemory_briefing`` wraps,
and hand the result back to the agent with the instruction to report the result
through ``record_orgmemory_outcome`` (or ``orgmemory outcome``).

A briefing is advice, never authorization, so these hooks never answer
``allow``. In Claude Code they either add context and leave the normal
permission flow alone, or — when the briefing says a person has to agree —
escalate to ``ask``. Cursor has no documented "no decision" answer, so the
Cursor hook only ever escalates to ``ask`` and is meant to be matched to
consequential commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from .client import MemoryWorks, env_setting
from .exceptions import MemoryWorksError

# Shell commands that change shared state. The list is intentionally about the
# command, not the wording of a task: the server applies its own consequential
# verb list to the intent we send.
CONSEQUENTIAL_COMMAND_RE = re.compile(
    r"""(
        \bgit\s+(push|merge|rebase|reset\s+--hard|tag|branch\s+-D|clean\s+-[a-z]*f)
      | \bgh\s+(pr\s+merge|release|workflow\s+run|repo\s+delete)
      | \bkubectl\s+(apply|create|delete|scale|rollout|patch|replace|drain|cordon|edit|set)
      | \bhelm\s+(install|upgrade|uninstall|rollback|delete)
      | \bterraform\s+(apply|destroy|import|state\s+(rm|mv))
      | \bpulumi\s+(up|destroy)
      | \b(docker|podman)\s+(push|rm|rmi|system\s+prune)
      | \b(npm|pnpm|yarn)\s+publish | \btwine\s+upload | \bcargo\s+publish
      | \b(alembic\s+(upgrade|downgrade)|manage\.py\s+(migrate|flush)|rails\s+db:|prisma\s+migrate)
      | \baws\s+\S+\s+(delete|terminate|put|update|create)
      | \bgcloud\s+.*\b(deploy|delete|create|update)\b
      | \b(fly|vercel|netlify|heroku)\s+.*\b(deploy|scale|destroy|--prod)\b
      | \b(drop|truncate)\s+(table|database|schema)\b | \bdelete\s+from\b
      | \brm\s+-[a-z]*r[a-z]*f | \brm\s+-[a-z]*f[a-z]*r
      | \b(systemctl|service)\s+\S*\s*(restart|stop|disable)
      | \b(deploy|rollback|release|migrate|failover|backfill|reindex)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
STATE_TTL_SECONDS = 12 * 60 * 60
TASK_LIMIT = 2_000


def is_consequential_command(command: str) -> bool:
    return bool(CONSEQUENTIAL_COMMAND_RE.search(command or ""))


def _relative(path: str, cwd: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(cwd).resolve()))
    except (ValueError, OSError):
        return path


def intent_for_claude_code(
    event: Mapping[str, Any], *, brief_edits: bool = True
) -> tuple[str, str]:
    """Return ``(task, dedupe_key)`` for a Claude Code PreToolUse event, or ``("", "")``."""
    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") or {}
    cwd = str(event.get("cwd") or os.getcwd())
    where = Path(cwd).name or cwd
    if tool == "Bash":
        command = str(tool_input.get("command") or "").strip()
        if not is_consequential_command(command):
            return "", ""
        return (
            f"Run shell command in {where}: {command}"[:TASK_LIMIT],
            f"bash:{command}",
        )
    if tool in EDIT_TOOLS and brief_edits:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not path:
            return "", ""
        relative = _relative(path, cwd)
        # One briefing per file per session: the context for "edit this file"
        # does not change between the first and the tenth edit to it.
        return f"Edit {relative} in {where}"[:TASK_LIMIT], f"edit:{relative}"
    return "", ""


def intent_for_cursor(event: Mapping[str, Any]) -> tuple[str, str]:
    command = str(event.get("command") or "").strip()
    if not command:
        return "", ""
    cwd = str(event.get("cwd") or os.getcwd())
    where = Path(cwd).name or cwd
    return f"Run shell command in {where}: {command}"[:TASK_LIMIT], f"bash:{command}"


# ---------------------------------------------------------------------- dedupe


def _state_path(session: str) -> Path:
    digest = hashlib.sha256(session.encode()).hexdigest()[:24]
    root = Path(os.getenv("MEMORYWORKS_HOOK_STATE_DIR") or tempfile.gettempdir())
    return root / f"memoryworks-hook-{digest}.json"


def _load_state(session: str) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {
        key: value
        for key, value in state.items()
        if isinstance(value, dict)
        and now - float(value.get("at") or 0) < STATE_TTL_SECONDS
    }


def _save_state(session: str, state: dict[str, Any]) -> None:
    try:
        path = _state_path(session)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # dedupe is a convenience; a briefing must still be served


# --------------------------------------------------------------------- render


def _citation_lines(briefing: Mapping[str, Any], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for key in (
        "must_read",
        "constraints",
        "prior_incidents",
        "blast_radius",
        "procedures",
    ):
        for item in briefing.get(key) or []:
            if isinstance(item, Mapping) and item.get("memory_id"):
                lines.append(
                    f"- [{item.get('memory_id')}] {item.get('subject') or ''} — "
                    f"{item.get('why_it_matters') or ''}"
                )
    if len(lines) > limit:
        lines = [*lines[:limit], f"- …{len(lines) - limit} more cited memories"]
    return lines


def render_context(briefing: Mapping[str, Any], *, surface: str) -> str:
    briefing_id = briefing.get("briefing_id") or ""
    verdict = briefing.get("verdict") or ""
    lines = [
        f"MemoryWorks briefing ({verdict}), briefing_id={briefing_id or 'not recorded'}:",
        str(briefing.get("headline") or ""),
    ]
    if verdict == "no_memory":
        lines.append(
            "no_memory is not permission to act, and retrieval health is not yet reported, "
            "so it may also mean the index could not be read."
        )
    reasons = briefing.get("requires_approval") or []
    if reasons:
        lines.append("A person has to agree first:")
        lines.extend(f"- {reason}" for reason in reasons)
    lines.extend(_citation_lines(briefing))
    questions = briefing.get("open_questions") or []
    if questions:
        lines.append("Open questions:")
        lines.extend(f"- {question}" for question in questions)
    if briefing_id:
        lines.append(
            "After this step, report what happened: call record_orgmemory_outcome("
            f'briefing_id="{briefing_id}", action=<what you did>, outcome=<succeeded|failed|'
            f'partial|abandoned|unknown>, surface="{surface}", reason=<evidence>) or run '
            f"`orgmemory outcome {briefing_id} <action> --outcome <label> --surface {surface}`."
        )
    return "\n".join(line for line in lines if line)


def _unavailable(reason: str) -> str:
    return (
        f"MemoryWorks briefing unavailable ({reason}). Company constraints for this step are "
        "unknown — do not read this as permission; confirm with a person before a "
        "consequential action."
    )


def _ask_reason(seen: Mapping[str, Any]) -> str:
    return (
        f"MemoryWorks: {seen.get('headline') or 'a person has to agree first'} "
        f"(briefing {seen.get('briefing_id') or 'not recorded'})"
    )


# ----------------------------------------------------------------------- serve


def serve(
    task: str,
    *,
    surface: str,
    client: MemoryWorks | None = None,
    service: str = "",
    project_id: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Return ``(briefing, error)``. Never raises for API or network failures."""
    owned = client is None
    if client is None:
        if not env_setting("API_KEY"):
            return None, "MEMORYWORKS_API_KEY is not set"
        client = MemoryWorks(timeout=float(os.getenv("MEMORYWORKS_HOOK_TIMEOUT") or 8))
    try:
        served = client.get_briefing(
            task,
            service=service or env_setting("SERVICE"),
            project_id=project_id or env_setting("PROJECT_ID"),
            surface=surface,
        )
        return dict(served.raw), ""
    except (MemoryWorksError, httpx.HTTPError) as exc:
        return None, str(exc) or exc.__class__.__name__
    finally:
        if owned:
            client.close()


def claude_code(
    event: Mapping[str, Any],
    *,
    client: MemoryWorks | None = None,
    service: str = "",
    project_id: str = "",
) -> dict[str, Any] | None:
    """Handle one Claude Code PreToolUse event. ``None`` means print nothing."""
    brief_edits = (os.getenv("MEMORYWORKS_HOOK_BRIEF_EDITS") or "true").lower() not in {
        "0",
        "false",
        "no",
    }
    task, key = intent_for_claude_code(event, brief_edits=brief_edits)
    if not task:
        return None
    session = str(event.get("session_id") or "no-session")
    state = _load_state(session)
    seen = state.get(key)
    if seen:
        # Already briefed for this exact step in this session: no second
        # ledger row, but a step that needed a person still needs one.
        if seen.get("verdict") != "requires_approval":
            return None
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": _ask_reason(seen),
            }
        }

    briefing, error = serve(
        task,
        surface="claude_code",
        client=client,
        service=service,
        project_id=project_id,
    )
    output: dict[str, Any] = {"hookEventName": "PreToolUse"}
    if briefing is None:
        output["additionalContext"] = _unavailable(error)
        return {"hookSpecificOutput": output}

    seen = {
        "at": time.time(),
        "briefing_id": str(briefing.get("briefing_id") or ""),
        "verdict": str(briefing.get("verdict") or ""),
        "headline": str(briefing.get("headline") or ""),
    }
    state[key] = seen
    _save_state(session, state)
    output["additionalContext"] = render_context(briefing, surface="claude_code")
    if seen["verdict"] == "requires_approval":
        # Escalate to the person at the keyboard. Never "allow": the briefing
        # is not an approval, and the user's own permission rules still apply.
        output["permissionDecision"] = "ask"
        output["permissionDecisionReason"] = _ask_reason(seen)
    return {"hookSpecificOutput": output}


def cursor(
    event: Mapping[str, Any],
    *,
    client: MemoryWorks | None = None,
    service: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """Handle one Cursor beforeShellExecution event.

    Always answers ``ask``: Cursor documents no "no decision" output, and
    answering ``allow`` would turn advice into authorization.
    """
    task, _ = intent_for_cursor(event)
    if not task:
        return {"permission": "ask"}
    briefing, error = serve(
        task, surface="cursor", client=client, service=service, project_id=project_id
    )
    if briefing is None:
        message = _unavailable(error)
        return {"permission": "ask", "user_message": message, "agent_message": message}
    return {
        "permission": "ask",
        "user_message": (
            f"MemoryWorks ({briefing.get('verdict')}): {briefing.get('headline') or ''} "
            f"[briefing {briefing.get('briefing_id') or 'not recorded'}]"
        ),
        "agent_message": render_context(briefing, surface="cursor"),
    }
