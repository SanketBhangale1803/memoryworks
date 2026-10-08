from __future__ import annotations

import sqlite3
import threading

import pytest

from app.core.database import _initialized_paths, connect, init_db, new_id, row, rows, utcnow
from app.memory.company import CompanyMemoryService
from app.orgops.signals import SignalService, signal_fingerprint


def _workspace_project(conn=None):
    wid = new_id("wsp")
    pid = new_id("prj")
    now = utcnow()
    with connect() as c:
        c.execute(
            "INSERT INTO workspaces VALUES (?,?,?,?,?)",
            (wid, f"ws-{wid[-6:]}", f"slug-{wid[-6:]}", now, now),
        )
        c.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (pid, f"proj-{pid[-6:]}", "acme/api", "ready", now, now),
        )
        c.execute("INSERT INTO workspace_projects VALUES (?,?)", (wid, pid))
    return wid, pid


def _svc(graph):
    return SignalService(CompanyMemoryService(graph))


def _obs(wid, pid, **over):
    base = {
        "workspace_id": wid,
        "project_id": pid,
        "source": "github",
        "kind": "ci_failure",
        "subject": "acme/api",
        "lane": {"producer": "workflow", "target": "default", "workflow_id": "7"},
        "severity": "error",
        "state": "failure",
        "observed_at": "2026-10-08T10:00:00.000000+00:00",
        "generation_at": "2026-10-08T09:55:00.000000+00:00",
        "generation_id": 101,
        "attempt": 1,
        "observation_key": "obs-1",
        "source_ids": ["repository-metadata:acme/api", "github-repository:42"],
        "evidence_url": "https://github.com/acme/api/actions/runs/101",
        "details": {"producer": "workflow", "workflow_id": 7, "target": "default"},
    }
    base.update(over)
    return base


def test_fingerprint_is_stable_and_lane_scoped(graph):
    _workspace_project()
    fp1 = signal_fingerprint(
        workspace_id="wsp_1",
        project_id="prj_1",
        source="github",
        kind="ci_failure",
        subject="acme/api",
        lane={"producer": "workflow", "target": "default"},
        source_ids=["b", "a", "a"],
    )
    fp2 = signal_fingerprint(
        workspace_id="wsp_1",
        project_id="prj_1",
        source="github",
        kind="ci_failure",
        subject="acme/api",
        lane={"producer": "workflow", "target": "default"},
        source_ids=["a", "b"],
    )
    assert fp1 == fp2
    fp_other_lane = signal_fingerprint(
        workspace_id="wsp_1",
        project_id="prj_1",
        source="github",
        kind="ci_failure",
        subject="acme/api",
        lane={"producer": "check", "target": "default"},
        source_ids=["a", "b"],
    )
    assert fp_other_lane != fp1
    fp_other_project = signal_fingerprint(
        workspace_id="wsp_1",
        project_id="prj_2",
        source="github",
        kind="ci_failure",
        subject="acme/api",
        lane={"producer": "workflow", "target": "default"},
        source_ids=["a", "b"],
    )
    assert fp_other_project != fp1


