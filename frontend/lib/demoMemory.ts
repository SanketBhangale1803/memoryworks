/* Offline fixture transport.
 *
 * Set NEXT_PUBLIC_WEBMCP_OFFLINE=true to preview the product with no backend
 * at all (e.g. a fully static preview build): the tool surface runs against
 * this fixed, resettable in-browser workspace. The hosted deployment does NOT
 * use this — it runs the real API, real model, and real WebMCP session.
 */

export const OFFLINE_DEMO_MODE = process.env.NEXT_PUBLIC_WEBMCP_OFFLINE === "true";

const recordedAt = "2026-08-28T16:30:00.000Z";
const earlier = "2026-08-27T15:00:00.000Z";

const spaces = [
  { id: "space_product", name: "Product", memory_count: 3, memory_types: { decision: 2, goal: 1 }, open_tasks: 0, updated_at: recordedAt },
  { id: "space_engineering", name: "Engineering", memory_count: 4, memory_types: { decision: 2, status: 2 }, open_tasks: 1, updated_at: recordedAt },
  { id: "space_design", name: "Design", memory_count: 2, memory_types: { decision: 1, status: 1 }, open_tasks: 0, updated_at: earlier },
  { id: "space_security", name: "Security", memory_count: 3, memory_types: { policy: 1, decision: 1, status: 1 }, open_tasks: 1, updated_at: recordedAt },
  { id: "space_infrastructure", name: "Infrastructure", memory_count: 3, memory_types: { decision: 1, status: 2 }, open_tasks: 1, updated_at: earlier },
  { id: "space_launch", name: "Launch", memory_count: 3, memory_types: { goal: 1, status: 2 }, open_tasks: 1, updated_at: recordedAt },
  { id: "space_support", name: "Customer Support", memory_count: 2, memory_types: { procedure: 1, status: 1 }, open_tasks: 1, updated_at: earlier },
];

const memories = [
  {
    id: "mem_policy_review",
    space_id: "space_security",
    space_name: "Security",
    type: "policy",
    title: "External auth changes require a security review",
    content: "Any change to production authentication must complete security review before release.",
    confidence: 1,
    source_ids: ["src_security_policy"],
    updated_at: "2026-08-20T14:00:00.000Z",
  },
  {
    id: "mem_deploy_gate",
    space_id: "space_infrastructure",
    space_name: "Infrastructure",
    type: "decision",
    title: "Production deployment requires a completed security approval",
    content: "The deploy pipeline holds an OAuth release until the security task is complete.",
    confidence: 0.99,
    source_ids: ["src_pipeline_policy"],
    updated_at: "2026-08-22T12:00:00.000Z",
  },
  {
    id: "mem_engineering_hold",
    space_id: "space_engineering",
    space_name: "Engineering",
    type: "decision",
    title: "Production deploy waits for the security sign-off",
    content: "Engineering will not promote rc-14 until the recorded security gate is satisfied.",
    confidence: 0.98,
    source_ids: ["src_engineering_sync"],
    updated_at: "2026-08-26T17:15:00.000Z",
  },
  {
    id: "mem_security_open",
    space_id: "space_security",
    space_name: "Security",
    type: "status",
    title: "OAuth security review is still open in the security tracker",
    content: "The security tracker still marks the final OAuth review as open.",
    confidence: 0.97,
    source_ids: ["src_security_tracker"],
    updated_at: earlier,
  },
  {
    id: "mem_security_approved",
    space_id: "space_product",
    space_name: "Product",
    type: "decision",
    title: "OAuth security review approved for Friday release",
    content: "Security approved the OAuth launch in the Thursday go/no-go meeting with no further scope.",
    confidence: 0.99,
    source_ids: ["src_go_no_go"],
    updated_at: recordedAt,
  },
  {
    id: "mem_launch_goal",
    space_id: "space_launch",
    space_name: "Launch",
    type: "goal",
    title: "Checkout OAuth sign-in launches Friday 09:00 UTC",
    content: "Launch checkout OAuth sign-in after the release checklist resolves.",
    confidence: 1,
    source_ids: ["src_launch_plan"],
    updated_at: earlier,
  },
  {
    id: "mem_design_done",
    space_id: "space_design",
    space_name: "Design",
    type: "status",
    title: "Final sign-in interface approved",
    content: "Design approved the final interface and empty states.",
    confidence: 0.98,
    source_ids: ["src_design_review"],
    updated_at: earlier,
  },
  {
    id: "mem_rc_tested",
    space_id: "space_engineering",
    space_name: "Engineering",
    type: "status",
    title: "Backend release candidate rc-14 passed",
    content: "The OAuth backend release candidate passed the full release suite.",
    confidence: 0.99,
    source_ids: ["src_ci_rc14"],
    updated_at: earlier,
  },
  {
    id: "mem_support_gap",
    space_id: "space_support",
    space_name: "Customer Support",
    type: "status",
    title: "Support has not been briefed on rollback",
    content: "Customer Support still needs the OAuth rollback and incident response notes.",
    confidence: 0.96,
    source_ids: ["src_support_handoff"],
    updated_at: earlier,
  },
];

