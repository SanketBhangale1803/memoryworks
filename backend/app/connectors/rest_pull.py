"""Workspace-defined REST pull connector: ingest any platform with an API.

A workspace admin registers an HTTP JSON endpoint plus a field mapping, and
the platform becomes a first-class sync source: records flow through the same
verified sync engine, deduplication, sanitization, and memory pipeline as
built-in connectors. Credentials supplied as static headers are encrypted
under the workspace scope and are never returned through the API.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx

from app.auth.vault import OAuthTokenVault, configured_cipher
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
    RateLimitPolicy,
    RetryPolicy,
    SyncBatch,
    SyncOperation,
    SyncRecord,
    ToolKind,
    WebhookEvent,
    WebhookRequest,
)
from app.connectors.url_security import validate_remote_connector_url
from app.ingestion.documents import _html_text as html_to_text

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_ITEMS_PER_SYNC = 200
MAX_PAGES = 25

TOOLS = (
    ConnectorTool(
        "fetch",
        "Fetch the latest records from this registered platform API.",
        ToolKind.READ,
    ),
)


def rest_manifest_from_registration(record: dict[str, Any]) -> ConnectorManifest:
    config = json.loads(record["manifest_json"])
    manifest = ConnectorManifest(
        id=record["provider"],
        name=record["name"],
        icon=str(config.get("icon") or "api"),
        version=record["version"],
        execution_mode=ExecutionMode.CLOUD,
        oauth=None,
        resources=(
            ConnectorResource(
                str(config.get("resource_type") or "record"),
                str(record.get("name") or "records"),
                True,
                True,
            ),
        ),
        tools=TOOLS,
        webhooks=(),
        rate_limit=RateLimitPolicy(120, 60, 5),
        retry=RetryPolicy(max_attempts=5, base_delay_seconds=5, max_delay_seconds=300),
        data_policy=DataPolicy(
            residency="MemoryWorks workspace region",
            retention="Until source disconnect or workspace retention policy",
        ),
        package=f"rest:{record['server_url']}",
        signing_key_id=record.get("signing_key_id") or "workspace-attested",
    )
    return replace(manifest, signature=record["manifest_digest"])


class RestPullConnector(Connector):
    def __init__(self, record: dict[str, Any], vault: OAuthTokenVault | None = None):
        self.record = record
        self.secrets = vault
        self.manifest = rest_manifest_from_registration(record)
        config = json.loads(record["manifest_json"])
        self.config = config
        # Re-validated immediately before every request to keep the SSRF
        # window (DNS rebinding) as small as the remote MCP path.
        self.base_url = validate_remote_connector_url(str(config["base_url"]))
        self.workspace_id = str(record.get("workspace_id") or "")

    def connection_statuses(self) -> list[dict[str, Any]]:
        if not self.secrets:
            return []
        return self.secrets.status(self.manifest.id)

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        raise ConnectorCapabilityError(
            "REST source connectors use static headers registered by a workspace admin, "
            "not interactive OAuth"
        )

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        raise ConnectorCapabilityError("REST source connectors do not use OAuth")

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        batch = self.sync(account)
        return [
            {"id": record.id, "title": record.title, "url": record.source_url}
            for record in batch.records[:100]
        ]

    def sync(
        self, account: ConnectorAccount | None, cursor: dict[str, Any] | None = None
    ) -> SyncBatch:
        cursor = dict(cursor or {})
        self._current_url = self.base_url
        items: list[dict[str, Any]] = []
        next_cursor: dict[str, Any] = {**cursor, "page": int(cursor.get("page") or 1)}
        has_more = False
        for _ in range(MAX_PAGES):
            payload, page_has_more = self._fetch_page(next_cursor)
            batch_items = self._extract_items(payload)
            items.extend(batch_items)
            next_cursor["page"] = int(next_cursor.get("page") or 1) + 1
            if not page_has_more or not batch_items or len(items) >= MAX_ITEMS_PER_SYNC:
                has_more = bool(page_has_more and batch_items)
                break
        records = tuple(self._record_from_item(item) for item in items[:MAX_ITEMS_PER_SYNC])
        next_cursor["synced_at"] = datetime.now(UTC).isoformat()
        return SyncBatch(records, next_cursor, has_more=has_more)

    def search(
        self, account: ConnectorAccount | None, query: str, **filters: Any
    ) -> list[dict[str, Any]]:
        needle = query.casefold()
        batch = self.sync(account)
        output = []
        for record in batch.records:
            if needle in record.title.casefold() or needle in record.content.casefold():
                output.append(
                    {
                        "id": record.id,
                        "title": record.title,
                        "url": record.source_url,
                        "snippet": record.content[:300],
                    }
                )
        return output[:25]

    def execute(
        self,
        account: ConnectorAccount | None,
        action: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.manifest.tool(action)
        if action == "fetch":
            batch = self.sync(account)
            return {
                "records": [
                    {
                        "id": record.id,
                        "title": record.title,
                        "content": record.content[:4000],
                        "url": record.source_url,
                    }
                    for record in batch.records[:100]
                ]
            }
        raise ConnectorCapabilityError(f"Unsupported action {action!r}")

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        raise ConnectorCapabilityError(
            "REST source connectors pull on schedule; point the platform's outbound "
            "webhooks at the generic workspace webhook instead"
        )

    def revoke(self, account: ConnectorAccount | None) -> None:
        # The registration row is revoked by ConnectorRuntime.revoke_custom.
        return None

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        started = time.monotonic()
        try:
            self._request(self.base_url)
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

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "MemoryWorksIngest/1.0",
        }
        try:
            oauth_payload = json.loads(self.record.get("oauth_json") or "{}")
        except (ValueError, TypeError):
            oauth_payload = {}
        encrypted = str(oauth_payload.get("headers_encrypted") or "")
        if not encrypted:
            return headers
        cipher = configured_cipher()
        context = {
            "application": "orgmemory",
            "workspace_id": self.workspace_id,
            "user_id": "",
            "provider": self.manifest.id,
        }
        try:
            registered = json.loads(cipher.decrypt(encrypted, context))
        except Exception as exc:
            raise ConnectorCapabilityError(
                "The registered API headers could not be decrypted; re-register the source"
            ) from exc
        headers.update({str(key): str(value) for key, value in registered.items()})
        return headers

    def _request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        validated = validate_remote_connector_url(url)
        response = httpx.get(
            validated,
            headers=self._headers(),
            params=params,
            timeout=60,
            follow_redirects=False,
        )
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ConnectorCapabilityError("Response exceeds the 20MB sync limit")
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            # Be lenient: many platforms omit the content-type but still
            # serve JSON bodies.
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                return {"_text": response.text}
            return data if isinstance(data, dict) else {"_list": data}
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ConnectorCapabilityError(f"Response is not valid JSON: {exc}") from exc
        return data if isinstance(data, dict) else {"_list": data}

    def _fetch_page(self, cursor: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        params: dict[str, Any] = {}
        page_param = str(self.config.get("page_param") or "")
        if page_param:
            params[page_param] = int(cursor.get("page") or 1)
            page_size = int(self.config.get("page_size") or 0)
            if page_size:
                params[self.config.get("page_size_param") or "per_page"] = page_size
            payload = self._request(self._current_url, params or None)
            return payload, bool(self._extract_items(payload))
        payload = self._request(self._current_url, None)
        next_path = str(self.config.get("next_url_path") or "")
        if next_path:
            next_url = str(_resolve_path(payload, next_path) or "")
            if next_url:
                self._current_url = validate_remote_connector_url(next_url)
                return payload, True
        return payload, False

    def _extract_items(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        items_path = str(self.config.get("items_path") or "")
        extracted = _resolve_path(payload, items_path) if items_path else payload
        if isinstance(extracted, dict):
            extracted = extracted.get("_list") or [extracted]
        if not isinstance(extracted, list):
            return []
        return [item for item in extracted if isinstance(item, dict)]

    def _record_from_item(self, item: dict[str, Any]) -> SyncRecord:
        content_fields = self.config.get("content_fields") or ["content", "body", "description"]
        if isinstance(content_fields, str):
            content_fields = [content_fields]
        content = ""
        for field in content_fields:
            value = item.get(field)
            if value is None:
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            if "<" in text and ">" in text:
                with contextlib.suppress(Exception):
                    text, _ = html_to_text(text.encode("utf-8"))
            content = text
            if content.strip():
                break
        if not content:
            content = json.dumps(item)[:4000]
        record_id = str(
            item.get(str(self.config.get("id_field") or "id"))
            or f"item:{abs(hash(json.dumps(item, sort_keys=True))) % 10**12}"
        )
        return SyncRecord(
            id=f"rest:{self.manifest.id}:{record_id}",
            resource_type=str(self.config.get("resource_type") or "record"),
            operation=SyncOperation.UPSERT,
            version=str(item.get(str(self.config.get("updated_field") or "")) or ""),
            title=str(item.get(str(self.config.get("title_field") or "title")) or record_id)[:300],
            content=content,
            source_url=str(item.get(str(self.config.get("url_field") or "url")) or ""),
            updated_at=str(item.get(str(self.config.get("updated_field") or "")) or ""),
            metadata={
                "rest_source": self.manifest.id,
                "record_id": record_id,
            },
        )


def _resolve_path(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current
