from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.auth import OAuthStateStore
from app.auth.app_auth import (
    PUBLIC_DEMO_WORKSPACE_ID,
    issue_real_session,
    me_from_token,
)
from app.auth.google import google_oauth_url
from app.core.config import settings
from app.core.database import connect
from app.llm.providers import generate_grounded_json, model_catalog
from app.main import app


def test_local_loopback_origins_can_reach_the_api(graph):
    client = TestClient(app)

    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        response = client.options(
            "/api/auth/providers",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_email_code_login_issues_one_time_session(graph, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "email_auth_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "email_from", "")
    client = TestClient(app)

    requested = client.post("/api/auth/email/request", json={"email": "owner@example.com"})

    assert requested.status_code == 200
    payload = requested.json()
    assert payload["delivery"] == "development"
    assert len(payload["development_code"]) == 6

    verified = client.post(
        "/api/auth/email/verify",
        json={"email": "owner@example.com", "code": payload["development_code"]},
    )

    assert verified.status_code == 200
    assert verified.cookies.get(settings.session_cookie_name)
    assert verified.json()["user"]["auth_provider"] == "email"

    reused = client.post(
        "/api/auth/email/verify",
        json={"email": "owner@example.com", "code": payload["development_code"]},
    )
    assert reused.status_code == 400


def test_google_login_uses_pkce_and_redirects_to_workspace(graph, monkeypatch):
    # A deployment may pin PUBLIC_BASE_URL/FRONTEND_URL to its production
    # domain; these tests assert request-origin behavior, so pin them local.
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(settings, "google_client_id", "google-client")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(
        settings,
        "google_redirect_uri",
        "http://localhost:8000/api/auth/google/callback",
    )
    flow = OAuthStateStore().create("google", intent="login", use_pkce=True)
    authorization = parse_qs(urlparse(google_oauth_url(flow)).query)

    assert authorization["client_id"] == ["google-client"]
    assert authorization["code_challenge_method"] == ["S256"]
    assert "openid" in authorization["scope"][0]

    monkeypatch.setattr(
        "app.api.routes.complete_google_oauth",
        lambda code, consumed_flow, redirect_uri="": {
            "external_id": "google-user-42",
            "email": "owner@example.com",
            "display_name": "Owner",
            "avatar_url": "",
        },
    )
    response = TestClient(app).get(
        "/api/auth/google/callback",
        params={"code": "oauth-code", "state": flow["state"]},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    # The callback redirects to the origin the request actually came from
    # (the TestClient here), which is what makes a hosted deployment work
    # without baking its domain into the environment.
    assert response.headers["location"] == "http://testserver/workspace"
    assert response.cookies.get(settings.session_cookie_name)


def test_provider_and_model_catalogs_expose_readiness_without_secrets(graph, monkeypatch):
    monkeypatch.setattr(settings, "github_client_id", "")
    monkeypatch.setattr(settings, "github_client_secret", "")
    monkeypatch.setattr(settings, "openai_api_key", "configured-but-secret")
    response = TestClient(app).get("/api/auth/providers")
    models_response = TestClient(app).get("/api/models")

    assert response.status_code == 200
    assert response.json()["github"] is False
    assert "GITHUB_CLIENT_ID" in response.json()["details"]["github"]["setup"]
    assert models_response.status_code == 200
    assert next(item for item in model_catalog() if item["id"] == "gpt")["configured"] is True
    assert "configured-but-secret" not in models_response.text


def test_public_demo_login_uses_production_cookie_without_external_oauth(graph, monkeypatch):
    monkeypatch.setattr(settings, "public_demo_mode", True)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr("app.api.routes.seed_launch_scenario", lambda *args, **kwargs: {})

    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/auth/demo-login",
        json={"identity": "github", "display_name": "Challenge reviewer"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["auth_provider"] == "public-demo"
    assert response.json()["user"]["active_workspace_id"] == PUBLIC_DEMO_WORKSPACE_ID
    assert "token" not in response.json()
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie

    # Public demo sessions are signed rather than stored in container-local
    # SQLite, so a later request remains authenticated on another Vercel
    # instance. TestClient retains the secure cookie for the HTTPS base URL.
    authenticated = client.get("/api/auth/me")
    assert authenticated.status_code == 200
    assert authenticated.json()["active_workspace_id"] == PUBLIC_DEMO_WORKSPACE_ID

    token = response.cookies.get(settings.session_cookie_name)
    tampered_client = TestClient(app, base_url="https://testserver")
    assert (
        tampered_client.get(
            "/api/auth/me",
            headers={"Cookie": f"{settings.session_cookie_name}={token}tampered"},
        ).status_code
        == 401
    )


def test_openai_compatible_and_gemini_requests_use_current_provider_contracts(graph, monkeypatch):
    calls: list[dict] = []

    class Response:
        """A streamed reply: model calls read the body in pieces to bound total time."""

        def __init__(self, payload):
            self.body = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.body[:10]
            yield self.body[10:]

    def stream(method, url, **kwargs):
        assert method == "POST"
        calls.append({"url": url, **kwargs})
        if "generativelanguage" in url:
            return Response(
                {"candidates": [{"content": {"parts": [{"text": '{"answer":"gemini"}'}]}}]}
            )
        return Response({"choices": [{"message": {"content": '{"answer":"compatible"}'}}]})

    monkeypatch.setattr("app.llm.providers._open_stream", stream)
    monkeypatch.setattr(settings, "kimi_api_key", "kimi-secret")
    monkeypatch.setattr(settings, "openrouter_api_key", "openrouter-secret")
    monkeypatch.setattr(settings, "google_api_key", "google-secret")

    glm = generate_grounded_json("prompt", "glm")
    kimi = generate_grounded_json("prompt", "kimi")
    gemini = generate_grounded_json("prompt", "gemini")

    assert glm and glm[0]["answer"] == "compatible"
    assert calls[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls[0]["json"]["model"] == "z-ai/glm-5.3-flash"
    assert calls[0]["headers"]["HTTP-Referer"] == settings.frontend_url
    assert calls[0]["headers"]["X-OpenRouter-Title"] == "MemoryWorks"
    assert calls[0]["headers"]["Authorization"] == "Bearer openrouter-secret"
    assert "temperature" not in calls[0]["json"]
    assert kimi and kimi[0]["answer"] == "compatible"
    assert calls[1]["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert "temperature" not in calls[1]["json"]
    assert calls[1]["json"]["thinking"] == {"type": "disabled"}
    assert gemini and gemini[0]["answer"] == "gemini"
    assert calls[2]["headers"]["x-goog-api-key"] == "google-secret"
    assert "params" not in calls[2]
    assert "temperature" not in calls[2]["json"]["generationConfig"]


def test_oauth_state_and_sessions_survive_container_loss(graph, monkeypatch):
    """The hosted deployment runs stateless containers over per-container SQLite.

    A flow row written by /start is invisible to /callback on another container,
    and a session row likewise. Both must survive via their signed tokens:
    consume() decodes the state without its row, and me_from_token resolves an
    issued session after every local session row is gone.
    """
    store = OAuthStateStore()
    created = store.create("github", intent="login", use_pkce=True)
    assert created["code_challenge"]

    with connect() as conn:
        conn.execute("DELETE FROM oauth_flows")

    flow = store.consume("github", created["state"])
    assert flow["intent"] == "login"
    assert flow["code_verifier"]

    # Real production profile: the provider identity gets its own workspace,
    # and the session still resolves after every local session row is gone.
    monkeypatch.setattr(settings, "public_demo_mode", False)
    session = issue_real_session(
        "github",
        "github-user-42",
        "owner@example.com",
        "Owner",
        "Owner workspace",
    )
    assert session["token"].startswith("omr_")
    assert session["user"]["active_workspace_id"] != PUBLIC_DEMO_WORKSPACE_ID

    # Simulate the next request landing on a fresh container: no sessions row,
    # no user row, no workspace membership.
    with connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM workspace_members")
        conn.execute("DELETE FROM workspaces")
        conn.execute("DELETE FROM users")
    principal = me_from_token(session["token"])
    assert principal and principal["email"] == "owner@example.com"
    assert principal["active_workspace_id"]
    assert principal["role"] == "owner"

    # A tampered token resolves to nothing. Corrupt the FIRST signature
    # character: every base64 character there is fully significant. (The last
    # character can carry unused padding bits — changing it sometimes decodes
    # to the identical signature and the token stays valid, which flaked this
    # test about one run in thirty.)
    signature_start = session["token"].rfind(".") + 1
    head = "y" if session["token"][signature_start] != "y" else "z"
    tampered = session["token"][:signature_start] + head + session["token"][signature_start + 1 :]
    assert tampered != session["token"]
    assert me_from_token(tampered) is None
