from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.connectors.base import ConnectorAccount
from app.core.config import settings
from app.core.database import connect, new_id, rows, utcnow

logger = logging.getLogger(__name__)


class VaultCipher(Protocol):
    provider: str

    def encrypt(self, value: str, context: dict[str, str]) -> str: ...

    def decrypt(self, value: str, context: dict[str, str]) -> str: ...


class LocalFernetCipher:
    """Development-only cipher preserving the existing local setup path."""

    provider = "local-fernet"

    def __init__(self, key: bytes | None = None):
        key_path = settings.sqlite_path.parent / ".connector_key"
        if key is None and settings.integration_encryption_key:
            key = settings.integration_encryption_key.encode()
        if key is None and key_path.exists():
            key = key_path.read_bytes().strip()
        if key is None:
            logger.warning(
                "INTEGRATION_ENCRYPTION_KEY is not set; generated %s. Connector grants "
                "encrypted here cannot be read by any process that does not share this file.",
                key_path,
            )
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            key_path.chmod(0o600)
        self.cipher = Fernet(key)

    def encrypt(self, value: str, context: dict[str, str]) -> str:
        ciphertext = self.cipher.encrypt(value.encode()).decode()
        return json.dumps({"v": 1, "provider": self.provider, "ciphertext": ciphertext})

    def decrypt(self, value: str, context: dict[str, str]) -> str:
        try:
            envelope = json.loads(value)
            ciphertext = envelope["ciphertext"]
        except (json.JSONDecodeError, KeyError, TypeError):
            # Read grants created by the pre-vault Fernet implementation.
            ciphertext = value
        return self.cipher.decrypt(ciphertext.encode()).decode()


class AWSKMSCipher:
    """AWS KMS envelope encryption with tenant/user/provider context.

    boto3 is imported lazily so local development does not need cloud SDK
    credentials. KMS protects a per-value AES-256 data key; this avoids KMS's
    direct-encryption size limit for approved action arguments. Ciphertext is
    never decryptable without the same encryption context.
    """

    provider = "aws-kms"

    def __init__(self, key_id: str, region: str = ""):
        if not key_id:
            raise RuntimeError("CONNECTOR_KMS_KEY_ID is required for the AWS KMS vault")
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on production image
            raise RuntimeError("Install boto3 to use CONNECTOR_VAULT_PROVIDER=aws-kms") from exc
        self.key_id = key_id
        self.client = boto3.client("kms", region_name=region or None)

    def encrypt(self, value: str, context: dict[str, str]) -> str:
        response = self.client.generate_data_key(
            KeyId=self.key_id,
            KeySpec="AES_256",
            EncryptionContext=context,
        )
        nonce = os.urandom(12)
        ciphertext = AESGCM(response["Plaintext"]).encrypt(
            nonce,
            value.encode(),
            json.dumps(context, sort_keys=True, separators=(",", ":")).encode(),
        )
        return json.dumps(
            {
                "v": 2,
                "provider": self.provider,
                "key_id": self.key_id,
                "encrypted_data_key": base64.b64encode(response["CiphertextBlob"]).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            }
        )

    def decrypt(self, value: str, context: dict[str, str]) -> str:
        envelope = json.loads(value)
        if envelope.get("v") == 1:
            # Backward compatibility for the short-token direct-KMS envelope.
            response = self.client.decrypt(
                CiphertextBlob=base64.b64decode(envelope["ciphertext"]),
                EncryptionContext=context,
            )
            return response["Plaintext"].decode()
        response = self.client.decrypt(
            CiphertextBlob=base64.b64decode(envelope["encrypted_data_key"]),
            EncryptionContext=context,
        )
        plaintext = AESGCM(response["Plaintext"]).decrypt(
            base64.b64decode(envelope["nonce"]),
            base64.b64decode(envelope["ciphertext"]),
            json.dumps(context, sort_keys=True, separators=(",", ":")).encode(),
        )
        return plaintext.decode()