const baseTasks = [
  { id: "task_design", space_id: "space_design", space_name: "Design", title: "Approve final sign-in interface", description: "Final product design review", status: "done", owner: "Maya Chen", priority: "high", kind: "task", depends_on: [], source_memory_ids: ["mem_design_done"], updated_at: earlier },
  { id: "task_rc", space_id: "space_engineering", space_name: "Engineering", title: "Test backend release candidate rc-14", description: "Run the OAuth release suite", status: "done", owner: "Jon Bell", priority: "critical", kind: "task", depends_on: [], source_memory_ids: ["mem_rc_tested"], updated_at: earlier },
  { id: "task_security", space_id: "space_security", space_name: "Security", title: "Complete OAuth security approval", description: "Close the required security review", status: "open", owner: "Priya Shah", priority: "critical", kind: "task", depends_on: [], source_memory_ids: ["mem_security_open"], updated_at: earlier },
  { id: "task_deploy", space_id: "space_infrastructure", space_name: "Infrastructure", title: "Promote rc-14 to production", description: "Promote the tested release candidate", status: "blocked", owner: "Liam Ortiz", priority: "critical", kind: "task", depends_on: ["task_rc", "task_security"], source_memory_ids: ["mem_deploy_gate"], updated_at: earlier },
  { id: "task_launch", space_id: "space_launch", space_name: "Launch", title: "Launch checkout OAuth sign-in", description: "Enable the release for customers", status: "blocked", owner: "Nora Kim", priority: "critical", kind: "goal", depends_on: ["task_design", "task_deploy"], source_memory_ids: ["mem_launch_goal"], updated_at: earlier },
  { id: "task_support", space_id: "space_support", space_name: "Customer Support", title: "Brief support on OAuth rollback", description: "Share the rollback procedure", status: "open", owner: "Ava Morgan", priority: "normal", kind: "task", depends_on: [], source_memory_ids: ["mem_support_gap"], updated_at: earlier },
];

let tasks = structuredClone(baseTasks);
let conflictResolved = false;
let watch: any = null;
let planCounter = 0;
const plans = new Map<string, any>();

const memoryById = (id: string) => memories.find((memory) => memory.id === id) || memories[0];
const taskById = (id: string) => tasks.find((task) => task.id === id) || tasks[0];
const clone = <T,>(value: T): T => structuredClone(value);
const pause = () => new Promise((resolve) => setTimeout(resolve, 72));

function blocker() {
  const security = taskById("task_security");
  return {
    task: security,
    blocks: [taskById("task_deploy"), taskById("task_launch")],
    chain: [
      { id: "task_security", label: "Complete OAuth security approval", status: security.status, space_name: "Security" },
      { id: "task_deploy", label: "Promote rc-14 to production", status: "blocked", space_name: "Infrastructure" },
      { id: "task_launch", label: "Launch checkout OAuth sign-in", status: "blocked", space_name: "Launch" },
    ],
    severity: "critical",
    evidence: ["mem_policy_review", "mem_security_open"],
  };
}

