"use client";

import { BrandMark } from "@/components/BrandLogo";
import { useEffect, useRef, useState } from "react";

type Verdict = "proceed" | "proceed_with_context" | "requires_approval";

type Scenario = {
  tab: string;
  task: string;
  service: string;
  verdict: Verdict;
  title: string;
  rows: [string, string][];
  sources: string[];
};

/* Illustrative briefings across ordinary engineering changes. The verdicts are
   the ones get_orgmemory_briefing actually returns. */
const SCENARIOS: Scenario[] = [
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
  },
];

const VERDICT_LABEL: Record<Verdict, string> = {
  proceed: "Proceed",
  requires_approval: "Requires approval",
  proceed_with_context: "Proceed with context",
};

const CYCLE_MS = 7000;

export default function BriefingDemo() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [index, setIndex] = useState(0);
  const [typed, setTyped] = useState(SCENARIOS[0].task.length);
  const [paused, setPaused] = useState(false);
  const [visible, setVisible] = useState(false);
  const scenario = SCENARIOS[index];

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.35 });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // type the agent's command each time the scenario changes
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setTyped(SCENARIOS[index].task.length);
      return;
    }
    setTyped(0);
    const full = SCENARIOS[index].task.length;
    const timer = window.setInterval(() => {
      setTyped((n) => {
        if (n >= full) {
          window.clearInterval(timer);
          return n;
        }
        return n + 1;
      });
    }, 26);
    return () => window.clearInterval(timer);
  }, [index]);

  // advance on a timer while on screen and not being read
  useEffect(() => {
    if (!visible || paused) return;
    const timer = window.setTimeout(() => setIndex((i) => (i + 1) % SCENARIOS.length), CYCLE_MS);
    return () => window.clearTimeout(timer);
  }, [visible, paused, index]);

  const done = typed >= scenario.task.length;

  return (
    <div
      ref={rootRef}
      className="mw-demo"
      onPointerEnter={() => setPaused(true)}
      onPointerLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="mw-demo-tabs" role="tablist" aria-label="Example changes">
        {SCENARIOS.map((s, i) => (
          <button
            key={s.tab}
            type="button"
            role="tab"
            aria-selected={i === index}
            className={i === index ? "is-active" : ""}
            onClick={() => setIndex(i)}
          >
            {s.tab}
            {i === index && !paused && visible && <i style={{ animationDuration: `${CYCLE_MS}ms` }} aria-hidden="true" />}
          </button>
        ))}
      </div>

      <div className="mw-demo-term" aria-label={`Agent: ${scenario.task}`}>
        <span className="mw-demo-dots" aria-hidden="true"><i /><i /><i /></span>
        <code>
          <b>agent ›</b> {scenario.task.slice(0, typed)}
          <span className={`mw-caret ${done ? "is-blink" : ""}`} aria-hidden="true" />
        </code>
        <code className={`mw-demo-call ${done ? "is-in" : ""}`}>
          <em>→</em> get_orgmemory_briefing(task=<q>{scenario.task}</q>, service=<q>{scenario.service}</q>)
        </code>
      </div>

      <article key={index} className={`mw-brief v-${scenario.verdict} ${done ? "is-in" : ""}`} aria-label="Example briefing" role="tabpanel">
        <div className="mw-brief-head">
          <span><BrandMark />MemoryWorks briefing</span>
          <b>{VERDICT_LABEL[scenario.verdict]}</b>
        </div>
        <h3>{scenario.title}</h3>
        <dl>
          {scenario.rows.map(([term, value], i) => (
            <div key={term} style={{ transitionDelay: `${120 + i * 90}ms` }}>
              <dt>{term}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <ul className="mw-brief-sources" aria-label="Sources">
          {scenario.sources.map((source) => <li key={source}>{source}</li>)}
        </ul>
      </article>
    </div>
  );
}
