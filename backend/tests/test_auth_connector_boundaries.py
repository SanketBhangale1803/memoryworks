from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth import ConnectorSecrets, OAuthStateStore
from app.auth.app_auth import (
    create_dev_session,
    create_oauth_session,
    create_workspace,
    invite_member,
    me_from_token,
)
from app.connectors.github import GitHubConnector
from app.connectors.slack import SlackConnector
from app.core.config import settings
from app.core.database import connect, new_id, row, utcnow
from app.main import app


def test_application_api_requires_login_and_dev_login_sets_cookie(graph):
    client = TestClient(app)

    assert client.get("/api/projects").status_code == 401
    response = client.post(
        "/api/auth/dev-login",
        json={"email": "boundary@example.com", "display_name": "Boundary Owner"},
    )

    assert response.status_code == 200
    assert response.cookies.get("runbook_session")
    assert client.get("/api/projects").status_code == 200


def test_dev_session_never_replaces_oauth_identity_or_workspace_membership(graph):
    oauth = create_oauth_session(
        "github",
        "github-identity-7",
        "same-user@example.com",
        "Same User",
        "Same User workspace",
    )
    workspace_id = oauth["user"]["active_workspace_id"]

    local = create_dev_session("same-user@example.com", "Same User local check")

    identity = row(
        "SELECT auth_provider,external_id FROM users WHERE email=?",
        ("same-user@example.com",),
    )
    membership = row(
        "SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",
        (workspace_id, local["user"]["id"]),
    )
    assert identity == {"auth_provider": "github", "external_id": "github-identity-7"}
    assert membership == {"role": "owner"}
    assert local["user"]["active_workspace_id"] == workspace_id
    assert me_from_token(local["token"])["active_workspace_id"] == workspace_id


def test_connector_secrets_are_isolated_by_workspace(graph):
    first = create_dev_session("first@example.com", "First")
    second = create_dev_session("second@example.com", "Second")
    first_workspace = create_workspace("First isolated workspace", first["token"])
    second_workspace = create_workspace("Second isolated workspace", second["token"])
    first_secrets = ConnectorSecrets(first_workspace["id"], first["user"]["id"])
    second_secrets = ConnectorSecrets(second_workspace["id"], second["user"]["id"])

    first_secrets.save("github", "42", "octocat", "first-secret")

    assert first_secrets.token("github") == "first-secret"
    assert second_secrets.token("github") is None
    assert second_secrets.status("github") == []


def test_oauth_state_preserves_intent_and_cannot_be_reused(graph):
    store = OAuthStateStore()
    created = store.create(
        "github",
        intent="connect",
        workspace_id="wsp_test",
        user_id="usr_test",
        use_pkce=True,
        redirect_uri="https://orgmemory.example/api/auth/github/callback",
    )

    flow = store.consume("github", created["state"])

    assert flow["intent"] == "connect"
    assert flow["workspace_id"] == "wsp_test"
    assert flow["user_id"] == "usr_test"
    assert flow["redirect_uri"] == "https://orgmemory.example/api/auth/github/callback"
    assert flow["code_verifier"]
    assert created["code_challenge"]
    with pytest.raises(ValueError, match="invalid or expired"):
        store.consume("github", created["state"])


