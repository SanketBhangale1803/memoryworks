from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.auth import ConnectorSecrets
from app.auth.vault import access_expired
from app.connectors.base import (
    Connector,
    ConnectorAccount,
    ConnectorCapabilityError,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorResource,
    ConnectorTool,
    DataPolicy,
    ExecutionMode,
    OAuthConfig,
    RateLimitPolicy,
    RetryPolicy,
    SyncBatch,
    SyncOperation,
    SyncRecord,
    ToolKind,
    WebhookEvent,
    WebhookRequest,
)
from app.core.config import settings
from app.ingestion.documents import UnsupportedDocumentError, extract_document

DRIVE_API = "https://www.googleapis.com/drive/v3"
OAUTH_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN = "https://oauth2.googleapis.com/token"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
MAX_FILE_BYTES = 25 * 1024 * 1024
FILES_PER_BATCH = 25
# Picked files are imported one per batch: a large PDF can take minutes, and the
# job's progress, its lease, and the worker's pause for answers all happen
# between batches.
SELECTED_FILES_PER_BATCH = 1
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,modifiedTime,webViewLink,owners,size"
# Google-native editors export to these plain formats; binaries are parsed by
# the shared document extractor instead.
EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/markdown",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "image/png",
}

_MANIFEST = ConnectorManifest(
    id="google_drive",
    name="Google Drive",
    icon="google_drive",
    version="1.0.0",
    execution_mode=ExecutionMode.CLOUD,
    oauth=OAuthConfig(
        authorization_url=OAUTH_AUTH,
        token_url=OAUTH_TOKEN,
        scopes=(DRIVE_SCOPE,),
        pkce_required=False,
    ),
    resources=(
        ConnectorResource("file", "Files and native documents", syncable=True),
        ConnectorResource("folder", "Folders", syncable=False),
    ),
    tools=(
        ConnectorTool(
            "search_files",
            "Search the connected Google Drive for files by name or content.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "read_file",
            "Download and extract the text of one Drive file by id.",
            ToolKind.READ,
        ),
    ),
    webhooks=(),
    rate_limit=RateLimitPolicy(requests=240, window_seconds=60, burst=10),
    retry=RetryPolicy(max_attempts=6, base_delay_seconds=2, max_delay_seconds=180),
    data_policy=DataPolicy(
        residency="MemoryWorks workspace region",
        retention="Until source disconnect or workspace retention policy",
    ),
    package="orgmemory.connector.google_drive",
)
GOOGLE_DRIVE_MANIFEST = replace(_MANIFEST, signature=_MANIFEST.digest())


