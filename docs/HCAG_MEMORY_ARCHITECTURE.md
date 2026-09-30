# HCAG continuous-memory architecture

## Product objective

MemoryWorks is a project-scoped company memory, not a long prompt. New evidence is
indexed continuously, repeated observations consolidate, stale evidence loses
retrieval priority, and every answer remains attributable to current sources.
Retrieval is read-only: asking a question never makes a claim more authoritative.

## Research translated into the product

### Multi-timescale memory dynamics

[Continual Knowledge Updating in LLM Systems](https://arxiv.org/abs/2605.05097)
models an association with coupled fast and slow variables. A recent event lifts
the fast variable immediately; repeated events raise the slow variable; absence
allows both to decay. MemoryWorks adopts this at the evidence-version level:

- ingestion reinforces the fast signal;
- repeated observation of the same source version consolidates the slow signal;
- a changed source version gets a new fast trace and inherits only part of the
  old slow signal;
- retrieval reads these signals but never writes them.

The paper's evaluation is intentionally small and does not report retrieval QA
metrics. MemoryWorks therefore exposes the state in retrieval traces and treats it
as a ranking feature, not as proof of improved accuracy.

### Compressed and sparse selection

[DeepSeek-V4](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) alternates
Compressed Sparse Attention (CSA) with Heavily Compressed Attention (HCA) inside
the model. MemoryWorks cannot add those neural attention kernels to an API model.
It adopts the useful system pattern instead:

1. **Sparse exact lane** - high-resolution lexical, service, canonical-source,
   and intent matches.
2. **Compressed global lane** - compact indexed terms and vectors provide broad
   recall over the project library.
3. **Temporal memory lane** - source freshness plus fast/slow evidence state
   reorders evidence that is already relevant.

These lanes are fused before source diversity selection. The API labels the
strategy `sparse_compressed_temporal_v1` and explicitly states that this is an
application retriever, not a model attention implementation.

### Graph retrieval and continuous organization

- [HippoRAG 2](https://proceedings.mlr.press/v267/gutierrez25a.html) motivates
  combining passage retrieval with graph propagation rather than replacing
  factual retrieval with graph traversal.
- [Zep / Graphiti](https://arxiv.org/abs/2501.13956) motivates temporal edges and
  preserving historical relationships for enterprise memory.
- [A-MEM](https://arxiv.org/abs/2502.12110) motivates structured notes and links
  that can evolve when new memory arrives.
- [SYNAPSE](https://arxiv.org/abs/2601.02744) motivates bounded spreading
  activation, temporal decay, and hybrid graph/vector retrieval.
- [DYNA](https://arxiv.org/abs/2606.15778) provides further early evidence for
  temporal knowledge graphs as an external, retraining-free memory substrate.

MemoryWorks keeps typed ArcadeDB relationships and source provenance. Fast/slow
weights influence only relevant evidence; they cannot create relevance, bypass
workspace scope, or convert repetition into verification.

## Accuracy contract

An answer is eligible only when current retrieved evidence crosses the existing
sufficiency boundary. Consolidation improves ordering but does not change the
following rules:

- unsupported questions abstain;
- citations come only from the selected evidence pack;
- contradictory sources remain visible;
- stale or repeated content is not equivalent to human verification;
- project and workspace boundaries apply before retrieval;
- consequential actions still require AgentGate approval.

## Context activation swarm

HCAG routing now hands each question to a concurrent context-activation swarm.
Hybrid activation, bounded graph traversal, and current-truth retrieval are
isolated specialists. Their authorized evidence is combined by an
immune-system critic and one token-budgeted context compiler before the
existing grounded answer pipeline runs.

This makes graph traversal real rather than a post-selection explanation:
query-matched entity nodes seed a bounded breadth-first walk whose paths must
terminate at source chunks. Each path is retained in the retrieval trace.

Every run is durable and independently inspectable, and one specialist may
fail without discarding healthy evidence. See
[`CONTEXT_ACTIVATION_SWARM.md`](CONTEXT_ACTIVATION_SWARM.md) for contracts,
security boundaries, persistence, and API details.

## Evaluation required before claiming improvement

Measure the new strategy against the prior ranker on a versioned corpus:

1. Recall@5 and nDCG@10 for direct factual questions.
2. Multi-hop answer accuracy and graph-path precision.
3. Temporal ordering accuracy for “current”, “before”, and “after” questions.
4. Stale-evidence selection rate after a source update.
5. Contradiction recall and unsupported-question abstention precision.
6. P50/P95 retrieval latency as the number of chunks grows.

No production accuracy claim should be made until these results exist on real
company repositories and operational documents.
