# MemoryWorks architecture

MemoryWorks is a company brain for engineering organizations and the AI agents
working in them. It turns company sources into scoped, source-backed memory,
serves that memory as cited answers and pre-action briefings, and records what
happened after an agent acted on it.

The system has five boundaries, and most design decisions follow from keeping
them separate:

1. **Acquisition** — connectors fetch source content; credentials never enter
   memory.
2. **Memory** — ingestion turns raw content into chunks, atomic memories, and
   beliefs, each tied to an exact source revision.
3. **Context** — HCAG and the retrieval pipeline assemble authorized, current,
   token-bounded context for one question or intent.
4. **Action** — anything that changes the world (memory writes, org plans,
   code execution, connector writes) stops at a person.
5. **Outcome** — every served context is logged, and the result of acting on it
   is recorded against it.

Companion documents go deeper on individual parts:
[`COMPANY_BRAIN_ARCHITECTURE.md`](COMPANY_BRAIN_ARCHITECTURE.md) (revisions,
envelopes, artifacts, skills), [`CONTEXT_ACTIVATION_SWARM.md`](CONTEXT_ACTIVATION_SWARM.md),
[`GRAPH_MODEL.md`](GRAPH_MODEL.md), [`CONNECTORS.md`](CONNECTORS.md),
[`MEMORY_WORK.md`](MEMORY_WORK.md), [`APPROVALS.md`](APPROVALS.md),
[`MCP.md`](MCP.md), and [`SECURITY.md`](SECURITY.md).

---

## System overview

```text
                  ┌──────────────────────── clients ─────────────────────────┐
                  │ Next.js web app   browser agents    MCP clients   SDK/CLI │
                  │ (WorkspaceChat)   (36 page tools)   (stdio/HTTP) (Python)│
                  │                                     Tauri desktop bridge  │
                  └──────┬──────────────────┬─────────────────┬──────────────┘
                         │ session cookie   │ same cookie     │ API key / OAuth bearer
                         ▼                  ▼                 ▼
         ┌──────────────────────────── FastAPI backend ────────────────────────────┐
         │ APIGuard (body caps, rate limits) → AuthenticationBoundary → routers    │
         │   /api/*  (routes.py)   /api/org/*  (org_routes.py)   OAuth / .well-known│
         │                                                                          │
         │ ingestion ─► memory (units, beliefs, revisions) ─► HCAG + swarm         │
         │                                   │                    │                 │
         │                    retrieval lanes / briefing / org ops                  │
         │                                   │                                      │
         │        approvals · work packets · execution runner · connector runtime  │
         │                                   │                                      │
         │                          outcome ledger ─► learned skills                │
         │                                                                          │
         │ background: connector sync worker · standing watches · vector backfill  │
         └─────────────┬───────────────────────────────────────┬───────────────────┘
                       ▼                                       ▼
              SQLite (WAL) — application                ArcadeDB — graph
              state, 66 tables                          75 vertex / 94 edge types
```

| Layer | Technology |
|---|---|
| Frontend | Next.js 15 app router, React 19, TypeScript, no UI framework |
| Backend | FastAPI, Python 3.13, `pydantic-settings` |
| Application state | SQLite in WAL mode (`backend/app/core/database.py`) |
| Graph | ArcadeDB over HTTP; an in-memory store with the same contract for tests |
| Embeddings / rerank | FastEmbed or OpenAI embeddings, optional cross-encoder reranker, deterministic fallback |
| Models | GLM via OpenRouter (default), OpenAI, Anthropic, Google, xAI, Kimi |
| Agent surfaces | In-browser tools on the chat page, FastMCP server (stdio + streamable HTTP), Python SDK + CLI |
| Desktop | Tauri 2 thin bridge (`desktop/`) |

---

## Repository layout

