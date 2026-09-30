import { ORG_TOOLS } from "@/lib/orgTools";

export const MEMORYWORKS_TOOLS = [
  "list_orgmemory_spaces",
  "ask_orgmemory",
  "get_orgmemory_briefing",
  "record_orgmemory_outcome",
  "inspect_orgmemory_changes",
  "search_orgmemory",
  "get_orgmemory_memory",
  "get_orgmemory_related_memories",
  "get_orgmemory_incidents",
  "get_orgmemory_service_context",
  "get_orgmemory_dependencies",
  "get_orgmemory_decisions",
  "propose_repository_refresh",
  "propose_orgmemory_memory",
  "propose_orgmemory_incident",
  "propose_orgmemory_decision",
  "list_orgmemory_approvals",
  "resolve_orgmemory_approval",
  "list_orgmemory_proposals",
  "resolve_orgmemory_proposal",
  /* Organizational operations: the cross-space reads an agent needs to
     reconstruct a project, trace reasoning, and find what is actually blocking,
     plus the write path that always stops at a person. */
  "get_orgmemory_project_context",
  "get_orgmemory_recent_changes",
  "get_orgmemory_tasks",
  "get_orgmemory_task_dependencies",
  "get_orgmemory_dependency_graph",
  "find_orgmemory_blockers",
  "find_orgmemory_conflicts",
  "find_orgmemory_stale",
  "get_orgmemory_readiness",
  "get_orgmemory_reasoning_chain",
  "get_orgmemory_provenance",
  "get_orgmemory_people",
  "get_orgmemory_owner",
  "propose_orgmemory_changes",
  "create_orgmemory_task",
  "update_orgmemory_task",
] as const;

export type WebMCPToolName = (typeof MEMORYWORKS_TOOLS)[number];

/* Closed vocabulary, mirroring the backend ledger. Reward is derived from these,
   so an agent inventing a sixth value would quietly corrupt the corpus. */
export const ORGMEMORY_OUTCOMES = [
  "succeeded",
  "failed",
  "partial",
  "abandoned",
  "unknown",
] as const;

/* The memory kinds an agent may propose. Mirrors the backend's propable set;
   anything else is rejected server-side as well. */
export const ORGMEMORY_PROPOSABLE_KINDS = [
  "fact",
  "decision",
  "incident",
  "procedure",
  "policy",
  "convention",
  "config",
  "ownership",
  "dependency",
  "preference",
  "open_question",
] as const;

export type MemoryWorksSpace = {
  id: string;
  name: string;
  repository?: string;
};

export type MemoryWorksEvidence = {
  source_title: string;
  source_type: string;
  source_url?: string;
};

export type MemoryWorksAnswer = {
  answer: string;
  answer_sufficient: boolean;
  answer_scope: string;
  resolved_subject?: string;
  searched_sources?: number;
  evidence: MemoryWorksEvidence[];
  likely_cause?: string;
  confidence?: number;
  trust_score?: {
    level?: string;
    reason?: string;
    contradictions?: unknown[];
  };
  memory_units?: MemoryWorksUnit[];
  related_entities?: string[];
  updates?: unknown[];
  conflicts?: unknown[];
  safe_actions?: string[];
  approval_required?: string[];
  retrieval_trace?: {
    engine?: string;
    graph_paths?: unknown[];
    source_projects?: string[];
    scope_mode?: string;
    context_selection_policy?: string;
    security_trimmed?: boolean;
  };
  context_envelope?: {
    id?: string;
    memory_ids?: string[];
    evidence_ids?: string[];
  };
};

/* One cited memory inside a pre-action briefing. `why_it_matters` is written by
   the server rather than the agent: the point of a briefing is that the reason a
   record is in front of you is not the reader's guess. */
export type MemoryWorksBriefingCitation = {
  memory_id: string;
  type: string;
  subject: string;
  content: string;
  service?: string | null;
  project_id?: string;
  project_name?: string | null;
  confidence?: number;
  sources?: number;
  updated_at?: string;
  why_it_matters: string;
};

export type MemoryWorksBriefingPrecedent = {
  skill_id?: string;
  name?: string;
  trigger?: string;
  steps?: string[];
  successes?: number;
  confidence?: number;
};

export type MemoryWorksBriefing = {
  briefing_id?: string | null;
  task: string;
  service?: string | null;
  project_id?: string | null;
  /* "no_memory" is a real answer and never collapsed into "proceed": an agent
     must be able to tell "nothing constrains this" from "nothing is known". */
  verdict: "no_memory" | "proceed" | "proceed_with_context" | "requires_approval";
  headline: string;
  consequential_action?: string | null;
  must_read: MemoryWorksBriefingCitation[];
  constraints: MemoryWorksBriefingCitation[];
  prior_incidents: MemoryWorksBriefingCitation[];
  blast_radius: MemoryWorksBriefingCitation[];
  procedures: MemoryWorksBriefingCitation[];
  precedents: MemoryWorksBriefingPrecedent[];
  requires_approval: string[];
  safe_actions: string[];
  open_questions: string[];
  memory_count: number;
};

export type MemoryWorksBriefingInput = {
  task: string;
  service?: string;
  projectId?: string;
  surface?: string;
};

export type MemoryWorksOutcomeInput = {
  briefingId: string;
  action: string;
  outcome: "succeeded" | "failed" | "partial" | "abandoned" | "unknown";
  target?: string;
  surface?: string;
  reason?: string;
};

export type MemoryWorksOutcomeReceipt = {
  briefing_id: string;
  action: { id: string; action_type: string };
  outcome: { id: string; outcome: string; reward: number };
  recorded: boolean;
};

export type MemoryWorksChangeSet = {
  id: string;
  source_id: string;
  actor?: string;
  review_status?: string;
  created_at: string;
  added?: unknown[];
  updated?: unknown[];
  invalidated?: unknown[];
  conflicts?: unknown[];
  affected_artifacts?: unknown[];
  affected_skills?: unknown[];
};

export type MemoryWorksRefreshRequestResult = {
  files_scanned?: number;
  incremental?: { sources_changed?: number };
};

export type MemoryWorksRefreshRequest = {
  id: string;
  project_id: string;
  project_name?: string;
  repository: string;
  reason: string;
  status:
    | "pending_approval"
    | "denied"
    | "queued"
    | "running"
    | "succeeded"
    | "failed";
  requested_at: string;
  requested_by_id?: string;
  requested_by_name?: string;
  requested_by_email?: string;
  result?: MemoryWorksRefreshRequestResult;
  error?: string;
};

export type WebMCPActivity = {
  id: string;
  tool: WebMCPToolName;
  state: "running" | "complete" | "error";
  message?: string;
  input: Record<string, unknown>;
  inputSummary: string;
  startedAt: string;
  completedAt?: string;
  durationMs?: number;
  resultCount?: number;
  resultSummary?: string;
  permission: "read-only" | "ledger-append" | "approval-required" | "admin-decision";
};

