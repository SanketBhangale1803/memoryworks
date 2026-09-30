import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const webmcp = readFileSync(new URL("../lib/webmcp.ts", import.meta.url), "utf8");
const hook = readFileSync(
  new URL("../hooks/useMemoryWorksTools.ts", import.meta.url),
  "utf8",
);
const chat = readFileSync(
  new URL("../components/WorkspaceChat.tsx", import.meta.url),
  "utf8",
);
const types = readFileSync(new URL("../types/webmcp.d.ts", import.meta.url), "utf8");
const tools = readFileSync(new URL("../hooks/useWorkspaceTools.ts", import.meta.url), "utf8");
const agentTurn = readFileSync(new URL("../components/AgentTurn.tsx", import.meta.url), "utf8");
const approvals = readFileSync(new URL("../app/approvals/page.tsx", import.meta.url), "utf8");
const orgTools = readFileSync(new URL("../lib/orgTools.ts", import.meta.url), "utf8");
const catalog = readFileSync(new URL("../lib/webmcpCatalog.ts", import.meta.url), "utf8");

const compilerOptions = {
  module: ts.ModuleKind.ESNext,
  target: ts.ScriptTarget.ES2022,
};

function dataModule(source) {
  const output = ts.transpileModule(source, { compilerOptions }).outputText;
  return `data:text/javascript;base64,${Buffer.from(output).toString("base64")}`;
}

/* Modules load here as data URLs, which have no notion of the "@/" path alias.
   The organizational tool map is compiled the same way and linked in by URL, so
   registration is exercised against the real definitions rather than a stub;
   only the HTTP transport underneath it is replaced. */
const orgToolsUrl = dataModule(
  orgTools
    .replace(
      /^import \{ api, API \} from "@\/lib\/api";$/m,
      "const api = async () => ({ spaces: [], plans: [], results: [] }); const API = '';",
    )
    .replace(
      /import \{[^}]*\} from "@\/lib\/demoMemory";/m,
      "const OFFLINE_DEMO_MODE = false; const demoOrgRequest = async () => ({}); const demoAgentSession = async () => ({}); const demoFollowups = async () => ({ suggestions: [] });",
    ),
);
const webmcpStandalone = webmcp.replace('"@/lib/orgTools"', JSON.stringify(orgToolsUrl));

const compiled = ts.transpileModule(webmcpStandalone, { compilerOptions }).outputText;
const runtime = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("the authenticated workspace registers browser-native WebMCP tools", () => {
  assert.match(types, /interface Document/);
  assert.match(types, /modelContext\?: WebMCPModelContext/);
  assert.match(webmcp, /document\.modelContext\.registerTool|modelContext\.registerTool/);

  for (const tool of [
    "list_orgmemory_spaces",
    "ask_orgmemory",
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
  ]) {
    assert.match(webmcp, new RegExp(`name: "${tool}"`));
  }

  assert.match(webmcp, /readOnlyHint: true/);
  assert.match(webmcp, /destructiveHint: false/);
  assert.match(webmcp, /openWorldHint: false/);
  assert.match(webmcp, /additionalProperties: false/g);
});

test("approval decisions stay inside the human boundary", () => {
  // The listing tool is read-only and scoped; the resolving tool never runs a
  // refresh itself and says so where an agent reads it.
  assert.match(
    webmcp,
    /List repository refresh requests that are waiting for a human approval decision/,
  );
  assert.match(
    webmcp,
    /This tool must only be used when the signed-in person has actually decided\./,
  );
  assert.match(webmcp, /annotations: READ_ONLY/);
  assert.match(webmcp, /APPROVAL_REQUIRED_WRITE/);
});

test("WebMCP registration is feature-detected and cleaned up with page lifecycle", () => {
  assert.match(webmcp, /!document\.modelContext/);
  assert.match(webmcp, /new AbortController\(\)/);
  assert.match(webmcp, /signal: controller\.signal/);
  assert.match(webmcp, /dispose: \(\) => controller\.abort\(\)/);
  assert.match(hook, /registration\.dispose\(\)/);
  assert.match(hook, /setStatus\(registration\.supported \? "ready" : "unsupported"\)/);
});