```text
backend/app/
  main.py               app factory, middleware order, lifespan workers
  api/                  routes.py (/api, 152 routes), org_routes.py (/api/org, 31), schemas.py
  core/                 config, SQLite schema + column migrations, API guard, logging
  auth/                 sessions, dev/demo/email/OAuth login, API keys, MCP OAuth, token vault
  governance/           teams, project grants, source/memory scope binding
  connectors/           GitHub, Slack, Notion, Google Drive, Teams, remote MCP, REST pull,
                        signed package registry, runtime, sync engine
  ingestion/            one ingest path, repository scanner, documents, web fetch, sanitizer
  graph/                GraphStore contract, ArcadeDB + in-memory stores, ranker, traversal
  hcag_adapter/         routing, context windows, context store, memory dynamics
  swarm/                concurrent specialist retrieval + context compiler
  memory/               atomic memory, beliefs, authority, change sets,
                        revisions/envelopes/artifacts/skill specs, briefings
  retrieval/            the ask pipeline and its lanes, memory_search ranking
  intelligence/         trust, drift, correlation, simulation, blast radius
  outcomes/             context → action → outcome ledger
  skills/               learned skills distilled from verified execution runs
  work/                 Memory Work packets
  execution/            headless coding-agent runner
  orgops/               cross-space organizational operations, agent, watches, seed
  webmcp_agent.py       the model-driven agent loop Agent mode runs (with orgops tools)
  company_context/      inspectable company-context briefing
  llm/                  model provider catalog and grounded JSON generation
  approvals/ agentgate_adapter/ audit/   action proposals, policy, audit trail
  runbooks/ reliability/ importers/      legacy procedure extraction and assertions (no UI), incident importers
backend/tests/          51 pytest modules
backend/evals/          briefing evaluation with known-answer cases
frontend/               Next.js app (20 routes), components, lib, node:test suites
mcp_server/             standalone MCP server + contract tests
python_sdk/             typed sync/async client and the `orgmemory` CLI
desktop/                Tauri 2 OS bridge (keychain, folders, local MCP sidecar)
deploy/oci/             Caddy + bootstrap scripts for a single-VM deployment
```

---

## Backend runtime

### Request path

`main.py` installs middleware so the cheapest rejection happens first:

```text
APIGuardMiddleware              body-size cap, sliding-window rate limit per principal/IP
  → AuthenticationBoundaryMiddleware   every /api path is authenticated unless allow-listed
    → CORSMiddleware                   FRONTEND_URL only in production
      → routers
```

The authentication boundary is default-deny: only an explicit set of public
paths (health, login flows, provider callbacks) and signature-verified
`/api/webhooks/*` bypass it. In `PUBLIC_DEMO_MODE`, a list of mutation
prefixes is blocked outright.

Principals come from one of three places, all resolved to a workspace, user,
and role (`owner`, `admin`, `member`):

- a signed session cookie (browser, and therefore the in-browser agent tools);
- an `om_live_…` API key (SDK, CLI, stdio MCP);
- an MCP OAuth access token, issued by the backend's own authorization server
  (`/oauth/*`, dynamic client registration optional) and introspected by the
  MCP server.

### Lifespan workers

On startup the backend asserts the configuration is safe for the environment,
creates or migrates the SQLite schema, initializes the graph, and — in public
demo mode — recreates the deterministic demo identities and launch scenario on
every cold start. It then runs three background tasks:

| Worker | What it does |
|---|---|
| Vector backfill | Re-embeds legacy chunks off the request path. Mixed-model chunks stay safe because dense scoring only compares vectors from the active model. |
| Connector sync | Drains `connector_sync_jobs` every `CONNECTOR_SYNC_POLL_SECONDS`. A failed iteration is logged and the loop continues. |
| Standing watches | Runs due org watches every `ORG_WATCH_POLL_SECONDS`. Watches record findings and draft plans; they never apply one. |

### Storage split

SQLite holds application state: identity and sessions, workspaces, teams and
grants, connector accounts and encrypted grants, ingestion and sync jobs,
knowledge items, memory units and relationships, beliefs, source revisions and
change sets, context envelopes and activation runs, artifacts, skill specs,
work packets, execution runs, the outcome ledger, learned skills, and org
tasks/plans/watches.

ArcadeDB holds relationships and retrieval memory: repository structure
(files, services, endpoints, env vars, workflows), knowledge chunks, context
windows, memory units and their entities, beliefs, revisions, envelopes, and
provenance edges. Everything reaches it through the `GraphStore` contract in
`graph/base.py`; `GraphStoreProxy` lets tests swap in `InMemoryGraphStore`.