export type MemoryWorksUnit = {
  id: string;
  project_id: string;
  project_name?: string;
  type: string;
  subject: string;
  content: string;
  scope?: {
    company?: string;
    project?: string;
    repo?: string;
    service?: string;
    person?: string;
  };
  confidence?: number;
  source_ids?: string[];
  valid_from?: string | null;
  valid_to?: string | null;
  updated_at?: string;
  score?: number;
};

export type MemoryWorksRelatedEntry = {
  relationship: string;
  linked_at?: string;
  memory: MemoryWorksUnit;
};

export type MemoryWorksServiceContextEntry = {
  project_id: string;
  project_name: string;
  profile: {
    name?: string;
    current_facts?: MemoryWorksUnit[];
    decisions?: MemoryWorksUnit[];
    incidents?: MemoryWorksUnit[];
    dependencies?: MemoryWorksUnit[];
    owners?: MemoryWorksUnit[];
    procedures?: MemoryWorksUnit[];
    policies?: MemoryWorksUnit[];
  };
};

export type MemoryWorksProposalInput = {
  projectId: string;
  kind: string;
  subject: string;
  content: string;
  service?: string;
  reason?: string;
};

export type MemoryWorksProposal = {
  id: string;
  project_id: string;
  project_name?: string;
  kind: string;
  subject: string;
  content: string;
  service?: string;
  reason?: string;
  origin?: string;
  status: "pending_approval" | "denied" | "approved";
  requested_at: string;
  requested_by_name?: string;
  requested_by_email?: string;
  memory_id?: string;
};

type RegistrationOptions = {
  spaces: MemoryWorksSpace[];
  getActiveProjectId: () => string;
  ask: (
    question: string,
    projectId: string,
    scope: "workspace" | "project",
  ) => Promise<MemoryWorksAnswer>;
  inspectChanges: (projectId: string, limit: number) => Promise<MemoryWorksChangeSet[]>;
  brief: (input: MemoryWorksBriefingInput) => Promise<MemoryWorksBriefing>;
  recordOutcome: (input: MemoryWorksOutcomeInput) => Promise<MemoryWorksOutcomeReceipt>;
  searchMemory: (
    projectId: string,
    query: string,
    type?: string,
    limit?: number,
  ) => Promise<MemoryWorksUnit[]>;
  getMemory: (memoryId: string) => Promise<MemoryWorksUnit>;
  getRelatedMemories: (memoryId: string) => Promise<MemoryWorksRelatedEntry[]>;
  listIncidents: (projectId: string, service?: string) => Promise<MemoryWorksUnit[]>;
  getServiceContext: (service: string) => Promise<MemoryWorksServiceContextEntry[]>;
  listDecisions: (projectId: string, limit?: number) => Promise<MemoryWorksUnit[]>;
  proposeMemory: (input: MemoryWorksProposalInput) => Promise<MemoryWorksProposal>;
  listProposals?: () => Promise<MemoryWorksProposal[]>;
  canResolveProposals?: boolean;
  resolveProposal?: (proposalId: string, approved: boolean) => Promise<MemoryWorksProposal>;
  proposeRepositoryRefresh: (
    projectId: string,
    reason: string,
  ) => Promise<MemoryWorksRefreshRequest>;
  listApprovals?: (projectId: string) => Promise<MemoryWorksRefreshRequest[]>;
  canResolveApprovals?: boolean;
  resolveApproval?: (
    requestId: string,
    approved: boolean,
  ) => Promise<MemoryWorksRefreshRequest>;
  onActivity?: (activity: WebMCPActivity) => void;
};

export type WebMCPRegistration = {
  supported: boolean;
  toolCount: number;
  dispose: () => void;
};

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
} as const;

/* Appending to the outcome ledger is a write, but it changes no company
   knowledge — it records that a briefing was used and how it went. Keeping it a
   separate tier from APPROVAL_REQUIRED_WRITE is the honest annotation: an agent
   may report back freely, and still cannot put a single fact into memory. */
const LEDGER_APPEND = {
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: false,
  openWorldHint: false,
} as const;

const APPROVAL_REQUIRED_WRITE = {
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
} as const;

function toolResult(summary: string, structuredContent: unknown): WebMCPToolResult {
  return {
    content: [
      { type: "text", text: summary },
      { type: "text", text: JSON.stringify(structuredContent) },
    ],
    structuredContent,
  };
}

function stringInput(input: Record<string, unknown>, key: string): string {
  return typeof input[key] === "string" ? input[key].trim() : "";
}

/* Optional project selection with the same authorization check as the required
   variant: an id an agent invented must never reach a scoped API call. */
function optionalProjectId(input: Record<string, unknown>, spaces: MemoryWorksSpace[]): string {
  const requested = stringInput(input, "project_id");
  if (requested && !spaces.some((space) => space.id === requested)) {
    throw new Error(
      "Choose a project_id returned by list_orgmemory_spaces before using this tool.",
    );
  }
  return requested;
}

function boundedLimit(input: Record<string, unknown>, fallback: number): number {
  const requested = typeof input.limit === "number" ? input.limit : fallback;
  return Math.max(1, Math.min(50, Math.trunc(requested)));
}

function compactUnit(unit: MemoryWorksUnit) {
  return {
    memory_id: unit.id,
    project_id: unit.project_id,
    project_name: unit.project_name,
    type: unit.type,
    subject: unit.subject,
    content: unit.content,
    service: unit.scope?.service || undefined,
    confidence: unit.confidence,
    sources: unit.source_ids?.length || 0,
    updated_at: unit.updated_at,
    score: unit.score,
  };
}

function projectFor(
  input: Record<string, unknown>,
  spaces: MemoryWorksSpace[],
  activeProjectId: string,
): MemoryWorksSpace {
  const requested = stringInput(input, "project_id") || activeProjectId;
  const project = spaces.find((space) => space.id === requested);
  if (!project) {
    throw new Error(
      "Choose a project_id returned by list_orgmemory_spaces before using this tool.",
    );
  }
  return project;
}

function count(value: unknown[] | undefined): number {
  return Array.isArray(value) ? value.length : 0;
}

