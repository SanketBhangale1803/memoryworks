# Competitive Differentiation

## What incident tools do

PagerDuty, Rootly, incident.io, Opsgenie, and similar products coordinate
humans around incidents: on-call scheduling, alert routing, incident
declaration, status pages, retrospectives, workflow automation, AI
summaries and triage. They orchestrate the response; they do not model how
the company's systems actually work.

## What Runbook is

Runbook is the **executable company brain**: it turns scattered company
knowledge — repositories, issues, PRs, CI configuration, Slack threads,
logs, incident history — into a living graph plus executable,
approval-gated runbooks that AI agents can act on safely.

| Capability | Incident tools | Runbook |
|---|---|---|
| System understanding | tags/service catalogs maintained by hand | live graph compiled from repos, configs, issues, PRs, Slack |
| Answers | AI summaries of the current incident | evidence-grounded answers with citations, confidence, trust score, graph trace |
| Procedures | wiki runbooks, manual upkeep | extracted from evidence, versioned, cited, drift-checked |
| Staleness | unnoticed until a 3am failure | drift signals from real source changes (`fresh` → `stale`) |
| Change context | timeline of alerts | change-to-incident correlation ranking suspicious PRs/issues by shared services/env vars/files |
| Impact analysis | manual dependency docs | blast-radius traversal over real dependency edges |
| AI agents | summarize and page humans | typed action taxonomy; safe actions allowed, risky actions approval-gated, everything audited |
| Institutional memory | postmortem documents | operational memories with provenance, approval, and last-verified timestamps |
| Trust | implicit | explicit trust scores from source quality, recency, support breadth, contradictions |
| Rehearsal | game days | simulation mode: dry-run any runbook through the real policy engine |

## The wedge

Incident tools answer "who do we page and how do we communicate?" Runbook
answers the questions that actually resolve incidents:

- Why is this service failing? (evidence + likely cause + confidence)
- What changed recently? (correlated PRs/issues with overlap reasons)
- Which previous incident looked like this? (graph + retrieval)
- Which runbook applies, and is it still current? (drift status)
- What is an agent allowed to do about it? (policy decision per step)
- What breaks if we restart it? (blast radius)

## Migration, not rip-and-replace

`app/importers` provides the `IncidentToolImporter` interface for
PagerDuty (live), Rootly, incident.io, Opsgenie, Statuspage, Jira Service
Management, and ServiceNow — imported incidents flow through the same
evidence pipeline as every other source. Importers without credentials say
`Not connected`; nothing fakes a migration.

## Honesty as a feature

Every answer shows its evidence or explicitly refuses
(`I do not have enough evidence to answer this confidently.`). Trust scores
expose contradictions instead of hiding them. Benchmarks report losses.
This is the property that lets a company hand Runbook to an AI agent.
