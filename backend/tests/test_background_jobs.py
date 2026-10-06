"""Slow work is queued durably and run by whichever process claims it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app import jobs
from app.auth.app_auth import create_workspace
from app.core.config import settings
from app.core.database import connect


def _calls(kind: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    jobs.handler(kind)(seen.append)
    return seen


def test_a_queued_job_waits_for_a_worker_when_the_api_does_no_background_work(graph, monkeypatch):
    monkeypatch.setattr(settings, "background_work_enabled", False)
    seen = _calls("test.echo")

    job_id = jobs.enqueue("test.echo", {"n": 1})

    assert jobs.get(job_id)["status"] == "queued" and seen == []
    assert jobs.run_next() is True
    assert seen == [{"n": 1}]
    assert jobs.get(job_id)["status"] == "succeeded"
    assert jobs.run_next() is False


def test_a_job_runs_once_however_many_processes_reach_for_it(graph, monkeypatch):
    monkeypatch.setattr(settings, "background_work_enabled", False)
    seen = _calls("test.once")
    job_id = jobs.enqueue("test.once", {})

    assert jobs.run_job(job_id) is True
    assert jobs.run_job(job_id) is False
    assert len(seen) == 1


def test_a_failing_job_is_recorded_not_raised(graph, monkeypatch):
    monkeypatch.setattr(settings, "background_work_enabled", False)

    def boom(payload: dict[str, Any]) -> None:
        raise RuntimeError("clone failed")

    jobs.handler("test.boom")(boom)
    job_id = jobs.enqueue("test.boom", {})
    jobs.run_next()

    job = jobs.get(job_id)
    assert job["status"] == "failed" and "clone failed" in job["error"]


def test_a_job_whose_process_died_is_retried_then_given_up(graph, monkeypatch):
    monkeypatch.setattr(settings, "background_work_enabled", False)
    _calls("test.stale")
    job_id = jobs.enqueue("test.stale", {}, max_attempts=2)
    long_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    def die_mid_run(attempts: int) -> None:
        with connect() as conn:
            conn.execute(
                "UPDATE background_jobs SET status='running', attempts=?, heartbeat_at=? "
                "WHERE id=?",
                (attempts, long_ago, job_id),
            )

    die_mid_run(1)
    assert jobs.requeue_stale() == 1
    assert jobs.get(job_id)["status"] == "queued"

    die_mid_run(2)
    jobs.requeue_stale()
    assert jobs.get(job_id)["status"] == "failed"


def test_repository_ingestion_is_queued_for_the_worker(graph, monkeypatch):
    """With BACKGROUND_WORK_ENABLED=false the API records the ingestion and
    returns; the worker rebuilds the request from the job and runs it."""
    from app.api import routes
    from app.main import app

    monkeypatch.setattr(settings, "background_work_enabled", False)
    ran: list[tuple] = []
    monkeypatch.setattr(
        routes,
        "_run_github_ingest_job",
        lambda principal, request, team_ids, connector, job_id: ran.append(
            (principal, request.repo_url_or_path, team_ids, job_id)
        ),
    )
    client = TestClient(app)
    token = client.post(
        "/api/auth/dev-login", json={"email": "queue@example.com", "display_name": "Q"}
    ).json()["token"]
    create_workspace("Queue workspace", f"Bearer {token}")
    headers = {"Authorization": f"Bearer {token}"}
    active = client.get("/api/auth/me", headers=headers).json()["active_workspace_id"]

    response = client.post(
        "/api/ingest/github",
        json={
            "repo_url_or_path": "https://github.com/acme/api",
            "project_name": "acme/api",
            "background": True,
        },
        headers=headers,
    ).json()

    assert response.get("status") == "running", response
    assert ran == []
    assert jobs.run_next() is True
    principal, repository, team_ids, job_id = ran[0]
    assert repository == "https://github.com/acme/api"
    assert job_id == response["job_id"]
    assert principal["active_workspace_id"] == active


def test_the_worker_entrypoint_imports():
    from app import worker

    assert callable(worker.main)
