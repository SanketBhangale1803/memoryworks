"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgentAnswer, EvidencePanel, stepsFrom, type AgentState } from "@/components/AgentTurn";
import { BrandMark } from "@/components/BrandLogo";
import CommandMenu, { useCommandMenu } from "@/components/CommandMenu";
import GitHubIcon from "@/components/icons/GitHubIcon";
import MarkdownAnswer from "@/components/MarkdownAnswer";
import { NavToggle } from "@/components/WorkspaceFrame";
import { useWorkspaceTools } from "@/hooks/useWorkspaceTools";
import { API, api } from "@/lib/api";
import { orgApi, type OrgPlan } from "@/lib/orgTools";
import { newThreadId, threads, titleFrom, useThreads, type ThreadMode } from "@/lib/threads";
import type { MemoryWorksUnit } from "@/lib/webmcp";

/* The one window.
 *
 * Everything a person does after signing in starts here: ask a question and
 * get a sourced answer, or switch to Agent and let it work across every space —
 * reading, cross-checking, and proposing changes that wait for approval right
 * in the conversation. Sources, memory, and approvals are one click away in
 * the sidebar, but nobody has to visit them to get an answer. */

type Model = { id: string; label: string; company: string; configured: boolean; default: boolean };
type Project = { id: string; name: string; repository?: string };
type Source = { chunk_id: string; source_title: string; source_type: string; source_url?: string };
type Precedent = { id: string; trigger: string; successes: number; confidence: number };
type Handoff = {
  title: string;
  task: string;
  why: string;
  steps: string[];
  files: string[];
  approval_required: string[];
  prompt: string;
  /* Prior work that already solved this, carried into the agent's prompt. */
  precedents?: Precedent[];
};
type Answer = {
  answer: string;
  answer_sufficient: boolean;
  answer_scope: string;
  evidence: Source[];
  handoff?: Handoff;
  /* Present when the request could land in more than one repository. Offering
     the choice is the whole point — an agent guessing here edits the wrong code. */
  clarification?: {
    question: string;
    detail: string;
    reason?: string;
    options: { project_id?: string; label: string; files?: string[]; hint?: string }[];
  };
  /* What a follow-up like "why is it failing" was bound to. Shown so a wrong
     binding is visible and correctable rather than silently wrong. */
  resolved_subject?: string;
  /* How many connected sources were searched — enough to say "I looked and
     found nothing" without exposing how the looking works. */
  searched_sources?: number;
  /* Identifies the context this answer was built from, so what happens next can
     be attributed back to it. Never shown — it is a link, not a statistic. */
  context_event_id?: string;
  trust_score?: { level?: string };
  memory_units?: MemoryWorksUnit[];
  conflicts?: unknown[];
};
type Run = {
  id: string;
  status: string;
  branch: string;
  commit_sha: string;
  files_changed: string[];
  diff_stat: string;
  pull_request_url: string;
  error: string;
  executor: string;
};
type Turn = { question: string; mode: ThreadMode; answer?: Answer; agent?: AgentState; error?: string };

/* Terminal states stop the poller. Anything else is still in flight. */
const RUN_DONE = ["committed", "pushed", "no_changes", "failed"];

/* How many prior turns travel with a question. Only the last few carry the live
   subject of a conversation, and the server caps this too. */
const HISTORY_TURNS = 8;

const STARTERS: Record<ThreadMode, string[]> = {
  ask: [
    "What changed recently?",
    "Who owns what, and where?",
    "What should I know before editing this?",
    "Why was this decision made?",
  ],
  agent: [
    "Catch me up on what changed this week",
    "What is blocking us right now?",
    "Where do our sources disagree?",
    "Who should I talk to about each open issue?",
  ],
};

const MODE_HINT: Record<ThreadMode, string> = {
  ask: "Answers from your company’s memory, with sources.",
  agent: "Works across every space step by step. Changes wait for your approval.",
};

/* Flatten the thread into the alternating roles the server expects. Clarifying
   questions are left out: they are the assistant asking rather than telling. */