function conflict() {
  return {
    id: "conflict_security_review",
    task: taskById("task_security"),
    tracked_state: "open",
    tracked_source: memoryById("mem_security_open"),
    recorded_state: "settled",
    recorded_at: recordedAt,
    basis: "CONTRADICTS",
    source: memoryById("mem_security_approved"),
    matched_terms: ["OAuth", "security", "review"],
    resolution: {
      op: "update_task",
      task_id: "task_security",
      status: "done",
      reason: "The go/no-go record supersedes the stale tracker state.",
      source_memory_ids: ["mem_security_approved"],
      preview: "Mark “Complete OAuth security approval” done",
    },
  };
}

function readiness() {
  const securityDone = taskById("task_security").status === "done";
  const checklist = [
    { id: "task_design", label: "Approve final sign-in interface", kind: "task", space_id: "space_design", space_name: "Design", owner: "Maya Chen", state: "done", status: "done", blocked_by: [], evidence: ["mem_design_done"] },
    { id: "task_rc", label: "Test backend release candidate rc-14", kind: "task", space_id: "space_engineering", space_name: "Engineering", owner: "Jon Bell", state: "done", status: "done", blocked_by: [], evidence: ["mem_rc_tested"] },
    { id: "task_security", label: "Complete OAuth security approval", kind: "task", space_id: "space_security", space_name: "Security", owner: "Priya Shah", state: securityDone ? "done" : "open", status: securityDone ? "done" : "open", blocked_by: [], evidence: [securityDone ? "mem_security_approved" : "mem_security_open"] },
    { id: "task_deploy", label: "Promote rc-14 to production", kind: "task", space_id: "space_infrastructure", space_name: "Infrastructure", owner: "Liam Ortiz", state: securityDone ? "ready" : "blocked", status: "blocked", blocked_by: securityDone ? [] : [{ id: "task_security", label: "Complete OAuth security approval" }], evidence: ["mem_deploy_gate"] },
    { id: "task_launch", label: "Launch checkout OAuth sign-in", kind: "goal", space_id: "space_launch", space_name: "Launch", owner: "Nora Kim", state: "blocked", status: "blocked", blocked_by: [{ id: "task_deploy", label: "Promote rc-14 to production" }], evidence: ["mem_launch_goal"] },
  ];
  return {
    ready: false,
    status: "NOT READY",
    goal: { id: "task_launch", label: "Launch checkout OAuth sign-in" },
    blocker_count: securityDone ? 0 : 1,
    outstanding: checklist.filter((item) => item.state !== "done").map((item) => item.label),
    checklist,
    blockers: securityDone ? [] : [blocker()],
  };
}

function provenance(memoryId: string) {
  const memory = memoryById(memoryId);
  return {
    memory,
    sources: (memory.source_ids || []).map((id) => ({
      id,
      title: id === "src_go_no_go" ? "Thursday launch go/no-go notes" : memory.title,
      type: id.includes("tracker") ? "tracker" : id.includes("ci") ? "pipeline" : "document",
    })),
    relations:
      memoryId === "mem_security_approved"
        ? [{ target_id: "mem_security_open", target_title: memoryById("mem_security_open").title, type: "CONTRADICTS" }]
        : [{ target_id: "mem_deploy_gate", target_title: memoryById("mem_deploy_gate").title, type: "SUPPORTS" }],
  };
}

function context() {
  const currentBlockers = taskById("task_security").status === "done" ? [] : [blocker()];
  return {
    spaces,
    memory_count: 20,
    decisions: [memoryById("mem_policy_review"), memoryById("mem_deploy_gate"), memoryById("mem_engineering_hold"), memoryById("mem_security_approved")],
    open_tasks: tasks.filter((task) => task.status !== "done"),
    unresolved: conflictResolved ? [memoryById("mem_support_gap")] : [memoryById("mem_security_open"), memoryById("mem_support_gap")],
    recent_changes: [memoryById("mem_security_approved"), memoryById("mem_engineering_hold"), memoryById("mem_rc_tested")],
    blockers: currentBlockers,
    next_best_action: {
      action: conflictResolved ? "Promote rc-14 to production" : "Reconcile the security tracker with the approved decision",
      why: conflictResolved ? "Every prerequisite is now complete." : "A newer source-backed decision contradicts the open tracker state.",
      task_id: conflictResolved ? "task_deploy" : "task_security",
      owner: conflictResolved ? "Liam Ortiz" : "Priya Shah",
    },
  };
}

