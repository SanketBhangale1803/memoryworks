# Fable 5 Enterprise Upgrade Plan

Date: 2026-07-01. This plan was produced after inspecting every module in
`runbook/`, `hcag/`, and `agentgate/`. It upgrades the existing product
in-place. Nothing is rebuilt from scratch and no working code is discarded
without a replacement that passes the existing test suite.

## Current Runbook architecture

- **Backend** — FastAPI (`backend/app`), Python 3.11+, SQLite for application
  state (projects, users, workspaces, sessions, knowledge items, runbooks,
  actions, audit events, connector accounts, ingestion jobs), ArcadeDB over
  HTTP for the knowledge graph. Single router in `app/api/routes.py`.
- **Graph** — `app/graph/`: `GraphStore` interface (`base.py`), ArcadeDB
  implementation (`arcadedb_store.py` + `arcade_client.py`), an in-memory
  implementation for tests (`memory_graph.py`), idempotent schema migrations
  (`migrations.py`, 41 vertex types / 49 edge types), and a graph builder used
  by repository ingestion.
- **Ingestion** — `IngestionService.ingest_item` normalizes every source
  (repo file, issue, PR, Slack, upload, log) into knowledge items + chunks with
  extracted services/signals, writes vertices and edges, and records audit
  events. `RepositoryIngestor` scans repos (docs, compose, workflows,
  Jenkinsfiles, source files, imports, endpoints, env vars, commands, errors)
  and GitHub issues/PRs when authenticated. Jobs are recorded in
  `ingestion_jobs` with per-source counts.
- **Ask pipeline** — query → `HCAGAdapter.route_query` (imports HCAG's
  `QueryPlanner` when available, deterministic fallback otherwise) →
  `GraphStore.retrieve_context` (lexical + service + source-type + overview
  ranking over project chunks) → `llm_answer` (evidence-only prompt, only when
  `OPENAI_API_KEY` set) or `evidence_answer` (extractive) → citations,
  confidence, retrieval trace, suggested runbooks, action boundaries. A
  narrow diagnostic-hypothesis path engages for low-confidence
  service-down questions.
- **Runbook extraction** — `RunbookService.extract` builds cited YAML/JSON
  runbooks from retrieved procedures/commands, classifies step action types,
  derives approval rules and risk, links Runbook/RunbookStep vertices to
  sources and services. Returns zero runbooks when no executable procedure
  exists in evidence.
- **Approvals / AgentGate** — `AgentGateAdapter` loads AgentGate's real
  `PolicyEngine` from `~/Desktop/startup/agentgate` and combines it with
  Runbook's product policy (read/analysis/draft allowed; mutation, send,
  deployment, production change, export require approval; credential access
  fails closed). All proposals/resolutions audited. No shell executor ships;
  `ALLOW_LOCAL_COMMAND_EXECUTION=false`.
- **Auth** — dev login issuing real User/Workspace/WorkspaceMember/Session
  rows, bearer sessions, roles owner/admin/member/viewer, workspace CRUD and
  invites. Google/GitHub/Microsoft env vars are wired in settings.
- **Connectors** — GitHub and Slack with OAuth start/callback + verified token
  fallback, encrypted secrets (Fernet), status endpoints, repo/channel
  listing; planned-connector registry rendered honestly in the UI.
- **MCP** — `mcp_server/server.py` (FastMCP, stdio) exposing 12 tools that
  call the real HTTP API.
- **Frontend** — Next.js app router, custom design system in `globals.css`
  (no template), pages: login, overview, projects, graph, ask, runbooks,
  runbook detail, connectors, ingest, jobs, approvals, audit, integrations,
  settings.
- **Tests** — backend: grounding, no-canned-answers, ingestion, policy,
  approvals, graph/auth/jobs, hypotheses; frontend: navigation render test.

## Current HCAG architecture

- Flat module layout (`semantic_router.py`, `boundary_detector.py`,
  `context_window_manager.py`, `context_windows.py`, `query_planner.py`,
  `graph_core.py`, `guard.py`, `memgraph_window_store.py`,
  `retrieval_trace.py`, `dynamic_seed.py`, plus a small `hcag/` package with a
  CLI). Routing is embedding-based over a domain/subdomain seed graph with
  ambiguity margins, confidence floor, domain prefixes, and boundary
  detection.
- Persistence is Memgraph/Neo4j (`memgraph_window_store.py`,
  `graph_database.py` defaults to `GRAPH_PROVIDER=memgraph`); `neo4j` is a
  hard install dependency in `pyproject.toml`.
- Benchmarks exist as ad-hoc scripts (`locomo_test/`, `longmemeval_test/`,
  `benchmark_*.py`) with real prior results under `.benchmarks/` (e.g. a
  LongMemEval single-session-assistant run with a local judge). There is no
  `make benchmark`, no unified harness, no `benchmark_reports/`, no README,
  no Makefile, and no tests directory wired to CI.

