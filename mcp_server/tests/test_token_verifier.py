from __future__ import annotations

import asyncio

import httpx
import pytest

import server


@pytest.mark.parametrize("audience", [server.MCP_RESOURCE, "https://other.example/mcp", None])
def test_verifier_requires_the_local_resource_audience(monkeypatch, audience):
    async def introspect(request):
        assert request.url.path == "/api/oauth/introspect"
        assert request.headers["Authorization"] == "Bearer test_token"
        return httpx.Response(
            200,
            json={"active": True, "sub": "user", "aud": audience, "scope": "read"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(introspect))
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kwargs: client)
    token = asyncio.run(server.BackendTokenVerifier().verify_token("test_token"))

    if audience == server.MCP_RESOURCE:
        assert token is not None
        assert token.resource == server.MCP_RESOURCE
        assert token.scopes == ["read"]
    else:
        assert token is None
