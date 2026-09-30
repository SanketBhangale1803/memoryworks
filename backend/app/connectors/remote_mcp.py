from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.auth.vault import OAuthTokenVault
from app.core.config import settings

from .base import (
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
)
from .url_security import validate_remote_connector_url


def manifest_from_registration(record: dict[str, Any]) -> ConnectorManifest:
    payload = json.loads(record["manifest_json"])
    oauth_payload = payload.get("oauth") or json.loads(record.get("oauth_json") or "{}")
    oauth = (
        OAuthConfig(
            authorization_url=str(oauth_payload.get("authorization_url") or ""),
            token_url=str(oauth_payload.get("token_url") or ""),
            scopes=tuple(oauth_payload.get("scopes") or ()),
            pkce_required=bool(oauth_payload.get("pkce_required", True)),
            dynamic_client_registration=bool(
                oauth_payload.get("dynamic_client_registration", False)
            ),
            revoke_url=str(oauth_payload.get("revoke_url") or ""),
        )
        if oauth_payload
        else None
    )
    tools = []
    for item in payload.get("tools") or []:
        kind = ToolKind(str(item.get("kind") or "read"))
        tools.append(
            ConnectorTool(
                name=str(item["name"]),
                description=str(item.get("description") or item["name"]),
                kind=kind,
                risk_level=RiskLevel(str(item.get("risk_level") or "low")),
                input_schema=dict(item.get("input_schema") or {}),
                idempotency_required=kind == ToolKind.WRITE,
                approval_required=kind == ToolKind.WRITE,
            )
        )
    manifest = ConnectorManifest(
        id=record["provider"],
        name=record["name"],
        icon=str(payload.get("icon") or "plug"),
        version=record["version"],
        execution_mode=ExecutionMode.CLOUD,
        oauth=oauth,
        resources=tuple(
            ConnectorResource(
                str(item["type"]),
                str(item.get("label") or item["type"]),
                bool(item.get("searchable", True)),
                bool(item.get("syncable", False)),
            )
            for item in payload.get("resources") or []
        ),
        tools=tuple(tools),
        webhooks=(),
        rate_limit=RateLimitPolicy(
            int((payload.get("rate_limit") or {}).get("requests") or 60),
            int((payload.get("rate_limit") or {}).get("window_seconds") or 60),
            int((payload.get("rate_limit") or {}).get("burst") or 5),
        ),
        retry=RetryPolicy(),
        data_policy=DataPolicy(
            residency=str((payload.get("data_policy") or {}).get("residency") or "External"),
            retention=str(
                (payload.get("data_policy") or {}).get("retention")
                or "Controlled by the external MCP server"
            ),
        ),
        package=f"remote-mcp:{record['server_url']}",
        signing_key_id=record.get("signing_key_id") or "workspace-attested",
    )
    return replace(manifest, signature=record["manifest_digest"])


