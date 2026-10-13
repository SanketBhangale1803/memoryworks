# Follow-up interview: what is inevitable

20 of 20 simulated buyers answered. Simulated buyers, not customers: use this to choose what to test.

## The inevitable need, by category

| answer | buyers |
| --- | ---: |
| agent_guardrails | 9 |
| agent_context_delivery | 4 |
| incident_memory | 2 |
| agent_audit_trail | 2 |
| ownership_and_dependency_map | 2 |
| access_control_for_agents | 1 |

## What forces it

| answer | buyers |
| --- | ---: |
| agent_caused_incident | 13 |
| customer_security_review | 4 |
| repeat_incidents | 1 |
| team_growth | 1 |
| agent_scale | 1 |

Months until needed: median 9 (range 6 to 12).

## Form they would accept

| answer | buyers |
| --- | ---: |
| self_hosted | 16 |
| feature_inside_existing_tool | 4 |

## Pricing model they would accept

| answer | buyers |
| --- | ---: |
| flat_per_team | 19 |
| per_repo_or_service | 1 |

Team budget per month: median $1,200, range $150 to $3,000.

## Is it MemoryWorks?

| answer | buyers |
| --- | ---: |
| close_needs_changes | 20 |

## Must-haves (all mentions)

- Permission-aware retrieval that enforces our existing Slack/Drive/GitHub ACLs, with audit logs of who and which agent saw what (P001)
- Can run inside our own infrastructure (or comes with a SOC 2 Type II report and a clear data-residency story) so it clears security review (P001)
- Proven recall on our real history: it would have flagged the postmortem behind our last repeat outage, with citations, and stays quiet when nothing is relevant (P001)
- Runs entirely inside our VPC with no code, tickets or chat leaving our network (P002)
- Enforces at the merge/deploy chokepoint (GitHub rulesets, CI, deploy pipeline) so an agent cannot route around it, with policies we version in git (P002)
- Immutable audit log tied to our SSO identities that I can hand straight to an auditor (P002)
- Runs inside our own infrastructure, or at least never sends our code and Slack out to a vendor (P003)
- Lives in the CI/PR and agent flow we already use (GitHub checks, the agent's own tool calls), not a new UI my engineers have to open (P003)
- Every block or warning cites the specific incident, owner or doc it's based on, so engineers trust it and don't just override it (P003)
- Per-client isolation that holds up: separate stores and credentials per engagement, not a tag on shared data (P004)
- Runs on our own infrastructure or the client's, set up in an afternoon with Docker (P004)
- Works with the agents we already use (Claude Code, Cursor) through MCP, with no lock-in to a new chat UI (P004)
- Runs inside our infrastructure, or the vendor has proven security (SOC 2), so incident history and internal Slack never go to an unproven cloud (P005)
- Every warning cites the actual postmortem or incident and stays quiet when it has nothing, so my team doesn't learn to ignore it (P005)
- Sits in the change path for the agents we already use (Claude Code, Cursor) and for PRs, with an approval gate on risky areas (P005)
- Stays current with zero extra work from engineers, built only from commits, PRs, CODEOWNERS and Slack incident threads we already generate (P006)
- Plugs into the agents we already run through MCP, so the briefing lands in the agent's context before it edits and needs no new UI for engineers (P006)
- Every claim cites its source and it says 'I don't know' rather than guessing an owner or a constraint (P006)
- Strict per-client isolation, so one client's code, logs and context never show up in another client's work (P007)
- Data stays in our own cloud account or on our machines; nothing goes to a third party's servers (P007)
- Close to zero upkeep: set it up in an afternoon and it keeps running without someone babysitting it (P007)
- Runs inside our own infra or reads only what it strictly needs; no copy of source code or private Slack in someone else's early-stage cloud (P008)
- Every agent action tied to a named human approver, with an export I can hand straight to an auditor or attach to a questionnaire (P008)
- Works across the agents we already use (Claude Code, Cursor, our own scripts) without each engineer having to set it up (P008)
- Runs fully inside our network (self-hosted or VPC) with least-privilege, read-only, per-repo scoped access, and nothing leaves our boundary (P009)
- Enforced as a required check in CI / branch protection, with a deterministic block-or-approve outcome and an audit log of who approved what (P009)
- Every flag cites the specific postmortem or rule it came from, with a low enough false-positive rate that engineers don't start ignoring it (P009)
- Runs in our own infrastructure or our own cloud account, so code, Slack and incident data never leave our perimeter (P010)
- Enforced at the agent tool-call or CI level (hooks, MCP, branch protection), not advisory text the agent can ignore (P010)
- Exportable audit log of agent actions and human approvals that I can hand to an auditor or a customer's security team (P010)
- Every ownership or dependency answer cites where it came from (commits, CODEOWNERS, on-call rotation, incident threads), and it says 'unknown' rather than guessing (P011)
- Stays current on its own from existing systems, with no manual catalog upkeep for my team (P011)
- Exposed via an API or MCP so agents and our review bots can query it, not just a chat window for humans (P011)
- Runs inside our own infra or VPC, so incident history and Slack data never leave it (P012)
- Plugs into the agents we already use through MCP or a pre-PR hook with no workflow change, and every claim it makes cites a source (P012)
- Proven to catch real repeats: on our own history it flags the incidents and constraints a senior engineer would have flagged (P012)
- Runs inside our network or our cloud account, with scoped read-only access we control, so it clears security review (P013)
- Enforced in the paths engineers already use (CI checks, PR review, the agent's own tool calls) with zero extra steps or new UI for engineers (P013)
- Low noise: it blocks only on real risk tied to ownership and past incidents, and every block cites why (P013)
- Runs entirely inside our VPC or on our own infrastructure, with SSO, RBAC and no data leaving our network (P014)
- Hard approval gates tied to ownership and blast radius, enforced in the CI/merge path rather than as advice the agent can ignore (P014)
- Tamper-evident audit log of every agent action, prompt-to-merge, exportable to our SIEM (P014)
- Enforced inside the merge and deploy path we already use (GitHub checks, CI, CODEOWNERS), not a side chat window agents can ignore (P015)
- Deterministic, explainable rules: when it blocks or escalates it says exactly why and cites the incident, owner or constraint behind it (P015)
- Low false-positive rate, so engineers don't learn to click through it within two weeks (P015)
- Pulls from our actual incident history and postmortems and maps them to code paths and services, with a link to the source for every warning (P016)
- Plugs straight into Claude Code and Cursor (MCP or hook) with no change to how engineers run agents, and adds seconds, not minutes (P016)
- Low noise: it says nothing or 'I don't know' rather than padding every briefing with generic Slack chatter (P016)
- Runs inside our own infrastructure or VPC, so source code, incident history and Slack never leave our boundary (P017)
- Enforces, not just advises: it can actually block or require approval in the agent loop and in CI, and it works with Claude Code, Cursor and our own agents (P017)
- Every block or warning cites the specific past incident, PR or decision it came from, so engineers trust it and can override with a recorded reason (P017)
- Read-only and least-privilege by default, scoped per repo, with data staying in our infrastructure or a vendor we already trust (P018)
- Builds and refreshes itself from CODEOWNERS, on-call schedules and commit history, with every owner claim linked to its source, so no one has to curate it by hand (P018)
- Agents can query it through a standard interface (MCP or API) and it plugs into the code review and on-call tools we already run (P018)
- Plugs into Claude Code and Cursor (MCP is fine) with zero change to how engineers work; agents just get the context (P019)
- Builds and refreshes itself from the repo, PRs and incident tickets, not from a Drive folder someone has to curate (P019)
- Short, cited and low-noise: a few relevant lines with links, not a wall of text that bloats every prompt (P019)
- Runs inside the agent workflow (MCP or CI hook) and can block or require human approval, not just advise (P020)
- Ownership, prior incidents and blast radius pulled from our real repos, Slack and incident history, with citations I can check (P020)
- Self-hostable or a data-handling story that passes our security review without months of back-and-forth (P020)

## Deal-breakers (all mentions)

- Hosted-only early-preview product wanting read access to our entire Slack, Drive and GitHub history (P001)
- Noisy briefings or hallucinated 'prior incidents' that engineers learn to ignore within a month (P001)
- Per-seat pricing across all 300+ engineers when only the platform/SRE side really drives it (P001)
- Any hosted component that needs read access to our source code or Slack from a vendor without SOC 2 Type II and a DPA (P002)
- Per-engineer seat pricing across 300+ engineers when only a few teams run agents (P002)
- Adds noticeable latency or flaky blocks to CI that make engineers disable it (P002)
- Per-seat pricing across every engineer in the org (P003)
- Needs broad read access to all of Slack and Drive before it can show any value (P003)
- Noisy false positives that slow down normal PRs, because the team will route around it within a week (P003)
- Client code or Slack going through a vendor's hosted service, especially one still in preview (P004)
- Per-seat pricing that costs more than the hours it would take us to build a rough version ourselves (P004)
- Needing a dedicated person to keep it running or tune it (P004)
- Requires broad Slack, Drive and GitHub access to a hosted product in early preview with no SOC 2 report (P005)
- Noisy or uncited warnings that lead people to ignore it within a month (P005)
- A vendor that might vanish with no migration path, or self-hosting that turns into a part-time job for one of my SREs (P005)
- Needs engineers to tag owners, write decision records or fill in anything new to stay accurate (P006)
- Our code and Slack have to leave our infrastructure, or it can't run self-hosted (P006)
- Briefings that go stale or confidently wrong, because an agent trusting bad context is worse than no context (P006)
- A hosted service from a small vendor with no SOC 2 that reads our clients' Slack and GitHub (P007)
- Needs someone to run and patch a multi-service Docker stack every week (P007)
- Per-seat pricing that keeps climbing when we bring in contractors for a single client project (P007)
- A hosted vendor with no SOC 2 Type II report and no signed DPA (P008)
- Per-seat pricing that grows with headcount and that I have to defend to finance (P008)
- Broad read access to all of GitHub, Slack and Drive just to produce an audit log (P008)
- Org-wide read access to code, Slack, Drive and Notion all at once, or a multi-tenant hosted service that can't show a SOC 2 Type II (P009)
- Advisory-only output that agents or engineers can silently skip (P009)
- Noisy or uncited warnings that turn it into yet another place nobody checks (P009)
- Requires broad read access to private Slack or all repos through a hosted vendor without SOC 2 Type II and a signed DPA (P010)
- Adds noticeable latency or approval friction to routine low-risk agent changes, so engineers route around it (P010)
- Locks us to a single agent vendor instead of covering Claude Code, Cursor and our own scripts (P010)
- Needs broad read access to all our Slack and incident history in a hosted product with no SOC 2 or clear data-handling story (P011)
- Confidently wrong owners: if it routes reviews or pages to the wrong team a couple of times, people stop trusting it (P011)
- Turns into another portal that someone has to curate by hand, the way Backstage did for us (P011)
- Per-engineer seat pricing that I have to defend to finance as yet another line item (P012)
- Hosted-only or still-in-preview handling of our incident and Slack data with no SSO or data-retention terms (P012)
- Noisy briefings that pad the agent's context with generic or uncited stuff, so they make the agent worse or slower (P012)
- Requires broad read access to Slack, Drive and Teams just to work (P013)
- Per-seat pricing across all 300+ engineers when only a few dozen people and the agents actually touch it (P013)
- No SOC 2 report or no clear data residency story, or a vendor still in early preview (P013)
- Requires piping our Slack, GitHub and docs to a vendor cloud without SOC 2 Type II, a signed DPA and a completed security questionnaire (P014)
- Only advisory, so agents can skip it and nothing actually blocks a risky change (P014)
- Locked to a single agent vendor when we run several (Claude Code, Cursor, internal agents) (P014)
- Yet another standalone UI or knowledge store my team has to keep up to date by hand (P015)
- Sending our code or incident data to an early-stage vendor's cloud without SSO, an audit log and a clean security review (P015)
- Probabilistic 'risk scores' with no traceable reasoning, which my team would end up double-checking anyway (P015)
- Requires sending our incident history and repo access to an unproven third-party cloud with no self-host option (P016)
- Floods agents and reviewers with false-positive warnings so people learn to ignore it (P016)
- Needs a dedicated person to curate or tag knowledge by hand to stay useful (P016)
- An early-stage vendor hosting a single store of our code, Slack and Drive without a SOC 2 Type II report (P017)
- Noisy false positives that slow agents down until the team routes around it (P017)
- Being just another search box over our docs, which Glean and Notion search already cover (P017)
- Needs broad read access to all of Slack and Drive before it delivers any value (P018)
- Comes from a vendor with no SOC 2 Type II report, or is a self-hosted app my platform team has to babysit (P018)
- Yet another chat window my engineers have to remember to open, instead of being built into tools they already use (P018)
- Needs a person to write or curate the knowledge for it to be useful (P019)
- Sends our source code to an early-preview hosted service without a clear security story (P019)
- Per-seat pricing for every engineer when only a handful of people actually drive agents (P019)
- Per-seat pricing across the whole engineering org when only a handful of people and agents drive the value (P020)
- Confidently wrong ownership or blast-radius answers with no sources, so engineers learn to ignore it (P020)
- Sending Slack, Drive and incident data to an early-stage hosted service with no SOC 2 or clear retention and access controls (P020)

## Each buyer, in their words

| id | who | need | expected provider | MemoryWorks would need |
| --- | --- | --- | --- | --- |
| P001 | Platform or SRE lead at a larger company, Incident-scarred | Before anyone, human or agent, changes a service, something has to automatically put the relevant past postmortems, action items and known constraints for that service in front of them, and it has to block or escalate when the change touches something we already got burned on. Our postmortems can't keep rotting in a Drive folder nobody opens. | Honestly, most likely our incident-management or dev-platform vendor (whoever already holds our postmortems and on-call data) bolts it on, or the AI coding tool makers add a context hook for it; failing that, my team builds a rough internal version on top of our postmortem docs. A new startup only wins if it gets through security faster than those options. | A self-hosted deployment that respects our existing ACLs and logs every agent and human query, plus a pilot where one of my engineers points it at our postmortems and shows it would have flagged the change behind our last repeat outage. If they want the hosted version, I need a SOC 2 report and flat team pricing, not $20 per head across the whole org. |
| P002 | Platform or SRE lead at a larger company, Budget-constrained | A hard policy gate between AI agents and production: every agent gets its own scoped identity, risky changes (infra, auth, data migrations, anything touching a service with recent incidents) are blocked until a named human approves, and every agent action is logged with who authorised it. Not advice to the agent, enforcement on the path to merge and deploy. | Most likely GitHub and our CI/deploy tooling extending rulesets and agent identities, or the agent makers adding approval controls, with my own platform team gluing it together on top of what we already run. A new startup only gets in if it is open source and self-hosted, because I won't put another unknown vendor in the path of production. | MemoryWorks would have to turn the 'whether a person must approve' part of its briefing into actual enforcement in our GitHub and deploy pipeline, plus an auditor-ready log of agent actions, all running fully self-hosted in our VPC. Then I'd pilot the free MIT version with one platform team and pay a flat team or support price, not $20 a seat, only once a SOC 2 Type II report and a reference customer my size exist. |
| P003 | Engineering manager at a scale-up, Tool-fatigued | Before any AI agent merges or deploys a change to a production service, something has to check it against what we already know about that service: past incidents, who owns it, what depends on it. If the change is risky it has to stop and get a named human to sign off. I don't need another place to search docs. I need a gate in the pipeline that agents can't skip. | Most likely the platform we already pay for adds it: GitHub or the agent vendors (Anthropic, Cursor) ship policy hooks and pre-merge checks, and my platform team wires our incident history into them. A startup has a shot only if it's open source, self-hosted, and in place before the big players ship something good enough. | Lead with the pre-change agent briefing and approval gate as a self-hosted GitHub check and MCP hook, scoped to a single team's repos and incident history and not the whole org's Slack and Drive. Price it per team or per agent, not per engineer. Then show me in a one-month pilot that it caught at least one real repeat-incident change. |
| P004 | Small agency or consultancy lead, Build-it-ourselves | A hard wall between clients for our agents: each client engagement gets its own scoped context and permissions, so an agent working in client A's repo can never see client B's code, Slack or docs, plus a plain log of what the agent read and changed that I can hand to that client. | Probably my own team first, a vector DB and an MCP server per client wired up in a few weekends. Longer term I expect Anthropic or Cursor to ship workspace-level scoping and audit logs, which would make a separate tool redundant for a shop our size. | Make per-client isolation a first-class feature of the self-hosted MIT version, with one instance or namespace per client and an exportable log of what each agent was briefed on and did that I can give to the client. Then show it stopping an agent from repeating a real past incident on one of our repos. I'd pay a small flat support fee, not $20 a head. |
| P005 | Engineering manager at a scale-up, Incident-scarred | Before any agent or engineer changes a service, something has to check our postmortems and incident history for that code path and say, with a link to the postmortem, 'this broke before, here's why, a human signs off.' It has to block or flag the change automatically, not depend on someone remembering to search. | Probably our incident-management vendor or the AI coding tool makers bolt something like this on within two years, because they already have the incidents or the agent. Until then it's either a startup we self-host or a script my platform team hacks together around the postmortem docs. | A self-hosted pilot on our repos that flags the outage we repeated, cited from our own postmortem, before an agent touches that code, without a full-time babysitter. Then, before I'd pay for the cloud: a SOC 2 report or a named reference customer my size, and a flat team price instead of per-seat. |
| P006 | Startup CTO or founding engineer, Build-it-ourselves | Before any agent touches a service, it has to automatically get the context a senior engineer would carry in their head: who owns it, what broke there before and why, and what it must not change. All of it pulled from git history, PRs and incident threads we already produce, not from a wiki someone has to maintain. | Most likely my own team: a vector DB, a GitHub and Slack ingester and an MCP server is a few weeks of work, and it's a few weeks I control. The AI tool makers like Anthropic and Cursor will probably ship some per-repo memory too, but it won't cover our incidents and Slack. I'd only pay a startup for a self-hosted thing that saves us from maintaining the ingestion and staleness logic ourselves. | Show me, on our real repos and Slack in the free self-hosted version over two weeks, that it answers 'who owns this service?' correctly and flags an agent change that would have repeated a past incident, with nobody on my team doing any data entry. If it does, I'd pay a flat team price for support and upgrades on the self-hosted version, not $20 per engineer for an early-preview cloud. |
| P007 | Small agency or consultancy lead, Bottom-up trialer | A per-client record of exactly what our AI agents read, changed and were allowed to touch in each client's repos, kept separate per client, that I can export and hand to a client when they ask what an AI did to their code. | Most likely the AI tool makers or GitHub, built into the agents and repos we already pay for, because they already see every action and clients already trust them. A startup only wins if it's free to self-host and much better than what comes bundled. | The bit where agents report back what they did would have to become an exportable, per-client audit log that runs entirely in our own cloud with almost no upkeep, and the hosted version would need SOC 2 or a bring-your-own-cloud option. If it also briefed an agent on a past incident before it repeated during a two-week trial on one repo, I'd pay a flat team price. |
| P008 | Startup CTO or founding engineer, Security gatekeeper | A tamper-evident record of every change an AI agent makes to our production repos and infra: which agent, which human kicked it off or approved it, what it read, what it touched. I need to export it as evidence when an auditor or a customer asks. | Most likely GitHub or the agent vendors themselves (Anthropic, Cursor) shipping enterprise audit logs, because they already see the actions and already have the paperwork. If that doesn't come fast enough, one of my engineers builds a thin logging layer on top of what they expose. | Turn the approval and report-back loop into an exportable audit trail first, with the memory and search second, and sell it as a paid self-hosted tier with support at a flat team price so our data never leaves our infra. If it's the managed cloud, it needs a SOC 2 Type II report and a signed DPA before I'd let it near our GitHub. |
| P009 | Platform or SRE lead at a larger company, Incident-scarred | A pre-merge, pre-deploy gate for agent-authored changes. It checks the diff against our own postmortem action items and known failure modes for that service. If the change touches something that has burned us before, it blocks or routes to a named human approver and links the exact postmortem. It has to run inside our CI and change-management path, not as a chat window someone has to remember to open. | Most likely my own platform team stitching it together on top of GitHub rulesets, our incident tool and a policy engine, or a feature from GitHub or our incident-management vendor, because those already passed my security review. A new startup only wins if it ships a supported self-hosted enterprise tier and proves it catches our real repeats. | Start narrow: a supported, self-hosted enterprise tier that only needs GitHub plus our postmortem repository and works as a required CI check, not a chat-first memory layer that wants every connector on day one. Then a pilot on our own history where it flags the change that repeated our last outage and links the right postmortem, with a SOC 2 Type II before any hosted option is even on the table. |
| P010 | Startup CTO or founding engineer, Security gatekeeper | A policy gate in front of every AI agent change to production repos and infra: it knows which paths, services and actions are risky, blocks or requires a named human approval before the agent proceeds, and leaves a tamper-evident log of what the agent was told, what it did and who signed off. | Most likely the agent makers and GitHub, since Claude Code hooks, Cursor rules and GitHub rulesets already cover part of it; until they do it well across tools, my own platform engineer will glue it together, and a startup only wins if it's self-hostable and cross-agent. | Make the pre-change briefing an enforced gate (block or require approval, with an exportable audit log) rather than just context, running fully self-hosted with a supported paid tier, and show on our own repos that it catches risky agent changes. If I ever use the hosted version, SOC 2 Type II and a signed DPA are table stakes. |
| P011 | Engineering manager at a scale-up, AI-agent early adopter | A living map of who owns each service and what it depends on, built from what actually happens in GitHub, Slack and incidents rather than a hand-maintained catalog, that my engineers and our agents can both query and get a sourced answer from. When an agent's PR touches three services, it has to know whose review to request without me answering it in Slack. | Most likely my own platform team, glued onto our existing catalog and CODEOWNERS, or a feature inside a dev-portal or incident tool we already pay for. A new startup could win it only if it runs self-hosted and is clearly more accurate than what we could hack together in a quarter. | Lead with ownership and dependency answers instead of pre-change briefings, prove on a few weeks of self-hosted trial on our repos that the citations are right, and expose that map to our agents and review tooling. For the hosted version I'd also need SOC 2 or a written data-handling story, and flat team pricing instead of $20 per engineer across the whole org. |
| P012 | Startup CTO or founding engineer, Build-it-ourselves | Before an agent opens a PR on a production service, something automatically feeds it the decisions and past incidents that apply to that code, the stuff we keep re-explaining in architecture review that isn't in the repo or the CLAUDE.md, and blocks the merge if it touches something a human has to sign off on. | Most likely my own team builds a thin version first (vector DB, an MCP server, a postmortem ingester), and within a year or two the agent makers like Anthropic or Cursor ship org-level memory themselves. I'd only pay a startup if its open-source core is clearly better than my weekend build and they sell support or enterprise features on top. | Show me on our own self-hosted install, inside a month, that a briefing stopped an agent from repeating a real past incident. Then sell a flat per-org enterprise tier, with SSO, audit logs, a data-retention policy security will sign and support, for the self-hosted version instead of $20 a seat for a hosted preview. |
| P013 | Platform or SRE lead at a larger company, Budget-constrained | A gate that sits between any AI agent and our production repos and infra: before an agent merges or applies a change, it checks blast radius and service ownership against policy, forces a named human approval for anything risky, and logs who or what approved it. Briefings are nice; I need the gate. | Most likely the AI tool makers and GitHub building approval and policy controls into their enterprise tiers, because they own the agent and the PR path. If what they ship is too shallow, my platform team builds the rest on policy-as-code in CI. A startup has a chance only if it's open source and self-hostable. | Lead with the self-hosted pre-change gate (blast radius, owners, prior incidents, required human approval, audit log) running from GitHub and our incident tracker alone, with Slack/Drive/Teams optional. Then offer a flat platform-team price with a SOC 2 report for the paid support tier, and prove in a one-team pilot that it cuts repeat incidents without engineers doing anything new. |
| P014 | Engineering manager at a scale-up, Security gatekeeper | A gate in front of every AI agent change to production repos and infra: before it merges or deploys, the change is checked against what has broken there before and who owns it, risky blast radius forces a named human approver, and every agent action lands in an immutable audit log I can hand to security. | Most likely GitHub or the AI tool makers bolting approval rules and audit logs onto what we already run, because they already hold our code and passed our security review; failing that, my platform team builds a thinner version on branch protection, CODEOWNERS and CI checks. A new startup only gets in if it ships self-hosted with enterprise controls. | MemoryWorks would need a supported enterprise self-hosted tier (SSO, RBAC, audit logs, every byte in our VPC) and to turn its pre-change briefing into an enforced gate in the merge/CI path with a full audit trail, not just context the agent may read. If they want the managed cloud instead, show me SOC 2 Type II, a signed DPA and a completed security questionnaire first. |
| P015 | Engineering manager at a scale-up, Tool-fatigued | A hard gate between AI agents and production: before an agent's change merges or deploys, something checks it against ownership, the services it touches and our known-risky areas, forces a named human approval when it crosses a line, and logs who or what approved it. | Most likely GitHub or the agent makers (Anthropic, Cursor) building policy and approval hooks into their own tools, with my platform team writing the org-specific rules on top. A new startup only gets in if it plugs straight into those hooks and stays cheaper than what we'd build ourselves. | The pre-change briefing would have to become an enforcing GitHub/CI check that blocks or requires approval based on cited past incidents and ownership, running self-hosted on our repos. And it would have to prove in a two-week pilot that its blast-radius calls are right often enough that we stop double-checking them. The chat and search side doesn't matter to me unless it replaces Glean or Confluence search instead of adding a third place to look. |
| P016 | Startup CTO or founding engineer, AI-agent early adopter | Before any agent touches a service, it automatically gets the incidents, postmortem findings and 'never do X here' constraints for the exact files and services it is about to change, and it stops for a human when the change hits something that has burned us before. | Honestly, probably the agent makers themselves (Anthropic, Cursor) or GitHub will bolt on some version of repo memory, and if that's not good enough my platform engineer will hack a version together on our postmortems. A startup wins only if it's clearly better on incident recall and gets there before those two do; I don't expect Glean or Notion to do this well because they index docs, not code paths. | The agent-briefing piece is exactly what I described, but it has to prove itself on our own repo by catching a real repeat before it ships, with a low false-positive rate. After that I'd pay roughly $500-600 a month flat for a hosted version only if it's out of early preview with a clear security story (SOC 2 or equivalent, scoped read-only repo access), otherwise I stay on the free self-hosted version. |
| P017 | Startup CTO or founding engineer, AI-agent early adopter | A gate that sits between our coding agents and production: before an agent merges or deploys a change, it checks that change against our known incidents, protected paths and blast radius, and either blocks it or routes it to a named human for approval, with a log of what the agent was told and what it did. | Most likely the agent makers themselves (Anthropic, Cursor, GitHub) will ship hooks and policy controls, and my team will glue those to our incident history ourselves. A startup only wins if it does the incident-aware part clearly better and lets me self-host it. | Turn the agent briefing into an enforcement point: a CI and agent-hook check that actually blocks or requires approval based on cited past incidents, proven in a self-hosted pilot to stop an agent from repeating one of our own incidents. For me to pay for managed rather than run it myself, add a SOC 2 Type II report and a flat team price. |
| P018 | Engineering manager at a scale-up, Tool-fatigued | A service ownership and dependency map that stays current on its own and that machines can read: who owns each service, what it depends on, who must sign off. Our engineers and our coding agents both check it before touching a service, so I stop being the human lookup table in Slack. | Most likely an add-on from a vendor we already pay, like PagerDuty's service catalog, Atlassian/Compass or GitHub, or else my platform team wiring up Backstage. Those vendors already hold the data and are through security review, and AI tool makers will expose hooks for it, not own it. | Start as a narrow ownership-and-blast-radius service: read-only, one repo plus CODEOWNERS and on-call data, no Slack or Drive, running in our VPC, piloted in a week without security sign-off. For the paid version it would need SOC 2 Type II and a reference from an engineering leader I trust who saw ownership and incident questions actually drop. |
| P019 | Startup CTO or founding engineer, Bottom-up trialer | Every agent session automatically gets a short, current, per-repo context: the conventions, the don't-touch zones, who owns what, and the few past incidents that matter. It has to keep itself up to date from the code, PRs and incident tickets, because nobody on my team will maintain a doc for it. | Most likely the AI tool makers themselves (Anthropic, Cursor, GitHub Copilot), since project memory and rules files are already heading that way and it's the cheapest thing for them to bundle. A small startup only wins if it's clearly better at keeping the context fresh across repos and tools. | Lead with automatic, self-maintaining repo context delivered to Claude Code and Cursor over MCP, built from code, PRs and incident tickets rather than our dead postmortem folder. It also has to pass my two-week test: the team keeps using it on one repo without me pushing, then a flat team price around $300 a month instead of $20 per engineer. |
| P020 | Engineering manager at a scale-up, AI-agent early adopter | A gate that sits in front of every agent change: before Claude Code or Cursor touches a service, it knows who owns it, what broke there before and what depends on it, and risky changes get routed to the right human for approval automatically. Not a wiki, an enforced check in the agent's loop. | Probably a new startup or our own platform team stitching it together. The agent makers will add basic permission prompts, but they won't own our cross-tool org context, and GitHub or our incident tool will only see their own slice. | Make the agent briefing an enforceable gate with approval routing, not just context, and prove on a self-hosted pilot that its owner and blast-radius answers are right with citations. Then price it per repo or per agent with a security posture finance and security can sign off on, not $20 per engineer for the whole org. |
