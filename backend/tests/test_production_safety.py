import pytest

from app.core.config import Settings


def test_production_configuration_fails_closed_with_dev_defaults():
    config = Settings(
        environment="production",
        frontend_url="http://localhost:3000",
        auth_dev_mode=True,
        jwt_secret="runbook-local-dev-secret",
    )

    with pytest.raises(RuntimeError) as exc:
        config.assert_safe_for_environment()

    message = str(exc.value)
    assert "AUTH_DEV_MODE must be false" in message
    assert "JWT_SECRET" in message
    assert "FRONTEND_URL must use HTTPS" in message


def test_production_configuration_accepts_explicit_safe_boundaries():
    config = Settings(
        environment="production",
        auth_dev_mode=False,
        jwt_secret="a-production-secret-that-is-longer-than-32-characters",
        frontend_url="https://runbook.example.com",
        runbook_demo_mode=False,
        allow_local_command_execution=False,
        arcadedb_password="a-production-arcade-password",
        github_client_id="production-client-id",
        github_client_secret="production-client-secret",
        runbook_embedding_provider="fastembed",
        connector_vault_provider="aws-kms",
        connector_kms_key_id="arn:aws:kms:us-east-1:123456789012:key/test",
        mcp_public_url="https://mcp.orgmemory.example.com",
        mcp_oauth_issuer_url="https://api.orgmemory.example.com",
    )

    config.assert_safe_for_environment()


def test_production_configuration_accepts_oci_kms():
    config = Settings(
        environment="production",
        auth_dev_mode=False,
        jwt_secret="a-production-secret-that-is-longer-than-32-characters",
        frontend_url="https://orgmemory.example.com",
        github_client_id="production-client-id",
        github_client_secret="production-client-secret",
        runbook_embedding_provider="fastembed",
        connector_vault_provider="oci-kms",
        connector_kms_key_id="ocid1.key.oc1.example",
        arcadedb_password="a-production-arcade-password",
        connector_oci_kms_crypto_endpoint="https://vault-crypto.kms.example.com",
        mcp_public_url="https://mcp.orgmemory.example.com",
        mcp_oauth_issuer_url="https://api.orgmemory.example.com",
    )

    config.assert_safe_for_environment()


def test_public_demo_configuration_is_production_safe_and_isolated():
    config = Settings(
        environment="production",
        public_demo_mode=True,
        auth_dev_mode=False,
        jwt_secret="a-public-demo-secret-that-is-longer-than-32-characters",
        frontend_url="https://orgmemory.vercel.app",
        graph_backend="memory",
        sqlite_path="/tmp/orgmemory/runbook.db",
        # Passed by validation alias: a developer's root .env sets
        # RUNBOOK_DEMO_MODE, which out-prioritizes the field-name kwarg.
        **{"ORGMEMORY_DEMO_MODE": True},
        allow_local_command_execution=False,
        org_memory_execution_enabled=False,
        connector_sync_worker_enabled=False,
        connector_custom_mcp_enabled=False,
        mcp_public_url="https://orgmemory.vercel.app/mcp",
        mcp_oauth_issuer_url="https://orgmemory.vercel.app",
    )

    config.assert_safe_for_environment()


def test_public_demo_configuration_refuses_execution_or_external_connectors():
    config = Settings(
        environment="production",
        public_demo_mode=True,
        auth_dev_mode=False,
        jwt_secret="a-public-demo-secret-that-is-longer-than-32-characters",
        frontend_url="https://orgmemory.vercel.app",
        graph_backend="memory",
        sqlite_path="/tmp/orgmemory/runbook.db",
        **{"ORGMEMORY_DEMO_MODE": True},
        allow_local_command_execution=True,
        org_memory_execution_enabled=True,
        connector_sync_worker_enabled=True,
        connector_custom_mcp_enabled=True,
        mcp_public_url="https://orgmemory.vercel.app/mcp",
        mcp_oauth_issuer_url="https://orgmemory.vercel.app",
    )

    with pytest.raises(RuntimeError) as exc:
        config.assert_safe_for_environment()

    assert "execution must be disabled" in str(exc.value)
    assert "External connector execution must be disabled" in str(exc.value)
