# Buyer panel: MemoryWorks (company memory and pre-action briefings for engineering teams and their AI agents)

**0 buy · 20 pass** (0% buy) out of 20 simulated buyers. Seed 7, so the same cards can be dealt again.

These are simulated buyers, not customers. Use this to find objections and weak spots, then confirm the big ones with real people before you spend.

## By segment

| group | buyers | buy rate |
| --- | ---: | ---: |
| Platform or SRE lead at a larger company (300+ engineers), needs security review and procurement | 4 | 0%  (thin) |
| Engineering manager at a scale-up (50 to 300 engineers), has a team tooling budget | 7 | 0%  (thin) |
| Small agency or consultancy lead (under 10 engineers) | 2 | 0%  (thin) |
| Startup CTO or founding engineer (10 to 50 engineers), signs the card themselves | 7 | 0%  (thin) |

## By buying behaviour

| group | buyers | buy rate |
| --- | ---: | ---: |
| Incident-scarred | 3 | 0%  (thin) |
| Budget-constrained | 2 | 0%  (thin) |
| Tool-fatigued | 3 | 0%  (thin) |
| Build-it-ourselves | 3 | 0%  (thin) |
| Bottom-up trialer | 2 | 0%  (thin) |
| Security gatekeeper | 3 | 0%  (thin) |
| AI-agent early adopter | 4 | 0%  (thin) |

## By income

| group | buyers | buy rate |
| --- | ---: | ---: |
| $238,000 and up | 7 | 0%  (thin) |
| $191,000 to $238,000 | 7 | 0%  (thin) |
| under $191,000 | 6 | 0%  (thin) |

## Why they pass

| reason | buyers | in their words |
| --- | ---: | --- |
| trust | 14 | "The repeat-outage thing still stings, so surfacing old postmortems before a change is honestly the part that caught my eye. But a hosted 'early preview' that wants read access to our Slack, Drive and GitHub is not getting through our security review this month, and our agents aren't touching anything risky enough yet to make me fight procurement for it." (P001) · "You're asking me to pipe our GitHub, Slack, Drive and Teams into a hosted service that's in early preview, from a company I've never heard of. That won't get through my own security review, never mind procurement, and at $20 a seat across 300+ engineers I can't show finance a concrete saving." (P002) |
| habit | 4 | "We're under ten engineers. Half of what this does I'd stitch together myself in a weekend with a vector DB and an MCP server, and the rest is already in Notion and Confluence search. If it's MIT, I'd clone it, poke at it, and maybe self-host it. I'm not paying $20 a head for a cloud version that's still in early preview." (P004) · "It's MIT and runs in Docker, so if I want it at all I'll have someone stand it up on a spare box over a weekend and pull the parts we like into our own MCP server. I'm not putting a card down for an early preview of something we can self-host or rebuild with a vector DB." (P006) |
| need | 1 | "Our agents mostly write tests and refactor small things, nothing risky enough to need a pre-change briefing yet. If I touch it at all it's the free self-hosted version on one repo, and I'm not paying for a hosted preview in month one." (P019) |
| price | 1 | "The agent briefing and blast-radius idea is exactly what I've been hunting for, so I'd spin up the free self-hosted version on one team's repos this month. But I wouldn't put $20 per engineer per month for an early-preview hosted product in front of finance until a pilot proves it cuts the 'who owns this?' churn and catches a bad agent change." (P020) |

## Why they buy

| reason | buyers | in their words |
| --- | ---: | --- |

## What would flip a no

- A SOC 2 report plus permission-aware retrieval that respects our existing Slack/Drive ACLs, or one of my engineers self-hosting it and showing me it would have flagged the postmortem behind our last repeat outage.
- A SOC 2 Type II report and a paying reference customer my size, plus self-hosting that runs fully inside our VPC. Then I might pilot the free MIT version with one platform team and only pay if it measurably cuts incident time or bad agent changes.
- A self-hosted pilot on one team showing that it actually caught a repeat incident or stopped a bad agent change within a month, plus pricing based on the team or on agent usage instead of a per-seat charge on every engineer.
- Seeing it stop an agent from repeating a real past incident on one of our own repos, on a self-hosted install that took an afternoon. And per-client isolation, so I could sell it into client engagements.
- A self-hosted pilot where it actually flags the incident we repeated, from our own postmortem, before an agent touches that code, plus a SOC 2 report or a named reference customer my size on the paid cloud.
- Running the free self-hosted version on our real repos and Slack for two weeks and seeing it correctly answer 'who owns this service?' and catch an agent change that would have repeated a past incident, with nobody on my team doing extra data entry.
- If the free self-hosted version, run on one repo, actually briefed our agents on a past incident before it repeated within the two-week trial, and the hosted version had a clear security story (SOC 2 or data never leaving our own cloud), I'd pay for the team.
- A SOC 2 Type II report and signed DPA for the managed cloud, or a paid self-hosted tier with support so our data never leaves our infra, priced flat instead of per engineer.
- A SOC 2 Type II report with a GA hosted version (or a supported self-hosted enterprise tier), plus a pilot where it surfaces our existing postmortem before a change that would have repeated that outage.
- A SOC 2 Type II report and a signed DPA for the managed cloud, plus proof on our own repos that the pre-change briefings actually stop our Claude Code agents from repeating past incidents.
- A self-hosted trial where it correctly answers our ownership and past-incident questions with real sources for a few weeks, plus a SOC 2 or clear data-handling story for the hosted version.
- Running the self-hosted version for a month and seeing it actually stop an agent from repeating a past incident, plus pricing per org or per agent instead of per engineer, with SSO and a data-retention story finance and security will sign.

20 buyers gave all four price answers. Run founder-pricing's van_westendorp.py on the answers folder.