Schema changes to an existing table must also be added to `_migrate_columns`
in `core/database.py`. `CREATE TABLE IF NOT EXISTS` never adds a column to a
table that already exists, and tests run on fresh databases, so they cannot
catch the omission.

---

## Acquisition: connectors

```text
provider OAuth / token ─► OAuthTokenVault (Fernet locally, AWS KMS or OCI Vault in production)
                                  │
ConnectorRegistry (signed, version-pinned manifests)
                                  │
ConnectorRuntime ── authorize · discover · invoke · request_write → approval → execute_approved
                                  │
SyncEngine ── enqueue / verified webhook ─► connector_sync_jobs ─► sync worker ─► ingest_item
```

- **Built-in connectors:** GitHub, Slack, Notion, Google Drive, Microsoft Teams.
- **Workspace-defined sources:** a remote MCP server (`remote_mcp.py`) or any
  HTTP JSON endpoint with a field mapping (`rest_pull.py`). Both flow through
  the same sync engine, deduplication, sanitization, and memory pipeline.
  Private-network targets are refused unless explicitly allowed
  (`url_security.py`).
- **Webhooks:** GitHub and Slack events are signature-verified, deduplicated in
  `connector_webhook_deliveries`, and trigger incremental reconciliation.
- **Writes:** a connector write is a `connector_tool_calls` row in
  `pending_approval`; it executes only after an admin resolves it.
- **Importers:** incident-tool migration interfaces (PagerDuty live; others
  report `not_connected` rather than pretend).

---

## Memory: ingestion to beliefs

Every source type — repositories, issues, pull requests, Slack, uploads, pasted
text, web pages, connector records — goes through
`IngestionService.ingest_item`.

```text
raw item
  → sanitize_for_index        credentials removed before SQLite, ArcadeDB, logs, or embeddings
  → extract_document          office, PDF, sheets, slides, HTML, mail, code → text with structure markers
  → chunk + embed + index     KnowledgeChunk vertices, HCAG context windows
  → SourceRevision            immutable, content-addressed
  → extract_atomic_memories   conservative; only source-backed statements are promoted
  → MemoryChangeSet           added / updated / invalidated / conflicting memories
  → scope binding             the source's team grants copied to every derived memory
  → stale marking             dependent artifacts and skill specs flagged, never rewritten
```

Repository code is read structurally: manifests, service tables, routes,
config schemas, and docstrings can become memory; CSS, JSX fragments, and
partial expressions stay as evidence chunks. Repository evidence must match
the selected project's repository, and uploads are assigned to exactly one
project, so records from different projects are never silently mixed.

Three memory representations sit on top of chunks:

| Representation | Module | Role |
|---|---|---|
| `MemoryUnit` | `memory/company.py` | Typed atomic fact (decision, policy, procedure, incident, dependency, …) with scope, validity window, and `UPDATES` / `CONTRADICTS` / `SUPPORTS` / … relationships. A new memory supersedes a current one only when subject *and* kind match (incidents never supersede each other); records that say they supersede, replace, or deprecate another retire its memories, whatever the ingestion order. |
| `Belief` | `memory/beliefs.py` | Append-only claim with explicit history and evidence. Competing beliefs are resolved by `AuthorityResolver` using `ORG_MEMORY_AUTHORITY_ORDER` (current code/config first, inferred memory last); neither side of a disagreement is deleted. |
| Profiles | `memory/company.py` | Company, project, repo, and service profiles assembled from current atomic memory. |

`ChangeIntelligenceService` interprets source diffs into claim deltas — with a
structured-output model when configured, otherwise a deterministic interpreter
— and records them as `semantic_change_events` that update beliefs.

`CompanyBrainService` (`memory/brain.py`) owns the lifecycle around this:
revisions, change sets, version vectors, context envelopes, revisioned
artifacts, and compiled `SkillSpec`s.

---

## Context: HCAG and the swarm

