"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, formatDate } from "@/lib/api";

/* The outcome loop, made visible.
 *
 * This surface exists because the ledger behind it is the only asset here a
 * competitor with a better model cannot copy: a per-company record of which
 * context actually produced correct action. It was being written from day one
 * and never shown, which meant nobody — not the team, not a customer — could
 * see the thing compounding.
 *
 * Two rules govern what it renders. It never invents a number: every figure
 * comes from `/api/outcomes/stats` or the export, and an empty ledger says it
 * is empty rather than showing a hopeful zero dressed as a metric. And it
 * treats "closed" as the number that matters, not "served" — a thousand
 * answers with no recorded outcome is a log, not a corpus.
 */

type Stats = {
  contexts: number;
  actions: number;
  outcomes: number;
  closed_rate: number;
  success_rate: number;
  acceptance_rate: number;
  trainable_examples: number;
  by_lens?: { selected_lens: string; served: number; judged: number; success_rate: number }[];
  by_scope?: { answer_scope: string; served: number; judged: number; success_rate: number }[];
  by_model?: { model_provider: string; served: number; judged: number; success_rate: number }[];
};

type Record_ = {
  context_event_id: string;
  project_id: string;
  surface: string;
  query: string;
  answer: string;
  answer_scope: string;
  answer_kind: string;
  evidence_count: number;
  actions: { action_type: string; target: string; surface: string; created_at: string }[];
  outcomes: { outcome: string; signal: string; reason: string; observed_at: string }[];
  label: string;
  reward: number;
  created_at: string;
};

type Skill = {
  id: string;
  name: string;
  trigger: string;
  steps?: string[];
  status: string;
  successes: number;
  failures: number;
  confidence: number;
  updated_at?: string;
};

const OUTCOME_TONE: Record<string, string> = {
  succeeded: "good",
  partial: "mixed",
  unknown: "quiet",
  abandoned: "mixed",
  failed: "bad",
};

