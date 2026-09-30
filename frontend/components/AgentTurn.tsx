"use client";

import { useState } from "react";
import MarkdownAnswer from "@/components/MarkdownAnswer";
import type { OrgAgentSession, OrgPlan } from "@/lib/orgTools";

/* What Agent mode renders inside the chat: the steps the agent took, its
 * answer, the memories it cited, and — when it wants to change something —
 * the plan a person approves or declines right there.
 *
 * Every step is a real call against this workspace. The steps start folded
 * away, the way an editor shows "worked for 12s": the answer is what people
 * came for, and the trail is one click away when they want to check it. */

export type AgentStep = {
  tool: string;
  args: Record<string, unknown>;
  state: "running" | "done" | "error";
  summary: string;
  thought?: string;
  ms: number;
  gated: boolean;
};

export type AgentState = {
  steps: AgentStep[];
  session?: OrgAgentSession;
  plan?: OrgPlan | null;
  done: boolean;
};

export function stepsFrom(session: OrgAgentSession): AgentStep[] {
  const running = session.status === "running";
  return (session.steps || []).map((step, index) => ({
    tool: step.tool,
    args: step.arguments || {},
    state: running && index === session.steps.length - 1 ? "running" : "done",
    summary: step.summary,
    thought: step.thought,
    ms: step.duration_ms || 0,
    gated: step.tool.startsWith("propose_") || step.tool.startsWith("create_"),
  }));
}

/* "find_orgmemory_blockers" reads as "Found blockers" — the call, in words. */
const VERBS: Record<string, [string, string]> = {
  get: ["Reading", "Read"],
  find: ["Finding", "Found"],
  list: ["Listing", "Listed"],
  search: ["Searching", "Searched"],
  propose: ["Drafting", "Drafted"],
  create: ["Drafting", "Drafted"],
  ask: ["Asking", "Asked"],
  inspect: ["Inspecting", "Inspected"],
  resolve: ["Resolving", "Resolved"],
  record: ["Recording", "Recorded"],
};

export function describeStep(tool: string, running: boolean) {
  const [verb, ...rest] = tool.replace(/_?orgmemory_?/g, "_").split("_").filter(Boolean);
  const words = rest.join(" ") || "memory";
  const forms = VERBS[verb];
  if (!forms) return `${verb} ${words}`;
  return `${running ? forms[0] : forms[1]} ${words}`;
}

/* The agent cites memories inline by id so its answer stays checkable. The
   numbered chips under the answer carry those same citations, so the raw ids
   are dropped from the prose a person reads. */
export function withoutMemoryIds(text: string) {
  return text
    .replace(/\s*\(\s*mem_[0-9a-z]{6,}\s*\)/gi, "")
    .replace(/,\s*mem_[0-9a-z]{6,}/gi, "")
    .replace(/\bmem_[0-9a-z]{6,}\b/gi, "")
    .replace(/[ \t]{2,}/g, " ");
}

