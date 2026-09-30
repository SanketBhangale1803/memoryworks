# MemoryWorks repository and verification map

Use the rows relevant to the current change. Paths are relative to the MemoryWorks repository root. This map was inspected on September 9, 2026; recheck current files before acting. It records code locations, not a claim that every deployed integration works.

## Context entry points

| Task | Read first | Follow only if needed |
|---|---|---|
| Startup milestone | `docs/STARTUP_EXECUTION_PLAN.md`, requested release | Relevant source and test rows below; roadmap modules may not exist yet |
| Pre-action briefing | `backend/app/memory/briefing.py`, `backend/app/api/schemas.py` | `create_briefing` in `backend/app/api/routes.py`, `frontend/lib/webmcp.ts` |
| Memory provenance and scope | `backend/app/memory/company.py`, `backend/app/governance/scopes.py` | `backend/app/memory/authority.py`, `backend/app/memory/beliefs.py` |
| Context delivery and retrieval | `backend/app/hcag_adapter/adapter.py`, `backend/app/retrieval/service.py` | `backend/app/hcag_adapter/context_store.py`, `backend/app/swarm/service.py` |
| Ledger and feedback | `backend/app/outcomes/ledger.py`, `backend/app/core/database.py` | Outcome routes in `backend/app/api/routes.py`, `frontend/app/loop/page.tsx` |
| GitHub ingestion and events | `backend/app/connectors/github/client.py`, `backend/app/connectors/sync.py` | Webhooks in `backend/app/api/routes.py`, `backend/app/ingestion/repository.py` |
| Authentication and approval | `backend/app/auth/middleware.py`, `backend/app/auth/api_keys.py` | `backend/app/approvals/service.py`, `backend/app/auth/mcp_oauth.py`, `backend/app/work/service.py` |
| Standalone MCP | `mcp_server/server.py`, `mcp_server/requirements.txt` | `docs/MCP.md`, OAuth scopes and backend authorization |
| Python SDK / CLI | `python_sdk/src/orgmemory/client.py`, `models.py`, `cli.py` in that same directory | `python_sdk/tests/test_client.py`, `python_sdk/tests/test_cli.py` |
| Browser tools and UI | `frontend/lib/webmcp.ts`, `frontend/lib/webmcpCatalog.ts` | `frontend/components/WorkspaceChat.tsx`, `frontend/lib/workspaceMap.ts`, relevant app page |
| Execution / learned precedent | `backend/app/execution/runner.py`, `backend/app/skills/library.py` | `backend/app/work/service.py`, `backend/app/outcomes/ledger.py` |
| Deployment / migrations | `backend/app/core/config.py`, `backend/app/core/database.py` | `compose.production.yml`, `docs/SECURITY.md`, `backend/app/graph/migrations.py` |

## Invariants worth preserving

- The briefing builder makes its verdict without a model call. Stability assumes the same intent, authorized evidence snapshot, and rule version; changed evidence can change a verdict.
- Missing or ambiguous service scope must not borrow unrelated service constraints. Preserve workspace, project, and team authorization through evidence and outcomes.
- A briefing is advice. `requires_approval` is not a completed approval, and `no_memory` is not permission to act.
- `record_context` is currently best-effort and may return an empty ID. The briefing route currently records an empty evidence list even when the response cites memories. Recheck these gaps before claiming durable evidence capture.
- Outcomes use `succeeded`, `failed`, `partial`, `abandoned`, and `unknown`. Preserve observation history; separate CI observations from actual operational outcomes.
- A new SQLite column needs an upgrade path for existing databases. Review both `SCHEMA` and `_migrate_columns`; graph migrations address a different store.
- Browser WebMCP and standalone MCP are separate registrations. As inspected, browser briefing/outcome tools exist but the standalone server does not register them. Discover current tools before use.
- The standalone MCP HTTP helper infers `write` for most POST calls. A future read-only briefing tool needs an explicit scope decision as well as backend authorization.
- The execution runner can invoke external agent CLIs, create commits, and optionally push. It is not a sandbox just because it uses a fresh clone.
- Existing `runbook` table, cookie, environment, and package names may be compatibility contracts. Read callers before renaming them.
- The HCAG adapter can fall back when an optional sibling dependency is absent. Report the engine actually tested rather than implying the full external engine ran.

## Select relevant checks

Resolve an available Python interpreter with the required dependencies. Examples below use an existing `backend/.venv`; if absent, use the configured environment. Avoid creating a new environment until it is needed.

For a briefing or ledger change, run from `backend/`:

```sh
.venv/bin/python -m pytest tests/test_briefing.py tests/test_outcome_loop.py -q
```

For API and scope changes, select affected suites from `backend/`:

```sh
.venv/bin/python -m pytest tests/test_agent_briefing.py tests/test_api_key_authorization.py tests/test_auth_connector_boundaries.py tests/test_production_safety.py -q
```

For schema upgrades, add a regression using a pre-change fixture database when columns or indexes change. Fresh database tests do not prove migration compatibility.

For MCP changes, test actual tool discovery and calling behavior. Inspect `backend/tests/test_api_key_authorization.py` and existing MCP-related tests; add missing coverage with the current MCP dependency installed. Do not run a roadmap-only test filename that has not been created. After an MCP change, check browser/standalone naming, response shape, and scope parity where applicable.

For SDK changes, run from the repository root:

```sh
PYTHONPATH=python_sdk/src backend/.venv/bin/python -m pytest python_sdk/tests -q
```

For frontend changes, run from `frontend/`:

```sh
npm test
npx tsc --noEmit
```

Run `npm run build` when the change affects routing, bundling, environment use, or release readiness. Render and inspect changed UI states when layout or interaction changes.

For broad backend changes, the CI contract in `.github/workflows/ci.yml` includes pytest, Ruff, and Black. Run the relevant targeted checks first and broader checks when the affected surface warrants them. Current CI has backend, frontend, and SDK jobs; inspect whether an MCP job has since been added.

For Markdown-only work, validate frontmatter, local links, referenced paths, and example syntax as relevant. No product test suite is required for a prose edit. Check new files directly; `git diff --check` does not inspect untracked files.

The backend test fixtures redirect graph/database state and clear model credentials. Confirm that new tests use those fixtures before running them. Do not invoke reset, purge, demo-loading, or `scripts/smoke_loop.py` against an existing workspace for routine verification.
