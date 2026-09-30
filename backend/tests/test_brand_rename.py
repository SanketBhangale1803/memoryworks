import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_primary_product_surfaces_use_memoryworks_brand():
    for relative in (
        "frontend/app/page.tsx",
        "frontend/components/WorkspaceFrame.tsx",
        "frontend/lib/workspaceMap.ts",
        "backend/app/main.py",
        "README.md",
        "mcp_server/server.py",
    ):
        content = (ROOT / relative).read_text()
        assert "MemoryWorks" in content, relative


def test_old_product_name_is_gone_from_tracked_files():
    """OrgMemory survives only where it is a compatibility contract.

    The Python import path, env vars, and tool identifiers keep lowercase or
    upper-case forms (``orgmemory``, ``ORGMEMORY_*``, ``search_orgmemory``) so
    existing installs keep working. The capitalised product name appears only
    in the SDK aliases that keep ``from orgmemory import OrgMemory`` importable,
    and in notes that explain those aliases.
    """
    allowed = {
        "python_sdk/src/orgmemory/__init__.py",
        "python_sdk/tests/test_client.py",
        "mcp_server/server.py",
        "backend/tests/test_brand_rename.py",
        "frontend/tests/navigation.test.mjs",
    }
    tracked = subprocess.run(
        ["git", "grep", "-l", "OrgMemory"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    leftovers = [
        path
        for path in tracked
        if path not in allowed and not path.startswith(("presentation/", "reports/", ".agents/"))
    ]
    assert leftovers == []


def test_the_registry_keeps_real_places_and_drops_retired_ones():
    workspace_map = (ROOT / "frontend/lib/workspaceMap.ts").read_text()
    for reachable in ("/approvals", "/loop", "/connectors", "/memories"):
        assert f'href: "{reachable}"' in workspace_map
    for retired in ("/simulation", "/reliability", "/drift", "/runbooks", "/webmcp", "/benchmarks"):
        assert f'href: "{retired}"' not in workspace_map
