"""Workstream 0 containment proof: production starts only with safe configuration.

Covers the internal security remediation plan (kept outside the repository), Workstream 0 — autonomous
execution defaults off and production refuses it without an explicit isolated
profile; the ArcadeDB development password is refused at startup; local
repository paths are rejected in the production profile; buffered endpoints
reject unbounded chunked bodies; a connector work step cannot be approved by
the principal that requested it; and the Compose/Caddy deployment files keep
the database private and the proxy bounded.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.core.api_guard import _BODY_LIMIT_EXEMPT_PREFIXES, APIGuardMiddleware
from app.core.config import Settings, settings
from app.core.database import connect, utcnow
from app.execution import ExecutionError, start
from app.ingestion.repository import RepositoryIngestor
from app.work.service import MemoryWorkService

REPO_ROOT = Path(__file__).resolve().parents[2]


def _safe_production(**overrides) -> Settings:
    values = dict(
        environment="production",
        auth_dev_mode=False,
        jwt_secret="a-production-secret-that-is-longer-than-32-characters",
        frontend_url="https://orgmemory.example.com",
        # Aliased fields are passed by alias so a developer's root .env cannot
        # out-prioritize the fixture through the validation alias.
        **{"ORGMEMORY_DEMO_MODE": False},
        allow_local_command_execution=False,
        github_client_id="production-client-id",
        github_client_secret="production-client-secret",
        runbook_embedding_provider="fastembed",
        connector_vault_provider="aws-kms",
        connector_kms_key_id="arn:aws:kms:us-east-1:123456789012:key/test",
        arcadedb_password="a-production-arcade-password",
        mcp_public_url="https://mcp.orgmemory.example.com",
        mcp_oauth_issuer_url="https://api.orgmemory.example.com",
    )
    values.update(overrides)
    return Settings(**values)


def test_execution_defaults_to_disabled():
    config = Settings(_env_file=None)
    assert config.org_memory_execution_enabled is False
    assert config.org_memory_execution_isolated_profile is False


def test_production_startup_rejects_enabled_execution_without_isolated_profile():
    config = _safe_production(org_memory_execution_enabled=True)

    with pytest.raises(RuntimeError) as exc:
        config.assert_safe_for_environment()

    message = str(exc.value)
    assert "execution" in message.casefold()
    assert "isolated" in message.casefold()


def test_production_startup_accepts_explicit_isolated_execution_profile():
    config = _safe_production(
        org_memory_execution_enabled=True,
        org_memory_execution_isolated_profile=True,
    )
    config.assert_safe_for_environment()


def test_production_startup_rejects_default_or_empty_arcadedb_password():
    for unsafe in ("", "runbook_dev_password"):
        config = _safe_production(arcadedb_password=unsafe)

        with pytest.raises(RuntimeError) as exc:
            config.assert_safe_for_environment()

        assert "ARCADEDB_PASSWORD" in str(exc.value)


def test_production_startup_allows_memory_graph_without_arcadedb_password():
    config = _safe_production(graph_backend="memory", arcadedb_password="")
    config.assert_safe_for_environment()


def test_execution_start_fails_closed_when_disabled(graph, monkeypatch):
    monkeypatch.setattr(settings, "org_memory_execution_enabled", False)

    with pytest.raises(ExecutionError) as exc:
        start(
            project_id="proj_sec",
            handoff={"task": "t", "prompt": "do the thing"},
            repository="https://github.com/example/orgmemory",
        )

    assert "disabled" in str(exc.value).casefold()


def test_production_rejects_local_repository_ingestion(graph, monkeypatch, tmp_path):
    local_repo = tmp_path / "sibling-repo"
    local_repo.mkdir()
    ingestor = object.__new__(RepositoryIngestor)

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(ValueError) as exc:
        ingestor._checkout(str(local_repo), "proj_sec")
    assert "production" in str(exc.value).casefold()

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(ValueError):
        ingestor._checkout("file:///etc/passwd", "proj_sec")

    # Development keeps local checkouts so the ingest cache still works.
    monkeypatch.setattr(settings, "environment", "development")
    resolved, cloned = ingestor._checkout(str(local_repo), "proj_sec")
    assert resolved == local_repo.resolve()
    assert cloned is False


def _call_guard(path: str, headers: list[tuple[bytes, bytes]]):
    middleware = APIGuardMiddleware(app=None)
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": path,
            "headers": headers,
            "query_string": b"",
            "client": ("testclient", 123),
        }
    )

    async def call_next(_request):
        return PlainTextResponse("reached handler")

    return asyncio.run(middleware.dispatch(request, call_next))


def test_api_guard_rejects_undeclared_chunked_bodies():
    response = _call_guard(
        "/api/auth/dev-login",
        [(b"transfer-encoding", b"chunked")],
    )
    assert response.status_code == 411
    assert b"chunked" in response.body.lower()


def test_api_guard_still_accepts_declared_and_streaming_bodies():
    reached = _call_guard(
        "/api/auth/dev-login",
        [(b"content-length", b"2")],
    )
    assert reached.status_code == 200

    streaming = _call_guard(
        _BODY_LIMIT_EXEMPT_PREFIXES[0],
        [(b"transfer-encoding", b"chunked")],
    )
    assert streaming.status_code == 200


def _insert_pending_work(work_id: str, requested_by: str) -> None:
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, repository, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (f"proj_{work_id}", "Security Fixture", "", "active", now, now),
        )
        conn.execute(
            "INSERT INTO memory_work (id, workspace_id, project_id, objective, status,"
            " requested_by, target_connector, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                work_id,
                "wsp_sec",
                f"proj_{work_id}",
                "Post the release update to Slack",
                "pending_approval",
                requested_by,
                "slack",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO memory_work_steps (id, work_id, position, step_type, title,"
            " description, status, approval_required, connector, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"{work_id}_step",
                work_id,
                1,
                "connector_action",
                "Post the update",
                "",
                "pending_approval",
                1,
                "slack",
                now,
                now,
            ),
        )


def test_connector_step_cannot_be_approved_by_requester(graph):
    service = MemoryWorkService(retrieval=None, brain=None)
    _insert_pending_work("work_self", "Requester Rita")
    _insert_pending_work("work_other", "Requester Rita")

    with pytest.raises(ValueError) as exc:
        service.resolve_step("work_self", "work_self_step", True, "Requester Rita")
    assert "approve" in str(exc.value).casefold()

    resolved = service.resolve_step("work_other", "work_other_step", True, "Approver Al")
    assert resolved["status"] == "ready_for_worker"


def test_dev_compose_requires_arcadedb_password_and_binds_loopback():
    text = (REPO_ROOT / "docker-compose.yml").read_text()
    arcadedb_block = text.split("services:", 1)[1].split("\n  backend:", 1)[0]

    assert "${ARCADEDB_PASSWORD:-" not in arcadedb_block
    assert "${ARCADEDB_PASSWORD:?" in arcadedb_block
    for published in ('"2480:2480"', '"2424:2424"'):
        assert published not in arcadedb_block
    assert '"127.0.0.1:2480:2480"' in arcadedb_block
    assert '"127.0.0.1:2424:2424"' in arcadedb_block


def test_production_compose_keeps_database_private_and_execution_disabled():
    text = (REPO_ROOT / "compose.production.yml").read_text()
    arcadedb_block = text.split("services:", 1)[1].split("\n  backend:", 1)[0]

    assert "ports" not in arcadedb_block
    assert "${ARCADEDB_PASSWORD:?" in arcadedb_block
    assert 'ORGMEMORY_EXECUTION_ENABLED: "false"' in text


def test_caddyfile_bounds_request_bodies():
    caddy = (REPO_ROOT / "deploy" / "server" / "Caddyfile").read_text()
    api_block = caddy.split("api.{$PUBLIC_DOMAIN} {", 1)[1].split("\n}", 1)[0]
    mcp_block = caddy.split("mcp.{$PUBLIC_DOMAIN} {", 1)[1].split("\n}", 1)[0]

    assert "request_body" in api_block and "max_size" in api_block
    assert "request_body" in mcp_block and "max_size" in mcp_block
