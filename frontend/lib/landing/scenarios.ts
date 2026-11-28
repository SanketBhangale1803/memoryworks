/* Illustrative briefings across ordinary engineering changes, shared by the
   hero's memory timeline and the briefing demo so both tell the same stories.
   The verdicts are the ones get_orgmemory_briefing actually returns. No
   three.js here: the demo renders with the page. */

export type Verdict = "proceed" | "proceed_with_context" | "requires_approval";

export type MemoryKind = "incident" | "decision" | "constraint" | "pr" | "thread" | "doc" | "owner" | "outcome";

export type Memory = {
  kind: MemoryKind;
  title: string;
  /** Months before now (October 2026): where the memory sits in time. */
  monthsAgo: number;
  source: string;
};

export type Scenario = {
  tab: string;
  task: string;
  service: string;
  verdict: Verdict;
  title: string;
  rows: [string, string][];
  sources: string[];
  /** The memories a briefing for this task pulls forward. */
  memories: Memory[];
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  proceed: "Proceed",
  requires_approval: "Requires approval",
  proceed_with_context: "Proceed with context",
};

export const SCENARIOS: Scenario[] = [
  {
    tab: "Key rotation",
    task: "rotate the token signing keys",
    service: "auth-service",
    verdict: "requires_approval",
    title: "Rotating keys signs out clients that still cache the old one.",
    rows: [
      ["Prior incident", "Last rotation signed out every mobile session for 40 minutes"],
      ["Constraint", "Publish the new key 24 hours before signing with it"],
      ["Owner", "Identity team — on-call approves rotations"],
    ],
    sources: ["Postmortem · token rotation", "GitHub · PR #412", "Slack · #identity"],
    memories: [
      { kind: "incident", title: "Key rotation signed out mobile sessions for 40 min", monthsAgo: 7, source: "Postmortem" },
      { kind: "constraint", title: "Publish the new key 24 h before signing with it", monthsAgo: 6, source: "Runbook" },
      { kind: "owner", title: "Identity on-call approves key rotations", monthsAgo: 2, source: "Slack #identity" },
    ],
  },
  {
    tab: "Cluster upgrade",
    task: "upgrade the search cluster to the next major version",
    service: "search-cluster",
    verdict: "requires_approval",
    title: "A recorded decision holds this upgrade until the analyzer migration ships.",
    rows: [
      ["Decision", "ADR-014: stay on the current major until analyzers are migrated"],
      ["Status", "Analyzer migration still open — 2 pull requests in review"],
      ["Blast radius", "Search, autocomplete, and the nightly reindex job"],
    ],
    sources: ["Notion · ADR-014", "GitHub · 2 open PRs", "Drive · upgrade plan"],
    memories: [
      { kind: "decision", title: "ADR-014: hold the major upgrade until analyzers migrate", monthsAgo: 5, source: "Notion" },
      { kind: "doc", title: "Upgrade plan: search cluster", monthsAgo: 3, source: "Google Drive" },
      { kind: "pr", title: "Analyzer migration — 2 pull requests still open", monthsAgo: 1, source: "GitHub" },
    ],
  },
  {
    tab: "Concurrency",
    task: "raise worker concurrency on the ingest queue",
    service: "ingest-workers",
    verdict: "requires_approval",
    title: "More workers has exhausted the shared database before.",
    rows: [
      ["Prior incident", "Connection pool exhausted at 48 workers"],
      ["Constraint", "Workers × pool size must stay under the database limit"],
      ["Blast radius", "Ingest, notifications, and reporting share the cluster"],
    ],
    sources: ["Incident review · pool exhaustion", "Slack · #platform", "GitHub · config"],
    memories: [
      { kind: "incident", title: "Connection pool exhausted at 48 workers", monthsAgo: 8, source: "Incident review" },
      { kind: "constraint", title: "Workers × pool size stays under the DB limit", monthsAgo: 8, source: "Runbook" },
      { kind: "doc", title: "Ingest, notifications, reporting share one cluster", monthsAgo: 4, source: "Architecture doc" },
    ],
  },
  {
    tab: "Webhook retry",
    task: "add retries to the outbound email webhook",
    service: "notifications",
    verdict: "proceed_with_context",
    title: "This matches a change that worked — with one thing to keep.",
    rows: [
      ["Precedent", "Same retry policy shipped for SMS; recorded outcome: succeeded"],
      ["Keep", "Make delivery idempotent — the provider redelivers on timeout"],
      ["Owner", "Messaging team"],
    ],
    sources: ["GitHub · PR #289", "Outcome ledger · SMS retries", "Docs · provider API"],
    memories: [
      { kind: "outcome", title: "SMS retry policy shipped — outcome: succeeded", monthsAgo: 2, source: "Outcome ledger" },
      { kind: "doc", title: "Provider redelivers on timeout: make it idempotent", monthsAgo: 4, source: "Provider docs" },
      { kind: "owner", title: "Messaging team owns notifications", monthsAgo: 3, source: "CODEOWNERS" },
    ],
  },
];