function createPlan(body: any) {
  planCounter += 1;
  const id = `plan_demo_${planCounter}`;
  const operations = (body.operations || []).map((operation: any) => ({
    ...operation,
    preview:
      operation.preview ||
      (operation.op === "update_task"
        ? `Update ${taskById(operation.task_id).title}`
        : operation.op === "create_task"
          ? `Create “${operation.title}”`
          : `Add ${operation.type || "memory"} record`),
  }));
  const plan = {
    id,
    space_id: body.space_id || operations[0]?.space_id || "space_security",
    summary: body.summary || "Proposed organizational changes",
    status: "pending_approval",
    operations,
    results: [],
    created_at: new Date().toISOString(),
  };
  plans.set(id, plan);
  return plan;
}

function applyPlan(id: string, approved: boolean) {
  const plan = plans.get(id) || createPlan({ summary: "Reconcile security status", operations: [conflict().resolution] });
  plan.status = approved ? "approved" : "denied";
  if (approved) {
    for (const operation of plan.operations) {
      if (operation.op === "update_task" && operation.task_id) {
        const target = taskById(operation.task_id);
        if (operation.status) target.status = operation.status;
        if (operation.owner) target.owner = operation.owner;
        if (operation.priority) target.priority = operation.priority;
      }
    }
    if (plan.operations.some((operation: any) => operation.task_id === "task_security")) {
      conflictResolved = true;
    }
    plan.results = plan.operations.map((operation: any) => ({ op: operation.op, ok: true }));
  }
  plans.set(plan.id, plan);
  return plan;
}