`HCAGAdapter` imports the HCAG `StructuredQueryPlanner` from `HCAG_PATH` when
it is present and falls back to a deterministic planner otherwise. It routes a
query to a memory domain (project, policy, decision, ownership, temporal,
deployment, incident), resolves scope, and persists context windows in
ArcadeDB.

`ContextActivationSwarm` then runs concurrent deterministic specialists —
hybrid lexical/semantic retrieval, bounded graph traversal
(`graph/traversal.py`), and a current-truth historian — whose evidence is
deduplicated by a critic and compiled once under this invariant:

```text
authorized team scope ∩ task relevance ∩ current truth
∩ entity graph neighborhood ∩ token budget
```

Security trimming happens before ranking. Owners and admins see the whole
workspace; members and agents see only sources granted to their teams, and
everything derived from a source is at most as visible as the source.

Each run persists a `context_activation_runs` row and a `ContextEnvelope`
recording the principal, authorized teams, selected memories and evidence, the
exact compiled context, active skills, the source version vector, the token
budget, and the retrieval trace — so any answer's context is reproducible.

---

## Answers: the ask pipeline

`RetrievalService.ask` (`retrieval/service.py`) is the single answer path for
Ask mode in the chat, the API, the SDK, MCP, and the in-browser tools. It resolves each question into
one of three lanes — company, general, or conversational — and never presents
general knowledge as company truth.

```text
question (+ thread history)
  │
  ├─ assistant_reply            greetings, thanks, "what is MemoryWorks" → deterministic, no model
  │
  ├─ continuity.resolve         bind pronouns to the thread's subject;
  │                             a dangling reference is asked about, not guessed
  │
  ├─ HCAG route
  ├─ belief grounding           current beliefs first
  ├─ memory grounding           then current atomic memories
  ├─ workspace evidence         otherwise swarm retrieval, security-trimmed;
  │                             widens from a pinned project to the workspace only when empty
  │
  ├─ evidence_answer / universal_evidence_answer   deterministic extractors
  ├─ deliberate                 N candidate syntheses under different lenses + a judge
  │                             (ORG_MEMORY_ANSWER_CANDIDATES, default 5)
  ├─ validate_grounded_answer   evidence contract enforced on every lane
  │
  ├─ general_knowledge_answer   only if company evidence is empty AND the question
  │                             is not about the company; labelled as general knowledge
  │
  ├─ build_handoff              editor-ready task envelope for fix/patch/review asks
  ├─ clarification              ask "where" or "what" instead of acting on an ambiguous change
  ├─ extract_hypotheses         service-down diagnostics from graph-held evidence only
  │
  ├─ ContextEnvelope persisted
  └─ record_context             opens an outcome-ledger row; returns context_event_id
```

When evidence is insufficient for a company question, the pipeline abstains
rather than fabricates. The model provider only changes who writes the
answer; every provider receives the same retrieved evidence
(`llm/providers.py`).

Agent mode in the chat is a separate entry point: `/api/org/ask` runs a
tool-using agent over the organizational-operations surface (below), and
`/api/org/ask/stream` — what the chat calls — streams that session as NDJSON
within one request, so a load-balanced deployment cannot lose the
process-local run mid-answer. After each turn, `/api/org/followups` drafts the
suggested next questions from what the turn found.

---

## Pre-action briefings

`memory/briefing.py` answers an intent ("restart the payments pool") rather
than a question. It is deliberately model-free, so the same intent produces
the same verdict twice:

- ranks memory with `retrieval/memory_search.py`: whole-word, stemmed,
  IDF-weighted lexical scoring with a relative floor, optionally reranked by a
  local cross-encoder (which may also admit embedding neighbours) — never raw
  embedding recall on its own;
- groups results into `must_read`, `constraints`, `prior_incidents`, and
  `blast_radius`, with each memory in exactly one group;
- with a named service, keeps only memories about that service, a service a
  recorded dependency links it to, or no service at all, so another team's
  incident is never shown as this service's warning;
- requires approval for consequential verbs from an explicit list, and also
  whenever a memory for the service states a review, approval, or sign-off
  rule, whatever kind the memory was filed as;
- returns one of `no_memory` → `proceed` → `proceed_with_context` →
  `requires_approval`, keeping "nothing is known" distinct from "nothing to
  worry about".

