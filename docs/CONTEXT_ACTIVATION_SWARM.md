# Context activation swarm

MemoryWorks retrieves answer context through a small ecosystem of independent
specialists. The swarm does not generate facts. It activates source-backed
evidence, critiques the combined result, and gives downstream reasoning one
authorized, token-bounded context.

## Runtime

```text
query + route + authorization boundary
  ├─ sensory activation ─ hybrid semantic/lexical/temporal retrieval
  ├─ graph forager ───── bounded query-seeded relationship traversal
  └─ current historian ─ current atomic memories mapped back to sources
                         │
                         ▼
                 immune-system critic
          deduplication, consensus, contradictions
                         │
                         ▼
                   context compiler
       source diversity + hard evidence token budget
                         │
                         ▼
             existing grounded answer pipeline
```

Domain specialists join this core ecosystem for API contracts, repository
briefings, change history, and procedure validity. Core specialists run
concurrently and fail independently. A failed specialist marks the activation
run `degraded`; evidence from healthy specialists remains usable.

The specialists currently use deterministic, inspectable tools. Their typed
`AgentReport` contract deliberately separates agent execution from
orchestration, so model-backed specialists can be added without changing the
critic, compiler, authorization boundary, or answer API.

## Security invariant

Every specialist output is filtered against source/team scope before it enters
the critic or context compiler:

```text
authorized evidence = candidate source IDs ∩ caller-visible source IDs
```

The existing post-retrieval security trim remains as defense in depth. Neither
graph distance nor agreement between agents can broaden the caller's scope.

## Graph forager

The graph specialist:

1. finds typed entity nodes matching query terms;
2. performs deterministic breadth-first traversal over a knowledge-bearing
   relationship allow-list;
3. stops at a configurable bound of one to five hops;
4. returns only paths that terminate at a `KnowledgeChunk`;
5. records the seed, hop count, and exact traversed edges on the evidence.

Execution and approval relationships are excluded. Graph proximity helps find
evidence, but never becomes evidence by itself.

## Critic and compiler

The critic rejects empty evidence, consolidates duplicate content, records
which specialists agreed, and exposes explicit contradictory causal claims.
The compiler then:

- preserves source diversity;
- applies a single evidence budget, reserving 20% (up to 1,000 tokens) for the
  downstream answer;
- truncates the final admitted source when needed;
- never emits more context than its evidence budget;
- stores the exact compiled context and selected IDs.

The configured LLM receives that exact compiled context. The extractive
fallback consumes the same selected evidence objects. Sufficiency checks,
citations, trust scoring, and abstention behavior remain unchanged.

## Durability and inspection

Each compilation creates a `context_activation_runs` record containing:

- agent reports and per-agent latency/status;
- critic decisions and contradictions;
- selected evidence/source IDs;
- the exact compiled context;
- token allocation and omissions.

The resulting `ContextEnvelope` records the compiled context and activation run
IDs, and its source version vector includes sources selected from chunk
evidence—not only sources attached to promoted memories.

Authorized callers can inspect a run at:

```text
GET /api/memory/swarm/{run_id}
```

The Ask UI exposes the active run status, healthy-agent count, and compiled
token usage inside the dynamic context panel.