export async function demoOrgRequest<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  params: Record<string, any> = {},
  body: any = {},
): Promise<T> {
  await pause();
  if (method === "GET" && path === "/spaces") return clone({ count: spaces.length, spaces }) as T;
  if (method === "GET" && path.startsWith("/spaces/")) {
    const space = spaces.find((item) => item.id === path.split("/")[2]) || spaces[0];
    return clone({ ...space, recent_memories: memories.filter((item) => item.space_id === space.id) }) as T;
  }
  if (method === "GET" && path === "/context") return clone(context()) as T;
  if (method === "GET" && path === "/search") {
    const needle = String(params.query || "").toLowerCase();
    const results = memories.filter((item) => !needle || `${item.title} ${item.content}`.toLowerCase().includes(needle)).slice(0, params.limit || 10);
    return clone({ count: results.length, results }) as T;
  }
  if (method === "GET" && path === "/recent-changes") {
    const changes = context().recent_changes.map((item) => ({ ...item, change: "added" }));
    return clone({ count: changes.length, changes }) as T;
  }
  if (method === "GET" && path === "/decisions") {
    const decisions = context().decisions;
    return clone({ count: decisions.length, decisions }) as T;
  }
  if (method === "GET" && path === "/tasks") {
    let results = tasks.filter((task) => task.status !== "done");
    if (params.assignee) results = results.filter((task) => task.owner.toLowerCase().includes(String(params.assignee).toLowerCase()));
    if (params.priority) results = results.filter((task) => task.priority === params.priority);
    if (params.status) results = tasks.filter((task) => task.status === params.status);
    return clone({ count: results.length, tasks: results }) as T;
  }
  if (method === "GET" && /^\/tasks\/[^/]+\/dependencies$/.test(path)) {
    const id = path.split("/")[2];
    const task = taskById(id);
    return clone({ task, requires: task.depends_on.map(taskById), required_by: tasks.filter((item) => item.depends_on.includes(id)) }) as T;
  }
  if (method === "GET" && path === "/people") {
    const people = [
      { id: "person_priya", name: "Priya Shah", owns: ["OAuth security approval ownership"], open_tasks: [taskById("task_security")] },
      { id: "person_liam", name: "Liam Ortiz", owns: ["production deploy ownership"], open_tasks: [taskById("task_deploy")] },
      { id: "person_nora", name: "Nora Kim", owns: ["launch ownership"], open_tasks: [taskById("task_launch")] },
      { id: "person_ava", name: "Ava Morgan", owns: ["support readiness ownership"], open_tasks: [taskById("task_support")] },
    ];
    return clone({ count: people.length, people }) as T;
  }
  if (method === "GET" && path.startsWith("/owner/")) {
    const target = path.split("/")[2];
    const task = tasks.find((item) => item.id === target);
    return clone({ object_id: target, owner: task?.owner || "Priya Shah", evidence: task?.source_memory_ids || ["mem_security_approved"] }) as T;
  }
  if (method === "GET" && path.startsWith("/provenance/")) return clone(provenance(path.split("/")[2])) as T;
  if (method === "GET" && path === "/reasoning-chain") {
    const chain = {
      topic: params.topic || "why security review blocks launch",
      steps: [
        { position: 1, role: "requirement", memory: memoryById("mem_policy_review") },
        { position: 2, role: "constraint", memory: memoryById("mem_deploy_gate") },
        { position: 3, role: "decision", memory: memoryById("mem_engineering_hold") },
        { position: 4, role: "current state", memory: memoryById("mem_security_open") },
      ],
      edges: [
        { from: "mem_policy_review", to: "mem_deploy_gate", type: "SUPPORTS" },
        { from: "mem_deploy_gate", to: "mem_engineering_hold", type: "SUPPORTS" },
        { from: "mem_engineering_hold", to: "mem_security_open", type: "SUPPORTS" },
      ],
    };
    return clone(chain) as T;
  }
  if (method === "GET" && path === "/dependency-graph") {
    const edges = tasks.flatMap((task) => task.depends_on.map((dependency) => ({ from: dependency, to: task.id, type: "REQUIRED_FOR" })));
    return clone({ node_count: tasks.length, edge_count: edges.length, nodes: tasks, edges }) as T;
  }
  if (method === "GET" && path === "/blockers") {
    const blockers = taskById("task_security").status === "done" ? [] : [blocker()];
    return clone({ count: blockers.length, blockers }) as T;
  }
  if (method === "GET" && path === "/conflicts") {
    const conflicts = conflictResolved ? [] : [conflict()];
    return clone({ count: conflicts.length, conflicts }) as T;
  }
  if (method === "GET" && path === "/stale") return clone({ count: 1, stale: [{ ...memoryById("mem_support_gap"), age_days: 124 }] }) as T;
  if (method === "GET" && path === "/readiness") return clone(readiness()) as T;
  if (method === "POST" && path === "/plans") return clone(createPlan(body)) as T;
  if (method === "GET" && path === "/plans") {
    const items = [...plans.values()].filter((plan) => !params.status || plan.status === params.status);
    return clone({ count: items.length, plans: items }) as T;
  }
  if (method === "POST" && /\/plans\/[^/]+\/(approve|reject)$/.test(path)) {
    const parts = path.split("/");
    return clone(applyPlan(parts[2], parts[3] === "approve")) as T;
  }
  if (method === "POST" && path === "/scenario/seed") {
    if (body.reset) {
      tasks = structuredClone(baseTasks);
      conflictResolved = false;
      watch = null;
      plans.clear();
    }
    return clone({ spaces: spaces.length, memories: 20, tasks: tasks.length, reset: Boolean(body.reset) }) as T;
  }
  if (method === "GET" && path === "/watches") return clone({ count: watch ? 1 : 0, watches: watch ? [watch] : [] }) as T;
  if (method === "POST" && path === "/watches") {
    watch = {
      id: "watch_demo_launch",
      name: body.name || "Checkout OAuth launch",
      space_ids: body.space_ids || spaces.map((space) => space.id),
      checks: body.checks || ["blockers", "conflicts", "stale"],
      interval_seconds: body.interval_seconds || 900,
      status: "active",
      runs: 0,
      open_findings: 0,
      findings: [],
    };
    return clone(watch) as T;
  }
  if (method === "POST" && /\/watches\/[^/]+\/run$/.test(path)) {
    if (!watch) throw new Error("Watch not found");
    watch.runs += 1;
    watch.last_run_at = new Date().toISOString();
    if (!conflictResolved && !watch.findings.length) {
      const plan = createPlan({ summary: "Reconcile the security tracker", operations: [conflict().resolution] });
      watch.findings = [{ id: "finding_demo_conflict", kind: "conflict", headline: "Security approval is recorded but the tracker remains open", detail: "A human-approved update is drafted and waiting.", plan_id: plan.id, status: "open", created_at: watch.last_run_at }];
    }
    watch.open_findings = watch.findings.filter((finding: any) => finding.status === "open").length;
    return clone({ ...watch, new_findings: watch.open_findings }) as T;
  }
  if (method === "POST" && /\/watches\/[^/]+\/findings\/[^/]+\/resolve$/.test(path)) {
    const findingId = path.split("/")[4];
    const finding = watch?.findings.find((item: any) => item.id === findingId);
    if (finding) finding.status = "resolved";
    if (watch) watch.open_findings = watch.findings.filter((item: any) => item.status === "open").length;
    return clone({ id: findingId, status: "resolved" }) as T;
  }
  if (method === "DELETE" && path.startsWith("/watches/")) {
    watch = null;
    return clone({ deleted: true }) as T;
  }

  throw new Error(`Demo endpoint is not implemented: ${method} ${path}`);
}

