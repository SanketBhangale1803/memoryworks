# Status board

Only the orchestrator (Claude Code) edits this file, on the control branch `claude/credit-balance-question-2jp80s`.
States: `open` → `plan-ready` → `approved` → `in-progress` → `in-review` → `done` | `replan` | `blocked`

| Task | State | Planner (codex) | Executor (Muse) | PR | Notes |
|---|---|---|---|---|---|
| T-001 | approved (phase A) | plan 5e919e3 | — | — | Phase A = SKILLS 1–5, 8, 9 on `muse/T-001a-signal-store`. Phase B (6, 7) after A merges. See review in tasks/T-001.md |
| T-002 | open | — | — | — | Waits on T-001 for execution; planning may start |
| T-003 | open | — | — | — | Waits on T-001 for execution; planning may start |
| T-004 | open | — | — | — | Independent |

## Orchestrator log
- 2026-10-08: Protocol, vision, roadmap, and Wave 1 briefs published. Next: the planner drafts T-001 and T-004.
- 2026-10-08: Muse dry run OK ("No approved work"). Added `run-agents.sh` to loop both agents from worktrees.
- 2026-10-08: T-001 plan reviewed and approved in two phases (storage/normalization first, then polling/webhooks). Next: planner takes T-002; Muse builds T-001 phase A.
