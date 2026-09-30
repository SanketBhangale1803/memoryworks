# Approvals and agent safety

Agents and teammates can **propose**. Nothing changes company memory, a
repository's memory, or a connected tool until a **person** decides. There is
deliberately no tool, API key scope, or agent path that approves its own
proposal.

Everything waiting on a person appears in one place: **Approvals** in the
sidebar (with a count of what is waiting), which also shows the change inline
in the chat when an agent proposed it there.

## What waits for a decision

| Kind | Who proposes | Who decides | What approval does |
|---|---|---|---|
| **New memory** — a fact, incident, or decision | An agent (`propose_orgmemory_memory`, `…_incident`, `…_decision`) or a teammate | Owner or admin | The proposal becomes company memory, with the proposer and reason on record. Declining saves nothing. |
| **Agent change plan** — edits across memory spaces | Agent mode, or any client of `propose_orgmemory_changes` | Owner or admin | The plan's operations are applied exactly as previewed. Declining changes nothing. |
| **Repository refresh** — re-read a repository | Any member (`propose_repository_refresh`) | Owner or admin | The repository is re-ingested; memory updates when it finishes. |
| **Write to a connected tool** — e.g. posting to Slack | An agent or integration through the connector gateway | The person who requested it, or an admin | The connector call runs once, with its idempotency key. Argument values stay hidden until approved. |

The server enforces every rule in the "who decides" column. The interface only
hides buttons that would be refused.

## Where decisions happen

- **In the chat.** When Agent mode proposes changes, the plan appears in the
  conversation with **Approve** and **Decline**. Admins decide it there; the
  turn records the result.
- **In Approvals.** One inbox for all four kinds, updated every few seconds.
  Only kinds with something waiting are shown. Members see their own requests
  marked "With an admin".
- **From an agent, for an admin.** A browser agent acting for an owner or admin
  can list and resolve refresh requests and memory proposals
  (`list_orgmemory_approvals`, `resolve_orgmemory_approval`,
  `list_orgmemory_proposals`, `resolve_orgmemory_proposal`) through the same
  authorized endpoints as the buttons. These decision tools are not registered
  at all for members.

## API

```text
GET  /api/memory/proposals                         POST to propose
POST /api/memory/proposals/{id}/resolve            {"approved": true|false}   owner/admin
GET  /api/org/plans?status=pending_approval
POST /api/org/plans/{id}/approve | /reject                                    owner/admin
GET  /api/repository-refresh-requests              POST to request
POST /api/repository-refresh-requests/{id}/resolve {"approved": true|false}   owner/admin
GET  /api/connector-tool-calls
POST /api/connector-tool-calls/{id}/resolve        {"approved": true|false}   requester or admin
```

## Briefings are advice, not permission

`get_orgmemory_briefing` can return `requires_approval` for an intent such as
"restart the payments connection pool". That verdict tells the agent a person
must agree; it never grants permission, and the briefing records nothing as
approved. See the README's section on briefings.

## Membership

The loop starts with people: an owner invites teammates from **Settings →
People** (or the Getting started card). Invited teammates land in the workspace
when they sign in, can ask and propose from their own sessions or agents, and
owners and admins decide.

## Audit

Proposals, approvals, denials, applied plans, connector writes, memory
decisions, importer runs, and API-key lifecycle events are recorded in
`audit_events` with actor, project, summary, and payload, and shown under
**Approvals → Audit log**.

## Legacy action policy (off by default)

The backend still contains the typed action policy from the product's earlier
runbook direction (`agentgate_adapter/`, `/api/actions/*`): each proposed action
carries an `action_type`, unknown types fail closed, and production changes
escalate to admin approval. It has no page, stays behind
`ORG_MEMORY_ENABLE_ACTIONS=false`, never executes shell commands, and is slated
for removal.
