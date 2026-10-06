from __future__ import annotations

import os
import warnings
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


LEGACY_ENVIRONMENT_NAMES = {
    "RUNBOOK_EMBEDDING_PROVIDER": "ORGMEMORY_EMBEDDING_PROVIDER",
    "RUNBOOK_EMBEDDING_MODEL": "ORGMEMORY_EMBEDDING_MODEL",
    "RUNBOOK_OPENAI_EMBEDDING_MODEL": "ORGMEMORY_OPENAI_EMBEDDING_MODEL",
    "RUNBOOK_RERANKER_PROVIDER": "ORGMEMORY_RERANKER_PROVIDER",
    "RUNBOOK_RERANKER_MODEL": "ORGMEMORY_RERANKER_MODEL",
    "RUNBOOK_EMBEDDING_CACHE_DIR": "ORGMEMORY_EMBEDDING_CACHE_DIR",
    "RUNBOOK_SEMANTIC_CANDIDATE_LIMIT": "ORGMEMORY_SEMANTIC_CANDIDATE_LIMIT",
    "RUNBOOK_EMBEDDING_BATCH_SIZE": "ORGMEMORY_EMBEDDING_BATCH_SIZE",
    "RUNBOOK_MODEL_THREADS": "ORGMEMORY_MODEL_THREADS",
    "RUNBOOK_DEMO_MODE": "ORGMEMORY_DEMO_MODE",
    "MCP_PUBLIC_URL": "ORGMEMORY_MCP_PUBLIC_URL",
    "MCP_OAUTH_ISSUER_URL": "ORGMEMORY_MCP_OAUTH_ISSUER_URL",
    "MCP_OAUTH_ACCESS_TOKEN_MINUTES": "ORGMEMORY_MCP_OAUTH_ACCESS_TOKEN_MINUTES",
    "MCP_OAUTH_REFRESH_TOKEN_DAYS": "ORGMEMORY_MCP_OAUTH_REFRESH_TOKEN_DAYS",
    "MCP_OAUTH_ENABLE_DCR": "ORGMEMORY_MCP_OAUTH_ENABLE_DCR",
}


def _warn_for_legacy_environment() -> None:
    for legacy, replacement in LEGACY_ENVIRONMENT_NAMES.items():
        if legacy in os.environ and replacement not in os.environ:
            warnings.warn(
                f"{legacy} is deprecated; use {replacement}",
                FutureWarning,
                stacklevel=2,
            )


_warn_for_legacy_environment()


