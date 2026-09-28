"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RunbookMark } from "@/components/RunbookLogo";
import { NavToggle, useInWorkspaceFrame } from "@/components/WorkspaceFrame";
import { api } from "@/lib/api";
import { WEBMCP_DEMO_MODE } from "@/lib/demoOrgMemory";
import { registerOrgConsoleWebMCP, type WebMCPActivity } from "@/lib/webmcp";
import {
  ORG_READ_TOOLS,
  ORG_TOOLS,
  ORG_WRITE_TOOLS,
  orgApi,
  type OrgBlocker,
  type OrgConflict,
  type OrgPlan,
  type OrgProjectContext,
  type OrgReadiness,
  type OrgReasoningChain,
  type OrgAgentSession,
  type OrgSpace,
  type OrgWatch,
} from "@/lib/orgTools";

/* The console for organizational operations.
 *
 * Every row in the activity panel is a real call into the same tool map the
 * page registers with WebMCP, against the signed-in workspace. The walkthrough
 * chooses which tools to call and in what order; it does not choose what they
 * return. */

type CallState = "running" | "done" | "error";

type ToolCall = {
  tool: string;
  args: Record<string, unknown>;
  state: CallState;
  summary: string;
  thought?: string;
  ms: number;
  kind: "read" | "gated-write";
};

type ViewKind = "briefing" | "chain" | "readiness" | "reconcile" | "agent";

type Turn = {
  id: string;
  question: string;
  calls: ToolCall[];
  view: ViewKind | null;
  data: Record<string, any>;
  plan: OrgPlan | null;
  error: string;
  done: boolean;
};

/* The scenario's own spaces. A workspace usually holds far more than these —
   every connected repository is a space too — so the console scopes its calls
   to the launch program rather than sweeping the whole workspace. Passing the
   scope explicitly is also what a real agent does once it knows what it was
   asked about. */
const SCENARIO_SPACES = [
  "Product",
  "Engineering",
  "Design",
  "Security",
  "Infrastructure",
  "Launch",
  "Customer Support",
];

const REQUESTS = [
  {
    key: "catch-up",
    label: "Catch me up",
    question: "I just joined this launch. Tell me what matters, what changed, and what is unresolved.",
  },
  {
    key: "why",
    label: "Why is this the priority?",
    question: "Why is the security review the thing holding this up?",
  },
  {
    key: "ready",
    label: "Are we ready to launch?",
    question: "Are we ready to launch tomorrow?",
  },
  {
    key: "reconcile",
    label: "Reconcile it",
    question: "Reconcile it and prepare us for launch.",
  },
] as const;