/* Everything else the company remembers: the background of the timeline. */
export const BACKGROUND_MEMORIES: Memory[] = [
  { kind: "pr", title: "Add idempotency keys to the webhook sender", monthsAgo: 1, source: "GitHub" },
  { kind: "thread", title: "Should search reindex hourly or nightly?", monthsAgo: 1, source: "Slack #search" },
  { kind: "doc", title: "Q4 capacity plan", monthsAgo: 0, source: "Google Drive" },
  { kind: "owner", title: "search-api moved to Search platform", monthsAgo: 2, source: "Slack #platform" },
  { kind: "pr", title: "Rotate staging credentials monthly", monthsAgo: 3, source: "GitHub" },
  { kind: "incident", title: "Notifications delayed 25 minutes", monthsAgo: 5, source: "Postmortem" },
  { kind: "decision", title: "ADR-011: one queue per tenant tier", monthsAgo: 6, source: "Notion" },
  { kind: "thread", title: "Who owns billing-events now?", monthsAgo: 7, source: "Slack #platform" },
  { kind: "pr", title: "Bump the connection pool to 40", monthsAgo: 9, source: "GitHub" },
  { kind: "doc", title: "Runbook: rotating database credentials", monthsAgo: 9, source: "Google Drive" },
  { kind: "decision", title: "Move session tokens to RS256", monthsAgo: 10, source: "Notion" },
  { kind: "incident", title: "Search latency doubled after reindex", monthsAgo: 11, source: "Postmortem" },
  { kind: "thread", title: "Freeze deploys during the November sale", monthsAgo: 11, source: "Slack #eng" },
  { kind: "pr", title: "Split ingest workers from the API", monthsAgo: 4, source: "GitHub" },
  { kind: "doc", title: "On-call handbook", monthsAgo: 12, source: "Notion" },
  { kind: "outcome", title: "Cache warm-up on deploy — outcome: partial", monthsAgo: 6, source: "Outcome ledger" },
  { kind: "thread", title: "Retire the v1 search endpoint?", monthsAgo: 3, source: "Slack #api" },
  { kind: "pr", title: "Alert when the queue backs up past 5 min", monthsAgo: 7, source: "GitHub" },
  { kind: "decision", title: "Postgres for app state, graph for memory", monthsAgo: 12, source: "Notion" },
  { kind: "doc", title: "Incident review template", monthsAgo: 10, source: "Google Drive" },
];

export const KIND_LABEL: Record<MemoryKind, string> = {
  incident: "Incident",
  decision: "Decision",
  constraint: "Constraint",
  pr: "Pull request",
  thread: "Thread",
  doc: "Document",
  owner: "Owner",
  outcome: "Outcome",
};

/* The site's own palette, one hue per kind of memory. */
export const KIND_COLOR: Record<MemoryKind, string> = {
  incident: "#fc786d",
  decision: "#a168fa",
  constraint: "#f485ad",
  pr: "#50a8fc",
  thread: "#eab8fa",
  doc: "#fecb91",
  owner: "#eab8fa",
  outcome: "#50a8fc",
};
