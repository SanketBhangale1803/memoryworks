from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.auth import ConnectorSecrets
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
from app.ingestion.documents import _html_text as html_to_text

GRAPH_API = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = (
    "offline_access",
    "Team.ReadBasic.All",
    "ChannelMessage.Read.All",
    "Channel.ReadBasic.All",
    "User.Read",
)
CHANNELS_PER_SYNC = 2
MESSAGES_PER_CHANNEL = 100

_MANIFEST = ConnectorManifest(
    id="teams",
    name="Microsoft Teams",
    icon="teams",
    version="1.0.0",
    execution_mode=ExecutionMode.CLOUD,
    oauth=OAuthConfig(
        authorization_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        scopes=GRAPH_SCOPES,
        pkce_required=False,
    ),
    resources=(
        ConnectorResource("team", "Teams", syncable=False),
        ConnectorResource("channel", "Channel messages", syncable=True),
        ConnectorResource("file", "Shared files", syncable=True),
    ),
    tools=(
        ConnectorTool(
            "list_teams",
            "List the Microsoft Teams the connected identity belongs to.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "get_channel_messages",
            "Read recent messages from one Teams channel.",
            ToolKind.READ,
        ),
    ),
    webhooks=(),
    rate_limit=RateLimitPolicy(requests=120, window_seconds=60, burst=5),
    retry=RetryPolicy(max_attempts=6, base_delay_seconds=3, max_delay_seconds=300),
    data_policy=DataPolicy(
        residency="MemoryWorks workspace region",
        retention="Until source disconnect or workspace retention policy",
    ),
    package="orgmemory.connector.teams",
)
TEAMS_MANIFEST = replace(_MANIFEST, signature=_MANIFEST.digest())


def _tenant() -> str:
    return settings.microsoft_tenant_id.strip() or "common"


def _auth_endpoint(path: str) -> str:
    return f"https://login.microsoftonline.com/{_tenant()}/oauth2/v2.0/{path}"


