from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
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
    WebhookSubscription,
)
from app.core.config import settings

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
# One sync job moves at most this many containers before yielding, so a large
# workspace streams through repeated job pages instead of one long request.
PAGES_PER_BATCH = 30
ROWS_PER_DATABASE = 200

_MANIFEST = ConnectorManifest(
    id="notion",
    name="Notion",
    icon="notion",
    version="1.0.0",
    execution_mode=ExecutionMode.CLOUD,
    oauth=OAuthConfig(
        authorization_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        scopes=(),
        pkce_required=False,
    ),
    resources=(
        ConnectorResource("page", "Pages", syncable=True),
        ConnectorResource("database", "Databases and rows", syncable=True),
    ),
    tools=(
        ConnectorTool(
            "search",
            "Search the connected Notion workspace for pages and databases.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "get_page",
            "Read the text content of one Notion page by id.",
            ToolKind.READ,
        ),
    ),
    webhooks=(
        WebhookSubscription("page.content_updated", "Page content changed", "x-notion-signature"),
    ),
    rate_limit=RateLimitPolicy(requests=90, window_seconds=60, burst=3),
    retry=RetryPolicy(max_attempts=7, base_delay_seconds=2, max_delay_seconds=300),
    data_policy=DataPolicy(
        residency="MemoryWorks workspace region",
        retention="Until source disconnect or workspace retention policy",
    ),
    package="orgmemory.connector.notion",
)
NOTION_MANIFEST = replace(_MANIFEST, signature=_MANIFEST.digest())


def _block_text(block: dict[str, Any]) -> tuple[str, str]:
    """Return (kind, text) for one Notion block."""
    for kind, value in block.items():
        if not isinstance(value, dict) or not isinstance(value.get("rich_text"), list):
            continue
        text = "".join(str(part.get("plain_text") or "") for part in value["rich_text"]).strip()
        if not text and kind != "empty":
            continue
        return kind, text
    return "", ""


def _render_blocks(token: str, blocks: list[dict[str, Any]], depth: int = 0) -> list[str]:
    lines: list[str] = []
    indent = "  " * min(depth, 3)
    for block in blocks:
        kind, text = _block_text(block)
        if kind in {"heading_1", "heading_2", "heading_3"}:
            level = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}[kind]
            lines.append(f"{level} {text}")
        elif kind == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif kind == "numbered_list_item":
            lines.append(f"{indent}1. {text}")
        elif kind == "to_do":
            checked = block.get("to_do", {}).get("checked")
            lines.append(f"{indent}- [{'x' if checked else ' '}] {text}")
        elif kind == "quote":
            lines.append(f"> {text}")
        elif kind == "divider":
            lines.append("---")
        elif text:
            lines.append(f"{indent}{text}")
        if block.get("has_children") and depth < 2:
            try:
                child_blocks = list(
                    NotionConnector._api(
                        token,
                        "GET",
                        f"/blocks/{block.get('id')}/children",
                        {"page_size": 100},
                    ).get("results", [])
                )
            except Exception:
                child_blocks = []
            lines.extend(_render_blocks(token, child_blocks, depth + 1))
    return lines


