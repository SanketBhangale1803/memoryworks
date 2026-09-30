# Runbook Level 2 Upgrade Plan

## Current state

- Backend: FastAPI application under `backend/app` with Pydantic schemas, SQLite app state, ArcadeDB graph adapter, HCAG adapter, AgentGate adapter, GitHub/Slack connectors, ingestion, retrieval, runbook extraction, approvals, audit, and tests.
- Frontend: Next.js App Router TypeScript app under `frontend/app` with Overview, Projects, Connectors, Ingest, Ask, Runbooks, Approvals, Audit, Integrations, and Settings pages.
- Storage: SQLite stores app state, raw knowledge items, generated runbooks, approvals, audit events, connector accounts, and OAuth state. ArcadeDB stores the knowledge graph through `GraphStore`.
- Graph: ArcadeDB is active. Existing graph has Project, Repository, File, Issue, PullRequest, SlackMessage, KnowledgeItem, KnowledgeChunk, Runbook, RunbookStep, Service, AgentAction, and basic edges.
- Ingestion: repository ingestion scans useful repo files plus GitHub issues/PRs when a GitHub token/OAuth account is present. Slack ingestion supports authenticated channel history and pasted uploads.
- Retrieval: `/api/ask` routes through HCAG adapter, retrieves ArcadeDB chunks, ranks evidence lexically, and generates grounded answers with LLM or deterministic fallback. Insufficient evidence is explicit.
- Runbook extraction: generates YAML/JSON runbooks from retrieved evidence and links runbooks to source knowledge.
- MCP: MCP server exposes Runbook tools and calls backend endpoints.
- Auth: source connector OAuth/token storage exists. Application login/workspace/RBAC architecture is not yet complete.
- Tests: backend tests cover evidence grounding, repository ingestion, policy, and runbook approval. Frontend has basic test/build coverage.

## Upgrade scope in this pass

- Add explicit Level 2 graph schema coverage for repository structure, code entities, workflows, commands, dependencies, environment variables, config keys, errors, labels, comments, context windows, users, and organizations.
- Add lightweight repo graph building during ingestion without replacing existing chunk ingestion.
- Add graph summary, node, edge, service, file, and trace endpoints backed by ArcadeDB.
- Add SQLite-backed ingestion jobs with job status/progress metrics.
- Add dev-mode application auth, workspace, membership, role, and RBAC tables/endpoints.
- Add Graph Summary / Repo Graph UI page using real graph counts/nodes/edges.
- Add MCP graph tools.
- Add tests for graph summary, job tracking, dev auth/workspace, and no canned answers.
- Update Makefile with `graph-check`.

## Files to touch

- `backend/app/core/database.py`
- `backend/app/core/config.py`
- `backend/app/api/routes.py`
- `backend/app/api/schemas.py`
- `backend/app/graph/base.py`
- `backend/app/graph/migrations.py`
- `backend/app/graph/arcadedb_store.py`
- `backend/app/graph/memory_graph.py`
- `backend/app/graph/schema.py`
- `backend/app/graph/graph_builder.py`
- `backend/app/graph/graph_retriever.py`
- `backend/app/ingestion/repository.py`
- `backend/app/ingestion/extractors.py`
- `backend/app/auth/app_auth.py`
- `backend/app/auth/__init__.py`
- `backend/scripts/graph_check.py`
- `frontend/components/Nav.tsx`
- `frontend/app/graph/page.tsx`
- `frontend/lib/api.ts`
- `mcp_server/server.py`
- `Makefile`
- tests under `backend/tests`

## Deferred intentionally

- Full hosted production OAuth session hardening and external identity provider deployment. The code will expose the architecture and dev login/JWT-style fallback; production secret/session management remains deployment-specific.
- Deep AST parsing for every language. This pass implements deterministic lightweight parsing for common Python, JS/TS, Docker Compose, GitHub Actions, Jenkinsfile, Markdown, and logs.
- Force-directed graph visualization. The UI will provide a professional graph explorer with counts, searchable node/edge tables, service detail, and evidence paths.
- Incremental webhook ingestion. Re-ingestion and job tracking will exist; webhooks are a future production connector enhancement.