def test_github_connector_start_uses_public_request_origin(graph, monkeypatch):
    session = create_dev_session("connector-oauth@example.com", "Connector OAuth")
    monkeypatch.setattr(
        settings, "github_redirect_uri", "http://localhost:8000/api/auth/github/callback"
    )
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(settings, "github_client_id", "github-client")

    response = TestClient(app).get(
        "/api/connectors/github/auth/start",
        headers={"Authorization": f"Bearer {session['token']}"},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["redirect_uri"] == ["http://testserver/api/auth/github/callback"]
    flow = OAuthStateStore().consume("github", query["state"][0])
    assert flow["redirect_uri"] == "http://testserver/api/auth/github/callback"


def test_github_login_callback_redirects_to_authenticated_workspace(graph, monkeypatch):
    # Pin to a local config: a deployment may set PUBLIC_BASE_URL/FRONTEND_URL
    # to its production domain, and these tests assert request-origin behavior.
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "public_base_url", "")
    flow = OAuthStateStore().create("github", intent="login", use_pkce=True)
    monkeypatch.setattr(
        GitHubConnector,
        "complete_oauth",
        lambda self, code, consumed_flow, redirect_uri="": {
            "token": "github-access-token",
            "external_id": "github-user-42",
            "login": "runbook-owner",
            "display_name": "Runbook Owner",
            "email": "owner@example.com",
            "avatar_url": "",
            "scope": "read:user user:email",
        },
    )

    response = TestClient(app).get(
        "/api/auth/github/callback",
        params={"code": "oauth-code", "state": flow["state"]},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    # Redirect follows the request origin so hosted deployments need no
    # baked-in frontend URL.
    assert response.headers["location"] == "http://testserver/workspace"
    assert response.cookies.get(settings.session_cookie_name)


def test_github_sign_in_requests_repository_access(graph, monkeypatch):
    monkeypatch.setattr(settings, "github_client_id", "github-client")
    monkeypatch.setattr(settings, "public_base_url", "")

    response = TestClient(app).get("/api/auth/github/start", follow_redirects=False)

    assert response.status_code in {302, 307}
    scopes = parse_qs(urlparse(response.headers["location"]).query)["scope"][0].split()
    assert "repo" in scopes and "user:email" in scopes


def _sign_in_with_github(monkeypatch, scope: str, external_id: str, email: str):
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "public_base_url", "")
    flow = OAuthStateStore().create("github", intent="login", use_pkce=True)
    monkeypatch.setattr(
        GitHubConnector,
        "complete_oauth",
        lambda self, code, consumed_flow, redirect_uri="": {
            "token": "github-access-token",
            "external_id": external_id,
            "login": "memoryworks-owner",
            "display_name": "MemoryWorks Owner",
            "email": email,
            "avatar_url": "",
            "scope": scope,
        },
    )
    client = TestClient(app)
    response = client.get(
        "/api/auth/github/callback",
        params={"code": "oauth-code", "state": flow["state"]},
        follow_redirects=False,
    )
    assert response.status_code in {302, 307}
    token = response.cookies.get(settings.session_cookie_name)
    connectors = client.get("/api/connectors", headers={"Authorization": f"Bearer {token}"})
    assert connectors.status_code == 200
    return next(item for item in connectors.json() if item["provider"] == "github")


def test_github_sign_in_with_repo_scope_leaves_github_connected(graph, monkeypatch):
    github = _sign_in_with_github(
        monkeypatch, "repo,read:org,read:user,user:email", "github-user-77", "repo@example.com"
    )
    assert github["connected"] is True


def test_github_sign_in_without_repo_scope_does_not_fake_a_connection(graph, monkeypatch):
    github = _sign_in_with_github(
        monkeypatch, "read:user user:email", "github-user-78", "identity@example.com"
    )
    assert github["connected"] is False


def test_github_connector_callback_exchanges_code_only_in_runtime(graph, monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "http://localhost:3000")
    monkeypatch.setattr(settings, "public_base_url", "")
    flow = OAuthStateStore().create(
        "github", intent="connect", workspace_id="wsp_test", user_id="usr_test"
    )
    completed = {}

    def complete(provider, consumed_flow, code):
        completed.update(provider=provider, flow=consumed_flow, code=code)
        return {"grant_id": "grant_test"}

    monkeypatch.setattr("app.api.routes.connector_runtime.complete_authorization", complete)
    monkeypatch.setattr(
        GitHubConnector,
        "complete_oauth",
        lambda *_args: pytest.fail("connector callback must not exchange the code twice"),
    )

    response = TestClient(app).get(
        "/api/auth/github/callback",
        params={"code": "oauth-code", "state": flow["state"]},
        follow_redirects=False,
    )

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "http://testserver/connectors?connected=github"
    assert completed["provider"] == "github"
    assert completed["code"] == "oauth-code"