class RemoteMCPConnector(Connector):
    def __init__(self, record: dict[str, Any], vault: OAuthTokenVault):
        self.record = record
        self.secrets = vault
        self.manifest = manifest_from_registration(record)
        self.server_url = validate_remote_connector_url(record["server_url"])
        self._session_id = ""
        self._initialized = False

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.secrets.status(self.manifest.id)

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        if not self.manifest.oauth:
            raise ValueError("This remote MCP server has no OAuth configuration")
        flow = user.get("flow") or user
        client_id = str(json.loads(self.record.get("oauth_json") or "{}").get("client_id") or "")
        if not client_id:
            raise ValueError("The remote MCP registration requires an OAuth client_id")
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": f"{settings.api_url.rstrip('/')}/api/connectors/{self.manifest.id}/auth/callback",
            "scope": " ".join(scopes),
            "state": flow["state"],
        }
        if flow.get("code_challenge"):
            params.update(
                {"code_challenge": flow["code_challenge"], "code_challenge_method": "S256"}
            )
        return self.manifest.oauth.authorization_url + "?" + urlencode(params)

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        if not self.manifest.oauth:
            raise ValueError("This remote MCP server has no OAuth configuration")
        config = json.loads(self.record.get("oauth_json") or "{}")
        response = httpx.post(
            self.manifest.oauth.token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": config.get("client_id", ""),
                "code": code,
                "redirect_uri": f"{settings.api_url.rstrip('/')}/api/connectors/{self.manifest.id}/auth/callback",
                "code_verifier": flow.get("code_verifier", ""),
            },
            timeout=30,
        )
        response.raise_for_status()
        token = response.json()
        return {
            "external_id": flow["user_id"],
            "display_name": self.manifest.name,
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token", ""),
            "scope": token.get("scope", " ".join(self.manifest.oauth.scopes)),
        }

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        result = self._rpc(account, "tools/list", {})
        return list(result.get("tools") or [])

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        sync_tool = next(
            (tool for tool in self.manifest.tools if tool.name in {"sync", "sync_incremental"}),
            None,
        )
        if not sync_tool:
            return SyncBatch((), dict(cursor or {}))
        payload = self._call_tool(account, sync_tool.name, {"cursor": cursor or {}})
        records = tuple(
            SyncRecord(
                id=str(item["id"]),
                resource_type=str(item.get("resource_type") or "remote"),
                operation=SyncOperation(str(item.get("operation") or "upsert")),
                version=str(item.get("version") or ""),
                title=str(item.get("title") or ""),
                content=str(item.get("content") or ""),
                source_url=str(item.get("source_url") or ""),
                updated_at=str(item.get("updated_at") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload.get("records") or []
        )
        return SyncBatch(
            records,
            dict(payload.get("next_cursor") or cursor or {}),
            bool(payload.get("has_more")),
            payload.get("retry_after_seconds"),
        )

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        tool = next(
            (
                item
                for item in self.manifest.tools
                if item.kind == ToolKind.READ and "search" in item.name
            ),
            None,
        )
        if not tool:
            raise ConnectorCapabilityError("Remote MCP connector has no search tool")
        result = self._call_tool(account, tool.name, {"query": query, **filters})
        return list(result.get("items") or result.get("results") or [])

    def execute(
        self,
        account: ConnectorAccount,
        action: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        tool = self.manifest.tool(action)
        payload = dict(arguments)
        if tool.kind == ToolKind.WRITE:
            payload["idempotency_key"] = idempotency_key
        return self._call_tool(account, action, payload)

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        raise ConnectorCapabilityError("Remote MCP connectors do not receive provider webhooks")

    def revoke(self, account: ConnectorAccount) -> None:
        self.secrets.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        started = time.monotonic()
        try:
            self._rpc(account, "tools/list", {})
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

    def _call_tool(
        self, account: ConnectorAccount, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        result = self._rpc(account, "tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or []
        text = next((item.get("text") for item in content if item.get("type") == "text"), "")
        if not text:
            return result.get("structuredContent") or result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text, "_meta": {"trust": "untrusted_connector_data"}}

    def _rpc(
        self, account: ConnectorAccount | None, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        # Re-resolve immediately before every request. This narrows the DNS
        # rebinding window and also catches a hostname that later moves private.
        validate_remote_connector_url(self.server_url)
        if not self._initialized and method != "initialize":
            self._initialize(account)
        return self._request(account, method, params)

    def _initialize(self, account: ConnectorAccount | None) -> None:
        self._request(
            account,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "MemoryWorks Connector Gateway", "version": "1.0.0"},
            },
        )
        self._initialized = True
        self._request(account, "notifications/initialized", {}, notification=True)

    def _request(
        self,
        account: ConnectorAccount | None,
        method: str,
        params: dict[str, Any],
        *,
        notification: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if account:
            headers["Authorization"] = f"Bearer {account.access_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request_payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if not notification:
            request_payload["id"] = 1
        response = httpx.post(
            self.server_url,
            headers=headers,
            json=request_payload,
            timeout=60,
            follow_redirects=False,
        )
        response.raise_for_status()
        self._session_id = response.headers.get("mcp-session-id", self._session_id)
        if notification or not response.content:
            return {}
        if "text/event-stream" in response.headers.get("content-type", ""):
            data_lines = [
                line.removeprefix("data:").strip()
                for line in response.text.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                raise ConnectorCapabilityError("Remote MCP server returned an empty event stream")
            payload = json.loads(data_lines[-1])
        else:
            payload = response.json()
        if payload.get("error"):
            raise ConnectorCapabilityError(str(payload["error"]))
        return dict(payload.get("result") or {})