class NotionConnector(Connector):
    manifest = NOTION_MANIFEST

    def __init__(self, secrets: ConnectorSecrets | None = None):
        self.secrets = secrets or ConnectorSecrets()

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.secrets.status(self.manifest.id)

    # -- OAuth ----------------------------------------------------------------

    def oauth_configured(self) -> bool:
        return bool(settings.notion_client_id and settings.notion_client_secret)

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        if not settings.notion_client_id:
            raise ValueError("Notion OAuth is not configured (NOTION_CLIENT_ID)")
        flow = user.get("flow") or user
        params = {
            "client_id": settings.notion_client_id,
            "response_type": "code",
            "owner": "user",
            "state": flow["state"],
            "redirect_uri": flow.get("redirect_uri")
            or f"{settings.api_url.rstrip('/')}/api/connectors/notion/auth/callback",
        }
        return f"{NOTION_API}/oauth/authorize?{urlencode(params)}"

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        if not settings.notion_client_id or not settings.notion_client_secret:
            raise ValueError("Notion OAuth is not configured")
        response = httpx.post(
            f"{NOTION_API}/oauth/token",
            auth=(settings.notion_client_id, settings.notion_client_secret),
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": flow.get("redirect_uri")
                or f"{settings.api_url.rstrip('/')}/api/connectors/notion/auth/callback",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        workspace_name = str(data.get("workspace_name") or "Notion workspace")
        return {
            "token": data["access_token"],
            "external_id": str(data.get("workspace_id") or "notion"),
            "display_name": workspace_name,
            "bot_id": str(data.get("bot_id") or ""),
            "scope": str(data.get("scope") or ""),
        }

    # -- API surface ----------------------------------------------------------

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        token = (account.access_token if account else None) or self._token()
        results = self._api(token, "POST", "/search", {"page_size": 100}).get("results", [])
        return [
            {
                "id": item.get("id"),
                "type": item.get("object"),
                "name": self._title_of(item),
                "url": item.get("url", ""),
            }
            for item in results
        ]

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        token = (account.access_token if account else None) or self._token()
        cursor = dict(cursor or {})
        results = self._api(
            token,
            "POST",
            "/search",
            {
                "page_size": 100,
                **(
                    {"start_cursor": cursor["search_cursor"]} if cursor.get("search_cursor") else {}
                ),
            },
        )
        records: list[SyncRecord] = []
        for item in results.get("results", [])[:PAGES_PER_BATCH]:
            container_id = str(item.get("id") or "")
            if not container_id:
                continue
            container_type = str(item.get("object") or "page")
            title = self._title_of(item)
            if container_type == "database":
                records.extend(self._database_records(token, container_id, title))
            else:
                records.append(
                    SyncRecord(
                        id=f"notion-page:{container_id}",
                        resource_type="page",
                        operation=SyncOperation.UPSERT,
                        version=str(item.get("last_edited_time") or ""),
                        title=title or "Untitled page",
                        content=self.page_content(token, container_id),
                        source_url=str(item.get("url") or ""),
                        updated_at=str(item.get("last_edited_time") or ""),
                        metadata={"notion_id": container_id, "notion_type": "page"},
                    )
                )
        next_cursor = results.get("next_cursor") or ""
        has_more = bool(results.get("has_more") and next_cursor)
        return SyncBatch(
            tuple(records),
            {**cursor, "search_cursor": next_cursor, "synced_at": datetime.now(UTC).isoformat()},
            has_more=has_more,
        )

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        token = (account.access_token if account else None) or self._token()
        results = self._api(token, "POST", "/search", {"query": query, "page_size": 20}).get(
            "results", []
        )
        return [
            {
                "id": item.get("id"),
                "type": item.get("object"),
                "title": self._title_of(item),
                "url": item.get("url", ""),
            }
            for item in results
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
        token = (account.access_token if account else None) or self._token()
        if action == "search":
            return {"results": self.search(account, str(arguments.get("query") or ""))}
        if action == "get_page":
            page_id = str(arguments.get("page_id") or "")
            if not page_id:
                raise ValueError("get_page requires page_id")
            page = self._api(token, "GET", f"/pages/{page_id}", {})
            return {
                "id": page_id,
                "title": self._title_of(page),
                "url": page.get("url", ""),
                "content": self.page_content(token, page_id),
            }
        raise ConnectorCapabilityError(f"Unsupported Notion action {action!r}")

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        if not settings.notion_client_secret:
            raise ValueError("Notion webhook verification is not configured")
        signature = request.headers.get("x-notion-signature", "")
        expected = hmac.new(
            settings.notion_client_secret.encode(), request.body, hashlib.sha256
        ).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature.removeprefix("sha256=")):
            raise ValueError("Invalid Notion webhook signature")
        payload = json.loads(request.body)
        if payload.get("type") == "url_verification":
            return WebhookEvent(
                delivery_id=hashlib.sha256(request.body).hexdigest(),
                event_type="url_verification",
                resource_id="",
                challenge=str(payload.get("verification") or payload.get("challenge") or ""),
            )
        entity = payload.get("entity") or {}
        event_type = str(payload.get("type") or "page.content_updated")
        return WebhookEvent(
            delivery_id=str(payload.get("request_id") or hashlib.sha256(request.body).hexdigest()),
            event_type=event_type,
            resource_id=str(entity.get("id") or ""),
        )

    def revoke(self, account: ConnectorAccount) -> None:
        self.secrets.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        token = (account.access_token if account else None) or self._token()
        if not token:
            return ConnectorHealth(self.manifest.id, "disconnected", datetime.now(UTC).isoformat())
        started = time.monotonic()
        try:
            self._api(token, "GET", "/users/me", {})
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

    # -- internals ------------------------------------------------------------

    def _token(self) -> str:
        token = self.secrets.token("notion")
        if not token:
            raise ValueError("Notion is not connected")
        return token

    @staticmethod
    def _api(token: str, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.request(
            method,
            f"{NOTION_API}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            json=payload if method == "POST" else None,
            timeout=30,
        )
        if response.status_code == 429:
            retry_after = float(response.headers.get("retry-after", "1"))
            raise ConnectorCapabilityError(f"Notion rate limited; retry in {retry_after}s")
        response.raise_for_status()
        return response.json()

    def page_content(self, token: str, page_id: str) -> str:
        lines: list[str] = []
        cursor = ""
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            response = self._api(token, "GET", f"/blocks/{page_id}/children", payload)
            lines.extend(_render_blocks(token, list(response.get("results", []))))
            if not response.get("has_more") or not response.get("next_cursor"):
                break
            cursor = str(response["next_cursor"])
            if len(lines) > 4000:
                lines.append("(content truncated at 4000 blocks)")
                break
        return "\n".join(lines).strip()

    def _database_records(self, token: str, database_id: str, title: str) -> list[SyncRecord]:
        records: list[SyncRecord] = []
        cursor = ""
        rows_seen = 0
        while rows_seen < ROWS_PER_DATABASE:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            response = self._api(token, "POST", f"/databases/{database_id}/query", payload)
            for row in response.get("results", []):
                rows_seen += 1
                if rows_seen > ROWS_PER_DATABASE:
                    break
                row_id = str(row.get("id") or "")
                properties = row.get("properties") or {}
                row_title = ""
                parts: list[str] = []
                for value in properties.values():
                    kind = value.get("type")
                    rich = value.get(kind) if isinstance(value.get(kind), list) else []
                    text = " ".join(
                        str(part.get("plain_text") or "") for part in rich if isinstance(part, dict)
                    ).strip()
                    if kind == "title" and text and not row_title:
                        row_title = text
                    elif text:
                        parts.append(f"{kind}: {text}")
                records.append(
                    SyncRecord(
                        id=f"notion-row:{row_id}",
                        resource_type="database_row",
                        operation=SyncOperation.UPSERT,
                        version=str(row.get("last_edited_time") or ""),
                        title=f"{title} · {row_title or 'Untitled row'}",
                        content="\n".join(parts),
                        source_url=str(row.get("url") or ""),
                        updated_at=str(row.get("last_edited_time") or ""),
                        metadata={
                            "notion_id": row_id,
                            "notion_type": "database_row",
                            "database_id": database_id,
                            "database_title": title,
                        },
                    )
                )
            if not response.get("has_more") or not response.get("next_cursor"):
                break
            cursor = str(response["next_cursor"])
        return records

    @staticmethod
    def _title_of(item: dict[str, Any]) -> str:
        properties = item.get("properties") or {}
        for value in properties.values():
            if value.get("type") == "title":
                parts = value.get("title") or []
                text = "".join(str(part.get("plain_text") or "") for part in parts).strip()
                if text:
                    return text[:300]
        container = item.get("title") or item.get("Name") or []
        if isinstance(container, list):
            text = "".join(str(part.get("plain_text") or "") for part in container).strip()
            if text:
                return text[:300]
        return ""
