"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import AgentActivityLayer, { WebMCPStatusButton } from "@/components/AgentActivityLayer";
import CommandMenu, { useCommandMenu } from "@/components/CommandMenu";
import MarkdownAnswer from "@/components/MarkdownAnswer";
import { RunbookMark } from "@/components/RunbookLogo";
import { NavToggle } from "@/components/WorkspaceFrame";
import { useOrgMemoryWebMCP } from "@/hooks/useOrgMemoryWebMCP";
import { api } from "@/lib/api";
import type {
  OrgMemoryBriefing,
  OrgMemoryBriefingInput,
  OrgMemoryChangeSet,
  OrgMemoryOutcomeInput,
  OrgMemoryOutcomeReceipt,
  OrgMemoryProposal,
  OrgMemoryProposalInput,
  OrgMemoryRelatedEntry,
  OrgMemoryRunbook,
  OrgMemoryServiceContextEntry,
  OrgMemoryRefreshRequest,
  OrgMemoryUnit,
} from "@/lib/webmcp";

type Model = { id: string; label: string; company: string; model: string; configured: boolean; default: boolean };
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
    /* "ambiguous_target" offers repositories to pick from; "missing_target" and
       "unresolved_reference" offer examples of the detail the request left out,
       which the asker types. */
    reason?: string;
    options: { project_id?: string; label: string; files?: string[]; hint?: string }[];
  };
  /* What a follow-up like "why is it failing" was bound to. Shown so a wrong
     binding is visible and correctable rather than silently wrong. */
  resolved_subject?: string;
  /* How many connected sources were searched. A count, not a trace — enough to
     say "I looked and found nothing" without exposing how the looking works. */
  searched_sources?: number;
  /* Identifies the context this answer was built from, so what happens next can
     be attributed back to it. Never shown — it is a link, not a statistic. */
  context_event_id?: string;
  likely_cause?: string;
  confidence?: number;
  trust_score?: {
    level?: string;
    reason?: string;
    contradictions?: unknown[];
  };
  memory_units?: OrgMemoryUnit[];
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
type Run = {
  id: string;
  status: string;
  branch: string;
  base_branch: string;
  commit_sha: string;
  files_changed: string[];
  diff_stat: string;
  pull_request_url: string;
  error: string;
  executor: string;
};
type Turn = { question: string; answer?: Answer; error?: string };
type WorkspaceMember = {
  id: string;
  email: string;
  display_name?: string;
  role: string;
  status: string;
};

/* Terminal states stop the poller. Anything else is still in flight. */
const RUN_DONE = ["committed", "pushed", "no_changes", "failed"];

/* The thread survives a reload. It is kept per project so switching memory
   spaces does not show someone another project's conversation, and capped so a
   long session cannot outgrow the storage quota. */
const THREAD_KEY = "orgmemory.thread";
const THREAD_LIMIT = 40;

