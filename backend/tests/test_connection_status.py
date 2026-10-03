"""Questions about a connection are answered from the connection records.

"Why is the Google Drive connection failing?" took 73 s and came back with how
the connector is built — company memory has no idea that Google blocked the
consent screen an hour ago. The system's own records do: a connection attempt
that was started and never came back is exactly what a blocked consent screen
looks like from MemoryWorks's side.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.auth import OAuthStateStore
from app.connectors.status import is_connection_question, mentioned_providers, provider_status
from app.core.database import connect
from app.main import app


def _signed_in(client: TestClient) -> tuple[dict[str, str], str, str]:
    token = client.post(
        "/api/auth/dev-login",
        json={"email": "drive@example.com", "display_name": "Drive"},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    project = client.post("/api/projects", json={"name": "memoryworks"}, headers=headers).json()
    return headers, me["active_workspace_id"], project["id"]


def _abandoned_attempt(workspace_id: str, minutes_ago: int = 30) -> str:
    """A connection attempt sent to the provider that never came back."""
    flow = OAuthStateStore().create("google_drive", workspace_id=workspace_id, user_id="u")
    expired = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    with connect() as conn:
        conn.execute("UPDATE oauth_flows SET expires_at=? WHERE state=?", (expired, flow["state"]))
    return flow["state"]


def test_connection_questions_are_told_apart_from_content_questions():
    assert is_connection_question("why is the google drive connection failing?")
    assert is_connection_question("show me the recent google drive connections")
    assert is_connection_question("is Slack still connected?")
    assert is_connection_question("why did the notion import fail")
    assert not is_connection_question("what did Slack say about access control?")
    assert not is_connection_question("summarize the onboarding doc in google drive")
    assert not is_connection_question("why is checkout failing?")
    assert mentioned_providers("Sign in with Google is broken") == []


def test_an_attempt_that_never_came_back_is_diagnosed(graph):
    with TestClient(app):
        _abandoned_attempt("ws_drive")
        _abandoned_attempt("ws_drive", minutes_ago=50)
        report = provider_status("ws_drive", "google_drive")

    assert [item["outcome"] for item in report["attempts"]] == ["never_returned", "never_returned"]
    finding = " ".join(report["findings"])
    assert "2 connection attempts in this period never came back from Google Drive" in finding
    assert "test users" in finding


def test_a_provider_refusal_is_recorded_and_reported(graph):
    with TestClient(app) as client:
        headers, workspace_id, _ = _signed_in(client)
        state = _abandoned_attempt(workspace_id)
        response = client.get(
            "/api/connectors/google_drive/auth/callback",
            params={"state": state, "error": "access_denied"},
            follow_redirects=False,
        )
        report = provider_status(workspace_id, "google_drive")

    # Used to be a raw 422 because `code` was required.
    assert response.status_code in {302, 307}
    message = parse_qs(urlparse(response.headers["location"]).query)["error"][0]
    assert "refused the connection: access_denied" in message
    assert report["attempts"][0]["outcome"] == "denied"
    assert "refused the most recent connection (access_denied)" in report["findings"][0]


def test_ask_answers_from_live_records_without_searching(graph):
    with TestClient(app) as client:
        headers, workspace_id, project_id = _signed_in(client)
        _abandoned_attempt(workspace_id)
        response = client.post(
            "/api/ask/stream",
            json={"project_id": project_id, "query": "why is the google drive connection failing?"},
            headers=headers,
        )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    labels = [event["label"] for event in events if event["type"] == "step"]
    final = events[-1]["answer"]

    assert "Checking live connection records" in labels
    assert "Searching connected sources" not in labels
    assert final["answer_scope"] == "system_state"
    assert final["answer_sufficient"]
    assert "never came back from Google Drive" in final["answer"]
    assert "access_token" not in json.dumps(final)


def test_agent_tool_reports_connections(graph):
    from app.orgops.agent import build_org_executor

    with TestClient(app):
        _abandoned_attempt("ws_agent")
        execute = build_org_executor(None, lambda _: [], None, None)
        summary, data = execute(
            {"id": "u", "active_workspace_id": "ws_agent"},
            "get_orgmemory_connections",
            {"provider": "Google Drive"},
        )

    assert "never came back from Google Drive" in summary
    assert data["reports"][0]["provider"] == "google_drive"
