from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, uuid5

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
    RiskLevel,
    SyncBatch,
    SyncOperation,
    SyncRecord,
    ToolKind,
    WebhookEvent,
    WebhookRequest,
    WebhookSubscription,
)
from app.core.config import settings

_SLACK_MANIFEST = ConnectorManifest(
    id="slack",
    name="Slack",
    icon="slack",
    version="1.0.0",
    execution_mode=ExecutionMode.CLOUD,
    oauth=OAuthConfig(
        authorization_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        revoke_url="https://slack.com/api/auth.revoke",
        scopes=(
            "channels:history",
            "groups:history",
            "channels:read",
            "groups:read",
            "chat:write",
        ),
        pkce_required=False,
    ),
    resources=(
        ConnectorResource("channel", "Channels"),
        ConnectorResource("message", "Messages and threads"),
    ),
    tools=(
        ConnectorTool(
            "list_channels",
            "List channels visible to the delegated Slack user.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "get_channel_history",
            "Read messages from a channel visible to the delegated Slack user.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "send_message",
            "Send a message as the delegated Slack user after explicit approval.",
            ToolKind.WRITE,
            RiskLevel.HIGH,
            idempotency_required=True,
            approval_required=True,
        ),
    ),
    webhooks=(
        WebhookSubscription("message", "Message created", "x-slack-signature"),
        WebhookSubscription("message_changed", "Message changed", "x-slack-signature"),
        WebhookSubscription("message_deleted", "Message deleted", "x-slack-signature"),
    ),
    rate_limit=RateLimitPolicy(requests=45, window_seconds=60, burst=5),
    retry=RetryPolicy(max_attempts=7, base_delay_seconds=2, max_delay_seconds=300),
    data_policy=DataPolicy(
        residency="MemoryWorks workspace region",
        retention="Until source disconnect or workspace retention policy",
    ),
    package="orgmemory.connector.slack",
)
SLACK_MANIFEST = replace(_SLACK_MANIFEST, signature=_SLACK_MANIFEST.digest())