function loadThread(project: string): Turn[] {
  if (typeof window === "undefined" || !project) return [];
  try {
    const stored = window.localStorage.getItem(`${THREAD_KEY}.${project}`);
    const parsed = stored ? JSON.parse(stored) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/* How many prior turns travel with a question. Only the last few carry the live
   subject of a conversation, and the server caps this too — this is about
   sending a sensible payload, not about trusting the client. */
const HISTORY_TURNS = 8;

/* Flatten the thread into the alternating roles the server expects. Clarifying
   questions are left out: they are the assistant asking rather than telling, and
   feeding them back makes the next question look like it was about them. */
function threadHistory(turns: Turn[]): { role: "user" | "assistant"; content: string }[] {
  const history: { role: "user" | "assistant"; content: string }[] = [];
  for (const turn of turns.slice(-HISTORY_TURNS)) {
    if (turn.question) history.push({ role: "user", content: turn.question });
    const answer = turn.answer;
    if (answer?.answer && answer.answer_scope !== "clarification") {
      history.push({ role: "assistant", content: answer.answer.slice(0, 2000) });
    }
  }
  return history;
}

function saveThread(project: string, turns: Turn[]) {
  if (typeof window === "undefined" || !project) return;
  try {
    window.localStorage.setItem(
      `${THREAD_KEY}.${project}`,
      JSON.stringify(turns.slice(-THREAD_LIMIT)),
    );
  } catch {
    /* a full or disabled store must not break the conversation */
  }
}

const starters = [
  "What changed recently?",
  "Who owns this service?",
  "Why is it failing?",
  "What should I know before editing this?",
];

export default function WorkspaceChat({ user }: { user: any }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState("");
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [menu, setMenu] = useState<"" | "model" | "space">("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [copied, setCopied] = useState("");
  const [restored, setRestored] = useState(false);
  /* Workspace-wide by default: someone asking their company a question is not
     thinking about which repository holds the answer. */
  const [scope, setScope] = useState<"workspace" | "project">("workspace");
  /* Approvals ride inline with the conversation. A workspace admin should not
     have to know a queue exists on another page: the request appears here the
     moment an employee (or a browser agent acting for one) proposes it, and the
     decision is two buttons in the same place they already work. */
  const [requests, setRequests] = useState<OrgMemoryRefreshRequest[]>([]);
  const [proposals, setProposals] = useState<OrgMemoryProposal[]>([]);
  const [decidingId, setDecidingId] = useState("");
  const [decidingProposalId, setDecidingProposalId] = useState("");
  const [inboxNote, setInboxNote] = useState("");
  const [inboxError, setInboxError] = useState("");
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [filter, setFilter] = useState("");
  const [activityOpen, setActivityOpen] = useState(false);
  const [agentActivityEnabled, setAgentActivityEnabled] = useState(true);
  const [followOrb, setFollowOrb] = useState(false);

  const command = useCommandMenu();

  const picker = useRef<HTMLDivElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api<Project[]>("/api/projects")
      .then((items) => {
        setProjects(items);
        if (items[0]) setProject(items[0].id);
      })
      .catch((error) => setLoadError(error.message));
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
  }, []);

  useEffect(() => {
    const activityPreference = window.localStorage.getItem("orgmemory.agent-activity");
    const followPreference = window.localStorage.getItem("orgmemory.follow-orb");
    if (activityPreference !== null) setAgentActivityEnabled(activityPreference !== "off");
    if (followPreference !== null) setFollowOrb(followPreference === "on");
    const pendingQuestion = window.sessionStorage.getItem("orgmemory.pending-question");
    if (pendingQuestion) {
      setDraft(pendingQuestion);
      window.sessionStorage.removeItem("orgmemory.pending-question");
    }
  }, []);

  useEffect(() => {
    if (!menu) return;
    function onPointerDown(event: MouseEvent) {
      if (!picker.current?.contains(event.target as Node)) setMenu("");
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

  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  /* Restore on load and whenever the project changes. `restored` gates the save
     below so the empty initial state cannot overwrite a stored thread before
     this has run. */
  useEffect(() => {
    if (!project) return;
    setTurns(loadThread(project));
    setRestored(true);
  }, [project]);

  useEffect(() => {
    if (!restored || !project) return;
    saveThread(project, turns);
  }, [turns, project, restored]);

  const activeModel = useMemo(() => models.find((item) => item.id === model), [models, model]);
  const activeProject = useMemo(() => projects.find((item) => item.id === project), [projects, project]);
  const visibleProjects = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return projects;
    return projects.filter(
      (item) =>
        item.name.toLowerCase().includes(needle) ||
        (item.repository || "").toLowerCase().includes(needle),
    );
  }, [projects, filter]);

  const ask = useCallback(
    async (
      prompt: string,
      overrideProject?: string,
      surface: "web" | "webmcp" = "web",
      requestedScope?: "workspace" | "project",
    ): Promise<Answer> => {
      const question = prompt.trim();
      const target = overrideProject || project;
      if (!question || !target) throw new Error("Choose a memory space and enter a question.");
      if (busy) throw new Error("OrgMemory is already answering another question.");
      setDraft("");
      setBusy(true);
      /* Captured before the new turn is appended, so the thread sent is what came
         before this question rather than including it. */
      const history = threadHistory(turns);
      setTurns((current) => [...current, { question }]);
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
            scope: requestedScope || (overrideProject ? "project" : scope),
            history,
          }),
        });
        setTurns((current) =>
          current.map((turn, index) => (index === current.length - 1 ? { ...turn, answer: response } : turn)),
        );
        return response;
      } catch (error: any) {
        setTurns((current) =>
          current.map((turn, index) => (index === current.length - 1 ? { ...turn, error: error.message } : turn)),
        );
        throw error;
      } finally {
        setBusy(false);
      }
    },
    [busy, model, project, scope, turns],
  );

  const inspectChanges = useCallback(
    (projectId: string, limit: number) =>
      api<OrgMemoryChangeSet[]>(
        `/api/memory/change-sets?project_id=${encodeURIComponent(projectId)}&limit=${limit}`,
      ),
    [],
  );

  /* The pre-action briefing: the one retrieval surface that answers an intent
     rather than a question. Serving it opens a row in the outcome ledger, so
     the briefing_id an agent gets back is also how it reports what happened. */
  const briefApi = useCallback(
    (input: OrgMemoryBriefingInput) =>
      api<OrgMemoryBriefing>("/api/briefings", {
        method: "POST",
        body: JSON.stringify({
          task: input.task,
          service: input.service || "",
          project_id: input.projectId || "",
          surface: input.surface || "webmcp",
        }),
      }),
    [],
  );

  const recordOutcomeApi = useCallback(
    (input: OrgMemoryOutcomeInput) =>
      api<OrgMemoryOutcomeReceipt>("/api/briefings/outcome", {
        method: "POST",
        body: JSON.stringify({
          briefing_id: input.briefingId,
          action: input.action,
          outcome: input.outcome,
          target: input.target || "",
          surface: input.surface || "webmcp",
          reason: input.reason || "",
        }),
      }),
    [],
  );

  /* Structured memory retrieval shared by the WebMCP tools and the demo page:
     the same endpoints a browser agent reaches through its page tools, with the
     backend doing the permission trimming. */
  const searchMemoryApi = useCallback(
    async (projectId: string, query: string, type?: string, limit?: number) => {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (projectId) params.set("project_id", projectId);
      if (type) params.set("type", type);
      params.set("limit", String(limit ?? 10));
      const response = await api<{ results: OrgMemoryUnit[] }>(`/api/memory/search?${params}`);
      return response.results || [];
    },
    [],
  );

  const getMemoryApi = useCallback(
    (memoryId: string) => api<OrgMemoryUnit>(`/api/memory/units/${encodeURIComponent(memoryId)}`),
    [],
  );

  const getRelatedMemoriesApi = useCallback(
    async (memoryId: string) => {
      const response = await api<{ related: OrgMemoryRelatedEntry[] }>(
        `/api/memory/units/${encodeURIComponent(memoryId)}/related`,
      );
      return response.related || [];
    },
    [],
  );

  const listIncidentsApi = useCallback(
    (projectId: string, service?: string) =>
      searchMemoryApi(projectId, service || "", "incident", 20),
    [searchMemoryApi],
  );

  const findRunbooksApi = useCallback(
    async (service: string, issue?: string) => {
      const runbooks = await api<OrgMemoryRunbook[]>("/api/runbooks");
      const needle = service.toLowerCase();
      const issueNeedle = (issue || "").toLowerCase();
      return runbooks.filter((runbook) => {
        const haystack = [
          runbook.key,
          runbook.title,
          runbook.trigger,
          (runbook.procedures || []).join(" "),
          (runbook.steps || []).join(" "),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return haystack.includes(needle) && (!issueNeedle || haystack.includes(issueNeedle));
      });
    },
    [],
  );

  const getServiceContextApi = useCallback(
    async (service: string) => {
      /* One profile per authorized space; the backend trims each to the
         signed-in person's team scope. Empty profiles are skipped so an agent
         never receives a wall of nothing. */
      const response = await api<Project[]>("/api/projects");
      const entries = await Promise.all(
        (response || []).map(async (space) => {
          try {
            const profile = await api<OrgMemoryServiceContextEntry["profile"]>(
              `/api/memory/profiles/service/${encodeURIComponent(service)}?project_id=${encodeURIComponent(space.id)}`,
            );
            const total =
              (profile.current_facts || []).length +
              (profile.decisions || []).length +
              (profile.incidents || []).length +
              (profile.dependencies || []).length +
              (profile.owners || []).length +
              (profile.procedures || []).length;
            return total > 0
              ? { project_id: space.id, project_name: space.name, profile }
              : null;
          } catch {
            return null;
          }
        }),
      );
      return entries.filter(Boolean) as OrgMemoryServiceContextEntry[];
    },
    [],
  );

  const listDecisionsApi = useCallback(
    (projectId: string, limit?: number) => searchMemoryApi(projectId, "", "decision", limit ?? 10),
    [searchMemoryApi],
  );

  const proposeMemoryApi = useCallback(
    (input: OrgMemoryProposalInput) =>
      api<OrgMemoryProposal>("/api/memory/proposals", {
        method: "POST",
        body: JSON.stringify({
          project_id: input.projectId,
          kind: input.kind,
          subject: input.subject,
          content: input.content,
          service: input.service || "",
          reason: input.reason || "",
        }),
      }),
    [],
  );

  const loadProposals = useCallback(() => {
    return api<OrgMemoryProposal[]>("/api/memory/proposals")
      .then((items) => {
        setProposals(items);
        return items;
      })
      .catch(() => []);
  }, []);

  const resolveProposalApi = useCallback(
    (proposalId: string, approved: boolean) =>
      api<OrgMemoryProposal>(
        `/api/memory/proposals/${encodeURIComponent(proposalId)}/resolve`,
        { method: "POST", body: JSON.stringify({ approved }) },
      ),
    [],
  );

  const proposeRepositoryRefresh = useCallback(
    (projectId: string, reason: string) =>
      api<OrgMemoryRefreshRequest>("/api/repository-refresh-requests", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, reason }),
      }),
    [],
  );

  const loadRequests = useCallback(() => {
    // The backend already limits this list to requests inside projects the
    // caller can see, so the poll cannot surface anything unauthorized.
    return api<OrgMemoryRefreshRequest[]>("/api/repository-refresh-requests")
      .then((items) => {
        setRequests(items);
        return items;
      })
      .catch(() => undefined);
  }, []);

  /* Raw API surface shared by the inline buttons and the WebMCP tools: a
     browser agent resolving an approval uses exactly the same authorized
     endpoint as a person pressing the button. */
  const resolveApprovalApi = useCallback(
    (requestId: string, approved: boolean) =>
      api<OrgMemoryRefreshRequest>(
        `/api/repository-refresh-requests/${encodeURIComponent(requestId)}/resolve`,
        { method: "POST", body: JSON.stringify({ approved }) },
      ),
    [],
  );

  const listApprovals = useCallback(
    async (projectId: string) => {
      const items = await api<OrgMemoryRefreshRequest[]>("/api/repository-refresh-requests");
      setRequests(items);
      return items.filter((item) => item.project_id === projectId);
    },
    [],
  );

  const isAdmin = user?.role === "owner" || user?.role === "admin";

  const loadMembers = useCallback(async () => {
    if (!isAdmin || !user?.active_workspace_id) return;
    const items = await api<WorkspaceMember[]>(
      `/api/workspaces/${user.active_workspace_id}/members`,
    );
    setMembers(items);
  }, [isAdmin, user?.active_workspace_id]);

  useEffect(() => {
    if (!user?.active_workspace_id) return;
    loadRequests();
    loadProposals();
    void loadMembers().catch(() => undefined);
    const timer = window.setInterval(() => {
      loadRequests();
      loadProposals();
    }, 8000);
    return () => window.clearInterval(timer);
  }, [user?.active_workspace_id, loadMembers, loadRequests, loadProposals]);

  const webMCP = useOrgMemoryWebMCP({
    enabled: projects.length > 0 && Boolean(project),
    spaces: projects,
    activeProjectId: project,
    ask: async (question, projectId, requestedScope) => {
      setProject(projectId);
      setScope(requestedScope);
      return ask(question, projectId, "webmcp", requestedScope);
    },
    inspectChanges,
    brief: briefApi,
    recordOutcome: recordOutcomeApi,
    searchMemory: searchMemoryApi,
    getMemory: getMemoryApi,
    getRelatedMemories: getRelatedMemoriesApi,
    listIncidents: listIncidentsApi,
    findRunbooks: findRunbooksApi,
    getServiceContext: getServiceContextApi,
    listDecisions: listDecisionsApi,
    proposeMemory: proposeMemoryApi,
    listProposals: loadProposals,
    canResolveProposals: isAdmin,
    resolveProposal: async (proposalId, approved) => {
      const resolved = await resolveProposalApi(proposalId, approved);
      // Mirror the agent's decision into the same state the human inbox reads.
      setProposals((current) =>
        current.map((item) => (item.id === resolved.id ? resolved : item)),
      );
      return resolved;
    },
    proposeRepositoryRefresh,
    listApprovals,
    canResolveApprovals: isAdmin,
    resolveApproval: async (requestId, approved) => {
      const resolved = await resolveApprovalApi(requestId, approved);
      // Mirror the agent's decision into the same state the human inbox reads.
      setRequests((current) =>
        current.map((item) => (item.id === resolved.id ? resolved : item)),
      );
      return resolved;
    },
  });

  const webMCPLabel = (() => {
    if (webMCP.status !== "ready") return "";
    if (webMCP.activity?.state === "running") return "Agent is reading company memory";
    if (webMCP.activity?.state === "complete") return "Agent used company memory";
    if (webMCP.activity?.state === "error") return "Agent call needs attention";
    return "Agent ready";
  })();

  const pendingApprovals = useMemo(
    () => requests.filter((item) => item.status === "pending_approval"),
    [requests],
  );
  const pendingProposals = useMemo(
    () => proposals.filter((item) => item.status === "pending_approval"),
    [proposals],
  );
  const inFlight = useMemo(
    () => requests.filter((item) => item.status === "queued" || item.status === "running"),
    [requests],
  );

  async function decide(request: OrgMemoryRefreshRequest, approved: boolean) {
    setDecidingId(request.id);
    setInboxNote("");
    setInboxError("");
    try {
      const resolved = await resolveApprovalApi(request.id, approved);
      setRequests((current) =>
        current.map((item) => (item.id === resolved.id ? resolved : item)),
      );
      setInboxNote(
        approved
          ? `Approved — refreshing ${resolved.repository} now. Memory updates when it lands.`
          : `Request from ${request.requested_by_name || "the requester"} was denied.`,
      );
    } catch (error: any) {
      setInboxError(error.message);
    } finally {
      setDecidingId("");
    }
  }

  /* Approving a memory proposal is the moment agent-supplied knowledge becomes
     organizational memory. It runs through the same authorized endpoint as the
     WebMCP resolve tool, so a button and an agent decision are identical. */
  async function decideProposal(proposal: OrgMemoryProposal, approved: boolean) {
    setDecidingProposalId(proposal.id);
    setInboxNote("");
    setInboxError("");
    try {
      const resolved = await resolveProposalApi(proposal.id, approved);
      setProposals((current) =>
        current.map((item) => (item.id === resolved.id ? resolved : item)),
      );
      setInboxNote(
        approved
          ? `Approved — "${resolved.subject}" is now ${resolved.kind} memory.`
          : `Proposal "${resolved.subject}" was denied. Nothing was saved.`,
      );
    } catch (error: any) {
      setInboxError(error.message);
    } finally {
      setDecidingProposalId("");
    }
  }

  async function inviteMember() {
    const email = inviteEmail.trim();
    if (!email || !user?.active_workspace_id) return;
    setInviting(true);
    setInboxError("");
    try {
      const result = await api<{ invite_delivery?: string }>(
        `/api/workspaces/${user.active_workspace_id}/members/invite`,
        { method: "POST", body: JSON.stringify({ email, role: inviteRole }) },
      );
      setInviteEmail("");
      setInviteOpen(false);
      setInboxNote(
        result.invite_delivery === "email"
          ? `Invitation email sent to ${email}.`
          : `${email} was added. Share the sign-in link because email delivery is not configured.`,
      );
      await loadMembers();
    } catch (error: any) {
      setInboxError(error.message);
    } finally {
      setInviting(false);
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

  const workspace = user?.workspaces?.find((item: any) => item.id === user?.active_workspace_id);
  const noProjects = !projects.length && !loadError;

  return (
    <div className="om-home ws-app agentic-workspace" data-webmcp-status={webMCP.status}>
      <header className="ws-bar">
        {/* The brand and account live in the sidebar; this bar holds the chat's own controls. */}
        <div className="page-bar-id">
          <NavToggle />
          <span>
            <small>{workspace?.name || "Company brain"}</small>
            <strong>Chat</strong>
          </span>
        </div>

        <div className="ws-controls" ref={picker}>
          <WebMCPStatusButton
            status={webMCP.status}
            toolCount={webMCP.toolCount}
            activity={webMCP.activity}
            onClick={() => setActivityOpen(true)}
          />
          {projects.length > 1 && (
            <div className="ws-pick" data-orb-target="spaces">
              <button className="ws-pill" aria-expanded={menu === "space"} onClick={() => setMenu(menu === "space" ? "" : "space")}>
                <small>Memory</small>
                <span>{scope === "workspace" ? "All memory" : activeProject?.name || "Select"}</span>
                <em aria-hidden="true">▾</em>
              </button>
              {menu === "space" && (
                <div className="ws-menu" role="listbox">
                  <div className="ws-menu-search">
                    <input
                      autoFocus
                      value={filter}
                      placeholder="Filter repositories…"
                      onChange={(event) => setFilter(event.target.value)}
                      aria-label="Filter repositories"
                    />
                  </div>
                  <button
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
                      <small>Search every connected source</small>
                    </div>
                  </button>
                  {visibleProjects.map((item) => (
                    <button
                      key={item.id}
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
                  {!visibleProjects.length && <p className="ws-menu-empty">No repository matches that.</p>}
                </div>
              )}
            </div>
          )}

          <div className="ws-pick">
            <button className="ws-pill" aria-expanded={menu === "model"} aria-haspopup="listbox" onClick={() => setMenu(menu === "model" ? "" : "model")}>
              <i className={activeModel?.configured ? "on" : ""} />
              <small>Model</small>
              <span>{activeModel?.label || "—"}</span>
              <em aria-hidden="true">▾</em>
            </button>
            {menu === "model" && (
              <div className="ws-menu" role="listbox">
                {models.map((item) => (
                  <button
                    key={item.id}
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
                  Keys live in <Link href="/settings">settings</Link>. Every model answers from the same
                  retrieved company memory.
                </p>
              </div>
            )}
          </div>

          {turns.length > 0 && (
            <button className="ws-pill quiet" onClick={() => setTurns([])} title="Start a new conversation">
              <span>New chat</span>
            </button>
          )}
          {pendingApprovals.length > 0 && (
            <Link className="ws-pill attention" href="#workspace-controls" title="Pending approvals">
              <i className="on" />
              <span>
                {pendingApprovals.length + pendingProposals.length} approval
                {pendingApprovals.length + pendingProposals.length === 1 ? "" : "s"} waiting
              </span>
            </Link>
          )}
          <button
            type="button"
            className="ws-pill ws-jump"
            onClick={() => command.setOpen(true)}
            title="Jump anywhere, add knowledge, or ask a question"
          >
            <span>Jump to…</span>
            <kbd>⌘K</kbd>
          </button>
        </div>
      </header>

      <main className="ws-workspace" data-orb-target="canvas">
        <section className="ws-thread" ref={thread}>
          <div className="ws-thread-inner">
          {loadError && <div className="ws-alert">{loadError}</div>}

          {noProjects && (
            <section className="ws-onboard">
              <span className="ws-orb" aria-hidden="true">
                <i />
                <RunbookMark />
              </span>
              <h1>Your brain is empty — for about a minute.</h1>
              <p>
                Connect one source and OrgMemory starts answering from it: a repository, a Slack
                workspace, or a document you paste in.
              </p>
              <Link className="home-btn" href="/ingest">
                Connect your first source <span aria-hidden="true">→</span>
              </Link>
            </section>
          )}

          {!noProjects && !turns.length && (
            <section className="ws-rest" data-orb-target="memory">
              <span className="ws-orb command" aria-hidden="true"><i />✦</span>
              <h1>Ask your company memory.</h1>
              <p>
                Every repository, decision, incident, and owner this workspace has indexed,
                answerable in one place and cited back to its source.
              </p>
              <div className="ws-starters">
                {starters.map((item) => (
                  <button key={item} onClick={() => void ask(item).catch(() => undefined)}>
                    {item}
                  </button>
                ))}
              </div>
            </section>
          )}

          {turns.map((turn, index) => (
            <article className="ws-turn" key={`${turn.question}-${index}`}>
              <div className="ws-asked">
                <p>{turn.question}</p>
              </div>

              {turn.error && <div className="ws-alert">{turn.error}</div>}

              {turn.answer && <AnswerBlock
                answer={turn.answer}
                question={turn.question}
                onCopy={copyHandoff}
                copied={copied}
                turnKey={String(index)}
                project={project}
                onPick={(projectId) => void ask(turn.question, projectId).catch(() => undefined)}
                onFollow={() => {
                  setFollowOrb(true);
                  window.localStorage.setItem("orgmemory.follow-orb", "on");
                  document.querySelector<HTMLElement>("[data-investigation-trail]")?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
              />}

              {!turn.answer && !turn.error && busy && index === turns.length - 1 && (
                <div className="ws-working" role="status" aria-live="polite">
                  <span className="ws-working-orb" aria-hidden="true">✦</span>
                  <div>
                    <strong>Orb is searching organizational memory</strong>
                    <p>Real results will appear as soon as retrieval returns.</p>
                  </div>
                </div>
              )}
            </article>
          ))}
          </div>
        </section>

        <div className="ws-rail-wrap" data-orb-target="approval">
        <WorkspaceControlRail
          workspaceName={workspace?.name || "Company memory"}
          role={user?.role || "member"}
          isAdmin={isAdmin}
          members={members}
          pendingApprovals={pendingApprovals}
          inFlight={inFlight}
          pendingProposals={pendingProposals}
          decidingId={decidingId}
          decidingProposalId={decidingProposalId}
          inboxNote={inboxNote}
          inboxError={inboxError}
          inviteOpen={inviteOpen}
          inviteEmail={inviteEmail}
          inviteRole={inviteRole}
          inviting={inviting}
          webMCPLabel={webMCPLabel}
          webMCPToolCount={webMCP.toolCount}
          onInviteOpen={() => setInviteOpen((current) => !current)}
          onInviteEmailChange={setInviteEmail}
          onInviteRoleChange={setInviteRole}
          onInvite={() => void inviteMember()}
          onDecide={(request, approved) => void decide(request, approved)}
          onDecideProposal={(proposal, approved) => void decideProposal(proposal, approved)}
        />
        </div>
      </main>

      <footer className="ws-compose-wrap">
        <div className="ws-compose-layout">
          <div>
            <div className="ws-compose">
              <textarea
                ref={composer}
                rows={1}
                value={draft}
                disabled={noProjects}
                placeholder={noProjects ? "Connect a source to start asking…" : "Ask OrgMemory anything…"}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void ask(draft).catch(() => undefined);
                  }
                }}
                aria-label="Ask OrgMemory"
              />
              <button className="ws-send" onClick={() => void ask(draft).catch(() => undefined)} disabled={busy || noProjects || !draft.trim()} aria-label="Send">
                ↑
              </button>
            </div>
            <p className="ws-foot">
              <button type="button" className="ws-command-hint" onClick={() => command.setOpen(true)}><kbd>⌘K</kbd> Jump anywhere</button>
              {webMCPLabel && <button type="button" className="ws-agent-ready" title={`${webMCP.toolCount} browser-native WebMCP tools are available`} onClick={() => setActivityOpen(true)}><i />{webMCPLabel}</button>}
              <Link href="/memories">Inspect evidence</Link>
            </p>
          </div>
        </div>
      </footer>

      <CommandMenu
        open={command.open}
        onClose={command.close}
        isAdmin={isAdmin}
        pendingApprovals={pendingApprovals.length + pendingProposals.length}
        onAsk={(question) => void ask(question).catch(() => undefined)}
      />

      <AgentActivityLayer
        status={webMCP.status}
        toolCount={webMCP.toolCount}
        activities={webMCP.activityLog}
        open={activityOpen}
        onClose={() => setActivityOpen(false)}
        enabled={agentActivityEnabled}
        onEnabledChange={(enabled) => {
          setAgentActivityEnabled(enabled);
          window.localStorage.setItem("orgmemory.agent-activity", enabled ? "on" : "off");
        }}
        follow={followOrb}
        onFollowChange={(enabled) => {
          setFollowOrb(enabled);
          window.localStorage.setItem("orgmemory.follow-orb", enabled ? "on" : "off");
        }}
      />
    </div>
  );
}

type WorkspaceControlRailProps = {
  workspaceName: string;
  role: string;
  isAdmin: boolean;
  members: WorkspaceMember[];
  pendingApprovals: OrgMemoryRefreshRequest[];
  inFlight: OrgMemoryRefreshRequest[];
  pendingProposals: OrgMemoryProposal[];
  decidingId: string;
  decidingProposalId: string;
  inboxNote: string;
  inboxError: string;
  inviteOpen: boolean;
  inviteEmail: string;
  inviteRole: string;
  inviting: boolean;
  webMCPLabel: string;
  webMCPToolCount: number;
  onInviteOpen: () => void;
  onInviteEmailChange: (email: string) => void;
  onInviteRoleChange: (role: string) => void;
  onInvite: () => void;
  onDecide: (request: OrgMemoryRefreshRequest, approved: boolean) => void;
  onDecideProposal: (proposal: OrgMemoryProposal, approved: boolean) => void;
};

/* The workspace rail keeps the state that governs an agent's work in sight:
   who is here, who can decide, what is waiting, and which page-native tools
   are available. It is deliberately part of the workspace rather than another
   destination someone has to remember to visit. */
function WorkspaceControlRail({
  workspaceName,
  role,
  isAdmin,
  members,
  pendingApprovals,
  inFlight,
  pendingProposals,
  decidingId,
  decidingProposalId,
  inboxNote,
  inboxError,
  inviteOpen,
  inviteEmail,
  inviteRole,
  inviting,
  webMCPLabel,
  webMCPToolCount,
  onInviteOpen,
  onInviteEmailChange,
  onInviteRoleChange,
  onInvite,
  onDecide,
  onDecideProposal,
}: WorkspaceControlRailProps) {
  const memberCount = members.length;

  return (
    <aside className="ws-rail" id="workspace-controls" aria-label="Workspace controls">
      <section className="ws-rail-card ws-workspace-card">
        <p className="ws-rail-eyebrow">Workspace</p>
        <div className="ws-rail-title">
          <div>
            <strong>{workspaceName}</strong>
            <span>{isAdmin ? "You manage access and approvals" : "Your access is managed by an admin"}</span>
          </div>
          <b className={`ws-role-badge ${role}`}>{role}</b>
        </div>

        {isAdmin ? (
          <>
            <div className="ws-members-head">
              <span>People {memberCount ? `· ${memberCount}` : ""}</span>
              <button type="button" onClick={onInviteOpen} aria-expanded={inviteOpen}>
                {inviteOpen ? "Close" : "Add person"}
              </button>
            </div>
            {inviteOpen && (
              <form
                className="ws-invite-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  onInvite();
                }}
              >
                <label>
                  Work email
                  <input
                    type="email"
                    value={inviteEmail}
                    placeholder="teammate@company.com"
                    onChange={(event) => onInviteEmailChange(event.target.value)}
                    required
                  />
                </label>
                <label>
                  Role
                  <select value={inviteRole} onChange={(event) => onInviteRoleChange(event.target.value)}>
                    <option value="member">Member — can ask and request refreshes</option>
                    <option value="viewer">Viewer — can read company memory</option>
                    <option value="admin">Admin — can manage people and approve</option>
                  </select>
                </label>
                <button className="ws-primary-action" type="submit" disabled={inviting || !inviteEmail.trim()}>
                  {inviting ? "Adding…" : "Add to workspace"}
                </button>
              </form>
            )}
            {memberCount > 0 ? (
              <ul className="ws-member-list">
                {members.slice(0, 4).map((member) => (
                  <li key={member.id}>
                    <span>{member.display_name || member.email}</span>
                    <small>{member.role}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ws-rail-copy">Invite your first teammate to share this memory space.</p>
            )}
          </>
        ) : (
          <p className="ws-rail-copy">
            You can ask company memory and propose a repository refresh. An owner or admin reviews any request before it runs.
          </p>
        )}
      </section>

      <section className="ws-rail-card" aria-label="Approval inbox">
        <div className="ws-rail-section-head">
          <div>
            <p className="ws-rail-eyebrow">Approval inbox</p>
            <strong>{pendingApprovals.length ? `${pendingApprovals.length} waiting` : "All clear"}</strong>
          </div>
          <Link href="/approvals">History →</Link>
        </div>
        {inboxNote && <div className="ws-inbox-note">{inboxNote}</div>}
        {inboxError && <div className="ws-alert">{inboxError}</div>}
        {pendingApprovals.length ? (
          <div className="ws-approval-list">
            {pendingApprovals.map((request) => (
              <article className="ws-approval" key={request.id}>
                <div className="ws-approval-body">
                  <p><strong>{request.project_name || request.repository}</strong></p>
                  <small>{request.requested_by_name || "A teammate"} requested a refresh</small>
                  {request.reason && <em>&ldquo;{request.reason}&rdquo;</em>}
                </div>
                {isAdmin ? (
                  <div className="ws-approval-actions">
                    <button disabled={decidingId === request.id} onClick={() => onDecide(request, true)}>
                      Approve
                    </button>
                    <button className="danger" disabled={decidingId === request.id} onClick={() => onDecide(request, false)}>
                      Deny
                    </button>
                  </div>
                ) : (
                  <span className="ws-approval-status">With an admin</span>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="ws-rail-copy">
            {isAdmin
              ? "New refresh requests appear here for an inline decision."
              : "Your refresh requests appear here until an admin decides."}
          </p>
        )}
        {inFlight.length > 0 && (
          <p className="ws-refreshing" aria-label="Refreshes in progress">
            <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>
            {inFlight.length === 1 ? `Refreshing ${inFlight[0].repository}…` : `Refreshing ${inFlight.length} repositories…`}
          </p>
        )}
      </section>

      <section className="ws-rail-card" aria-label="Memory proposals">
        <div className="ws-rail-section-head">
          <div>
            <p className="ws-rail-eyebrow">Memory proposals</p>
            <strong>{pendingProposals.length ? `${pendingProposals.length} waiting` : "None"}</strong>
          </div>
          <Link href="/webmcp">What is this? →</Link>
        </div>
        {pendingProposals.length ? (
          <div className="ws-approval-list">
            {pendingProposals.map((proposal) => (
              <article className="ws-approval" key={proposal.id}>
                <div className="ws-approval-body">
                  <p><strong>[{proposal.kind}] {proposal.subject}</strong></p>
                  <small>
                    Proposed by {proposal.requested_by_name || "an agent"} in{" "}
                    {proposal.project_name || proposal.project_id}
                  </small>
                  <em>&ldquo;{proposal.content}&rdquo;</em>
                  {proposal.reason && <small>Why: {proposal.reason}</small>}
                </div>
                {isAdmin ? (
                  <div className="ws-approval-actions">
                    <button disabled={decidingProposalId === proposal.id} onClick={() => onDecideProposal(proposal, true)}>
                      Approve
                    </button>
                    <button className="danger" disabled={decidingProposalId === proposal.id} onClick={() => onDecideProposal(proposal, false)}>
                      Deny
                    </button>
                  </div>
                ) : (
                  <span className="ws-approval-status">With an admin</span>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p className="ws-rail-copy">
            When a browser agent proposes verified knowledge, it waits here — nothing enters
            company memory until a person approves it.
          </p>
        )}
      </section>

      <section className="ws-rail-card ws-automation-card">
        <p className="ws-rail-eyebrow">WebMCP automation</p>
        <strong>{webMCPLabel || "Browser tools unavailable"}</strong>
        <p className="ws-rail-copy">
          {webMCPLabel
            ? `${webMCPToolCount} tools are registered on this page: search, incidents, decisions, service context, dependencies, blockers, contradictions, and provenance. Writes stop at a proposal${isAdmin ? " that you can approve here." : " and wait for an admin."}`
            : "Open this workspace in a WebMCP-capable browser agent to use the page-native tools."}
        </p>
        <Link className="ws-rail-link" href="/webmcp">
          Open agent operations <span aria-hidden="true">→</span>
        </Link>
      </section>
    </aside>
  );
}

/* The completion notice. It reports what actually happened to the repository —
   branch, files, commit — because "done" without evidence is not something
   anyone should trust from an agent that edits code on its own. */
function RunCard({ run }: { run: Run }) {
  const working = !RUN_DONE.includes(run.status);

  if (working) {
    return (
      <div className="ws-run working">
        <span className="ws-run-dots" aria-hidden="true"><i /><i /><i /></span>
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

/* The API answer is written for machines as much as people: it carries a lane
   heading and inline [Source Title] markers. Both are duplicated by the sources
   list below the answer, so they are removed here rather than in the response —
   the payload stays traceable, the reading experience stays clean. */
function readable(answer: string, sources: Source[]) {
  const titles = new Set(sources.map((item) => item.source_title));
  return answer
    .split("\n")
    .filter((line) => !/^\*\*answer from current company memory\*\*$/i.test(line.trim()))
    .map((line) =>
      line.replace(/\s*\[([^[\]]+)\]/g, (marker, label) => (titles.has(label) ? "" : marker)),
    )
    .join("\n")
    .trim();
}

function AnswerBlock({
  answer,
  question,
  onCopy,
  copied,
  turnKey,
  project,
  onPick,
  onFollow,
}: {
  answer: Answer;
  question: string;
  onCopy: (handoff: Handoff, key: string, contextEventId?: string) => void;
  copied: string;
  turnKey: string;
  project: string;
  onPick: (projectId: string) => void;
  onFollow: () => void;
}) {
  const [openSources, setOpenSources] = useState(false);
  const [rated, setRated] = useState<"" | "succeeded" | "failed">("");
  const [run, setRun] = useState<Run>();
  const [runError, setRunError] = useState("");
  const sources = answer.evidence || [];

  /* The agent takes minutes, so the run is polled rather than awaited. Polling
     stops on a terminal status, and on unmount so a closed tab stops asking. */
  useEffect(() => {
    if (!run || RUN_DONE.includes(run.status)) return;
    let live = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<Run>(`/api/execute/${run.id}`);
        if (!live) return;
        setRun(next);
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
      const started = await api<Run>("/api/execute", {
        method: "POST",
        body: JSON.stringify({
          project_id: project,
          handoff: answer.handoff,
          context_event_id: answer.context_event_id,
        }),
      });
      setRun(started);
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
    /* Claiming "nothing is connected" to someone with nineteen connected
       repositories is simply false, and it sends them to a page that will not
       help. What is true is narrower: this search came back empty. */
    const searched = answer.searched_sources ?? 0;
    return (
      <div className="ws-answer">
        <div className="ws-withheld">
          <strong>I couldn&rsquo;t find this in your company&rsquo;s memory.</strong>
          {searched > 0 ? (
            <>
              <p>
                I searched {searched === 1 ? "1 connected source" : `${searched} connected sources`}{" "}
                and found nothing that answers this. Guessing about your company is worse than
                saying so &mdash; but the question may just need a name I can match on.
              </p>
              <p className="ws-withheld-try">
                Try naming the service, repository, or file &mdash; or the error you&rsquo;re
                looking at. If it lives somewhere I&rsquo;m not connected to yet, add it.
              </p>
            </>
          ) : (
            <p>
              Nothing is connected yet, so there is no memory to search. Connect the source that
              would know, and ask again.
            </p>
          )}
          <Link className="home-link" href="/ingest">
            {searched > 0 ? "Connect another source" : "Connect a source"}{" "}
            <span aria-hidden="true">→</span>
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
                <button key={option.project_id} onClick={() => onPick(option.project_id!)}>
                  <span>{option.label}</span>
                  {(option.files?.length ?? 0) > 0 && (
                    <small>{option.files!.slice(0, 2).join(", ")}</small>
                  )}
                </button>
              ) : (
                /* Nothing to click: only the asker knows the answer, so these are
                   examples of the shape it should take, not choices to make. */
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

  const memories = answer.memory_units || [];
  const relatedEntities = answer.related_entities || [];
  const supportCount = memories.length || sources.length;
  const conflictCount = answer.conflicts?.length || 0;
  const confidenceLabel = answer.trust_score?.level
    ? `${answer.trust_score.level.replace(/_/g, " ")} confidence`
    : "Source-backed answer";
  /* `likely_cause` answers "what broke". For every other kind of question the
     server fills it with a "Not applicable — …" placeholder, which is a note to
     other code, not a sentence anyone should read. Headlining it made a real,
     source-backed answer look like a refusal. */
  const diagnosis = answer.likely_cause?.startsWith("Not applicable") ? "" : answer.likely_cause;
  const trail = [
    { type: "Question", label: question },
    ...relatedEntities.slice(0, 2).map((entity) => ({ type: "Service context", label: entity })),
    ...memories.slice(0, 3).map((memory) => ({ type: memory.type, label: memory.subject })),
    ...(diagnosis ? [{ type: "Conclusion", label: diagnosis }] : []),
  ];

  return (
    <div className="ws-answer">
      <section className="intelligence-canvas" data-orb-target="canvas">
        <header className="intelligence-head">
          <div>
            <p>Intelligence Canvas</p>
            <h2>{diagnosis || "Answer from current organizational memory"}</h2>
          </div>
          <div className="intelligence-badges">
            <span className="confidence"><i />{confidenceLabel}</span>
            {supportCount > 0 && <span>{supportCount} supporting record{supportCount === 1 ? "" : "s"}</span>}
            {conflictCount > 0 && <span className="conflict">{conflictCount} conflicting source{conflictCount === 1 ? "" : "s"}</span>}
          </div>
        </header>

        {(memories.length > 0 || sources.length > 0) && (
          <div className="memory-convergence" data-orb-target="memory" aria-label="Retrieved evidence converging into the answer">
            <div className="convergence-query"><span>✦</span><strong>Evidence assembled</strong></div>
            <div className="convergence-nodes">
              {memories.slice(0, 5).map((memory, index) => (
                <article key={memory.id} style={{ "--memory-index": index } as CSSProperties}>
                  <i />
                  <span>{memory.type}</span>
                  <strong>{memory.subject}</strong>
                </article>
              ))}
              {!memories.length && sources.slice(0, 5).map((source, index) => (
                <article key={source.chunk_id} style={{ "--memory-index": index } as CSSProperties}>
                  <i />
                  <span>{source.source_type.replace(/_/g, " ")}</span>
                  <strong>{source.source_title}</strong>
                </article>
              ))}
            </div>
          </div>
        )}

        <div className="intelligence-grid">
          <article className="intelligence-reading">
            <p className="intelligence-label">Grounded answer</p>
            <MarkdownAnswer>{readable(answer.answer, sources)}</MarkdownAnswer>
          </article>

          <aside className="intelligence-evidence">
            <section>
              <p className="intelligence-label">Observed facts</p>
              {memories.length ? (
                <ul>
                  {memories.slice(0, 4).map((memory) => (
                    <li key={memory.id}><span>{memory.type}</span>{memory.content}</li>
                  ))}
                </ul>
              ) : (
                <p>No atomic memories were promoted for this answer; inspect the cited source evidence.</p>
              )}
            </section>
            {answer.safe_actions && answer.safe_actions.length > 0 && (
              <section>
                <p className="intelligence-label">Safe next actions</p>
                <ul>{answer.safe_actions.slice(0, 4).map((action) => <li key={action}>{action}</li>)}</ul>
              </section>
            )}
            {answer.approval_required && answer.approval_required.length > 0 && (
              <section className="intelligence-approval">
                <p className="intelligence-label">Approval required</p>
                <ul>{answer.approval_required.slice(0, 4).map((action) => <li key={action}>{action}</li>)}</ul>
              </section>
            )}
          </aside>
        </div>

        {trail.length > 1 && (
          <section className="investigation-trail" data-orb-target="trail" data-investigation-trail>
            <header>
              <div><p>Observable actions and evidence</p><h3>Investigation Trail</h3></div>
              <span>{trail.length} steps</span>
            </header>
            <ol>
              {trail.map((item, index) => (
                <li key={`${item.type}-${item.label}-${index}`}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div><small>{item.type}</small><strong>{item.label}</strong></div>
                </li>
              ))}
            </ol>
          </section>
        )}

        <footer className="intelligence-actions">
          <button type="button" onClick={() => setOpenSources(true)}>Inspect Evidence</button>
          <button type="button" className="quiet" onClick={onFollow}>Follow Orb</button>
          {answer.retrieval_trace?.security_trimmed && <span>Permission-trimmed before ranking</span>}
        </footer>
      </section>

      {answer.resolved_subject && (
        /* A follow-up was bound to an earlier subject. Saying which one turns a
           silent wrong guess into an obvious one the person can correct. */
        <p className="ws-scope">Answered about: {answer.resolved_subject}</p>
      )}

      {answer.answer_scope === "general_knowledge" && (
        <p className="ws-scope">General knowledge — not from your company&rsquo;s memory.</p>
      )}

      {answer.handoff && (
        <section className="ws-handoff">
          <header>
            <span>Ready for your editor</span>
            <div className="ws-handoff-actions">
              <button className="quiet" onClick={() => onCopy(answer.handoff!, turnKey, answer.context_event_id)}>
                {copied === turnKey ? "Copied" : "Copy"}
              </button>
              <button onClick={runIt} disabled={Boolean(run)}>
                {run ? "Running…" : "Do it for me"}
              </button>
            </div>
          </header>
          <strong>{answer.handoff.task}</strong>
          {answer.handoff.files.length > 0 && (
            <div className="ws-handoff-files">
              {answer.handoff.files.map((file) => (
                <code key={file}>{file}</code>
              ))}
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
          {answer.handoff.approval_required.length > 0 && (
            <small>Needs your approval before anything is applied.</small>
          )}
          {runError && <p className="ws-run-error">{runError}</p>}
          {run && <RunCard run={run} />}
        </section>
      )}

      {answer.context_event_id && (
        <div className="ws-rate">
          {rated ? (
            <span className="ws-rate-done">
              {rated === "succeeded" ? "Noted — thanks." : "Noted. I'll weigh this differently next time."}
            </span>
          ) : (
            <>
              <span>Did this work?</span>
              <button onClick={() => rate("succeeded")}>Yes</button>
              <button onClick={() => rate("failed")}>No</button>
            </>
          )}
        </div>
      )}

      {sources.length > 0 && (
        <div className={`ws-sources ${openSources ? "open" : ""}`}>
          <button onClick={() => setOpenSources((open) => !open)}>
            {sources.length} source{sources.length === 1 ? "" : "s"}
            <em aria-hidden="true">{openSources ? "▴" : "▾"}</em>
          </button>
          {openSources && (
            <ul>
              {sources.map((item) => (
                <li key={item.chunk_id}>
                  {item.source_url ? (
                    <a href={item.source_url} target="_blank" rel="noreferrer">
                      {item.source_title}
                    </a>
                  ) : (
                    <span>{item.source_title}</span>
                  )}
                  <small>{item.source_type.replace(/_/g, " ")}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
