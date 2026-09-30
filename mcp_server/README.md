# MemoryWorks MCP server

The server gives Cursor and other MCP clients a governed preflight loop around
consequential work:

1. Call `get_orgmemory_briefing` before acting. It returns a cited verdict,
   applicable constraints, precedents, and a durable `briefing_id`.
2. Perform the action only within the returned constraints and the caller's own
   approval policy.
3. Call `record_orgmemory_outcome` with that `briefing_id` to close the ledger.

`get_orgmemory_briefing` requires `read` scope. `record_orgmemory_outcome` is an
append-only audit write and requires `write` scope. A `requires_approval` verdict
does not grant approval, and `no_memory` does not grant permission.

The default catalog also exposes the current `orgmemory_*` read tools for asking,
searching, profiles, lineage, conflicts, change sets, skills, and work packages.
Legacy `runbook_*` tools are hidden unless
`ORGMEMORY_ENABLE_LEGACY_TOOLS=true` is set.

Configure the server with `ORGMEMORY_API_URL` and a workspace-scoped
`ORGMEMORY_API_KEY`. The former `RUNBOOK_API_URL` and `RUNBOOK_API_KEY` names are
accepted for one migration window and emit a deprecation warning.