test("WebMCP activity is sourced from real tool execution and persisted as a bounded trace", () => {
  assert.match(webmcp, /startedAt: new Date\(started\)\.toISOString\(\)/);
  assert.match(webmcp, /durationMs: finished - started/);
  assert.match(webmcp, /resultMetadata\(value\)/);
  assert.match(webmcp, /summarizeInput\(input\)/);
  assert.match(hook, /orgmemory\.webmcp-activity/);
  assert.match(hook, /CustomEvent\("orgmemory:webmcp-activity"/);
  assert.match(hook, /slice\(-24\)/);
});

test("Agent mode shows the steps the agent really took", () => {
  // Steps come from the streamed session, not a script, and durations are the
  // server's measurements — never invented on the page.
  assert.match(chat, /orgApi\.askStream\(question, spaceIds\(\)/);
  assert.match(agentTurn, /ms: step\.duration_ms \|\| 0/);
  assert.doesNotMatch(agentTurn, /Math\.random|setInterval/);
  // A proposed change stops at a person, in the conversation that made it.
  assert.match(agentTurn, /plan\.status === "pending_approval"/);
});

test("organizational operations are registered as real tools, with writes gated", () => {
  assert.match(webmcp, /Object\.values\(ORG_TOOLS\)\.map/);
  assert.match(webmcp, /tool\.kind === "read" \? READ_ONLY : APPROVAL_REQUIRED_WRITE/);
  for (const tool of [
    "get_orgmemory_project_context",
    "get_orgmemory_reasoning_chain",
    "get_orgmemory_dependency_graph",
    "find_orgmemory_blockers",
    "find_orgmemory_conflicts",
    "get_orgmemory_readiness",
    "get_orgmemory_provenance",
    "propose_orgmemory_changes",
  ]) {
    assert.match(orgTools, new RegExp(`name: "${tool}"`));
    assert.ok(runtime.MEMORYWORKS_TOOLS.includes(tool), `${tool} missing from the name list`);
  }
  // Nothing may approve its own plan: approval is a person, not a tool.
  assert.doesNotMatch(orgTools, /name: "approve_/);
});

test("the tool catalog stays derived from the executable definitions", () => {
  assert.match(catalog, /Structured result example|resultExample/);
  assert.match(catalog, /Object\.values\(ORG_TOOLS\)\.map/);
});

test("browser-agent questions reuse the secure API and update the visible conversation", () => {
  assert.match(chat, /surface: "web" \| "webmcp"/);
  assert.match(chat, /askMemory\(question, projectId, "webmcp", requestedScope\)/);
  // The agent's question lands in the same chat history a person sees.
  assert.match(chat, /beginTurn\(question, "ask", target, turnScope\)/);
  assert.match(webmcp, /source citations/);
  assert.match(webmcp, /options\.ask\(question, project\.id, scope\)/);
  assert.match(webmcp, /likely_cause: answer\.likely_cause/);
  assert.match(webmcp, /memory_units: \(answer\.memory_units \|\| \[\]\)\.map\(compactUnit\)/);
  assert.match(webmcp, /safe_actions: answer\.safe_actions/);
  assert.match(webmcp, /approval_required: answer\.approval_required/);
  assert.match(webmcp, /retrieval_trace: answer\.retrieval_trace/);
  // Registered quietly: the page never advertises the transport to a person.
  assert.match(chat, /useWorkspaceTools\(/);
  assert.doesNotMatch(chat, /WebMCP/);
});

test("approvals are decided on one authorized path, by a person or an agent", () => {
  // The browser tools resolve through the same endpoint the Approvals inbox
  // uses, and only admins are handed the decision tools at all.
  assert.match(tools, /repository-refresh-requests\/\$\{encodeURIComponent\(requestId\)\}\/resolve/);
  assert.match(tools, /canResolveApprovals: isAdmin/);
  assert.match(tools, /canResolveProposals: isAdmin/);
  assert.match(approvals, /\/api\/repository-refresh-requests/);
  assert.match(approvals, /\/api\/memory\/proposals/);
  assert.match(approvals, /orgApi\.approvePlan/);
  assert.match(approvals, /With an admin/);
});

test("employees cannot receive the browser-agent approval-decision tools", async () => {
  const registered = new Map();
  const previousDocument = globalThis.document;
  globalThis.document = {
    modelContext: {
      async registerTool(tool, { signal }) {
        registered.set(tool.name, tool);
        signal.addEventListener("abort", () => registered.delete(tool.name), { once: true });
      },
    },
  };
  try {
    const registration = await runtime.registerMemoryWorksTools({
      spaces: [{ id: "prj_demo", name: "Demo", repository: "acme/demo" }],
      getActiveProjectId: () => "prj_demo",
      async ask() { return { answer: "ok", answer_sufficient: true, answer_scope: "project", evidence: [] }; },
      async inspectChanges() { return []; },
      async brief() { return { briefing_id: "ctx_1", task: "t", verdict: "no_memory", headline: "h", must_read: [], constraints: [], prior_incidents: [], blast_radius: [], procedures: [], precedents: [], requires_approval: [], safe_actions: [], open_questions: [], memory_count: 0 }; },
      async recordOutcome() { return { briefing_id: "ctx_1", action: { id: "act_1", action_type: "a" }, outcome: { id: "out_1", outcome: "unknown", reward: 0 }, recorded: true }; },
      async searchMemory() { return []; },
      async getMemory() { throw new Error("not found"); },
      async getRelatedMemories() { return []; },
      async listIncidents() { return []; },
      async getServiceContext() { return []; },
      async listDecisions() { return []; },
      async proposeMemory() { throw new Error("should not be called in this test"); },
      async listProposals() { return []; },
      canResolveProposals: false,
      async proposeRepositoryRefresh() { return { id: "req_1", project_id: "prj_demo", repository: "acme/demo", reason: "stale", status: "pending_approval", requested_at: "" }; },
      async listApprovals() { return []; },
      canResolveApprovals: false,
    });
    // Everything except the two human-decision tools, which are admin-only.
    assert.equal(registration.toolCount, runtime.MEMORYWORKS_TOOLS.length - 2);
    assert.equal(registered.has("resolve_orgmemory_approval"), false);
    assert.equal(registered.has("resolve_orgmemory_proposal"), false);
    registration.dispose();
  } finally {
    globalThis.document = previousDocument;
  }
});

test("WebMCP validates project access before calling scoped backend endpoints", () => {
  assert.match(webmcp, /spaces\.find\(\(space\) => space\.id === requested\)/);
  assert.match(webmcp, /Choose a project_id returned by list_orgmemory_spaces/);
  assert.match(tools, /encodeURIComponent\(projectId\)/);
  assert.match(tools, /from "@\/lib\/api"/);
});

test("the real WebMCP implementation registers, invokes, and unregisters all tools", async () => {
  const registered = new Map();
  const previousDocument = globalThis.document;
  globalThis.document = {
    modelContext: {
      async registerTool(tool, { signal }) {
        registered.set(tool.name, tool);
        signal.addEventListener("abort", () => registered.delete(tool.name), { once: true });
      },
    },
  };

  const calls = [];
  const unit = (overrides = {}) => ({
    id: "mem_1",
    project_id: "prj_demo",
    project_name: "Demo",
    type: "fact",
    subject: "payments pool",
    content: "Payments depends on PostgreSQL pool limits.",
    scope: { service: "payments" },
    confidence: 0.9,
    source_ids: ["src_1"],
    updated_at: "2026-08-26T12:00:00Z",
    score: 3,
    ...overrides,
  });
  try {
    const registration = await runtime.registerMemoryWorksTools({
      spaces: [{ id: "prj_demo", name: "Demo", repository: "acme/demo" }],
      getActiveProjectId: () => "prj_demo",
      async ask(question, projectId, scope) {
        calls.push({ kind: "ask", question, projectId, scope });
        return {
          answer: "Checkout is owned by Platform.",
          answer_sufficient: true,
          answer_scope: "company_memory",
          resolved_subject: "checkout",
          searched_sources: 2,
          evidence: [
            {
              source_title: "Service owners",
              source_type: "document",
              source_url: "https://example.test/owners",
            },
          ],
        };
      },
      async inspectChanges(projectId, limit) {
        calls.push({ kind: "changes", projectId, limit });
        return [
          {
            id: "chg_1",
            source_id: "src_1",
            created_at: "2026-08-25T12:00:00Z",
            review_status: "needs_review",
            added: ["mem_1"],
            conflicts: ["mem_2"],
          },
        ];
      },
      async brief({ task, service }) {
        calls.push({ kind: "brief", task, service });
        return {
          briefing_id: "ctx_1",
          task,
          service: service || null,
          verdict: "requires_approval",
          headline: "Get a human decision first.",
          consequential_action: "restarting",
          must_read: [{ memory_id: "mem_1", type: "incident", subject: "payments pool", content: "", why_it_matters: "x" }],
          constraints: [],
          prior_incidents: [{ memory_id: "mem_1", type: "incident", subject: "payments pool", content: "", why_it_matters: "x" }],
          blast_radius: [],
          procedures: [],
          precedents: [],
          requires_approval: ["A person has to agree before it happens."],
          safe_actions: [],
          open_questions: [],
          memory_count: 1,
        };
      },
      async recordOutcome({ briefingId, action, outcome }) {
        calls.push({ kind: "outcome", briefingId, action, outcome });
        return {
          briefing_id: briefingId,
          action: { id: "act_1", action_type: action },
          outcome: { id: "out_1", outcome, reward: 1 },
          recorded: true,
        };
      },
      async searchMemory(projectId, query, type, limit) {
        calls.push({ kind: "search", projectId, query, type, limit });
        return [unit({ type: type || "fact" })];
      },
      async getMemory(memoryId) {
        calls.push({ kind: "get", memoryId });
        return unit();
      },
      async getRelatedMemories(memoryId) {
        calls.push({ kind: "related", memoryId });
        return [{ relationship: "UPDATES", memory: unit({ id: "mem_0" }) }];
      },
      async listIncidents(projectId, service) {
        calls.push({ kind: "incidents", projectId, service });
        return [unit({ type: "incident", subject: "payments outage" })];
      },
      async getServiceContext(service) {
        calls.push({ kind: "service", service });
        return [
          {
            project_id: "prj_demo",
            project_name: "Demo",
            profile: {
              current_facts: [unit()],
              decisions: [unit({ type: "decision" })],
              incidents: [unit({ type: "incident" })],
              dependencies: [unit({ type: "dependency" })],
            },
          },
        ];
      },
      async listDecisions(projectId, limit) {
        calls.push({ kind: "decisions", projectId, limit });
        return [unit({ type: "decision", subject: "cap worker concurrency" })];
      },
      async proposeMemory(input) {
        calls.push({ kind: "propose", input });
        return {
          id: "mprop_1",
          project_id: input.projectId,
          kind: input.kind,
          subject: input.subject,
          content: input.content,
          status: "pending_approval",
          requested_at: "2026-08-26T12:00:00Z",
          requested_by_name: "Demo User",
        };
      },
      async listProposals() {
        calls.push({ kind: "listProposals" });
        return [
          {
            id: "mprop_1",
            project_id: "prj_demo",
            kind: "incident",
            subject: "payments outage recurrence",
            content: "Verified against monitoring.",
            status: "pending_approval",
            requested_at: "2026-08-26T12:00:00Z",
            requested_by_name: "Demo User",
          },
        ];
      },
      canResolveProposals: true,
      async resolveProposal(proposalId, approved) {
        calls.push({ kind: "resolveProposal", proposalId, approved });
        return {
          id: proposalId,
          project_id: "prj_demo",
          kind: "incident",
          subject: "payments outage recurrence",
          status: approved ? "approved" : "denied",
          memory_id: approved ? "mem_new" : "",
        };
      },
      async proposeRepositoryRefresh(projectId, reason) {
        calls.push({ kind: "refresh", projectId, reason });
        return {
          id: "refresh_1",
          project_id: projectId,
          repository: "https://github.com/acme/demo.git",
          reason,
          status: "pending_approval",
          requested_at: "2026-08-26T12:00:00Z",
        };
      },
      async listApprovals(projectId) {
        calls.push({ kind: "approvals", projectId });
        return [
          {
            id: "refresh_1",
            project_id: "prj_demo",
            project_name: "Demo",
            repository: "acme/demo",
            reason: "Evidence is stale.",
            status: "pending_approval",
            requested_at: "2026-08-26T12:00:00Z",
            requested_by_id: "usr_employee",
            requested_by_name: "Team Employee",
            requested_by_email: "employee@example.com",
          },
        ];
      },
      canResolveApprovals: true,
      async resolveApproval(requestId, approved) {
        calls.push({ kind: "resolve", requestId, approved });
        return {
          id: requestId,
          project_id: "prj_demo",
          project_name: "Demo",
          repository: "acme/demo",
          reason: "The latest commit evidence is stale.",
          status: approved ? "queued" : "denied",
          requested_at: "2026-08-26T12:00:00Z",
          requested_by_name: "Team Employee",
        };
      },
    });

    assert.equal(registration.supported, true);
    assert.equal(registration.toolCount, runtime.MEMORYWORKS_TOOLS.length);
    // The exported list is the documented surface; registration order is an
    // implementation detail, so the two are compared as sets.
    assert.deepEqual(
      [...registered.keys()].sort(),
      [...runtime.MEMORYWORKS_TOOLS].sort(),
    );
    // The original memory-tool family must all still be there.
    assert.deepEqual([...registered.keys()].slice(0, 20).sort(), [
      "ask_orgmemory",
      "get_orgmemory_briefing",
      "get_orgmemory_decisions",
      "get_orgmemory_dependencies",
      "get_orgmemory_incidents",
      "get_orgmemory_memory",
      "get_orgmemory_related_memories",
        "get_orgmemory_service_context",
      "inspect_orgmemory_changes",
      "list_orgmemory_approvals",
      "list_orgmemory_proposals",
      "list_orgmemory_spaces",
      "propose_orgmemory_decision",
      "propose_orgmemory_incident",
      "propose_orgmemory_memory",
      "propose_repository_refresh",
      "record_orgmemory_outcome",
      "resolve_orgmemory_approval",
      "resolve_orgmemory_proposal",
      "search_orgmemory",
    ]);

    // A briefing answers an intent, and its id is what closes the loop later.
    const briefing = await registered.get("get_orgmemory_briefing").execute({
      task: "restart the payments connection pool",
      service: "payments",
    });
    assert.equal(briefing.structuredContent.verdict, "requires_approval");
    assert.equal(briefing.structuredContent.briefing_id, "ctx_1");
    assert.match(briefing.content[0].text, /Human approval required/);

    const reported = await registered.get("record_orgmemory_outcome").execute({
      briefing_id: "ctx_1",
      action: "followed_procedure",
      outcome: "succeeded",
    });
    // The ledger is not company memory, and the tool has to keep saying so.
    assert.equal(reported.structuredContent.changed_company_memory, false);
    assert.equal(reported.structuredContent.outcome, "succeeded");
    await assert.rejects(
      () => registered.get("record_orgmemory_outcome").execute({
        briefing_id: "ctx_1",
        action: "guessed",
        outcome: "probably_fine",
      }),
      /outcome must be one of/,
    );

    const spaces = await registered.get("list_orgmemory_spaces").execute({});
    assert.equal(spaces.structuredContent.spaces[0].project_id, "prj_demo");

    const answer = await registered.get("ask_orgmemory").execute({
      question: "Who owns checkout?",
      project_id: "prj_demo",
      scope: "project",
    });
    assert.equal(answer.structuredContent.answer_sufficient, true);
    assert.equal(answer.structuredContent.evidence[0].title, "Service owners");

    const changes = await registered.get("inspect_orgmemory_changes").execute({
      project_id: "prj_demo",
      limit: 500,
    });
    assert.equal(changes.structuredContent.changes[0].conflicts, 1);

    // Read-only organizational memory surface.
    const search = await registered.get("search_orgmemory").execute({
      query: "payments",
      type: "incident",
    });
    assert.equal(search.structuredContent.results[0].subject, "payments pool");

    const memory = await registered.get("get_orgmemory_memory").execute({ memory_id: "mem_1" });
    assert.equal(memory.structuredContent.memory_id, "mem_1");

    const related = await registered
      .get("get_orgmemory_related_memories")
      .execute({ memory_id: "mem_1" });
    assert.equal(related.structuredContent.related[0].relationship, "UPDATES");

    const incidents = await registered
      .get("get_orgmemory_incidents")
      .execute({ service: "payments" });
    assert.equal(incidents.structuredContent.incident_count, 1);

    const context = await registered
      .get("get_orgmemory_service_context")
      .execute({ service: "payments" });
    assert.equal(context.structuredContent.space_count, 1);
    assert.equal(context.structuredContent.spaces[0].decisions.length, 1);

    const decisions = await registered.get("get_orgmemory_decisions").execute({});
    assert.equal(decisions.structuredContent.decisions[0].type, "decision");

    // Approval-gated writes: proposing never persists anything by itself.
    const proposal = await registered.get("propose_orgmemory_memory").execute({
      subject: "payments pool cap",
      content: "Worker concurrency is capped to protect the pool.",
      kind: "fact",
      service: "payments",
      reason: "Verified against the deployment manifest.",
    });
    assert.equal(proposal.structuredContent.status, "pending_approval");
    assert.match(proposal.structuredContent.next_step, /person must approve/);

    await assert.rejects(
      registered.get("propose_orgmemory_memory").execute({
        subject: "bad",
        content: "bad",
        kind: "not_a_kind",
      }),
      /kind must be one of/,
    );

    const incidentProposal = await registered
      .get("propose_orgmemory_incident")
      .execute({ subject: "payments outage recurrence", content: "Verified diagnosis.", service: "payments" });
    assert.equal(incidentProposal.structuredContent.kind, "incident");
    assert.match(incidentProposal.structuredContent.next_step, /approve or deny/);

    const decisionProposal = await registered
      .get("propose_orgmemory_decision")
      .execute({ subject: "cap worker concurrency", content: "Decided by the platform team during the architecture review." });
    assert.equal(decisionProposal.structuredContent.kind, "decision");

    const proposals = await registered.get("list_orgmemory_proposals").execute({});
    assert.equal(proposals.structuredContent.pending_count, 1);
    assert.equal(proposals.structuredContent.proposals[0].requested_by_name, "Demo User");

    const resolvedProposal = await registered
      .get("resolve_orgmemory_proposal")
      .execute({ proposal_id: "mprop_1", approved: true });
    assert.equal(resolvedProposal.structuredContent.status, "approved");
    assert.equal(resolvedProposal.structuredContent.memory_id, "mem_new");

    const refresh = await registered.get("propose_repository_refresh").execute({
      project_id: "prj_demo",
      reason: "The latest commit evidence is stale.",
    });
    assert.equal(refresh.structuredContent.status, "pending_approval");

    // Approval listing is project-filtered against the registered spaces, so a
    // pending request in another space never leaks through an agent tool.
    const approvals = await registered.get("list_orgmemory_approvals").execute({
      project_id: "prj_demo",
    });
    assert.equal(approvals.structuredContent.pending_count, 1);
    assert.equal(approvals.structuredContent.approvals[0].requested_by_name, "Team Employee");

    const decision = await registered.get("resolve_orgmemory_approval").execute({
      refresh_request_id: "refresh_1",
      approved: true,
    });
    assert.equal(decision.structuredContent.status, "queued");
    assert.match(decision.structuredContent.next_step, /queued/);

    await assert.rejects(
      registered.get("list_orgmemory_approvals").execute({ project_id: "prj_not_authorized" }),
      /Choose a project_id returned by list_orgmemory_spaces/,
    );
    await assert.rejects(
      registered.get("resolve_orgmemory_approval").execute({ refresh_request_id: "", approved: true }),
      /refresh_request_id is required/,
    );

    assert.deepEqual(
      calls.filter((call) => call.kind === "search" || call.kind === "get" || call.kind === "related" || call.kind === "incidents" || call.kind === "service" || call.kind === "decisions" || call.kind === "propose" || call.kind === "listProposals" || call.kind === "resolveProposal"),
      [
        { kind: "search", projectId: "", query: "payments", type: "incident", limit: 10 },
        { kind: "get", memoryId: "mem_1" },
        { kind: "related", memoryId: "mem_1" },
        { kind: "incidents", projectId: "", service: "payments" },
        { kind: "service", service: "payments" },
        { kind: "decisions", projectId: "", limit: 10 },
        {
          kind: "propose",
          input: {
            projectId: "prj_demo",
            kind: "fact",
            subject: "payments pool cap",
            content: "Worker concurrency is capped to protect the pool.",
            service: "payments",
            reason: "Verified against the deployment manifest.",
          },
        },
        {
          kind: "propose",
          input: {
            projectId: "prj_demo",
            kind: "incident",
            subject: "payments outage recurrence",
            content: "Verified diagnosis.",
            service: "payments",
            reason: undefined,
          },
        },
        {
          kind: "propose",
          input: {
            projectId: "prj_demo",
            kind: "decision",
            subject: "cap worker concurrency",
            content: "Decided by the platform team during the architecture review.",
            service: undefined,
            reason: undefined,
          },
        },
        { kind: "listProposals" },
        { kind: "resolveProposal", proposalId: "mprop_1", approved: true },
      ],
    );

    await assert.rejects(
      registered.get("ask_orgmemory").execute({
        question: "Who owns checkout?",
        project_id: "prj_not_authorized",
      }),
      /Choose a project_id returned by list_orgmemory_spaces/,
    );
    await assert.rejects(
      registered.get("search_orgmemory").execute({ query: "", type: "" }),
      /Provide a query or a memory type/,
    );

    registration.dispose();
    assert.equal(registered.size, 0);
  } finally {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});