/* -------------------------------------------------- offline guided agent */

type DemoAgentStep = {
  tool: string;
  arguments: Record<string, unknown>;
  summary: string;
  thought: string;
  duration_ms: number;
};

export type DemoAgentSession = {
  id: string;
  question: string;
  model: string;
  status: "running" | "complete" | "error";
  mode: "model" | "guided";
  steps: DemoAgentStep[];
  answer: string;
  memory_ids: string[];
  error: string;
};

const AGENT_RE = {
  reconcile: /\b(reconcile|fix it|resolve|handle it|unblock)\b/i,
  catchup: /\b(catch me up|caught up|joined|onboard|overview|what matters)\b/i,
  why: /\b(why|reason|because|rationale|how come)\b/i,
  readiness: /\b(ready|launch|ship|go[- ]live|status)\b/i,
  blockers: /\b(block|blocker|blocking|stuck|stalled|holding)\b/i,
};

/* The offline twin of the backend's guided policy: the tool order follows the
   question's shape, and every summary and citation is produced by the same
   fixture handlers the page's own calls use. Nothing here is canned prose. */
export async function demoAgentSession(
  question: string,
  spaceIds: string[],
  onSession: (session: DemoAgentSession) => void,
): Promise<DemoAgentSession> {
  const session: DemoAgentSession = {
    id: `plan_demo_agent_${Date.now()}`,
    question,
    model: "deterministic",
    status: "running",
    mode: "guided",
    steps: [],
    answer: "",
    memory_ids: [],
    error: "",
  };
  const emit = () => onSession(structuredClone(session));

  const run = async (
    tool: string,
    thought: string,
    summary: string,
    memoryIds: string[] = [],
  ) => {
    await new Promise((resolve) => setTimeout(resolve, 260));
    session.steps.push({ tool, arguments: {}, summary, thought, duration_ms: 260 });
    session.memory_ids = [...new Set([...session.memory_ids, ...memoryIds])].slice(0, 6);
    emit();
  };

  const currentConflict = conflictResolved ? null : conflict();
  const currentReadiness = readiness();

  if (currentConflict && AGENT_RE.reconcile.test(question)) {
    await run(
      "find_orgmemory_conflicts",
      "Guided reconcile: check tracked state against newer records first.",
      `1 conflict — “${currentConflict.task.title}” is ${currentConflict.tracked_state}, but ${currentConflict.source.space_name} already settled it.`,
      [currentConflict.tracked_source.id, currentConflict.source.id],
    );
    await run(
      "propose_orgmemory_changes",
      "A contradiction exists, so the fix is proposed — never applied.",
      `1 change proposed and waiting for a person. Nothing applied.`,
      [currentConflict.source.id],
    );
  } else if (AGENT_RE.catchup.test(question)) {
    const state = context();
    await run(
      "get_orgmemory_project_context",
      "Guided catch-up: assemble everything current across the scoped spaces.",
      `${state.memory_count} memories across ${state.spaces.length} spaces: ${state.decisions.length} decisions, ${state.open_tasks.length} open items, ${state.blockers.length} blocker.`,
      state.decisions.map((item: any) => item.id),
    );
    await run(
      "get_orgmemory_recent_changes",
      "Then check what moved recently.",
      `${state.recent_changes.length} change(s) on record.`,
      state.recent_changes.map((item: any) => item.id),
    );
    await run(
      "find_orgmemory_blockers",
      "Finish with what is actually holding things up.",
      currentReadiness.blockers.length
        ? `1 blocker — ${currentReadiness.blockers[0].task.title} (critical), holding ${currentReadiness.blockers[0].blocks.length} item(s).`
        : "Nothing is blocking.",
      currentReadiness.blockers[0]?.evidence || [],
    );
  } else if (AGENT_RE.why.test(question)) {
    await run(
      "get_orgmemory_reasoning_chain",
      "Guided why: walk recorded relationships, not keyword matches.",
      "4 steps of recorded reasoning.",
      ["mem_policy_review", "mem_deploy_gate", "mem_engineering_hold"],
    );
    await run(
      "find_orgmemory_blockers",
      "Ground the chain in the current blocker.",
      currentReadiness.blockers.length
        ? `1 blocker — ${currentReadiness.blockers[0].task.title} (critical), holding ${currentReadiness.blockers[0].blocks.length} item(s).`
        : "Nothing is blocking.",
      currentReadiness.blockers[0]?.evidence || [],
    );
  } else if (AGENT_RE.readiness.test(question)) {
    await run(
      "get_orgmemory_readiness",
      "Guided readiness: compute the checklist from the dependency graph.",
      `${currentReadiness.status} — ${currentReadiness.blocker_count} blocker(s), ${currentReadiness.checklist.length} checklist item(s).`,
      currentReadiness.checklist.flatMap((item) => item.evidence),
    );
    await run(
      "find_orgmemory_conflicts",
      "Then check whether any record disagrees with the tracker.",
      currentConflict
        ? `1 conflict — “${currentConflict.task.title}” is ${currentConflict.tracked_state}, but ${currentConflict.source.space_name} already settled it.`
        : "No contradictions found.",
      currentConflict ? [currentConflict.source.id] : [],
    );
  } else {
    const needle = question.toLowerCase();
    const results = memories
      .filter((item) => !needle || `${item.title} ${item.content}`.toLowerCase().includes(needle))
      .slice(0, 5);
    await run(
      "search_orgmemory_records",
      "Guided search: match the question against scoped memory.",
      `${results.length} matching memories.`,
      results.map((item) => item.id),
    );
    await run(
      "get_orgmemory_project_context",
      "Add the surrounding state so the answer is grounded.",
      `${context().memory_count} memories across 7 spaces.`,
      [],
    );
  }

  session.answer = session.steps
    .map((step) => step.summary)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  session.status = "complete";
  emit();
  return structuredClone(session);
}

/* Offline follow-ups: derived from the fixture's live state — the open
   contradiction, the blocker chain, the readiness board — never a script. */
export async function demoFollowups(
  question: string,
  _summaries: string[],
): Promise<{ suggestions: string[]; source: string }> {
  await pause();
  const ideas: string[] = [];
  const currentConflict = conflictResolved ? null : conflict();
  const board = readiness();
  if (currentConflict) {
    ideas.push(`Reconcile “${currentConflict.task.title}”`);
    ideas.push(`Who owns “${currentConflict.task.title}”?`);
  }
  if (board.blockers.length) {
    ideas.push(`What unblocks “${board.blockers[0].task.title}”?`);
  }
  if (board.outstanding.length) {
    ideas.push("Are we ready to launch yet?");
  }
  if (!conflictResolved && !AGENT_RE.reconcile.test(question)) {
    ideas.push("Reconcile it and prepare us for launch.");
  }
  if (!ideas.length) {
    ideas.push("What changed recently?", "What is still unresolved?");
  }
  return { suggestions: [...new Set(ideas)].slice(0, 3), source: "workspace" };
}