## Current AgentGate architecture

- FastAPI app with policy engine (`policy_engine.py` + `policies/default.yaml`),
  risk engine, sensitive-data detection, approvals, audit, HTML admin UI, MCP
  server, and a real pytest suite. Runbook already consumes the policy engine
  through `app/agentgate_adapter`.

## Existing stack summary

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python 3.11, SQLite (app state) |
| Graph | ArcadeDB 26.5.1 (HTTP API), in-memory store for tests |
| Frontend | Next.js (app router), TypeScript, custom CSS design system |
| MCP | FastMCP stdio server calling the HTTP API |
| Auth | Session tokens, dev login, OAuth env plumbing, Fernet-encrypted connector secrets |
| CI-ish | `make test` (pytest + node test), `make lint` (ruff + black + tsc) |

## Main gaps (spec vs. current)

1. **Trust scoring (4.11)** — answers/runbooks have confidence but no trust
   score with source-quality/recency/support/contradiction reasoning.
2. **Runbook drift detection (4.5)** — no drift states, no drift signals, no
   endpoint, no UI.
3. **Change-to-incident correlation (4.6)** — recent PR/issue/commit evidence
   is retrievable but never correlated with failures as a ranked suspect list.
4. **Simulation mode (4.8)** — absent entirely.
5. **Blast radius (4.10)** — dependency edges exist; no traversal endpoint or view.
6. **Operational memory (4.9)** — approver signals are extracted but never
   stored as structured, evidence-backed, approvable memories.
7. **Migration importers (4.7)** — no importer interface or honest UI cards.
8. **Runbook versioning (12)** — re-extraction overwrites; no version history.
9. **API keys (8)** — concept required, not implemented.
10. **MCP (17)** — missing `runbook_get_blast_radius`,
    `runbook_simulate_incident`, `runbook_check_runbook_drift`.
11. **Ask response shape (11)** — missing `trust_score`,
    `related_slack_messages`; graph trace is edge tuples without explanation.
12. **Graph modules (6)** — `graph_ranker.py` and `graph_explainer.py` absent.
13. **Frontend (16)** — missing Drift, Simulation, Benchmark Reports,
    MCP/API Keys, Admin/Security pages; Ask/Runbook Detail lack the new fields.
14. **HCAG (13)** — hard Memgraph/neo4j dependency, no benchmark harness or
    reports, no Makefile/README/docs/tests, no pluggable window store.
15. **Docs (20)** — required uppercase docs absent; lowercase
    `architecture.md`/`security.md` predate this upgrade.

## Upgrade strategy

Incremental, in-place, evidence-first. Each phase keeps `make test` green.

1. **Graph schema growth** — add `OperationalMemory`, `ServiceOwner`,
   `BlastRadius`, `TrustScore`, `RunbookDriftSignal` vertices and
   `SERVICE_OWNED_BY`, `SERVICE_AFFECTS_SERVICE`, `RUNBOOK_HAS_DRIFT_SIGNAL`,
   `OPERATIONAL_MEMORY_BACKED_BY`, `TRUST_SCORE_DERIVED_FROM` edges
   (idempotent migrations; both stores).
2. **Intelligence layer** — new `app/intelligence/` package:
   `trust.py` (trust scores computed from actual evidence age, source mix,
   support count, contradiction detection), `drift.py` (compare runbook
   sources against currently ingested knowledge; emit typed drift signals),
   `correlation.py` (rank recent change evidence against failure evidence by
   shared files/services/env tokens), `simulation.py` (walk a real runbook's
   steps through AgentGate policy, report missing context, dangerous steps,
   required approvals), `blast_radius.py` (graph traversal over dependency /
   env / runbook edges).
3. **Operational memory** — `app/memory/` with SQLite table, candidate
   derivation strictly from ingested evidence signals, explicit approval flow,
   graph vertices with `OPERATIONAL_MEMORY_BACKED_BY` edges.
4. **Importers** — `app/importers/` with `IncidentToolImporter` interface and
   registry entries for PagerDuty, Rootly, incident.io, Opsgenie, Statuspage,
   Jira Service Management, ServiceNow. Everything reports `not_connected`
   unless credentials exist; import calls fail honestly instead of faking.
5. **Ask pipeline v2** — `graph_ranker.py` (ranking moved behind a named
   module + graph-degree boost), `graph_explainer.py` (human-readable trace
   paths), `trust_score` and `related_slack_messages` in the response.
6. **Runbook versioning** — payload carries `version`, `versions` history,
   `drift_status`, `trust_score`; re-extraction bumps the version only when
   content changes.
7. **API keys** — hashed at rest, prefix-identified, workspace-scoped,
   create/list/revoke endpoints + UI.
8. **MCP + frontend** — three new tools; Drift, Simulation, Benchmarks,
   API Keys, Admin pages; upgraded Ask and Runbook Detail; blast-radius view
   on Repo Graph.
