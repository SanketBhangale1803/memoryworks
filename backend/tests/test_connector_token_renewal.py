"""Delegated grants renew themselves, and a dead one says "reconnect".

Google access tokens last an hour, and GitHub App user tokens eight. Before
this, the stored token was used until the provider answered 401, so Drive
stopped listing files an hour after every connect and the picker showed a
raw HTTP error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.auth.app_auth import create_dev_session
from app.auth.vault import OAuthTokenVault, access_expired
from app.connectors.github.client import GitHubConnector
from app.connectors.google_drive.client import GoogleDriveConnector


class _Response:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.content = payload if isinstance(payload, bytes) else b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self) -> Any:
        return self._payload


def _vault(email: str) -> OAuthTokenVault:
    principal = create_dev_session(email, "Grant Owner")["user"]
    return OAuthTokenVault(principal["active_workspace_id"], principal["id"])


def _stamp(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def test_expired_drive_token_is_renewed_before_listing(graph, monkeypatch):
    vault = _vault("drive-renew@example.com")
    vault.save(
        "google_drive",
        "ext",
        "Google Drive",
        "stale-token",
        {"scope": "drive.readonly"},
        refresh_token="refresh-1",
        expires_at=_stamp(-20),
    )
    exchanged: list[str] = []
    seen_tokens: list[str] = []

    def fake_post(url, data=None, **kwargs):
        exchanged.append(data["refresh_token"])
        return _Response({"access_token": "fresh-token", "expires_in": 3599})

    def fake_request(method, url, headers=None, **kwargs):
        seen_tokens.append(headers["Authorization"])
        return _Response({"files": [{"id": "doc-1", "name": "Plan", "mimeType": "x"}]})

    monkeypatch.setattr("app.connectors.google_drive.client.httpx.post", fake_post)
    monkeypatch.setattr("app.connectors.google_drive.client.httpx.request", fake_request)

    files = GoogleDriveConnector(vault).discover(vault.account("google_drive"))

    assert [item["name"] for item in files] == ["Plan"]
    assert exchanged == ["refresh-1"]
    assert seen_tokens == ["Bearer fresh-token"]
    renewed = vault.account("google_drive")
    assert renewed.access_token == "fresh-token"
    assert not access_expired(renewed)
    # Google doesn't rotate refresh tokens; the original keeps working.
    assert vault.refresh_token("google_drive") == "refresh-1"


def test_drive_grant_google_refuses_is_marked_expired(graph, monkeypatch):
    vault = _vault("drive-dead@example.com")
    vault.save("google_drive", "ext", "Google Drive", "revoked-token", refresh_token="gone")

    def refuse_refresh(url, **kwargs):
        return _Response({"error": "invalid_grant"}, status_code=400)

    monkeypatch.setattr("app.connectors.google_drive.client.httpx.post", refuse_refresh)
    monkeypatch.setattr(
        "app.connectors.google_drive.client.httpx.request",
        lambda *args, **kwargs: _Response({}, status_code=401),
    )

    with pytest.raises(ValueError, match="Reconnect Google Drive"):
        GoogleDriveConnector(vault).discover(vault.account("google_drive"))

    assert vault.account("google_drive") is None
    assert [item["status"] for item in vault.status("google_drive")] == ["expired"]


def test_reconnecting_restores_an_expired_grant(graph):
    vault = _vault("drive-reconnect@example.com")
    vault.save("google_drive", "ext", "Google Drive", "old")
    vault.mark_expired("google_drive")
    vault.save("google_drive", "ext", "Google Drive", "new", refresh_token="r")
    assert vault.account("google_drive").access_token == "new"


def test_github_401_without_refresh_token_asks_for_reconnect(graph, monkeypatch):
    vault = _vault("github-revoked@example.com")
    vault.save("github", "42", "octo", "revoked-oauth-app-token", {"scope": "repo"})
    monkeypatch.setattr(
        "app.connectors.github.client.httpx.request",
        lambda *args, **kwargs: _Response({"message": "Bad credentials"}, status_code=401),
    )

    with pytest.raises(ValueError, match="Reconnect GitHub"):
        GitHubConnector(vault).list_repositories()

    assert [item["status"] for item in vault.status("github")] == ["expired"]


def test_github_app_token_is_renewed_on_401(graph, monkeypatch):
    vault = _vault("github-app@example.com")
    vault.save("github", "42", "octo", "old-token", refresh_token="ghr_1", expires_at=_stamp(30))

    def fake_post(url, data=None, **kwargs):
        assert data["grant_type"] == "refresh_token"
        return _Response(
            {"access_token": "new-token", "refresh_token": "ghr_2", "expires_in": 28800}
        )

    def fake_request(method, url, headers=None, **kwargs):
        if headers["Authorization"] == "Bearer old-token":
            return _Response({}, status_code=401)
        return _Response([{"full_name": "octo/app"}])

    monkeypatch.setattr("app.connectors.github.client.httpx.post", fake_post)
    monkeypatch.setattr("app.connectors.github.client.httpx.request", fake_request)

    repositories = GitHubConnector(vault).list_repositories()

    assert repositories == [{"full_name": "octo/app"}]
    # GitHub rotates refresh tokens; the new one must replace the spent one.
    assert vault.refresh_token("github") == "ghr_2"
    assert vault.account("github").access_token == "new-token"


def test_drive_imports_exactly_the_selected_files_in_batches(monkeypatch):
    requested: list[str] = []

    def fake_request(method, url, **kwargs):
        file_id = url.rsplit("/", 1)[-1]
        requested.append(file_id)
        if file_id == "missing":
            return _Response({}, status_code=404)
        return _Response(
            {
                "id": file_id,
                "name": f"Doc {file_id}",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-10-01T00:00:00Z",
                "webViewLink": f"https://docs.google.com/{file_id}",
            }
        )

    monkeypatch.setattr("app.connectors.google_drive.client.httpx.request", fake_request)
    monkeypatch.setattr(
        "app.connectors.google_drive.client.httpx.get",
        lambda url, **kwargs: _Response(b"Decision: ship on Fridays."),
    )
    monkeypatch.setattr("app.connectors.google_drive.client.FILES_PER_BATCH", 2)
    from app.connectors.base import ConnectorAccount

    account = ConnectorAccount("a", "w", "u", "google_drive", "ext", "Drive", "token")
    connector = GoogleDriveConnector()

    first = connector.sync(account, {"file_ids": ["a1", "missing", "b2"]})
    assert [record.id for record in first.records] == ["gdrive-file:a1"]
    assert first.has_more
    second = connector.sync(account, first.next_cursor)
    assert [record.id for record in second.records] == ["gdrive-file:b2"]
    assert not second.has_more
    assert requested == ["a1", "missing", "b2"]
    assert [line.split(":")[0] for line in second.next_cursor["failures"]] == ["file missing"]


def test_drive_403_carries_googles_reason(graph, monkeypatch):
    vault = _vault("drive-forbidden@example.com")
    vault.save("google_drive", "ext", "Google Drive", "valid-token")
    reason = "Google Drive API has not been used in project 123 before or it is disabled."
    monkeypatch.setattr(
        "app.connectors.google_drive.client.httpx.request",
        lambda *args, **kwargs: _Response({"error": {"code": 403, "message": reason}}, 403),
    )

    with pytest.raises(ValueError, match="has not been used in project 123"):
        GoogleDriveConnector(vault).discover(vault.account("google_drive"))
    # A refusal is not an expired grant; the connection stays.
    assert vault.account("google_drive") is not None
