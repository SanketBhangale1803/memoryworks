from pathlib import Path


def test_primary_product_surfaces_use_orgmemory_brand():
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "frontend/app/page.tsx",
        "frontend/components/WorkspaceFrame.tsx",
        "frontend/lib/workspaceMap.ts",
        "backend/app/main.py",
        "README.md",
        "mcp_server/server.py",
    ):
        content = (root / relative).read_text()
        assert "OrgMemory" in content
    # The old top bar hid these behind a URL nobody could guess. The registry
    # that replaced it must carry every one of them, because the command menu
    # renders exactly what it lists and nothing else.
    workspace_map = (root / "frontend/lib/workspaceMap.ts").read_text()
    for reachable in ("/approvals", "/simulation", "/reliability", "/drift", "/loop"):
        assert f'href: "{reachable}"' in workspace_map