class SlackConnector(Connector):
    manifest = SLACK_MANIFEST

    def __init__(self, secrets: ConnectorSecrets | None = None):
        self.secrets = secrets or ConnectorSecrets()

    def token(self) -> str | None:
        return self.secrets.token("slack") or (
            settings.slack_bot_token if not self.secrets.workspace_id else None
        )

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.secrets.status(self.manifest.id)

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        return self.oauth_url(
            user.get("flow") or user,
            scopes=scopes or list(self.manifest.oauth.scopes if self.manifest.oauth else ()),
        )

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        return self.complete_oauth(code, flow)

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        return self.list_channels()

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        cursor = dict(cursor or {})
        channel_id = str(cursor.get("channel_id") or cursor.get("resource_id") or "")
        if not channel_id:
            return SyncBatch((), {**cursor, "synced_at": datetime.now(UTC).isoformat()})
        channel, messages = self.history(channel_id, int(cursor.get("limit") or 200))
        last_timestamp = str(cursor.get("last_timestamp") or "")
        fresh = [
            message
            for message in messages
            if str(message.get("ts") or "") > last_timestamp
            and str(message.get("text") or "").strip()
        ]
        channel_name = str(channel.get("name") or channel_id)
        records = tuple(
            SyncRecord(
                id=f"slack-message:{channel_id}:{message.get('ts')}",
                resource_type="message",
                operation=SyncOperation.UPSERT,
                version=str(message.get("edited", {}).get("ts") or message.get("ts") or ""),
                title=f"#{channel_name} at {message.get('ts')}",
                content=str(message.get("text") or ""),
                source_url=self.permalink(channel_id, str(message.get("ts") or "")),
                updated_at=str(message.get("edited", {}).get("ts") or message.get("ts") or ""),
                metadata={
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "user": message.get("user", ""),
                    "timestamp": message.get("ts", ""),
                    "thread_ts": message.get("thread_ts", ""),
                },
            )
            for message in sorted(fresh, key=lambda item: str(item.get("ts") or ""))
        )
        newest = max((str(message.get("ts") or "") for message in messages), default=last_timestamp)
        return SyncBatch(
            records,
            {
                **cursor,
                "channel_id": channel_id,
                "last_timestamp": newest,
                "synced_at": datetime.now(UTC).isoformat(),
            },
        )

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        channel_id = str(filters.get("channel_id") or "")
        if not channel_id:
            raise ConnectorCapabilityError("Slack search requires a channel_id")
        _, messages = self.history(channel_id, int(filters.get("limit") or 200))
        needle = query.casefold()
        return [
            message for message in messages if needle in str(message.get("text") or "").casefold()
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
        if action == "list_channels":
            return {"channels": self.list_channels()}
        if action == "get_channel_history":
            channel, messages = self.history(
                str(arguments.get("channel_id") or ""), int(arguments.get("limit") or 100)
            )
            return {"channel": channel, "messages": messages}
        if action == "send_message":
            return self.post_message(
                str(arguments.get("channel_id") or ""),
                str(arguments.get("text") or ""),
                idempotency_key=idempotency_key,
            )
        raise ConnectorCapabilityError(f"Unsupported Slack action {action!r}")

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        if not settings.slack_signing_secret:
            raise ValueError("Slack webhook verification is not configured")
        timestamp_text = request.headers.get("x-slack-request-timestamp", "")
        try:
            timestamp = int(timestamp_text)
        except ValueError as exc:
            raise ValueError("Invalid Slack request timestamp") from exc
        if abs(int(time.time()) - timestamp) > 300:
            raise ValueError("Stale Slack webhook request")
        expected = (
            "v0="
            + hmac.new(
                settings.slack_signing_secret.encode(),
                f"v0:{timestamp_text}:".encode() + request.body,
                hashlib.sha256,
            ).hexdigest()
        )
        signature = request.headers.get("x-slack-signature", "")
        if not signature or not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid Slack webhook signature")
        payload = json.loads(request.body)
        if payload.get("type") == "url_verification":
            return WebhookEvent(
                delivery_id=hashlib.sha256(request.body).hexdigest(),
                event_type="url_verification",
                resource_id="",
                challenge=str(payload.get("challenge") or ""),
            )
        event = payload.get("event") or {}
        subtype = str(event.get("subtype") or "message")
        message = event.get("message") if subtype == "message_changed" else event
        previous = event.get("previous_message") or {}
        channel_id = str(event.get("channel") or "")
        ts = str(
            (message or {}).get("ts")
            or previous.get("ts")
            or event.get("deleted_ts")
            or event.get("ts")
            or ""
        )
        delivery_id = str(payload.get("event_id") or hashlib.sha256(request.body).hexdigest())
        if not channel_id or not ts:
            return WebhookEvent(delivery_id, subtype, channel_id)
        operation = SyncOperation.DELETE if subtype == "message_deleted" else SyncOperation.UPSERT
        record = SyncRecord(
            id=f"slack-message:{channel_id}:{ts}",
            resource_type="message",
            operation=operation,
            version=str(event.get("event_ts") or ts),
            title=f"Slack #{channel_id} at {ts}",
            content=str((message or {}).get("text") or ""),
            updated_at=str(event.get("event_ts") or ts),
            metadata={
                "channel_id": channel_id,
                "channel_name": channel_id,
                "user": (message or {}).get("user", ""),
                "timestamp": ts,
            },
        )
        return WebhookEvent(delivery_id, subtype, channel_id, (record,))

    def revoke(self, account: ConnectorAccount) -> None:
        self.secrets.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        if not self.token():
            return ConnectorHealth(self.manifest.id, "disconnected", datetime.now(UTC).isoformat())
        started = time.monotonic()
        try:
            self._api("auth.test", {})
            return ConnectorHealth(
                self.manifest.id,
                "healthy",
                datetime.now(UTC).isoformat(),
                int((time.monotonic() - started) * 1_000),
            )
        except Exception as exc:
            return ConnectorHealth(
                self.manifest.id,
                "degraded",
                datetime.now(UTC).isoformat(),
                int((time.monotonic() - started) * 1_000),
                str(exc),
            )

    def oauth_url(self, flow: dict[str, str], scopes: list[str] | None = None) -> str:
        if not settings.slack_client_id:
            raise ValueError("Slack OAuth is not configured")
        return "https://slack.com/oauth/v2/authorize?" + urlencode(
            {
                "client_id": settings.slack_client_id,
                # A user token can read the public and private conversations
                # the connecting person can access. This is the correct
                # boundary for personal company memory; a bot token remains a
                # fallback for existing workspace installations.
                "user_scope": ",".join(
                    scopes or list(self.manifest.oauth.scopes if self.manifest.oauth else ())
                ),
                "redirect_uri": settings.slack_redirect_uri,
                "state": flow["state"],
            }
        )

    def complete_oauth(self, code: str, flow: dict) -> dict:
        response = httpx.post(
            "https://slack.com/api/oauth.v2.access",
            auth=(settings.slack_client_id, settings.slack_client_secret),
            data={"code": code, "redirect_uri": settings.slack_redirect_uri},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError(f"Slack OAuth failed: {data.get('error')}")
        team = data["team"]
        authed_user = data.get("authed_user") or {}
        token = authed_user.get("access_token") or data.get("access_token")
        if not token:
            raise ValueError("Slack OAuth did not return a user or bot access token")
        return {
            "token": token,
            "external_id": team["id"],
            "display_name": team["name"],
            "scope": authed_user.get("scope") or data.get("scope", ""),
            "slack_user_id": authed_user.get("id", ""),
            "token_type": authed_user.get("token_type") or data.get("token_type", ""),
        }

    def list_channels(self) -> list[dict[str, Any]]:
        return self._api(
            "conversations.list",
            {"types": "public_channel,private_channel", "limit": 200},
        ).get("channels", [])

    def history(self, channel_id: str, limit: int) -> tuple[dict, list[dict]]:
        info = self._api("conversations.info", {"channel": channel_id})["channel"]
        roots = self._api(
            "conversations.history", {"channel": channel_id, "limit": min(limit, 200)}
        ).get("messages", [])
        messages: list[dict] = []
        for root in roots:
            if root.get("reply_count"):
                messages.extend(
                    self._api(
                        "conversations.replies",
                        {"channel": channel_id, "ts": root["ts"], "limit": 200},
                    ).get("messages", [])
                )
            else:
                messages.append(root)
        return info, messages

    def permalink(self, channel_id: str, ts: str) -> str:
        try:
            return self._api("chat.getPermalink", {"channel": channel_id, "message_ts": ts})[
                "permalink"
            ]
        except Exception:
            return f"slack://{channel_id}/{ts}"

    def post_message(
        self, channel_id: str, text: str, *, idempotency_key: str = ""
    ) -> dict[str, Any]:
        channel_id = channel_id.strip()
        text = text.strip()
        if not channel_id:
            raise ValueError("Choose a Slack channel before posting")
        if not text:
            raise ValueError("The Slack message is empty")
        if len(text) > 35_000:
            raise ValueError("The Slack message is too long")
        payload = self._api(
            "chat.postMessage",
            {
                "channel": channel_id,
                "text": text,
                "mrkdwn": "true",
                "unfurl_links": "false",
                "unfurl_media": "false",
                **(
                    {"client_msg_id": str(uuid5(NAMESPACE_URL, idempotency_key))}
                    if idempotency_key
                    else {}
                ),
            },
        )
        ts = str(payload.get("ts") or "")
        channel = str(payload.get("channel") or channel_id)
        return {
            "channel_id": channel,
            "message_ts": ts,
            "message": text,
            "source_url": self.permalink(channel, ts) if ts else "",
        }

    def _api(self, method: str, data: dict):
        token = self.token()
        if not token:
            raise ValueError("Slack is not connected")
        response = httpx.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            data=data,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            if error == "missing_scope":
                raise ValueError(
                    "Slack needs permission to post messages. Reconnect Slack and approve "
                    "the chat:write permission, then retry."
                )
            if error in {"not_in_channel", "channel_not_found"}:
                raise ValueError(
                    "MemoryWorks cannot post to that Slack channel. Choose a channel the "
                    "connected Slack identity can access."
                )
            raise ValueError(f"Slack API error: {error}")
        return payload