def _is_fernet_key(value: str) -> bool:
    from cryptography.fernet import Fernet

    try:
        Fernet(value.encode())
    except (ValueError, TypeError):
        return False
    return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", populate_by_name=True)

    app_name: str = "MemoryWorks"
    environment: str = "development"
    log_level: str = "INFO"
    api_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    # The public scheme://host of THIS deployment. When empty or local, the
    # OAuth and redirect layer derives it from the incoming request's
    # forwarded headers, so a hosted deployment needs no baked-in domain.
    public_base_url: str = ""
    # Persisted storage names remain stable during the product-name migration.
    sqlite_path: Path = ROOT / "data" / "runbook.db"
    generated_runbooks_dir: Path = ROOT / "generated_runbooks"
    repo_cache_dir: Path = ROOT / "data" / "repos"
    local_repo_mount: Path = Path("/workspace/local_repos")
    # Execution gets its own clone per run. The ingest cache is read to build
    # memory and must never be left dirty by an agent editing files in it.
    execution_dir: Path = ROOT / "data" / "executions"

    graph_backend: str = "arcadedb"
    # Chunks held prepared in memory for ranking (app/graph/project_index.py),
    # ~15 KB each: 40,000 is about 600 MB.
    graph_index_max_chunks: int = 40_000
    arcadedb_host: str = "localhost"
    arcadedb_port: int = 2480
    arcadedb_user: str = "root"
    arcadedb_password: str = "runbook_dev_password"
    arcadedb_database: str = "runbook"

    hcag_path: Path = ROOT.parent / "hcag"
    agentgate_path: Path = ROOT.parent / "agentgate"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    google_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    openrouter_api_key: str = ""
    glm_model: str = "z-ai/glm-5.3-flash"
    glm_base_url: str = "https://openrouter.ai/api/v1"
    # GLM 5.3 reasons before answering and OpenRouter will not let that be turned
    # off. At the provider's default effort a grounded answer took ~140 s, which
    # outlives the web proxy; "low" answered the same question in ~5 s.
    glm_reasoning_effort: str = "low"
    # Process-wide caps on model requests, whatever asked for them: at most this
    # many in flight, at most this many started per minute, and no single call
    # longer than this when nothing tighter applies. A call waits for a slot only
    # as long as its own budget allows, then gives up and its caller falls back.
    llm_max_concurrent_requests: int = 4
    llm_max_requests_per_minute: int = 60
    llm_request_max_seconds: float = 90.0
    xai_api_key: str = ""
    grok_model: str = "grok-4.5"
    kimi_api_key: str = ""
    kimi_model: str = "kimi-k2.6"
    kimi_base_url: str = "https://api.moonshot.ai/v1"
    org_memory_default_model_provider: str = "glm"
    # A question is answered several independent ways and a judge picks the one
    # that actually fits what was asked. 1 disables the parallel pass entirely.
    org_memory_answer_candidates: int = 5
    org_memory_answer_judge_enabled: bool = True
    # Wall-clock budget for the parallel candidates. Whatever finished in time is
    # judged; a slow candidate is dropped rather than holding the answer hostage.
    org_memory_answer_deadline_seconds: float = 40.0
    # One budget per answer, shared by every model call it makes (drafts, the
    # judged candidates, the general-knowledge fallback): the calls stop when
    # either runs out, and the answer falls back to what it already has.
    org_memory_answer_llm_budget_seconds: float = 75.0
    org_memory_answer_max_llm_calls: int = 8
    # How long the chat's stream waits for an answer before saying it is busy.
    org_memory_answer_stream_timeout_seconds: float = 150.0
    # An Agent-mode run may take one model call per step plus a few to recover.
    org_agent_max_llm_calls: int = 12
    org_agent_llm_budget_seconds: float = 300.0
    # When company memory holds nothing relevant and the question is not about
    # the company, answer from the model's own knowledge instead of refusing.
    org_memory_general_knowledge_enabled: bool = True
    # Autonomous execution. `executor` picks which headless coding agent applies
    # a handoff. Pushing is a separate switch because committing to a throwaway
    # local clone is reversible and publishing to a shared remote is not.
    # Disabled by default: the runner is not sandboxed, so enabling it outside
    # the isolated-executor profile is a deliberate, explicit decision.
    org_memory_executor: str = "cursor"
    org_memory_execution_enabled: bool = False
    # Containment opt-in for deployments that provide their own disposable,
    # resource-limited worker. Normal production startup refuses execution
    # without it.
    org_memory_execution_isolated_profile: bool = False
    org_memory_execution_timeout_seconds: int = 900
    org_memory_execution_allow_push: bool = False
    org_memory_execution_branch_prefix: str = "orgmemory/"
    runbook_embedding_provider: str = Field(
        default="deterministic",
        validation_alias=AliasChoices(
            "ORGMEMORY_EMBEDDING_PROVIDER",
            "RUNBOOK_EMBEDDING_PROVIDER",
            "runbook_embedding_provider",
        ),
    )
    runbook_embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        validation_alias=AliasChoices(
            "ORGMEMORY_EMBEDDING_MODEL",
            "RUNBOOK_EMBEDDING_MODEL",
            "runbook_embedding_model",
        ),
    )
    runbook_openai_embedding_model: str = Field(
        default="text-embedding-3-large",
        validation_alias=AliasChoices(
            "ORGMEMORY_OPENAI_EMBEDDING_MODEL",
            "RUNBOOK_OPENAI_EMBEDDING_MODEL",
            "runbook_openai_embedding_model",
        ),
    )
    runbook_reranker_provider: str = Field(
        default="deterministic",
        validation_alias=AliasChoices(
            "ORGMEMORY_RERANKER_PROVIDER",
            "RUNBOOK_RERANKER_PROVIDER",
            "runbook_reranker_provider",
        ),
    )
    runbook_reranker_model: str = Field(
        default="Xenova/ms-marco-MiniLM-L-6-v2",
        validation_alias=AliasChoices(
            "ORGMEMORY_RERANKER_MODEL",
            "RUNBOOK_RERANKER_MODEL",
            "runbook_reranker_model",
        ),
    )
    runbook_embedding_cache_dir: Path = Field(
        default=ROOT / "data" / "models",
        validation_alias=AliasChoices(
            "ORGMEMORY_EMBEDDING_CACHE_DIR",
            "RUNBOOK_EMBEDDING_CACHE_DIR",
            "runbook_embedding_cache_dir",
        ),
    )
    runbook_semantic_candidate_limit: int = Field(
        default=48,
        validation_alias=AliasChoices(
            "ORGMEMORY_SEMANTIC_CANDIDATE_LIMIT",
            "RUNBOOK_SEMANTIC_CANDIDATE_LIMIT",
            "runbook_semantic_candidate_limit",
        ),
    )
    runbook_embedding_batch_size: int = Field(
        default=16,
        validation_alias=AliasChoices(
            "ORGMEMORY_EMBEDDING_BATCH_SIZE",
            "RUNBOOK_EMBEDDING_BATCH_SIZE",
            "runbook_embedding_batch_size",
        ),
    )
    runbook_model_threads: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "ORGMEMORY_MODEL_THREADS",
            "RUNBOOK_MODEL_THREADS",
            "runbook_model_threads",
        ),
    )
    assertion_auto_verify_enabled: bool = True
    assertion_auto_verify_days: int = 7

    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/api/auth/github/callback"
    github_oauth_use_pkce: bool = True
    github_token: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""
    slack_redirect_uri: str = "http://localhost:8000/api/auth/slack/callback"
    slack_bot_token: str = ""
    notion_client_id: str = ""
    notion_client_secret: str = ""
    integration_encryption_key: str = ""
    connector_vault_provider: str = "local"
    connector_kms_key_id: str = ""
    connector_kms_region: str = ""
    connector_oci_kms_crypto_endpoint: str = ""
    connector_oci_kms_auth: str = "instance-principal"
    connector_oci_config_profile: str = "DEFAULT"
    connector_manifest_public_keys_json: str = "{}"
    connector_sync_worker_enabled: bool = True
    # Slow work — imports, connector syncs, webhook processing, watches, the
    # startup backfill — runs in this process when true (one container does
    # everything). Set it false on the API and run `python -m app.worker` beside
    # it, so answering never competes with importing for this process's CPU.
    background_work_enabled: bool = True
    connector_sync_poll_seconds: int = 2
    # Imports wait up to this long between files while an answer is being
    # written, so a large import never starves the person who is asking.
    connector_sync_yield_to_answers_seconds: float = 60.0
    # How often standing organizational watches are re-evaluated.
    org_watch_poll_seconds: int = 120
    connector_custom_mcp_enabled: bool = True
    connector_rest_sources_enabled: bool = True
    connector_custom_mcp_allow_private_networks: bool = False
    # API guard: buffered-body cap and per-principal sliding-window limits.
    # Limits are per instance; a load-balanced deployment multiplies the budget.
    api_max_body_bytes: int = 115 * 1024 * 1024
    api_rate_limit_enabled: bool = True
    api_rate_limit_per_minute: int = 600
    api_rate_limit_public_per_minute: int = 120

    mcp_public_url: str = Field(
        default="http://localhost:8001",
        validation_alias=AliasChoices(
            "ORGMEMORY_MCP_PUBLIC_URL", "MCP_PUBLIC_URL", "mcp_public_url"
        ),
    )
    mcp_oauth_issuer_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices(
            "ORGMEMORY_MCP_OAUTH_ISSUER_URL",
            "MCP_OAUTH_ISSUER_URL",
            "mcp_oauth_issuer_url",
        ),
    )
    mcp_oauth_access_token_minutes: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "ORGMEMORY_MCP_OAUTH_ACCESS_TOKEN_MINUTES",
            "MCP_OAUTH_ACCESS_TOKEN_MINUTES",
            "mcp_oauth_access_token_minutes",
        ),
    )
    mcp_oauth_refresh_token_days: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "ORGMEMORY_MCP_OAUTH_REFRESH_TOKEN_DAYS",
            "MCP_OAUTH_REFRESH_TOKEN_DAYS",
            "mcp_oauth_refresh_token_days",
        ),
    )
    mcp_oauth_enable_dcr: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ORGMEMORY_MCP_OAUTH_ENABLE_DCR",
            "MCP_OAUTH_ENABLE_DCR",
            "mcp_oauth_enable_dcr",
        ),
    )

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = ""
    email_auth_enabled: bool = True
    email_code_ttl_minutes: int = 10
    email_code_resend_seconds: int = 45
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    nextauth_secret: str = ""
    jwt_secret: str = "orgmemory-local-dev-secret"
    app_base_url: str = "http://localhost:3000"
    auth_dev_mode: bool = True
    # Credential-free, isolated access for the hosted challenge demo. Unlike
    # AUTH_DEV_MODE, this profile still runs with production cookie/security
    # behavior and refuses to start if connectors or execution are enabled.
    public_demo_mode: bool = False
    session_cookie_name: str = Field(
        default="orgmemory_session",
        validation_alias=AliasChoices(
            "ORGMEMORY_SESSION_COOKIE_NAME",
            "SESSION_COOKIE_NAME",
            "session_cookie_name",
        ),
    )
    session_cookie_domain: str = ""

    runbook_demo_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ORGMEMORY_DEMO_MODE", "RUNBOOK_DEMO_MODE", "runbook_demo_mode"
        ),
    )
    allow_local_command_execution: bool = False
    org_memory_enable_actions: bool = False
    org_memory_enable_procedures: bool = False
    org_memory_enable_advanced_reliability: bool = False
    org_memory_authority_order: str = (
        "current_code_config,approved_policy_decision,recent_authoritative_slack,"
        "merged_pull_request,open_issue,readme_documentation,old_slack,inferred_memory"
    )
    org_memory_change_interpreter_provider: str = "auto"
    org_memory_run_live_llm_tests: bool = False
    github_webhook_secret: str = ""

    @field_validator("mcp_public_url")
    @classmethod
    def _mcp_origin_only(cls, value: str) -> str:
        # The MCP endpoint path is appended wherever the URL is used, so a value
        # that already ends in /mcp would be advertised as .../mcp/mcp.
        return value.rstrip("/").removesuffix("/mcp")

    @property
    def arcadedb_url(self) -> str:
        return f"http://{self.arcadedb_host}:{self.arcadedb_port}"

    def storage_issues(self) -> list[str]:
        """Reasons this process's state would not survive a restart or be shared
        with another process. Empty means storage is durable."""
        issues: list[str] = []
        if str(self.sqlite_path).startswith("/tmp/"):
            issues.append("database_in_tmp")
        if self.graph_backend.casefold() == "memory":
            issues.append("graph_in_memory")
        if (
            self.connector_vault_provider.casefold() not in {"aws-kms", "oci-kms"}
            and not self.integration_encryption_key
        ):
            issues.append("connector_key_per_process")
        return issues

    def assert_safe_for_environment(self) -> None:
        """Refuse production startup with development trust boundaries."""
        if self.environment.casefold() != "production":
            return
        faults: list[str] = []
        if self.public_demo_mode:
            if self.auth_dev_mode:
                faults.append("AUTH_DEV_MODE must be false in the public demo")
            if self.graph_backend.casefold() != "memory":
                faults.append("GRAPH_BACKEND must be memory in the public demo")
            if not self.runbook_demo_mode:
                faults.append("ORGMEMORY_DEMO_MODE must be true in the public demo")
            if self.allow_local_command_execution or self.org_memory_execution_enabled:
                faults.append("All command and agent execution must be disabled in the public demo")
            if self.connector_sync_worker_enabled or self.connector_custom_mcp_enabled:
                faults.append("External connector execution must be disabled in the public demo")
            if not str(self.sqlite_path).startswith("/tmp/orgmemory/"):
                faults.append(
                    "The public demo SQLite database must be disposable under /tmp/orgmemory"
                )
            if not self.frontend_url.startswith("https://"):
                faults.append("FRONTEND_URL must use HTTPS")
            if self.jwt_secret == "orgmemory-local-dev-secret" or len(self.jwt_secret) < 32:
                faults.append("JWT_SECRET must be a non-default secret of at least 32 characters")
            if not self.mcp_public_url.startswith("https://"):
                faults.append("MCP_PUBLIC_URL must use HTTPS")
            if not self.mcp_oauth_issuer_url.startswith("https://"):
                faults.append("MCP_OAUTH_ISSUER_URL must use HTTPS")
            if faults:
                raise RuntimeError("Unsafe public demo configuration: " + "; ".join(faults))
            return
        if self.auth_dev_mode:
            faults.append("AUTH_DEV_MODE must be false")
        if self.jwt_secret == "orgmemory-local-dev-secret" or len(self.jwt_secret) < 32:
            faults.append("JWT_SECRET must be a non-default secret of at least 32 characters")
        if self.runbook_demo_mode:
            faults.append("ORGMEMORY_DEMO_MODE must be false")
        if self.allow_local_command_execution:
            faults.append("ALLOW_LOCAL_COMMAND_EXECUTION must remain false")
        if self.org_memory_execution_enabled and not self.org_memory_execution_isolated_profile:
            faults.append(
                "Autonomous execution must stay disabled in production unless "
                "ORGMEMORY_EXECUTION_ISOLATED_PROFILE is explicitly true"
            )
        if self.graph_backend.casefold() != "memory" and self.arcadedb_password in {
            "",
            "runbook_dev_password",
        }:
            faults.append("ARCADEDB_PASSWORD must be set to a non-default value")
        if not self.frontend_url.startswith("https://"):
            faults.append("FRONTEND_URL must use HTTPS")
        if not (
            self.github_client_id
            and self.github_client_secret
            or self.google_client_id
            and self.google_client_secret
            or self.email_auth_enabled
            and self.smtp_host
            and self.email_from
        ):
            faults.append(
                "At least one production sign-in provider (GitHub, Google, or email) "
                "must be configured"
            )
        if self.runbook_embedding_provider.casefold() == "deterministic":
            faults.append("ORGMEMORY_EMBEDDING_PROVIDER must use fastembed or openai")
        vault_provider = self.connector_vault_provider.casefold()
        if vault_provider == "local":
            # A key generated next to the database would be lost with the
            # container and copied into every backup; only an explicit key,
            # kept outside the data volume, is acceptable in production.
            if not _is_fernet_key(self.integration_encryption_key):
                faults.append(
                    "CONNECTOR_VAULT_PROVIDER=local in production requires "
                    "INTEGRATION_ENCRYPTION_KEY set to a Fernet key"
                )
        elif vault_provider not in {"aws-kms", "oci-kms"} or not self.connector_kms_key_id:
            faults.append(
                "Production connector grants require CONNECTOR_VAULT_PROVIDER=aws-kms "
                "or oci-kms and CONNECTOR_KMS_KEY_ID, or local and INTEGRATION_ENCRYPTION_KEY"
            )
        if vault_provider == "oci-kms" and not self.connector_oci_kms_crypto_endpoint.startswith(
            "https://"
        ):
            faults.append("CONNECTOR_OCI_KMS_CRYPTO_ENDPOINT must use HTTPS")
        if not self.mcp_public_url.startswith("https://"):
            faults.append("MCP_PUBLIC_URL must use HTTPS")
        if not self.mcp_oauth_issuer_url.startswith("https://"):
            faults.append("MCP_OAUTH_ISSUER_URL must use HTTPS")
        if faults:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(faults))


settings = Settings()
