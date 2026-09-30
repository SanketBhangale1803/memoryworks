# MemoryWorks

**The memory layer for engineering organizations — and for the AI agents working alongside them.**

MemoryWorks holds what your organization already learned — every incident, decision, owner, and dependency — tied to its source. People ask it questions in one chat window. Agents get briefed by it **before** they change something, and report back what happened after.

Live at **[memoryworks.app](https://memoryworks.app)**.

| | |
|---|---|
| **Stack** | Next.js 15 · React 19 · FastAPI · Python 3.13 · SQLite + ArcadeDB |
| **Surfaces** | Web app · REST API · Python SDK · CLI · MCP server (stdio + HTTP) · in-browser agent tools |
| **Tests** | 335 backend · 30 frontend · 8 SDK · 4 MCP server |

---

## Contents

- [The problem](#the-problem)
- [Using MemoryWorks](#using-memoryworks)
- [The loop this product is](#the-loop-this-product-is)
- [Agents: briefings, outcomes, and the approval boundary](#agents-briefings-outcomes-and-the-approval-boundary)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
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

MemoryWorks turns company sources into a time-aware memory graph where every promoted fact cites its evidence, then puts that memory in front of people and agents at the moment they are about to act — as a **pre-action control**, not a search box.

---

## Using MemoryWorks

After sign-in, everything happens in **one window**, laid out like an editor:

```text
┌──────────────┬───────────────────────────────────────────────┐
│ + New chat   │  What changed in payments this week?          │
│ ⌕ Search  ⌘K │                                               │
│ Sources      │  • Retries on the card processor are capped…  │
│ Memory       │  • Ownership moved to the Checkout team…      │
│ Approvals  2 │  2 sources ▾   Did this work?  Yes  No        │
│              │                                               │
│ Chats        │                                               │
│  Payments…   │                                               │
│  Who owns…   │  ┌─────────────────────────────────────────┐  │
│              │  │ Ask a follow-up…                        │  │
│ Getting      │  │ [Ask ▾] [All memory ▾]        [GLM ▾] ↑ │  │
│ started 2/3  │  └─────────────────────────────────────────┘  │
└──────────────┴───────────────────────────────────────────────┘
```

- **Ask** answers from your company's memory with its sources. If memory holds nothing, it says so rather than guess.
- **Agent** works across every memory space step by step — reading, cross-checking owners, blockers, and contradictions. The steps fold into "Worked through N steps"; cited memories open their evidence. When it wants to change something, the proposed change appears in the conversation and **waits for you to approve or decline**.
- **Sources** connects GitHub, Slack, Google Drive, Notion, and more, and holds uploads, sync status, AI-tool setup, and API keys as tabs.
- **Memory** is what MemoryWorks knows — memories, graph, profiles, spaces, conflicts.
- **Approvals** is one inbox for everything waiting on a person.
- **⌘K** reaches every page and answers a typed question from anywhere.

**Signing in with GitHub also connects your repositories.** The sign-in consent includes repository access, and the grant is stored as the workspace's GitHub connection, so the next step after sign-in is choosing which repositories to remember — there is no second "Connect GitHub".

Every page is registered once in `frontend/lib/workspaceMap.ts`; the sidebar, the command menu, the page title bar, and the hub tabs all read that one file.

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

That last leg is the point. Anyone can ingest the same Slack and the same repositories. What only your workspace accumulates is the record of **which context actually produced correct action here** — visible under **What worked** (`/loop`), and the one asset a better model cannot copy.

---

## Agents: briefings, outcomes, and the approval boundary

Agents reach MemoryWorks three ways, all through the same authorized API:

| Where the agent runs | How it connects |
|---|---|
| Claude Code, Cursor, VS Code, Claude, ChatGPT | The [MCP server](#mcp-server) — set up from **Sources → AI tools** |
| A browser, on a signed-in MemoryWorks tab | The chat page registers its tools with the browser (`document.modelContext`), reusing the page's session cookie — the agent never receives credentials |
| Your own code | The [REST API](#api), [Python SDK, or CLI](#python-sdk-and-cli) |

The chat page registers 36 tools: 20 memory tools (13 read-only, 1 ledger-append, 6 approval-gated) and 16 cross-space operations (13 read-only, 3 that can only draft a plan). Definitions live in `frontend/lib/webmcp.ts` and `frontend/lib/orgTools.ts`; `frontend/hooks/useWorkspaceTools.ts` registers them.

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
3. **An unnamed service pulls no constraints at all.** Another team's postmortem shown under "this has gone wrong before" is indistinguishable from a real warning. With no service the briefing falls back to relevance and says so in `open_questions`.
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

`outcome` is a closed vocabulary validated on both sides, because reward is derived from it and one invented sixth value quietly corrupts the corpus. In the chat, "Did this work?" and copying a handoff prompt feed the same ledger.

### The permission boundary

| Tier | What it can do | Why it needs no approval — or does |
|---|---|---|
| `read-only` | Retrieve memory | Permission-trimmed **server-side** against the caller's team scope. The client check is a convenience, never the boundary. |
| `ledger-append` | Record an outcome | Writes an observation. Changes no company knowledge, so it needs no approval. |
| `approval-gated` | Propose memory, draft a change plan, request a refresh | Always stops at a person. There is deliberately no tool that approves — approving is done in **Approvals** or inline in the chat, by a person. |

A briefing never authorizes the change it describes: `requires_approval` is advice returned to the agent. Everything an agent reads is treated as data, never as instructions.

---

## Quick start

### Option A — Docker (everything at once)

Start Docker Desktop first and wait until it reports the engine is running.

```bash
git clone https://github.com/SanketBhangale1803/orgmemory.git && cd orgmemory
cp .env.example .env
make memoryworks        # `make orgmemory` and `make runbook` still work as aliases
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API + OpenAPI docs | http://localhost:8000/docs |
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

The backend reads `.env` itself via `pydantic-settings`, so nothing needs exporting. Graph features use ArcadeDB by default; start just that container with `docker compose up -d arcadedb` and then `make arcade-init`, or set `GRAPH_BACKEND=memory` for the in-process graph.

### First run

1. Sign in at [`/login`](http://localhost:3000/login). With `AUTH_DEV_MODE=true` a development login works without an OAuth provider.
2. The chat opens with the next step: **Connect GitHub** (or **Choose repositories**, if you signed in with GitHub), **Upload documents**, **Paste something**, or another source.
3. Ask a question. Switch the composer to **Agent** for multi-step work across every space.
4. Load a sample dataset with `make demo` if you want something to explore first.

---

## How it works

### The ingestion pipeline

```text
Raw company sources → ingestion → chunks → atomic memories → entity graph
→ company/project/repo/service profiles → HCAG context assembly → cited answer
```

All ingestion paths — GitHub repositories, issues, pull requests, Slack, pasted knowledge, uploaded files, web pages — store the raw item, chunk it, index the chunks, conservatively extract atomic memories, link each memory to its exact source chunks, and reconcile current-memory relationships.

Repository code is interpreted **structurally**: manifests, documented service tables, routes, configuration schemas, and docstrings can become memory, while CSS, JSX fragments, validation errors, and incomplete expressions remain evidence chunks and are never promoted as company policy.

Source provenance is enforced at ingestion. Repository evidence must match the selected project's GitHub repository; uploads and Slack messages are explicitly assigned to one memory space and inherit its team scope. MemoryWorks does not silently mix records from another repository or project.

### The memory model

`MemoryUnit` records carry workspace/project scope, type, subject, content, company/project/repo/service/person scope, source IDs, confidence, validity dates, and current status. Relationships:

```text
UPDATES   EXTENDS   DERIVES   CONTRADICTS   SUPPORTS   MENTIONS
BELONGS_TO   OWNED_BY   DEPENDS_ON   DECIDED_BY   VALID_FOR   INVALIDATED_BY
```

### Revisions, updates, and conflicts

Memory is continuous, not a one-time index:

```text
source revision → memory change set → current truth reconciliation
→ affected profiles/reports/skills → governed context envelope → agent
```

Every stable source has immutable `SourceRevision` records. A changed revision produces a `MemoryChangeSet` listing added, updated, invalidated, and conflicting memories. When new evidence changes a prior memory with the same subject, MemoryWorks preserves **both** records, closes the prior validity window when appropriate, and adds an `UPDATES` or `CONTRADICTS` relationship. Removed claims are invalidated rather than silently disappearing. Disagreements surface under **Memory → Conflicts** and in answers that rely on them.

Reports and briefs are versioned `Artifact` records linked to the exact source revisions, memories, and context envelope used to create them. Policies and procedures compile into versioned `SkillSpec` files for agents; a relevant memory change marks the skill stale.

### HCAG context assembly

The HCAG adapter routes questions to company-memory domains — project, policy, decision, ownership, temporal, deployment, incident — and compiles context under this invariant:

```text
authorized team scope ∩ task relevance ∩ current truth
∩ entity graph neighborhood ∩ token budget
```

Each query activates a concurrent specialist swarm: hybrid sensory retrieval, bounded graph traversal, and a current-truth historian. A critic deduplicates their authorized evidence and records contradictions; one context compiler produces the final token-bounded context.

Each answer persists a `ContextEnvelope` containing the principal, authorized teams, task, selected memory and evidence, exact compiled context, active skill specs, source version vector, token budget, expiry, and retrieval trace — so the context given to a model is inspectable and reproducible. See [`docs/CONTEXT_ACTIVATION_SWARM.md`](docs/CONTEXT_ACTIVATION_SWARM.md).

### Answer lanes

`/api/ask` answers in one of three lanes, reported as `answer_scope`: `company_memory` (from retrieved evidence), `general_knowledge` (memory held nothing and the question is not about the company — labelled as such in the chat), and `assistant` (greetings and "what are you", answered without retrieval). Free-form company questions are synthesized as several candidates under different readings of the question, and a judge picks the best — every candidate is held to the same evidence contract.

### Governed organizational scope

Workspaces define hierarchical teams, team membership, and project grants. Sources may be shared with one or more teams; extracted memory inherits the source visibility boundary. Owners and admins see the whole workspace; member retrieval is security-trimmed **before** ranking and answer generation.

The invariant is strict: **derived memory, context, briefs, and skills cannot be broader than their supporting source.**

### Why this is not RAG

RAG retrieves passages. MemoryWorks also extracts typed, scoped, temporally valid memories; connects them to entities and sources; tracks which memory updates or contradicts another; and assembles current profiles from atomic facts. Retrieval still uses the original evidence, so the graph never becomes an unsupported summary layer.

### Handing work to an agent

When a question is really a code change and the evidence locates the files, the answer carries a **handoff**: the task, the files, the steps, trimmed context, and a prompt to paste into Cursor, Copilot, or Claude Code — or **Do it for me**, which runs a coding agent on a branch and reports the branch, commit, and diff. **Hand off work** (`/work`) builds the same kind of source-backed brief from an outcome you describe. See [`docs/MEMORY_WORK.md`](docs/MEMORY_WORK.md).

---

## Repository layout

```text
backend/                FastAPI service
  app/
    api/                routes.py and org_routes.py (all HTTP endpoints) + schemas.py
    memory/             company memory service, briefing engine, change sets
    orgops/             cross-space operations: context, blockers, conflicts, plans
    outcomes/           the context → action → outcome ledger
    skills/             precedent distilled from verified runs
    retrieval/          answer pipeline, candidate generation, judging
    hcag_adapter/       context assembly and the specialist swarm
    graph/              ArcadeDB and in-memory graph stores
    ingestion/          repository, upload, web, and maintenance pipelines
    connectors/         GitHub, Slack, Drive, Notion, Teams, sync engine, custom connectors
    governance/         team scoping and security trimming
    auth/               sessions, API keys, OAuth, MCP OAuth, token vault
    webmcp_agent.py     the model-driven agent behind Agent mode
  tests/                51 test modules

frontend/               Next.js 15 app router
  app/                  20 routes — landing, docs, login, and the signed-in pages
  components/
    WorkspaceFrame.tsx  the sidebar: places, chats, getting started
    WorkspaceChat.tsx   the one window: Ask and Agent modes
    AgentTurn.tsx       agent steps, citations, and inline plan approval
    PageBar.tsx         title bar and hub tabs on every other page
    CommandMenu.tsx     ⌘K
  hooks/
    useWorkspaceTools.ts  registers the in-browser agent tools
  lib/
    workspaceMap.ts     the one destination registry
    threads.ts          chat history (per workspace, in the browser)
    webmcp.ts           memory tool definitions
    orgTools.ts         cross-space operation tools and API client
  tests/                node:test suites

mcp_server/             standalone MCP server (stdio + streamable HTTP)
python_sdk/             typed client + CLI (import path `orgmemory`)
desktop/                Tauri desktop shell
docs/                   architecture, security, connectors, operations
scripts/                demo loading, reset, graph checks
```

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

### Ask, agent, and work

```text
POST /api/ask                            the answer pipeline (Ask mode)
POST /api/org/ask/stream                 a model-driven agent session, streamed as NDJSON (Agent mode)
POST /api/org/followups                  suggested next questions from what a turn found
GET  /api/org/plans                      change plans drafted by agents
POST /api/org/plans/{id}/approve | /reject
POST /api/work                           create a work package
GET  /api/work | /api/work/{work_id}
POST /api/execute                        hand a package to a coding agent
```

The full `/api/org/*` surface (spaces, context, readiness, blockers, conflicts, reasoning chains, provenance, people, owners) is documented at [memoryworks.app/docs#org-api](https://memoryworks.app/docs#org-api).

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
POST /api/projects/{project_id}/memory/repair
```

### Governance and approvals

```text
GET  /api/memory/proposals               POST to propose
POST /api/memory/proposals/{id}/resolve  admin decision
GET  /api/repository-refresh-requests    POST to request
POST /api/repository-refresh-requests/{id}/resolve
GET  /api/connector-tool-calls           POST /{id}/resolve
GET  /api/audit
GET  /api/workspaces/{workspace_id}/teams       POST to create
POST /api/teams/{team_id}/members
POST /api/projects/{project_id}/teams
```

### Ingestion and connectors

```text
POST /api/ingest/github | /github/all | /upload | /file | /website | /slack
GET  /api/ingest/jobs | /api/ingest/jobs/{job_id}
GET  /api/connectors | /catalog | /coverage
GET  /api/connectors/{provider}/auth/start
POST /api/connectors/{provider}/sync
POST /api/webhooks/github | /api/webhooks/slack
```

Ask responses contain `answer`, `answer_scope`, `confidence`, `memory_units`, `evidence`, `related_entities`, `updates`, `conflicts`, `retrieval_trace`, and the persisted `context_envelope`. When evidence is insufficient, MemoryWorks abstains.

### Webhooks

Verified GitHub webhooks trigger incremental repository reconciliation — chunks, atomic memories, updates, conflicts, profiles, and retrieval state. Once a Slack channel is connected, verified message events add, update, or retire the corresponding memories. Set `GITHUB_WEBHOOK_SECRET` and `SLACK_SIGNING_SECRET`, and point the providers at `/api/webhooks/github` and `/api/webhooks/slack`.

---

## Python SDK and CLI

```bash
make sdk-install
```

The client reads `ORGMEMORY_API_URL` and `ORGMEMORY_API_KEY`, or takes them explicitly:

```python
from orgmemory import MemoryWorks

memory = MemoryWorks(base_url="https://memoryworks.app", api_key="om_live_...")

context = memory.ask(
    "prj_platform",
    "What changed in checkout, and why?",
    model="claude",
)
print(context.answer)
print(context.compiled_context)
```

`AsyncMemoryWorks` is available for async applications. The import path stays `orgmemory`, and the pre-rename names `OrgMemory` / `AsyncOrgMemory` remain importable, so existing code keeps working. The package ships a CLI:

```bash
orgmemory health
orgmemory projects
orgmemory ask prj_platform "What changed in checkout, and why?" --model claude
orgmemory memories prj_platform
orgmemory graph prj_platform
orgmemory swarm swarm_01J... --json
```

The public developer guide is at [memoryworks.app/docs](https://memoryworks.app/docs).

---

## MCP server

MemoryWorks ships a standalone MCP server for Claude Code, Cursor, VS Code, Claude, ChatGPT, and other MCP clients. The signed-in app generates the exact configuration for each client under **Sources → AI tools**.

```bash
make mcp        # stdio
make mcp-http   # streamable HTTP on :8001, with OAuth
```

The Vercel deployment at memoryworks.app does not run the MCP server; point the stdio bridge at it with an API key.

```text
get_orgmemory_briefing                 record_orgmemory_outcome
orgmemory_ingest_github_repo           orgmemory_ingest_slack_channel
orgmemory_upload_source
orgmemory_ask                          orgmemory_get_memory_graph
orgmemory_search_memories              orgmemory_list_memory_conflicts
orgmemory_get_company_profile          orgmemory_list_memory_updates
orgmemory_get_project_profile          orgmemory_list_source_revisions
orgmemory_get_service_profile          orgmemory_list_change_sets
orgmemory_list_skills                  orgmemory_compile_skill
orgmemory_create_work                  orgmemory_list_work
orgmemory_get_work                     orgmemory_resolve_work_step
orgmemory_complete_work_step           orgmemory_request_connector_action
orgmemory_list_connector_action_requests
```

Tool names keep the `orgmemory` prefix because MCP clients and agent prompts call them by name. Compatibility tools from the product's earlier direction stay hidden unless `ORGMEMORY_ENABLE_LEGACY_TOOLS=true`. The server reads `MEMORYWORKS_API_URL`, `MEMORYWORKS_API_KEY`, and `MEMORYWORKS_MCP_*`; the older `ORGMEMORY_*` and `RUNBOOK_*` names still work with a deprecation warning. See [`docs/MCP.md`](docs/MCP.md).

---

## Configuration

Copy `.env.example` to `.env`. The backend loads it directly, so nothing needs exporting.

### Models — at least one key required

```env
OPENROUTER_API_KEY=                    # GLM through OpenRouter
GLM_MODEL=z-ai/glm-5.3-flash
GLM_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
XAI_API_KEY=
KIMI_API_KEY=
ORG_MEMORY_DEFAULT_MODEL_PROVIDER=glm
```

Every model answers from the same retrieved company memory — the provider changes the writer, never the evidence. With no key at all, Agent mode falls back to a deterministic policy that still calls the real tools.

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
GITHUB_CLIENT_ID=                       # sign-in and the GitHub connection use the same OAuth App
GITHUB_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
PUBLIC_BASE_URL=                        # pin the canonical origin, e.g. https://memoryworks.app
```

GitHub sign-in requests `repo read:org read:user user:email`, and a grant that includes `repo` becomes the signed-in person's GitHub connection. OAuth callbacks are `https://<host>/api/auth/github/callback` and `https://<host>/api/auth/google/callback`. Full setup, including Slack and passwordless email, is in [`docs/OAUTH_SETUP.md`](docs/OAUTH_SETUP.md).

### Graph and retrieval

```env
GRAPH_BACKEND=arcadedb                  # or memory, for the in-process graph
ARCADEDB_HOST=localhost
ARCADEDB_PORT=2480
ORGMEMORY_EMBEDDING_PROVIDER=deterministic
ORGMEMORY_RERANKER_PROVIDER=deterministic
```

### Webhooks

```env
GITHUB_WEBHOOK_SECRET=
SLACK_SIGNING_SECRET=
```

Secrets are encrypted at rest, source access is workspace-scoped, and API keys are workspace-bound. See [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Production deployment

Production runs at **https://memoryworks.app** as a Vercel Services project (`orgmemory`): Next.js serves the web app and a FastAPI container serves same-origin `/api/*`, so session cookies stay first-party.

- A push to `main` creates the production deployment; other branches create preview deployments (behind Vercel login).
- **Storage on Vercel is not durable.** The backend container keeps SQLite under `/tmp/orgmemory/` and uses the in-memory graph, and Vercel containers are stateless, so workspace data can reset when a new container starts. For durable data, run the single-VM deployment in [`deploy/oci/`](deploy/oci/README.md) (Caddy, ArcadeDB, persistent volumes).
- Production needs the values in `.env.production.example`, plus a sensitive `JWT_SECRET` and a model key. Environment changes take effect on the next deployment.
- The URL settings — `FRONTEND_URL`, `PUBLIC_BASE_URL`, `APP_BASE_URL`, `API_URL`, `MCP_OAUTH_ISSUER_URL`, `NEXT_PUBLIC_SITE_URL` — are `https://memoryworks.app`.
- The MCP server does not run on Vercel. Editors connect to memoryworks.app through the stdio bridge with an API key; HTTP MCP with OAuth needs the Docker `mcp` profile or the single-VM deployment.

### The domain

`memoryworks.app` is registered at Squarespace, which keeps serving its DNS. Two custom records point it at Vercel (the Squarespace default records were deleted):

| Host | Type | Data |
|---|---|---|
| `@` | A | `76.76.21.21` |
| `www` | CNAME | `cname.vercel-dns.com` |

Vercel issues the HTTPS certificate (Let's Encrypt) automatically once DNS resolves; `npx vercel certs issue memoryworks.app www.memoryworks.app` requests it immediately. Check the setup with `npx vercel domains inspect memoryworks.app` and `dig +short A memoryworks.app @1.1.1.1`.

Run the full verification locally before publishing:

```bash
make ci
```

---

## Testing

```bash
make test          # backend pytest + frontend node:test + SDK
make lint          # ruff + black + tsc --noEmit
make ci            # test + lint + next build
make sdk-test
npm --prefix frontend test
```

| Suite | Result |
|---|---|
| Backend (`pytest backend`) | 335 passed, 1 skipped (deliberate live-LLM contract test) |
| Frontend (`node:test`) | 30 passed |
| Python SDK | 8 passed |
| MCP server | 4 passed |
| `ruff` / `black` / `tsc` | clean |
| `next build` | succeeds |

Tests read the repository-root `.env`. If a test passes in a clean checkout but fails locally, compare your `.env` against `.env.example` — local settings can change retrieval behaviour.

The frontend suite also guards the product itself: no retired page may come back, and no page may name an internal transport, the old brand, or runbooks. Grounding tests verify that the same question over different sources produces different answers, that confident answers require evidence, that insufficient evidence abstains, and that no service-specific canned response exists. The briefing suite (`backend/tests/test_briefing.py`) pins verdict stability for a repeated intent, no memory cited twice, a named service never borrowing another service's history, and `no_memory` never being reported as safe.

---

## Troubleshooting

**"Application error" or `ChunkLoadError` in the browser during development.**
`npm run build` was run while `next dev` was live — the production build overwrites `.next` underneath the dev server. Fix:

```bash
pkill -f "next dev" && rm -rf frontend/.next && make frontend
```

**`ConnectError: [Errno 61] Connection refused` on graph pages.**
ArcadeDB is not running. `docker compose up -d arcadedb`, then `make arcade-init` on first setup — or set `GRAPH_BACKEND=memory`.

**`Address already in use` on :8000 or :3000.**
An earlier process is still bound. `pkill -f "uvicorn app.main:app"` or `pkill -f "next dev"`, then restart.

**"Cannot reach the MemoryWorks API at http://localhost:8000".**
The backend is down, or you are browsing on `127.0.0.1`. `next.config.ts` redirects `127.0.0.1` to `localhost` so OAuth callbacks and credentialed requests stay on one site — use `localhost`.

**`bad interpreter: …/runbook/backend/.venv/…` when running `uvicorn` directly.**
The virtualenv was created when the repository lived at an older path. Use `backend/.venv/bin/python -m uvicorn …`, or recreate the venv.

**GitHub sign-in fails with a redirect mismatch.**
The GitHub OAuth App's callback must be exactly `https://<host>/api/auth/github/callback`, and `PUBLIC_BASE_URL` must name the same host.

**The site still shows the Squarespace page after a DNS change.**
Your network's resolver is caching the old record (up to 4 hours). `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder` clears the Mac's own cache; a router or ISP cache clears on its own. `dig +short A memoryworks.app @1.1.1.1` shows what the rest of the internet sees.

**`.env` parse errors when sourcing it in a shell.**
Don't `source .env` — the backend reads it directly through `pydantic-settings`.

**Briefing returns `no_memory` for a service you know exists.**
Service matching is against `scope.service`, the subject, and the body. If the extractor never tagged the service, name it explicitly in the `service` parameter.

---

## Known limitations

- Extraction is deliberately conservative and primarily deterministic. Models may synthesize answers but cannot promote unsupported memory.
- GitHub, Slack, uploads (PDF/DOCX/XLSX/PPTX/ODT/RTF/HTML/EML), websites, Notion, Google Drive, Microsoft Teams, custom REST API sources, API, Python, CLI, and MCP are live. Gmail, Microsoft 365 (SharePoint/OneDrive), Outlook, and Atlassian remain adapter work.
- Chat history is stored in the browser (per workspace), not on the server, so it does not follow you to another device yet.
- The Vercel deployment at memoryworks.app has no durable storage yet and does not host the MCP server (see [Production deployment](#production-deployment)).
- Conflict matching relies on normalized subjects; entity-assisted and model-assisted reconciliation would improve it.
- Profiles are assembled on read from current memories rather than materialized incrementally.
- Team grants are project- and source-scoped. Field-level classification and external identity-group sync are not implemented.
- The briefing verb list is explicit and inspectable, which also means it is not exhaustive.
- The backend still contains the procedure-extraction, simulation, and reliability engines from the product's earlier runbook direction. They have no pages or navigation and are off by default (`ORG_MEMORY_ENABLE_PROCEDURES`, `ORG_MEMORY_ENABLE_ACTIONS`, `ORG_MEMORY_ENABLE_ADVANCED_RELIABILITY`); removing them from the backend is planned.

---

## Documentation index

| Document | Covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture |
| [`docs/COMPANY_BRAIN_ARCHITECTURE.md`](docs/COMPANY_BRAIN_ARCHITECTURE.md) | The continuous memory loop |
| [`docs/HCAG_MEMORY_ARCHITECTURE.md`](docs/HCAG_MEMORY_ARCHITECTURE.md) | Context assembly engine |
| [`docs/CONTEXT_ACTIVATION_SWARM.md`](docs/CONTEXT_ACTIVATION_SWARM.md) | The specialist swarm and context envelopes |
| [`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md) | Nodes, edges, and traversal |
| [`docs/MEMORY_WORK.md`](docs/MEMORY_WORK.md) | Work packages and agent handoffs |
| [`docs/APPROVALS.md`](docs/APPROVALS.md) | The human decision boundary |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Scoping, secrets, and trust boundaries |
| [`docs/CONNECTORS.md`](docs/CONNECTORS.md) | Connector platform and custom connectors |
| [`docs/MCP.md`](docs/MCP.md) | Standalone MCP server |
| [`docs/OAUTH_SETUP.md`](docs/OAUTH_SETUP.md) | GitHub, Google, Slack, email auth |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | A timed product walkthrough |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Retrieval evaluation |
| [`docs/STARTUP_EXECUTION_PLAN.md`](docs/STARTUP_EXECUTION_PLAN.md) | Customer validation, pilot, metrics, and fundraising plan |
| [`docs/HARNESS_EXECUTION.md`](docs/HARNESS_EXECUTION.md) | Reusable execution skill and resumable checkpoints |

---

## License

MemoryWorks is released under the [MIT License](LICENSE).

## Naming note

The product was previously called Runbook, then OrgMemory. Some identifiers keep the older names because existing installs and clients depend on them: the Python import path `orgmemory`, `ORGMEMORY_*` settings, tool names such as `get_orgmemory_briefing`, and some database table names. Everything a person reads says MemoryWorks.