def test_repository_refresh_requires_approval_before_running(graph, monkeypatch):
    session = create_dev_session("refresh@example.com", "Refresh Requester")
    project_id = new_id("prj")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (
                project_id,
                "Refreshable project",
                "https://github.com/acme/refreshable.git",
                "ready",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO workspace_projects VALUES (?,?)",
            (session["user"]["active_workspace_id"], project_id),
        )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {session['token']}"}
    payload = {"project_id": project_id, "reason": "Recent GitHub evidence is stale."}

    proposed = client.post("/api/repository-refresh-requests", json=payload, headers=headers)
    assert proposed.status_code == 200
    proposal = proposed.json()
    assert proposal["status"] == "pending_approval"

    repeated = client.post("/api/repository-refresh-requests", json=payload, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == proposal["id"]

    executed: list[str] = []
    monkeypatch.setattr("app.api.routes._run_repository_refresh", executed.append)
    approved = client.post(
        f"/api/repository-refresh-requests/{proposal['id']}/resolve",
        json={"approved": True},
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "queued"
    assert executed == [proposal["id"]]


def test_workspace_admin_sees_requester_and_resolves_their_refresh(graph, monkeypatch):
    """The employee proposes a refresh; the workspace owner approves it inline."""
    owner = create_dev_session("admin2@example.com", "Workspace Admin")
    workspace_id = owner["user"]["active_workspace_id"]
    # Add the employee to the workspace first; their first sign-in then lands
    # them inside the team rather than in a personal workspace.
    invite_member(workspace_id, "employee@example.com", "member")
    requester_session = create_oauth_session(
        "github", "gh_team_employee", "employee@example.com", "Team Employee", "Personal"
    )
    assert requester_session["user"]["active_workspace_id"] == workspace_id

    project_id = new_id("prj")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?)",
            (project_id, "Admin project", "https://github.com/acme/team.git", "ready", now, now),
        )
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", (workspace_id, project_id))

    employee_client = TestClient(app)
    proposed = employee_client.post(
        "/api/repository-refresh-requests",
        json={"project_id": project_id, "reason": "Memory does not cover the new service."},
        headers={"Authorization": f"Bearer {requester_session['token']}"},
    )
    assert proposed.status_code == 200
    proposal = proposed.json()
    assert proposal["status"] == "pending_approval"
    assert proposal["requested_by_email"] == "employee@example.com"
    assert proposal["requested_by_name"] == "Team Employee"

    # A requester's browser-agent must not be able to self-approve. The same
    # endpoint backs the inline buttons and the WebMCP decision tool.
    self_resolution = employee_client.post(
        f"/api/repository-refresh-requests/{proposal['id']}/resolve",
        json={"approved": True},
        headers={"Authorization": f"Bearer {requester_session['token']}"},
    )
    assert self_resolution.status_code == 403

    listing = TestClient(app).get(
        "/api/repository-refresh-requests",
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert [
        item["requested_by_name"] for item in listing.json() if item["id"] == proposal["id"]
    ] == ["Team Employee"]

    resolved = TestClient(app).post(
        f"/api/repository-refresh-requests/{proposal['id']}/resolve",
        json={"approved": False},
        headers={"Authorization": f"Bearer {owner['token']}"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "denied"
    assert resolved.json()["resolved_by"] == owner["user"]["id"]


def test_slack_oauth_requests_and_prefers_personal_user_token(graph, monkeypatch):
    settings.slack_client_id = "slack-client"
    settings.slack_client_secret = "slack-secret"
    settings.slack_redirect_uri = "http://localhost:8000/api/auth/slack/callback"
    connector = SlackConnector()
    query = parse_qs(urlparse(connector.oauth_url({"state": "safe-state"})).query)

    assert "user_scope" in query
    assert "channels:history" in query["user_scope"][0]
    assert "chat:write" in query["user_scope"][0]

    class OAuthResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "access_token": "xoxb-bot-fallback",
                "scope": "channels:read",
                "token_type": "bot",
                "team": {"id": "T1", "name": "Acme"},
                "authed_user": {
                    "id": "U1",
                    "access_token": "xoxp-personal",
                    "scope": "channels:read,channels:history",
                    "token_type": "user",
                },
            }

    monkeypatch.setattr(
        "app.connectors.slack.client.httpx.post",
        lambda *args, **kwargs: OAuthResponse(),
    )

    identity = connector.complete_oauth("code", {})

    assert identity["token"] == "xoxp-personal"
    assert identity["slack_user_id"] == "U1"
    assert identity["token_type"] == "user"


def test_connectors_without_an_oauth_app_read_as_needing_setup(graph, monkeypatch):
    monkeypatch.setattr(settings, "slack_client_id", "")
    monkeypatch.setattr(settings, "slack_client_secret", "")
    monkeypatch.setattr(settings, "github_client_id", "github-client")
    monkeypatch.setattr(settings, "github_client_secret", "github-secret")

    assert SlackConnector().status().available is False
    assert GitHubConnector().status().available is True
