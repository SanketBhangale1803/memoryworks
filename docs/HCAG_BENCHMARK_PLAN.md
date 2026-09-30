# HCAG Benchmark Plan

## Goal

Make HCAG's contribution measurable and honest: every claimed improvement
must come from a harness run over committed datasets, with per-case results
available for audit, and losses reported as plainly as wins.

## Phase 1 — offline harness (shipped)

- `hcag/benchmarks/` package: metrics (Recall@k, Precision@k, MRR, nDCG@k,
  macro-F1), two systems under test sharing one scorer, four suites
  (multi_hop_incidents, temporal_retrieval, company_brain_eval,
  boundary_detection), reports in `benchmark_reports/latest.{json,md}`.
- Ablation discipline: baseline = flat lexical pool; HCAG = same memories
  in windows via the real `InMemoryWindowStore` + `BoundaryDetector` +
  `QueryPlanner`. The measured delta is the windowing/routing layer.
- Current standing is recorded in the committed report; as of harness v1,
  HCAG improves nDCG, evidence precision, multi-hop success, MRR, and
  temporal accuracy with zero regressions over 35 labeled cases. These are
  regression benchmarks, not leaderboard results — the datasets are small
  by design so they run in CI in milliseconds.

## Phase 2 — live external benchmarks (requires API keys)

- **LongMemEval** — `longmemeval_test/benchmark_longmemeval.py` exists and
  has archived prior results under `.benchmarks/longmemeval/results/`
  (single-session-assistant subset, local judge). Next: full-type runs and
  a summary imported into `benchmark_reports/`.
- **LoCoMo** — `locomo_test/` scripts exist; wire results into the same
  report format.
- **RepoQA** — planned; dataset not in this checkout. The harness lists it
  as skipped until real runs exist.

## Phase 3 — retrieval quality roadmap

Measured-first: each item lands only with a before/after harness delta.

1. IDF weighting in the shared scorer (both systems benefit; windowing
   delta stays isolated).
2. Embedding-based window routing using stored centroids when
   `OPENAI_API_KEY` is present, with the lexical router as fallback.
3. Contradiction/staleness-aware ranking penalties fed by MemoryWorks trust
   module.
4. Larger company-brain corpora generated from real ingested repositories
   (via the MemoryWorks exporter) with human-labeled relevance.

## Reporting rules

1. Never fabricate numbers or "beats frontier model X" claims.
2. Reports carry generation timestamps and per-case retrievals.
3. `hcag_beats_baseline` requires zero regressed metrics.
4. Skipped suites are listed with reasons.
5. If a change regresses a metric, the report ships anyway and the
   regression is named.
