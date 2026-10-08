# T-004: Dogfood eval. Real questions about this repository, scored in CI
Status: approved

## Problem
Nothing in CI measures whether MemoryWorks answers questions about a real engineering codebase correctly. `backend/evals/briefing_eval.py` scores briefings on a fictional corpus (`backend/evals/briefing_cases.json`), and it isn't run in CI (`.github/workflows/ci.yml` has no eval step). `docs/BENCHMARKS.md` describes the HCAG harness, which lives in a sibling repository. So "better than Glean" and "better than asking a coding agent" aren't numbers yet (`orchestration/VISION.md`).

## Design

### Corpus: this repository's own documentation
The corpus is a fixed, stable slice of this repo: `README.md` plus every `docs/*.md` except `docs/archive/`. Each file is ingested with the real `IngestionService.ingest_item` (as `briefing_eval.py:92` does), with `source_type="document"`, `title=<repo-relative path>`, and `source_id=f"file:{path}"`. Code files are left out on purpose. Docs change less often than code, so the gold answers stay valid longer, and when they do drift the eval fails loudly. That's the point of it.

### Question set: `backend/evals/dogfood_questions.json`
```json
{
  "corpus": {"include": ["README.md", "docs/*.md"], "exclude": ["docs/archive/*"]},
  "questions": [
    {
      "id": "q01-briefing-no-model",
      "category": "how-it-works",
      "question": "Does a model run when MemoryWorks builds a pre-action briefing?",
      "facts": [{"any_of": ["no model", "deterministic"]}],
      "gold_sources": ["file:README.md"],
      "evidence": [{"path": "README.md", "contains": "No model runs in this path"}]
    }
  ]
}
```
- `category` is one of `ownership`, `decision`, `how-it-works`, `history`, `no-answer`, `situation`.
- `facts`: each fact passes when the answer text (casefolded) contains at least one of its `any_of` phrases (also casefolded). Keep phrases short and literal.
- `gold_sources`: the `source_id`s a correct answer may cite.
- `evidence`: for every fact, the file and a literal substring proving the gold answer. The validator checks every `contains` string really appears in that file, so a gold answer can't be invented.
- `no-answer` questions have `facts: []`, `gold_sources: []` and ask about something the corpus doesn't contain, such as "What is the on-call rotation for the billing team?". The correct behaviour is to abstain.
- `situation` questions ("What are the issues in the system?") get `"requires": "situation_lane"` and are reported as `skipped (needs T-003)` until that lane exists.

40 questions: at least 5 `no-answer`, exactly 5 `situation`, and the rest spread over the other four categories with at least 5 each.

### Runner: `backend/evals/dogfood_eval.py`
Copy the structure of `briefing_eval.py`: `_hermetic()` (same settings), a throwaway SQLite file, an in-memory graph, no model keys, a dev session plus a workspace-scoped API key, and the real `TestClient(app)`.

- `--validate` checks the question file only (schema, counts, and that every `evidence.contains` is found in its file) and exits 0 or 1. It doesn't ingest anything.
- The default run ingests the corpus, then for each question calls `POST /api/ask` with `{"project_id": ..., "query": ..., "surface": "eval", "scope": "project"}`, timing each call.
- Scoring per question:
  - `fact_recall` = facts matched / facts. Skipped for `no-answer`.
  - `citation_precision` = |cited ∩ gold_sources| / |cited|, where `cited` = the distinct `source_id`s in `response["evidence"]`. Skipped when nothing is cited, and for `no-answer`.
  - `abstained` = `not response["answer_sufficient"]`. For `no-answer` questions the question passes only if it abstained. For the others, abstaining counts as a miss.
  - `latency_ms`.
- Summary: `fact_recall` (mean), `citation_precision` (mean), `abstention_accuracy` (correct abstentions over no-answer questions, plus correct non-abstentions over answerable questions, divided by all scored questions), `p50_ms`, `p95_ms`, and `scored`/`skipped` counts.
- `--json PATH` writes the full report. Without it, the report is written to `backend/evals/dogfood_last_run.json`, which is git-ignored.
- `--check-baseline backend/evals/dogfood_baseline.json` exits 1 if `fact_recall`, `citation_precision` or `abstention_accuracy` falls more than **0.05** below the baseline. Latency is reported but never gated, because CI machines vary.
- `--write-baseline` writes the current summary's three quality metrics to the baseline file.
- `--score-transcript PATH` scores a recorded answer set `[{"id": "q01-...", "answer": "...", "cited": ["file:README.md"]}]` with the same metrics, with no ingestion. This is how a cold coding agent's answers become a comparable number.

### Wiring
- `Makefile`: add an `eval-dogfood` target that runs `cd backend && .venv/bin/python evals/dogfood_eval.py`, and add it to `.PHONY`.
- `.github/workflows/ci.yml` backend job: after `Tests`, add a step `Dogfood eval` that runs `python evals/dogfood_eval.py --validate && python evals/dogfood_eval.py --check-baseline evals/dogfood_baseline.json`.
- `.gitignore`: add `backend/evals/dogfood_last_run.json`.
- `docs/BENCHMARKS.md`: add a short "Dogfood eval" section covering what it measures, how to run it, and the baseline rule.

### Not in scope
No retrieval or answer changes. This task only measures. If a score is low, that's a finding for the report, not something to tune in this PR.

## Risks
- **Flaky scores.** The run is hermetic and deterministic (no model, lexical retrieval), as `briefing_eval.py` already shows. If two consecutive runs differ, that's a bug to report, not something to paper over with tolerance.
- **CI time.** About 19 documents plus 40 asks. It must finish in under 3 minutes on CI. Report the measured time.
- **Gold answers drifting from docs.** `--validate` runs first in CI and points to the exact question whose evidence disappeared.

## Acceptance
- `cd backend && .venv/bin/python evals/dogfood_eval.py --validate` exits 0.
- `cd backend && .venv/bin/python evals/dogfood_eval.py` prints the per-question table and the summary, and writes `dogfood_last_run.json`.
- Running it twice gives an identical summary, apart from latency.
- `--check-baseline evals/dogfood_baseline.json` exits 0 on this branch. When a temporary copy of the baseline is edited 0.10 higher, it exits 1. The test file asserts this.
- The full CI set is green.
