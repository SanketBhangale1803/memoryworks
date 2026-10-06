"""Importing by asking in the chat.

"Import the docs from my Google Drive" used to be answered as a question: the
chat explained how Drive could be set up instead of importing anything. These
tests pin which messages are import requests, and what the chat endpoint does
with each one.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.app_auth import create_dev_session, create_workspace
from app.auth.vault import OAuthTokenVault
from app.ingestion.chat_import import ImportIntent, parse_import


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Import the docs from my Google Drive into memory", ImportIntent("google_drive")),
        (
            "can you import https://github.com/acme/api.git please",
            ImportIntent("github", "acme/api"),
        ),
        ("sync github repo acme/payments", ImportIntent("github", "acme/payments")),
        ("index all of my repositories", ImportIntent("github_all")),
        (
            "please ingest https://docs.example.com/handbook.pdf.",
            ImportIntent("website", "https://docs.example.com/handbook.pdf"),
        ),
        (
            "let's import www.example.com/blog",
            ImportIntent("website", "https://www.example.com/blog"),
        ),
        (
            "import the spreadsheets from drive named 'Q3 plan'",
            ImportIntent("google_drive", "Q3 plan", ("spreadsheets",)),
        ),
        (
            "Import the docs called Hiring Plan from my drive",
            ImportIntent("google_drive", "Hiring Plan"),
        ),
        ("import my slack #eng channel", ImportIntent("slack")),
    ],
)
def test_import_requests_are_recognised(message, expected):
    assert parse_import(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "How do I import from Google Drive?",
        "can I import from notion?",
        "What changed recently?",
        "remember that we deploy on Fridays",
        "add our drive policy: no USB sticks",
        "Who owns the import pipeline?",
        "import",
    ],
)
def test_questions_and_statements_are_not_imports(message):
    assert parse_import(message) is None


def _session(email: str) -> tuple[TestClient, dict[str, str], dict[str, Any]]:
    from app.main import app

    client = TestClient(app)
    token = client.post(
        "/api/auth/dev-login", json={"email": email, "display_name": "Importer"}
    ).json()["token"]
    create_workspace(f"Import Workspace {email}", f"Bearer {token}")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    project = client.post("/api/projects", json={"name": "Company memory"}, headers=headers).json()
    return client, headers, {**me, "project_id": project["id"]}


def test_a_question_is_left_for_the_answer_path(graph):
    client, headers, me = _session("chat-question@example.com")
    response = client.post(
        "/api/chat/import",
        json={"text": "What changed recently?", "project_id": me["project_id"]},
        headers=headers,
    )
    assert response.json() == {"matched": False}


def test_drive_import_without_a_connection_offers_to_connect(graph):
    client, headers, me = _session("chat-drive-none@example.com")
    body = client.post(
        "/api/chat/import",
        json={"text": "Import the docs from my Google Drive", "project_id": me["project_id"]},
        headers=headers,
    ).json()
    assert body["matched"] and body["status"] == "needs_connection"
    assert body["action"]["href"] == "/api/connectors/google_drive/auth/start"


def test_drive_import_queues_the_matching_files(graph, monkeypatch):
    client, headers, me = _session("chat-drive@example.com")
    OAuthTokenVault(me["active_workspace_id"], me["id"]).save(
        "google_drive", "ext", "Google Drive", "token"
    )
    listing = [
        {
            "id": "f1",
            "name": "Hiring plan",
            "mime_type": "application/vnd.google-apps.document",
            "url": "https://docs.google.com/f1",
        },
        {
            "id": "f2",
            "name": "Budget",
            "mime_type": "application/vnd.google-apps.spreadsheet",
            "url": "https://docs.google.com/f2",
        },
    ]
    monkeypatch.setattr(
        "app.api.routes.connector_runtime.discover", lambda provider, principal: listing
    )

    body = client.post(
        "/api/chat/import",
        json={"text": "import 'hiring' from my google drive", "project_id": me["project_id"]},
        headers=headers,
    ).json()

    assert body["status"] == "running"
    assert [item["name"] for item in body["files"]] == ["Hiring plan"]
    job = client.get(f"/api/connector-sync-jobs/{body['job']['id']}", headers=headers).json()
    assert job["provider"] == "google_drive"
    assert job["project_id"] == me["project_id"]
    assert job["cursor"]["file_ids"] == ["f1"]


def test_unsupported_sources_point_to_where_they_can_be_imported(graph):
    client, headers, me = _session("chat-slack@example.com")
    body = client.post(
        "/api/chat/import",
        json={"text": "import my slack channels", "project_id": me["project_id"]},
        headers=headers,
    ).json()
    assert body["status"] == "unsupported"
    assert body["action"]["href"] == "/ingest?source=slack"


def test_another_workspaces_sync_job_is_not_readable(graph):
    client, headers, me = _session("chat-owner@example.com")
    from app.api.routes import connector_sync

    own = connector_sync.enqueue(
        "google_drive",
        me["active_workspace_id"],
        me["id"],
        "selected-files",
        project_id=me["project_id"],
        cursor={"file_ids": ["mine"]},
    )
    stranger = create_dev_session("chat-stranger@example.com", "Stranger")
    elsewhere = create_workspace("Stranger workspace", stranger["token"])
    foreign = connector_sync.enqueue(
        "google_drive",
        elsewhere["id"],
        stranger["user"]["id"],
        "selected-files",
        cursor={"file_ids": ["theirs"]},
    )
    assert client.get(f"/api/connector-sync-jobs/{own['id']}", headers=headers).status_code == 200
    assert (
        client.get(f"/api/connector-sync-jobs/{foreign['id']}", headers=headers).status_code == 404
    )


def test_drive_import_with_an_expired_grant_says_reconnect(graph):
    client, headers, me = _session("chat-drive-expired@example.com")
    vault = OAuthTokenVault(me["active_workspace_id"], me["id"])
    vault.save("google_drive", "ext", "Google Drive", "dead-token")
    vault.mark_expired("google_drive")
    body = client.post(
        "/api/chat/import",
        json={"text": "import my google drive docs", "project_id": me["project_id"]},
        headers=headers,
    ).json()
    assert body["status"] == "needs_connection"
    assert "expired" in body["message"]
    assert body["action"]["label"] == "Reconnect Google Drive"


@pytest.mark.parametrize(
    ("message", "limit"),
    [
        ("Ingest first 3 files from Drive", 3),
        ("import the latest five docs from my Google Drive", 5),
        ("Import 2 sheets from GD", 2),
        ("import the docs from my google drive", 0),
    ],
)
def test_a_number_of_files_is_how_many_get_imported(message, limit):
    intent = parse_import(message)
    assert intent and intent.source == "google_drive"
    assert intent.limit == limit


def test_drive_import_takes_only_as_many_files_as_asked(graph, monkeypatch):
    """ "Ingest first 3 files" queued 25 (the cap), which flooded the server."""
    client, headers, me = _session("chat-drive-count@example.com")
    OAuthTokenVault(me["active_workspace_id"], me["id"]).save(
        "google_drive", "ext", "Google Drive", "token"
    )
    listing = [
        {
            "id": f"f{index}",
            "name": f"File {index}",
            "mime_type": "application/pdf",
            "url": f"https://drive.google.com/f{index}",
        }
        for index in range(10)
    ]
    monkeypatch.setattr(
        "app.api.routes.connector_runtime.discover", lambda provider, principal: listing
    )

    body = client.post(
        "/api/chat/import",
        json={"text": "Ingest first 3 files from Drive", "project_id": me["project_id"]},
        headers=headers,
    ).json()

    assert body["status"] == "running"
    assert [item["id"] for item in body["files"]] == ["f0", "f1", "f2"]
    assert "more can be picked" not in body["message"]
    job = client.get(f"/api/connector-sync-jobs/{body['job']['id']}", headers=headers).json()
    assert job["cursor"]["file_ids"] == ["f0", "f1", "f2"]
