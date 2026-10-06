import { api, API, AnswerStopped, cancelStream } from "@/lib/api";
import {
  demoAgentSession,
  demoFollowups,
  demoOrgRequest,
  OFFLINE_DEMO_MODE,
} from "@/lib/demoMemory";

/**
 * Organizational operations, as tools.
 *
 * Every entry here is one call an agent can make against the signed-in
 * workspace, and the exact same handler the page registers with WebMCP. The
 * console on /webmcp does not simulate these — it invokes this map, so what a
 * judge watches on screen is the traffic a browser agent would produce.
 *
 * Reads run immediately. Writes never apply: they enqueue a plan and stop,
 * because capability and authorization are not the same thing.
 */

export type OrgToolKind = "read" | "gated-write";

export type OrgToolResult = {
  summary: string;
  data: Record<string, unknown>;
};

export type OrgTool = {
  name: string;
  title: string;
  kind: OrgToolKind;
  description: string;
  /** What this call replaces when a person does it by hand. */
  manualEquivalent: string;
  inputSchema: Record<string, unknown>;
  run: (input: Record<string, any>) => Promise<OrgToolResult>;
};

/* ------------------------------------------------------------------ types */

export type OrgSpace = {
  id: string;
  name: string;
  repository?: string;
  memory_count: number;
  memory_types: Record<string, number>;
  open_tasks: number;
  updated_at?: string;
};

export type MemoryWorksRecord = {
  id: string;
  space_id: string;
  space_name: string;
  type: string;
  title: string;
  content: string;
  confidence?: number;
  source_ids?: string[];
  created_at?: string;
  updated_at?: string;
  change?: string;
  age_days?: number;
  decision_status?: string;
};

export type OrgTask = {
  id: string;
  space_id: string;
  space_name: string;
  title: string;
  description: string;
  status: string;
  owner: string;
  priority: string;
  kind: string;
  depends_on: string[];
  source_memory_ids: string[];
  updated_at?: string;
};

export type OrgBlocker = {
  task: OrgTask;
  blocks: OrgTask[];
  chain: { id: string; label: string; status: string; space_name: string }[];
  severity: string;
  evidence: string[];
};

export type OrgConflict = {
  id: string;
  task: OrgTask;
  tracked_state: string;
  tracked_source: MemoryWorksRecord;
  recorded_state: string;
  recorded_at: string;
  basis: string;
  source: MemoryWorksRecord;
  matched_terms: string[];
  resolution: Record<string, unknown>;
};

export type OrgChainStep = {
  position: number;
  role: string;
  memory: MemoryWorksRecord;
};

export type OrgReasoningChain = {
  topic: string;
  steps: OrgChainStep[];
  edges: { from: string; to: string; type: string }[];
};

export type OrgChecklistItem = {
  id: string;
  label: string;
  kind: string;
  space_id: string;
  space_name: string;
  owner: string;
  state: "done" | "ready" | "open" | "blocked";
  status: string;
  blocked_by: { id: string; label: string }[];
  evidence: string[];
};

export type OrgReadiness = {
  ready: boolean;
  status: string;
  goal: { id: string; label: string } | null;
  blocker_count: number;
  outstanding: string[];
  checklist: OrgChecklistItem[];
  blockers: OrgBlocker[];
};

export type OrgPlanOperation = Record<string, unknown> & { op: string; preview?: string };

export type OrgPlan = {
  id: string;
  space_id: string;
  summary: string;
  status: "pending_approval" | "approved" | "denied";
  operations: OrgPlanOperation[];
  results: { op: string; ok: boolean; error?: string }[];
  created_at?: string;
};

export type OrgWatchFinding = {
  id: string;
  kind: "blocker" | "conflict" | "stale";
  headline: string;
  detail: string;
  plan_id: string;
  status: string;
  created_at?: string;
};

export type OrgWatch = {
  id: string;
  name: string;
  space_ids: string[];
  checks: string[];
  interval_seconds: number;
  status: string;
  runs: number;
  last_run_at?: string;
  last_error?: string;
  open_findings: number;
  findings: OrgWatchFinding[];
};

export type OrgAgentStep = {
  tool: string;
  arguments: Record<string, unknown>;
  summary: string;
  thought?: string;
  duration_ms?: number;
};