class GoogleDriveConnector(Connector):
    manifest = GOOGLE_DRIVE_MANIFEST

    def __init__(self, secrets: ConnectorSecrets | None = None):
        self.secrets = secrets or ConnectorSecrets()

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.secrets.status(self.manifest.id)

    # -- OAuth ----------------------------------------------------------------

    def oauth_configured(self) -> bool:
        return bool(settings.google_client_id and settings.google_client_secret)

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        if not settings.google_client_id:
            raise ValueError("Google OAuth is not configured (GOOGLE_CLIENT_ID)")
        flow = user.get("flow") or user
        params = {
            "client_id": settings.google_client_id,
            "response_type": "code",
            "scope": " ".join(scopes or [DRIVE_SCOPE]),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": flow["state"],
            "redirect_uri": flow.get("redirect_uri")
            or f"{settings.api_url.rstrip('/')}/api/connectors/google_drive/auth/callback",
        }
        return f"{OAUTH_AUTH}?{urlencode(params)}"

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        if not settings.google_client_id or not settings.google_client_secret:
            raise ValueError("Google OAuth is not configured")
        response = httpx.post(
            OAUTH_TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": flow.get("redirect_uri")
                or f"{settings.api_url.rstrip('/')}/api/connectors/google_drive/auth/callback",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)))
        ).isoformat()
        return {
            "token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "external_id": str(data.get("id_token", "google_drive")[:64] or "google_drive"),
            "display_name": "Google Drive",
            "scope": data.get("scope", DRIVE_SCOPE),
            "expires_at": expires_at,
        }

    # -- Sync -----------------------------------------------------------------

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        """The person's most recently modified Drive files, for choosing what to import."""
        token = self._token(account)
        payload = self._api(
            token,
            "GET",
            "/files",
            {
                "pageSize": 100,
                "orderBy": "modifiedTime desc",
                # Folders and trashed files can't be imported, so don't offer them.
                "q": f"trashed = false and mimeType != '{FOLDER_MIME}'",
                "fields": "files(id,name,mimeType,modifiedTime,webViewLink,owners)",
            },
        )
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "mime_type": item.get("mimeType"),
                "modified_at": item.get("modifiedTime", ""),
                "owner": ((item.get("owners") or [{}])[0].get("displayName") or ""),
                "url": item.get("webViewLink", ""),
            }
            for item in payload.get("files", [])
        ]

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        cursor = dict(cursor or {})
        if cursor.get("file_ids"):
            return self._sync_selected(self._token(account), cursor)
        token = self._token(account)
        params: dict[str, Any] = {
            "pageSize": FILES_PER_BATCH,
            "orderBy": "modifiedTime desc",
            "fields": f"nextPageToken,files({FILE_FIELDS})",
        }
        query = ["trashed = false"]
        if cursor.get("folder_id"):
            query.append(f"'{cursor['folder_id']}' in parents")
        if cursor.get("last_modified"):
            query.append(f"modifiedTime > '{cursor['last_modified']}'")
        params["q"] = " and ".join(query)
        if cursor.get("page_token"):
            params["pageToken"] = cursor["page_token"]
        payload = self._api(token, "GET", "/files", params)
        files = payload.get("files", [])
        failures: list[str] = []
        records = self._records(token, files, failures)
        next_page = str(payload.get("nextPageToken") or "")
        newest = max(
            [str(item.get("modifiedTime") or "") for item in files]
            + [cursor.get("last_modified", "")]
        )
        return SyncBatch(
            tuple(records),
            {
                **cursor,
                "page_token": next_page,
                "last_modified": newest,
                "failures": failures[:10],
                "synced_at": datetime.now(UTC).isoformat(),
            },
            has_more=bool(next_page),
        )

    def _sync_selected(self, token: str, cursor: dict[str, Any]) -> SyncBatch:
        """Import exactly the files the person picked, a batch at a time."""
        file_ids = [str(item) for item in cursor["file_ids"] if str(item)]
        offset = int(cursor.get("offset") or 0)
        failures = list(cursor.get("failures") or [])
        files = []
        for file_id in file_ids[offset : offset + SELECTED_FILES_PER_BATCH]:
            try:
                files.append(self._api(token, "GET", f"/files/{file_id}", {"fields": FILE_FIELDS}))
            except httpx.HTTPError as exc:
                failures.append(f"file {file_id}: {exc}")
        records = self._records(token, files, failures)
        offset += SELECTED_FILES_PER_BATCH
        return SyncBatch(
            tuple(records),
            {
                **cursor,
                "offset": offset,
                # Every failure is kept (a selection is at most a page of files),
                # so the importer can say exactly which ones didn't make it.
                "failures": failures,
                "synced_at": datetime.now(UTC).isoformat(),
            },
            has_more=offset < len(file_ids),
        )

    def _records(
        self, token: str, files: list[dict[str, Any]], failures: list[str]
    ) -> list[SyncRecord]:
        records: list[SyncRecord] = []
        for item in files:
            mime = str(item.get("mimeType") or "")
            file_id = str(item.get("id") or "")
            if not file_id or mime == FOLDER_MIME:
                continue
            content = self._file_content(token, file_id, mime, failures)
            modified = str(item.get("modifiedTime") or "")
            if content is None:
                continue
            records.append(
                SyncRecord(
                    id=f"gdrive-file:{file_id}",
                    resource_type="document",
                    operation=SyncOperation.UPSERT,
                    version=modified,
                    title=str(item.get("name") or file_id),
                    content=content,
                    source_url=str(item.get("webViewLink") or ""),
                    updated_at=modified,
                    metadata={
                        "drive_file_id": file_id,
                        "mime_type": mime,
                        "owner": ((item.get("owners") or [{}])[0].get("emailAddress") or ""),
                        "warnings": failures,
                    },
                )
            )
        return records

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        token = self._token(account)
        escaped = query.replace("'", "\\'")
        payload = self._api(
            token,
            "GET",
            "/files",
            {
                "pageSize": 20,
                "fields": "files(id,name,mimeType,webViewLink,modifiedTime)",
                "q": f"fullText contains '{escaped}'",
            },
        )
        return [
            {
                "id": item.get("id"),
                "title": item.get("name"),
                "mime_type": item.get("mimeType"),
                "url": item.get("webViewLink", ""),
            }
            for item in payload.get("files", [])
        ]

    def execute(
        self,
        account: ConnectorAccount,
        action: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.manifest.tool(action)
        token = self._token(account)
        if action == "search_files":
            return {"results": self.search(account, str(arguments.get("query") or ""))}
        if action == "read_file":
            file_id = str(arguments.get("file_id") or "")
            if not file_id:
                raise ValueError("read_file requires file_id")
            meta = self._api(
                token,
                "GET",
                f"/files/{file_id}",
                {"fields": "id,name,mimeType,webViewLink,modifiedTime"},
            )
            content = self._file_content(token, file_id, str(meta.get("mimeType") or ""), [])
            return {
                "id": file_id,
                "title": meta.get("name"),
                "url": meta.get("webViewLink", ""),
                "content": content or "",
            }
        raise ConnectorCapabilityError(f"Unsupported Google Drive action {action!r}")

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        raise ConnectorCapabilityError(
            "Google Drive updates arrive through scheduled sync; webhooks are not enabled"
        )

    def revoke(self, account: ConnectorAccount) -> None:
        self.secrets.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        started = time.monotonic()
        try:
            token = self._token(account)
            self._api(token, "GET", "/about", {"fields": "user"})
            return ConnectorHealth(
                self.manifest.id,
                "healthy",
                datetime.now(UTC).isoformat(),
                int((time.monotonic() - started) * 1_000),
            )
        except Exception as exc:
            return ConnectorHealth(
                self.manifest.id,
                "disconnected" if "not connected" in str(exc) else "degraded",
                datetime.now(UTC).isoformat(),
                int((time.monotonic() - started) * 1_000),
                str(exc),
            )

    # -- internals ------------------------------------------------------------

    def _token(self, account: ConnectorAccount | None) -> str:
        account = (
            account if account and account.access_token else self.secrets.account("google_drive")
        )
        if not account or not account.access_token:
            raise ValueError("Google Drive is not connected")
        # Google access tokens last an hour. Renew ahead of expiry rather than
        # letting the first call of every session fail.
        if access_expired(account):
            return self._renew() or account.access_token
        return account.access_token

    def _renew(self) -> str | None:
        if not self.secrets.workspace_id or not self.secrets.user_id:
            return None
        renewed = self.secrets.renew("google_drive", _refresh_exchange)
        return renewed.access_token if renewed else None

    def _file_content(self, token: str, file_id: str, mime: str, failures: list[str]) -> str | None:
        try:
            if mime in EXPORT_MIME:
                export_mime = EXPORT_MIME[mime]
                response = httpx.get(
                    f"{DRIVE_API}/files/{file_id}/export",
                    params={"mimeType": export_mime},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=60,
                )
                response.raise_for_status()
                if export_mime in {"text/plain", "text/markdown", "text/csv"}:
                    return response.content.decode("utf-8", errors="replace")
                return f"(Exported as {export_mime}; binary preview not indexed)"
            response = httpx.get(
                f"{DRIVE_API}/files/{file_id}",
                params={"alt": "media"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=120,
            )
            response.raise_for_status()
            data = response.content[:MAX_FILE_BYTES]
            document = extract_document("drive-file", data)
            return document.text
        except (httpx.HTTPError, UnsupportedDocumentError) as exc:
            failures.append(f"file {file_id}: {exc}")
            return None

    def _api(self, token: str, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = _send(token, method, path, params)
        if response.status_code == 401:
            # The token was revoked or expired early. One renewal attempt; if
            # Google refuses that too, the grant is dead and only Reconnect
            # can fix it, so say exactly that instead of a raw 401.
            renewed = self._renew()
            if not renewed:
                self.secrets.mark_expired(self.manifest.id)
                raise ValueError(RECONNECT_MESSAGE)
            response = _send(renewed, method, path, params)
        if response.status_code == 403:
            # A 403 is about the account or the Google Cloud project (the Drive
            # API disabled, a missing scope, a quota), never one file — and only
            # Google's own reason tells someone which of those to fix.
            raise ValueError(f"Google Drive refused the request: {_google_reason(response)}")
        response.raise_for_status()
        return response.json()


def _google_reason(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return f"HTTP {response.status_code}"
    message = str(error.get("message") or "").strip()
    return message or f"HTTP {response.status_code}"


RECONNECT_MESSAGE = (
    "Google Drive access has expired and could not be renewed. "
    "Reconnect Google Drive on the Sources page."
)


def _send(token: str, method: str, path: str, params: dict[str, Any]) -> httpx.Response:
    return httpx.request(
        method,
        f"{DRIVE_API}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )


def _refresh_exchange(refresh_token: str) -> dict[str, Any]:
    response = httpx.post(
        OAUTH_TOKEN,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