def test_repeated_failures_cluster_and_duplicate_does_not_count(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    first = svc.observe(**_obs(wid, pid, observation_key="k1"))
    second = svc.observe(**_obs(wid, pid, observation_key="k2"))
    assert first["issue_id"] == second["issue_id"]
    issue = row("SELECT * FROM live_issues WHERE id=?", (first["issue_id"],))
    assert issue["occurrences"] == 2
    dup = svc.observe(**_obs(wid, pid, observation_key="k1"))
    assert dup["signal_id"] == first["signal_id"]
    assert dup["created"] is False
    issue2 = row("SELECT * FROM live_issues WHERE id=?", (first["issue_id"],))
    assert issue2["occurrences"] == 2


def test_newer_success_resolves_across_sha(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    svc.observe(**_obs(wid, pid, observation_key="red-1"))
    res = svc.observe(
        **_obs(
            wid,
            pid,
            state="success",
            severity="info",
            observation_key="green-1",
            observed_at="2026-10-08T11:00:00.000000+00:00",
            generation_at="2026-10-08T10:55:00.000000+00:00",
            generation_id=102,
        )
    )
    assert res["status"] == "resolved"
    issue = row("SELECT * FROM live_issues WHERE id=?", (res["issue_id"],))
    assert issue["status"] == "resolved"
    assert issue["resolved_at"] == "2026-10-08T11:00:00.000000+00:00"


def test_late_old_execution_cannot_change_latest_state(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    svc.observe(
        **_obs(
            wid,
            pid,
            observation_key="old-red",
            observed_at="2026-10-08T10:00:00.000000+00:00",
            generation_at="2026-10-08T09:55:00.000000+00:00",
            generation_id=101,
        )
    )
    svc.observe(
        **_obs(
            wid,
            pid,
            state="success",
            severity="info",
            observation_key="new-green",
            observed_at="2026-10-08T11:00:00.000000+00:00",
            generation_at="2026-10-08T10:55:00.000000+00:00",
            generation_id=102,
        )
    )
    # Late old failure arrives after the green resolution.
    late = svc.observe(
        **_obs(
            wid,
            pid,
            observation_key="late-old",
            observed_at="2026-10-08T09:00:00.000000+00:00",
            generation_at="2026-10-08T08:55:00.000000+00:00",
            generation_id=100,
        )
    )
    assert late["status"] == "resolved"
    issue = row("SELECT * FROM live_issues WHERE id=?", (late["issue_id"],))
    assert issue["status"] == "resolved"
    assert issue["occurrences"] == 2  # lifetime count includes out-of-order failure


def test_success_before_failure_preserves_resolved_history(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    green = svc.observe(
        **_obs(
            wid,
            pid,
            state="success",
            severity="info",
            observation_key="early-green",
            observed_at="2026-10-08T09:00:00.000000+00:00",
            generation_at="2026-10-08T08:55:00.000000+00:00",
            generation_id=100,
        )
    )
    assert green["issue_id"] is None
    red = svc.observe(
        **_obs(
            wid,
            pid,
            observation_key="later-red",
            observed_at="2026-10-08T10:00:00.000000+00:00",
            generation_at="2026-10-08T09:55:00.000000+00:00",
            generation_id=101,
        )
    )
    assert red["issue_id"] is not None
    sigs = rows("SELECT * FROM live_signals WHERE workspace_id=?", (wid,))
    assert len(sigs) == 2
    # Earlier success is attached when the issue is created.
    assert {s["issue_id"] for s in sigs} == {red["issue_id"]}
    issue = row("SELECT * FROM live_issues WHERE id=?", (red["issue_id"],))
    assert issue["status"] == "open"
    assert issue["occurrences"] == 1


def test_failure_reopens_and_muted_remains_muted(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    svc.observe(**_obs(wid, pid, observation_key="r1"))
    svc.observe(
        **_obs(
            wid,
            pid,
            state="success",
            severity="info",
            observation_key="g1",
            observed_at="2026-10-08T11:00:00.000000+00:00",
            generation_at="2026-10-08T10:55:00.000000+00:00",
            generation_id=102,
        )
    )
    reopen = svc.observe(
        **_obs(
            wid,
            pid,
            observation_key="r2",
            observed_at="2026-10-08T12:00:00.000000+00:00",
            generation_at="2026-10-08T11:55:00.000000+00:00",
            generation_id=103,
        )
    )
    assert reopen["status"] == "open"
    with connect() as conn:
        conn.execute("UPDATE live_issues SET status='muted' WHERE id=?", (reopen["issue_id"],))
    muted = svc.observe(
        **_obs(
            wid,
            pid,
            observation_key="r3",
            observed_at="2026-10-08T13:00:00.000000+00:00",
            generation_at="2026-10-08T12:55:00.000000+00:00",
            generation_id=104,
        )
    )
    assert muted["status"] == "muted"
    issue = row("SELECT * FROM live_issues WHERE id=?", (reopen["issue_id"],))
    assert issue["status"] == "muted"


def test_equal_time_success_wins(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    svc.observe(
        **_obs(
            wid,
            pid,
            observation_key="red-eq",
            observed_at="2026-10-08T10:00:00.000000+00:00",
            generation_at="2026-10-08T09:55:00.000000+00:00",
            generation_id=101,
            attempt=1,
        )
    )
    res = svc.observe(
        **_obs(
            wid,
            pid,
            state="success",
            severity="info",
            observation_key="green-eq",
            observed_at="2026-10-08T10:00:00.000000+00:00",
            generation_at="2026-10-08T09:55:00.000000+00:00",
            generation_id=101,
            attempt=1,
        )
    )
    assert res["status"] == "resolved"


def test_duplicate_refreshes_expiry_without_increment(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    first = svc.observe(
        **_obs(wid, pid, observation_key="dup", collected_at="2026-10-08T10:00:00.000000+00:00")
    )
    before = row("SELECT * FROM live_signals WHERE id=?", (first["signal_id"],))
    second = svc.observe(
        **_obs(wid, pid, observation_key="dup", collected_at="2026-10-08T12:00:00.000000+00:00")
    )
    assert second["signal_id"] == first["signal_id"]
    after = row("SELECT * FROM live_signals WHERE id=?", (first["signal_id"],))
    assert after["expires_at"] > before["expires_at"]
    issue = row("SELECT * FROM live_issues WHERE id=?", (first["issue_id"],))
    assert issue["occurrences"] == 1


def test_invalid_observation_rolls_back(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    before_signals = len(rows("SELECT id FROM live_signals WHERE workspace_id=?", (wid,)))
    before_issues = len(rows("SELECT id FROM live_issues WHERE workspace_id=?", (wid,)))
    with pytest.raises(ValueError):
        svc.observe(**_obs(wid, pid, severity="bogus", observation_key="bad"))
    with pytest.raises(ValueError):
        svc.observe(**_obs(wid, pid, observation_key="bad2", generation_at="not-a-time"))
    # Conflicting duplicate raises and writes nothing new.
    svc.observe(**_obs(wid, pid, observation_key="k-conflict"))
    with pytest.raises(ValueError, match="conflicting duplicate"):
        svc.observe(
            **_obs(wid, pid, observation_key="k-conflict", state="success", severity="info")
        )
    after_signals = len(rows("SELECT id FROM live_signals WHERE workspace_id=?", (wid,)))
    after_issues = len(rows("SELECT id FROM live_issues WHERE workspace_id=?", (wid,)))
    assert after_signals == before_signals + 1
    assert after_issues == before_issues + 1


def test_concurrent_observations_count_once(graph):
    wid, pid = _workspace_project()
    svc = _svc(graph)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _run():
        try:
            barrier.wait(timeout=5)
            svc.observe(**_obs(wid, pid, observation_key="race"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    sigs = rows(
        "SELECT * FROM live_signals WHERE workspace_id=? AND observation_key=?",
        (wid, "race"),
    )
    assert len(sigs) == 1
    issue = row("SELECT * FROM live_issues WHERE workspace_id=?", (wid,))
    assert issue["occurrences"] == 1


def test_workspace_only_signal_uses_null_project(graph):
    wid, _ = _workspace_project()
    svc = _svc(graph)
    res = svc.observe(
        workspace_id=wid,
        project_id=None,
        source="runtime",
        kind="sync_error",
        subject="worker",
        lane={"producer": "worker"},
        severity="warning",
        state="failure",
        observed_at="2026-10-08T10:00:00.000000+00:00",
        generation_at="2026-10-08T09:55:00.000000+00:00",
        generation_id=1,
        attempt=1,
        observation_key="ws-only",
        source_ids=[],
    )
    assert res["issue_id"] is not None
    issue = row("SELECT * FROM live_issues WHERE id=?", (res["issue_id"],))
    assert issue["project_id"] is None
    listed = svc.list_issues(workspace_id=wid, project_ids=[], allowed_team_ids=None)
    assert [i["id"] for i in listed] == [res["issue_id"]]


def test_existing_database_upgrade_is_additive_and_idempotent(graph, tmp_path):
    from app.core.config import settings

    legacy_path = tmp_path / "legacy.db"
    now = utcnow()
    with sqlite3.connect(legacy_path) as conn:
        conn.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, repository TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE workspace_projects (workspace_id TEXT NOT NULL, project_id TEXT NOT NULL, PRIMARY KEY(workspace_id, project_id))"
        )
        conn.execute(
            "INSERT INTO workspaces VALUES (?,?,?,?,?)",
            ("wsp_legacy", "Legacy", "legacy", now, now),
        )
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            ("prj_legacy", "LegacyProj", "acme/legacy", "ready", now, now),
        )
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", ("wsp_legacy", "prj_legacy"))
        conn.commit()
    old_sqlite = settings.sqlite_path
    settings.sqlite_path = legacy_path
    try:
        resolved = str(legacy_path.resolve())
        _initialized_paths.discard(resolved)
        init_db()
        _initialized_paths.discard(resolved)
        init_db()
        with sqlite3.connect(legacy_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "live_issues" in tables
            assert "live_signals" in tables
            idx = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "live_issues_scope_status" in idx
            assert "live_signals_issue_time" in idx
            assert "live_signals_freshness" in idx
            ws = dict(conn.execute("SELECT * FROM workspaces WHERE id='wsp_legacy'").fetchone())
            assert ws["name"] == "Legacy"
            pr = dict(conn.execute("SELECT * FROM projects WHERE id='prj_legacy'").fetchone())
            assert pr["repository"] == "acme/legacy"
    finally:
        settings.sqlite_path = old_sqlite
        _initialized_paths.discard(str(legacy_path.resolve()))