function threadHistory(turns: Turn[]) {
  const history: { role: "user" | "assistant"; content: string }[] = [];
  for (const turn of turns.slice(-HISTORY_TURNS)) {
    if (turn.question) history.push({ role: "user", content: turn.question });
    const reply =
      turn.answer && turn.answer.answer_scope !== "clarification"
        ? turn.answer.answer
        : turn.agent?.session?.answer;
    if (reply) history.push({ role: "assistant", content: reply.slice(0, 2000) });
  }
  return history;
}

export default function WorkspaceChat({ user }: { user: any }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [project, setProject] = useState("");
  const [scope, setScope] = useState<"workspace" | "project">("workspace");
  const [mode, setMode] = useState<ThreadMode>("ask");
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [menu, setMenu] = useState<"" | "mode" | "space" | "model">("");
  const [filter, setFilter] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [copied, setCopied] = useState("");
  const [evidence, setEvidence] = useState<any>(null);
  const [followups, setFollowups] = useState<string[]>([]);
  const [github, setGithub] = useState(false);
  const [importing, setImporting] = useState(0);

  const history = useThreads();
  const command = useCommandMenu();
  const composerRow = useRef<HTMLDivElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  const active = history.threads.find((item) => item.id === history.activeId);
  const turns: Turn[] = (active?.turns as Turn[]) || [];
  const isAdmin = user?.role === "owner" || user?.role === "admin";

  useEffect(() => {
    api<Project[]>("/api/projects")
      .then((items) => {
        setProjects(items);
        if (items[0]) setProject((current) => current || items[0].id);
      })
      .catch((error) => setLoadError(error.message))
      .finally(() => setLoaded(true));
    api<{ models: Model[]; default: string }>("/api/models")
      .then((catalog) => {
        const list = catalog.models || [];
        setModels(list);
        const preferred =
          list.find((item) => item.configured && item.default) ||
          list.find((item) => item.configured) ||
          list.find((item) => item.id === catalog.default) ||
          list[0];
        if (preferred) setModel(preferred.id);
      })
      .catch(() => undefined);
    api<any[]>("/api/connectors")
      .then((items) => setGithub(items.some((item) => item.provider === "github" && item.connected)))
      .catch(() => undefined);
    api<any[]>("/api/ingest/jobs")
      .then((jobs) => setImporting(jobs.filter((job) => job.status === "running" || job.status === "queued").length))
      .catch(() => undefined);
    const pendingQuestion = window.sessionStorage.getItem("memoryworks.pending-question");
    if (pendingQuestion) {
      setDraft(pendingQuestion);
      window.sessionStorage.removeItem("memoryworks.pending-question");
    }
  }, []);

  // Opening a chat restores how it was asked: its mode and its memory space.
  useEffect(() => {
    if (!active) return;
    setMode(active.mode);
    setScope(active.scope);
    if (active.projectId) setProject(active.projectId);
    setFollowups([]);
    // Only when a different chat is opened, not on every answer landing in it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id]);

  useEffect(() => {
    if (!history.activeId) setFollowups([]);
    composer.current?.focus();
  }, [history.activeId]);

  useEffect(() => {
    if (!menu) return;
    function onPointerDown(event: MouseEvent) {
      if (!composerRow.current?.contains(event.target as Node)) setMenu("");
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setMenu("");
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onEscape);
    };
  }, [menu]);

  const lastSteps = turns.at(-1)?.agent?.steps.length;
  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
  }, [turns.length, busy, lastSteps]);

  const activeModel = useMemo(() => models.find((item) => item.id === model), [models, model]);
  const activeProject = useMemo(() => projects.find((item) => item.id === project), [projects, project]);
  const visibleProjects = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return projects;
    return projects.filter(
      (item) =>
        item.name.toLowerCase().includes(needle) || (item.repository || "").toLowerCase().includes(needle),
    );
  }, [projects, filter]);

  /* Append a question to the open chat, or start one. Returns where the
     answer goes, so it lands in the right chat even if the person switches. */
  function beginTurn(question: string, turnMode: ThreadMode, targetProject: string, turnScope: "workspace" | "project") {
    const turn: Turn = { question, mode: turnMode };
    const open = threads.get().threads.find((item) => item.id === threads.get().activeId);
    if (open) {
      threads.update(open.id, (current) => ({ ...current, mode: turnMode, turns: [...current.turns, turn] }));
      return { threadId: open.id, index: open.turns.length, prior: open.turns as Turn[] };
    }
    const id = newThreadId();
    threads.save({
      id,
      title: titleFrom(question),
      projectId: targetProject,
      scope: turnScope,
      mode: turnMode,
      turns: [turn],
      updatedAt: Date.now(),
    });
    return { threadId: id, index: 0, prior: [] as Turn[] };
  }

  function patchTurn(threadId: string, index: number, patch: Partial<Turn>) {
    threads.update(threadId, (current) => ({
      ...current,
      turns: current.turns.map((turn, position) => (position === index ? { ...turn, ...patch } : turn)),
    }));
  }

  const spaceIds = useCallback(
    () => (scope === "project" && project ? [project] : projects.map((item) => item.id)),
    [scope, project, projects],
  );

  async function suggestNext(question: string, answer: string, summaries: string[]) {
    try {
      const result = await orgApi.followups(question, answer, summaries, spaceIds());
      setFollowups((result.suggestions || []).slice(0, 3));
    } catch {
      /* suggestions are a convenience; failing to draft them is invisible */
    }
  }

  async function askMemory(
    prompt: string,
    overrideProject?: string,
    surface: "web" | "webmcp" = "web",
    requestedScope?: "workspace" | "project",
  ): Promise<Answer> {
    const question = prompt.trim();
    const target = overrideProject || project;
    if (!question || !target) throw new Error("Connect a source, then ask your question.");
    if (busy) throw new Error("MemoryWorks is already answering another question.");
    const turnScope = requestedScope || (overrideProject ? "project" : scope);
    setDraft("");
    setFollowups([]);
    setBusy(true);
    const { threadId, index, prior } = beginTurn(question, "ask", target, turnScope);
    try {
      const response = await api<Answer>("/api/ask", {
        method: "POST",
        body: JSON.stringify({
          project_id: target,
          query: question,
          model: model || undefined,
          surface,
          // Choosing a repository from a clarification answers the question it
          // asked, so that turn is scoped to it rather than searched workspace-wide.
          scope: turnScope,
          history: threadHistory(prior),
        }),
      });
      patchTurn(threadId, index, { answer: response });
      if (response.answer_sufficient && !response.clarification) {
        void suggestNext(question, response.answer, (response.memory_units || []).map((unit) => unit.subject));
      }
      return response;
    } catch (error: any) {
      patchTurn(threadId, index, { error: error.message });
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function runAgent(prompt: string) {
    const question = prompt.trim();
    if (!question || busy || !projects.length) return;
    setDraft("");
    setFollowups([]);
    setBusy(true);
    const { threadId, index } = beginTurn(question, "agent", project, scope);
    patchTurn(threadId, index, { agent: { steps: [], done: false } });
    try {
      const session = await orgApi.askStream(question, spaceIds(), (partial) =>
        patchTurn(threadId, index, { agent: { steps: stepsFrom(partial), session: partial, done: false } }),
      );
      patchTurn(threadId, index, {
        agent: {
          steps: stepsFrom(session),
          session,
          plan: (session.proposal as OrgPlan | null) || null,
          done: true,
        },
      });
      void suggestNext(question, session.answer, session.steps.map((step) => step.summary));
    } catch (error: any) {
      patchTurn(threadId, index, {
        agent: { steps: [], done: true },
        error: error?.message || "The agent could not finish.",
      });
    } finally {
      setBusy(false);
    }
  }

  function send(text = draft) {
    if (!text.trim()) return;
    if (mode === "agent") void runAgent(text);
    else void askMemory(text).catch(() => undefined);
  }

  async function decidePlan(threadId: string, index: number, plan: OrgPlan, approve: boolean) {
    try {
      const decided = approve ? await orgApi.approvePlan(plan.id) : await orgApi.rejectPlan(plan.id);
      threads.update(threadId, (current) => ({
        ...current,
        turns: current.turns.map((turn: Turn, position) =>
          position === index && turn.agent ? { ...turn, agent: { ...turn.agent, plan: decided } } : turn,
        ),
      }));
    } catch (error: any) {
      patchTurn(threadId, index, { error: error.message });
    }
  }

  async function openEvidence(memoryId: string) {
    setEvidence({ loading: true });
    try {
      setEvidence(await orgApi.provenance(memoryId));
    } catch (error: any) {
      setEvidence({ error: error?.message || "That memory is no longer available." });
    }
  }

  async function copyHandoff(handoff: Handoff, key: string, contextEventId?: string) {
    try {
      await navigator.clipboard.writeText(handoff.prompt);
      setCopied(key);
      window.setTimeout(() => setCopied(""), 2200);
      // Copying a handoff is the strongest unprompted signal that an answer was
      // worth acting on, and it costs the person nothing to give.
      noteAction(contextEventId, "handoff_copied", { target: "editor" });
    } catch {
      setCopied("");
    }
  }

  useWorkspaceTools({
    spaces: projects,
    activeProjectId: project,
    isAdmin,
    ask: async (question, projectId, requestedScope) => {
      setProject(projectId);
      setScope(requestedScope);
      return (await askMemory(question, projectId, "webmcp", requestedScope)) as any;
    },
  });

  const noSources = loaded && !projects.length && !loadError;
  const placeholder = noSources
    ? "Connect a source to start asking…"
    : active
      ? "Ask a follow-up…"
      : mode === "agent"
        ? "Give the agent something to work on…"
        : "Ask anything about your company…";
  const lastTurn = turns.at(-1);
  const lastDone = Boolean(lastTurn && (lastTurn.answer || lastTurn.error || lastTurn.agent?.done));

  return (
    <div className="om-home ws-app ws-one">
      <header className="ws-bar chat-bar">
        <div className="page-bar-id">
          <NavToggle />
          <strong title={active?.title}>{active?.title || "New chat"}</strong>
        </div>
      </header>

      <main className="ws-workspace">
        <section className="ws-thread" ref={thread}>
          <div className="ws-thread-inner">
            {loadError && <div className="ws-alert">{loadError}</div>}

            {noSources && (
              <section className="ws-onboard">
                <span className="ws-orb" aria-hidden="true"><BrandMark /></span>
                <h1>{importing ? "Reading your sources…" : "Give MemoryWorks something to remember."}</h1>
                <p>
                  {importing
                    ? `${importing} import${importing === 1 ? " is" : "s are"} running. You can ask as soon as the first one finishes.`
                    : "Connect one source and you can start asking right away. Everything stays tied to where it came from."}
                </p>
                <div className="ws-onboard-grid">
                  {github ? (
                    <Link className="ws-onboard-card primary" href="/ingest?source=github">
                      <span><GitHubIcon size={20} /></span>
                      <strong>Choose repositories</strong>
                      <small>GitHub is already connected. Pick what to remember.</small>
                    </Link>
                  ) : (
                    <a className="ws-onboard-card primary" href={`${API}/api/connectors/github/auth/start`}>
                      <span><GitHubIcon size={20} /></span>
                      <strong>Connect GitHub</strong>
                      <small>Code, pull requests, issues, and who owns what.</small>
                    </a>
                  )}
                  <Link className="ws-onboard-card" href="/ingest?source=files">
                    <span aria-hidden="true">↑</span>
                    <strong>Upload documents</strong>
                    <small>PDF, Word, Slides, spreadsheets, and text.</small>
                  </Link>
                  <Link className="ws-onboard-card" href="/ingest?source=paste">
                    <span aria-hidden="true">✎</span>
                    <strong>Paste something</strong>
                    <small>A decision, a policy, or meeting notes.</small>
                  </Link>
                  <Link className="ws-onboard-card" href="/connectors">
                    <span aria-hidden="true">＋</span>
                    <strong>Slack, Drive, Notion…</strong>
                    <small>Every other source your team uses.</small>
                  </Link>
                </div>
              </section>
            )}

            {!noSources && loaded && !turns.length && (
              <section className="ws-rest">
                <span className="ws-orb" aria-hidden="true"><BrandMark /></span>
                <h1>{mode === "agent" ? "What should the agent work on?" : "What do you want to know?"}</h1>
                <p>{MODE_HINT[mode]}</p>
                <div className="ws-starters">
                  {STARTERS[mode].map((item) => (
                    <button key={item} type="button" onClick={() => send(item)}>
                      {item}
                    </button>
                  ))}
                </div>
              </section>
            )}

            {turns.map((turn, index) => (
              <article className="ws-turn" key={`${active?.id}-${index}`}>
                <div className="ws-asked">
                  {turn.mode === "agent" && <span className="ws-mode-tag">Agent</span>}
                  <p>{turn.question}</p>
                </div>

                {turn.agent && (
                  <AgentAnswer
                    state={turn.agent}
                    onEvidence={openEvidence}
                    onApprove={(plan) => decidePlan(active!.id, index, plan, true)}
                    onDecline={(plan) => decidePlan(active!.id, index, plan, false)}
                  />
                )}

                {turn.error && <div className="ws-alert">{turn.error}</div>}

                {turn.answer && (
                  <AnswerBlock
                    answer={turn.answer}
                    onCopy={copyHandoff}
                    copied={copied}
                    turnKey={`${active?.id}-${index}`}
                    project={project}
                    onPick={(projectId) => void askMemory(turn.question, projectId).catch(() => undefined)}
                  />
                )}

                {turn.mode === "ask" && !turn.answer && !turn.error && busy && index === turns.length - 1 && (
                  <div className="ws-working" role="status" aria-live="polite">
                    <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>
                    <p>Searching your company’s memory…</p>
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>
      </main>

      <footer className="ws-compose-wrap">
        <div className="ws-compose-layout">
          {lastDone && followups.length > 0 && !busy && (
            <div className="ws-followups" aria-label="Suggested follow-ups">
              {followups.map((item) => (
                <button key={item} type="button" onClick={() => send(item)}>
                  {item}
                </button>
              ))}
            </div>
          )}
          <div className="ws-compose" ref={composerRow}>
            <textarea
              ref={composer}
              rows={1}
              value={draft}
              disabled={noSources}
              placeholder={placeholder}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              aria-label="Ask MemoryWorks"
            />
            <div className="ws-compose-bar">
              <div className="ws-pick">
                <button
                  type="button"
                  className={`ws-chip ${mode === "agent" ? "agent" : ""}`}
                  aria-expanded={menu === "mode"}
                  onClick={() => setMenu(menu === "mode" ? "" : "mode")}
                >
                  {mode === "agent" ? "Agent" : "Ask"}
                  <em aria-hidden="true">▾</em>
                </button>
                {menu === "mode" && (
                  <div className="ws-menu up" role="listbox" aria-label="Mode">
                    {(["ask", "agent"] as ThreadMode[]).map((item) => (
                      <button
                        key={item}
                        type="button"
                        role="option"
                        aria-selected={mode === item}
                        className={mode === item ? "picked" : ""}
                        onClick={() => {
                          setMode(item);
                          setMenu("");
                          composer.current?.focus();
                        }}
                      >
                        <div>
                          <strong>{item === "agent" ? "Agent" : "Ask"}</strong>
                          <small>{MODE_HINT[item]}</small>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {projects.length > 1 && (
                <div className="ws-pick">
                  <button
                    type="button"
                    className="ws-chip"
                    aria-expanded={menu === "space"}
                    onClick={() => setMenu(menu === "space" ? "" : "space")}
                  >
                    {scope === "workspace" ? "All memory" : activeProject?.name || "Choose a space"}
                    <em aria-hidden="true">▾</em>
                  </button>
                  {menu === "space" && (
                    <div className="ws-menu up" role="listbox" aria-label="Memory space">
                      <div className="ws-menu-search">
                        <input
                          autoFocus
                          value={filter}
                          placeholder="Filter spaces…"
                          onChange={(event) => setFilter(event.target.value)}
                          aria-label="Filter spaces"
                        />
                      </div>
                      <button
                        type="button"
                        role="option"
                        aria-selected={scope === "workspace"}
                        className={scope === "workspace" ? "picked" : ""}
                        onClick={() => {
                          setScope("workspace");
                          setMenu("");
                          setFilter("");
                        }}
                      >
                        <div>
                          <strong>All memory</strong>
                          <small>Every connected source</small>
                        </div>
                      </button>
                      {visibleProjects.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          role="option"
                          aria-selected={scope === "project" && item.id === project}
                          className={scope === "project" && item.id === project ? "picked" : ""}
                          onClick={() => {
                            setProject(item.id);
                            setScope("project");
                            setMenu("");
                            setFilter("");
                          }}
                        >
                          <div>
                            <strong>{item.name}</strong>
                            {item.repository && <small>{item.repository}</small>}
                          </div>
                        </button>
                      ))}
                      {!visibleProjects.length && <p className="ws-menu-empty">No space matches that.</p>}
                    </div>
                  )}
                </div>
              )}

              <span className="ws-compose-spacer" />

              {mode === "ask" && models.length > 0 && (
                <div className="ws-pick">
                  <button
                    type="button"
                    className="ws-chip quiet"
                    aria-expanded={menu === "model"}
                    onClick={() => setMenu(menu === "model" ? "" : "model")}
                  >
                    {activeModel?.label || "Model"}
                    <em aria-hidden="true">▾</em>
                  </button>
                  {menu === "model" && (
                    <div className="ws-menu up right" role="listbox" aria-label="Model">
                      {models.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          role="option"
                          aria-selected={item.id === model}
                          className={`${item.configured ? "ready" : ""} ${item.id === model ? "picked" : ""}`}
                          onClick={() => {
                            setModel(item.id);
                            setMenu("");
                          }}
                        >
                          <i />
                          <div>
                            <strong>{item.label}</strong>
                            <small>{item.company}</small>
                          </div>
                          <em>{item.configured ? "Ready" : "Add key"}</em>
                        </button>
                      ))}
                      <p>
                        Add model keys in <Link href="/settings">Settings</Link>. Every model answers from
                        the same company memory.
                      </p>
                    </div>
                  )}
                </div>
              )}

              <button
                type="button"
                className="ws-send"
                onClick={() => send()}
                disabled={busy || noSources || !draft.trim()}
                aria-label="Send"
              >
                ↑
              </button>
            </div>
          </div>
        </div>
      </footer>

      <CommandMenu
        open={command.open}
        onClose={command.close}
        isAdmin={isAdmin}
        onAsk={(question) => send(question)}
      />

      {evidence && <EvidencePanel evidence={evidence} onClose={() => setEvidence(null)} />}
    </div>
  );
}

/* The completion notice. It reports what actually happened to the repository —
   branch, files, commit — because "done" without evidence is not something
   anyone should trust from an agent that edits code on its own. */
function RunCard({ run }: { run: Run }) {
  if (!RUN_DONE.includes(run.status)) {
    return (
      <div className="ws-run working">
        <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>
        <div>
          <strong>{run.executor} is making the change…</strong>
          <small>Working on a branch. Nothing touches your main branch.</small>
        </div>
      </div>
    );
  }
  if (run.status === "failed") {
    return (
      <div className="ws-run failed">
        <strong>Couldn&rsquo;t finish this one.</strong>
        <small>{run.error || "The agent stopped before making a change."}</small>
      </div>
    );
  }
  if (run.status === "no_changes") {
    return (
      <div className="ws-run">
        <strong>Nothing needed changing.</strong>
        <small>The agent read the code and decided it already does this.</small>
      </div>
    );
  }
  return (
    <div className="ws-run done">
      <strong>Done — the change is committed.</strong>
      <dl>
        <div><dt>Branch</dt><dd><code>{run.branch}</code></dd></div>
        {run.commit_sha && <div><dt>Commit</dt><dd><code>{run.commit_sha.slice(0, 10)}</code></dd></div>}
        {run.files_changed.length > 0 && (
          <div><dt>Files</dt><dd>{run.files_changed.map((file) => <code key={file}>{file}</code>)}</dd></div>
        )}
      </dl>
      {run.diff_stat && <pre>{run.diff_stat}</pre>}
      {run.pull_request_url ? (
        <a href={run.pull_request_url} target="_blank" rel="noreferrer">Open the pull request →</a>
      ) : (
        <small>Committed locally on that branch. Review it, then push when you&rsquo;re happy.</small>
      )}
    </div>
  );
}

/* Both writes are deliberately fire-and-forget. Feedback is a side effect of
   using the product; if recording it fails, the person asking should never find
   out — they got their answer either way. */
function noteAction(contextEventId: string | undefined, actionType: string, extra: Record<string, unknown> = {}) {
  if (!contextEventId) return;
  api("/api/outcomes/actions", {
    method: "POST",
    body: JSON.stringify({ context_event_id: contextEventId, action_type: actionType, surface: "web", ...extra }),
  }).catch(() => undefined);
}

function noteOutcome(contextEventId: string | undefined, outcome: "succeeded" | "failed") {
  if (!contextEventId) return;
  api("/api/outcomes/outcomes", {
    method: "POST",
    body: JSON.stringify({ context_event_id: contextEventId, outcome, signal: "human" }),
  }).catch(() => undefined);
}

/* The API answer carries a lane heading and inline [Source Title] markers for
   machines. The sources list below already shows both, so they are removed
   here rather than in the response — the payload stays traceable, the reading
   stays clean. */
function readable(answer: string, sources: Source[]) {
  const titles = new Set(sources.map((item) => item.source_title));
  return answer
    .split("\n")
    .filter((line) => !/^\*\*answer from current company memory\*\*$/i.test(line.trim()))
    .map((line) => line.replace(/\s*\[([^[\]]+)\]/g, (marker, label) => (titles.has(label) ? "" : marker)))
    .join("\n")
    .trim();
}

function AnswerBlock({
  answer,
  onCopy,
  copied,
  turnKey,
  project,
  onPick,
}: {
  answer: Answer;
  onCopy: (handoff: Handoff, key: string, contextEventId?: string) => void;
  copied: string;
  turnKey: string;
  project: string;
  onPick: (projectId: string) => void;
}) {
  const [openEvidence, setOpenEvidence] = useState(false);
  const [rated, setRated] = useState<"" | "succeeded" | "failed">("");
  const [run, setRun] = useState<Run>();
  const [runError, setRunError] = useState("");
  const sources = answer.evidence || [];
  const memories = answer.memory_units || [];

  /* The agent takes minutes, so the run is polled rather than awaited. Polling
     stops on a terminal status, and on unmount so a closed tab stops asking. */
  useEffect(() => {
    if (!run || RUN_DONE.includes(run.status)) return;
    let live = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<Run>(`/api/execute/${run.id}`);
        if (live) setRun(next);
      } catch {
        /* a transient poll failure should not kill the run display */
      }
    }, 3000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [run]);

  async function runIt() {
    setRunError("");
    try {
      setRun(
        await api<Run>("/api/execute", {
          method: "POST",
          body: JSON.stringify({ project_id: project, handoff: answer.handoff, context_event_id: answer.context_event_id }),
        }),
      );
    } catch (error: any) {
      setRunError(error.message);
    }
  }

  function rate(outcome: "succeeded" | "failed") {
    setRated(outcome);
    noteAction(answer.context_event_id, outcome === "succeeded" ? "accepted" : "rejected");
    noteOutcome(answer.context_event_id, outcome);
  }

  if (!answer.answer_sufficient) {
    /* What is true is narrow: this search came back empty. Claiming "nothing
       is connected" to someone with nineteen repositories is simply false. */
    const searched = answer.searched_sources ?? 0;
    return (
      <div className="ws-answer">
        <div className="ws-withheld">
          <strong>I couldn&rsquo;t find this in your company&rsquo;s memory.</strong>
          <p>
            {searched > 0
              ? `I searched ${searched === 1 ? "1 connected source" : `${searched} connected sources`} and found nothing that answers it. Try naming the service, repository, file, or error — or add the source that would know.`
              : "Nothing is connected yet, so there is no memory to search. Connect the source that would know, and ask again."}
          </p>
          <Link className="home-link" href="/connectors">
            Add a source <span aria-hidden="true">→</span>
          </Link>
        </div>
      </div>
    );
  }

  if (answer.clarification) {
    return (
      <div className="ws-answer">
        <div className="ws-clarify">
          <strong>{answer.clarification.question}</strong>
          <p>{answer.clarification.detail}</p>
          <div className="ws-clarify-options">
            {answer.clarification.options.map((option, index) =>
              option.project_id ? (
                <button key={option.project_id} type="button" onClick={() => onPick(option.project_id!)}>
                  <span>{option.label}</span>
                  {(option.files?.length ?? 0) > 0 && <small>{option.files!.slice(0, 2).join(", ")}</small>}
                </button>
              ) : (
                /* Only the asker knows the answer, so these are examples of the
                   shape it should take, not choices to make. */
                <div className="ws-clarify-example" key={`${option.label}-${index}`}>
                  <span>{option.label}</span>
                  {option.hint && <small>{option.hint}</small>}
                </div>
              ),
            )}
          </div>
        </div>
      </div>
    );
  }

  const conflicts = answer.conflicts?.length || 0;
  const evidenceCount = memories.length + sources.length;

  return (
    <div className="ws-answer">
      <div className="ws-reply">
        <MarkdownAnswer>{readable(answer.answer, sources)}</MarkdownAnswer>
      </div>

      {(answer.resolved_subject || answer.answer_scope === "general_knowledge" || conflicts > 0) && (
        <p className="ws-scope">
          {answer.resolved_subject && <>Answered about {answer.resolved_subject}. </>}
          {answer.answer_scope === "general_knowledge" && <>General knowledge — not from your company&rsquo;s memory. </>}
          {conflicts > 0 && (
            <>
              {conflicts} source{conflicts === 1 ? " disagrees" : "s disagree"} —{" "}
              <Link href="/conflicts">review</Link>.
            </>
          )}
        </p>
      )}

      {answer.handoff && (
        <section className="ws-handoff">
          <header>
            <span>Ready for your editor</span>
            <div className="ws-handoff-actions">
              <button type="button" className="quiet" onClick={() => onCopy(answer.handoff!, turnKey, answer.context_event_id)}>
                {copied === turnKey ? "Copied" : "Copy prompt"}
              </button>
              <button type="button" onClick={runIt} disabled={Boolean(run)}>
                {run ? "Running…" : "Do it for me"}
              </button>
            </div>
          </header>
          <strong>{answer.handoff.task}</strong>
          {answer.handoff.files.length > 0 && (
            <div className="ws-handoff-files">
              {answer.handoff.files.map((file) => <code key={file}>{file}</code>)}
            </div>
          )}
          {(answer.handoff.precedents?.length ?? 0) > 0 && (
            <div className="ws-precedent">
              <span>This has been done before</span>
              <ul>
                {answer.handoff.precedents!.map((item) => (
                  <li key={item.id}>
                    {item.trigger}
                    <small>worked {item.successes}×</small>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p>Paste into Cursor, Copilot, or Claude Code. It carries only the context this task needs.</p>
          {answer.handoff.approval_required.length > 0 && <small>Needs your approval before anything is applied.</small>}
          {runError && <p className="ws-run-error">{runError}</p>}
          {run && <RunCard run={run} />}
        </section>
      )}

      <div className="ws-meta">
        {evidenceCount > 0 && (
          <button type="button" className="ws-meta-toggle" aria-expanded={openEvidence} onClick={() => setOpenEvidence((open) => !open)}>
            {sources.length
              ? `${sources.length} source${sources.length === 1 ? "" : "s"}`
              : `${memories.length} memor${memories.length === 1 ? "y" : "ies"}`}
            <em aria-hidden="true">{openEvidence ? "▴" : "▾"}</em>
          </button>
        )}
        {answer.trust_score?.level && <span>{answer.trust_score.level.replace(/_/g, " ")} confidence</span>}
        {answer.context_event_id && (
          <span className="ws-rate">
            {rated ? (
              rated === "succeeded" ? "Thanks — noted." : "Noted. I’ll weigh this differently next time."
            ) : (
              <>
                Did this work?
                <button type="button" onClick={() => rate("succeeded")}>Yes</button>
                <button type="button" onClick={() => rate("failed")}>No</button>
              </>
            )}
          </span>
        )}
      </div>

      {openEvidence && (
        <div className="ws-evidence-list">
          {memories.length > 0 && (
            <ul>
              {memories.slice(0, 6).map((memory) => (
                <li key={memory.id}>
                  <small>{memory.type}</small>
                  <span>{memory.content}</span>
                </li>
              ))}
            </ul>
          )}
          {sources.length > 0 && (
            <ul>
              {sources.map((item) => (
                <li key={item.chunk_id}>
                  <small>{item.source_type.replace(/_/g, " ")}</small>
                  {item.source_url ? (
                    <a href={item.source_url} target="_blank" rel="noreferrer">{item.source_title}</a>
                  ) : (
                    <span>{item.source_title}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
