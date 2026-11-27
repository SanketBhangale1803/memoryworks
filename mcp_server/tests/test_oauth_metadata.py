from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def test_protected_resource_metadata_matches_the_authorization_server():
    client = TestClient(server.mcp.streamable_http_app())
    for path in (
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.json()
        assert body["resource"] == server.MCP_RESOURCE
        assert body["authorization_servers"] == [server.OAUTH_ISSUER]
        assert not body["authorization_servers"][0].endswith("/")
        assert body["scopes_supported"] == ["read", "write"]


def test_unauthenticated_mcp_request_points_at_the_metadata():
    client = TestClient(server.mcp.streamable_http_app())
    response = client.post("/mcp", json={})
    assert response.status_code == 401
    assert "oauth-protected-resource/mcp" in response.headers.get("www-authenticate", "")