class OCIKMSCipher:
    """OCI Vault envelope encryption using a delegated instance identity.

    The VM never stores a master key. OCI Vault wraps a random AES-256 data key
    and enforces the workspace/user/provider tuple as authenticated associated
    data. The data key then encrypts the OAuth grant locally, keeping large
    approved-action payloads below OCI KMS direct-encryption limits.
    """

    provider = "oci-kms"

    def __init__(
        self,
        key_id: str,
        crypto_endpoint: str,
        auth: str = "instance-principal",
        config_profile: str = "DEFAULT",
    ):
        if not key_id:
            raise RuntimeError("CONNECTOR_KMS_KEY_ID is required for the OCI KMS vault")
        if not crypto_endpoint.startswith("https://"):
            raise RuntimeError(
                "CONNECTOR_OCI_KMS_CRYPTO_ENDPOINT must be the HTTPS crypto endpoint"
            )
        try:
            import oci
        except ImportError as exc:  # pragma: no cover - depends on production image
            raise RuntimeError("Install oci to use CONNECTOR_VAULT_PROVIDER=oci-kms") from exc

        self.oci = oci
        self.key_id = key_id
        auth_mode = auth.casefold()
        if auth_mode == "instance-principal":
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            self.client = oci.key_management.KmsCryptoClient(
                config={},
                service_endpoint=crypto_endpoint,
                signer=signer,
                retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
            )
        elif auth_mode == "config-file":
            config = oci.config.from_file(profile_name=config_profile)
            self.client = oci.key_management.KmsCryptoClient(
                config=config,
                service_endpoint=crypto_endpoint,
                retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
            )
        else:
            raise RuntimeError("CONNECTOR_OCI_KMS_AUTH must be instance-principal or config-file")

    @staticmethod
    def _aad(context: dict[str, str]) -> bytes:
        return json.dumps(context, sort_keys=True, separators=(",", ":")).encode()

    def encrypt(self, value: str, context: dict[str, str]) -> str:
        data_key = os.urandom(32)
        wrapped = self.client.encrypt(
            self.oci.key_management.models.EncryptDataDetails(
                key_id=self.key_id,
                plaintext=base64.b64encode(data_key).decode(),
                associated_data=context,
                encryption_algorithm="AES_256_GCM",
                logging_context=context,
            )
        ).data
        nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(nonce, value.encode(), self._aad(context))
        return json.dumps(
            {
                "v": 2,
                "provider": self.provider,
                "key_id": self.key_id,
                "key_version_id": wrapped.key_version_id,
                "encrypted_data_key": wrapped.ciphertext,
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            }
        )

    def decrypt(self, value: str, context: dict[str, str]) -> str:
        envelope = json.loads(value)
        response = self.client.decrypt(
            self.oci.key_management.models.DecryptDataDetails(
                key_id=envelope.get("key_id") or self.key_id,
                key_version_id=envelope.get("key_version_id"),
                ciphertext=envelope["encrypted_data_key"],
                associated_data=context,
                encryption_algorithm="AES_256_GCM",
                logging_context=context,
            )
        ).data
        data_key = base64.b64decode(response.plaintext)
        plaintext = AESGCM(data_key).decrypt(
            base64.b64decode(envelope["nonce"]),
            base64.b64decode(envelope["ciphertext"]),
            self._aad(context),
        )
        return plaintext.decode()


def configured_cipher() -> VaultCipher:
    provider = settings.connector_vault_provider.casefold()
    if provider == "aws-kms":
        return AWSKMSCipher(settings.connector_kms_key_id, settings.connector_kms_region)
    if provider == "oci-kms":
        return OCIKMSCipher(
            settings.connector_kms_key_id,
            settings.connector_oci_kms_crypto_endpoint,
            settings.connector_oci_kms_auth,
            settings.connector_oci_config_profile,
        )
    if provider != "local":
        raise RuntimeError(f"Unsupported connector vault provider {provider!r}")
    return LocalFernetCipher()


@dataclass(frozen=True)
class OAuthGrant:
    id: str
    workspace_id: str
    user_id: str
    provider: str
    external_id: str
    display_name: str
    scopes: tuple[str, ...]
    metadata: dict[str, Any]


