"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import MarkdownAnswer from "@/components/MarkdownAnswer";
import Page from "@/components/Page";
import { api, formatDate } from "@/lib/api";

const STARTERS = [
  "Prepare a release readiness brief from GitHub and remembered decisions.",
  "Draft a Slack update explaining what changed and what remains blocked.",
  "Triage the open launch risks and prepare a decision memo.",
];

function statusLabel(value = "") {
  return value.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}

function isDirectNotification(value = "") {
  return (
    /\b(?:draft|prepare|write|post|send)\s+(?:a\s+)?slack\s+(?:message|update|announcement)\s+(?:that|saying)\b/i.test(value)
    || (
      /\b(?:notify|announce\s+to|message|tell|remind|send\s+(?:a\s+)?(?:notification|message|update)\s+to)\b/i.test(value)
      && /\b(?:team|everyone|group|people|staff|colleagues)\b/i.test(value)
    )
  );
}

export default function MemoryWorkPage() {
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState("");
  const [objective, setObjective] = useState(STARTERS[0]);
  const [history, setHistory] = useState<any[]>([]);
  const [work, setWork] = useState<any>();
  const [creating, setCreating] = useState(false);
  const [resolving, setResolving] = useState("");
  const [channels, setChannels] = useState<any[]>([]);
  const [channelId, setChannelId] = useState("");
  const [messageDraft, setMessageDraft] = useState("");
  const [slackError, setSlackError] = useState("");
  const [error, setError] = useState("");

  const loadHistory = useCallback(async (nextProjectId: string, openLatest = false) => {
    if (!nextProjectId) return;
    const items = await api<any[]>(`/api/work?project_id=${encodeURIComponent(nextProjectId)}`);
    setHistory(items);
    if (openLatest && items[0]) setWork(await api(`/api/work/${items[0].id}`));
  }, []);

  useEffect(() => {
    api<any[]>("/api/projects").then(items => {
      setProjects(items);
      if (items[0]) setProjectId(items[0].id);
    }).catch(exc => setError(exc.message));
  }, []);

  useEffect(() => {
    setWork(undefined);
    loadHistory(projectId, true).catch(exc => setError(exc.message));
  }, [projectId, loadHistory]);

  async function createWork() {
    if (!projectId || !objective.trim()) return;
    setCreating(true);
    setError("");
    setSlackError("");
    try {
      const created = await api<any>("/api/work", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, objective: objective.trim() }),
      });
      setWork(created);
      await loadHistory(projectId);
    } catch (exc: any) {
      setError(exc.message);
    } finally {
      setCreating(false);
    }
  }

  async function resolve(stepId: string, approved: boolean) {
    if (!work) return;
    setResolving(stepId);
    setError("");
    setSlackError("");
    try {
      const next: any = await api(`/api/work/${work.id}/steps/${stepId}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          approved,
          channel_id: approved ? channelId : "",
          message: approved ? messageDraft : "",
        }),
      });
      setWork(next);
      const failed = next.steps?.find((step: any) => step.connector === "slack" && step.status === "failed");
      if (failed?.output?.error) setSlackError(failed.output.error);
      await loadHistory(projectId);
    } catch (exc: any) {
      setError(exc.message);
    } finally {
      setResolving("");
    }
  }

  async function openWork(id: string) {
    setWork(await api(`/api/work/${id}`));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const evidence = work?.context?.evidence || [];
  const memories = work?.context?.memory_units || [];
  const actionOnly = Boolean(work?.context?.action_only);
  const directNotification = isDirectNotification(objective);
  const slackStep = work?.steps?.find((step: any) => step.connector === "slack" && step.step_type === "connector_action");
  const approvalStep = slackStep && ["pending_approval", "approved", "failed"].includes(slackStep.status) ? slackStep : undefined;
  const postedUrl = slackStep?.status === "completed" ? slackStep.output?.source_url : "";

  useEffect(() => {
    if (!slackStep) return;
    setMessageDraft(slackStep.input?.message || slackStep.output?.message || "");
    setChannelId(current => current || slackStep.input?.channel_id || "");
    if (slackStep.status === "failed" && slackStep.output?.error) setSlackError(slackStep.output.error);
    api<any[]>("/api/connectors/slack/channels").then(items => {
      setChannels(items);
      setChannelId(current => current || items[0]?.id || "");
    }).catch(exc => setSlackError(exc.message));
  }, [slackStep?.id, slackStep?.status]);

  function startAnother() {
    setWork(undefined);
    setSlackError("");
    setMessageDraft("");
    setChannelId("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  const selectedChannel = channels.find(channel => channel.id === channelId);
  const activeStage = useMemo(() => {
    if (creating) return 1;
    if (!work) return 0;
    if (work.status === "blocked_context") return 1;
    if (["awaiting_approval", "ready_for_worker", "posting", "post_failed"].includes(work.status)) return 3;
    return 4;
  }, [creating, work]);

  return <Page
    eyebrow="Company memory → useful work"
    title="Memory Work"
    description="Give MemoryWorks an outcome. It activates source-backed context, prepares the result, and executes only the connector action you explicitly approve."
    action={projects.length ? <select aria-label="Active project" value={projectId} onChange={event => setProjectId(event.target.value)}>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select> : undefined}
  >
    {error && <div className="notice error">{error}</div>}

    {slackStep ? <section className={`work-composer work-slack-review ${resolving ? "is-posting" : ""}`}>
      <div className="work-composer-copy">
        <div className="memory-live"><i/><span>{slackStep.status === "completed" ? "Posted to Slack" : "Approval required"}</span></div>
        <h2>{slackStep.status === "completed" ? "Your update is live." : "Review exactly what Slack will receive."}</h2>
        {slackStep.status !== "completed" && <>
          <label className="slack-channel-field"><span>Post in</span><select aria-label="Slack channel" value={channelId} onChange={event => setChannelId(event.target.value)} disabled={resolving !== ""}>{channels.length ? channels.map(channel => <option value={channel.id} key={channel.id}>#{channel.name || channel.id}</option>) : <option value="">No available channels</option>}</select></label>
          <label className="slack-message-field"><span>Slack message</span><textarea aria-label="Slack message" value={messageDraft} onChange={event => setMessageDraft(event.target.value)} rows={10} disabled={resolving !== ""}/></label>
        </>}
        {slackError && <div className="slack-post-error"><strong>Could not post</strong><span>{slackError}</span><Link href="/connectors">Reconnect Slack</Link></div>}
        <div className="slack-approval-actions">
          {approvalStep && <button className="button work-create-button" disabled={resolving !== "" || !channelId || !messageDraft.trim()} onClick={() => resolve(approvalStep.id, true)}>
            {resolving ? <><span className="button-spinner"/> Posting to Slack…</> : <>{approvalStep.status === "failed" ? "Retry posting" : approvalStep.status === "approved" ? "Post approved message" : "Approve & post"}{selectedChannel ? ` to #${selectedChannel.name}` : ""} <span>→</span></>}
          </button>}
          {approvalStep?.status === "pending_approval" && <button className="button work-draft-button" disabled={resolving !== ""} onClick={() => resolve(approvalStep.id, false)}>Keep as draft</button>}
          {postedUrl && <a className="button work-create-button" href={postedUrl} target="_blank" rel="noreferrer">Open Slack message ↗</a>}
          <button className="text-button work-new-outcome" onClick={startAnother}>{slackStep.status === "completed" ? "Create another update" : "Start over"}</button>
        </div>
      </div>
      <WorkFlow activeStage={activeStage} finalLabel="Post" />
    </section> :
    <section className={`work-composer ${creating ? "is-creating" : ""}`}>
      <div className="work-composer-copy">
        <div className="memory-live"><i/><span>HCAG context activation</span></div>
        <h2>Describe the outcome. MemoryWorks handles the context.</h2>
        <p>Describe the result once. MemoryWorks selects the current company context and exact source evidence.</p>
        <textarea aria-label="Work objective" value={objective} onChange={event => setObjective(event.target.value)} rows={4} placeholder="What should an AI worker get done?"/>
        <div className="work-starters">{STARTERS.map(starter => <button key={starter} type="button" onClick={() => setObjective(starter)}>{starter}</button>)}</div>
        <button className="button work-create-button" disabled={creating || !projectId || objective.trim().length < 3} onClick={createWork}>
          {creating
            ? <><span className="button-spinner"/> {directNotification ? "Preparing Slack message…" : "Activating company context…"}</>
            : <>{directNotification ? "Prepare Slack message" : "Prepare Memory Work"} <span>→</span></>}
        </button>
      </div>
      <WorkFlow activeStage={activeStage} />
    </section>}

    {work && <section className="work-result" key={work.id}>
      <header className="work-result-head">
        <div><span className={`work-status status-${work.status}`}>{slackStep?.status === "approved" ? "Approved · not posted" : statusLabel(work.status)}</span><h2>{work.objective}</h2><p>Created {formatDate(work.created_at)} · {actionOnly ? "Direct instruction · no memory retrieval" : `${Math.round((work.confidence || 0) * 100)}% retrieval confidence`}</p></div>
        {!slackStep && <button className="button secondary" onClick={startAnother}>New outcome</button>}
      </header>

      {work.status === "blocked_context" ? <div className="work-context-blocked"><span>?</span><div><strong>Company memory is not sufficient yet</strong><p>MemoryWorks stopped before producing work because it could not find evidence for this outcome. Add a relevant source, then try again.</p></div></div> :
      actionOnly ? <div className="work-action-only"><span>✓</span><div><small>Action only</small><strong>No company memory was created.</strong><p>This Slack message came only from your instruction. Nothing will be posted until you select a channel and approve the exact message above.</p></div></div> :
      <div className="work-result-grid">
        <div className="work-primary">
          <section className="panel work-context-panel">
            <div className="panel-head"><div><span className="panel-label">Source-backed result</span><h2>Answer</h2></div><span className="source-count">{evidence.length} sources · {memories.length} memories</span></div>
            <div className="panel-body"><MarkdownAnswer>{work.context?.answer || "Source-backed context was assembled for this outcome."}</MarkdownAnswer></div>
          </section>
        </div>

        <aside className="work-sidebar">
          <section className="panel work-evidence-panel">
            <div className="panel-head"><div><span className="panel-label">Citations</span><h2>Sources used</h2></div></div>
            <div className="panel-body">{evidence.length ? evidence.slice(0, 8).map((item: any, index: number) => <a key={`${item.source_id}-${index}`} href={item.source_url || "#"} target={item.source_url ? "_blank" : undefined} rel="noreferrer"><span>{index + 1}</span><div><strong>{item.source_title || item.title || "Company source"}</strong><small>{Math.round((item.relevance ?? item.confidence ?? item.score ?? 0) * 100)}% retrieval match</small></div></a>) : <p className="muted">No evidence attached.</p>}</div>
          </section>
        </aside>
      </div>}
    </section>}

    <section className="panel work-history">
      <div className="panel-head"><div><span className="panel-label">Durable work memory</span><h2>Recent outcomes</h2></div></div>
      <div className="panel-body">{history.length ? history.map(item => <button key={item.id} onClick={() => openWork(item.id)}><span className={`work-history-dot status-${item.status}`}/><div><strong>{item.objective}</strong><small>{statusLabel(item.status)} · {formatDate(item.updated_at)}</small></div><b>→</b></button>) : <div className="friendly-empty"><span>✦</span><div><strong>No Memory Work yet</strong><p>Your first outcome and its evidence trail will stay here.</p></div></div>}</div>
    </section>
  </Page>;
}

function WorkFlow({ activeStage, finalLabel = "Hand off" }: { activeStage: number; finalLabel?: string }) {
  const labels = ["Scope", "Activate", "Prepare", finalLabel];
  return <div className="work-flow" aria-hidden="true">
    <div className="work-flow-aura"/>
    {labels.map((label, index) => <div className={`work-flow-stage ${index < activeStage ? "complete" : index === activeStage ? "active" : ""}`} key={label}>
      <span>{index < activeStage ? "✓" : index + 1}</span><strong>{label}</strong>
      {index < labels.length - 1 && <i><b/></i>}
    </div>)}
    <small>Authorized context in · verifiable work out</small>
  </div>;
}
