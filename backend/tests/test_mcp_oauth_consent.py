"""Hosted MCP OAuth: consent, scope negotiation, and sign-in round trips."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth.api_keys import create_api_key
from app.auth.app_auth import create_dev_session
from app.auth.mcp_oauth import oauth_resource_metadata, oauth_server_metadata
from app.auth.security import safe_next_path
from app.core.config import settings
from app.main import app

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "launch-day-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
)


@pytest.fixture(autouse=True)
def _dynamic_registration(monkeypatch):
    # Production enables dynamic client registration (ChatGPT and Claude use it).
    monkeypatch.setattr(settings, "mcp_oauth_enable_dcr", True)


def _register(client: TestClient, scope: str | None = None) -> dict:
    payload = {"client_name": "Claude", "redirect_uris": [REDIRECT]}
    if scope is not None:
        payload["scope"] = scope
    response = client.post("/oauth/register", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _authorize(client: TestClient, client_id: str, scope: str = "read write"):
    return client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "scope": scope,
            "state": "s1",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )


def _consent(page_html: str) -> str:
    return page_html.split('name="consent" value="', 1)[1].split('"', 1)[0]


def _signed_in_client(email: str) -> tuple[TestClient, dict]:
    session = create_dev_session(email, "Consent User")
    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, session["token"])
    return client, session


def test_registration_without_scope_allows_read_and_write(graph):
    client = TestClient(app)
    assert _register(client)["scope"] == "read write"
    assert _register(client, "read")["scope"] == "read"


def test_authorize_never_issues_a_code_without_consent(graph):
    client, _ = _signed_in_client("consent-1@example.com")
    registered = _register(client)
    page = _authorize(client, registered["client_id"])
    assert page.status_code == 200
    assert "code=" not in page.headers.get("location", "")
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "claude.ai" in page.text

    denied = client.post(
        "/oauth/authorize",
        data={"consent": _consent(page.text), "decision": "deny"},
        follow_redirects=False,
    )
    query = parse_qs(urlparse(denied.headers["location"]).query)
    assert query == {"error": ["access_denied"], "state": ["s1"]}


def test_approved_read_write_token_carries_write(graph):
    client, _ = _signed_in_client("consent-2@example.com")
    registered = _register(client)
    page = _authorize(client, registered["client_id"])
    approved = client.post(
        "/oauth/authorize",
        data={"consent": _consent(page.text), "decision": "allow"},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": registered["client_id"],
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
    )
    assert token.status_code == 200
    assert token.json()["scope"] == "read write"


def test_read_only_client_is_downscoped_instead_of_refused(graph):
    client, _ = _signed_in_client("consent-3@example.com")
    registered = _register(client, "read")
    page = _authorize(client, registered["client_id"], "read write")
    assert page.status_code == 200
    assert "<strong>write</strong>" not in page.text
    assert _authorize(client, registered["client_id"], "write").status_code == 400


def test_consent_token_is_bound_to_the_session_that_saw_it(graph):
    owner, _ = _signed_in_client("consent-owner@example.com")
    registered = _register(owner)
    consent = _consent(_authorize(owner, registered["client_id"]).text)
    other, _ = _signed_in_client("consent-other@example.com")
    stolen = other.post(
        "/oauth/authorize", data={"consent": consent, "decision": "allow"}, follow_redirects=False
    )
    assert stolen.status_code == 400
    anonymous = TestClient(app).post(
        "/oauth/authorize", data={"consent": consent, "decision": "allow"}, follow_redirects=False
    )
    assert anonymous.status_code == 401


def test_signed_out_authorize_returns_to_a_relative_path(graph, monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://memoryworks.app")
    client = TestClient(app)
    registered = _register(client)
    response = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": REDIRECT,
            "scope": "read write",
            "code_challenge": CHALLENGE,
        },
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "memoryworks.app"},
        follow_redirects=False,
    )
    location = urlparse(response.headers["location"])
    assert (location.scheme, location.netloc, location.path) == (
        "https",
        "memoryworks.app",
        "/login",
    )
    next_path = parse_qs(location.query)["next"][0]
    assert next_path.startswith("/oauth/authorize?")
    assert safe_next_path(next_path) == next_path


def test_metadata_scopes_and_issuer_agree(monkeypatch):
    monkeypatch.setattr(settings, "mcp_oauth_issuer_url", "https://memoryworks.app/")
    server = oauth_server_metadata()
    resource = oauth_resource_metadata()
    assert resource["authorization_servers"] == [server["issuer"]] == ["https://memoryworks.app"]
    assert resource["scopes_supported"] == server["scopes_supported"] == ["read", "write"]


def test_safe_next_path_refuses_open_redirects():
    for unsafe in (
        "https://evil.example/x",
        "//evil.example/x",
        "/\\evil.example",
        "javascript:alert(1)",
        "workspace",
        "/ok\nLocation: https://evil.example",
        "",
        None,
    ):
        assert safe_next_path(unsafe) == "/workspace", unsafe
    assert safe_next_path("/connectors?connected=github") == "/connectors?connected=github"
    assert safe_next_path("/oauth/authorize?client_id=a&scope=read") == (
        "/oauth/authorize?client_id=a&scope=read"
    )


def test_sign_in_start_carries_next_in_the_signed_state(graph):
    from app.auth.security import OAuthStateStore

    store = OAuthStateStore()
    flow = store.create("google", intent="login", use_pkce=True, next_path="/connectors")
    assert store.consume("google", flow["state"])["next_path"] == "/connectors"
    hostile = store.create("google", intent="login", next_path="https://evil.example")
    assert store.consume("google", hostile["state"])["next_path"] == ""


def test_introspection_rejects_workspace_api_keys(graph):
    """The hosted MCP endpoint is OAuth-only: an API key is never an active token."""
    _, session = _signed_in_client("consent-key@example.com")
    key = create_api_key("editor", session["user"]["active_workspace_id"], session["user"]["id"])[
        "api_key"
    ]
    client = TestClient(app)

    def introspect(bearer: str):
        return client.post("/api/oauth/introspect", headers={"Authorization": f"Bearer {bearer}"})

    # A real key or session passes the auth boundary but is not an MCP token.
    for bearer in (key, session["token"]):
        response = introspect(bearer)
        assert response.status_code == 200
        assert response.json() == {"active": False}
    # Anything else is refused at the boundary, which the MCP verifier also
    # treats as inactive.
    assert introspect("not-a-token").status_code == 401