async function tracked<T>(
  tool: WebMCPActivity["tool"],
  onActivity: RegistrationOptions["onActivity"],
  inputOrRun: Record<string, unknown> | (() => Promise<T> | T),
  maybeRun?: () => Promise<T> | T,
): Promise<T> {
  const input = typeof inputOrRun === "function" ? {} : inputOrRun;
  const run = typeof inputOrRun === "function" ? inputOrRun : maybeRun;
  if (!run) throw new Error("WebMCP tool is missing its execution handler");
  const safeInput = summarizeInput(input);
  const started = Date.now();
  const id = `${tool}-${started}-${Math.random().toString(36).slice(2, 8)}`;
  const permission = tool.startsWith("resolve_orgmemory_")
    ? "admin-decision"
    : tool.startsWith("propose_")
      ? "approval-required"
      : tool === "record_orgmemory_outcome"
        ? "ledger-append"
        : "read-only";
  const base = {
    id,
    tool,
    input: safeInput,
    inputSummary: inputLabel(safeInput),
    startedAt: new Date(started).toISOString(),
    permission,
  } satisfies Omit<WebMCPActivity, "state">;
  onActivity?.({ ...base, state: "running" });
  try {
    const value = await run();
    const finished = Date.now();
    const result = resultMetadata(value);
    onActivity?.({
      ...base,
      ...result,
      state: "complete",
      completedAt: new Date(finished).toISOString(),
      durationMs: finished - started,
    });
    return value;
  } catch (error) {
    const finished = Date.now();
    const message = error instanceof Error ? error.message : "Tool execution failed";
    onActivity?.({
      ...base,
      state: "error",
      message,
      completedAt: new Date(finished).toISOString(),
      durationMs: finished - started,
    });
    throw error;
  }
}

function summarizeInput(input: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(input).map(([key, value]) => {
      if (/token|secret|password|key|authorization/i.test(key)) return [key, "[redacted]"];
      if (typeof value === "string") return [key, value.length > 96 ? `${value.slice(0, 93)}…` : value];
      return [key, value];
    }),
  );
}

function inputLabel(input: Record<string, unknown>): string {
  const entries = Object.entries(input);
  if (!entries.length) return "No arguments";
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join(" · ");
}

function resultMetadata(value: unknown): Pick<WebMCPActivity, "resultCount" | "resultSummary"> {
  const result = value as { content?: Array<{ text?: string }>; structuredContent?: Record<string, unknown> };
  const structured = result?.structuredContent || {};
  const countEntry = Object.entries(structured).find(
    ([key, entry]) => /(_count|count)$/.test(key) && typeof entry === "number",
  );
  const arrayEntry = Object.entries(structured).find(([, entry]) => Array.isArray(entry));
  const summary = result?.content?.find((entry) => typeof entry.text === "string")?.text || "";
  return {
    resultCount: countEntry
      ? Number(countEntry[1])
      : arrayEntry
        ? (arrayEntry[1] as unknown[]).length
        : undefined,
    resultSummary: summary.length > 120 ? `${summary.slice(0, 117)}…` : summary,
  };
}

/**
 * Register the authenticated workspace as a browser-native Model Context
 * Provider. The page owns execution, so every call reuses its secure session
 * cookie and the same authorization checks as a human action in the UI.
 */