class TeamsConnector(Connector):
    manifest = TEAMS_MANIFEST

    def __init__(self, secrets: ConnectorSecrets | None = None):
        self.secrets = secrets or ConnectorSecrets()

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.secrets.status(self.manifest.id)

    # -- OAuth ----------------------------------------------------------------

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        if not settings.microsoft_client_id:
            raise ValueError("Microsoft OAuth is not configured (MICROSOFT_CLIENT_ID)")
        flow = user.get("flow") or user
        params = {
            "client_id": settings.microsoft_client_id,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(scopes or GRAPH_SCOPES),
            "state": flow["state"],
            "redirect_uri": flow.get("redirect_uri")
            or f"{settings.api_url.rstrip('/')}/api/connectors/teams/auth/callback",
        }
        return f"{_auth_endpoint('authorize')}?{urlencode(params)}"

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        if not settings.microsoft_client_id or not settings.microsoft_client_secret:
            raise ValueError("Microsoft OAuth is not configured")
        data = self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "code": code,
                "redirect_uri": flow.get("redirect_uri")
                or f"{settings.api_url.rstrip('/')}/api/connectors/teams/auth/callback",
            }
        )
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)))
        ).isoformat()
        profile = {}
        try:
            profile = httpx.get(
                f"{GRAPH_API}/me",
                headers={"Authorization": f"Bearer {data['access_token']}"},
                timeout=30,
            ).json()
        except Exception:
            profile = {}
        display_name = str(profile.get("displayName") or "Microsoft Teams")
        return {
            "token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "external_id": str(profile.get("id") or "microsoft"),
            "display_name": display_name,
            "scope": data.get("scope", " ".join(GRAPH_SCOPES)),
            "expires_at": expires_at,
        }

    # -- Sync -----------------------------------------------------------------

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        teams = self._api("GET", "/me/joinedTeams", token=self._token(account)).get("value", [])
        output: list[dict[str, Any]] = []
        for team in teams:
            channels = self._api(
                "GET",
                f"/teams/{team.get('id')}/channels",
                token=self._token(account),
            ).get("value", [])
            output.append(
                {
                    "id": team.get("id"),
                    "name": team.get("displayName"),
                    "channels": [
                        {"id": channel.get("id"), "name": channel.get("displayName")}
                        for channel in channels
                    ],
                }
            )
        return output

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        token = self._token(account)
        cursor = dict(cursor or {})
        records: list[SyncRecord] = []
        channel_queue: list[dict[str, str]] = list(cursor.get("channel_queue") or [])
        if not channel_queue and not cursor.get("channels_done"):
            channel_queue = self._channel_queue(token)
        for channel_info in channel_queue[:CHANNELS_PER_SYNC]:
            records.extend(self._channel_records(token, channel_info, cursor))
        remaining = channel_queue[CHANNELS_PER_SYNC:]
        return SyncBatch(
            tuple(records),
            {
                **cursor,
                "channel_queue": remaining,
                "channels_done": (
                    True if not remaining and channel_queue else cursor.get("channels_done", False)
                ),
                "synced_at": datetime.now(UTC).isoformat(),
            },
            has_more=bool(remaining),
        )

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        token = self._token(account)
        payload = {
            "requests": [
                {
                    "entityTypes": ["chatMessage"],
                    "query": {"queryString": query},
                    "from": 0,
                    "size": 25,
                }
            ]
        }
        try:
            response = self._api("POST", "/search/query", token=token, payload=payload)
        except Exception as exc:
            raise ConnectorCapabilityError(
                f"Microsoft Search does not cover this tenant or scope: {exc}"
            ) from exc
        results: list[dict[str, Any]] = []
        for container in response.get("value", []):
            for hit in container.get("hitsContainers", []) or container.get("hits", []):
                resource = hit.get("resource") or hit
                results.append(
                    {
                        "id": hit.get("hitId") or resource.get("id"),
                        "title": ((resource.get("summary") or "")[:160]),
                        "url": (resource.get("webUrl") or ""),
                        "snippet": resource.get("summary", ""),
                    }
                )
        return results

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
        if action == "list_teams":
            return {"teams": self.discover(account)}
        if action == "get_channel_messages":
            team_id = str(arguments.get("team_id") or "")
            channel_id = str(arguments.get("channel_id") or "")
            if not team_id or not channel_id:
                raise ValueError("get_channel_messages requires team_id and channel_id")
            messages = self._api(
                "GET",
                f"/teams/{team_id}/channels/{channel_id}/messages",
                token=token,
                params={"top": int(arguments.get("limit") or 50)},
            ).get("value", [])
            return {"messages": [self._message_payload(message) for message in messages]}
        raise ConnectorCapabilityError(f"Unsupported Teams action {action!r}")

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        raise ConnectorCapabilityError(
            "Teams updates arrive through scheduled sync; change notifications are not enabled"
        )

    def revoke(self, account: ConnectorAccount) -> None:
        self.secrets.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        started = time.monotonic()
        try:
            self._api("GET", "/me", token=self._token(account))
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
        if account and account.access_token:
            return account.access_token
        token = self.secrets.token("teams")
        if not token:
            raise ValueError("Microsoft Teams is not connected")
        return token

    def _refresh(self, account: ConnectorAccount) -> str | None:
        """Rotate an expired Microsoft access token using the stored grant."""
        if not self.secrets.workspace_id or not self.secrets.user_id:
            return None
        stored = self.secrets.account("teams")
        if not stored:
            return None
        refresh_token = self.secrets.refresh_token("teams")
        if not refresh_token or not settings.microsoft_client_id:
            return None
        try:
            data = self._token_request(
                {
                    "grant_type": "refresh_token",
                    "client_id": settings.microsoft_client_id,
                    "client_secret": settings.microsoft_client_secret,
                    "refresh_token": refresh_token,
                    "scope": " ".join(GRAPH_SCOPES),
                }
            )
        except Exception:
            return None
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600)))
        ).isoformat()
        self.secrets.save(
            "teams",
            stored.external_id,
            stored.display_name,
            data["access_token"],
            {"scope": data.get("scope", ""), "expires_at": expires_at},
            refresh_token=data.get("refresh_token", refresh_token),
            expires_at=expires_at,
        )
        return data["access_token"]

    def _channel_queue(self, token: str) -> list[dict[str, str]]:
        queue: list[dict[str, str]] = []
        for team in self._api("GET", "/me/joinedTeams", token=token).get("value", []):
            channels = self._api("GET", f"/teams/{team.get('id')}/channels", token=token).get(
                "value", []
            )
            for channel in channels:
                queue.append(
                    {
                        "team_id": str(team.get("id") or ""),
                        "team_name": str(team.get("displayName") or ""),
                        "channel_id": str(channel.get("id") or ""),
                        "channel_name": str(channel.get("displayName") or ""),
                    }
                )
        return queue

    def _channel_records(
        self, token: str, channel_info: dict[str, str], cursor: dict[str, Any]
    ) -> list[SyncRecord]:
        team_id = channel_info.get("team_id", "")
        channel_id = channel_info.get("channel_id", "")
        key = f"{team_id}:{channel_id}"
        last_seen = str((cursor.get("last_message") or {}).get(key, ""))
        messages = self._api(
            "GET",
            f"/teams/{team_id}/channels/{channel_id}/messages",
            token=token,
            params={"top": MESSAGES_PER_CHANNEL},
        ).get("value", [])
        records: list[SyncRecord] = []
        newest = last_seen
        for message in messages:
            created = str(
                message.get("lastModifiedDateTime") or message.get("createdDateTime") or ""
            )
            if created <= last_seen:
                continue
            payload = self._message_payload(message)
            if not payload["content"].strip():
                continue
            records.append(
                SyncRecord(
                    id=f"teams-message:{channel_id}:{message.get('id')}",
                    resource_type="channel_message",
                    operation=SyncOperation.UPSERT,
                    version=created,
                    title=f"[{channel_info.get('channel_name', channel_id)}] {payload['author']}: {payload['content'][:80]}",
                    content=payload["content"],
                    source_url=payload["web_url"],
                    updated_at=created,
                    metadata={
                        "team_id": team_id,
                        "team_name": channel_info.get("team_name", ""),
                        "channel_id": channel_id,
                        "channel_name": channel_info.get("channel_name", ""),
                        "user": payload["author"],
                        "timestamp": created,
                    },
                )
            )
            newest = max(newest, created)
        cursor.setdefault("last_message", {})[key] = newest
        return records

    @staticmethod
    def _message_payload(message: dict[str, Any]) -> dict[str, Any]:
        body = message.get("body") or {}
        content = str(body.get("content") or "")
        if str(body.get("contentType") or "").lower() == "html":
            text, _ = html_to_text(content.encode("utf-8"))
            content = text
        author = str(((message.get("from") or {}).get("user") or {}).get("displayName") or "")
        return {
            "content": content.strip(),
            "author": author,
            "web_url": str(message.get("webUrl") or ""),
            "created": str(message.get("createdDateTime") or ""),
        }

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        response = httpx.post(_auth_endpoint("token"), data=form, timeout=30)
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("error_description") or "")
            except Exception:
                detail = response.text[:200]
            raise ValueError(f"Microsoft OAuth failed: {detail}")
        response.raise_for_status()
        return response.json()

    def _api(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = httpx.request(
            method,
            f"{GRAPH_API}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            params=params,
            json=payload,
            timeout=60,
        )
        if response.status_code == 401 and self.secrets.workspace_id:
            stored = self.secrets.account("teams")
            refreshed = self._refresh(stored) if stored else None
            if refreshed:
                response = httpx.request(
                    method,
                    f"{GRAPH_API}{path}",
                    headers={
                        "Authorization": f"Bearer {refreshed}",
                        "Content-Type": "application/json",
                    },
                    params=params,
                    json=payload,
                    timeout=60,
                )
        response.raise_for_status()
        return response.json()
