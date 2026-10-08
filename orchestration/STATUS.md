# Status board

Only the orchestrator (Claude Code) edits this file, on the control branch `claude/credit-balance-question-2jp80s`.
States: `open` → `planning` → `approved` → `in-progress` → `in-review` → `done` | `blocked`

| Task | State | Plan | Executor (Muse) | PR | Notes |
|---|---|---|---|---|---|
| T-001 | in-review (phase A, changes requested) | plans/T-001 (codex draft, reviewed) | `muse/T-001a-signal-store` | [#1](https://github.com/SanketBhangale1803/memoryworks/pull/1) | Phase A = SKILLS 1–5, 8, 9 on `muse/T-001a-signal-store`. Phase B (6, 7) after A merges. See review in tasks/T-001.md |
| T-002 | open | — | — | — | Waits on T-001 for execution; planning may start |
| T-003 | open | — | — | — | Waits on T-001 for execution; planning may start |
| T-004 | approved | plans/T-004 | — | — | Dogfood eval. Branch `muse/T-004-dogfood-eval` |

## Orchestrator log
- 2026-10-08: Protocol, vision, roadmap, and Wave 1 briefs published. Next: the planner drafts T-001 and T-004.
- 2026-10-08: Muse dry run OK ("No approved work"). Added `run-agents.sh` to loop both agents from worktrees.
- 2026-10-08: T-001 plan reviewed and approved in two phases (storage/normalization first, then polling/webhooks). Next: planner takes T-002; Muse builds T-001 phase A.
- 2026-10-08: Codex removed from the loop at the user's request. Claude Code now writes plans and recipes on the control branch. The T-001 plan was copied here from `agents/codex-plans`. Next: Muse builds T-001 phase A; Claude plans T-004, then T-002/T-003.
- 2026-10-08 18:45 check-in: Reviewed PR #1 (T-001 phase A). Backend lint is clean; tests show 490 passed, 2 failed for environment reasons (DNS, also failing on main here). Requested changes: reject non-GitHub signal records whose source_ids come from untrusted metadata, and reuse orgops scoring helpers instead of copies. GitHub Actions CI did not run on the PR (only Vercel), so a person needs to check that Actions are enabled. Planned and approved T-004.
- 2026-10-08 19:00: PR #1 re-reviewed. Both threads are fixed and resolved. One item left: `black --check` fails on `tests/test_config_migration.py`. This was already failing on main, and my first review wrongly reported black as clean. Asked Muse to format it. Muse's prompt now covers review bodies, not just threads, and requires real exit codes.