export function AgentAnswer({
  state,
  onEvidence,
  onApprove,
  onDecline,
}: {
  state: AgentState;
  onEvidence: (memoryId: string) => void;
  onApprove: (plan: OrgPlan) => void;
  onDecline: (plan: OrgPlan) => void;
}) {
  const [open, setOpen] = useState(false);
  const session = state.session;
  const running = !state.done;
  const totalMs = state.steps.reduce((sum, step) => sum + step.ms, 0);
  const current = state.steps.at(-1);

  return (
    <div className="ws-answer agent">
      {state.steps.length > 0 && (
        <div className={`ws-steps ${open ? "open" : ""}`}>
          <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
            {running ? (
              <>
                <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>
                {current ? describeStep(current.tool, true) : "Working"}…
              </>
            ) : (
              <>
                Worked through {state.steps.length} step{state.steps.length === 1 ? "" : "s"}
                {totalMs > 0 && <small>{(totalMs / 1000).toFixed(1)}s</small>}
              </>
            )}
            <em aria-hidden="true">{open ? "▴" : "▾"}</em>
          </button>
          {open && (
            <ol>
              {state.steps.map((step, index) => (
                <li key={`${step.tool}-${index}`} className={step.state}>
                  <strong>
                    {describeStep(step.tool, step.state === "running")}
                    {step.gated && <b>needs approval</b>}
                  </strong>
                  {step.thought && <p className="ws-step-thought">{step.thought}</p>}
                  {step.summary && <p>{step.summary}</p>}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {running && !state.steps.length && (
        <div className="ws-working" role="status" aria-live="polite">
          <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>
          <p>Planning the first step…</p>
        </div>
      )}

      {session?.answer && (
        <div className="ws-reply">
          <MarkdownAnswer>{withoutMemoryIds(session.answer)}</MarkdownAnswer>
        </div>
      )}

      {session?.error && !session.answer && <div className="ws-alert">{session.error}</div>}

      {(session?.memory_ids?.length ?? 0) > 0 && (
        <div className="ws-cites" aria-label="Cited memories">
          <span>Cited</span>
          {session!.memory_ids.map((id, index) => (
            <button key={id} type="button" onClick={() => onEvidence(id)} title="Open the evidence behind this">
              {index + 1}
            </button>
          ))}
        </div>
      )}

      <PlanCard
        plan={state.plan || (session?.proposal as OrgPlan | undefined) || null}
        onApprove={onApprove}
        onDecline={onDecline}
      />
    </div>
  );
}

/* A proposed change never applies itself. It stops here, in the conversation
   that produced it, until a person decides. */
function PlanCard({
  plan,
  onApprove,
  onDecline,
}: {
  plan: OrgPlan | null;
  onApprove: (plan: OrgPlan) => void;
  onDecline: (plan: OrgPlan) => void;
}) {
  const [busy, setBusy] = useState(false);
  if (!plan || !plan.operations?.length) return null;
  if (plan.status === "denied") {
    return <p className="ws-scope">Declined. Nothing was changed.</p>;
  }
  const waiting = plan.status === "pending_approval";
  return (
    <section className={`ws-plan ${plan.status}`}>
      <header>
        <strong>{waiting ? "Proposed changes" : "Changes applied"}</strong>
        <span>{waiting ? "Nothing changes until you approve" : "Approved by a person in this workspace"}</span>
      </header>
      <ul>
        {plan.operations.map((operation, index) => (
          <li key={index}>
            <span>{String(operation.preview || operation.op)}</span>
            {operation.reason ? <small>{String(operation.reason)}</small> : null}
          </li>
        ))}
      </ul>
      {waiting && (
        <div className="ws-plan-actions">
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await onApprove(plan);
              setBusy(false);
            }}
          >
            Approve
          </button>
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await onDecline(plan);
              setBusy(false);
            }}
          >
            Decline
          </button>
        </div>
      )}
    </section>
  );
}

export function EvidencePanel({ evidence, onClose }: { evidence: any; onClose: () => void }) {
  return (
    <div className="ws-evidence" role="dialog" aria-label="Evidence" onClick={onClose}>
      <div className="ws-evidence-card" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="ws-evidence-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        {evidence.loading ? (
          <p className="ws-muted">Loading…</p>
        ) : evidence.error ? (
          <p className="ws-muted">{evidence.error}</p>
        ) : (
          <>
            <small>{evidence.memory?.type}</small>
            <h3>{evidence.memory?.title}</h3>
            <p>{evidence.memory?.content}</p>
            <dl>
              <div>
                <dt>Space</dt>
                <dd>{evidence.memory?.space_name}</dd>
              </div>
              <div>
                <dt>Confidence</dt>
                <dd>{Math.round((evidence.memory?.confidence || 0) * 100)}%</dd>
              </div>
            </dl>
            {evidence.sources?.length > 0 && (
              <>
                <small>Sources</small>
                <ul>
                  {evidence.sources.map((source: any) => (
                    <li key={source.id}>
                      {source.url ? (
                        <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
                      ) : (
                        <span>{source.title}</span>
                      )}
                      <em>{source.type}</em>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
