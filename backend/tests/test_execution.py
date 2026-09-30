"""Autonomous execution: what it does, and what it must refuse to do."""

import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.execution import ExecutionError, execute, get, start
from app.outcomes import export_training_records, record_context

WORKSPACE = "wsp_exec"
HANDOFF = {"task": "change the bg colour to navy", "prompt": "Task: change the bg colour to navy"}


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    """Agent auth and binary presence are machine properties, not the
    behaviour under test.

    CI has no cursor-agent installed, so which() reports every executor as
    present; tests that care about the unauthenticated path patch this back
    themselves.
    """
    import shutil as _shutil

    from app.execution.runner import EXECUTORS

    real_which = _shutil.which
    executor_binaries = {command[0] for command in EXECUTORS.values()}

    def fake_which(command):
        if command in executor_binaries:
            return f"/usr/local/bin/{command}"
        return real_which(command)

    monkeypatch.setattr("app.execution.runner.shutil.which", fake_which)
    monkeypatch.setattr("app.execution.runner.is_signed_in", lambda name: True)


@pytest.fixture(autouse=True)
def execution_opt_in(monkeypatch):
    """These tests exercise the runner itself, so they opt in explicitly;
    the product default is disabled."""
    monkeypatch.setattr(settings, "org_memory_execution_enabled", True)


