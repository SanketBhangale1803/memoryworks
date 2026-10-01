from __future__ import annotations

import base64
import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal


class ExecutionMode(StrEnum):
    CLOUD = "cloud"
    LOCAL = "local"
    HYBRID = "hybrid"


class ToolKind(StrEnum):
    READ = "read"
    WRITE = "write"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SyncOperation(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(frozen=True)
class OAuthConfig:
    authorization_url: str
    token_url: str
    scopes: tuple[str, ...]
    pkce_required: bool = True
    dynamic_client_registration: bool = False
    revoke_url: str = ""


@dataclass(frozen=True)
class ConnectorResource:
    type: str
    label: str
    searchable: bool = True
    syncable: bool = True


@dataclass(frozen=True)
class ConnectorTool:
    name: str
    description: str
    kind: ToolKind
    risk_level: RiskLevel = RiskLevel.LOW
    input_schema: dict[str, Any] = field(default_factory=dict)
    idempotency_required: bool = False
    approval_required: bool = False

    def __post_init__(self) -> None:
        if self.kind == ToolKind.WRITE and (
            not self.idempotency_required or not self.approval_required
        ):
            raise ValueError(
                f"Write tool {self.name!r} must require approval and an idempotency key"
            )


@dataclass(frozen=True)
class WebhookSubscription:
    event: str
    description: str
    signature_header: str


@dataclass(frozen=True)
class RateLimitPolicy:
    requests: int
    window_seconds: int
    burst: int = 1


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: int = 2
    max_delay_seconds: int = 300
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504)


@dataclass(frozen=True)
class DataPolicy:
    residency: str
    retention: str
    stores_source_content: bool = True
    content_is_untrusted: bool = True


@dataclass(frozen=True)
class ConnectorManifest:
    id: str
    name: str
    icon: str
    version: str
    execution_mode: ExecutionMode
    oauth: OAuthConfig | None
    resources: tuple[ConnectorResource, ...]
    tools: tuple[ConnectorTool, ...]
    webhooks: tuple[WebhookSubscription, ...]
    rate_limit: RateLimitPolicy
    retry: RetryPolicy
    data_policy: DataPolicy
    package: str = ""
    signature: str = ""
    signing_key_id: str = ""
    signature_algorithm: Literal["builtin-sha256", "ed25519"] = "builtin-sha256"

    def tool(self, name: str) -> ConnectorTool:
        tool = next((item for item in self.tools if item.name == name), None)
        if not tool:
            raise ConnectorCapabilityError(f"Connector {self.id!r} has no tool {name!r}")
        return tool

    def unsigned_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("signature", None)
        return payload

    def digest(self) -> str:
        canonical = json.dumps(
            self.unsigned_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def verify_signature(self, public_key: str = "") -> bool:
        """Verify a pinned package manifest.

        Built-ins pin the canonical digest in source. Third-party packages use
        an Ed25519 public key supplied by the workspace trust store.
        """
        if self.signature_algorithm == "builtin-sha256":
            return bool(self.signature) and self.signature == self.digest()
        if self.signature_algorithm != "ed25519" or not public_key or not self.signature:
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
            key.verify(
                base64.b64decode(self.signature),
                json.dumps(self.unsigned_payload(), sort_keys=True, separators=(",", ":")).encode(),
            )
            return True
        except (ValueError, TypeError):
            return False

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["verified"] = bool(self.signature)
        return payload


@dataclass(frozen=True)
class ConnectorAccount:
    id: str
    workspace_id: str
    user_id: str
    provider: str
    external_id: str
    display_name: str
    access_token: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorStatus:
    provider: str
    available: bool
    connected: bool
    accounts: list[dict[str, Any]]
    version: str = ""
    execution_mode: str = "cloud"
    revoked: bool = False


@dataclass(frozen=True)
class ConnectorHealth:
    provider: str
    status: Literal["healthy", "degraded", "unavailable", "disconnected"]
    checked_at: str
    latency_ms: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class SyncRecord:
    id: str
    resource_type: str
    operation: SyncOperation
    version: str
    title: str = ""
    content: str = ""
    source_url: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # Connector output is always data. Neither the sync engine nor an LLM may
    # reinterpret it as system/developer instructions.
    trust: Literal["untrusted_connector_data"] = "untrusted_connector_data"


@dataclass(frozen=True)
class SyncBatch:
    records: tuple[SyncRecord, ...]
    next_cursor: dict[str, Any]
    has_more: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class WebhookRequest:
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class WebhookEvent:
    delivery_id: str
    event_type: str
    resource_id: str
    records: tuple[SyncRecord, ...] = ()
    cursor: dict[str, Any] = field(default_factory=dict)
    challenge: str = ""


class ConnectorError(RuntimeError):
    pass


class ConnectorCapabilityError(ConnectorError):
    pass


class ConnectorAuthError(ConnectorError):
    pass


class Connector(ABC):
    """Contract implemented by built-in and separately installed connectors."""

    manifest: ConnectorManifest

    @abstractmethod
    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str: ...

    @abstractmethod
    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def sync(
        self, account: ConnectorAccount, cursor: dict[str, Any] | None = None
    ) -> SyncBatch: ...

    @abstractmethod
    def search(
        self, account: ConnectorAccount, query: str, **filters: Any
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def execute(
        self,
        account: ConnectorAccount,
        action: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent: ...

    @abstractmethod
    def revoke(self, account: ConnectorAccount) -> None: ...

    @abstractmethod
    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth: ...

    def oauth_configured(self) -> bool:
        """Whether this deployment can start the connector's authorization.

        OAuth connectors override this to require their client credentials, so
        an unconfigured provider reads as needing setup instead of offering a
        Connect button that fails.
        """
        return True

    def status(self) -> ConnectorStatus:
        """Compatibility view used by existing clients during the SDK migration."""
        accounts = self.connection_statuses()
        return ConnectorStatus(
            provider=self.manifest.id,
            available=self.oauth_configured(),
            connected=any(item.get("status") == "connected" for item in accounts),
            accounts=accounts,
            version=self.manifest.version,
            execution_mode=self.manifest.execution_mode.value,
        )

    def connection_statuses(self) -> list[dict[str, Any]]:
        return []
