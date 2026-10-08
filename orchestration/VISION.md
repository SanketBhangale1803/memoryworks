# MemoryWorks: the default company brain

> Agents do the work. They don't do it without MemoryWorks.

## The test we fail today

A person asks: **"What are the issues in the system?"**

Today they ask a coding agent. The agent rereads the repository from scratch, guesses, and forgets everything when the session ends. MemoryWorks can't answer either. It holds *memory* (decisions, incidents, owners, conflicts), but it has no *live state*:

- `backend/app/connectors/github/client.py` syncs commits, files, issues and PRs. It never reads **check runs, workflow runs, deployments, or review state**.
- The PagerDuty importer (`backend/app/importers/registry.py`) exists, but nothing turns pages into open problems.
- MemoryWorks doesn't record **its own** failures (connector 403s, failed import jobs, LLM timeouts) as knowledge it can answer from.
- `orgops/watch.py` checks only memory hygiene: `blockers`, `conflicts`, and `stale`. It never checks system health.

**North-star acceptance test:** connect MemoryWorks to its own repository, CI, and runtime. Ask "What are the issues in the system?" It should answer in under 10 seconds with a ranked list of open problems. Each problem has evidence (a failing check, an error, a stale sync), an owner, when it started, what it blocks, and the prior memory that explains it. It should match or beat what a coding agent finds by reading the repo cold. Every task in `ROADMAP.md` moves this test forward or removes a parity blocker.

## Why this beats Glean (and Guru, Dashworks, Copilot connectors)

Glean answers *"where is the document about X?"* Its moat is connector breadth plus a permission-aware index. We won't win on breadth. We win on four things Glean structurally can't do:

| | Glean-class search | MemoryWorks |
|---|---|---|
| Unit of knowledge | Document or passage | Typed, scoped, time-valid **memory** with provenance, plus live **signals** |
| Time | Latest index | Current truth: what changed, what got superseded, and when (`SourceRevision`, `MemoryChangeSet`) |
| Position in the workflow | Beside the work (a search box) | **In the path** of the work: a pre-action briefing, a required check, a credential broker |
| Learning | Click signals | The **outcome ledger**: which context produced correct action *here* |
| State | Static knowledge | Knowledge **and** live operational state ("what's broken right now, and why it broke last time") |

The compounding asset is the outcome ledger. Anyone can index the same Slack. Only a system that sits in the action path sees which context led to a correct action, so only that system gets better with use. That's why "not without MemoryWorks" is both the product and the moat.

## Five pillars

### 1. Situation: live state, not only memory
Add a `Signal` layer. A signal is something observed now: a failed check run, a deploy, a page, a red sync, an error spike, a PR waiting on review. Signals cluster into **Issues**: deduplicated, ranked open problems, each with an owner, `first_seen`, a blast radius, and links to the memories that explain it (prior incidents, decisions, runbooks). Signals expire. Memories don't, but their validity windows do. Questions like "what's broken", "what are the issues", or "is X healthy" route to a deterministic **situation report**. They don't go through passage retrieval.

### 2. Dogfood: MemoryWorks is its own first customer
Its connector syncs, background jobs, LLM calls and API errors become signals in its own workspace. When the product breaks, the product is the first to know, and it can explain why. This is the cheapest way to make pillar 1 real and to demo it.

### 3. Mandatory: in the action path, not beside it
"Not without MemoryWorks" needs enforcement points. Hoping an agent calls a tool isn't enough:
- **PR preflight check.** `memoryworks/preflight` is a GitHub required status check. It briefs every PR on its head SHA, posts the briefing as a PR comment, and fails when the verdict is `requires_approval` and no person has approved.
- **Agent bootstrap.** **Sources → AI tools** generates `AGENTS.md`/`CLAUDE.md` blocks and hook configs for Claude Code, Codex, and Cursor. A pre-tool hook calls the briefing before consequential commands, and an end-of-session hook records the outcome.
- **Credential broker** (later). Agents get short-lived, scoped connector tokens *from* MemoryWorks, and only against a briefing. The brain holds the keys, so skipping it means having no access.

### 4. Learning: the ledger turns into better briefings
Close the loop that exists in `outcomes/ledger.py` and `skills/library.py`. Outcomes re-rank which memories appear in briefings. Failed outcomes raise the weight of the constraints that were ignored. We measure this with **briefing precision** (cited memories later marked useful) and **prevented repeats** (a briefing cited a prior incident and the outcome succeeded).

### 5. Parity: the boring blockers
- Durable production storage. The Vercel deployment keeps SQLite in `/tmp` and can reset (`README.md` → Production deployment).
- Server-side chat history. It lives in the browser today.
- Identity and group sync (SCIM, Google and Okta groups), so permissions match the source systems.
- Model-assisted entity reconciliation for conflicts. Matching is subject-string only today.
- Signal connectors: GitHub Actions and Deployments, Sentry, PagerDuty, Linear or Jira. Breadth beyond these comes from `connectors/remote_mcp.py` (any MCP server becomes a source), not hand-built adapters.
- A public eval: a dogfood benchmark of real questions about this repository, scored in CI, so "better than Glean" is a number.

## Non-goals

- Becoming a coding agent. Agents are replaceable. The brain is durable.
- Auto-applying changes. A person approves consequential actions, every time.
- Chasing connector count.
