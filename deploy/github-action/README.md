# MemoryWorks briefing — GitHub Action

Serves a MemoryWorks briefing for every pull request and posts it as a
**MemoryWorks briefing** Check run plus one sticky PR comment.

It runs on your runner, summarizes the pull request (title, description, and a
changed-file summary, capped at the 2,000-character `task` limit), and calls the
existing `POST /api/briefings` with `surface="github_check"`. There is no
separate GitHub briefing API; the verdict comes from the same deterministic
engine every other surface uses.

A briefing is advice, not authorization. The check can be required in GitHub,
but MemoryWorks never approves or merges the change.

## Install

1. Create a workspace API key in MemoryWorks and store it as the
   `MEMORYWORKS_API_KEY` secret. Its team scope decides which memories can be
   cited.
2. Copy [`example-workflow.yml`](example-workflow.yml) to
   `.github/workflows/memoryworks-briefing.yml`. Set `service` to the service the
   repository changes. Pin `uses:` to a reviewed commit SHA.
3. Optionally make **MemoryWorks briefing** a required status check.

## What the check reports

| Briefing | Check conclusion | Title |
|---|---|---|
| `proceed` / `proceed_with_context` | `success` | Proceed (with the cited context) |
| `requires_approval` | `action_required` (or `neutral` via `approval-conclusion`) | Requires a person's decision — MemoryWorks does not approve it |
| `no_memory`, with the API confirming retrieval was healthy | `neutral` | No applicable company memory — this is not permission |
| `no_memory` without that confirmation (today's API), API error, bad key, retrieval failure | `failure` | Briefing unavailable |

Today the API cannot tell an empty memory from a failed index, so an empty
briefing is reported as **briefing unavailable**, never as "company memory holds
nothing." When the API starts returning `failure_state` (`none`/`healthy`,
`partial`, `retrieval_failed`), the check uses it with no change here.

The summary always includes the `verdict`, `briefing_id`, service, head SHA, the
`requires_approval` reasons, and the cited `memory_id`s. Each id links to
`<app-url>/api/memory/units/<memory_id>`, which shows the memory to a signed-in
person who is allowed to see it and returns 404 to anyone else. When no service
is named, the check says the service-scoped history was not pulled.

## Idempotency

- One check run per head SHA. Re-running the workflow for the same SHA and the
  same request reuses that check and its briefing — no second ledger row.
- A changed title, description, or input on the same SHA serves a new briefing and
  updates the same check.
- A check that was **unavailable** is retried on re-run.
- A new commit gets its own check. The PR keeps one comment, edited in place.

## Disclosure

The check and comment are visible to everyone who can read the repository.
By default they contain the verdict, headline, approval reasons, and memory ids,
not memory text. The approval reasons can quote a policy subject or a recorded
review rule. `show-memory-text: "true"` adds memory subjects. Use an API key whose
team scope matches the repository's audience.

## Limits

- Fork pull requests receive no secrets and a read-only token; the example
  workflow skips them.
- `requires_approval` stays on the check until the pull request changes. Clearing
  it is a GitHub decision (an admin override or a non-required check); a
  commit-bound MemoryWorks approval does not exist yet.
- Closing the outcome when CI or the merge finishes is a separate workstream.
  Until then, report it with `orgmemory outcome <briefing_id> …`.