class OAuthTokenVault:
    """Per-user delegated OAuth grant storage backed by a configurable KMS."""

    def __init__(
        self,
        workspace_id: str = "",
        user_id: str = "",
        cipher: VaultCipher | None = None,
    ):
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.cipher = cipher or configured_cipher()

    def _context(self, provider: str, user_id: str | None = None) -> dict[str, str]:
        return {
            "application": "orgmemory",
            "workspace_id": self.workspace_id,
            "user_id": user_id or self.user_id,
            "provider": provider,
        }

    def save(
        self,
        provider: str,
        external_id: str,
        display_name: str,
        access_token: str,
        metadata: dict[str, Any] | None = None,
        *,
        refresh_token: str = "",
        expires_at: str | None = None,
        scopes: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        if not self.workspace_id or not self.user_id:
            raise ValueError("OAuth grants require an active workspace and delegated user")
        metadata = dict(metadata or {})
        if scopes is None:
            raw_scopes = metadata.get("scope") or metadata.get("scopes") or []
            scopes = (
                [item for item in str(raw_scopes).replace(",", " ").split() if item]
                if isinstance(raw_scopes, str)
                else list(raw_scopes)
            )
        grant_id, now = new_id("grant"), utcnow()
        context = self._context(provider)
        encrypted_access = self.cipher.encrypt(access_token, context)
        encrypted_refresh = self.cipher.encrypt(refresh_token, context) if refresh_token else ""
        with connect() as conn:
            conn.execute(
                """INSERT INTO oauth_token_grants
                (id,workspace_id,user_id,provider,external_id,display_name,status,
                 access_token_encrypted,refresh_token_encrypted,token_expires_at,
                 scopes_json,metadata_json,created_at,updated_at,revoked_at)
                VALUES (?,?,?,?,?,?,'connected',?,?,?,?,?,?,?,NULL)
                ON CONFLICT(workspace_id,user_id,provider,external_id) DO UPDATE SET
                  display_name=excluded.display_name,status='connected',
                  access_token_encrypted=excluded.access_token_encrypted,
                  refresh_token_encrypted=excluded.refresh_token_encrypted,
                  token_expires_at=excluded.token_expires_at,
                  scopes_json=excluded.scopes_json,metadata_json=excluded.metadata_json,
                  updated_at=excluded.updated_at,revoked_at=NULL""",
                (
                    grant_id,
                    self.workspace_id,
                    self.user_id,
                    provider,
                    external_id,
                    display_name,
                    encrypted_access,
                    encrypted_refresh,
                    expires_at,
                    json.dumps(sorted(set(scopes or []))),
                    json.dumps(metadata),
                    now,
                    now,
                ),
            )
        return grant_id

    def account(self, provider: str) -> ConnectorAccount | None:
        if not self.workspace_id or not self.user_id:
            return None
        records = rows(
            """SELECT * FROM oauth_token_grants
            WHERE workspace_id=? AND user_id=? AND provider=? AND status='connected'
            ORDER BY updated_at DESC LIMIT 1""",
            (self.workspace_id, self.user_id, provider),
        )
        if not records:
            return self._legacy_account(provider)
        record = records[0]
        return ConnectorAccount(
            id=record["id"],
            workspace_id=record["workspace_id"],
            user_id=record["user_id"],
            provider=record["provider"],
            external_id=record["external_id"],
            display_name=record["display_name"],
            access_token=self.cipher.decrypt(
                record["access_token_encrypted"], self._context(provider)
            ),
            metadata={
                **json.loads(record.get("metadata_json") or "{}"),
                "scopes": json.loads(record.get("scopes_json") or "[]"),
            },
        )

    def token(self, provider: str) -> str | None:
        account = self.account(provider)
        return account.access_token if account else None

    def refresh_token(self, provider: str) -> str:
        """The stored refresh token for the delegated grant, if one exists."""
        if not self.workspace_id or not self.user_id:
            return ""
        records = rows(
            """SELECT refresh_token_encrypted FROM oauth_token_grants
            WHERE workspace_id=? AND user_id=? AND provider=? AND status='connected'
            ORDER BY updated_at DESC LIMIT 1""",
            (self.workspace_id, self.user_id, provider),
        )
        if not records or not records[0].get("refresh_token_encrypted"):
            return ""
        return self.cipher.decrypt(records[0]["refresh_token_encrypted"], self._context(provider))

    def status(self, provider: str) -> list[dict[str, Any]]:
        if not self.workspace_id:
            return []
        clauses = ["workspace_id=?", "provider=?"]
        params: list[Any] = [self.workspace_id, provider]
        if self.user_id:
            clauses.append("user_id=?")
            params.append(self.user_id)
        records = rows(
            """SELECT id,user_id,external_id,display_name,status,scopes_json,
            metadata_json,created_at,updated_at,revoked_at FROM oauth_token_grants
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC",
            tuple(params),
        )
        for record in records:
            record["metadata"] = {
                **json.loads(record.pop("metadata_json") or "{}"),
                "scopes": json.loads(record.pop("scopes_json") or "[]"),
            }
        if records:
            return records
        return self._legacy_status(provider)

    def disconnect(self, provider: str) -> int:
        if not self.workspace_id or not self.user_id:
            raise ValueError("Disconnect requires the delegated user who owns the grant")
        now = utcnow()
        with connect() as conn:
            current = conn.execute(
                """UPDATE oauth_token_grants
                SET status='revoked',revoked_at=?,updated_at=?
                WHERE workspace_id=? AND user_id=? AND provider=? AND status='connected'""",
                (now, now, self.workspace_id, self.user_id, provider),
            ).rowcount
            legacy = conn.execute(
                """UPDATE workspace_connector_accounts
                SET status='disconnected',updated_at=?
                WHERE workspace_id=? AND user_id=? AND provider=? AND status='connected'""",
                (now, self.workspace_id, self.user_id, provider),
            ).rowcount
        return current + legacy

    def encrypt_payload(self, provider: str, payload: dict[str, Any]) -> str:
        return self.cipher.encrypt(json.dumps(payload), self._context(provider))

    def decrypt_payload(self, provider: str, payload: str) -> dict[str, Any]:
        return json.loads(self.cipher.decrypt(payload, self._context(provider)))

    def _legacy_account(self, provider: str) -> ConnectorAccount | None:
        records = rows(
            """SELECT * FROM workspace_connector_accounts
            WHERE workspace_id=? AND user_id=? AND provider=? AND status='connected'
            ORDER BY updated_at DESC LIMIT 1""",
            (self.workspace_id, self.user_id, provider),
        )
        if not records:
            return None
        record = records[0]
        # Legacy records used the same local Fernet key. KMS production mode
        # intentionally refuses silent use of a file-encrypted legacy grant.
        if not isinstance(self.cipher, LocalFernetCipher):
            raise RuntimeError("Reconnect this account to migrate its OAuth grant into KMS")
        return ConnectorAccount(
            id=record["id"],
            workspace_id=record["workspace_id"],
            user_id=record["user_id"],
            provider=record["provider"],
            external_id=record["external_id"],
            display_name=record["display_name"],
            access_token=self.cipher.decrypt(record["secret_encrypted"], self._context(provider)),
            metadata=json.loads(record.get("metadata_json") or "{}"),
        )

    def _legacy_status(self, provider: str) -> list[dict[str, Any]]:
        clauses = ["workspace_id=?", "provider=?"]
        params: list[Any] = [self.workspace_id, provider]
        if self.user_id:
            clauses.append("user_id=?")
            params.append(self.user_id)
        records = rows(
            """SELECT id,user_id,external_id,display_name,status,metadata_json,
            created_at,updated_at FROM workspace_connector_accounts WHERE """
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC",
            tuple(params),
        )
        for record in records:
            record["metadata"] = json.loads(record.pop("metadata_json") or "{}")
        return records