A briefing never authorizes anything. `requires_approval` is advice; the
approvals queue is where a person decides. Serving a briefing opens a ledger
row recording every memory shown, in display order, so a later
`POST /api/briefings/outcome` is attributable to the exact context that
preceded it.

`backend/evals/briefing_eval.py` scores briefings against known-answer cases
(`briefing_cases.json`) through the real ingestion service and
`/api/briefings` route, hermetically (throwaway SQLite, in-memory graph, no
model keys). It reports pass rate, recall, precision, leaks, approval accuracy,
and ledger attribution; `--semantic` adds the local embedding model and
cross-encoder. Retrieval and briefing changes are measured with it.

---

## Organizational operations

`orgops/` treats a workspace as a set of spaces (projects) and answers the
questions a person would otherwise reconstruct by opening all of them: project
context, recent changes, decisions, provenance, reasoning chains, owners,
tasks and dependencies, blockers, conflicts between spaces, stale information,
and launch readiness. No model runs in `OrgOpsService`; every result carries
stable ids back to stored rows.

- **Plans** are the only write. `propose_plan` files an `org_action_plans` row
  and stops; `approve_plan` / `reject_plan` are human decisions.
- **Watches** (`orgops/watch.py`) run the same read checks on an interval and
  record findings, drafting a plan when the fix is unambiguous.
- **The org agent** (`orgops/agent.py`) lets a model pick tools from the same
  catalog and executor the HTTP routes use, with a deterministic decider when
  the model is unreachable.
- **The launch scenario** (`orgops/seed.py`) is an idempotent multi-space
  fixture used by public demo mode and tests. The product never loads it into
  a real workspace on its own.

---

## Action: approvals, work, and execution

Every path that changes state beyond reading ends at a person:

| Path | Where it stops |
|---|---|
| Memory proposals (single facts from agents or people) | `memory_proposals`, admin resolve |
| Repository refresh requests | `repository_refresh_requests`, admin resolve |
| Org plans | `org_action_plans`, approve / reject |
| Connector writes | `connector_tool_calls`, admin resolve before `execute_approved` |
| Operational actions | AgentGate policy → `actions` approve / deny |
| Memory Work steps | per-step resolve on consequential steps |
| Code execution | disabled unless `ORG_MEMORY_EXECUTION_ENABLED=true` |

**AgentGate.** `agentgate_adapter/` loads the AgentGate policy engine from
`AGENTGATE_PATH` when present and combines it with MemoryWorks's action taxonomy;
otherwise the built-in product policy applies. Unresolved operational
assertions escalate production-changing proposals to admin review.

**Memory Work.** `MemoryWorkService` turns an outcome description into a
revisioned brief plus a portable `agent_packet` with an approval-aware plan
that any compatible worker can execute through the API or MCP.

**Execution runner.** `execution/runner.py` hands a handoff to `cursor-agent`
or `claude` running headless, and is intentionally blunt about safety:

- it always works in a throwaway clone under `EXECUTION_DIR`, never the user's
  checkout or the ingest cache;
- it always commits on an `orgmemory/<slug>` branch, never the default branch;
- pushing requires `ORG_MEMORY_EXECUTION_ALLOW_PUSH`;
- production refuses to start with execution enabled unless
  `ORG_MEMORY_EXECUTION_ISOLATED_PROFILE` is also set;
- "no changes" is reported as `no_changes`, not success;
- every run writes itself into the outcome ledger.

---

## Outcome: the ledger and learned skills

```text
context_events   what was served (question, answer, candidates, evidence ids → envelope)
   → action_events    what was done with it (open vocabulary)
   → outcome_events   what happened: succeeded | failed | partial | abandoned | unknown
```

`outcomes/ledger.py` follows three rules: logging never breaks an answer
(every write is best-effort), rows link to envelopes rather than duplicate
them, and outcomes are a closed vocabulary because reward is derived from them.
`/api/outcomes/stats` reports closed, success, and acceptance rates;
`/api/outcomes/export` emits the labelled corpus.

`skills/library.py` distils successful execution runs into learned skills —
the complement to `SkillSpec`, which compiles what is written down:

