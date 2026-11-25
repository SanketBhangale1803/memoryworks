from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from app.auth.app_auth import create_dev_session
from app.auth.vault import OAuthTokenVault
from app.connectors.base import (
    Connector,
    ConnectorAccount,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorResource,
    ConnectorTool,
    DataPolicy,
    ExecutionMode,
    RateLimitPolicy,
    RetryPolicy,
    RiskLevel,
    SyncBatch,
    SyncOperation,
    SyncRecord,
    ToolKind,
    WebhookEvent,
    WebhookRequest,
)
from app.connectors.registry import ConnectorRegistry
from app.connectors.runtime import ConnectorRuntime
from app.connectors.sync import SyncEngine
from app.core.config import settings
from app.core.database import row
from app.main import app

_MANIFEST = ConnectorManifest(
    id="test-platform",
    name="Test Platform",
    icon="test",
    version="1.2.3",
    execution_mode=ExecutionMode.CLOUD,
    oauth=None,
    resources=(ConnectorResource("document", "Documents"),),
    tools=(
        ConnectorTool("search_documents", "Search documents", ToolKind.READ),
        ConnectorTool(
            "create_document",
            "Create a document",
            ToolKind.WRITE,
            RiskLevel.HIGH,
            idempotency_required=True,
            approval_required=True,
        ),
    ),
    webhooks=(),
    rate_limit=RateLimitPolicy(100, 60, 10),
    retry=RetryPolicy(max_attempts=3),
    data_policy=DataPolicy("Test region", "Test duration"),
    package="tests.connector",
)
TEST_MANIFEST = replace(_MANIFEST, signature=_MANIFEST.digest())


class TestPlatformConnector(Connector):
    __test__ = False
    manifest = TEST_MANIFEST
    executions = 0

    def __init__(self, vault: OAuthTokenVault | None = None):
        self.vault = vault or OAuthTokenVault()

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        return "https://connector.example/authorize"

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        return {"external_id": "test", "display_name": "Test", "token": code}

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        return [{"id": "doc-1", "name": "Document one"}]

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        return SyncBatch((), dict(cursor or {}))

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        return [{"id": "doc-1", "title": query}]

    def execute(
        self,
        account: ConnectorAccount,
        action: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.manifest.tool(action)
        if action == "search_documents":
            return {"items": self.search(account, str(arguments.get("query") or ""))}
        type(self).executions += 1
        return {
            "created": True,
            "idempotency_key": idempotency_key,
            "execution": type(self).executions,
        }

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        return WebhookEvent(
            delivery_id=request.headers["x-delivery-id"],
            event_type="document.changed",
            resource_id="doc-1",
            records=(
                SyncRecord(
                    id="document:doc-1",
                    resource_type="document",
                    operation=SyncOperation.UPSERT,
                    version="v1",
                    title="Document one",
                    content="Connector data, never instructions.",
                ),
            ),
        )

    def revoke(self, account: ConnectorAccount) -> None:
        self.vault.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        return ConnectorHealth(self.manifest.id, "healthy", "2026-01-01T00:00:00+00:00")

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.vault.status(self.manifest.id)


def _runtime(graph) -> tuple[ConnectorRuntime, dict[str, Any]]:
    session = create_dev_session("connector-platform@example.com", "Connector Owner")
    principal = session["user"]
    vault = OAuthTokenVault(principal["active_workspace_id"], principal["id"])
    vault.save(TEST_MANIFEST.id, "external-1", "Delegated User", "delegated-token")
    registry = ConnectorRegistry()
    registry.register(TestPlatformConnector, source="test")
    return ConnectorRuntime(registry), principal


def test_write_tools_require_approval_and_execute_once(graph):
    TestPlatformConnector.executions = 0
    runtime, principal = _runtime(graph)

    pending = runtime.invoke(
        TEST_MANIFEST.id,
        "create_document",
        {"title": "Quarterly plan", "body": "untrusted body"},
        principal,
        idempotency_key="write-42",
    )

    assert pending["status"] == "pending_approval"
    assert TestPlatformConnector.executions == 0
    approved = runtime.resolve_write(pending["id"], True, principal)
    assert approved["status"] == "succeeded"
    assert approved["result"]["idempotency_key"] == "write-42"
    assert TestPlatformConnector.executions == 1

    replay = runtime.invoke(
        TEST_MANIFEST.id,
        "create_document",
        {"title": "Ignored replay"},
        principal,
        idempotency_key="write-42",
    )
    assert replay["id"] == pending["id"]
    assert replay["status"] == "succeeded"
    assert TestPlatformConnector.executions == 1
    assert row("SELECT id FROM audit_events WHERE event_type='connector.tool.succeeded'")


def test_duplicate_webhook_is_verified_and_applied_once(graph):
    runtime, principal = _runtime(graph)
    applied: list[SyncRecord] = []
    engine = SyncEngine(lambda record, _: applied.append(record) or {}, runtime.registry)
    request = WebhookRequest(
        headers={"x-delivery-id": "delivery-7"},
        body=b'{"document":"doc-1","version":"v1"}',
    )

    first = engine.receive_webhook(TEST_MANIFEST.id, principal["active_workspace_id"], request)
    replay = engine.receive_webhook(TEST_MANIFEST.id, principal["active_workspace_id"], request)

    assert first == {
        "accepted": True,
        "replayed": False,
        "delivery_id": "delivery-7",
        "records_applied": 1,
    }
    assert replay["accepted"] is False
    assert replay["replayed"] is True
    assert len(applied) == 1
    assert applied[0].trust == "untrusted_connector_data"


def test_remote_mcp_oauth_uses_pkce_and_delegated_user(graph):
    session = create_dev_session("mcp-oauth@example.com", "MCP User")
    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, session["token"])
    registration = client.post(
        "/api/mcp/oauth/clients",
        headers={"Authorization": f"Bearer {session['token']}"},
        json={
            "name": "Remote agent",
            "redirect_uris": ["http://localhost/callback"],
            "scopes": ["read", "write"],
        },
    )
    assert registration.status_code == 200
    client_id = registration.json()["client_id"]
    verifier = "orgmemory-test-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    authorization = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost/callback",
            "scope": "read write",
            "state": "state-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    # A signed-in person is asked first; nothing is issued without consent.
    assert authorization.status_code == 200
    assert "Allow Remote agent" in authorization.text
    consent = authorization.text.split('name="consent" value="', 1)[1].split('"', 1)[0]
    approved = client.post(
        "/oauth/authorize",
        data={"consent": consent, "decision": "allow"},
        follow_redirects=False,
    )
    assert approved.status_code == 302
    code = approved.headers["location"].split("code=", 1)[1].split("&", 1)[0]
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": "http://localhost/callback",
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    payload = token.json()
    assert payload["scope"] == "read write"
    introspection = client.post(
        "/api/oauth/introspect",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert introspection.json()["active"] is True
    assert introspection.json()["sub"] == session["user"]["id"]
    assert introspection.json()["workspace_id"] == session["user"]["active_workspace_id"]