export type OrgAgentSession = {
  id: string;
  question: string;
  model: string;
  status: "running" | "complete" | "error" | "cancelled";
  /** "model" when a model chose the tools; "guided" when none was reachable. */
  mode?: "model" | "guided";
  steps: OrgAgentStep[];
  answer: string;
  memory_ids: string[];
  /** A plan the agent proposed; it never applies itself. */
  proposal?: Record<string, unknown> | null;
  error: string;
};

export type OrgProjectContext = {
  spaces: OrgSpace[];
  memory_count: number;
  decisions: MemoryWorksRecord[];
  open_tasks: OrgTask[];
  unresolved: MemoryWorksRecord[];
  recent_changes: MemoryWorksRecord[];
  blockers: OrgBlocker[];
  next_best_action: { action: string; why: string; task_id: string; owner: string };
};

/* -------------------------------------------------------------- transport */

function query(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, Array.isArray(value) ? value.join(",") : String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

const get = <T,>(path: string, params: Record<string, unknown> = {}) =>
  OFFLINE_DEMO_MODE
    ? demoOrgRequest<T>("GET", path, params)
    : api<T>(`/api/org${path}${query(params)}`);

const post = <T,>(path: string, body: unknown) =>
  OFFLINE_DEMO_MODE
    ? demoOrgRequest<T>("POST", path, {}, body)
    : api<T>(`/api/org${path}`, { method: "POST", body: JSON.stringify(body) });

function plural(count: number, one: string, many = `${one}s`) {
  return `${count} ${count === 1 ? one : many}`;
}

/* ------------------------------------------------------------ direct calls */

export const orgApi = {
  spaces: () => get<{ count: number; spaces: OrgSpace[] }>("/spaces"),
  space: (spaceId: string) => get<OrgSpace & { recent_memories: MemoryWorksRecord[] }>(`/spaces/${spaceId}`),
  context: (spaceIds?: string[]) =>
    get<OrgProjectContext>("/context", { space_ids: spaceIds }),
  search: (queryText: string, spaceIds?: string[], memoryType = "", limit = 10) =>
    get<{ count: number; results: MemoryWorksRecord[] }>("/search", {
      query: queryText,
      space_ids: spaceIds,
      memory_type: memoryType,
      limit,
    }),
  recentChanges: (since = "", spaceIds?: string[]) =>
    get<{ count: number; changes: MemoryWorksRecord[] }>("/recent-changes", {
      since,
      space_ids: spaceIds,
    }),
  decisions: (spaceIds?: string[], status = "") =>
    get<{ count: number; decisions: MemoryWorksRecord[] }>("/decisions", {
      space_ids: spaceIds,
      status,
    }),
  tasks: (spaceIds?: string[], assignee = "", priority = "", status = "") =>
    get<{ count: number; tasks: OrgTask[] }>("/tasks", {
      space_ids: spaceIds,
      assignee,
      priority,
      status,
    }),
  taskDependencies: (taskId: string) =>
    get<{ task: OrgTask; requires: OrgTask[]; required_by: OrgTask[] }>(
      `/tasks/${taskId}/dependencies`,
    ),
  people: (queryText = "", personId = "", spaceIds?: string[]) =>
    get<{ count: number; people: any[] }>("/people", {
      query: queryText,
      person_id: personId,
      space_ids: spaceIds,
    }),
  owner: (objectId: string) => get<any>(`/owner/${objectId}`),
  provenance: (memoryId: string) => get<any>(`/provenance/${memoryId}`),
  reasoningChain: (topic: string, spaceIds?: string[]) =>
    get<OrgReasoningChain>("/reasoning-chain", { topic, space_ids: spaceIds }),
  dependencyGraph: (spaceIds?: string[]) =>
    get<{ node_count: number; edge_count: number; nodes: any[]; edges: any[] }>(
      "/dependency-graph",
      { space_ids: spaceIds },
    ),
  blockers: (spaceIds?: string[]) =>
    get<{ count: number; blockers: OrgBlocker[] }>("/blockers", { space_ids: spaceIds }),
  conflicts: (spaceIds?: string[], topic = "") =>
    get<{ count: number; conflicts: OrgConflict[] }>("/conflicts", {
      space_ids: spaceIds,
      topic,
    }),
  stale: (topic = "", maxAgeDays = 90, spaceIds?: string[]) =>
    get<{ count: number; stale: MemoryWorksRecord[] }>("/stale", {
      topic,
      max_age_days: maxAgeDays,
      space_ids: spaceIds,
    }),
  readiness: (spaceIds?: string[]) => get<OrgReadiness>("/readiness", { space_ids: spaceIds }),
  proposePlan: (summary: string, operations: Record<string, unknown>[], spaceId = "") =>
    post<OrgPlan>("/plans", { summary, operations, space_id: spaceId }),
  plans: (status = "") => get<{ count: number; plans: OrgPlan[] }>("/plans", { status }),
  approvePlan: (planId: string) => post<OrgPlan>(`/plans/${planId}/approve`, {}),
  rejectPlan: (planId: string) => post<OrgPlan>(`/plans/${planId}/reject`, {}),
  seedScenario: (reset = false) => post<any>("/scenario/seed", { reset }),
  ask: (question: string, spaceIds: string[]) =>
    post<OrgAgentSession>("/ask", { question, space_ids: spaceIds }),
  askStatus: (runId: string) => get<OrgAgentSession>(`/ask/${runId}`),
  /** The next questions, drafted from what the last turn actually found. */
  followups: (
    question: string,
    answer: string,
    summaries: string[],
    spaceIds: string[],
  ) =>
    OFFLINE_DEMO_MODE
      ? demoFollowups(question, summaries)
      : post<{ suggestions: string[]; source: string }>("/followups", {
          question,
          answer,
          summaries,
          space_ids: spaceIds,
        }),
  /** Free-text question, answered by a model driving the tool surface.
   *
   * Streams one NDJSON request so the run, its tool calls, and its trace stay
   * on one server instance (the session registry is process-local), and the
   * console can render each step the moment it lands. Falls back to the
   * create-then-poll transport when the stream is unavailable, and to the
   * guided offline agent when there is no backend at all. Aborting `signal`
   * stops the run on the server too and rejects with AnswerStopped. */
  askStream: async (
    question: string,
    spaceIds: string[],
    onSession: (session: OrgAgentSession) => void,
    signal?: AbortSignal,
  ): Promise<OrgAgentSession> => {
    const publish = (session: OrgAgentSession) => onSession({ ...session, steps: [...session.steps] });
    if (OFFLINE_DEMO_MODE) {
      return (await demoAgentSession(question, spaceIds, publish as (session: any) => void)) as OrgAgentSession;
    }
    const state: { session: OrgAgentSession | null } = { session: null };
    // An Agent run outlives a dropped connection on purpose, so Stop must say so.
    const stop = () => {
      if (state.session?.id) cancelStream(`/api/org/ask/${encodeURIComponent(state.session.id)}/cancel`);
    };
    signal?.addEventListener("abort", stop, { once: true });
    let response: Response;
    try {
      response = await fetch(`${API}/api/org/ask/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        cache: "no-store",
        body: JSON.stringify({ question, space_ids: spaceIds }),
        signal,
      });
    } catch {
      if (signal?.aborted) throw new AnswerStopped();
      throw new Error(
        `Cannot reach the MemoryWorks API at ${API}. Check that the backend is running and refresh the page.`,
      );
    }
    /* Follow a run by polling its stored session until it finishes. */
    const follow = async (first: OrgAgentSession) => {
      let session = first;
      publish(session);
      const deadline = Date.now() + 240000;
      while (session.status === "running" && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (signal?.aborted) throw new AnswerStopped();
        session = await orgApi.askStatus(session.id);
        publish(session);
      }
      if (session.status === "cancelled") throw new AnswerStopped();
      if (session.status === "error") throw new Error(session.error || "The agent could not finish.");
      if (session.status === "running") throw new Error("The agent is taking unusually long. Check back in a minute.");
      return session;
    };
    if (!response.ok || !response.body) {
      return follow(await post<OrgAgentSession>("/ask", { question, space_ids: spaceIds }));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const apply = (next: OrgAgentSession) => {
      state.session = next;
      publish(next);
    };
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch {
        if (signal?.aborted) throw new AnswerStopped();
        /* The connection dropped mid-run ("network error"). The run keeps going
           on the server and stores every step, so pick it up from there rather
           than losing the answer. */
        if (state.session?.id) return follow(state.session);
        throw new Error("The connection to MemoryWorks dropped before the agent started. Try again.");
      }
      const { done, value } = chunk;
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
        if (!line) continue;
        let event: {
          type: string;
          id?: string;
          model?: string;
          step?: OrgAgentStep;
          session?: OrgAgentSession;
          message?: string;
        };
        try {
          event = JSON.parse(line);
        } catch {
          continue;
        }
        if (event.type === "start") {
          apply({
            id: event.id || "",
            question,
            model: event.model || "",
            status: "running",
            steps: [],
            answer: "",
            memory_ids: [],
            error: "",
          });
          // Stop was pressed before the server said which run this is.
          if (signal?.aborted) stop();
        } else if (event.type === "cancelled") {
          throw new AnswerStopped();
        } else if (event.type === "step" && event.step) {
          const previous = state.session;
          apply({
            ...(previous || {
              id: event.id || "",
              question,
              model: "",
              status: "running",
              steps: [],
              answer: "",
              memory_ids: [],
              error: "",
            }),
            status: "running",
            steps: [...(previous?.steps || []), event.step],
          });
        } else if (event.type === "done" && event.session) {
          // Guard the live trace: the final session should carry every step,
          // but never let a sparse terminal payload wipe what was streamed.
          const streamed = state.session?.steps || [];
          apply(
            event.session.steps?.length
              ? event.session
              : { ...event.session, steps: streamed },
          );
        } else if (event.type === "error") {
          throw new Error(event.message || "The agent could not finish.");
        }
      }
    }
    if (!state.session) throw new Error("The agent stream ended without a result.");
    // A stream that closed without its final event was cut off; the stored run has the rest.
    if (signal?.aborted) throw new AnswerStopped();
    if (state.session.status === "running" && state.session.id) return follow(state.session);
    return state.session;
  },
  watches: () => get<{ count: number; watches: OrgWatch[] }>("/watches"),
  createWatch: (name: string, spaceIds: string[], checks: string[], intervalSeconds = 900) =>
    post<OrgWatch>("/watches", {
      name,
      space_ids: spaceIds,
      checks,
      interval_seconds: intervalSeconds,
    }),
  runWatch: (watchId: string) => post<OrgWatch & { new_findings: number }>(`/watches/${watchId}/run`, {}),
  resolveFinding: (watchId: string, findingId: string) =>
    post<{ id: string; status: string }>(`/watches/${watchId}/findings/${findingId}/resolve`, {}),
  deleteWatch: (watchId: string) =>
    OFFLINE_DEMO_MODE
      ? demoOrgRequest("DELETE", `/watches/${watchId}`)
      : api(`/api/org/watches/${watchId}`, { method: "DELETE" }),
};

/* --------------------------------------------------------------- schemas */

const spaceFilter = {
  space_ids: {
    type: "array",
    items: { type: "string" },
    description: "Restrict to these space ids. Omit to cover every authorized space.",
  },
};

/* ----------------------------------------------------------------- tools */

export const ORG_TOOLS: Record<string, OrgTool> = {
  get_orgmemory_project_context: {
    name: "get_orgmemory_project_context",
    title: "Reconstruct project context",
    kind: "read",
    description:
      "Assemble the current state of a project across every authorized space: what is being worked on, what was decided, what is open, what changed, and what is blocked. Use this first when someone needs to be caught up.",
    manualEquivalent: "Opening every space and reading back through it",
    inputSchema: { type: "object", properties: { ...spaceFilter }, additionalProperties: false },
    run: async (input) => {
      const data = await orgApi.context(input.space_ids);
      return {
        summary: `${data.memory_count} memories across ${plural(data.spaces.length, "space")}: ${plural(data.decisions.length, "decision")}, ${plural(data.open_tasks.length, "open item")}, ${plural(data.blockers.length, "blocker")}.`,
        data: data as unknown as Record<string, unknown>,
      };
    },
  },

  get_orgmemory_recent_changes: {
    name: "get_orgmemory_recent_changes",
    title: "What changed recently",
    kind: "read",
    description:
      "List memories created or revised since a timestamp, newest first, across the authorized spaces.",
    manualEquivalent: "Scrolling every channel and doc for the last week",
    inputSchema: {
      type: "object",
      properties: {
        since: { type: "string", description: "ISO 8601 timestamp. Defaults to seven days ago." },
        ...spaceFilter,
      },
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.recentChanges(input.since || "", input.space_ids);
      return { summary: `${plural(data.count, "change")} on record.`, data: data as any };
    },
  },

  get_orgmemory_tasks: {
    name: "get_orgmemory_tasks",
    title: "List open work",
    kind: "read",
    description:
      "List tasks that are still open, optionally filtered by owner, priority, or status.",
    manualEquivalent: "Checking the tracker in each team's own board",
    inputSchema: {
      type: "object",
      properties: {
        assignee: { type: "string" },
        priority: { type: "string", enum: ["low", "normal", "high", "critical"] },
        status: { type: "string", enum: ["open", "in_progress", "blocked", "done", "cancelled"] },
        ...spaceFilter,
      },
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.tasks(
        input.space_ids,
        input.assignee || "",
        input.priority || "",
        input.status || "",
      );
      return { summary: `${plural(data.count, "open item")}.`, data: data as any };
    },
  },

  get_orgmemory_task_dependencies: {
    name: "get_orgmemory_task_dependencies",
    title: "Trace one task's dependencies",
    kind: "read",
    description: "Return what a task requires and what is waiting on it.",
    manualEquivalent: "Asking three teams what they are waiting for",
    inputSchema: {
      type: "object",
      properties: { task_id: { type: "string" } },
      required: ["task_id"],
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.taskDependencies(input.task_id);
      return {
        summary: `${plural(data.requires.length, "prerequisite")}, ${plural(data.required_by.length, "dependent item")}.`,
        data: data as any,
      };
    },
  },

  get_orgmemory_dependency_graph: {
    name: "get_orgmemory_dependency_graph",
    title: "Read the dependency graph",
    kind: "read",
    description:
      "Return the full graph of work items and the REQUIRED_FOR edges between them, across spaces.",
    manualEquivalent: "Redrawing the dependency chain on a whiteboard",
    inputSchema: { type: "object", properties: { ...spaceFilter }, additionalProperties: false },
    run: async (input) => {
      const data = await orgApi.dependencyGraph(input.space_ids);
      return {
        summary: `${plural(data.node_count, "work item")}, ${plural(data.edge_count, "dependency", "dependencies")}.`,
        data: data as any,
      };
    },
  },

  find_orgmemory_blockers: {
    name: "find_orgmemory_blockers",
    title: "Find what is actually blocking",
    kind: "read",
    description:
      "Return only the root causes of a stall: unfinished items that other work depends on and whose own prerequisites are already met. Downstream items appear in the chain rather than as separate blockers.",
    manualEquivalent: "Working backwards from the deadline through five teams",
    inputSchema: { type: "object", properties: { ...spaceFilter }, additionalProperties: false },
    run: async (input) => {
      const data = await orgApi.blockers(input.space_ids);
      const top = data.blockers[0];
      return {
        summary: data.count
          ? `${plural(data.count, "blocker")} — ${top.task.title} (${top.severity}), holding ${plural(top.blocks.length, "item")}.`
          : "Nothing is blocking.",
        data: data as any,
      };
    },
  },

  find_orgmemory_conflicts: {
    name: "find_orgmemory_conflicts",
    title: "Find contradicting records",
    kind: "read",
    description:
      "Find work items whose tracked state disagrees with the organization's own newer record — a review still marked open after a meeting already approved it, for example. Anchored to the evidence each item cites, not to keyword similarity.",
    manualEquivalent: "Remembering that a meeting happened and checking the notes",
    inputSchema: {
      type: "object",
      properties: { topic: { type: "string" }, ...spaceFilter },
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.conflicts(input.space_ids, input.topic || "");
      const top = data.conflicts[0];
      return {
        summary: data.count
          ? `${plural(data.count, "conflict")} — “${top.task.title}” is ${top.tracked_state}, but ${top.source.space_name} already settled it.`
          : "No contradictions found.",
        data: data as any,
      };
    },
  },

  find_orgmemory_stale: {
    name: "find_orgmemory_stale",
    title: "Find information that has aged out",
    kind: "read",
    description: "Return memories older than a threshold that nothing newer has superseded.",
    manualEquivalent: "Noticing that a doc has not been touched since last quarter",
    inputSchema: {
      type: "object",
      properties: {
        topic: { type: "string" },
        max_age_days: { type: "integer", minimum: 1, maximum: 3650 },
        ...spaceFilter,
      },
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.stale(input.topic || "", input.max_age_days || 90, input.space_ids);
      return { summary: `${plural(data.count, "aging record")}.`, data: data as any };
    },
  },

  get_orgmemory_readiness: {
    name: "get_orgmemory_readiness",
    title: "Compute launch readiness",
    kind: "read",
    description:
      "Compute the launch checklist from the dependency graph rather than reading a maintained one. Each item resolves to done, ready, open, or blocked, and readiness is judged over the goal's dependency closure.",
    manualEquivalent: "Asking every team “are we good?” and hoping",
    inputSchema: { type: "object", properties: { ...spaceFilter }, additionalProperties: false },
    run: async (input) => {
      const data = await orgApi.readiness(input.space_ids);
      return {
        summary: `${data.status} — ${plural(data.blocker_count, "blocker")}, ${plural(data.checklist.length, "checklist item")}.`,
        data: data as any,
      };
    },
  },

  get_orgmemory_reasoning_chain: {
    name: "get_orgmemory_reasoning_chain",
    title: "Reconstruct why",
    kind: "read",
    description:
      "Return the ordered chain of records that produced a decision — the requirement, the dependency it created, the decision, and the work that followed — by walking recorded relationships rather than ranking by similarity.",
    manualEquivalent: "Asking whoever has been here longest",
    inputSchema: {
      type: "object",
      properties: { topic: { type: "string", minLength: 3 }, ...spaceFilter },
      required: ["topic"],
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.reasoningChain(input.topic, input.space_ids);
      return {
        summary: `${plural(data.steps.length, "step")} of recorded reasoning, ${plural(data.edges.length, "link")}.`,
        data: data as any,
      };
    },
  },

  get_orgmemory_provenance: {
    name: "get_orgmemory_provenance",
    title: "Open a memory's evidence",
    kind: "read",
    description:
      "Return the sources behind one memory, the relationships it holds with other memories, and the work derived from it.",
    manualEquivalent: "Hunting for the original thread",
    inputSchema: {
      type: "object",
      properties: { memory_id: { type: "string" } },
      required: ["memory_id"],
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.provenance(input.memory_id);
      return {
        summary: `${plural(data.sources.length, "source")}, ${plural(data.relations.length, "relationship")}.`,
        data,
      };
    },
  },

  get_orgmemory_people: {
    name: "get_orgmemory_people",
    title: "Who owns what",
    kind: "read",
    description:
      "Return the people in this organization, what they own according to memory, and the open work assigned to them.",
    manualEquivalent: "Asking around for the right owner",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" }, person_id: { type: "string" }, ...spaceFilter },
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.people(input.query || "", input.person_id || "", input.space_ids);
      return { summary: `${plural(data.count, "person", "people")}.`, data: data as any };
    },
  },

  get_orgmemory_owner: {
    name: "get_orgmemory_owner",
    title: "Resolve an owner",
    kind: "read",
    description: "Return the recorded owner of one task or memory, with the evidence for it.",
    manualEquivalent: "Guessing from a commit history",
    inputSchema: {
      type: "object",
      properties: { object_id: { type: "string" } },
      required: ["object_id"],
      additionalProperties: false,
    },
    run: async (input) => {
      const data = await orgApi.owner(input.object_id);
      return { summary: data.owner ? `Owner: ${data.owner}.` : "No recorded owner.", data };
    },
  },

  /* ---------------------------------------------------------- gated writes */

  propose_orgmemory_changes: {
    name: "propose_orgmemory_changes",
    title: "Propose changes for approval",
    kind: "gated-write",
    description:
      "Submit a set of changes to organizational state for human approval. Nothing is applied by this call. There is deliberately no tool that approves a plan — only a person in the workspace can do that.",
    manualEquivalent: "Updating four systems by hand and hoping you got them all",
    inputSchema: {
      type: "object",
      properties: {
        summary: { type: "string", description: "What this set of changes accomplishes." },
        space_id: { type: "string" },
        conflict_id: {
          type: "string",
          description:
            "Reconcile a conflict from find_orgmemory_conflicts by reference: its recorded resolution is copied verbatim, never retyped.",
        },
        operations: {
          type: "array",
          minItems: 1,
          items: {
            type: "object",
            properties: {
              op: { type: "string", enum: ["create_task", "update_task", "add_memory"] },
              task_id: { type: "string" },
              space_id: { type: "string" },
              title: { type: "string" },
              description: { type: "string" },
              content: { type: "string" },
              type: { type: "string" },
              status: { type: "string" },
              owner: { type: "string" },
              priority: { type: "string" },
              reason: { type: "string" },
              source_memory_ids: { type: "array", items: { type: "string" } },
            },
            required: ["op"],
          },
        },
      },
      required: ["summary"],
      additionalProperties: false,
    },
    run: async (input) => {
      let summary = input.summary;
      let operations = input.operations;
      let spaceId = input.space_id || "";
      if (input.conflict_id) {
        // Reconciliation by reference: the exact resolution the system
        // computed, not a transcription of it.
        const { conflicts } = await orgApi.conflicts(input.space_ids || undefined);
        const conflict = conflicts.find((item) => item.id === input.conflict_id);
        if (!conflict) {
          throw new Error(`No open conflict ${input.conflict_id} in the authorized spaces.`);
        }
        operations = [conflict.resolution as Record<string, unknown>];
        spaceId = spaceId || conflict.task.space_id;
        summary = summary || `Reconcile “${conflict.task.title}” with the record that settled it`;
      }
      const plan = await orgApi.proposePlan(summary, operations || [], spaceId);
      return {
        summary: `${plural(plan.operations.length, "change")} proposed and waiting for a person. Nothing applied.`,
        data: plan as any,
      };
    },
  },

  create_orgmemory_task: {
    name: "create_orgmemory_task",
    title: "Propose a new task",
    kind: "gated-write",
    description:
      "Propose creating a task, attached to the memories that justify it. Waits for human approval.",
    manualEquivalent: "Filing a ticket and pasting in the context",
    inputSchema: {
      type: "object",
      properties: {
        space_id: { type: "string" },
        title: { type: "string" },
        description: { type: "string" },
        owner: { type: "string" },
        priority: { type: "string", enum: ["low", "normal", "high", "critical"] },
        source_memory_ids: { type: "array", items: { type: "string" } },
      },
      required: ["space_id", "title"],
      additionalProperties: false,
    },
    run: async (input) => {
      const plan = await orgApi.proposePlan(
        `Create task: ${input.title}`,
        [{ ...input, op: "create_task" }],
        input.space_id,
      );
      return { summary: "Task proposed. Waiting for approval.", data: plan as any };
    },
  },

  update_orgmemory_task: {
    name: "update_orgmemory_task",
    title: "Propose a task update",
    kind: "gated-write",
    description:
      "Propose changing a task's status, owner, or priority, citing the memories that justify it. Waits for human approval.",
    manualEquivalent: "Editing the tracker and telling the team in a thread",
    inputSchema: {
      type: "object",
      properties: {
        task_id: { type: "string" },
        status: { type: "string", enum: ["open", "in_progress", "blocked", "done", "cancelled"] },
        owner: { type: "string" },
        priority: { type: "string", enum: ["low", "normal", "high", "critical"] },
        reason: { type: "string" },
        source_memory_ids: { type: "array", items: { type: "string" } },
      },
      required: ["task_id"],
      additionalProperties: false,
    },
    run: async (input) => {
      const plan = await orgApi.proposePlan(
        input.reason || "Update a tracked work item",
        [{ ...input, op: "update_task" }],
      );
      return { summary: "Update proposed. Waiting for approval.", data: plan as any };
    },
  },
};

export const ORG_TOOL_NAMES = Object.keys(ORG_TOOLS);
export const ORG_READ_TOOLS = ORG_TOOL_NAMES.filter((name) => ORG_TOOLS[name].kind === "read");
export const ORG_WRITE_TOOLS = ORG_TOOL_NAMES.filter(
  (name) => ORG_TOOLS[name].kind === "gated-write",
);
