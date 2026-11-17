from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import ingestion
from app.graph.arcadedb_store import ArcadeDBGraphStore
from app.main import app


def _create_project(client: TestClient, origin: str):
    login = client.post(
        "/api/auth/dev-login",
        json={"email": "api-errors@example.test", "display_name": "API Errors"},
    )
    assert login.status_code == 200
    return client.post("/api/projects", headers={"Origin": origin}, json={"name": "Test project"})


@pytest.mark.parametrize("origin", ["http://localhost:3000", "https://other.example.test"])
def test_unhandled_exception_returns_safe_json_with_normal_cors(graph, monkeypatch, origin):
    def fail_to_create(*args, **kwargs):
        raise RuntimeError("internal configuration value")

    monkeypatch.setattr(ingestion, "create_project", fail_to_create)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = _create_project(client, origin)

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    if origin == "http://localhost:3000":
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "Origin" in response.headers["vary"]
    else:
        assert "access-control-allow-origin" not in response.headers


def test_project_creation_returns_cors_json_503_when_arcadedb_is_down(graph, monkeypatch, caplog):
    def offline(url, **kwargs):
        assert "/api/v1/command/" in url
        raise httpx.ConnectError("Connection refused by test graph database")

    monkeypatch.setattr(ingestion, "graph", ArcadeDBGraphStore())
    monkeypatch.setattr(httpx, "post", offline)
    origin = "http://localhost:3000"
    with TestClient(app) as client:
        response = _create_project(client, origin)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The graph database is not reachable. Check that ArcadeDB is running."
    }
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Connection refused by test graph database" in caplog.text
