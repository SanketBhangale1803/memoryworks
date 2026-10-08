# Roadmap

Waves run in order. A task in a later wave can start planning early, but it isn't approved for execution until the tasks it depends on are `done`.

## Wave 1: answer "what are the issues?" about MemoryWorks itself

| ID | Task | Pillar | Depends on |
|---|---|---|---|
| T-001 | Signal and Issue model, plus GitHub CI and PR-state signals | Situation | — |
| T-002 | Self-observability: MemoryWorks's own failures become signals | Dogfood | T-001 |
| T-003 | Situation report endpoint, and routing in Ask, Agent, and MCP | Situation | T-001 |
| T-004 | Dogfood eval: 40 real questions about this repo, scored in CI | Parity / eval | — |

**Exit:** the north-star acceptance test in `VISION.md` passes against a local workspace connected to this repository.

## Wave 2: in the action path

| ID | Task | Pillar |
|---|---|---|
| T-005 | `memoryworks/preflight` GitHub check plus a briefing comment on PRs | Mandatory |
| T-006 | Agent bootstrap: generated AGENTS.md/CLAUDE.md, plus Claude Code and Codex hooks | Mandatory |
| T-007 | Durable production storage and server-side chat history | Parity |
| T-008 | The outcome ledger re-ranks briefings; briefing-precision metric | Learning |

## Wave 3: depth and moat

| ID | Task | Pillar |
|---|---|---|
| T-009 | Sentry, PagerDuty, and Linear/Jira as signal sources | Situation |
| T-010 | Model-assisted entity reconciliation for conflicts | Parity |
| T-011 | Group and identity sync for permissions | Parity |
| T-012 | Credential broker: scoped, briefing-bound connector tokens | Mandatory |
| T-013 | Proactive digests: a daily situation report to Slack, and watches on issues | Situation |

Wave 2 and 3 briefs are written when Wave 1 is in review. Their scope will change based on what Wave 1 teaches us.
