"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatDate } from "@/lib/api";
import { orgApi, type OrgPlan } from "@/lib/orgTools";
import type { MemoryWorksProposal, MemoryWorksRefreshRequest } from "@/lib/webmcp";

/* One inbox for everything that waits on a person.
 *
 * Agents and teammates can propose — a new memory, a change across spaces, a
 * repository refresh, a write to a connected tool — but nothing applies until
 * someone here decides. Only the kinds that have something in them are shown,
 * so an empty inbox reads as one calm line instead of four empty boxes. */

type Principal = { id: string; role: string };

const REFRESH_STATES: Record<string, { label: string; tone: "info" | "success" | "danger" | "warning" }> = {
  queued: { label: "queued", tone: "info" },
  running: { label: "refreshing", tone: "info" },
  succeeded: { label: "completed", tone: "success" },
  failed: { label: "failed", tone: "danger" },
  denied: { label: "denied", tone: "warning" },
  pending_approval: { label: "waiting", tone: "warning" },
};

export default function Approvals() {
  const [principal, setPrincipal] = useState<Principal>();
  const [requests, setRequests] = useState<MemoryWorksRefreshRequest[]>([]);
  const [proposals, setProposals] = useState<MemoryWorksProposal[]>([]);
  const [plans, setPlans] = useState<OrgPlan[]>([]);
  const [connectorCalls, setConnectorCalls] = useState<any[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    return Promise.all([
      api<MemoryWorksRefreshRequest[]>("/api/repository-refresh-requests").then(setRequests).catch(() => undefined),
      api<MemoryWorksProposal[]>("/api/memory/proposals").then(setProposals).catch(() => undefined),
      orgApi.plans("pending_approval").then((result) => setPlans(result.plans || [])).catch(() => undefined),
      api<any[]>("/api/connector-tool-calls").then(setConnectorCalls).catch(() => undefined),
    ]).finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    api<Principal>("/api/auth/me").then(setPrincipal).catch(() => undefined);
    load();
    // Stays live without a refresh: something an agent proposes, or another
    // admin decides, shows up here within seconds.
    const timer = window.setInterval(load, 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  const isAdmin = principal?.role === "owner" || principal?.role === "admin";
  const waitingProposals = useMemo(() => proposals.filter((item) => item.status === "pending_approval"), [proposals]);
  const waitingCalls = useMemo(() => connectorCalls.filter((item) => item.status === "pending_approval"), [connectorCalls]);
  const recentRequests = useMemo(
    () => requests.filter((item) => item.status === "pending_approval" || item.status === "running" || item.status === "queued"),
    [requests],
  );
  const waiting =
    waitingProposals.length +
    plans.length +
    requests.filter((item) => item.status === "pending_approval").length +
    waitingCalls.length;

  async function decide(id: string, work: () => Promise<string>) {
    setBusyId(id);
    setNote("");
    setError("");
    try {
      setNote(await work());
      await load();
    } catch (exc: any) {
      setError(exc.message);
    } finally {
      setBusyId("");
    }
  }

  const resolveProposal = (proposal: MemoryWorksProposal, approved: boolean) =>
    decide(proposal.id, async () => {
      await api(`/api/memory/proposals/${encodeURIComponent(proposal.id)}/resolve`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      return approved
        ? `“${proposal.subject}” is now part of company memory.`
        : `“${proposal.subject}” was declined. Nothing was saved.`;
    });

  const resolvePlan = (plan: OrgPlan, approved: boolean) =>
    decide(plan.id, async () => {
      if (approved) await orgApi.approvePlan(plan.id);
      else await orgApi.rejectPlan(plan.id);
      return approved ? "Changes applied." : "Changes declined. Nothing was changed.";
    });

  const resolveRefresh = (request: MemoryWorksRefreshRequest, approved: boolean) =>
    decide(request.id, async () => {
      const result = await api<MemoryWorksRefreshRequest>(
        `/api/repository-refresh-requests/${encodeURIComponent(request.id)}/resolve`,
        { method: "POST", body: JSON.stringify({ approved }) },
      );
      return approved
        ? `Refreshing ${result.repository}. Memory updates when it finishes.`
        : `Declined ${request.requested_by_name ? `${request.requested_by_name}'s request` : "the request"}.`;
    });

  const resolveConnector = (call: any, approved: boolean) =>
    decide(call.id, async () => {
      const result: any = await api(`/api/connector-tool-calls/${call.id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      return approved ? `Approved — the ${call.provider} call ${result.status}.` : "Declined.";
    });

  const actions = (id: string, onApprove: () => void, onDecline: () => void, approveLabel = "Approve") =>
    isAdmin ? (
      <div className="ap-row-actions">
        <button className="button" disabled={busyId === id} onClick={onApprove}>
          {approveLabel}
        </button>
        <button className="button secondary" disabled={busyId === id} onClick={onDecline}>
          Decline
        </button>
      </div>
    ) : (
      <span className="badge">With an admin</span>
    );

  return (
    <div className="ap-wrap">
      <header className="ap-head">
        <div>
          <h1>{waiting ? `${waiting} waiting on you` : "Approvals"}</h1>
          <p>Agents and teammates can propose. Nothing changes until a person here decides.</p>
        </div>
      </header>

      {note && <div className="notice">{note}</div>}
      {error && <div className="notice error">{error}</div>}

      {loaded && !waiting && !recentRequests.length && (
        <div className="ap-empty ap-all-clear">
          <strong>Nothing is waiting on you.</strong>
          <span>
            When the agent proposes a change or someone asks for a refresh, it appears here — and
            in <Link href="/workspace">the chat</Link> where it came from.
          </span>
        </div>
      )}

      {waitingProposals.length > 0 && (
        <section className="ap-card">
          <div className="ap-card-head">
            <h2>New memories</h2>
            <span className="subtle">Knowledge someone wants the company to remember</span>
          </div>
          {waitingProposals.map((proposal) => (
            <article className="ap-row" key={proposal.id}>
              <div className="ap-row-main">
                <div className="ap-row-body">
                  <strong>{proposal.subject}</strong>
                  <p>&ldquo;{proposal.content}&rdquo;</p>
                  <small className="subtle">
                    {proposal.kind} · proposed by {proposal.requested_by_name || "an agent"} in{" "}
                    {proposal.project_name || proposal.project_id}
                    {proposal.reason ? ` · ${proposal.reason}` : ""}
                  </small>
                </div>
              </div>
              {actions(proposal.id, () => void resolveProposal(proposal, true), () => void resolveProposal(proposal, false))}
            </article>
          ))}
        </section>
      )}

      {plans.length > 0 && (
        <section className="ap-card">
          <div className="ap-card-head">
            <h2>Agent changes</h2>
            <span className="subtle">Changes the agent drafted while working</span>
          </div>
          {plans.map((plan) => (
            <article className="ap-row" key={plan.id}>
              <div className="ap-row-main">
                <div className="ap-row-body">
                  <strong>{plan.summary}</strong>
                  <ul className="ap-ops">
                    {plan.operations.map((operation, index) => (
                      <li key={index}>{String(operation.preview || operation.op)}</li>
                    ))}
                  </ul>
                  {plan.created_at && <small className="subtle">Drafted {formatDate(plan.created_at)}</small>}
                </div>
              </div>
              {actions(plan.id, () => void resolvePlan(plan, true), () => void resolvePlan(plan, false), "Apply changes")}
            </article>
          ))}
        </section>
      )}

      {recentRequests.length > 0 && (
        <section className="ap-card">
          <div className="ap-card-head">
            <h2>Repository refreshes</h2>
            <span className="subtle">Re-reading a repository so memory matches the code</span>
          </div>
          {recentRequests.map((request) => {
            const state = REFRESH_STATES[request.status] || REFRESH_STATES.pending_approval;
            // Refreshes re-read a whole repository, so only an owner or admin decides.
            const canResolve = request.status === "pending_approval" && isAdmin;
            return (
              <article className="ap-row" key={request.id}>
                <div className="ap-row-main">
                  <div className="ap-row-body">
                    <strong>{request.project_name || request.repository}</strong>
                    <p>
                      {request.requested_by_name || "A teammate"}
                      {request.reason ? ` · “${request.reason}”` : ""} · {formatDate(request.requested_at)}
                    </p>
                    {request.error && <div className="notice error">{request.error}</div>}
                  </div>
                  <span className={`badge ${state.tone}`}>{state.label}</span>
                </div>
                {canResolve && (
                  <div className="ap-row-actions">
                    <button className="button" disabled={busyId === request.id} onClick={() => void resolveRefresh(request, true)}>
                      Approve &amp; refresh
                    </button>
                    <button className="button secondary" disabled={busyId === request.id} onClick={() => void resolveRefresh(request, false)}>
                      Decline
                    </button>
                  </div>
                )}
              </article>
            );
          })}
        </section>
      )}

      {waitingCalls.length > 0 && (
        <section className="ap-card">
          <div className="ap-card-head">
            <h2>Writes to connected tools</h2>
            <span className="subtle">Values are hidden until approved</span>
          </div>
          {waitingCalls.map((call) => (
            <article className="ap-row" key={call.id}>
              <div className="ap-row-main">
                <div className="ap-row-body">
                  <strong>
                    {call.provider} · {String(call.tool_name).replace(/_/g, " ")}
                  </strong>
                  <p>
                    Fields: {(call.arguments?.declared_keys || []).join(", ") || "none"} · requested{" "}
                    {formatDate(call.requested_at)}
                  </p>
                </div>
                <span className={`badge ${["high", "critical"].includes(call.risk_level) ? "danger" : "warning"}`}>
                  {call.risk_level} risk
                </span>
              </div>
              {/* The person who asked for a write can approve their own; otherwise an admin decides. */}
              {isAdmin || call.user_id === principal?.id ? (
                <div className="ap-row-actions">
                  <button className="button" disabled={busyId === call.id} onClick={() => void resolveConnector(call, true)}>
                    Approve &amp; run
                  </button>
                  <button className="button secondary" disabled={busyId === call.id} onClick={() => void resolveConnector(call, false)}>
                    Decline
                  </button>
                </div>
              ) : (
                <span className="badge">With an admin</span>
              )}
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