export async function registerMemoryWorksTools(
  options: RegistrationOptions,
): Promise<WebMCPRegistration> {
  if (typeof document === "undefined" || !document.modelContext) {
    return { supported: false, toolCount: 0, dispose: () => undefined };
  }

  const controller = new AbortController();
  const modelContext = document.modelContext;
  const registration = { signal: controller.signal };

  const registrations = [
    modelContext.registerTool(
      {
        name: "list_orgmemory_spaces",
        title: "List MemoryWorks spaces",
        description:
          "List the company-memory projects the signed-in person can access. Call this before choosing a project for another MemoryWorks tool.",
        inputSchema: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: () =>
          tracked("list_orgmemory_spaces", options.onActivity, () => {
            const activeProjectId = options.getActiveProjectId();
            const payload = {
              active_project_id: activeProjectId,
              spaces: options.spaces.map(({ id, name, repository }) => ({
                project_id: id,
                name,
                repository: repository || undefined,
              })),
            };
            return toolResult(
              `${payload.spaces.length} authorized MemoryWorks space${payload.spaces.length === 1 ? "" : "s"} available.`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "ask_orgmemory",
        title: "Ask company memory",
        description:
          "Ask a question against current, permission-scoped company memory. The answer is shown in the MemoryWorks workspace and returned with its source citations.",
        inputSchema: {
          type: "object",
          properties: {
            question: {
              type: "string",
              minLength: 3,
              maxLength: 4000,
              description: "The question to answer from company memory.",
            },
            project_id: {
              type: "string",
              description:
                "An authorized project ID from list_orgmemory_spaces. Defaults to the active project.",
            },
            scope: {
              type: "string",
              enum: ["workspace", "project"],
              description:
                "Search all authorized company memory or only the selected project. Defaults to workspace.",
            },
          },
          required: ["question"],
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("ask_orgmemory", options.onActivity, input, async () => {
            const question = stringInput(input, "question");
            if (question.length < 3) throw new Error("question must contain at least 3 characters");
            const project = projectFor(
              input,
              options.spaces,
              options.getActiveProjectId(),
            );
            const scope = input.scope === "project" ? "project" : "workspace";
            const answer = await options.ask(question, project.id, scope);
            const payload = {
              project_id: project.id,
              project_name: project.name,
              scope,
              answer: answer.answer,
              answer_sufficient: answer.answer_sufficient,
              answer_scope: answer.answer_scope,
              resolved_subject: answer.resolved_subject,
              searched_sources: answer.searched_sources,
              likely_cause: answer.likely_cause,
              confidence: answer.confidence,
              trust_score: answer.trust_score,
              memory_units: (answer.memory_units || []).map(compactUnit),
              related_entities: answer.related_entities || [],
              updates: answer.updates || [],
              conflicts: answer.conflicts || [],
              safe_actions: answer.safe_actions || [],
              approval_required: answer.approval_required || [],
              retrieval_trace: answer.retrieval_trace,
              context_envelope: answer.context_envelope,
              evidence: (answer.evidence || []).map(
                ({ source_title, source_type, source_url }) => ({
                  title: source_title,
                  type: source_type,
                  url: source_url,
                }),
              ),
            };
            return toolResult(
              `${answer.answer}\n\nSources: ${payload.evidence.map((source) => source.title).join(", ") || "No source was sufficient."}`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_briefing",
        title: "Brief me before I act",
        description:
          "Call this BEFORE changing anything — on this site or any other. Describe what you are about to do and MemoryWorks returns what this company already knows about it: decisions that constrain the change, incidents that started the same way, the components a change here reaches, the established procedure if one exists, and whether a person has to approve first. Returns a briefing_id; report back with record_orgmemory_outcome once you know whether it worked.",
        inputSchema: {
          type: "object",
          properties: {
            task: {
              type: "string",
              minLength: 3,
              maxLength: 2000,
              description:
                "What you are about to do, in plain language. For example: 'raise worker concurrency on the payments service' or 'merge the pull request that changes the connection pool'.",
            },
            service: {
              type: "string",
              maxLength: 120,
              description:
                "The service, repository, or component the action targets. Naming it retrieves that component's incident history and owners.",
            },
            project_id: {
              type: "string",
              description:
                "An authorized project ID from list_orgmemory_spaces. Omit to draw on every memory space the signed-in person can see.",
            },
            surface: {
              type: "string",
              maxLength: 64,
              description:
                "Where you are working — a host name or app, such as 'github.com' or 'pagerduty'. Recorded so the company can tell which surfaces its context actually helps.",
            },
          },
          required: ["task"],
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_briefing", options.onActivity, input, async () => {
            const task = stringInput(input, "task");
            if (task.length < 3) {
              throw new Error("Describe what you are about to do in a few words.");
            }
            const projectId = optionalProjectId(input, options.spaces);
            const briefing = await options.brief({
              task,
              service: stringInput(input, "service") || undefined,
              projectId: projectId || undefined,
              surface: stringInput(input, "surface") || undefined,
            });
            /* The summary line is what a browser agent reads first and often the
               only thing a person sees quoted back, so the verdict leads it. */
            const summary =
              briefing.verdict === "no_memory"
                ? `No company memory covers this yet. ${briefing.headline}`
                : briefing.verdict === "requires_approval"
                  ? `Human approval required. ${briefing.prior_incidents.length} prior incident(s) and ${briefing.constraints.length} recorded decision(s) apply.`
                  : `${briefing.memory_count} remembered record(s) apply before you act.`;
            return toolResult(summary, briefing);
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "record_orgmemory_outcome",
        title: "Report what happened",
        description:
          "Report back what you did with a briefing and whether it worked. This appends an observation to the company's outcome ledger — it does NOT change company memory, so it needs no approval; use propose_orgmemory_incident or propose_orgmemory_memory for anything that should become durable knowledge. Reporting honestly, including failures, is what makes future briefings better.",
        inputSchema: {
          type: "object",
          properties: {
            briefing_id: {
              type: "string",
              description: "The briefing_id returned by get_orgmemory_briefing.",
            },
            action: {
              type: "string",
              minLength: 2,
              maxLength: 64,
              description:
                "What you actually did, as a short slug — for example 'followed_procedure', 'opened_pr', 'escalated_to_owner', or 'abandoned'.",
            },
            outcome: {
              type: "string",
              enum: ["succeeded", "failed", "partial", "abandoned", "unknown"],
              description:
                "Whether it worked. Use 'unknown' rather than guessing; a wrong label poisons the record more than a missing one.",
            },
            target: {
              type: "string",
              maxLength: 200,
              description: "What you acted on — a service, repository, or URL.",
            },
            surface: {
              type: "string",
              maxLength: 64,
              description: "Where you acted, such as 'github.com'.",
            },
            reason: {
              type: "string",
              maxLength: 2000,
              description: "One or two sentences on what happened and why.",
            },
          },
          required: ["briefing_id", "action", "outcome"],
          additionalProperties: false,
        },
        annotations: LEDGER_APPEND,
        execute: (input) =>
          tracked("record_orgmemory_outcome", options.onActivity, input, async () => {
            const briefingId = stringInput(input, "briefing_id");
            const action = stringInput(input, "action");
            if (!briefingId) throw new Error("briefing_id is required");
            if (action.length < 2) throw new Error("Describe the action you took.");
            const outcome = stringInput(input, "outcome") || "unknown";
            if (!ORGMEMORY_OUTCOMES.includes(outcome as (typeof ORGMEMORY_OUTCOMES)[number])) {
              throw new Error(`outcome must be one of ${ORGMEMORY_OUTCOMES.join(", ")}`);
            }
            const receipt = await options.recordOutcome({
              briefingId,
              action,
              outcome: outcome as MemoryWorksOutcomeInput["outcome"],
              target: stringInput(input, "target") || undefined,
              surface: stringInput(input, "surface") || undefined,
              reason: stringInput(input, "reason") || undefined,
            });
            return toolResult(
              `Recorded: ${action} → ${outcome}. Company memory is unchanged; this is an entry in the outcome ledger.`,
              {
                briefing_id: receipt.briefing_id,
                action_id: receipt.action?.id,
                outcome_id: receipt.outcome?.id,
                outcome: receipt.outcome?.outcome,
                changed_company_memory: false,
                next_step:
                  "If this produced knowledge the company should keep, propose it with propose_orgmemory_incident or propose_orgmemory_memory so a person can approve it.",
              },
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "inspect_orgmemory_changes",
        title: "Inspect recent memory changes",
        description:
          "Inspect recent source-backed memory changes for an authorized project, including additions, updates, invalidations, conflicts, and downstream artifacts needing review.",
        inputSchema: {
          type: "object",
          properties: {
            project_id: {
              type: "string",
              description:
                "An authorized project ID from list_orgmemory_spaces. Defaults to the active project.",
            },
            limit: {
              type: "integer",
              minimum: 1,
              maximum: 50,
              default: 10,
              description: "Maximum number of recent change sets to return.",
            },
          },
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("inspect_orgmemory_changes", options.onActivity, input, async () => {
            const project = projectFor(
              input,
              options.spaces,
              options.getActiveProjectId(),
            );
            const requestedLimit = typeof input.limit === "number" ? input.limit : 10;
            const limit = Math.max(1, Math.min(50, Math.trunc(requestedLimit)));
            const changes = await options.inspectChanges(project.id, limit);
            const payload = {
              project_id: project.id,
              project_name: project.name,
              change_count: changes.length,
              changes: changes.map((change) => ({
                change_set_id: change.id,
                source_id: change.source_id,
                created_at: change.created_at,
                actor: change.actor,
                review_status: change.review_status,
                added: count(change.added),
                updated: count(change.updated),
                invalidated: count(change.invalidated),
                conflicts: count(change.conflicts),
                affected_artifacts: count(change.affected_artifacts),
                affected_skills: count(change.affected_skills),
              })),
            };
            const reviewCount = payload.changes.filter(
              (change) => change.review_status === "needs_review",
            ).length;
            return toolResult(
              `${changes.length} recent change set${changes.length === 1 ? "" : "s"} found for ${project.name}; ${reviewCount} need review.`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "search_orgmemory",
        title: "Search company memory",
        description:
          "Search current, permission-scoped organizational memory by query and optional memory kind (incident, decision, fact, dependency, ...). Prefer this structured search over scraping or clicking through the UI.",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              maxLength: 400,
              description:
                "Words to look for, such as a service name, failure mode, or topic.",
            },
            project_id: {
              type: "string",
              description:
                "Optional project ID from list_orgmemory_spaces. Defaults to all authorized spaces.",
            },
            type: {
              type: "string",
              enum: [...ORGMEMORY_PROPOSABLE_KINDS],
              description: "Optional memory kind filter, e.g. incident or decision.",
            },
            limit: {
              type: "integer",
              minimum: 1,
              maximum: 50,
              default: 10,
              description: "Maximum number of memories to return.",
            },
          },
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("search_orgmemory", options.onActivity, input, async () => {
            const query = stringInput(input, "query");
            const kind = stringInput(input, "type");
            if (!query && !kind) {
              throw new Error("Provide a query or a memory type to search for.");
            }
            const projectId = optionalProjectId(input, options.spaces);
            const limit = boundedLimit(input, 10);
            const results = await options.searchMemory(projectId, query, kind || undefined, limit);
            const payload = {
              query: query || undefined,
              type: kind || undefined,
              project_id: projectId || undefined,
              result_count: results.length,
              results: results.map(compactUnit),
            };
            return toolResult(
              results.length
                ? `${results.length} memor${results.length === 1 ? "y" : "ies"} matched: ${results
                    .slice(0, 3)
                    .map((unit) => `[${unit.type}] ${unit.subject}`)
                    .join("; ")}${results.length > 3 ? "; …" : ""}.`
                : "No current company memory matched. The answer may be missing because the source was never ingested.",
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_memory",
        title: "Get one memory by ID",
        description:
          "Fetch a single organizational memory by its memory_id, with full content, scope, confidence, and validity dates. Use IDs returned by search_orgmemory.",
        inputSchema: {
          type: "object",
          properties: {
            memory_id: {
              type: "string",
              description: "A memory_id from search_orgmemory or another MemoryWorks tool.",
            },
          },
          required: ["memory_id"],
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_memory", options.onActivity, input, async () => {
            const memoryId = stringInput(input, "memory_id");
            if (!memoryId) throw new Error("memory_id is required");
            const unit = await options.getMemory(memoryId);
            const payload = { ...compactUnit(unit), valid_from: unit.valid_from, valid_to: unit.valid_to };
            return toolResult(
              `[${unit.type}] ${unit.subject}: ${unit.content}`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_related_memories",
        title: "Get related memories",
        description:
          "Follow the memory graph around one memory: updates, contradictions, supporting and derived memories, plus other memories about the same subject. Use this to understand how a fact changed over time.",
        inputSchema: {
          type: "object",
          properties: {
            memory_id: {
              type: "string",
              description: "A memory_id from search_orgmemory or another MemoryWorks tool.",
            },
          },
          required: ["memory_id"],
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_related_memories", options.onActivity, input, async () => {
            const memoryId = stringInput(input, "memory_id");
            if (!memoryId) throw new Error("memory_id is required");
            const related = await options.getRelatedMemories(memoryId);
            const payload = {
              memory_id: memoryId,
              related_count: related.length,
              related: related.map((entry) => ({
                relationship: entry.relationship,
                ...compactUnit(entry.memory),
              })),
            };
            return toolResult(
              related.length
                ? `${related.length} related memor${related.length === 1 ? "y" : "ies"}: ${related
                    .slice(0, 3)
                    .map((entry) => `${entry.relationship} → ${entry.memory.subject}`)
                    .join("; ")}${related.length > 3 ? "; …" : ""}.`
                : "No related memories found for this ID.",
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_incidents",
        title: "Get previous incidents",
        description:
          "Retrieve previous incident memories, optionally filtered by service name. Use this to compare current symptoms with what happened before.",
        inputSchema: {
          type: "object",
          properties: {
            service: {
              type: "string",
              description: "Service name such as payments. Omit to list recent incidents across spaces.",
            },
            project_id: {
              type: "string",
              description:
                "Optional project ID from list_orgmemory_spaces. Defaults to all authorized spaces.",
            },
          },
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_incidents", options.onActivity, input, async () => {
            const service = stringInput(input, "service");
            const projectId = optionalProjectId(input, options.spaces);
            const incidents = await options.listIncidents(projectId, service || undefined);
            const payload = {
              service: service || undefined,
              project_id: projectId || undefined,
              incident_count: incidents.length,
              incidents: incidents.map(compactUnit),
            };
            return toolResult(
              incidents.length
                ? `${incidents.length} previous incident${incidents.length === 1 ? "" : "s"} found: ${incidents
                    .slice(0, 3)
                    .map((unit) => unit.subject)
                    .join("; ")}${incidents.length > 3 ? "; …" : ""}.`
                : service
                  ? `No previous incidents remembered for ${service}.`
                  : "No incidents are remembered yet.",
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_service_context",
        title: "Get service context",
        description:
          "Retrieve the assembled context for a service: current facts, owners, dependencies, decisions, procedures, and incident history. Call this to understand a service before acting on it.",
        inputSchema: {
          type: "object",
          properties: {
            service: {
              type: "string",
              description: "Service name such as payments.",
            },
            project_id: {
              type: "string",
              description:
                "Optional project ID from list_orgmemory_spaces. Defaults to all authorized spaces.",
            },
          },
          required: ["service"],
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_service_context", options.onActivity, input, async () => {
            const service = stringInput(input, "service");
            if (!service) throw new Error("service is required");
            optionalProjectId(input, options.spaces);
            const entries = await options.getServiceContext(service);
            const payload = {
              service,
              space_count: entries.length,
              spaces: entries.map((entry) => ({
                project_id: entry.project_id,
                project_name: entry.project_name,
                facts: (entry.profile.current_facts || []).map(compactUnit),
                owners: (entry.profile.owners || []).map(compactUnit),
                dependencies: (entry.profile.dependencies || []).map(compactUnit),
                decisions: (entry.profile.decisions || []).map(compactUnit),
                procedures: (entry.profile.procedures || []).map(compactUnit),
                incidents: (entry.profile.incidents || []).map(compactUnit),
              })),
            };
            const factCount = entries.reduce(
              (total, entry) =>
                total +
                (entry.profile.current_facts || []).length +
                (entry.profile.owners || []).length,
              0,
            );
            return toolResult(
              entries.length
                ? `Service context for ${service} assembled from ${entries.length} space${entries.length === 1 ? "" : "s"}: ${factCount} facts/owners, ${payload.spaces.reduce((total, space) => total + space.decisions.length + space.incidents.length, 0)} decisions/incidents.`
                : `No company memory mentions a service called ${service}.`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_dependencies",
        title: "Get service dependencies",
        description:
          "Retrieve remembered dependencies for a service, including upstream and downstream relationships captured in memory. Use this to reason about blast radius.",
        inputSchema: {
          type: "object",
          properties: {
            service: {
              type: "string",
              description: "Service name such as payments.",
            },
            project_id: {
              type: "string",
              description:
                "Optional project ID from list_orgmemory_spaces. Defaults to all authorized spaces.",
            },
          },
          required: ["service"],
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_dependencies", options.onActivity, input, async () => {
            const service = stringInput(input, "service");
            if (!service) throw new Error("service is required");
            const projectId = optionalProjectId(input, options.spaces);
            const dependencies = await options.searchMemory(projectId, service, "dependency", 20);
            const payload = {
              service,
              project_id: projectId || undefined,
              dependency_count: dependencies.length,
              dependencies: dependencies.map(compactUnit),
            };
            return toolResult(
              dependencies.length
                ? `${dependencies.length} remembered dependenc${dependencies.length === 1 ? "y" : "ies"} for ${service}: ${dependencies
                    .slice(0, 3)
                    .map((unit) => unit.subject)
                    .join("; ")}${dependencies.length > 3 ? "; …" : ""}.`
                : `No dependencies are remembered for ${service}.`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "get_orgmemory_decisions",
        title: "Get architecture decisions",
        description:
          "Retrieve remembered architecture and operational decisions, optionally scoped to one project. Use this to check what the organization already decided before proposing something new.",
        inputSchema: {
          type: "object",
          properties: {
            project_id: {
              type: "string",
              description:
                "Optional project ID from list_orgmemory_spaces. Defaults to all authorized spaces.",
            },
            limit: {
              type: "integer",
              minimum: 1,
              maximum: 50,
              default: 10,
              description: "Maximum number of decisions to return.",
            },
          },
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("get_orgmemory_decisions", options.onActivity, input, async () => {
            const projectId = optionalProjectId(input, options.spaces);
            const limit = boundedLimit(input, 10);
            const decisions = await options.listDecisions(projectId, limit);
            const payload = {
              project_id: projectId || undefined,
              decision_count: decisions.length,
              decisions: decisions.map(compactUnit),
            };
            return toolResult(
              decisions.length
                ? `${decisions.length} remembered decision${decisions.length === 1 ? "" : "s"}: ${decisions
                    .slice(0, 3)
                    .map((unit) => unit.subject)
                    .join("; ")}${decisions.length > 3 ? "; …" : ""}.`
                : "No decisions are remembered yet.",
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "propose_repository_refresh",
        title: "Propose repository refresh",
        description:
          "Create an approval-required request to refresh an authorized GitHub repository when its MemoryWorks evidence is stale or incomplete. This tool never refreshes the repository itself; a person must approve it in MemoryWorks first.",
        inputSchema: {
          type: "object",
          properties: {
            project_id: {
              type: "string",
              description:
                "An authorized GitHub-backed project ID from list_orgmemory_spaces. Defaults to the active project.",
            },
            reason: {
              type: "string",
              minLength: 5,
              maxLength: 800,
              description:
                "Why a refresh is needed, such as stale commit evidence or a missing source.",
            },
          },
          required: ["reason"],
          additionalProperties: false,
        },
        annotations: APPROVAL_REQUIRED_WRITE,
        execute: (input) =>
          tracked("propose_repository_refresh", options.onActivity, input, async () => {
            const project = projectFor(input, options.spaces, options.getActiveProjectId());
            if (!project.repository) {
              throw new Error("Choose a GitHub-backed project before requesting a repository refresh.");
            }
            const reason = stringInput(input, "reason");
            if (reason.length < 5) throw new Error("reason must contain at least 5 characters");
            const request = await options.proposeRepositoryRefresh(project.id, reason);
            const payload = {
              refresh_request_id: request.id,
              project_id: project.id,
              project_name: project.name,
              repository: request.repository,
              reason: request.reason,
              status: request.status,
              next_step:
                "A person must approve or deny this request in MemoryWorks before any repository refresh runs.",
            };
            return toolResult(
              `Repository refresh request for ${project.name} is ${request.status}. No refresh has run yet; it requires human approval.`,
              payload,
            );
          }),
      },
      registration,
    ),
    modelContext.registerTool(
      {
        name: "list_orgmemory_approvals",
        title: "List pending approvals",
        description:
          "List repository refresh requests that are waiting for a human approval decision in the authorized MemoryWorks projects, including who requested each one and why.",
        inputSchema: {
          type: "object",
          properties: {
            project_id: {
              type: "string",
              description:
                "An authorized project ID from list_orgmemory_spaces. Defaults to showing every authorized project.",
            },
          },
          additionalProperties: false,
        },
        annotations: READ_ONLY,
        execute: (input) =>
          tracked("list_orgmemory_approvals", options.onActivity, input, async () => {
            const requested = stringInput(input, "project_id");
            if (requested && !options.spaces.some((space) => space.id === requested)) {
              throw new Error(
                "Choose a project_id returned by list_orgmemory_spaces before using this tool.",
              );
            }
            if (!options.listApprovals) {
              throw new Error("Approval tools are not available on this page.");
            }
            // The backend already scopes this list to what the signed-in person
            // may see and resolve; re-checking and re-filtering here keeps
            // unauthorized or cross-project rows out of the agent's payload.
            const fetched = requested
              ? await options.listApprovals(requested)
              : (
                  await Promise.all(
                    options.spaces.map((space) =>
                      options.listApprovals!(space.id).catch(() => []),
                    ),
                  )
                ).flat();
            const requests = requested
              ? fetched.filter((request) => request.project_id === requested)
              : fetched;
            const pending = requests.filter((request) => request.status === "pending_approval");
            const payload = {
              project_id: requested || undefined,
              pending_count: pending.length,
              resolved_recently: requests.length - pending.length,
              approvals: requests.map((request) => ({
                refresh_request_id: request.id,
                project_id: request.project_id,
                project_name: request.project_name,
                repository: request.repository,
                reason: request.reason,
                status: request.status,
                requested_by_name: request.requested_by_name,
                requested_by_email: request.requested_by_email,
                requested_at: request.requested_at,
              })),
            };
            return toolResult(
              pending.length
                ? `${pending.length} approval${pending.length === 1 ? "" : "s"} waiting, including ${pending
                    .slice(0, 3)
                    .map((request) => request.repository)
                    .join(", ")}.${options.canResolveApprovals ? " Use resolve_orgmemory_approval to record a decision." : " An MemoryWorks admin must record the decision."}`
                : "No approvals are waiting for a human decision.",
              payload,
            );
          }),
      },
      registration,
    ),
  ...(options.canResolveApprovals && options.resolveApproval
    ? [modelContext.registerTool(
      {
        name: "resolve_orgmemory_approval",
        title: "Approve or deny a pending request",
        description:
          "Record a human approval decision on a pending MemoryWorks repository refresh request. Approving queues the server-side GitHub ingest; denying closes it. This tool must only be used when the signed-in person has actually decided.",
        inputSchema: {
          type: "object",
          properties: {
            refresh_request_id: {
              type: "string",
              description:
                "The refresh_request_id of a pending approval from list_orgmemory_approvals.",
            },
            approved: {
              type: "boolean",
              description:
                "True records an approval and queues the repository refresh; false denies it.",
            },
          },
          required: ["refresh_request_id", "approved"],
          additionalProperties: false,
        },
        annotations: APPROVAL_REQUIRED_WRITE,
        execute: (input) =>
          tracked("resolve_orgmemory_approval", options.onActivity, input, async () => {
            if (!options.resolveApproval) {
              throw new Error("Approval decisions are not available on this page.");
            }
            const requestId = stringInput(input, "refresh_request_id");
            if (!requestId) throw new Error("refresh_request_id is required");
            const approved = input.approved === true;
            const request = await options.resolveApproval(requestId, approved);
            const payload = {
              refresh_request_id: request.id,
              project_id: request.project_id,
              project_name: request.project_name,
              repository: request.repository,
              reason: request.reason,
              requested_by_name: request.requested_by_name,
              status: request.status,
              next_step:
                request.status === "queued"
                  ? "The repository ingest was queued; memory will update when it completes."
                  : "The request stays pending until a person approves it.",
            };
            return toolResult(
              request.status === "queued"
                ? `Approved. The refresh of ${request.repository} is queued and will update company memory.`
                : `Decision recorded: ${request.status.replace(/_/g, " ")}.`,
              payload,
            );
          }),
      },
      registration,
    )]
      : []),
  modelContext.registerTool(
    {
      name: "propose_orgmemory_memory",
      title: "Propose a memory for approval",
      description:
        "Propose adding one verified piece of organizational knowledge (fact, procedure, policy, convention, ...) to company memory. Nothing is saved by this tool: the proposal enters an approval queue and only becomes memory after a person explicitly approves it. Never propose speculative conclusions.",
      inputSchema: {
        type: "object",
        properties: {
          subject: {
            type: "string",
            minLength: 3,
            maxLength: 300,
            description: "What the memory is about, e.g. 'payments connection pool'.",
          },
          content: {
            type: "string",
            minLength: 3,
            maxLength: 4000,
            description: "The statement itself, grounded in evidence you can cite.",
          },
          kind: {
            type: "string",
            enum: [...ORGMEMORY_PROPOSABLE_KINDS],
            default: "fact",
            description: "The memory kind. Defaults to fact.",
          },
          service: {
            type: "string",
            description: "Optional service this memory is scoped to.",
          },
          project_id: {
            type: "string",
            description:
              "An authorized project ID from list_orgmemory_spaces. Defaults to the active project.",
          },
          reason: {
            type: "string",
            maxLength: 800,
            description: "Why this is verified knowledge, for the human who reviews it.",
          },
        },
        required: ["subject", "content"],
        additionalProperties: false,
      },
      annotations: APPROVAL_REQUIRED_WRITE,
      execute: (input) =>
        tracked("propose_orgmemory_memory", options.onActivity, input, async () => {
          const kindInput = stringInput(input, "kind");
          const kind = kindInput || "fact";
          if (!(ORGMEMORY_PROPOSABLE_KINDS as readonly string[]).includes(kind)) {
            throw new Error(`kind must be one of: ${ORGMEMORY_PROPOSABLE_KINDS.join(", ")}`);
          }
          const project = projectFor(input, options.spaces, options.getActiveProjectId());
          const proposal = await options.proposeMemory({
            projectId: project.id,
            kind,
            subject: stringInput(input, "subject"),
            content: stringInput(input, "content"),
            service: stringInput(input, "service") || undefined,
            reason: stringInput(input, "reason") || undefined,
          });
          return toolResult(
            `Proposal queued as pending_approval. Nothing has been saved to company memory yet; a person must approve it in MemoryWorks.`,
            {
              proposal_id: proposal.id,
              project_id: proposal.project_id,
              kind: proposal.kind,
              subject: proposal.subject,
              status: proposal.status,
              requested_by: proposal.requested_by_name,
              next_step:
                "A person must approve or deny this proposal in MemoryWorks before it becomes company memory.",
            },
          );
        }),
    },
    registration,
  ),
  modelContext.registerTool(
    {
      name: "propose_orgmemory_incident",
      title: "Propose an incident record for approval",
      description:
        "Propose recording an incident into company memory once its diagnosis is verified (confirmed by monitoring, logs, or a person). Nothing is saved by this tool: the proposal waits for explicit human approval in MemoryWorks.",
      inputSchema: {
        type: "object",
        properties: {
          subject: {
            type: "string",
            minLength: 3,
            maxLength: 300,
            description: "Short incident title, e.g. 'payments outage: pool exhaustion'.",
          },
          content: {
            type: "string",
            minLength: 3,
            maxLength: 4000,
            description:
              "What happened, the verified cause, and the resolution. Only record verified findings.",
          },
          service: {
            type: "string",
            description: "The service the incident affected, e.g. payments.",
          },
          project_id: {
            type: "string",
            description:
              "An authorized project ID from list_orgmemory_spaces. Defaults to the active project.",
          },
          reason: {
            type: "string",
            maxLength: 800,
            description: "Why this incident record is verified, for the human who reviews it.",
          },
        },
        required: ["subject", "content"],
        additionalProperties: false,
      },
      annotations: APPROVAL_REQUIRED_WRITE,
      execute: (input) =>
        tracked("propose_orgmemory_incident", options.onActivity, input, async () => {
          const project = projectFor(input, options.spaces, options.getActiveProjectId());
          const proposal = await options.proposeMemory({
            projectId: project.id,
            kind: "incident",
            subject: stringInput(input, "subject"),
            content: stringInput(input, "content"),
            service: stringInput(input, "service") || undefined,
            reason: stringInput(input, "reason") || undefined,
          });
          return toolResult(
            `Incident proposal queued as pending_approval. No incident record was saved yet; a person must approve it.`,
            {
              proposal_id: proposal.id,
              project_id: proposal.project_id,
              kind: proposal.kind,
              subject: proposal.subject,
              status: proposal.status,
              requested_by: proposal.requested_by_name,
              next_step:
                "A person must approve or deny this proposal in MemoryWorks before it becomes an incident memory.",
            },
          );
        }),
    },
    registration,
  ),
  modelContext.registerTool(
    {
      name: "propose_orgmemory_decision",
      title: "Propose a decision record for approval",
      description:
        "Propose recording an architecture or operational decision into company memory once it is actually decided (not merely recommended). Nothing is saved by this tool: the proposal waits for explicit human approval in MemoryWorks.",
      inputSchema: {
        type: "object",
        properties: {
          subject: {
            type: "string",
            minLength: 3,
            maxLength: 300,
            description: "What was decided, e.g. 'cap payments worker concurrency'.",
          },
          content: {
            type: "string",
            minLength: 3,
            maxLength: 4000,
            description:
              "The decision itself and its rationale. Only record decisions that were actually made.",
          },
          service: {
            type: "string",
            description: "Optional service this decision applies to.",
          },
          project_id: {
            type: "string",
            description:
              "An authorized project ID from list_orgmemory_spaces. Defaults to the active project.",
          },
          reason: {
            type: "string",
            maxLength: 800,
            description: "Why this decision is verified, for the human who reviews it.",
          },
        },
        required: ["subject", "content"],
        additionalProperties: false,
      },
      annotations: APPROVAL_REQUIRED_WRITE,
      execute: (input) =>
        tracked("propose_orgmemory_decision", options.onActivity, input, async () => {
          const project = projectFor(input, options.spaces, options.getActiveProjectId());
          const proposal = await options.proposeMemory({
            projectId: project.id,
            kind: "decision",
            subject: stringInput(input, "subject"),
            content: stringInput(input, "content"),
            service: stringInput(input, "service") || undefined,
            reason: stringInput(input, "reason") || undefined,
          });
          return toolResult(
            `Decision proposal queued as pending_approval. No decision record was saved yet; a person must approve it.`,
            {
              proposal_id: proposal.id,
              project_id: proposal.project_id,
              kind: proposal.kind,
              subject: proposal.subject,
              status: proposal.status,
              requested_by: proposal.requested_by_name,
              next_step:
                "A person must approve or deny this proposal in MemoryWorks before it becomes a decision memory.",
            },
          );
        }),
    },
    registration,
  ),
  modelContext.registerTool(
    {
      name: "list_orgmemory_proposals",
      title: "List memory proposals",
      description:
        "List proposed organizational memories that are waiting for a human approval decision, including who proposed each one and why. Read-only.",
      inputSchema: {
        type: "object",
        properties: {
          project_id: {
            type: "string",
            description:
              "Optional project ID from list_orgmemory_spaces. Defaults to all authorized spaces.",
          },
        },
        additionalProperties: false,
      },
      annotations: READ_ONLY,
      execute: (input) =>
        tracked("list_orgmemory_proposals", options.onActivity, input, async () => {
          if (!options.listProposals) {
            throw new Error("Proposal tools are not available on this page.");
          }
          optionalProjectId(input, options.spaces);
          const proposals = await options.listProposals();
          const pending = proposals.filter((proposal) => proposal.status === "pending_approval");
          const payload = {
            pending_count: pending.length,
            proposals: proposals.map((proposal) => ({
              proposal_id: proposal.id,
              project_id: proposal.project_id,
              project_name: proposal.project_name,
              kind: proposal.kind,
              subject: proposal.subject,
              content: proposal.content,
              service: proposal.service || undefined,
              reason: proposal.reason || undefined,
              status: proposal.status,
              requested_by_name: proposal.requested_by_name,
              requested_at: proposal.requested_at,
              memory_id: proposal.memory_id || undefined,
            })),
          };
          return toolResult(
            pending.length
              ? `${pending.length} memory proposal${pending.length === 1 ? "" : "s"} waiting for a human decision.${options.canResolveProposals ? " Use resolve_orgmemory_proposal to record it." : ""}`
              : "No memory proposals are waiting for a decision.",
            payload,
          );
        }),
    },
    registration,
  ),
  ...(options.canResolveProposals && options.resolveProposal
    ? [modelContext.registerTool(
      {
        name: "resolve_orgmemory_proposal",
        title: "Approve or deny a memory proposal",
        description:
          "Record a human approval decision on a pending MemoryWorks memory proposal. Approving persists the memory into company memory; denying closes it. This tool must only be used when the signed-in person has actually decided.",
        inputSchema: {
          type: "object",
          properties: {
            proposal_id: {
              type: "string",
              description: "A proposal_id from list_orgmemory_proposals.",
            },
            approved: {
              type: "boolean",
              description:
                "True records an approval and persists the memory; false denies the proposal.",
            },
          },
          required: ["proposal_id", "approved"],
          additionalProperties: false,
        },
        annotations: APPROVAL_REQUIRED_WRITE,
        execute: (input) =>
          tracked("resolve_orgmemory_proposal", options.onActivity, input, async () => {
            if (!options.resolveProposal) {
              throw new Error("Proposal decisions are not available on this page.");
            }
            const proposalId = stringInput(input, "proposal_id");
            if (!proposalId) throw new Error("proposal_id is required");
            const approved = input.approved === true;
            const proposal = await options.resolveProposal(proposalId, approved);
            return toolResult(
              proposal.status === "approved"
                ? `Approved. The ${proposal.kind} memory "${proposal.subject}" is now part of company memory.`
                : `Decision recorded: ${proposal.status}. Nothing was saved to company memory.`,
              {
                proposal_id: proposal.id,
                status: proposal.status,
                kind: proposal.kind,
                subject: proposal.subject,
                memory_id: proposal.memory_id || undefined,
              },
            );
          }),
      },
      registration,
    )]
    : []),
  /* One registration per organizational operation. The handler an agent
     receives is the same object the page's own console calls, so there is no
     second, friendlier code path that only the demo takes. */
  ...Object.values(ORG_TOOLS).map((tool) =>
    modelContext.registerTool(
      {
        name: tool.name,
        title: tool.title,
        description: tool.description,
        inputSchema: tool.inputSchema,
        annotations: tool.kind === "read" ? READ_ONLY : APPROVAL_REQUIRED_WRITE,
        execute: (input) =>
          tracked(tool.name as WebMCPToolName, options.onActivity, input, async () => {
            const result = await tool.run(input as Record<string, unknown>);
            return toolResult(result.summary, result.data);
          }),
      },
      registration,
    ),
  ),
];

  try {
    await Promise.all(registrations);
  } catch (error) {
    controller.abort();
    throw error;
  }

  return {
    supported: true,
    toolCount: registrations.length,
    dispose: () => controller.abort(),
  };
}

/**
 * Register the agent-operations console as a Model Context Provider surface
 * of its own.
 *
 * The workspace registers the full 21-tool surface; this page registers the
 * organizational-operations set it actually exercises, so a browser agent
 * connected to the page drives the same handlers the on-screen console calls
 * — not a parallel demo path. Every external call flows back through
 * `onActivity`, which is what makes foreign agent traffic visible on screen.
 */
export async function registerOrgConsoleWebMCP(
  onActivity?: (activity: WebMCPActivity) => void,
): Promise<WebMCPRegistration> {
  if (typeof document === "undefined" || !document.modelContext) {
    return { supported: false, toolCount: 0, dispose: () => undefined };
  }

  const controller = new AbortController();
  const registration = { signal: controller.signal };

  const registrations = Object.values(ORG_TOOLS).map((tool) =>
    document.modelContext!.registerTool(
      {
        name: tool.name,
        title: tool.title,
        description: tool.description,
        inputSchema: tool.inputSchema,
        annotations: tool.kind === "read" ? READ_ONLY : APPROVAL_REQUIRED_WRITE,
        execute: (input) =>
          tracked(tool.name as WebMCPToolName, onActivity, input, async () => {
            const result = await tool.run(input as Record<string, unknown>);
            return toolResult(result.summary, result.data);
          }),
      },
      registration,
    ),
  );

  try {
    await Promise.all(registrations);
  } catch (error) {
    controller.abort();
    throw error;
  }

  return {
    supported: true,
    toolCount: registrations.length,
    dispose: () => controller.abort(),
  };
}
