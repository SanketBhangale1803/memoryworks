"""A question that names a commit is answered with that commit.

MemoryWorks cited "commit 3a1990ebf9aa" in an answer, and the follow-up "Show
commit 3a1990ebf9aa diff" came back "I couldn't find this in your company's
memory": ingestion keeps a commit's message, not its diff, and a search for a
hash matches nothing useful. The commit lane resolves the SHA against stored
commit records and reads the change itself from GitHub.
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.retrieval.commits import commit_references

SHA = "3a1990ebf9aa4c2d8e7f6b5a4c3d2e1f0a9b8c7d"
SLUG = "acme/memoryworks"


def _setup(client: TestClient) -> tuple[dict[str, str], str]:
    from app.api import routes

    token = client.post(
        "/api/auth/dev-login",
        json={"email": "commits@example.com", "display_name": "Commits"},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = client.post("/api/projects", json={"name": "memoryworks"}, headers=headers).json()[
        "id"
    ]
    routes.ingestion.ingest_item(
        project_id,
        "github_commit",
        f"Commit {SHA[:12]}: Require approval when a recorded rule says changes need a reviewer",
        "\n".join(
            (
                f"Repository: {SLUG}",
                f"Commit SHA: {SHA}",
                "Author: sanket",
                "Committed at: 2026-09-30T10:00:00Z",
                "Message: Require approval when a recorded rule says changes need a reviewer",
            )
        ),
        f"https://github.com/{SLUG}/commit/{SHA}",
        f"commit-source:{SLUG}:{SHA}",
        {"repository": SLUG, "commit_sha": SHA},
    )
    return headers, project_id


class _FakeGitHub:
    calls: list[tuple[str, str]] = []
    fail: int | None = None

    def __init__(self, secrets=None):
        pass

    def commit(self, slug: str, sha: str) -> dict:
        _FakeGitHub.calls.append((slug, sha))
        if _FakeGitHub.fail:
            request = httpx.Request("GET", "https://api.github.com")
            raise httpx.HTTPStatusError(
                "denied",
                request=request,
                response=httpx.Response(_FakeGitHub.fail, request=request),
            )
        return {
            "sha": sha,
            "html_url": f"https://github.com/{slug}/commit/{sha}",
            "commit": {
                "message": "Require approval when a recorded rule says changes need a reviewer\n\nOwnership memories count too.",
                "author": {"name": "Sanket", "date": "2026-09-30T10:00:00Z"},
            },
            "author": {"login": "sanket"},
            "stats": {"additions": 3, "deletions": 1},
            "files": [
                {
                    "filename": "backend/app/briefing.py",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                    "patch": "@@ -10,4 +10,6 @@\n-    if memory_type == 'policy':\n+    if REVIEW_RE.search(text):\n+        reasons.append(text)",
                }
            ],
        }


def _ask(client, headers, project_id, query, stream=False):
    if not stream:
        return client.post(
            "/api/ask", json={"project_id": project_id, "query": query}, headers=headers
        ).json()
    response = client.post(
        "/api/ask/stream", json={"project_id": project_id, "query": query}, headers=headers
    )
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_a_named_commit_is_answered_with_its_diff(graph, monkeypatch):
    _FakeGitHub.calls, _FakeGitHub.fail = [], None
    monkeypatch.setattr("app.api.routes.GitHubConnector", _FakeGitHub)
    with TestClient(app) as client:
        headers, project_id = _setup(client)
        answer = _ask(client, headers, project_id, "Show commit 3a1990ebf9aa diff")

    assert _FakeGitHub.calls == [(SLUG, SHA)], "the full SHA from the record is fetched"
    assert answer["answer_sufficient"]
    text = answer["answer"]
    assert "Require approval when a recorded rule" in text
    assert "`backend/app/briefing.py` (modified, +3 −1)" in text
    assert "```diff" in text and "+    if REVIEW_RE.search(text):" in text
    assert f"https://github.com/{SLUG}/commit/{SHA}" in text
    assert answer["evidence"] and answer["evidence"][0]["source_type"] == "github_commit"


def test_the_stream_narrates_the_lookup(graph, monkeypatch):
    _FakeGitHub.calls, _FakeGitHub.fail = [], None
    monkeypatch.setattr("app.api.routes.GitHubConnector", _FakeGitHub)
    with TestClient(app) as client:
        headers, project_id = _setup(client)
        events = _ask(client, headers, project_id, "what did 3a1990e change?", stream=True)

    labels = [event["label"] for event in events if event["type"] == "step"]
    assert "Found commit 3a1990ebf9aa" in labels
    assert "Fetching the change from GitHub" in labels
    assert "Searching connected sources" not in labels, "a SHA is looked up, not searched for"
    assert events[-1]["type"] == "done" and events[-1]["answer"]["answer_sufficient"]


def test_without_github_the_stored_record_still_answers(graph, monkeypatch):
    _FakeGitHub.calls, _FakeGitHub.fail = [], 401
    monkeypatch.setattr("app.api.routes.GitHubConnector", _FakeGitHub)
    with TestClient(app) as client:
        headers, project_id = _setup(client)
        answer = _ask(client, headers, project_id, "Show commit 3a1990ebf9aa diff")

    assert answer["answer_sufficient"]
    assert "Require approval when a recorded rule" in answer["answer"]
    assert "the GitHub connection needs to be renewed" in answer["answer"]
    assert f"https://github.com/{SLUG}/commit/{SHA}" in answer["answer"]


def test_an_unknown_hash_falls_through_to_normal_answering(graph, monkeypatch):
    _FakeGitHub.calls, _FakeGitHub.fail = [], None
    monkeypatch.setattr("app.api.routes.GitHubConnector", _FakeGitHub)
    with TestClient(app) as client:
        headers, project_id = _setup(client)
        _ask(client, headers, project_id, "Show commit 9f9f9f9f9f diff")

    assert _FakeGitHub.calls == []


def test_only_hex_with_digits_and_letters_reads_as_a_sha():
    assert commit_references("show 3a1990ebf9aa and ABC1234") == ["3a1990ebf9aa", "abc1234"]
    assert commit_references("deadbeef accede 20260930 cafe1") == []