export default function OutcomeLoop() {
  const [stats, setStats] = useState<Stats>();
  const [records, setRecords] = useState<Record_[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    Promise.all([
      api<Stats>("/api/outcomes/stats"),
      // Unlabelled rows are included on purpose: an open loop is the honest
      // picture, and hiding it would make the corpus look healthier than it is.
      api<{ records: Record_[] }>("/api/outcomes/export?labelled_only=false&limit=40"),
      api<{ skills: Skill[] }>("/api/skills/learned").catch(() => ({ skills: [] })),
    ])
      .then(([nextStats, nextExport, nextSkills]) => {
        if (!live) return;
        setStats(nextStats);
        setRecords(nextExport.records || []);
        setSkills(nextSkills.skills || []);
      })
      .catch((exc: any) => live && setError(exc.message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  const closed = records.filter((item) => item.outcomes.length > 0);
  const open = records.filter((item) => item.outcomes.length === 0);

  return (
    <div className="loop-page">
      <header className="loop-hero">
        <p className="loop-eyebrow">The compounding asset</p>
        <h1>Which context actually produced correct action here.</h1>
        <p className="loop-lede">
          Anyone can ingest the same Slack and the same repositories. What only this workspace
          holds is the record of what happened <em>after</em> the context was served — what a
          person or an agent did with it, and whether it worked. Every answer and every agent
          briefing opens a row here. Reporting the outcome closes it.
        </p>
        <div className="loop-flow" aria-label="The loop">
          <span>context served</span>
          <i aria-hidden="true">→</i>
          <span>action taken</span>
          <i aria-hidden="true">→</i>
          <span>outcome observed</span>
          <i aria-hidden="true">↺</i>
          <span>better context next time</span>
        </div>
      </header>

      {error && <p className="loop-error">{error}</p>}
      {loading && !stats && <p className="loop-quiet">Reading the ledger…</p>}

      {stats && (
        <>
          <section className="loop-metrics" aria-label="Ledger state">
            <Metric
              value={stats.contexts}
              label="contexts served"
              note="Answers and briefings that opened a row."
            />
            <Metric
              value={`${Math.round((stats.closed_rate || 0) * 100)}%`}
              label="loop closed"
              note="The figure that decides whether this is a corpus or a log."
              emphasis
            />
            <Metric
              value={stats.outcomes ? `${Math.round((stats.success_rate || 0) * 100)}%` : "—"}
              label="succeeded when closed"
              note={stats.outcomes ? `Across ${stats.outcomes} recorded outcomes.` : "No outcomes recorded yet."}
            />
            <Metric
              value={stats.trainable_examples}
              label="trainable examples"
              note="Labelled rows a reranker or judge could learn from."
            />
          </section>

          {!stats.contexts && (
            <p className="loop-quiet">
              Nothing has been served yet. Ask a question in the <Link href="/workspace">chat</Link>,
              or let an agent call <code>get_orgmemory_briefing</code>, and the first row appears here.
            </p>
          )}
        </>
      )}

      {(closed.length > 0 || open.length > 0) && (
        <section className="loop-ledger" aria-label="Recent loop entries">
          <header className="loop-section-head">
            <div>
              <p className="loop-eyebrow">Ledger</p>
              <h2>Recent entries</h2>
            </div>
            <span>
              {closed.length} closed · {open.length} still open
            </span>
          </header>
          <ol className="loop-entries">
            {records.map((record) => {
              const outcome = record.outcomes.at(-1);
              const action = record.actions.at(-1);
              const isBriefing = record.answer_scope === "briefing";
              return (
                <li
                  key={record.context_event_id}
                  className={`loop-entry ${outcome ? OUTCOME_TONE[outcome.outcome] || "quiet" : "pending"}`}
                >
                  <div className="loop-entry-head">
                    <span className="loop-kind">{isBriefing ? "briefing" : record.answer_scope || "answer"}</span>
                    <strong>{record.query.replace(/^\[briefing\]\s*/, "")}</strong>
                    <small>{formatDate(record.created_at)}</small>
                  </div>
                  <p className="loop-entry-answer">{record.answer}</p>
                  <div className="loop-entry-legs">
                    <span className="loop-leg served">
                      <em>served</em>
                      {record.surface || "web"}
                      {record.evidence_count > 0 && ` · ${record.evidence_count} evidence`}
                    </span>
                    <i aria-hidden="true">→</i>
                    <span className={`loop-leg ${action ? "done" : "waiting"}`}>
                      <em>action</em>
                      {action ? action.action_type.replace(/_/g, " ") : "not reported"}
                      {action?.target && ` · ${action.target}`}
                    </span>
                    <i aria-hidden="true">→</i>
                    <span className={`loop-leg ${outcome ? "done" : "waiting"}`}>
                      <em>outcome</em>
                      {outcome ? outcome.outcome : "still open"}
                      {outcome && ` · reward ${record.reward > 0 ? "+" : ""}${record.reward}`}
                    </span>
                  </div>
                  {outcome?.reason && <p className="loop-entry-reason">“{outcome.reason}”</p>}
                </li>
              );
            })}
          </ol>
        </section>
      )}

      <section className="loop-skills" aria-label="Learned skills">
        <header className="loop-section-head">
          <div>
            <p className="loop-eyebrow">Distilled precedent</p>
            <h2>What the loop has taught this workspace</h2>
          </div>
          <span>{skills.length ? `${skills.length} skills` : "none yet"}</span>
        </header>
        {skills.length ? (
          <ul className="loop-skill-list">
            {skills.map((skill) => (
              <li key={skill.id} className={skill.status}>
                <div>
                  <strong>{skill.name}</strong>
                  <small>{skill.trigger}</small>
                </div>
                <span className="loop-skill-trust">
                  {skill.successes}✓ {skill.failures}✗ · {Math.round((skill.confidence || 0) * 100)}%
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="loop-quiet">
            Skills are distilled only from runs that verifiably worked, so this stays empty until
            outcomes are recorded. That is the intended order — precedent has to be earned before
            it is offered back as advice.
          </p>
        )}
      </section>

      <footer className="loop-foot">
        <p>
          Agents close this loop themselves: <code>get_orgmemory_briefing</code> opens a row,{" "}
          <code>record_orgmemory_outcome</code> closes it. Neither one writes company memory —
          that still requires a person on the{" "}
          <Link href="/approvals">approvals</Link> queue.
        </p>
        <Link className="loop-link" href="/integrations">
          Connect your AI tools <span aria-hidden="true">→</span>
        </Link>
      </footer>
    </div>
  );
}

function Metric({
  value,
  label,
  note,
  emphasis,
}: {
  value: number | string;
  label: string;
  note: string;
  emphasis?: boolean;
}) {
  return (
    <article className={`loop-metric ${emphasis ? "emphasis" : ""}`}>
      <strong>{value}</strong>
      <span>{label}</span>
      <small>{note}</small>
    </article>
  );
}
