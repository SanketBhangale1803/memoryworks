# MemoryWorks

**The memory layer for engineering organizations — and for the AI agents working alongside them.**

MemoryWorks holds what your org already learned: every incident, decision, owner, dependency, and runbook, tied to its source. The authenticated workspace registers itself as a browser-native Model Context Provider through `document.modelContext.registerTool()`, so an AI agent about to change something can ask what this company knows **before** it acts — and report back what happened after.

Built for the [OpenAI WebMCP Challenge](https://openai.com/webmcp-challenge/). The implementation record is in [`docs/webmcp-challenge.md`](docs/webmcp-challenge.md); the deployed demo is at [orgmemory.vercel.app](https://orgmemory.vercel.app) and its live tool surface is at [`/webmcp`](https://orgmemory.vercel.app/webmcp).

| | |
|---|---|
| **Stack** | Next.js 15 · React 19 · FastAPI · Python 3.13 · SQLite + ArcadeDB |
| **WebMCP tools** | 37 in the signed-in workspace — 21 read-only, 1 ledger-append, 15 human-governed; the agent console registers the 16-tool org-operations subset |
| **Surfaces** | Web app · REST API · Python SDK · CLI · MCP server (stdio + HTTP) |
| **Tests** | 305 backend · 26 frontend · 5 SDK · CI on every push |

---

## Contents

- [The problem](#the-problem)
- [The loop this product is](#the-loop-this-product-is)
- [WebMCP: memory for browser agents](#webmcp-memory-for-browser-agents)
  - [The tool the product exists for](#the-tool-the-product-exists-for)
  - [Closing the loop](#closing-the-loop)
  - [The permission boundary](#the-permission-boundary)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Finding your way around the app](#finding-your-way-around-the-app)
- [API](#api)
- [Python SDK and CLI](#python-sdk-and-cli)
- [MCP server](#mcp-server)
- [Configuration](#configuration)
- [Production deployment](#production-deployment)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Documentation index](#documentation-index)

---

## The problem

Every engineering org already knows why its payments service failed last time. That knowledge is in a postmortem nobody reads, a Slack thread nobody can find, and one engineer's head.

So when an AI agent shows up to change something, it starts from zero — and repeats the outage you already had.

MemoryWorks is the layer that stops that. It turns company sources into a time-aware memory graph where every promoted fact cites its evidence, then exposes that graph to agents through WebMCP as a **pre-action control**, not a search box.

---

## The loop this product is

```text
agent is about to change something
   ↓
get_orgmemory_briefing        constraints, prior incidents, blast radius, verdict
   ↓
a person approves             nothing enters memory without one
   ↓
agent acts
   ↓
record_orgmemory_outcome      what it did, and whether it worked
   ↓
better briefing next time
```

That last leg is the point. Anyone can ingest the same Slack and the same repositories. What only your workspace accumulates is the record of **which context actually produced correct action here** — visible at [`/loop`](http://localhost:3000/loop), and the one asset a better model cannot copy.

---

## WebMCP: memory for browser agents

The authenticated workspace registers 21 browser-native tools. Tools register only inside the authenticated workspace, calls reuse the page's HttpOnly session cookie through the existing API client, and browser agents never receive credentials.

```text
read-only (14)                    ledger-append (1)           approval-gated (6)
──────────────────────────────    ─────────────────────────   ──────────────────────────────────
get_orgmemory_briefing            record_orgmemory_outcome    propose_orgmemory_memory
ask_orgmemory                                                 propose_orgmemory_incident
search_orgmemory                                              propose_orgmemory_decision
get_orgmemory_memory                                          propose_repository_refresh
get_orgmemory_related_memories                                resolve_orgmemory_proposal  (admin)
get_orgmemory_incidents                                       resolve_orgmemory_approval  (admin)
get_orgmemory_runbook
get_orgmemory_service_context
get_orgmemory_dependencies
get_orgmemory_decisions
inspect_orgmemory_changes
list_orgmemory_spaces
list_orgmemory_approvals
list_orgmemory_proposals
```

Implementation lives in `frontend/lib/webmcp.ts`, with lifecycle management in `frontend/hooks/useMemoryWorksWebMCP.ts` and a handler-free manifest in `frontend/lib/webmcpCatalog.ts` (so the product keeps exactly one execution path).

### The tool the product exists for

`get_orgmemory_briefing` answers an **intent** rather than a question. Every other retrieval tool wants the best passage; an intent wants the constraints it is about to violate.

```jsonc
// POST /api/briefings
// { "task": "restart the payments connection pool", "service": "payments" }
{
  "verdict": "requires_approval",
  "headline": "This changes production state for payments. Read the constraints below, then get an explicit human decision — MemoryWorks will not approve it for you.",
  "consequential_action": "restarting",
  "must_read":        [{ "memory_id": "mem_d5fd", "type": "procedure",  "subject": "payments pool exhaustion first response" }],
  "constraints":      [{ "memory_id": "mem_0110", "type": "decision",   "subject": "cap payments worker concurrency" }],
  "prior_incidents":  [{ "memory_id": "mem_018f", "type": "incident",   "subject": "payments outage: pool exhaustion" }],
  "blast_radius":     [{ "memory_id": "mem_79fc", "type": "dependency", "subject": "payments shares the PostgreSQL cluster" }],
  "requires_approval": ["This request involves restarting. A person has to agree before it happens."],
  "briefing_id": "ctx_d543"
}
```

Four properties make this safe to gate real work on, and each one was a deliberate decision:

1. **No model runs in this path.** A briefing that returns a different verdict for the same intent on two consecutive calls is not a control. Retrieval is deterministic and every line carries a memory id a person can open.
2. **Each memory appears in exactly one group.** Repeating one decision under both `must_read` and `constraints` made a single finding look like two, and cost the agent tokens to discover otherwise.
3. **An unnamed service pulls no constraints at all.** Kind-scoped retrieval is workspace-wide when unscoped, and another team's postmortem shown under "this has gone wrong before" is indistinguishable from a real warning. With no service the briefing falls back to relevance and says so in `open_questions`.
4. **`no_memory` is a distinct verdict.** "Nothing is known" and "nothing to worry about" are opposite instructions, and collapsing them is the failure that gets production restarted.

The verdict ladder is `no_memory` → `proceed` → `proceed_with_context` → `requires_approval`. Consequential intent is detected from an explicit verb list in `backend/app/memory/briefing.py` that deliberately **includes** `raise` and `bump` (changing a limit is the move behind most capacity incidents) and deliberately **excludes** `change` and `update` (they match almost any sentence and would collapse every verdict into the same one).

### Closing the loop

Serving a briefing opens a row in the outcome ledger. The agent closes it after acting, from wherever it acted:

```jsonc
// POST /api/briefings/outcome
{
  "briefing_id": "ctx_d543",
  "action": "followed_procedure",
  "outcome": "succeeded",          // succeeded | failed | partial | abandoned | unknown
  "surface": "github.com",
  "reason": "Followed the remembered first-response procedure; pool recovered without a restart."
}
```

`outcome` is a closed vocabulary validated on both sides, because reward is derived from it and one invented sixth value quietly corrupts the corpus. Both legs are written in a single call because an agent reporting back from another site gets one round trip, not two.

### The permission boundary

Three tiers, legible to an agent from the tool annotations alone:

| Tier | What it can do | Why it needs no approval — or does |
|---|---|---|
| `read-only` | Retrieve memory | Permission-trimmed **server-side** against the caller's team scope. The client check is a convenience, never the boundary. |
| `ledger-append` | Record an outcome | Writes an observation. Changes no company knowledge, so it needs no approval. |
| `approval-gated` | Propose memory, resolve decisions | The only path for a single fact into company memory, and it always runs through a person. |

A briefing never authorizes the change it describes: `requires_approval` is advice returned to the agent, and the approvals queue is where a person actually decides. Everything an agent reads is treated as data, never as instructions.

---

## Quick start

### Option A — Docker (everything at once)

Start Docker Desktop first and wait until it reports the engine is running.

```bash
git clone <this-repo> && cd orgmemory
cp .env.example .env
make orgmemory
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API + OpenAPI docs | http://localhost:8000/docs |
| WebMCP command center | http://localhost:3000/webmcp |
| Outcome loop | http://localhost:3000/loop |
| ArcadeDB console | http://localhost:2480 |
| MCP over HTTP | http://localhost:8001 |

If `docker.sock` is missing, Docker Desktop is not running yet — start it and rerun.

### Option B — Local processes (faster iteration)

Requires Python 3.13+ and Node 20+.

```bash
cp .env.example .env

# Terminal 1 — backend (creates backend/.venv on first run)
make backend

# Terminal 2 — frontend
make frontend
```

The backend reads `.env` itself via `pydantic-settings`, so you do not need to export anything. The graph-backed features need ArcadeDB; start just that container with `docker compose up -d arcadedb` and then `make arcade-init`.

### First run

1. Sign in at [`/login`](http://localhost:3000/login). With `AUTH_DEV_MODE=true` you can use the development login without configuring an OAuth provider.
2. Connect GitHub or Slack from **Connections**, or upload a document from **Add knowledge**. Ingestion returns `memory_units_created` and the new memory IDs.
3. Press **⌘K** and go anywhere: **Memory graph**, **Memories**, **Profiles**, **Approvals**, **Outcome loop**.
4. Load a sample dataset with `make demo`, or open `/webmcp` and use **Queue demo proposals** to seed a payments scenario you approve by hand.

Recording a demo? [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) has a timed 3-minute shot list and voiceover script.

---

## How it works

### The ingestion pipeline

```text
Raw company sources → ingestion → chunks → atomic memories → entity graph
→ company/project/repo/service profiles → HCAG context assembly → cited answer
```

All ingestion paths — GitHub repositories, issues, pull requests, Slack, pasted knowledge, uploaded files — store the raw item, chunk it, index the chunks, conservatively extract atomic memories, link each memory to its exact source chunks in ArcadeDB, and reconcile current-memory relationships.

Repository code is interpreted **structurally**: manifests, documented service tables, routes, configuration schemas, and docstrings can become memory, while CSS, JSX fragments, validation errors, and incomplete expressions remain evidence chunks and are never promoted as company policy.

Source provenance is enforced at ingestion. Repository evidence must match the selected project's GitHub repository; uploads and Slack messages are explicitly assigned to one project and inherit its team scope. MemoryWorks does not silently mix records from another repository or project.

### The memory model

`MemoryUnit` records carry workspace/project scope, type, subject, content, company/project/repo/service/person scope, source IDs, confidence, validity dates, and current status. Relationships:

```text
UPDATES   EXTENDS   DERIVES   CONTRADICTS   SUPPORTS   MENTIONS
BELONGS_TO   OWNED_BY   DEPENDS_ON   DECIDED_BY   VALID_FOR   INVALIDATED_BY
```

### Revisions, updates, and conflicts

The Company Brain loop is continuous, not a one-time index:

```text
source revision → memory change set → current truth reconciliation
→ affected profiles/reports/skills → governed context envelope → agent
```

Every stable source has immutable `SourceRevision` records. A changed revision produces a `MemoryChangeSet` listing added, updated, invalidated, and conflicting memories. When new evidence changes a prior memory with the same subject, MemoryWorks preserves **both** records, closes the prior validity window when appropriate, and adds an `UPDATES` or `CONTRADICTS` relationship. Removed claims are invalidated rather than silently disappearing.

Reports and briefs are versioned `Artifact` records linked to the exact source revisions, memories, and context envelope used to create them. A supporting change marks the artifact stale and creates a reviewable impact. Policies and procedures compile into versioned `SkillSpec` files for agents; a relevant memory change marks the skill stale.

### HCAG context assembly

The HCAG adapter routes questions to company-memory domains — project, policy, decision, ownership, temporal, deployment, incident — and compiles context under this invariant:

```text
authorized team scope ∩ task relevance ∩ current truth
∩ entity graph neighborhood ∩ token budget
```

Each query activates a concurrent specialist swarm: hybrid sensory retrieval, bounded graph traversal, and a current-truth historian. An immune-system critic deduplicates their authorized evidence and records contradictions; one context compiler produces the final token-bounded context.

Each answer persists a `context_activation_runs` record and a `ContextEnvelope` containing the principal, authorized teams, task, selected memory and evidence, exact compiled context, active skill specs, source version vector, token budget, expiry, and retrieval trace — so the dynamic context given to an LLM is inspectable and reproducible. See [`docs/CONTEXT_ACTIVATION_SWARM.md`](docs/CONTEXT_ACTIVATION_SWARM.md).

### Governed organizational scope

Workspaces define hierarchical teams, team membership, and project grants. Sources may be shared with one or more teams; extracted memory inherits the source visibility boundary. Owners and admins inspect the whole workspace; member retrieval is security-trimmed **before** ranking and answer generation.

The invariant is strict: **derived memory, context, briefs, and skills cannot be broader than their supporting source.**

### Why this is not RAG

RAG retrieves passages. MemoryWorks also extracts typed, scoped, temporally valid memories; connects them to entities and sources; tracks which memory updates or contradicts another; and assembles current profiles from atomic facts. Retrieval still uses the original evidence, so the graph never becomes an unsupported summary layer.

### Memory Work: from context to outcomes

Describe an outcome once; HCAG selects the authorized scope, current memories, related entities, conflicts, and exact source evidence. MemoryWorks saves a revisioned brief and produces an `agent_packet` with an approval-aware execution plan.

```text
Outcome → authorized HCAG context → source-backed work package
→ human approval for consequential actions → connected worker
→ result evidence returned to company memory
```

This complements broad AI-coworker runtimes such as [OpenWorker](https://github.com/andrewyng/openworker): the worker owns the tool-execution loop, while MemoryWorks owns organizational context, current truth, permissions, evidence, and durable outcome memory. The packet is portable through the API and MCP to any compatible worker.

---

## Repository layout

```text
backend/                FastAPI service
  app/
    api/                routes.py (all HTTP endpoints) + schemas.py
    memory/             company memory service, briefing engine, change intelligence
    outcomes/           the context → action → outcome ledger
    skills/             precedent distilled from verified runs
    retrieval/          answer pipeline, candidate generation, judging
    hcag_adapter/       context assembly and the specialist swarm
    graph/              ArcadeDB graph store
    ingestion/          repository, upload, and maintenance pipelines
    connectors/         GitHub, Slack, sync engine, custom connector runtime
    governance/         team scoping and security trimming
    auth/               sessions, API keys, OAuth, MCP OAuth
    webmcp_agent.py     live agent runner over the WebMCP tool surface
  tests/                44 test modules

frontend/               Next.js 15 app router
  app/                  32 pages across 28 route trees (workspace, webmcp, loop, graph, approvals, …)
  components/
    WorkspaceChat.tsx   the post-login surface — a chat, not a dashboard
    WebMCPDemo.tsx      the /webmcp command center and live briefing panel
    CommandMenu.tsx     ⌘K — the only navigation model
    AgentActivityLayer.tsx  observable WebMCP call trace
  lib/
    webmcp.ts           the 21 executable tool definitions
    webmcpCatalog.ts    handler-free manifest for the command center
    workspaceMap.ts     the one destination registry — 26 entries
  tests/                node:test suites

mcp_server/             standalone MCP server (stdio + streamable HTTP)
python_sdk/             typed client + `orgmemory` CLI
docs/                   architecture, security, connectors, challenge record
scripts/                demo loading, reset, graph checks
```

---

## Finding your way around the app

**One keystroke, everywhere: ⌘K.** It lists every destination in the product, ranks them as you type, and answers a typed question against company memory.

There is no second navigation model to learn. Every route is registered in `frontend/lib/workspaceMap.ts`; the command menu, the page title bar, and the tests all read that one file. Adding a route makes it reachable everywhere at once, and forgetting to register one is visible immediately because the page loses its title.

Destinations are grouped **Ask · Knowledge · Govern · Agents · Admin**. The post-login surface is the chat at `/workspace` — everything else is a satellite of it, wearing the same slim bar.

---

## API

Interactive OpenAPI docs: http://localhost:8000/docs

### Pre-action briefings and the outcome loop

```text
POST /api/briefings                      serve a briefing, open a ledger row
POST /api/briefings/outcome              close it: action + outcome in one call
POST /api/outcomes/actions               record an action against a context event
POST /api/outcomes/outcomes              record an outcome
GET  /api/outcomes/stats                 closed rate, success rate, acceptance rate
GET  /api/outcomes/export                the labelled corpus this workspace holds
GET  /api/skills/learned                 precedent distilled from verified runs
POST /api/skills/learned/{id}/retire     prune a skill by hand
```

### Memory

```text
GET  /api/memory/search                  structured, LLM-free, team-trimmed search
GET  /api/memory/units
GET  /api/memory/units/{memory_id}
GET  /api/memory/units/{memory_id}/related
GET  /api/memory/graph/summary | /nodes | /edges
GET  /api/memory/profiles/company
GET  /api/memory/profiles/project/{project_id}
GET  /api/memory/profiles/repo/{repo_id}
GET  /api/memory/profiles/service/{service_name}
GET  /api/memory/updates | /conflicts
GET  /api/memory/source-revisions | /change-sets
GET  /api/memory/artifacts               POST to save one
GET  /api/memory/skills                  POST /api/memory/skills/compile
GET  /api/memory/context/{envelope_id}
GET  /api/memory/swarm/{run_id}
POST /api/projects/{project_id}/memory/repair
```

### Ask and work

```text
POST /api/ask                            the answer pipeline
POST /api/work                           create a work package
GET  /api/work | /api/work/{work_id}
POST /api/work/{work_id}/steps/{step_id}/resolve | /complete
POST /api/execute                        hand a package to a coding agent
```

### Governance and approvals

```text
GET  /api/memory/proposals               POST to propose
POST /api/memory/proposals/{id}/resolve  admin decision
GET  /api/repository-refresh-requests    POST to request
POST /api/repository-refresh-requests/{id}/resolve
GET  /api/audit
GET  /api/workspaces/{workspace_id}/teams       POST to create
POST /api/teams/{team_id}/members
POST /api/projects/{project_id}/teams
```

### Ingestion and connectors

```text
POST /api/ingest/github | /github/all | /upload | /file | /slack
GET  /api/ingest/jobs | /api/ingest/jobs/{job_id}
GET  /api/connectors | /catalog | /coverage
POST /api/connectors/{provider}/sync
POST /api/webhooks/github | /api/webhooks/slack
```

Ask responses contain `answer`, `memory_profile_used`, `confidence`, `memory_units`, `evidence`, `related_entities`, `updates`, `conflicts`, `retrieval_trace`, and the persisted `context_envelope`. Answers derive from authorized retrieved evidence; when evidence is insufficient, MemoryWorks abstains.

### Webhooks

Verified GitHub webhooks trigger incremental repository reconciliation — chunks, atomic memories, updates, conflicts, profiles, and HCAG retrieval state. Once a Slack channel is connected, verified message events add, update, or retire the corresponding project memories. Set `GITHUB_WEBHOOK_SECRET` and `SLACK_SIGNING_SECRET`, and point the providers at `/api/webhooks/github` and `/api/webhooks/slack`.

GitHub sign-in and the GitHub connector both use the exact callback registered on the OAuth App: `https://<your-host>/api/auth/github/callback`. On hosted deployments, MemoryWorks derives that callback from the public request origin and carries it through the signed OAuth state, so a local `GITHUB_REDIRECT_URI` default cannot leak into the production authorization request. Set `PUBLIC_BASE_URL` to pin a canonical production origin when the deployment is reachable through more than one hostname.

---

## Python SDK and CLI

```bash
make sdk-install
```

The client reads `ORGMEMORY_API_URL` and `ORGMEMORY_API_KEY`, or takes them explicitly:

```python
from orgmemory import MemoryWorks

memory = MemoryWorks(base_url="http://localhost:8000", api_key="om_live_...")

context = memory.ask(
    "prj_platform",
    "What changed in checkout, and why?",
    model="claude",
)
print(context.answer)
print(context.compiled_context)
```

`AsyncMemoryWorks` is available for async applications. The same package ships an operational CLI:

```bash
orgmemory health
orgmemory projects
orgmemory ask prj_platform "What changed in checkout, and why?" --model claude
orgmemory memories prj_platform
orgmemory graph prj_platform
orgmemory swarm swarm_01J... --json
```

The public developer guide is served at http://localhost:3000/docs.

---

## MCP server

Separate from the in-page WebMCP surface, MemoryWorks ships a standalone MCP server for Claude, ChatGPT, and other MCP clients.

```bash
make mcp        # stdio
make mcp-http   # streamable HTTP on :8001, with OAuth
```

```text
get_orgmemory_briefing                 record_orgmemory_outcome
orgmemory_ask                          orgmemory_get_memory_graph
orgmemory_search_memories              orgmemory_list_memory_conflicts
orgmemory_get_company_profile          orgmemory_list_memory_updates
orgmemory_get_project_profile          orgmemory_list_source_revisions
orgmemory_get_service_profile          orgmemory_list_change_sets
orgmemory_list_skills                  orgmemory_list_work
orgmemory_get_work                     orgmemory_request_connector_action
orgmemory_list_connector_action_requests
```

Register a client from **⌘K → MCP & integrations**. Legacy `runbook_*` tools are
available only when `ORGMEMORY_ENABLE_LEGACY_TOOLS=true`. See
[`docs/MCP.md`](docs/MCP.md).

---

## Configuration

Copy `.env.example` to `.env`. The backend loads it directly, so nothing needs exporting.

### Models — at least one key required

```env
OPENROUTER_API_KEY=                    # GLM 5.3 Flash through OpenRouter
GLM_MODEL=z-ai/glm-5.3-flash
GLM_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
XAI_API_KEY=
KIMI_API_KEY=
ORG_MEMORY_DEFAULT_MODEL_PROVIDER=glm
```

Every model answers from the same retrieved company memory — the provider changes the writer, never the evidence.

### Answer pipeline

```env
ORG_MEMORY_ANSWER_CANDIDATES=5          # independent candidates per question; 1 disables the judge
ORG_MEMORY_ANSWER_JUDGE_ENABLED=true
ORG_MEMORY_GENERAL_KNOWLEDGE_ENABLED=true   # answer from model knowledge when memory holds nothing
```

### Auth and sessions

```env
AUTH_DEV_MODE=true                      # development login without an OAuth provider
JWT_SECRET=
NEXTAUTH_SECRET=
ORGMEMORY_SESSION_COOKIE_NAME=
```

The hosted challenge profile uses `PUBLIC_DEMO_MODE=true`. Its Google, GitHub, and guest choices are isolated demo personas—not external OAuth grants—and its signed, `HttpOnly`, `Secure`, `SameSite=Lax` session cookie is stateless so authentication survives Vercel container changes. Normal production installations use real OAuth or email sessions and should use durable shared storage.

GitHub, Google, Slack, and passwordless email setup are documented in [`docs/OAUTH_SETUP.md`](docs/OAUTH_SETUP.md).

### Graph and retrieval

```env
GRAPH_BACKEND=arcadedb
ARCADEDB_HOST=localhost
ARCADEDB_PORT=2480
ORGMEMORY_EMBEDDING_PROVIDER=deterministic
ORGMEMORY_RERANKER_PROVIDER=deterministic
```

### Webhooks and feature flags

```env
GITHUB_WEBHOOK_SECRET=
SLACK_SIGNING_SECRET=
ORG_MEMORY_ENABLE_ACTIONS=false
ORG_MEMORY_ENABLE_PROCEDURES=false
ORG_MEMORY_ENABLE_ADVANCED_RELIABILITY=false
```

Secrets are encrypted at rest, source access is workspace-scoped, and API keys are workspace-bound. See [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Production deployment

The public demo is a Vercel Services project: Next.js serves the web surface and the FastAPI container serves same-origin `/api/*` requests. Production requires the values in `.env.production.example`, plus a sensitive `JWT_SECRET` and `OPENROUTER_API_KEY`. Environment-variable changes only take effect after a new deployment.

This repository intentionally has no GitHub Actions or Dependabot update workflow. Run the full verification locally before publishing:

```bash
make ci
```

The `orgmemory` Vercel project is connected to this GitHub repository. A push or merged pull request to `main` automatically creates the production deployment; pushes to other branches create preview deployments. This Vercel build hook is separate from GitHub Actions, which remains disabled. CLI deployments remain available with `vercel --prod`.

---

## Testing

```bash
make test          # backend pytest + frontend node:test + SDK
make lint          # ruff + black + tsc --noEmit
make ci            # test + lint + next build
make sdk-test
npm --prefix frontend test
```

Current state:

| Suite | Result |
|---|---|
| Backend (`pytest backend`) | 300 passed, 1 skipped (deliberate live-LLM contract test) |
| Frontend (`node:test`) | 23 passed |
| Python SDK | 5 passed |
| `ruff` / `black` / `tsc` | clean |
| `next build` | succeeds |

One backend test — `test_webmcp_memory_api.py::test_memory_proposal_stays_pending_until_an_admin_approves` — requires ArcadeDB on `:2480` and fails with `ConnectError` when the container is not running. Start it with `docker compose up -d arcadedb`.

Grounding tests verify that the same question over different sources produces different answers, that confident answers require evidence, that insufficient evidence abstains, and that no service-specific canned response exists. The briefing suite (`backend/tests/test_briefing.py`) pins the properties an agent depends on: verdict stability for a repeated intent, no memory cited twice, a named service never borrowing another service's history, and `no_memory` never being reported as safe.

---

## Troubleshooting

**`Cannot find module './331.js'` in the browser, or a Next.js runtime error page.**
`npm run build` was run while `next dev` was live — the production build overwrites `.next` underneath the dev server. Fix:

```bash
pkill -f "next dev" && rm -rf frontend/.next && make frontend
```

**`ConnectError: [Errno 61] Connection refused` in backend tests or on graph pages.**
ArcadeDB is not running. `docker compose up -d arcadedb`, then `make arcade-init` on first setup.

**`Address already in use` on :8000 or :3000.**
An earlier process is still bound. `pkill -f "uvicorn app.main:app"` or `pkill -f "next dev"`, then restart.

**"Cannot reach the MemoryWorks API at http://localhost:8000".**
The backend is down, or you are browsing on `127.0.0.1`. The app canonicalises to `localhost` via `frontend/middleware.ts` so OAuth callbacks and credentialed requests do not cross a site boundary — use `localhost`.

**`.env` parse errors when sourcing it in a shell.**
Some values contain characters your shell will try to interpret. Don't `source .env` — the backend reads it directly through `pydantic-settings`.

**Briefing returns `no_memory` for a service you know exists.**
Service matching is against `scope.service`, the subject, and the body. If the extractor never tagged the service, name it explicitly in the `service` parameter rather than relying on inference from the task text.

---

## Known limitations

- Extraction is deliberately conservative and primarily deterministic. GPT, Claude, Gemini, Grok, or Kimi may synthesize answers but cannot promote unsupported memory.
- GitHub, Slack, uploads (PDF/DOCX/XLSX/PPTX/ODT/RTF/HTML/EML), websites, Notion, Google Drive, Microsoft Teams, custom REST API sources, API, Python, CLI, and MCP are live. Gmail, Microsoft 365 (SharePoint/OneDrive), Outlook, and Atlassian remain adapter work; any JSON-over-HTTPS platform can already be ingested through a custom REST source.
- Conflict matching relies on normalized subjects; entity-assisted and model-assisted reconciliation would improve it.
- Profiles are assembled on read from current memories rather than materialized incrementally.
- Team grants are project- and source-scoped. Field-level classification and external identity-group sync are not implemented.
- The briefing verb list is explicit and inspectable, which also means it is not exhaustive — an unusual phrasing for a consequential change can return `proceed_with_context` instead of `requires_approval`.
- Legacy advanced routes (simulation, reliability review, procedure extraction) remain in the codebase behind feature flags and are not part of default navigation.

---

## Documentation index

| Document | Covers |
|---|---|
| [`docs/webmcp-challenge.md`](docs/webmcp-challenge.md) | The WebMCP Challenge implementation record, phase by phase |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | Timed 3-minute demo shot list and voiceover script |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture |
| [`docs/COMPANY_BRAIN_ARCHITECTURE.md`](docs/COMPANY_BRAIN_ARCHITECTURE.md) | The continuous memory loop |
| [`docs/HCAG_MEMORY_ARCHITECTURE.md`](docs/HCAG_MEMORY_ARCHITECTURE.md) | Context assembly engine |
| [`docs/CONTEXT_ACTIVATION_SWARM.md`](docs/CONTEXT_ACTIVATION_SWARM.md) | The specialist swarm and context envelopes |
| [`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md) | Nodes, edges, and traversal |
| [`docs/MEMORY_WORK.md`](docs/MEMORY_WORK.md) | Work packages and agent packets |
| [`docs/APPROVALS.md`](docs/APPROVALS.md) | The human decision boundary |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Scoping, secrets, and trust boundaries |
| [`docs/CONNECTORS.md`](docs/CONNECTORS.md) | Connector platform and custom connectors |
| [`docs/MCP.md`](docs/MCP.md) | Standalone MCP server |
| [`docs/OAUTH_SETUP.md`](docs/OAUTH_SETUP.md) | GitHub, Google, Slack, email auth |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Retrieval evaluation |
| [`docs/STARTUP_EXECUTION_PLAN.md`](docs/STARTUP_EXECUTION_PLAN.md) | 90-day customer validation, pilot, metrics, and YC plan |
| [`docs/HARNESS_EXECUTION.md`](docs/HARNESS_EXECUTION.md) | Reusable execution skill, visual blueprint, and resumable checkpoints |

---

## License

MemoryWorks is released under the [MIT License](LICENSE).

---

## Product boundary

MemoryWorks is organization-specific rather than a generic memory API: company, project, repository, service, and person profiles; temporal validity; conflicts; permission-aware retrieval; an inspectable company graph; and approval-aware work. The web app is the starting point — channels, IDEs, SDKs, CLI, and MCP are delivery surfaces for the same governed brain.

## Migration note

Runbook was the previous product direction. MemoryWorks is focused on the company brain rather than operational runbooks. Legacy database table names and environment variables (`RUNBOOK_*`) remain temporarily for migration compatibility.