9. **HCAG enterprise pass** — pluggable window store (`window_store.py`
   in-memory + `arcadedb_window_store.py`), Memgraph made an optional extra
   (import becomes lazy; `neo4j` moves out of core deps), benchmark package
   (`benchmarks/`) with datasets + harness computing Recall@k / MRR / nDCG /
   boundary F1 / multi-hop success / latency against a naive baseline,
   honest reports in `benchmark_reports/latest.{md,json}`, Makefile, README,
   docs, tests.
10. **Docs + Make targets** — all required documents; `make benchmark` in both
    projects.

## Files to modify or add

Backend (new): `app/intelligence/{__init__,trust,drift,correlation,simulation,blast_radius}.py`,
`app/memory/{__init__,operational}.py`, `app/importers/{__init__,base,registry}.py`,
`app/graph/{graph_ranker,graph_explainer}.py`, `app/auth/api_keys.py`,
`app/hcag_adapter/arcadedb_window_store.py`.
Backend (modified): `app/api/routes.py`, `app/api/schemas.py`,
`app/graph/migrations.py`, `app/graph/arcadedb_store.py`,
`app/graph/memory_graph.py`, `app/retrieval/service.py`,
`app/runbooks/service.py`, `app/core/database.py`.
MCP: `mcp_server/server.py`, `mcp_server/README.md`.
Frontend (new): `app/drift/page.tsx`, `app/simulation/page.tsx`,
`app/benchmarks/page.tsx`, `app/keys/page.tsx`, `app/admin/page.tsx`.
Frontend (modified): `components/Nav.tsx`, `app/ask/page.tsx`,
`app/runbooks/[id]/page.tsx`, `app/graph/page.tsx`, `tests/navigation.test.mjs`.
HCAG (new): `Makefile`, `README.md`, `window_store.py`,
`arcadedb_window_store.py`, `benchmarks/` package with datasets and harness,
`benchmark_reports/`, `docs/{ARCHITECTURE,BENCHMARKS}.md`, `tests/`.
HCAG (modified): `pyproject.toml` (neo4j → optional extra),
`semantic_router.py` (lazy Memgraph import), `graph_database.py` (provider
default no longer assumes Memgraph is reachable).
Docs: everything listed in the spec §20.

## Risks

- **ArcadeDB availability** — all graph writes assume the container is up;
  the in-memory store keeps tests hermetic but production paths must degrade
  with explicit errors, never silently fabricate. Mitigation: health endpoint
  already reports `connected: false`; new endpoints follow the same pattern.
- **Drift/correlation honesty** — the only time axis for local uploads is
  ingestion time. Both modules label their basis explicitly rather than
  claiming deploy-time knowledge they do not have.
- **HCAG refactor breakage** — `semantic_router` is used by existing
  benchmark scripts. The Memgraph import becomes lazy so the module imports
  cleanly without neo4j; old scripts keep working when Memgraph is present.
- **Benchmark integrity** — the harness must not overfit to its own datasets.
  Baseline and HCAG run over the identical corpus and metric code; datasets
  are committed and reviewable; reports include per-case results.
- **Schema evolution on live SQLite** — new tables only (`CREATE TABLE IF NOT
  EXISTS`); the existing `runbooks` table is not altered (version history
  lives in `payload_json`).

## Benchmark plan

See `docs/HCAG_BENCHMARK_PLAN.md`. Summary: committed labeled datasets for
multi-hop incident retrieval, temporal retrieval, and company-brain QA;
harness computes Recall@k, MRR, nDCG, boundary-detection F1, multi-hop
success rate, answerability accuracy, and latency for (a) a naive lexical
baseline and (b) the HCAG routed pipeline over the same corpus; results are
written to `hcag/benchmark_reports/latest.{md,json}` with per-case detail.
LoCoMo/LongMemEval wrappers run only when their datasets/API keys are present
and otherwise report "skipped" — never fabricated numbers. If HCAG loses to
baseline on any metric, the report says so.

## Test plan

- Backend: unit tests per intelligence module (drift transitions, correlation
  ranking, simulation approval gating, blast radius traversal, trust factors,
  operational-memory approval flow, importer honesty, API key hashing,
  runbook version bumps), plus the existing grounding/no-canned-answer suites.
- Frontend: extend the navigation test to render the five new pages.
- HCAG: tests for router classification, boundary detection, window store
  interface, harness metric math (known-answer nDCG/MRR cases).

## Features intentionally deferred

- Real Google/GitHub/Microsoft OAuth login round-trips (architecture and env
  plumbing exist; provider apps are deployment-specific).
- Webhook-driven incremental ingestion; GitHub App installation flow.
- Embedding-backed vector retrieval in Runbook (interface-ready; the ranker
  is a named module so an embedder can slot in without contract changes).
- Isolated command executor workers (no shell execution ships by design).
- Live importer API clients for PagerDuty/Rootly/etc. (interface + honest
  not-connected status ship now).
- Slack user-ID → profile-name enrichment.
