"""Apply a handoff to real code with a headless coding agent, then commit it.

This is the step that turns MemoryWorks from something that *explains* into
something that *acts*. A handoff already contains the task and exactly the
context needed to do it; this module hands that to `cursor-agent` or `claude`
running non-interactively in a throwaway clone, captures what changed, and
commits it on a branch.

The safety model is deliberately blunt, because the failure mode of an
autonomous code editor is destroying work:

* **Never the user's checkout.** Every run clones into its own directory under
  `settings.execution_dir`. The ingest cache is read to build memory and is
  never handed to an agent.
* **Never the default branch.** A run always creates `orgmemory/<slug>` and
  commits there, so nothing lands on main by accident.
* **Committing is local; pushing is not.** A local commit on a throwaway clone
  is free to discard. Publishing to a shared remote is visible to other people,
  so it needs `allow_push` to be turned on explicitly.
* **No changes is a real outcome, not an error.** An agent that decides nothing
  needs doing is reported as `no_changes`, never as a fabricated success.

Every run also writes itself into the outcome ledger. That is the point: a
commit that survives is the strongest signal that the retrieved context was
right, and it is recorded without anyone clicking anything.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.database import connect, decode, new_id, rows, utcnow
from app.outcomes import record_action, record_outcome
from app.skills import distil, record_use

# Executors are shelled out to rather than imported: they are separately
# installed CLIs with their own auth, and a missing login must surface as a
# clear message rather than an import error.
EXECUTORS: dict[str, list[str]] = {
    # -f allows the agent to run commands unless explicitly denied.
    "cursor": ["cursor-agent", "-p", "--force", "--output-format", "text"],
    "claude": ["claude", "-p", "--permission-mode", "bypassPermissions", "--output-format", "text"],
}
# Statuses that mean the working tree was actually changed and recorded.
SUCCESS_STATUSES = {"committed", "pushed"}
MAX_DIFF_CHARS = 60_000
MAX_OUTPUT_CHARS = 20_000
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# CLIs that expect a terminal paint their UI with escape sequences. Captured to a
# pipe those become unreadable noise, which is exactly what a "not signed in"
# splash screen turned into on its way to the chat.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|[\x00-\x08\x0b-\x1f\x7f]")
# Signatures of an unauthenticated agent. Each CLI says it differently, and none
# of them exit with a distinguishable status code.
_LOGIN_MARKERS = (
    "press any key to sign in",
    "not logged in",
    "not authenticated",
    "please log in",
    "please login",
    "run `cursor-agent login`",
    "unauthorized",
)
_LOGIN_HELP = {
    "cursor": "Cursor is not signed in. Run `cursor-agent login` in your terminal, then try again.",
    "claude": "Claude Code is not signed in. Run `claude` in your terminal and sign in, then try again.",
}


class ExecutionError(RuntimeError):
    """Raised when a run cannot even be started."""


STATUS_COMMANDS: dict[str, list[str]] = {
    "cursor": ["cursor-agent", "status"],
    # Claude Code has no status subcommand; a trivial prompt is the cheap probe.
    "claude": ["claude", "-p", "ok", "--output-format", "text"],
}


def is_signed_in(executor: str) -> bool:
    """Whether the agent CLI can actually run. Unknown or missing counts as no."""
    command = STATUS_COMMANDS.get(executor)
    if not command or not shutil.which(command[0]):
        return False
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except Exception:
        return False
    if result.returncode:
        return False
    return not _needs_login(_clean(f"{result.stdout}\n{result.stderr}"))


def available_executors() -> dict[str, dict[str, bool]]:
    """Which agent CLIs are installed, and which are actually usable right now."""
    return {
        name: {
            "installed": shutil.which(command[0]) is not None,
            "signed_in": is_signed_in(name),
        }
        for name, command in EXECUTORS.items()
    }


def start(
    *,
    project_id: str,
    handoff: dict[str, Any],
    repository: str,
    workspace_id: str = "",
    context_event_id: str = "",
    executor: str = "",
    requested_by: str = "",
    push: bool = False,
) -> dict[str, Any]:
    """Create a queued run. Call :func:`execute` to actually do the work."""
    if not settings.org_memory_execution_enabled:
        raise ExecutionError("Autonomous execution is disabled")
    name = executor or settings.org_memory_executor
    if name not in EXECUTORS:
        raise ExecutionError(f"Unknown executor '{name}'. Available: {', '.join(EXECUTORS)}")
    if not shutil.which(EXECUTORS[name][0]):
        raise ExecutionError(f"{EXECUTORS[name][0]} is not installed")
    # Checked before the run is queued so an unauthenticated agent surfaces as an
    # immediate, actionable message rather than a clone and a minute of waiting.
    if not is_signed_in(name):
        raise ExecutionError(_LOGIN_HELP.get(name, f"{name} is not signed in."))
    if not repository:
        raise ExecutionError("This project has no repository to execute against")
    prompt = str(handoff.get("prompt") or "").strip()
    if not prompt:
        raise ExecutionError("Handoff carries no prompt to execute")
    if push and not settings.org_memory_execution_allow_push:
        raise ExecutionError(
            "Pushing is disabled. Set ORG_MEMORY_EXECUTION_ALLOW_PUSH=true to publish branches."
        )

    run_id = new_id("run")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO execution_runs ("
            "id, workspace_id, project_id, context_event_id, executor, status, task, prompt,"
            "repository, requested_by, skill_ids_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                workspace_id,
                project_id,
                context_event_id,
                name,
                "queued",
                str(handoff.get("task") or "")[:500],
                prompt,
                repository,
                requested_by,
                # Which precedents this prompt carried, so the run can report
                # back whether following them actually worked.
                json.dumps([str(item) for item in handoff.get("skill_ids") or []]),
                now,
                now,
            ),
        )
    # Requesting execution is itself an action taken on the served context.
    if context_event_id:
        with contextlib.suppress(Exception):
            record_action(
                context_event_id=context_event_id,
                action_type="execution_started",
                workspace_id=workspace_id,
                project_id=project_id,
                actor=requested_by,
                surface="execution",
                target=name,
                detail={"run_id": run_id, "push": push},
            )
    return get(run_id) or {}


def execute(run_id: str, *, push: bool = False) -> dict[str, Any]:
    """Run the agent, commit what it changed, and record the outcome.

    Intended to run in a background thread; it blocks for as long as the agent
    takes. Never raises — a failure is recorded on the run and returned.
    """
    # Deliberately the raw row: `get` strips the prompt for clients, and the
    # prompt is the one thing the agent actually needs.
    run = _raw(run_id)
    if not run:
        raise ExecutionError("Unknown run")

    worktree = Path(settings.execution_dir) / run_id
    try:
        _update(run_id, status="running", worktree=str(worktree))
        base_branch = _clone(run["repository"], worktree)
        branch = f"{settings.org_memory_execution_branch_prefix}{_slug(run['task'])}-{run_id[-6:]}"
        _git(worktree, "checkout", "-b", branch)
        _update(run_id, branch=branch, base_branch=base_branch)

        output = _run_agent(run["executor"], run["prompt"], worktree)
        _update(run_id, agent_output=output[:MAX_OUTPUT_CHARS])

        changed = _changed_files(worktree)
        if not changed:
            # An honest "nothing to do" beats an empty commit that looks like work.
            _finish(
                run_id,
                "no_changes",
                error="",
                context_event_id=run["context_event_id"],
                workspace_id=run["workspace_id"],
                project_id=run["project_id"],
            )
            return get(run_id) or {}

        diff_stat = _git(worktree, "diff", "--stat", "HEAD").strip()
        diff = _git(worktree, "diff", "HEAD")
        _git(worktree, "add", "-A")
        _git(
            worktree,
            "-c",
            "user.name=MemoryWorks",
            "-c",
            "user.email=orgmemory@local",
            "commit",
            "-m",
            _commit_message(run["task"]),
        )
        commit_sha = _git(worktree, "rev-parse", "HEAD").strip()
        _update(
            run_id,
            files_changed_json=json.dumps(changed),
            diff_stat=diff_stat,
            diff=diff[:MAX_DIFF_CHARS],
            commit_sha=commit_sha,
        )

        status = "committed"
        pull_request_url = ""
        if push and settings.org_memory_execution_allow_push:
            _git(worktree, "push", "-u", "origin", branch)
            status = "pushed"
            pull_request_url = _open_pull_request(worktree, run["task"], base_branch)
            _update(run_id, pushed=1, pull_request_url=pull_request_url)

        _finish(
            run_id,
            status,
            error="",
            context_event_id=run["context_event_id"],
            workspace_id=run["workspace_id"],
            project_id=run["project_id"],
        )
        return get(run_id) or {}
    except Exception as exc:
        _finish(
            run_id,
            "failed",
            error=str(exc)[:2000],
            context_event_id=run["context_event_id"],
            workspace_id=run["workspace_id"],
            project_id=run["project_id"],
        )
        return get(run_id) or {}


def get(run_id: str) -> dict[str, Any] | None:
    run = _raw(run_id)
    return _shape(run) if run else None


def _raw(run_id: str) -> dict[str, Any] | None:
    found = rows("SELECT * FROM execution_runs WHERE id=?", (run_id,))
    return decode(found[0]) if found else None


def list_runs(workspace_id: str, project_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    where = "workspace_id=?"
    params: tuple[Any, ...] = (workspace_id,)
    if project_id:
        where += " AND project_id=?"
        params += (project_id,)
    return [
        _shape(decode(item))
        for item in rows(
            f"SELECT * FROM execution_runs WHERE {where} ORDER BY created_at DESC LIMIT ?",
            (*params, max(1, min(int(limit), 200))),
        )
    ]


def _shape(run: dict[str, Any]) -> dict[str, Any]:
    """Trim the stored row to what a client should see."""
    run.pop("prompt", None)
    run["pushed"] = bool(run.get("pushed"))
    run["files_changed"] = run.pop("files_changed", []) or []
    return run


def _clone(repository: str, worktree: Path) -> str:
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repository, str(worktree)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode:
        raise ExecutionError(f"Clone failed: {result.stderr.strip()[-300:]}")
    return _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() or "main"


def _run_agent(executor: str, prompt: str, worktree: Path) -> str:
    command = [*EXECUTORS[executor], prompt]
    try:
        result = subprocess.run(
            command,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=settings.org_memory_execution_timeout_seconds,
            # An agent that decides to prompt reads stdin, and an inherited
            # terminal would leave it waiting for a keypress nobody is there to
            # press. /dev/null turns that into an immediate EOF, so the run fails
            # in seconds with a readable message instead of holding a slot until
            # the timeout.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"{executor} timed out after {settings.org_memory_execution_timeout_seconds}s"
        ) from exc
    output = _clean(f"{result.stdout}\n{result.stderr}")
    if _needs_login(output):
        # This is the single most likely failure and the CLI's own rendering of
        # it is a screenful of escape codes, so say the useful thing instead.
        raise ExecutionError(_LOGIN_HELP.get(executor, f"{executor} is not signed in."))
    if result.returncode:
        raise ExecutionError(f"{executor} failed: {output[-400:] or 'no output'}")
    return output


def _clean(output: str) -> str:
    """Strip terminal control sequences and collapse the blank lines they leave."""
    stripped = _ANSI_RE.sub("", output)
    return "\n".join(line.rstrip() for line in stripped.splitlines() if line.strip()).strip()


def _needs_login(output: str) -> bool:
    lowered = output.casefold()
    return any(marker in lowered for marker in _LOGIN_MARKERS)


def _changed_files(worktree: Path) -> list[str]:
    status = _git(worktree, "status", "--porcelain")
    return [line[3:].strip() for line in status.splitlines() if line.strip()]


def _git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(worktree), capture_output=True, text=True, timeout=300
    )
    if result.returncode:
        raise ExecutionError(f"git {args[0]} failed: {result.stderr.strip()[-300:]}")
    return result.stdout


def _open_pull_request(worktree: Path, task: str, base_branch: str) -> str:
    """Best effort: a pushed branch is still useful without a PR."""
    if not shutil.which("gh"):
        return ""
    result = subprocess.run(
        ["gh", "pr", "create", "--fill", "--base", base_branch, "--title", _commit_message(task)],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        return ""
    for line in result.stdout.splitlines():
        if line.startswith("http"):
            return line.strip()
    return ""


def _commit_message(task: str) -> str:
    subject = " ".join(str(task or "Apply change").split())[:72]
    return f"{subject}\n\nApplied by MemoryWorks from company context."


def _slug(task: str) -> str:
    return _SLUG_RE.sub("-", str(task or "change").lower()).strip("-")[:40] or "change"


def _update(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utcnow()
    assignments = ", ".join(f"{key}=?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE execution_runs SET {assignments} WHERE id=?",
            (*fields.values(), run_id),
        )


def _finish(
    run_id: str,
    status: str,
    *,
    error: str,
    context_event_id: str,
    workspace_id: str,
    project_id: str,
) -> None:
    now = utcnow()
    _update(run_id, status=status, error=error, completed_at=now)
    succeeded = status in SUCCESS_STATUSES
    run = _raw(run_id) or {}

    # Whether the precedents this run was given actually held. A skill that keeps
    # preceding failures retires itself; this is the pruning that keeps the
    # library worth reading from.
    with contextlib.suppress(Exception):
        if run.get("skill_ids") and status != "no_changes":
            record_use(list(run["skill_ids"]), succeeded=succeeded)

    # Never do one-off work: a verified success becomes reusable so the next
    # person does not re-derive it.
    if succeeded:
        with contextlib.suppress(Exception):
            distil(
                project_id=project_id or str(run.get("project_id") or ""),
                task=str(run.get("task") or ""),
                files=list(run.get("files_changed") or []),
                approach=str(run.get("diff_stat") or ""),
                workspace_id=workspace_id or str(run.get("workspace_id") or ""),
                run_id=run_id,
                context_event_id=context_event_id,
                commit_sha=str(run.get("commit_sha") or ""),
            )

    if not context_event_id:
        return
    # The real payoff of executing: an outcome label nobody had to be asked for.
    outcome = "succeeded" if succeeded else "abandoned" if status == "no_changes" else "failed"
    with contextlib.suppress(Exception):
        record_outcome(
            context_event_id=context_event_id,
            outcome=outcome,
            workspace_id=workspace_id,
            project_id=project_id,
            signal="execution",
            reason=error or f"Execution {status}.",
            detail={"run_id": run_id, "status": status},
        )
