# T-004 executor recipe

Branch: `muse/T-004-dogfood-eval`, cut from `origin/main`. One PR. Work in `backend/` with `backend/.venv`. Put scratch files under `$MUSE_SCRATCH` only.

1. **Read the pattern.** Read `backend/evals/briefing_eval.py` (all of it) and `backend/evals/briefing_cases.json`. You'll copy `_hermetic()` and the session, workspace and API key setup exactly. Read `README.md` and every `docs/*.md` except `docs/archive/`; that's the corpus you write questions about.
   **Verify:** `cd backend && .venv/bin/python evals/briefing_eval.py | tail -12` runs, and you save the output to `$MUSE_SCRATCH/briefing_eval.txt`. This proves the hermetic pattern works on your machine.

2. **Write the question file with its validator first.** Create `backend/evals/dogfood_questions.json` in PLAN.md's format: 40 questions, at least 5 `no-answer`, exactly 5 `situation` (with `"requires": "situation_lane"`), and at least 5 in each of `ownership`, `decision`, `how-it-works` and `history`. Every fact needs an `evidence` entry whose `contains` is a literal substring, copied from the file, of at most 80 characters. Write questions the way an engineer on this repo would ask them, not by rephrasing a heading.
   Create `backend/evals/dogfood_eval.py` with only `--validate` implemented: schema, category counts, unique ids, that every `evidence.path` exists relative to the repo root, and that every `contains` is found in that file. Exit 1 and print each failing question id otherwise.
   **Verify:** `cd backend && .venv/bin/python evals/dogfood_eval.py --validate` exits 0 and prints `40 questions valid`.

3. **Implement the run and scoring.** Add the corpus ingestion, the `/api/ask` loop and the per-question scoring and summary exactly as PLAN.md defines them, plus `--json`, the default output path, and a printed table in the style of `briefing_eval.py`'s `_print`. Situation questions are reported as skipped.
   **Verify:** run it twice, and the two summaries' `fact_recall`, `citation_precision` and `abstention_accuracy` are identical. Paste both summaries into `$MUSE_SCRATCH/t004-runs.txt`.

4. **Baseline, transcript scoring, and the test.** Add `--write-baseline`, `--check-baseline` (0.05 tolerance on the three quality metrics; latency is never gated) and `--score-transcript`. Run `--write-baseline` once and commit `backend/evals/dogfood_baseline.json`.
   Add `backend/tests/test_dogfood_eval.py` with these tests:
   - `test_question_file_validates`: runs the validator in-process.
   - `test_check_baseline_fails_on_regression`: writes a temporary baseline 0.10 above the committed one and asserts exit 1; asserts exit 0 against the committed baseline. To keep it fast, run the scorer on a 3-question subset through a `questions_path` and `limit` parameter. Don't run all 40 in the test suite.
   - `test_transcript_scoring_uses_same_metrics`: a fixed 3-answer transcript gives the expected numbers.
   **Verify:** `cd backend && .venv/bin/python -m pytest tests/test_dogfood_eval.py -q` passes, then ruff and black on `evals/` and the new test pass.

5. **Wire it in.** Add the `eval-dogfood` target to `Makefile` and `.PHONY`. In `.github/workflows/ci.yml`, add the `Dogfood eval` step to the backend job after `Tests`, with the exact commands from PLAN.md. Add `backend/evals/dogfood_last_run.json` to `.gitignore`. Add the "Dogfood eval" section to `docs/BENCHMARKS.md`.
   Note that the CI lint step runs `ruff check app tests scripts`, which does not cover `evals/`. Run ruff and black on `evals/` yourself anyway.
   **Verify:** `make eval-dogfood` works from the repo root. Time it with `time` and record the duration.

6. **Full CI.** Run every command from `.github/workflows/ci.yml` (backend, frontend, SDK, MCP), plus the new eval step. Never weaken a test.

7. **Report.** Write `orchestration/reports/T-004.md` covering each step's outcome, the real command output, the summary metrics, the per-category breakdown, and the five worst-scoring questions with why they miss. Those misses are the input for later retrieval work. Open the PR `T-004: Dogfood eval scored in CI`.
