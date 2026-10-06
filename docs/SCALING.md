# Scaling MemoryWorks

A billion users is not a code change; it is a sequence of migrations, each
removing the ceiling the previous stage hits. This document records where the
backend stands, the ceilings found in the code (with the measurement or line
that shows each one), and the stages from here, with what each one unlocks.

Numbers marked *measured* come from ArcadeDB 26.5.1 running locally against a
graph seeded with 120,000 edges. Capacity figures per stage are planning
estimates, to be replaced by load tests at each stage.

---

## Where it stands (2026-10-06)

```text
browser ─► Vercel (Next.js) ─► api.memoryworks.app ─► Caddy ─► backend (API only)
                                                                 │      ▲
                                                     background_jobs    │ answers
                                                                 ▼      │
                                                               worker ──┤
                                                                 │      │
                                                     SQLite (WAL, one file) + ArcadeDB (one node)
                                              one 4 vCPU / 8 GB VPS
```

Done in this stage:

| Change | Effect |
|---|---|
| Imports run in a separate worker (`python -m app.worker`) fed by a durable job table (`app/jobs.py`) | Answers no longer share a CPU with PDF parsing and embedding; a restart no longer loses an import; workers scale with `--scale worker=N` |
| Graph writes batched per document (`ArcadeDBGraphStore.batch`) | *Measured:* a 75-chunk document went from 309 ArcadeDB requests and 8.5 s to 11 requests and 0.5 s |
| `edge_key`, `KnowledgeChunk.item_id`, `KnowledgeItem.source_id` indexed | Every `link()` was a full scan of its edge type; ingestion slowed as the graph grew |
| `list_edges` walks out from the project's indexed vertices | *Measured:* 9.7 s → 0.9 s per call, same edges; an answer's graph time 10+ s → 1.8 s |
| Window and service writes once per document, not per chunk | Removed a per-chunk SQLite JSON scan and repeated edge rewrites |
| LLM caps, per-answer budgets, answers-first yielding | A slow provider or a busy import degrades an answer instead of hanging it |

Planning estimate for this shape: tens of active workspaces and low thousands
of users, bounded by one SQLite writer and one ArcadeDB node on one machine.

---

## Ceilings in the code

In the order they will be hit.

1. **Whole-project reads per answer.** `retrieve_context`
   (`graph/arcadedb_store.py`) loads every chunk of a project *with its
   embedding* into Python and ranks there; `traverse_context` loads up to 100k
   nodes and 100k edges per question; `list_nodes`/`list_edges` are ~70 indexed
   queries each. Answer cost grows linearly with the project. ArcadeDB also caps
   a query at 20,000 rows (*measured*), so very large projects are silently
   truncated rather than slow.
2. **One SQLite file.** All 68 tables, one writer at a time, one host. API
   replicas can only run on the same machine as the file. The database layer
   is contained — every module goes through `app/core/database.py` (49
   importers); SQLite-only SQL is 21 `INSERT OR …` and 8 `json_extract` calls.
3. **Process-local state.** Breaks as soon as there are two API hosts:
   - the LLM limiter and per-answer budget registry (`app/llm/limits.py`);
   - the API rate limiter (`core/api_guard.py`, in-memory deques);
   - Agent runs (`AgentSessionStore` in `webmcp_agent.py`): the chat's recovery
     polls `/api/org/ask/{run_id}`, which only the process running it can answer;
   - `_REPOSITORY_INGEST_LOCK` in `api/routes.py`.
4. **One graph node.** ArcadeDB runs single-node; graph and vectors live on
   one disk with one JVM heap (2 GB).
5. **Local disk.** Repository clones (`REPO_CACHE_DIR`) and uploads live on the
   VPS; a second machine cannot see them.
6. **Model spend.** At scale the bill is the LLM, not the servers (below).

---

## What a billion users means

Assume 1B registered, 10% daily active, 3 questions each per day:

| Quantity | Value |
|---|---|
| Answers per day | 300M (≈3,500/s average, ≈10,000/s peak) |
| Model calls (streamed path: ~2 per answer) | ≈20,000/s at peak |
| Model spend at $0.002 per answer | ≈$600k/day, ≈$220M/year |
| Memory, at 10M workspaces × 10k chunks × ~2.5 KB (text + 384-d vector) | ≈250 TB before replication |

Per-plan quotas, answer caching, prompt caching, and smaller models for drafts
are therefore product requirements at the top of this curve, not optimisations.

---

## Stages

### Stage 1 — many API servers (→ ~100k users)

Goal: any request can be served by any of N identical API instances.

- **Postgres for application state.** Managed, with a read replica. Port the 21
  `INSERT OR …` statements to `ON CONFLICT` and the 8 `json_extract` calls to
  `jsonb`; keep `row`/`rows`/`connect` as the only entry points.
- **Redis for shared state.** LLM limiter and budgets, API rate limits, answer
  activity, Agent run state (so stream recovery works on any instance), and the
  repository ingest lock.
- **Queue on Redis Streams or SQS.** Keep the `app/jobs.py` interface
  (`enqueue`, `handler`, `run_next`); autoscale workers on queue depth.
- **Object storage** for repository clones and uploads.
- **Retrieval pushdown.** Ask the store for the top-k chunks (ArcadeDB's vector
  index or pgvector, plus BM25) instead of loading the project; traverse a
  bounded neighbourhood from those chunks instead of the whole graph.
- **Observability.** OpenTelemetry traces with a span per answer stage; SLOs:
  p95 time-to-first-step < 1 s, p95 answer < 8 s, import throughput per worker.
- Load test: 500 concurrent askers against a 1M-chunk workspace.

### Stage 2 — partitioned data (→ ~10M users)

Goal: no single database holds everyone.

- **Tenant as the shard key.** `workspace_id` on every row; Postgres sharded by
  it (Citus, or a database per cell — see stage 3).
- **A dedicated search tier** (OpenSearch, Vespa, or a vector service) for
  hybrid retrieval, so the graph holds relationships, not every chunk's vector.
- **Graph partitioned by tenant** across ArcadeDB nodes (or a graph per cell).
- **An LLM gateway**: per-tenant quotas, provider routing and fallback,
  semantic answer cache, prompt caching, and spend metering.
- **GPU embedding service**, batched, instead of embedding on worker CPUs.
- Kubernetes with autoscaling, multi-AZ, backpressure (shed imports before answers).

### Stage 3 — cells and regions (→ 1B users)

Goal: failure and growth are contained.

- **Cell architecture.** A cell is a complete stack (API, workers, Postgres,
  search, graph, cache) sized for ~1M users; tenants are placed in a cell, and
  a cell is added rather than an existing one grown.
- **Global control plane.** Identity, tenant→cell routing, billing, and quotas.
- **Multi-region active-active** with data residency per tenant.
- **Cost controls as product.** Per-plan model budgets, cached answers for
  repeated questions on unchanged memory, small models for drafts and
  extraction, large models only where judged necessary.
- Operational maturity: per-cell canaries, chaos testing, capacity planning.

---

## Next steps, in order

1. **Retrieval pushdown** (stage 1). It is the ceiling that grows with every
   import, and it needs no new infrastructure: ArcadeDB has a vector index.
2. **Postgres behind `app/core/database.py`.** Removes the single-writer,
   single-host limit; the SQL surface to port is small.
3. **Redis for the process-local state** listed above. Then a second API
   instance is safe.
