"""MemoryWorks configuration names and one-window legacy compatibility."""

import pytest

from app.core.config import Settings, _warn_for_legacy_environment


def test_orgmemory_environment_names_are_primary(monkeypatch):
    monkeypatch.setenv("ORGMEMORY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("RUNBOOK_EMBEDDING_PROVIDER", "deterministic")
    monkeypatch.setenv("ORGMEMORY_MCP_PUBLIC_URL", "https://mcp.orgmemory.test")
    monkeypatch.setenv("ORGMEMORY_SESSION_COOKIE_NAME", "orgmemory_test")

    config = Settings(_env_file=None)

    assert config.runbook_embedding_provider == "openai"
    assert config.mcp_public_url == "https://mcp.orgmemory.test"
    assert config.session_cookie_name == "orgmemory_test"


def test_legacy_environment_name_is_accepted_with_a_warning(monkeypatch):
    monkeypatch.delenv("ORGMEMORY_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("RUNBOOK_EMBEDDING_PROVIDER", "fastembed")

    with pytest.warns(FutureWarning, match="ORGMEMORY_EMBEDDING_PROVIDER"):
        _warn_for_legacy_environment()

    assert Settings(_env_file=None).runbook_embedding_provider == "fastembed"
