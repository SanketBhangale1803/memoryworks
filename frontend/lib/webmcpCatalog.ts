import { ORG_TOOLS } from "@/lib/orgTools";
import type { WebMCPToolName } from "@/lib/webmcp";

export type WebMCPToolCatalogEntry = {
  name: WebMCPToolName;
  title: string;
  description: string;
  group: "Discover" | "Retrieve" | "Investigate" | "Brief" | "Report" | "Propose" | "Decide" | "Operate";
  /** What a person does instead when this tool does not exist. */
  manualEquivalent?: string;
  permission: "read-only" | "ledger-append" | "approval-required" | "admin-decision";
  inputSchema: Record<string, unknown>;
  resultExample: Record<string, unknown>;
};

const optionalProject = {
  project_id: {
    type: "string",
    description: "An authorized project ID returned by list_orgmemory_spaces.",
  },
};

/* A human-readable manifest for the command center. The executable definitions
   remain in webmcp.ts; this manifest deliberately contains no handlers or
   business logic, so the product still has one execution path. */
export const WEBMCP_TOOL_CATALOG: WebMCPToolCatalogEntry[] = [
  {
    name: "list_orgmemory_spaces",
    title: "List memory spaces",
    description:
      "Discover the company-memory projects visible to the signed-in person. Call this before selecting a project for another tool.",
    group: "Discover",
    permission: "read-only",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    resultExample: { active_project_id: "prj_checkout", spaces: [{ project_id: "prj_checkout", name: "Checkout" }] },
  },
  {
    name: "ask_orgmemory",
    title: "Ask company memory",
    description:
      "Answer a question from current, permission-scoped company memory and return the source citations behind the answer.",
    group: "Investigate",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: {
        question: { type: "string", minLength: 3, maxLength: 4000 },
        ...optionalProject,
        scope: { type: "string", enum: ["workspace", "project"] },
      },
      required: ["question"],
      additionalProperties: false,
    },
    resultExample: {
      answer_sufficient: true,
      scope: "workspace",
      likely_cause: "Worker concurrency exceeded the shared connection-pool cap.",
      memory_units: [{ memory_id: "mem_128", type: "incident", subject: "Checkout timeouts" }],
      safe_actions: ["Compare active workers with the configured cap."],
      approval_required: ["Restart the production pool."],
      evidence: [{ title: "Incident #128", type: "incident" }],
    },
  },
  {
    name: "get_orgmemory_briefing",
    title: "Brief me before I act",
    description:
      "Answer an intent rather than a question: what this company already knows about a change you are about to make, anywhere on the web. Returns constraining decisions, prior incidents, blast radius, the established procedure, and whether a person must approve first.",
    group: "Brief",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: {
        task: { type: "string", minLength: 3, maxLength: 2000 },
        service: { type: "string", maxLength: 120 },
        ...optionalProject,
        surface: { type: "string", maxLength: 64 },
      },
      required: ["task"],
      additionalProperties: false,
    },
    resultExample: {
      briefing_id: "ctx_9f21",
      verdict: "requires_approval",
      headline: "This changes production state for payments. Get an explicit human decision.",
      prior_incidents: [{ memory_id: "mem_128", subject: "payments outage: pool exhaustion" }],
      constraints: [{ memory_id: "mem_144", subject: "cap payments worker concurrency" }],
      blast_radius: [{ memory_id: "mem_151", subject: "payments shares the PostgreSQL cluster" }],
      requires_approval: ["This request involves restarting. A person has to agree before it happens."],
    },
  },
  {
    name: "record_orgmemory_outcome",
    title: "Report what happened",
    description:
      "Close the loop a briefing opened: what you did and whether it worked. Appends to the outcome ledger and changes no company memory, which is why it needs no approval while proposing a memory does.",
    group: "Report",
    permission: "ledger-append",
    inputSchema: {
      type: "object",
      properties: {
        briefing_id: { type: "string" },
        action: { type: "string", minLength: 2, maxLength: 64 },
        outcome: {
          type: "string",
          enum: ["succeeded", "failed", "partial", "abandoned", "unknown"],
        },
        target: { type: "string", maxLength: 200 },
        surface: { type: "string", maxLength: 64 },
        reason: { type: "string", maxLength: 2000 },
      },
      required: ["briefing_id", "action", "outcome"],
      additionalProperties: false,
    },
    resultExample: {
      briefing_id: "ctx_9f21",
      outcome: "succeeded",
      changed_company_memory: false,
      next_step: "Propose anything that should become durable knowledge.",
    },
  },
  {
    name: "inspect_orgmemory_changes",
    title: "Inspect memory changes",
    description:
      "Inspect source-backed additions, updates, invalidations, conflicts, and downstream artifacts that need review.",
    group: "Investigate",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: { ...optionalProject, limit: { type: "integer", minimum: 1, maximum: 50, default: 10 } },
      additionalProperties: false,
    },
    resultExample: { change_count: 2, changes: [{ added: 3, conflicts: 1, review_status: "needs_review" }] },
  },
  {
    name: "search_orgmemory",
    title: "Search organizational memory",
    description:
      "Search verified memories, incidents, decisions, dependencies, procedures, policies, and historical context. Prefer this structured retrieval over scraping the UI.",
    group: "Retrieve",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", maxLength: 400 },
        ...optionalProject,
        type: { type: "string", description: "Optional memory kind such as incident or decision." },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
      },
      additionalProperties: false,
    },
    resultExample: { result_count: 4, results: [{ memory_id: "mem_128", type: "incident", subject: "Checkout timeouts" }] },
  },
  {
    name: "get_orgmemory_memory",
    title: "Get one memory",
    description: "Retrieve one memory by ID with its content, scope, provenance count, confidence, and validity window.",
    group: "Retrieve",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: { memory_id: { type: "string" } },
      required: ["memory_id"],
      additionalProperties: false,
    },
    resultExample: { memory_id: "mem_128", type: "incident", subject: "Checkout timeouts", sources: 3 },
  },
  {
    name: "get_orgmemory_related_memories",
    title: "Follow memory relationships",
    description: "Follow updates, contradictions, supporting evidence, derived memories, and same-subject history around one memory.",
    group: "Investigate",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: { memory_id: { type: "string" } },
      required: ["memory_id"],
      additionalProperties: false,
    },
    resultExample: { related_count: 3, related: [{ relationship: "UPDATES", memory_id: "mem_127" }] },
  },
  {
    name: "get_orgmemory_incidents",
    title: "Retrieve previous incidents",
    description: "Retrieve previous incident memories and compare a current symptom with what happened before.",
    group: "Retrieve",
    permission: "read-only",
    inputSchema: { type: "object", properties: { service: { type: "string" }, ...optionalProject }, additionalProperties: false },
    resultExample: { service: "checkout", incident_count: 3, incidents: [{ subject: "Connection-pool exhaustion" }] },
  },
  {
    name: "get_orgmemory_service_context",
    title: "Assemble service context",
    description: "Assemble current facts, decisions, incidents, dependencies, owners, procedures, and policies for a service.",
    group: "Investigate",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: { service: { type: "string" }, ...optionalProject },
      required: ["service"],
      additionalProperties: false,
    },
    resultExample: { space_count: 2, spaces: [{ service: "checkout", incidents: 3, decisions: 2 }] },
  },
  {
    name: "get_orgmemory_dependencies",
    title: "Inspect service dependencies",
    description: "Retrieve remembered service relationships for dependency and blast-radius reasoning without inventing unknown links.",
    group: "Investigate",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: { service: { type: "string" }, ...optionalProject },
      required: ["service"],
      additionalProperties: false,
    },
    resultExample: { service: "checkout", dependency_count: 4, dependencies: [{ subject: "PostgreSQL" }] },
  },
  {
    name: "get_orgmemory_decisions",
    title: "Retrieve architecture decisions",
    description: "Retrieve current architecture and operational decisions for a project or across authorized memory spaces.",
    group: "Retrieve",
    permission: "read-only",
    inputSchema: {
      type: "object",
      properties: { ...optionalProject, limit: { type: "integer", minimum: 1, maximum: 50, default: 10 } },
      additionalProperties: false,
    },
    resultExample: { decision_count: 2, decisions: [{ subject: "Cap worker concurrency" }] },
  },
  {
    name: "propose_repository_refresh",
    title: "Propose a repository refresh",
    description: "Request fresh repository evidence. The request waits for a person before any ingest is queued.",
    group: "Propose",
    permission: "approval-required",
    inputSchema: {
      type: "object",
      properties: { ...optionalProject, reason: { type: "string", minLength: 5, maxLength: 800 } },
      required: ["reason"],
      additionalProperties: false,
    },
    resultExample: { status: "pending_approval", next_step: "A person must approve or deny this request." },
  },
  {
    name: "list_orgmemory_approvals",
    title: "List approval requests",
    description: "List repository refresh requests that are waiting for a human decision, including requester and reason.",
    group: "Discover",
    permission: "read-only",
    inputSchema: { type: "object", properties: { ...optionalProject }, additionalProperties: false },
    resultExample: { pending_count: 1, approvals: [{ repository: "acme/checkout", status: "pending_approval" }] },
  },
  {
    name: "resolve_orgmemory_approval",
    title: "Record an approval decision",
    description: "Admin-only. Record a human decision on a pending refresh request; this is never permission for an agent to decide by itself.",
    group: "Decide",
    permission: "admin-decision",
    inputSchema: {
      type: "object",
      properties: { refresh_request_id: { type: "string" }, approved: { type: "boolean" } },
      required: ["refresh_request_id", "approved"],
      additionalProperties: false,
    },
    resultExample: { status: "queued", next_step: "Repository ingest queued." },
  },
  {
    name: "propose_orgmemory_memory",
    title: "Propose verified memory",
    description: "Propose verified organizational knowledge. Nothing enters durable memory until a person approves it.",
    group: "Propose",
    permission: "approval-required",
    inputSchema: {
      type: "object",
      properties: {
        subject: { type: "string", minLength: 3, maxLength: 300 },
        content: { type: "string", minLength: 3, maxLength: 4000 },
        kind: { type: "string", default: "fact" },
        service: { type: "string" },
        ...optionalProject,
        reason: { type: "string", maxLength: 800 },
      },
      required: ["subject", "content"],
      additionalProperties: false,
    },
    resultExample: { proposal_id: "mprop_1", status: "pending_approval" },
  },
  {
    name: "propose_orgmemory_incident",
    title: "Propose a verified incident",
    description: "Propose an incident only after its diagnosis is verified. The proposal waits for explicit human approval.",
    group: "Propose",
    permission: "approval-required",
    inputSchema: {
      type: "object",
      properties: {
        subject: { type: "string", minLength: 3, maxLength: 300 },
        content: { type: "string", minLength: 3, maxLength: 4000 },
        service: { type: "string" },
        ...optionalProject,
        reason: { type: "string", maxLength: 800 },
      },
      required: ["subject", "content"],
      additionalProperties: false,
    },
    resultExample: { kind: "incident", status: "pending_approval" },
  },
  {
    name: "propose_orgmemory_decision",
    title: "Propose a verified decision",
    description: "Propose a decision only after it was actually made. The proposal waits for explicit human approval.",
    group: "Propose",
    permission: "approval-required",
    inputSchema: {
      type: "object",
      properties: {
        subject: { type: "string", minLength: 3, maxLength: 300 },
        content: { type: "string", minLength: 3, maxLength: 4000 },
        service: { type: "string" },
        ...optionalProject,
        reason: { type: "string", maxLength: 800 },
      },
      required: ["subject", "content"],
      additionalProperties: false,
    },
    resultExample: { kind: "decision", status: "pending_approval" },
  },
  {
    name: "list_orgmemory_proposals",
    title: "List memory proposals",
    description: "List proposed memories waiting for a human decision, with requester, evidence reason, and current status.",
    group: "Discover",
    permission: "read-only",
    inputSchema: { type: "object", properties: { ...optionalProject }, additionalProperties: false },
    resultExample: { pending_count: 2, proposals: [{ kind: "incident", status: "pending_approval" }] },
  },
  {
    name: "resolve_orgmemory_proposal",
    title: "Record a memory decision",
    description: "Admin-only. Record a person's approve/deny decision; approval is the only path into durable company memory.",
    group: "Decide",
    permission: "admin-decision",
    inputSchema: {
      type: "object",
      properties: { proposal_id: { type: "string" }, approved: { type: "boolean" } },
      required: ["proposal_id", "approved"],
      additionalProperties: false,
    },
    resultExample: { status: "approved", memory_id: "mem_new" },
  },
  /* The organizational operations share their definitions with the executable
     map in orgTools.ts. Restating name, description, and schema here by hand is
     how a catalog drifts from the tools it claims to document. */
  ...Object.values(ORG_TOOLS).map((tool) => ({
    name: tool.name as WebMCPToolName,
    title: tool.title,
    description: tool.description,
    group: (tool.kind === "read" ? "Operate" : "Propose") as WebMCPToolCatalogEntry["group"],
    permission: (tool.kind === "read"
      ? "read-only"
      : "approval-required") as WebMCPToolCatalogEntry["permission"],
    manualEquivalent: tool.manualEquivalent,
    inputSchema: tool.inputSchema,
    resultExample: {},
  })),
];

export const WEBMCP_READ_TOOL_COUNT = WEBMCP_TOOL_CATALOG.filter(
  (tool) => tool.permission === "read-only",
).length;

/* Appending to the outcome ledger is counted apart from both tiers on purpose.
   Calling it "read-only" would understate it and calling it "governed" would
   overstate it: it writes, and it still cannot put a fact into company memory. */
export const WEBMCP_LEDGER_TOOL_COUNT = WEBMCP_TOOL_CATALOG.filter(
  (tool) => tool.permission === "ledger-append",
).length;

export const WEBMCP_GOVERNED_TOOL_COUNT =
  WEBMCP_TOOL_CATALOG.length - WEBMCP_READ_TOOL_COUNT - WEBMCP_LEDGER_TOOL_COUNT;
