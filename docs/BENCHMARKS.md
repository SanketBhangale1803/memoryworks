# Benchmarks

MemoryWorks retrieval is measured two ways: the HCAG harness for routing and
windowing, and a briefing evaluation for pre-action briefings.

```bash
make benchmark        # from the repository root — delegates to ../hcag
# or
cd ../hcag && make benchmark && make benchmark-report
```

Reports are written to `hcag/benchmark_reports/latest.{json,md}` and returned
by `GET /api/benchmarks` (there is no page for them in the product). If no report
exists the endpoint says so — it never returns fabricated numbers.

Briefing quality is measured with known-answer cases:

```bash
backend/.venv/bin/python backend/evals/briefing_eval.py
```

Run it before and after any retrieval or briefing change and compare.

## What is measured

The harness compares a flat lexical baseline against the HCAG windowed
pipeline over the same committed labeled datasets with the same scorer, so
the delta isolates the routing/windowing layer. Suites:

- **multi_hop_incidents** — incident cause/config/ownership questions over
  a mixed-domain corpus with deliberate lexical distractors.
- **temporal_retrieval** — the temporally-correct memory must rank first.
- **company_brain_eval** — operational QA including unanswerable questions
  (abstention accuracy).
- **boundary_detection** — none/soft/hard transition F1 with the real
  `BoundaryDetector`.

Metrics: Recall@5, MRR, nDCG@5, evidence precision, answerability accuracy,
multi-hop success rate, temporal accuracy, boundary macro-F1, latency.

## Honesty rules

- Per-case retrievals ship inside `latest.json` for audit.
- `hcag_beats_baseline` is true only with zero regressed metrics.
- Regressions are listed by name when they happen.
- External suites (LoCoMo, LongMemEval, RepoQA) are reported as skipped
  with reasons when their datasets/API keys are absent.

See `hcag/docs/BENCHMARKS.md` for methodology detail and
`docs/HCAG_BENCHMARK_PLAN.md` for the plan and roadmap.
