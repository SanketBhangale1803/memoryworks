"""Repository ingestion that outlives a proxied request.

The web app reaches the API through Vercel's external rewrite, which closes a
request long before a real repository finishes ingesting. The page therefore
asks for a background job and polls it; these tests pin that contract, the
guard against two ingestions colliding, and ArcadeDB conflict retries.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.auth.app_auth import create_workspace
from app.core.config import settings
from app.core.database import connect, row, utcnow


def _client_and_token(email: str) -> tuple[TestClient, str, str]:
    from app.main import app

    client = TestClient(app)
    token = client.post(
        "/api/auth/dev-login", json={"email": email, "display_name": "Ingest"}
    ).json()["token"]
    create_workspace("Ingest Workspace", f"Bearer {token}")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    return client, token, me["active_workspace_id"]


def _repository(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "README.md").write_text(
        "# Orders\nThe orders service calls inventory. Retries are capped at 3."
    )
    return repository


def test_background_ingestion_returns_at_once_and_records_the_full_result(graph, tmp_path):
    client, token, _workspace_id = _client_and_token("background@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    started = client.post(
        "/api/ingest/github",
        json={
            "repo_url_or_path": str(_repository(tmp_path)),
            "project_name": "Orders",
            "background": True,
        },
        headers=headers,
    )

    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "running"
    assert set(body) == {"job_id", "status"}
    # TestClient runs background tasks before returning, so the job is done.
    job = client.get(f"/api/ingest/jobs/{body['job_id']}", headers=headers).json()
    assert job["status"] == "succeeded", job.get("error")
    assert job["result"]["project_id"] == job["project_id"]
    assert "memory_units_created" in job["result"]


def test_a_second_request_joins_the_running_ingestion(graph, tmp_path):
    client, token, workspace_id = _client_and_token("join@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    source = "https://github.com/example/orders.git"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ingestion_jobs
            (id, project_id, workspace_id, source, source_ref, status, progress,
             warnings_json, created_at, updated_at)
            VALUES ('job_inflight', NULL, ?, 'github', ?, 'running', 5, '[]', ?, ?)
            """,
            (workspace_id, source, utcnow(), utcnow()),
        )

    joined = client.post(
        "/api/ingest/github",
        json={"repo_url_or_path": source, "project_name": "Orders", "background": True},
        headers=headers,
    ).json()

    assert joined == {"job_id": "job_inflight", "status": "running"}
    count = row("SELECT COUNT(*) AS n FROM ingestion_jobs WHERE source_ref=?", (source,))
    assert count["n"] == 1


def test_existing_job_tables_gain_the_result_column(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE ingestion_jobs (
              id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT, source TEXT NOT NULL,
              source_ref TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
              files_scanned INTEGER NOT NULL DEFAULT 0, issues_scanned INTEGER NOT NULL DEFAULT 0,
              pull_requests_scanned INTEGER NOT NULL DEFAULT 0,
              knowledge_items_created INTEGER NOT NULL DEFAULT 0,
              knowledge_chunks_created INTEGER NOT NULL DEFAULT 0,
              graph_nodes_created INTEGER NOT NULL DEFAULT 0,
              graph_edges_created INTEGER NOT NULL DEFAULT 0, warnings_json TEXT NOT NULL DEFAULT '[]',
              error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              completed_at TEXT
            )
            """
        )
    monkeypatch.setattr(settings, "sqlite_path", path)

    with connect() as conn:
        columns = {record[1] for record in conn.execute("PRAGMA table_info(ingestion_jobs)")}

    assert "result_json" in columns


class _Response:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


CONFLICT = (
    '{"error":"Cannot execute command","detail":"Concurrent modification on page",'
    '"exception":"com.arcadedb.exception.ConcurrentModificationException"}'
)


def test_arcadedb_conflicts_are_retried(monkeypatch):
    from app.graph import arcade_client

    responses = [
        _Response(503, CONFLICT),
        _Response(503, CONFLICT),
        _Response(200, payload={"result": [1]}),
    ]
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return responses.pop(0)

    monkeypatch.setattr(arcade_client.httpx, "post", fake_post)
    monkeypatch.setattr(arcade_client.time, "sleep", lambda _seconds: None)

    assert arcade_client.ArcadeClient().command("select 1") == [1]
    assert len(calls) == 3


def test_other_arcadedb_errors_are_not_retried(monkeypatch):
    from app.graph import arcade_client

    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        return _Response(503, '{"error":"Server is starting"}')

    monkeypatch.setattr(arcade_client.httpx, "post", fake_post)

    with pytest.raises(arcade_client.ArcadeDBError):
        arcade_client.ArcadeClient().command("select 1")
    assert len(calls) == 1


def test_persistent_conflicts_eventually_fail(monkeypatch):
    from app.graph import arcade_client

    monkeypatch.setattr(arcade_client.httpx, "post", lambda *a, **k: _Response(503, CONFLICT))
    monkeypatch.setattr(arcade_client.time, "sleep", lambda _seconds: None)

    with pytest.raises(arcade_client.ArcadeDBError, match="ConcurrentModification"):
        arcade_client.ArcadeClient().command("select 1")
