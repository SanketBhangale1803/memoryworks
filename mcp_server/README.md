# MemoryWorks MCP server

A FastMCP server over the MemoryWorks HTTP API, for Claude Code, Cursor,
VS Code, Claude, ChatGPT, and other MCP clients. It holds no business logic:
every tool calls the API, and workspace and team scope are enforced there.

```bash
make mcp                                  # stdio, with an API key
make mcp-http                             # streamable HTTP on :8001, with OAuth
python mcp_server/server.py --health      # check the backend
```

Settings: `MEMORYWORKS_API_URL`, `MEMORYWORKS_API_KEY`, and `MEMORYWORKS_MCP_*`.
The older `ORGMEMORY_*` and `RUNBOOK_*` names still work with a deprecation
warning. The Vercel deployment at memoryworks.app does not run this server yet;
use stdio with an API key against it, or deploy the server (Docker `mcp` profile
or `deploy/oci/`) for HTTP with OAuth.

In the app, **Sources → AI tools** generates the configuration for each client.
Tool reference and examples: [`docs/MCP.md`](../docs/MCP.md).