@pytest.fixture
def origin(tmp_path):
    """A real git repository to clone, so git behaviour is never mocked."""
    repo = tmp_path / "origin"
    repo.mkdir()
    (repo / "styles.css").write_text(":root { --bg: #050806; }\n")
    for args in (
        ("init", "-b", "main"),
        ("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"),
        ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"),
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    settings.execution_dir = tmp_path / "executions"
    return repo


def _agent(writes: str):
    """Stand in for cursor-agent/claude: edit a file in the worktree, then exit.

    Replaces `_run_agent` rather than `subprocess.run`, so the real git commands
    still run against a real repository.
    """

    def fake(executor: str, prompt: str, worktree: Path) -> str:
        (Path(worktree) / "styles.css").write_text(writes)
        return "agent done"

    return fake


def test_a_run_commits_on_a_branch_and_never_on_the_default(graph, origin, monkeypatch):
    run = start(project_id="prj_1", handoff=HANDOFF, repository=str(origin), workspace_id=WORKSPACE)
    monkeypatch.setattr("app.execution.runner._run_agent", _agent(":root { --bg: navy; }"))

    result = execute(run["id"])

    assert result["status"] == "committed"
    assert result["branch"].startswith("orgmemory/")
    assert result["base_branch"] == "main"
    assert result["branch"] != result["base_branch"]
    assert result["files_changed"] == ["styles.css"]
    assert result["commit_sha"]


def test_the_users_checkout_is_never_touched(graph, origin, monkeypatch):
    """The agent runs in a fresh clone, so the origin working tree is untouched."""
    run = start(project_id="prj_1", handoff=HANDOFF, repository=str(origin), workspace_id=WORKSPACE)
    monkeypatch.setattr("app.execution.runner._run_agent", _agent(":root { --bg: navy; }"))

    execute(run["id"])

    assert (origin / "styles.css").read_text() == ":root { --bg: #050806; }\n"


def test_an_agent_that_changes_nothing_is_reported_honestly(graph, origin, monkeypatch):
    run = start(project_id="prj_1", handoff=HANDOFF, repository=str(origin), workspace_id=WORKSPACE)
    monkeypatch.setattr("app.execution.runner._run_agent", lambda *a, **k: "nothing to do")

    result = execute(run["id"])

    assert result["status"] == "no_changes"
    assert not result["commit_sha"]


def test_pushing_is_refused_unless_explicitly_enabled(graph, origin, monkeypatch):
    monkeypatch.setattr(settings, "org_memory_execution_allow_push", False)

    with pytest.raises(ExecutionError, match="Pushing is disabled"):
        start(
            project_id="prj_1",
            handoff=HANDOFF,
            repository=str(origin),
            workspace_id=WORKSPACE,
            push=True,
        )


def test_a_run_needs_a_repository_and_a_prompt(graph, origin):
    with pytest.raises(ExecutionError, match="no repository"):
        start(project_id="prj_1", handoff=HANDOFF, repository="", workspace_id=WORKSPACE)
    with pytest.raises(ExecutionError, match="no prompt"):
        start(
            project_id="prj_1",
            handoff={"task": "x"},
            repository=str(origin),
            workspace_id=WORKSPACE,
        )


def test_an_unknown_executor_is_rejected(graph, origin):
    with pytest.raises(ExecutionError, match="Unknown executor"):
        start(
            project_id="prj_1",
            handoff=HANDOFF,
            repository=str(origin),
            workspace_id=WORKSPACE,
            executor="rogue-agent",
        )


def test_a_failing_agent_records_a_failure_rather_than_raising(graph, origin, monkeypatch):
    run = start(project_id="prj_1", handoff=HANDOFF, repository=str(origin), workspace_id=WORKSPACE)

    def explode(*args, **kwargs):
        raise ExecutionError("cursor failed: not logged in")

    monkeypatch.setattr("app.execution.runner._run_agent", explode)

    result = execute(run["id"])

    assert result["status"] == "failed"
    assert "not logged in" in result["error"]


def test_a_committed_run_labels_the_context_that_produced_it(graph, origin, monkeypatch):
    """Execution closes the outcome loop without anyone being asked."""
    context_id = record_context(
        project_id="prj_1",
        query="change the bg colour to navy",
        result={"answer": "styles.css holds the token", "answer_sufficient": True},
        workspace_id=WORKSPACE,
    )
    run = start(
        project_id="prj_1",
        handoff=HANDOFF,
        repository=str(origin),
        workspace_id=WORKSPACE,
        context_event_id=context_id,
    )
    monkeypatch.setattr("app.execution.runner._run_agent", _agent(":root { --bg: navy; }"))

    execute(run["id"])

    record = export_training_records(WORKSPACE)[0]
    assert record["label"] == "succeeded"
    assert record["outcomes"][0]["signal"] == "execution"
    assert [item["action_type"] for item in record["actions"]] == ["execution_started"]


def test_the_stored_prompt_is_not_exposed_to_clients(graph, origin):
    run = start(project_id="prj_1", handoff=HANDOFF, repository=str(origin), workspace_id=WORKSPACE)

    assert "prompt" not in run
    assert "prompt" not in (get(run["id"]) or {})


def test_a_not_signed_in_agent_reports_something_a_human_can_act_on(graph, origin, monkeypatch):
    """cursor-agent paints a sign-in splash screen; captured to a pipe that is
    a screenful of escape codes. The chat should show the fix instead."""
    from app.execution.runner import _run_agent

    splash = (
        "\x1b[2K\x1b[1A Cursor Agent \x1b[38;5;240m{[#M##M##M#####*ppll\n"
        "\x1b[0m Press any key to sign in... \x1b[?25h"
    )
    monkeypatch.setattr(
        "app.execution.runner.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 1, splash, ""),
    )

    with pytest.raises(ExecutionError, match="cursor-agent login"):
        _run_agent("cursor", "do the thing", origin)


def test_terminal_escape_codes_never_reach_the_stored_output(graph):
    from app.execution.runner import _clean

    cleaned = _clean("\x1b[2K\x1b[1Ahello\x1b[0m\n\n\x1b[38;5;240mworld\x1b[0m")

    assert cleaned == "hello\nworld"


def test_an_unauthenticated_executor_is_refused_before_any_repository_is_cloned(
    graph, origin, monkeypatch
):
    monkeypatch.setattr("app.execution.runner.is_signed_in", lambda name: False)

    with pytest.raises(ExecutionError, match="not signed in|cursor-agent login"):
        start(
            project_id="prj_1",
            handoff=HANDOFF,
            repository=str(origin),
            workspace_id=WORKSPACE,
            executor="cursor",
        )


def test_a_successful_run_teaches_the_library_what_worked(graph, origin, monkeypatch):
    """Never do one-off work: a verified commit becomes reusable precedent."""
    from app.skills import list_skills

    monkeypatch.setattr("app.execution.runner._run_agent", _agent(":root { --bg: navy; }"))
    for _ in (1, 2):
        run = start(
            project_id="prj_1",
            handoff=HANDOFF,
            repository=str(origin),
            workspace_id=WORKSPACE,
        )
        execute(run["id"])
        # Reset the checkout so the second run has something to change again.
        subprocess.run(["git", "checkout", "."], cwd=origin, capture_output=True)

    skills = list_skills(WORKSPACE)
    assert len(skills) == 1, "the same job twice should reinforce, not duplicate"
    assert skills[0]["successes"] == 2
    assert skills[0]["status"] == "active"
    assert "styles.css" in skills[0]["files"]


def test_a_failed_run_discredits_the_precedent_it_was_given(graph, origin, monkeypatch):
    from app.skills import distil, get

    taught = distil(
        project_id="prj_1",
        task=HANDOFF["task"],
        files=["styles.css"],
        workspace_id=WORKSPACE,
        run_id="seed_1",
    )
    distil(
        project_id="prj_1",
        task=HANDOFF["task"],
        files=["styles.css"],
        workspace_id=WORKSPACE,
        run_id="seed_2",
    )
    assert get(taught["id"])["status"] == "active"

    run = start(
        project_id="prj_1",
        handoff={**HANDOFF, "skill_ids": [taught["id"]]},
        repository=str(origin),
        workspace_id=WORKSPACE,
    )
    monkeypatch.setattr(
        "app.execution.runner._run_agent",
        lambda *a, **k: (_ for _ in ()).throw(ExecutionError("agent gave up")),
    )
    execute(run["id"])

    assert get(taught["id"])["failures"] == 1


def test_a_database_created_before_skill_tracking_is_migrated(graph, tmp_path):
    """CREATE TABLE IF NOT EXISTS cannot add a column to an existing table.

    Every test elsewhere uses a fresh database and would miss this; a real
    deployment would fail on the first execution after upgrading.
    """
    import sqlite3

    from app.core.database import init_db

    legacy = tmp_path / "legacy.db"
    # The shape execution_runs had before skills existed, indexed columns included.
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            "CREATE TABLE execution_runs ("
            "id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '',"
            "project_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT '',"
            "created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')"
        )
    settings.sqlite_path = legacy

    init_db()

    with sqlite3.connect(legacy) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(execution_runs)")}
    assert "skill_ids_json" in columns
