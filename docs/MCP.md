# MemoryWorks MCP server

`mcp_server/server.py` exposes the real MemoryWorks HTTP API over FastMCP — stdio for a local bridge, or streamable HTTP with OAuth where the server is deployed (the Docker `mcp` profile, or the `mcp.` subdomain in `deploy/oci/`). The Vercel deployment at memoryworks.app does not run the MCP server yet, so connect to it over stdio with an API key. It does not contain canned answers: questions return authorized memory, evidence, a retrieval trace, and the persisted HCAG context envelope.

## Run

```bash
make mcp
python mcp_server/server.py --health
```

Use `MEMORYWORKS_API_URL` and `MEMORYWORKS_API_KEY` (and `MEMORYWORKS_MCP_*` for
the HTTP transport). The older `ORGMEMORY_*` and `RUNBOOK_*` names still work and
emit a deprecation warning. The default backend URL is `http://localhost:8000`.

The easiest setup is in the app: **Sources → AI tools** generates the exact
configuration for Claude Code, Cursor, VS Code, Claude, and ChatGPT against
this deployment's own URLs.

## Preflight contract

| Tool | Scope | Purpose |
|---|---|---|
| `get_orgmemory_briefing` | `read` | Return a cited verdict, constraints, precedents, and durable `briefing_id` before consequential work |
| `record_orgmemory_outcome` | `write` | Append the action's result to the briefing ledger without approving or mutating company memory |

Always carry the `briefing_id` from the first call into the outcome call. Treat
`requires_approval` as a signal to use the caller's approval system, not as
authorization from MemoryWorks. Treat `no_memory` as missing organizational context,
not permission to proceed.

## Company-memory tools

| Tool | Purpose |
|---|---|
| `orgmemory_ingest_github_repo` | Ingest repository evidence and extract atomic memory |
| `orgmemory_ingest_slack_channel` | Ingest source-backed team decisions and conventions |
| `orgmemory_upload_source` | Upload docs, reports, or pasted company knowledge |
| `orgmemory_ask` | Build a governed context envelope and answer with evidence |
| `orgmemory_search_memories` | Search current typed memory units |
| `orgmemory_get_company_profile` | Assemble the current company profile |
| `orgmemory_get_project_profile` | Assemble a project profile |
| `orgmemory_get_service_profile` | Assemble a service profile |
| `orgmemory_get_memory_graph` | Inspect real memory graph counts |
| `orgmemory_list_memory_conflicts` | List evidence-backed contradictions |
| `orgmemory_list_memory_updates` | List newer-to-older memory relationships |
| `orgmemory_list_source_revisions` | Inspect immutable source history |
| `orgmemory_list_change_sets` | Inspect semantic memory commits and impacts |
| `orgmemory_compile_skill` | Compile current policy/procedure memory into a versioned agent skill |
| `orgmemory_list_skills` | List current or stale skill specs |
| `orgmemory_create_work` / `orgmemory_list_work` / `orgmemory_get_work` | Create and read source-backed work packages |
| `orgmemory_resolve_work_step` / `orgmemory_complete_work_step` | Move a work package forward and return result evidence |
| `orgmemory_request_connector_action` | Ask to write to a connected tool — always waits for approval |
| `orgmemory_list_connector_action_requests` | See those requests and their status |

Tool names keep the `orgmemory` prefix on purpose: clients and agent prompts
call them by name, so renaming them would break existing setups.

## Local stdio configuration (Cursor example)

Create a workspace-scoped API key in **Sources → API keys**, then configure:

```json
{
  "mcpServers": {
    "memoryworks": {
      "command": "make",
      "args": ["-C", "/absolute/path/to/memoryworks", "mcp"],
      "env": {
        "MEMORYWORKS_API_URL": "http://localhost:8000",
        "MEMORYWORKS_API_KEY": "om_replace_with_a_new_workspace_key"
      }
    }
  }
}
```

Against a deployment that runs `make mcp-http` (or the compose `mcp`
service), an HTTP-capable client can connect to that server's `/mcp` path and
sign in through MemoryWorks OAuth instead of pasting a key. Set
`MEMORYWORKS_MCP_PUBLIC_URL` to the server's origin, without `/mcp`.

Restart the MCP connection after saving. The bridge forwards the key as an `Authorization: Bearer` header. Workspace and team scope are enforced before retrieval; source-restricted memory cannot enter an agent's context envelope.

Useful prompts:

```text
Use MemoryWorks to explain this repo before I edit it.
Use MemoryWorks to find prior decisions about this service.
Use MemoryWorks to retrieve current company context for this bug.
Use MemoryWorks to list what changed and which agent skills became stale.
Before changing the deploy workflow, get a MemoryWorks briefing and record the outcome afterward.
```

## Legacy compatibility

The `runbook_*` tools from the product's earlier direction are hidden by
default. Set `ORGMEMORY_ENABLE_LEGACY_TOOLS=true` only for a client that still
depends on them; they are slated for removal.