- a first success is `proposed` and is not injected into prompts until it
  works again;
- a near-identical success reinforces an existing skill instead of adding one;
- a skill that precedes failures loses confidence and is retired
  automatically, with the reason recorded;
- matching is deterministic token and file overlap, so precedent is
  explainable and never invented.

---

## Intelligence and reliability

`intelligence/` provides trust scoring, drift detection, change-to-incident
correlation, simulation, and blast radius. Each module states its evidence
basis — local corpora only have ingestion time, and the output says so.

`reliability/` tracks `OperationalAssertion` lifecycles with graph
provenance, and `ChangeImpactService` compares re-ingested file and version
metadata against existing edges to create review reports. A source change is a
reason to re-verify an assertion, never proof that a procedure is invalid.

`runbooks/` extracts versioned, cited procedures downstream of retrieval; a
procedure cannot exist without evidence-backed steps. These are engines from the
product's earlier runbook direction: they have no pages or navigation, stay
behind `ORG_MEMORY_ENABLE_*` flags (off by default), and are slated for removal
from the backend.

---

## Agent surfaces

### In the browser

The chat page registers its tools with the browser through
`document.modelContext.registerTool()`, so an AI agent running in a signed-in
tab can use MemoryWorks without scraping the interface. Nothing in the
interface names or advertises this; it is plumbing.

- `frontend/lib/webmcp.ts` — 20 memory tools (briefing, outcome, ask, search,
  memory reads, proposals, approvals);
- `frontend/lib/orgTools.ts` — 16 cross-space operations tools, the same ones
  Agent mode's server-side loop uses;
- `frontend/hooks/useWorkspaceTools.ts` — registers both sets from the chat
  page, wiring each tool to the same API calls the interface makes;
- `frontend/lib/webmcpCatalog.ts` — a handler-free manifest derived from the
  executable definitions, used by tests and the docs.

Tools reuse the page's `HttpOnly` session cookie through `lib/api.ts`; agents
never receive credentials. Tools are annotated in tiers — `read-only`,
`ledger-append`, `approval-required`, `admin-decision` — and the server enforces
authorization regardless of what the client advertises. Decision tools are only
registered for owners and admins.

`/api/webmcp/agent-sessions` is an older server-side entry point to the same
agent loop; no page calls it any more.

### MCP server

`mcp_server/server.py` is a FastMCP server that calls the HTTP API — it holds
no business logic. It runs over stdio with an API key or over streamable HTTP
at `/mcp` with OAuth, verifying bearer tokens by introspecting against the
backend. Every tool carries MCP annotations plus an `orgmemory/toolKind`
tag (`preflight` for the briefing, `outcome` for recording one, `read`,
`write`). Legacy `runbook_*` tools are
removed unless `ORGMEMORY_ENABLE_LEGACY_TOOLS=true`. Settings are read as
`MEMORYWORKS_*` first, with `ORGMEMORY_*` and `RUNBOOK_*` as deprecated
fallbacks.

### Python SDK and CLI

`python_sdk/` provides `MemoryWorks` and `AsyncMemoryWorks` typed clients and the
`orgmemory` CLI, reading `ORGMEMORY_API_URL` and `ORGMEMORY_API_KEY`. The import
path stays `orgmemory`, and the pre-rename `OrgMemory` names remain as aliases.

### Desktop bridge

`desktop/` is a thin Tauri 2 client for OS integration only: keychain
storage, folder selection, local-network probes, notifications, a local MCP
sidecar (the packaged `mcp_server`), and signed updates. All business logic
stays in the backend.

---

## Frontend

- **Shell.** `app/layout.tsx` wraps every page in `AppShell`, which places
  signed-in pages inside `WorkspaceFrame`, laid out like an editor: New chat,
  Search (⌘K), three places — Sources, Memory, Approvals (with a waiting
  count) — the chat history, a three-step Getting started card, and the account
  row. On narrow screens the sidebar is a drawer.
