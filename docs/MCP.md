# MemoryWorks MCP server

`mcp_server/server.py` exposes the real MemoryWorks HTTP API over FastMCP stdio. It does not contain canned answers: questions return authorized memory, evidence, a retrieval trace, and the persisted HCAG context envelope.

## Run

```bash
make mcp
python mcp_server/server.py --health
```

Use `ORGMEMORY_API_URL` and `ORGMEMORY_API_KEY`. The legacy environment variables
`RUNBOOK_API_URL` and `RUNBOOK_API_KEY` remain supported for one migration window
and emit a deprecation warning. The default backend URL is `http://localhost:8000`.

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

## Cursor configuration

Create a workspace-scoped API key in **Settings → API keys**, then configure:

```json
{
  "mcpServers": {
    "orgmemory": {
      "command": "make",
      "args": ["-C", "/absolute/path/to/orgmemory", "mcp"],
      "env": {
        "ORGMEMORY_API_URL": "http://localhost:8000",
        "ORGMEMORY_API_KEY": "om_replace_with_a_new_workspace_key"
      }
    }
  }
}
```

Restart the MCP connection after saving. The bridge forwards the key as an `Authorization: Bearer` header. Workspace and team scope are enforced before retrieval; source-restricted memory cannot enter an agent's context envelope.

Useful prompts:

```text
Use MemoryWorks to explain this repo before I edit it.
Use MemoryWorks to find prior decisions about this service.
Use MemoryWorks to retrieve current company context for this bug.
Use MemoryWorks to list what changed and which agent skills became stale.
Before changing the deploy workflow, get an MemoryWorks briefing and record the outcome afterward.
```

## Legacy compatibility

The previous `runbook_*` tools are hidden by default. Set
`ORGMEMORY_ENABLE_LEGACY_TOOLS=true` only for clients that have not migrated.
Procedure extraction, action policies, approvals, and simulation are advanced
compatibility features; they are not the primary MemoryWorks product surface.
