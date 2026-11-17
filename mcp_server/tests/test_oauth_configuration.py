from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ("MEMORYWORKS_", "ORGMEMORY_", "")


@pytest.mark.parametrize("preferred_prefix", PREFIXES)
def test_backend_and_mcp_resolve_the_same_oauth_urls(preferred_prefix):
    env = os.environ.copy()
    preferred_index = PREFIXES.index(preferred_prefix)
    for index, prefix in enumerate(PREFIXES):
        public_key = f"{prefix}MCP_PUBLIC_URL"
        issuer_key = f"{prefix}MCP_OAUTH_ISSUER_URL"
        env.pop(public_key, None)
        env.pop(issuer_key, None)
        if index >= preferred_index:
            env[public_key] = f"https://mcp-{index}.example.test/mcp/"
            env[issuer_key] = f"https://api-{index}.example.test/"

    # Load each process's settings in a fresh interpreter. Disable the
    # backend's dotenv file so the shared environment is the only input.
    backend_urls = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import json; from app.core.config import Settings; config = Settings(_env_file=None); "
            "print(json.dumps([config.mcp_public_url + '/mcp', "
            "config.mcp_oauth_issuer_url.rstrip('/')]))",
        ],
        cwd=ROOT / "backend",
        env=env,
        text=True,
    )
    mcp_urls = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import json; from server import MCP_RESOURCE, OAUTH_ISSUER; "
            "print(json.dumps([MCP_RESOURCE, OAUTH_ISSUER]))",
        ],
        cwd=ROOT / "mcp_server",
        env=env,
        text=True,
    )

    assert (
        json.loads(backend_urls)
        == json.loads(mcp_urls)
        == [
            env[f"{preferred_prefix}MCP_PUBLIC_URL"].rstrip("/"),
            env[f"{preferred_prefix}MCP_OAUTH_ISSUER_URL"].rstrip("/"),
        ]
    )