- **One registry.** `lib/workspaceMap.ts` lists every destination, grouped
  Chat, Sources, Memory, Review, and Settings. `SIDEBAR_PLACES` picks the three
  sidebar entries; every other page in a group appears as a tab on that group's
  hub (`components/PageBar.tsx`). The sidebar, the tabs, the ⌘K `CommandMenu`,
  and the page title bar all read the registry; `tests/navigation.test.mjs`
  checks it, including that retired pages stay gone.
- **Post-login surface.** `/workspace` renders `WorkspaceChat`, the one window:
  a composer with Ask/Agent mode, memory-space, and model chips; answers with
  folded evidence; Agent turns rendered by `components/AgentTurn.tsx` with
  their steps, citations, and inline plan approval. Chat history is kept per
  workspace in the browser (`lib/threads.ts`).
- **Retired routes.** `/webmcp`, `/ask`, `/runbooks`, `/simulation`,
  `/benchmarks`, `/updates`, `/drift`, `/reliability`, and `/admin` redirect
  (`next.config.ts`) to the chat or Approvals.
- **Public surfaces.** The landing page, `/docs`, and `/login` render outside
  the frame.
- **Design.** A custom token-based design system in `app/globals.css`; no
  component library.

---

## Deployment

| Target | Shape |
|---|---|
| Local | `docker-compose.yml`: `arcadedb`, `backend`, `frontend`, `mcp` (profile). Or `make backend` / `make frontend` against a local ArcadeDB. |
| Single VM (OCI) | `compose.production.yml` adds Caddy with automatic TLS: `app.`, `api.`, and `mcp.` subdomains, with proxy-level body caps (128 MB API, 10 MB MCP). Connector grants are encrypted through OCI Vault (`CONNECTOR_VAULT_PROVIDER=oci-kms`). See `deploy/oci/`. |
| Vercel (memoryworks.app) | `vercel.json` runs the frontend and a containerized backend (`Dockerfile.vercel`), routing `/api/*` and `/.well-known/*` to the backend. Production runs here with real sign-in (`PUBLIC_DEMO_MODE=false`), the in-memory graph, and SQLite at `/tmp/orgmemory/`. Vercel containers are stateless, so that state is **not durable** — a new container starts empty apart from what sessions rebuild. The single-VM target is the durable option. `PUBLIC_DEMO_MODE=true` is a stricter profile for a shared, disposable demo. |

`settings.assert_safe_for_environment()` refuses to start with
`ENVIRONMENT=production` when development trust boundaries remain: dev login,
a default or short `JWT_SECRET`, a default ArcadeDB password, deterministic
embeddings, no real sign-in provider, a local connector vault instead of AWS
KMS or OCI Vault, non-HTTPS public URLs, or execution enabled without an
isolated profile. Public demo mode has its own, stricter checklist.

CI (`.github/workflows/ci.yml`) runs four jobs on every push: backend (ruff,
black, pytest), frontend (node tests, `tsc`, build), Python SDK tests, and MCP
contract tests. `make ci` runs the same checks locally.

---

## Invariants

1. **Derived is never broader than source.** Memory, context, briefs, skills,
   and answers inherit the visibility of the sources that support them, and
   trimming happens before ranking.
2. **Evidence or abstain.** Every company answer carries the evidence it used;
   insufficient evidence is an explicit abstention. General knowledge is only
   used for non-company questions and is labelled as such.
3. **History is preserved.** Revisions are immutable; updates and
   contradictions add relationships instead of overwriting; removed claims are
   invalidated, not deleted.
4. **Stale, not rewritten.** A supporting change marks artifacts and skill
   specs stale for review; nothing human-facing is silently regenerated.
5. **Controls are deterministic.** Briefings, org operations, graph traversal,
   and skill matching run without a model so identical inputs give identical
   results.
6. **Consequential actions stop at a person.** Agents can propose and read;
   applying memory, plans, connector writes, operational actions, and pushes
   requires a human decision.
7. **Logging never breaks an answer.** Ledger writes are best-effort.
8. **Credentials never become memory.** Content is sanitized before storage,
   indexing, logs, or embeddings; grants live encrypted in the vault.
9. **Degrade honestly.** Missing graph, model, HCAG, or AgentGate produce
   explicit errors or labelled deterministic fallbacks, never fabricated
   counts, relationships, or success.