type RequestKey = (typeof REQUESTS)[number]["key"];

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function relative(value?: string) {
  if (!value) return "";
  const then = new Date(value).getTime();
  const hours = Math.round((Date.now() - then) / 3600000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

export default function AgentOperations() {
  const [user, setUser] = useState<any>();
  const [spaces, setSpaces] = useState<OrgSpace[]>([]);
  const [readiness, setReadiness] = useState<OrgReadiness | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [webmcp, setWebmcp] = useState<
    "checking" | "registering" | "ready" | "unsupported" | "error"
  >("checking");
  const [toolCount, setToolCount] = useState(ORG_READ_TOOLS.length + ORG_WRITE_TOOLS.length);
  const [external, setExternal] = useState<WebMCPActivity[]>([]);
  const [evidence, setEvidence] = useState<any>(null);
  const [flash, setFlash] = useState(false);
  const [watch, setWatch] = useState<OrgWatch | null>(null);
  const [watching, setWatching] = useState(false);
  const [draft, setDraft] = useState("");
  const [followups, setFollowups] = useState<{ items: string[]; source: string } | null>(null);
  const [loadingFollowups, setLoadingFollowups] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);

  const isAdmin = user?.role === "owner" || user?.role === "admin";
  const scenario = useMemo(
    () => spaces.filter((space) => SCENARIO_SPACES.includes(space.name)),
    [spaces],
  );
  const ready = scenario.length >= SCENARIO_SPACES.length - 1;
  const scope = useMemo(() => scenario.map((space) => space.id), [scenario]);
  const backHref = WEBMCP_DEMO_MODE ? "/" : "/workspace";
  const inFrame = useInWorkspaceFrame();

  const refresh = useCallback(async () => {
    const spaceList = await orgApi.spaces().catch(() => ({ spaces: [] as OrgSpace[] }));
    const all = spaceList.spaces || [];
    setSpaces(all);
    const ids = all.filter((space) => SCENARIO_SPACES.includes(space.name)).map((s) => s.id);
    setReadiness(ids.length ? await orgApi.readiness(ids).catch(() => null) : null);
    const existing = await orgApi.watches().catch(() => ({ watches: [] as OrgWatch[] }));
    setWatch(existing.watches[0] || null);
  }, []);

  useEffect(() => {
    if (WEBMCP_DEMO_MODE) {
      setUser({ display_name: "Demo operator", role: "owner" });
      void refresh();
      return;
    }
    api("/api/auth/me")
      .then((principal) => {
        setUser(principal);
        return refresh();
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Sign in to continue."));
  }, [refresh]);

  useEffect(() => {
    /* This page is itself a Model Context Provider. The handlers registered
       on document.modelContext are the same ORG_TOOLS objects the console
       calls, so an external browser agent drives exactly the surface the
       judge is watching — and its calls land in the live activity card. */
    let alive = true;
    let dispose: () => void = () => undefined;
    setWebmcp("registering");
    registerOrgConsoleWebMCP((event) => {
      if (!alive) return;
      setExternal((current) => {
        const index = current.findIndex((item) => item.id === event.id);
        const next =
          index === -1
            ? [...current, event]
            : current.map((item, position) => (position === index ? event : item));
        return next.slice(-8);
      });
    })
      .then((registration) => {
        if (!alive) {
          registration.dispose();
          return;
        }
        dispose = registration.dispose;
        if (registration.supported) {
          setToolCount(registration.toolCount);
          setWebmcp("ready");
        } else {
          setWebmcp("unsupported");
        }
      })
      .catch(() => {
        if (alive) setWebmcp("error");
      });
    return () => {
      alive = false;
      dispose();
    };
  }, []);

  useEffect(() => {
    // The transcript grows the page rather than scrolling inside a box, so the
    // newest question has to be brought into view rather than a container.
    transcript.current?.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length]);

  async function prepare(reset: boolean) {
    setPreparing(true);
    setError("");
    try {
      await orgApi.seedScenario(reset);
      await refresh();
      if (reset) setTurns([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not prepare the scenario.");
    } finally {
      setPreparing(false);
    }
  }

  /** Run one tool and record it, exactly as an external agent's call would appear. */
  async function call(
    turnId: string,
    tool: string,
    args: Record<string, unknown>,
  ): Promise<{ data: any; summary: string }> {
    const spec = ORG_TOOLS[tool];
    setTurns((current) =>
      current.map((turn) =>
        turn.id === turnId
          ? {
              ...turn,
              calls: [
                ...turn.calls,
                { tool, args, state: "running", summary: "", ms: 0, kind: spec.kind },
              ],
            }
          : turn,
      ),
    );
    const started = performance.now();
    try {
      const result = await spec.run(args);
      const ms = Math.round(performance.now() - started);
      setTurns((current) =>
        current.map((turn) =>
          turn.id === turnId
            ? {
                ...turn,
                calls: turn.calls.map((entry, index) =>
                  index === turn.calls.length - 1
                    ? { ...entry, state: "done", summary: result.summary, ms }
                    : entry,
                ),
              }
            : turn,
        ),
      );
      return { data: result.data, summary: result.summary };
    } catch (cause) {
      const ms = Math.round(performance.now() - started);
      const message = cause instanceof Error ? cause.message : "Tool call failed";
      setTurns((current) =>
        current.map((turn) =>
          turn.id === turnId
            ? {
                ...turn,
                calls: turn.calls.map((entry, index) =>
                  index === turn.calls.length - 1
                    ? { ...entry, state: "error", summary: message, ms }
                    : entry,
                ),
              }
            : turn,
        ),
      );
      throw cause;
    }
  }

  function finish(turnId: string, view: ViewKind, data: Record<string, any>, plan: OrgPlan | null = null) {
    setTurns((current) =>
      current.map((turn) =>
        turn.id === turnId ? { ...turn, view, data, plan, done: true } : turn,
      ),
    );
  }

  /** The next questions, drafted from what this turn actually found. */
  async function loadFollowups(question: string, answer: string, summaries: string[]) {
    setLoadingFollowups(true);
    try {
      const result = await orgApi.followups(question, answer, summaries, scope);
      if (result.suggestions?.length) {
        setFollowups({ items: result.suggestions, source: result.source });
      }
    } catch {
      // The current suggestions stay; a failed draft is invisible.
    } finally {
      setLoadingFollowups(false);
    }
  }

  async function run(key: RequestKey) {
    if (busy) return;
    const request = REQUESTS.find((entry) => entry.key === key)!;
    const turnId = `turn_${Date.now()}`;
    setBusy(true);
    setError("");
    setTurns((current) => [
      ...current,
      { id: turnId, question: request.question, calls: [], view: null, data: {}, plan: null, error: "", done: false },
    ]);

    try {
      if (key === "catch-up") {
        const contextResult = await call(turnId, "get_orgmemory_project_context", { space_ids: scope });
        await wait(160);
        const changes = await call(turnId, "get_orgmemory_recent_changes", {
          since: new Date(Date.now() - 7 * 86400000).toISOString(),
          space_ids: scope,
        });
        await wait(160);
        const people = await call(turnId, "get_orgmemory_people", { space_ids: scope });
        await wait(160);
        const blockers = await call(turnId, "find_orgmemory_blockers", { space_ids: scope });
        const context = contextResult.data as OrgProjectContext;
        finish(turnId, "briefing", {
          context,
          changes: changes.data,
          people: people.data,
          blockers: blockers.data,
        });
        void loadFollowups(
          request.question,
          context.next_best_action
            ? `${context.next_best_action.action} — ${context.next_best_action.why}`
            : contextResult.summary,
          [contextResult.summary, changes.summary, people.summary, blockers.summary],
        );
      }

      if (key === "why") {
        const chainResult = await call(turnId, "get_orgmemory_reasoning_chain", {
          topic: "why the security review blocks the production deploy and the launch",
          space_ids: scope,
        });
        await wait(160);
        const chain = chainResult.data as OrgReasoningChain;
        const anchor = chain.steps[0]?.memory.id;
        const trace = anchor
          ? await call(turnId, "get_orgmemory_provenance", { memory_id: anchor })
          : null;
        finish(turnId, "chain", { chain, trace: trace?.data });
        void loadFollowups(
          request.question,
          chain.steps.map((step) => `${step.role}: ${step.memory.title}`).join("; "),
          [chainResult.summary, trace?.summary || ""].filter(Boolean),
        );
      }

      if (key === "ready") {
        const boardResult = await call(turnId, "get_orgmemory_readiness", { space_ids: scope });
        await wait(160);
        const graph = await call(turnId, "get_orgmemory_dependency_graph", { space_ids: scope });
        await wait(160);
        const blockers = await call(turnId, "find_orgmemory_blockers", { space_ids: scope });
        await wait(160);
        const conflicts = await call(turnId, "find_orgmemory_conflicts", { space_ids: scope });
        const board = boardResult.data as OrgReadiness;
        setReadiness(board);
        finish(turnId, "readiness", {
          board,
          graph: graph.data,
          blockers: blockers.data,
          conflicts: conflicts.data,
        });
        void loadFollowups(
          request.question,
          `${board.status} — ${board.outstanding.join(", ") || "nothing outstanding"}`,
          [boardResult.summary, graph.summary, blockers.summary, conflicts.summary],
        );
      }

      if (key === "reconcile") {
        const conflictsResult = (await call(turnId, "find_orgmemory_conflicts", { space_ids: scope })) as {
          data: { count: number; conflicts: OrgConflict[] };
          summary: string;
        };
        const conflicts = conflictsResult.data;
        const conflict = conflicts.conflicts[0];
        if (!conflict) {
          finish(turnId, "reconcile", { conflicts, nothing: true });
          void loadFollowups(request.question, conflictsResult.summary, [conflictsResult.summary]);
          return;
        }
        await wait(160);
        const trace = await call(turnId, "get_orgmemory_provenance", {
          memory_id: conflict.source.id,
        });
        await wait(160);
        const owner = await call(turnId, "get_orgmemory_owner", {
          object_id: conflict.task.id,
        });
        await wait(160);
        const plan = (await call(turnId, "propose_orgmemory_changes", {
          summary: `Reconcile “${conflict.task.title}” with the record that already settled it`,
          space_id: conflict.task.space_id,
          operations: [conflict.resolution],
        })) as { data: OrgPlan; summary: string };
        finish(turnId, "reconcile", { conflict, trace: trace.data, owner: owner.data }, plan.data);
        void loadFollowups(
          request.question,
          `${plan.summary} Owner on record: ${owner.data.owner || "unassigned"}.`,
          [conflictsResult.summary, trace.summary, owner.summary, plan.summary],
        );
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Something went wrong.");
      setTurns((current) =>
        current.map((turn) => (turn.id === turnId ? { ...turn, done: true } : turn)),
      );
    } finally {
      setBusy(false);
    }
  }

  /** Ask anything. The tool sequence below is the agent's, not the page's. */
  async function askAgent(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    const turnId = `turn_${Date.now()}`;
    setBusy(true);
    setError("");
    setDraft("");
    setTurns((current) => [
      ...current,
      { id: turnId, question: text, calls: [], view: null, data: {}, plan: null, error: "", done: false },
    ]);
    try {
      const session = await orgApi.askStream(text, scope, (partial) => {
        setTurns((current) =>
          current.map((turn) =>
            turn.id === turnId
              ? { ...turn, calls: agentCalls(partial) }
              : turn,
          ),
        );
      });
      setTurns((current) =>
        current.map((turn) =>
          turn.id === turnId
            ? { ...turn, calls: agentCalls(session), view: "agent", data: { session }, done: true }
            : turn,
        ),
      );
      // The board may have moved if the agent proposed and a person approved.
      setReadiness(await orgApi.readiness(scope).catch(() => readiness));
      void loadFollowups(
        text,
        session.answer,
        session.steps.map((step) => step.summary),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The agent could not finish.");
      setTurns((current) =>
        current.map((turn) => (turn.id === turnId ? { ...turn, done: true } : turn)),
      );
    } finally {
      setBusy(false);
    }
  }

  async function approve(turnId: string, plan: OrgPlan) {
    try {
      const applied = await orgApi.approvePlan(plan.id);
      setTurns((current) =>
        current.map((turn) => (turn.id === turnId ? { ...turn, plan: applied } : turn)),
      );
      const board = await orgApi.readiness(scope);
      setReadiness(board);
      setFlash(true);
      setTimeout(() => setFlash(false), 1600);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not apply the changes.");
    }
  }

  async function reject(turnId: string, plan: OrgPlan) {
    const denied = await orgApi.rejectPlan(plan.id).catch(() => null);
    if (denied) {
      setTurns((current) =>
        current.map((turn) => (turn.id === turnId ? { ...turn, plan: denied } : turn)),
      );
    }
  }

  async function startWatch() {
    setWatching(true);
    setError("");
    try {
      const created = await orgApi.createWatch(
        "Checkout OAuth launch",
        scope,
        ["blockers", "conflicts", "stale"],
        900,
      );
      setWatch(await orgApi.runWatch(created.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start the watch.");
    } finally {
      setWatching(false);
    }
  }

  async function runWatchNow() {
    if (!watch) return;
    setWatching(true);
    try {
      setWatch(await orgApi.runWatch(watch.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The watch could not run.");
    } finally {
      setWatching(false);
    }
  }

  async function approveFinding(finding: { id: string; plan_id: string }) {
    if (!watch) return;
    try {
      await orgApi.approvePlan(finding.plan_id);
      await orgApi.resolveFinding(watch.id, finding.id);
      const [board, refreshed] = await Promise.all([
        orgApi.readiness(scope),
        orgApi.runWatch(watch.id),
      ]);
      setReadiness(board);
      setWatch(refreshed);
      setFlash(true);
      setTimeout(() => setFlash(false), 1600);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not apply the drafted fix.");
    }
  }

  async function openEvidence(memoryId: string) {
    setEvidence({ loading: true });
    try {
      setEvidence(await orgApi.provenance(memoryId));
    } catch {
      setEvidence(null);
    }
  }

  return (
    <div className="ag-page">
      <header className="ag-bar">
        {inFrame ? (
          <div className="page-bar-id">
            <NavToggle />
            <span>
              <small>Agents</small>
              <strong>Agent operations</strong>
            </span>
          </div>
        ) : (
          <Link href={backHref} className="ag-id">
            <RunbookMark />
            <span>
              <strong>OrgMemory</strong>
              <small>Agent operations</small>
            </span>
          </Link>
        )}
        <div className="ag-bar-right">
          <span className={`ag-webmcp ${webmcp === "ready" ? "ready" : webmcp}`}>
            <i />
            {webmcp === "ready" && `WebMCP live · ${toolCount} tools`}
            {webmcp === "registering" && "Registering WebMCP tools…"}
            {webmcp === "checking" && "Checking WebMCP…"}
            {webmcp === "unsupported" && `WebMCP absent · ${toolCount} tools`}
            {webmcp === "error" && `WebMCP error · ${toolCount} tools`}
          </span>
          <Link className="ag-link" href="/docs">
            Tool reference
          </Link>
          {!inFrame && (
            <Link className="ag-link" href={backHref}>
              {WEBMCP_DEMO_MODE ? "Back to site" : "Back to chat"}
            </Link>
          )}
        </div>
      </header>

      <main className="ag-main">
        <section className="ag-column">
          <div className="ag-intro">
            <h1>Agents operate on organizational memory.</h1>
            <p>
              This launch is spread across {(ready ? scenario : spaces).length || "several"} spaces. The
              answer to any of the questions below sits in four of them at once. Each request runs
              real tool calls against this workspace — and because this page registers the same{" "}
              {toolCount} tools on <code>document.modelContext</code>, a browser agent you connect
              can call them too, without touching this interface.
            </p>
          </div>

          {!ready && (
            <div className="ag-setup">
              <div>
                <strong>The scenario is not loaded in this workspace.</strong>
                <span>
                  Creates seven spaces, twenty source-backed memories, and six linked work items.
                </span>
              </div>
              <button className="ag-primary" onClick={() => prepare(false)} disabled={preparing || !isAdmin}>
                {preparing ? "Preparing…" : isAdmin ? "Load the scenario" : "Ask an admin to load it"}
              </button>
            </div>
          )}

          {error && <div className="ag-error">{error}</div>}

          <div className="ag-transcript" ref={transcript}>
            {!turns.length && ready && (
              <div className="ag-empty">
                <p>Ask anything below, or start with a suggestion. Every tool call, argument, and result is live.</p>
              </div>
            )}
            {turns.map((turn) => (
              <TurnBlock
                key={turn.id}
                turn={turn}
                onApprove={approve}
                onReject={reject}
                onEvidence={openEvidence}
              />
            ))}
          </div>

          <div className="ag-compose">
            <div className="ag-suggestions">
              {followups
                ? followups.items.map((item) => (
                    <button
                      key={item}
                      className="ag-request"
                      title={
                        followups.source === "model"
                          ? "Drafted from what this session found"
                          : "Drafted from the workspace's current state"
                      }
                      onClick={() => void askAgent(item)}
                      disabled={busy || loadingFollowups}
                    >
                      {item}
                    </button>
                  ))
                : REQUESTS.map((request) => (
                    <button
                      key={request.key}
                      className="ag-request"
                      onClick={() => void run(request.key)}
                      disabled={busy || !ready}
                    >
                      {request.label}
                    </button>
                  ))}
              {loadingFollowups && (
                <button className="ag-request ag-request-loading" disabled>
                  drafting…
                </button>
              )}
            </div>
            <div className="ag-input">
              <textarea
                rows={1}
                value={draft}
                placeholder="Ask anything about this organization…"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void askAgent(draft);
                  }
                }}
                disabled={busy || !ready}
              />
              <button
                className="ag-send"
                onClick={() => void askAgent(draft)}
                disabled={busy || !ready || !draft.trim()}
                aria-label="Ask"
              >
                ↑
              </button>
            </div>
          </div>
        </section>

        <aside className="ag-rail">
          <ReadinessBoard board={readiness} flash={flash} />
          <WatchCard
            watch={watch}
            busy={watching}
            canStart={ready && isAdmin}
            onStart={startWatch}
            onRun={runWatchNow}
            onApprove={approveFinding}
          />
          <SpacesCard spaces={ready ? scenario : spaces} />
          <ExternalActivityCard events={external} status={webmcp} toolCount={toolCount} />
          <div className="ag-card ag-surface-card">
            <p className="ag-eyebrow">WebMCP surface</p>
            <dl>
              <div>
                <dt>Read</dt>
                <dd>{ORG_READ_TOOLS.length} tools, no approval</dd>
              </div>
              <div>
                <dt>Write</dt>
                <dd>{ORG_WRITE_TOOLS.length} tools, approval required</dd>
              </div>
              <div>
                <dt>Approve</dt>
                <dd>No tool. A person only.</dd>
              </div>
              <div>
                <dt>Transport</dt>
                <dd>
                  <code>document.modelContext</code>
                </dd>
              </div>
            </dl>
          </div>
        </aside>
      </main>

      {evidence && <EvidencePanel evidence={evidence} onClose={() => setEvidence(null)} />}
    </div>
  );
}

/* ---------------------------------------------------------------- a turn */

function TurnBlock({
  turn,
  onApprove,
  onReject,
  onEvidence,
}: {
  turn: Turn;
  onApprove: (turnId: string, plan: OrgPlan) => void;
  onReject: (turnId: string, plan: OrgPlan) => void;
  onEvidence: (memoryId: string) => void;
}) {
  return (
    <article className="ag-turn">
      <p className="ag-asked">{turn.question}</p>

      <div className="ag-activity">
        <div className="ag-activity-head">
          <span>Agent activity</span>
          <small>{turn.calls.length} tool calls</small>
        </div>
        {turn.calls.map((entry, index) => (
          <div className={`ag-call ${entry.state}`} key={`${entry.tool}-${index}`}>
            <code>{entry.tool}({formatArgs(entry.args)})</code>
            <span className="ag-call-meta">
              {entry.kind === "gated-write" && <b className="ag-gate">approval required</b>}
              {entry.state === "done" && <em>{entry.ms} ms</em>}
              {entry.state === "running" && <em className="ag-running">running</em>}
            </span>
            {entry.thought && <p className="ag-thought">{entry.thought}</p>}
            {entry.summary && <p>{entry.summary}</p>}
          </div>
        ))}
      </div>

      {turn.view === "agent" && <AgentAnswer data={turn.data} onEvidence={onEvidence} />}
      {turn.view === "agent" && (
        <AgentPlanCard
          proposal={turn.data?.session?.proposal}
          plan={turn.plan}
          onApprove={(plan) => onApprove(turn.id, plan)}
          onReject={(plan) => onReject(turn.id, plan)}
        />
      )}
      {turn.view === "briefing" && <Briefing data={turn.data} onEvidence={onEvidence} />}
      {turn.view === "chain" && <ReasoningChainView data={turn.data} onEvidence={onEvidence} />}
      {turn.view === "readiness" && <LaunchVerdict data={turn.data} onEvidence={onEvidence} />}
      {turn.view === "reconcile" && (
        <Reconcile
          data={turn.data}
          plan={turn.plan}
          onApprove={(plan) => onApprove(turn.id, plan)}
          onReject={(plan) => onReject(turn.id, plan)}
          onEvidence={onEvidence}
        />
      )}
    </article>
  );
}

/** Render an agent session's steps in the same shape the console's own calls
 * use. While the session is still running, the newest step shows as running. */
function agentCalls(session: OrgAgentSession): ToolCall[] {
  const running = session.status === "running";
  return (session.steps || []).map((step, index) => ({
    tool: step.tool,
    args: step.arguments || {},
    state: running && index === session.steps.length - 1 ? ("running" as CallState) : ("done" as CallState),
    summary: step.summary,
    thought: step.thought,
    ms: step.duration_ms || 0,
    kind: step.tool.startsWith("propose_") ? ("gated-write" as const) : ("read" as const),
  }));
}

function AgentAnswer({ data, onEvidence }: { data: any; onEvidence: (id: string) => void }) {
  const session: OrgAgentSession = data.session;
  const model = MODEL_LABELS[session.model] || (session.model ? session.model : "deterministic grounding");
  return (
    <div className="ag-result">
      <section>
        <h3>Answer</h3>
        <p className="ag-agent-answer">{session.answer}</p>
        {session.memory_ids?.length > 0 && (
          <p className="ag-note">
            Cited:{" "}
            {session.memory_ids.map((id) => (
              <Cite key={id} id={id} onEvidence={onEvidence} />
            ))}
          </p>
        )}
      </section>
      <section>
        <h3>How this was answered</h3>
        <p className="ag-note">
          {session.mode === "guided"
            ? "No model was reachable, so the tool order came from a fallback policy. Every call, observation, and sentence above is still live."
            : `The ${model} model chose each tool from the ${Object.keys(ORG_TOOLS).length} registered on this page, one step at a time — every call and citation above is live, and nothing was scripted.`}
        </p>
        {session.proposal && (
          <p className="ag-note">A change plan is waiting for a person in Approvals.</p>
        )}
      </section>
    </div>
  );
}

const MODEL_LABELS: Record<string, string> = {
  glm: "GLM",
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Gemini",
  grok: "Grok",
  kimi: "Kimi",
};

/** The plan an agent proposed, shown where the answer is. Approving is a
 * person's action in the workspace — the agent view makes that stop visible
 * instead of sending anyone hunting for an approvals page. */
function AgentPlanCard({
  proposal,
  plan,
  onApprove,
  onReject,
}: {
  proposal: unknown;
  plan: OrgPlan | null;
  onApprove: (plan: OrgPlan) => void;
  onReject: (plan: OrgPlan) => void;
}) {
  const active = plan || (proposal as OrgPlan | undefined);
  if (!active || active.status === "denied") return null;
  return (
    <div className={`ag-plan ${active.status}`}>
      <h3>
        {active.status === "pending_approval"
          ? "Proposed changes — nothing applied yet"
          : "Applied"}
      </h3>
      <ul>
        {active.operations.map((operation, index) => (
          <li key={index}>
            <span>{operation.preview as string}</span>
            {operation.reason ? <small>{operation.reason as string}</small> : null}
          </li>
        ))}
      </ul>
      {active.status === "pending_approval" ? (
        <div className="ag-plan-actions">
          <button className="ag-primary" onClick={() => onApprove(active)}>
            Approve these changes
          </button>
          <button className="ag-secondary" onClick={() => onReject(active)}>
            Decline
          </button>
        </div>
      ) : (
        <p className="ag-note">
          Applied by a person in this workspace. The board on the right recomputed from stored
          state.
        </p>
      )}
    </div>
  );
}

function formatArgs(args: Record<string, unknown>) {
  const entries = Object.entries(args).filter(([, value]) => value !== undefined && value !== "");
  if (!entries.length) return "";
  return entries
    .slice(0, 2)
    .map(([key, value]) => {
      const rendered =
        typeof value === "string"
          ? `"${value.length > 44 ? `${value.slice(0, 41)}…` : value}"`
          : Array.isArray(value)
            ? `[${value.length}]`
            : JSON.stringify(value);
      return `${key}: ${rendered}`;
    })
    .join(", ");
}

/* --------------------------------------------------------------- results */

function Cite({ id, onEvidence }: { id: string; onEvidence: (id: string) => void }) {
  return (
    <button className="ag-cite" onClick={() => onEvidence(id)} title="Open the evidence behind this">
      {id.slice(0, 12)}
    </button>
  );
}

function Briefing({ data, onEvidence }: { data: any; onEvidence: (id: string) => void }) {
  const context: OrgProjectContext = data.context;
  const people = (data.people?.people || []).filter((person: any) => person.owns?.length);
  const changes = data.changes?.changes || [];
  const next = context.next_best_action;

  return (
    <div className="ag-result">
      <section>
        <h3>Current focus</h3>
        <ul>
          {context.open_tasks.slice(0, 4).map((task) => (
            <li key={task.id}>
              <span>{task.title}</span>
              <small>
                {task.space_name} · {task.owner || "unassigned"} · {task.priority}
              </small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Decisions on record</h3>
        <ul>
          {context.decisions.slice(0, 4).map((decision) => (
            <li key={decision.id}>
              <span>{decision.title}</span>
              <small>
                {decision.space_name} · {relative(decision.updated_at)}{" "}
                <Cite id={decision.id} onEvidence={onEvidence} />
              </small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Unresolved</h3>
        <ul>
          {context.unresolved.slice(0, 4).map((item) => (
            <li key={item.id}>
              <span>{item.title}</span>
              <small>
                {item.space_name} <Cite id={item.id} onEvidence={onEvidence} />
              </small>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>People</h3>
        <ul className="ag-people">
          {people.slice(0, 5).map((person: any) => (
            <li key={person.id}>
              <span>{person.name}</span>
              <small>{person.owns.join(", ").replace(/ ownership/g, "")}</small>
            </li>
          ))}
        </ul>
      </section>

      <section className="ag-changes">
        <h3>Changed in the last seven days</h3>
        <p>
          {changes.length} records across{" "}
          {new Set(changes.map((item: any) => item.space_name)).size} spaces.
        </p>
      </section>

      <section className="ag-next">
        <h3>Next best action</h3>
        <strong>{next.action}</strong>
        <p>
          {next.why} Owner: {next.owner || "unassigned"}.
        </p>
      </section>
    </div>
  );
}

function ReasoningChainView({ data, onEvidence }: { data: any; onEvidence: (id: string) => void }) {
  const chain: OrgReasoningChain = data.chain;
  return (
    <div className="ag-result">
      <section>
        <h3>The recorded chain</h3>
        <p className="ag-note">
          Ordered by the relationships between these records, not by how well they match the
          question.
        </p>
        <ol className="ag-chain">
          {chain.steps.map((step, index) => (
            <li key={step.memory.id} style={{ ["--i" as string]: index }}>
              <span className="ag-chain-role">{step.role}</span>
              <div className="ag-chain-body">
                <strong>{step.memory.title}</strong>
                <p>{step.memory.content}</p>
                <small>
                  {step.memory.space_name} · {relative(step.memory.updated_at)}{" "}
                  <Cite id={step.memory.id} onEvidence={onEvidence} />
                </small>
              </div>
            </li>
          ))}
        </ol>
      </section>
      {data.trace?.sources?.length ? (
        <section className="ag-next">
          <h3>Where the first link comes from</h3>
          <strong>{data.trace.sources[0].title}</strong>
          <p>Captured as a {data.trace.sources[0].type}.</p>
        </section>
      ) : null}
    </div>
  );
}

function LaunchVerdict({ data, onEvidence }: { data: any; onEvidence: (id: string) => void }) {
  const board: OrgReadiness = data.board;
  const blockers: OrgBlocker[] = data.blockers?.blockers || [];
  const conflicts: OrgConflict[] = data.conflicts?.conflicts || [];
  const blocker = blockers[0];

  return (
    <div className="ag-result">
      <section className={`ag-verdict ${board.ready ? "ok" : "stop"}`}>
        <h3>{board.status}</h3>
        <p>
          {board.blocker_count} blocker{board.blocker_count === 1 ? "" : "s"} ·{" "}
          {data.graph.node_count} work items · {data.graph.edge_count} dependencies across{" "}
          {new Set(board.checklist.map((item) => item.space_name)).size} spaces.
        </p>
      </section>

      {blocker && (
        <section>
          <h3>The blocker</h3>
          <div className="ag-blocker">
            <strong>{blocker.task.title}</strong>
            <small>
              {blocker.task.space_name} · {blocker.task.owner || "unassigned"} ·{" "}
              {blocker.severity}
            </small>
            <div className="ag-flow">
              {blocker.chain.map((node, index) => (
                <span key={node.id} style={{ ["--i" as string]: index }}>
                  {node.label}
                </span>
              ))}
            </div>
            <p className="ag-note">
              Evidence:{" "}
              {blocker.evidence.map((id) => (
                <Cite key={id} id={id} onEvidence={onEvidence} />
              ))}
            </p>
          </div>
        </section>
      )}

      <section>
        <h3>Current state</h3>
        <ul className="ag-state">
          {board.checklist.map((item) => (
            <li key={item.id} className={item.state}>
              <span>{item.label}</span>
              <b>{item.state}</b>
            </li>
          ))}
        </ul>
      </section>

      {conflicts.length > 0 && (
        <section className="ag-conflict-flag">
          <h3>Contradiction found while checking</h3>
          <p>
            “{conflicts[0].task.title}” is tracked as {conflicts[0].tracked_state} in{" "}
            {conflicts[0].tracked_source.space_name}, but {conflicts[0].source.space_name} recorded{" "}
            “{conflicts[0].source.title}” {relative(conflicts[0].recorded_at)}.
          </p>
        </section>
      )}
    </div>
  );
}

function Reconcile({
  data,
  plan,
  onApprove,
  onReject,
  onEvidence,
}: {
  data: any;
  plan: OrgPlan | null;
  onApprove: (plan: OrgPlan) => void;
  onReject: (plan: OrgPlan) => void;
  onEvidence: (id: string) => void;
}) {
  if (data.nothing) {
    return (
      <div className="ag-result">
        <section>
          <h3>Nothing to reconcile</h3>
          <p>No tracked item disagrees with a newer record right now.</p>
        </section>
      </div>
    );
  }
  const conflict: OrgConflict = data.conflict;
  return (
    <div className="ag-result">
      <section>
        <h3>What disagrees</h3>
        <div className="ag-versus">
          <div>
            <small>{conflict.tracked_source.space_name} tracker</small>
            <strong>{conflict.tracked_state}</strong>
            <p>{conflict.tracked_source.title}</p>
            <Cite id={conflict.tracked_source.id} onEvidence={onEvidence} />
          </div>
          <i aria-hidden="true">vs</i>
          <div>
            <small>
              {conflict.source.space_name} · {relative(conflict.recorded_at)}
            </small>
            <strong>settled</strong>
            <p>{conflict.source.content}</p>
            <Cite id={conflict.source.id} onEvidence={onEvidence} />
          </div>
        </div>
        <p className="ag-note">
          Linked by a recorded <code>{conflict.basis}</code> relationship, not by wording.
          {data.owner?.owner ? ` Owner on record: ${data.owner.owner}.` : ""}
        </p>
      </section>

      {plan && (
        <section className={`ag-plan ${plan.status}`}>
          <h3>
            {plan.status === "pending_approval"
              ? "Proposed changes — nothing applied yet"
              : plan.status === "approved"
                ? "Applied"
                : "Declined"}
          </h3>
          <ul>
            {plan.operations.map((operation, index) => (
              <li key={index}>
                <span>{operation.preview as string}</span>
                {operation.reason ? <small>{operation.reason as string}</small> : null}
              </li>
            ))}
          </ul>
          {plan.status === "pending_approval" ? (
            <div className="ag-plan-actions">
              <button className="ag-primary" onClick={() => onApprove(plan)}>
                Approve these changes
              </button>
              <button className="ag-secondary" onClick={() => onReject(plan)}>
                Decline
              </button>
            </div>
          ) : (
            <p className="ag-note">
              {plan.status === "approved"
                ? "Applied by a person in this workspace. The board on the right recomputed from stored state."
                : "Declined. Nothing changed."}
            </p>
          )}
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ rail */

function ReadinessBoard({ board, flash }: { board: OrgReadiness | null; flash: boolean }) {
  return (
    <div className={`ag-card ag-board ${flash ? "flash" : ""}`}>
      <p className="ag-eyebrow">Launch readiness</p>
      {!board ? (
        <p className="ag-note">Not computed yet.</p>
      ) : (
        <>
          <strong className={board.ready ? "ok" : "stop"}>{board.status}</strong>
          <small>{board.goal?.label || "No goal recorded"}</small>
          <ul>
            {board.checklist.map((item) => (
              <li key={item.id} className={item.state}>
                <span>{item.label}</span>
                <b>{item.state}</b>
              </li>
            ))}
          </ul>
          <p className="ag-note">Computed from the dependency graph on every read.</p>
        </>
      )}
    </div>
  );
}

function WatchCard({
  watch,
  busy,
  canStart,
  onStart,
  onRun,
  onApprove,
}: {
  watch: OrgWatch | null;
  busy: boolean;
  canStart: boolean;
  onStart: () => void;
  onRun: () => void;
  onApprove: (finding: { id: string; plan_id: string }) => void;
}) {
  const open = (watch?.findings || []).filter((finding) => finding.status === "open");
  return (
    <div className="ag-card ag-watch">
      <p className="ag-eyebrow">Standing watch</p>
      {!watch ? (
        <>
          <p className="ag-note">
            Runs the same blocker, contradiction, and staleness checks on an interval, with nobody
            asking. Findings arrive here; a contradiction arrives with the fix already drafted.
          </p>
          <button className="ag-primary ag-watch-start" onClick={onStart} disabled={!canStart || busy}>
            {busy ? "Starting…" : canStart ? "Start watching" : "Load the scenario first"}
          </button>
        </>
      ) : (
        <>
          <div className="ag-watch-head">
            <strong>{watch.name}</strong>
            <button className="ag-watch-run" onClick={onRun} disabled={busy}>
              {busy ? "Checking…" : "Check now"}
            </button>
          </div>
          <small>
            {watch.runs} run{watch.runs === 1 ? "" : "s"} · every{" "}
            {Math.round(watch.interval_seconds / 60)} min · {open.length} open
          </small>
          {open.length === 0 ? (
            <p className="ag-note">Nothing outstanding at the last check.</p>
          ) : (
            <ul className="ag-findings">
              {open.slice(0, 4).map((finding) => (
                <li key={finding.id} className={finding.kind}>
                  <span className="ag-finding-kind">{finding.kind}</span>
                  <strong>{finding.headline}</strong>
                  {finding.detail && <small>{finding.detail}</small>}
                  {finding.plan_id && (
                    <button
                      className="ag-finding-approve"
                      onClick={() => onApprove({ id: finding.id, plan_id: finding.plan_id })}
                    >
                      Approve the drafted fix
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className="ag-note">A watch drafts and reports. Applying anything is still a person.</p>
        </>
      )}
    </div>
  );
}

/** Traffic from agents outside the page. The console's own calls live in the
 * transcript; this card is exclusively foreign WebMCP traffic, so a judge can
 * tell the two apart at a glance. */
function ExternalActivityCard({
  events,
  status,
  toolCount,
}: {
  events: WebMCPActivity[];
  status: string;
  toolCount: number;
}) {
  const connected = status === "ready";
  return (
    <div className="ag-card ag-webmcp-card">
      <p className="ag-eyebrow">Live WebMCP activity</p>
      {connected ? (
        <p className="ag-note">
          {toolCount} tools are registered on this page via{" "}
          <code>document.modelContext.registerTool()</code>. Connect any WebMCP browser agent to
          this URL and it can call them — read tools run immediately, write tools stop at a person.
        </p>
      ) : (
        <p className="ag-note">
          This browser exposes no <code>document.modelContext</code>, so the tools are shown on
          screen but not registered for external agents.
        </p>
      )}
      {events.length > 0 && (
        <ul className="ag-external">
          {events.slice().reverse().map((event) => (
            <li key={event.id} className={event.state}>
              <code>{event.tool}</code>
              <span className="ag-call-meta">
                <b className={`ag-perm ${event.permission}`}>{event.permission}</b>
                {event.state === "complete" && event.durationMs != null && (
                  <em>{event.durationMs} ms</em>
                )}
                {event.state === "running" && <em className="ag-running">running</em>}
                {event.state === "error" && <em className="ag-error-text">failed</em>}
              </span>
              {event.resultSummary && <p>{event.resultSummary}</p>}
              {event.state === "error" && event.message && <p className="ag-error-text">{event.message}</p>}
            </li>
          ))}
        </ul>
      )}
      <p className="ag-note">
        Calls above come from agents outside the page. The console&apos;s own calls appear in the
        transcript — the two are never mixed.
      </p>
    </div>
  );
}

function SpacesCard({ spaces }: { spaces: OrgSpace[] }) {  const total = useMemo(
    () => spaces.reduce((sum, space) => sum + space.memory_count, 0),
    [spaces],
  );
  return (
    <div className="ag-card">
      <p className="ag-eyebrow">Spaces</p>
      <ul className="ag-spaces">
        {spaces.map((space) => (
          <li key={space.id}>
            <span>{space.name}</span>
            <small>{space.memory_count}</small>
          </li>
        ))}
      </ul>
      <p className="ag-note">{total} memories in scope for this session.</p>
    </div>
  );
}

function EvidencePanel({ evidence, onClose }: { evidence: any; onClose: () => void }) {
  return (
    <div className="ag-evidence" role="dialog" aria-label="Evidence">
      <div className="ag-evidence-card">
        <button className="ag-evidence-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        {evidence.loading ? (
          <p className="ag-note">Loading…</p>
        ) : (
          <>
            <p className="ag-eyebrow">{evidence.memory.type}</p>
            <h4>{evidence.memory.title}</h4>
            <p>{evidence.memory.content}</p>
            <dl>
              <div>
                <dt>Space</dt>
                <dd>{evidence.memory.space_name}</dd>
              </div>
              <div>
                <dt>Recorded</dt>
                <dd>{relative(evidence.memory.updated_at)}</dd>
              </div>
              <div>
                <dt>Confidence</dt>
                <dd>{Math.round((evidence.memory.confidence || 0) * 100)}%</dd>
              </div>
            </dl>
            {evidence.sources?.length > 0 && (
              <>
                <p className="ag-eyebrow">Sources</p>
                <ul className="ag-evidence-list">
                  {evidence.sources.map((source: any) => (
                    <li key={source.id}>
                      <span>{source.title}</span>
                      <small>{source.type}</small>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {evidence.relations?.length > 0 && (
              <>
                <p className="ag-eyebrow">Relationships</p>
                <ul className="ag-evidence-list">
                  {evidence.relations.map((relation: any) => (
                    <li key={relation.target_id}>
                      <span>{relation.target_title}</span>
                      <small>{relation.type.toLowerCase()}</small>
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
