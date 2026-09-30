"use client";

import { useCallback, useEffect } from "react";
import { useMemoryWorksTools } from "@/hooks/useMemoryWorksTools";
import { api } from "@/lib/api";
import {
  registerOrgConsoleWebMCP,
  type MemoryWorksAnswer,
  type MemoryWorksBriefing,
  type MemoryWorksBriefingInput,
  type MemoryWorksChangeSet,
  type MemoryWorksOutcomeInput,
  type MemoryWorksOutcomeReceipt,
  type MemoryWorksProposal,
  type MemoryWorksProposalInput,
  type MemoryWorksRefreshRequest,
  type MemoryWorksRelatedEntry,
  type MemoryWorksServiceContextEntry,
  type MemoryWorksUnit,
} from "@/lib/webmcp";

type Space = { id: string; name: string; repository?: string };

/* The chat page doubles as a tool provider for AI agents running in the
 * browser: the same memory, approvals, and cross-space operations a person
 * uses here are registered as structured tools, so an agent never has to
 * scrape the interface. None of it is visible — it is plumbing, and every
 * call goes through the same authorized endpoints as the buttons do. */
export function useWorkspaceTools({
  spaces,
  activeProjectId,
  isAdmin,
  ask,
}: {
  spaces: Space[];
  activeProjectId: string;
  isAdmin: boolean;
  ask: (question: string, projectId: string, scope: "workspace" | "project") => Promise<MemoryWorksAnswer>;
}) {
  const inspectChanges = useCallback(
    (projectId: string, limit: number) =>
      api<MemoryWorksChangeSet[]>(
        `/api/memory/change-sets?project_id=${encodeURIComponent(projectId)}&limit=${limit}`,
      ),
    [],
  );

  /* The pre-action briefing answers an intent rather than a question. Serving
     it opens a row in the outcome ledger, so the briefing_id an agent gets
     back is also how it reports what happened. */
  const brief = useCallback(
    (input: MemoryWorksBriefingInput) =>
      api<MemoryWorksBriefing>("/api/briefings", {
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

  const recordOutcome = useCallback(
    (input: MemoryWorksOutcomeInput) =>
      api<MemoryWorksOutcomeReceipt>("/api/briefings/outcome", {
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

  const searchMemory = useCallback(
    async (projectId: string, query: string, type?: string, limit?: number) => {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      if (projectId) params.set("project_id", projectId);
      if (type) params.set("type", type);
      params.set("limit", String(limit ?? 10));
      const response = await api<{ results: MemoryWorksUnit[] }>(`/api/memory/search?${params}`);
      return response.results || [];
    },
    [],
  );

  const getServiceContext = useCallback(async (service: string) => {
    /* One profile per authorized space; the backend trims each to the
       signed-in person's team scope. Empty profiles are skipped so an agent
       never receives a wall of nothing. */
    const response = await api<Space[]>("/api/projects");
    const entries = await Promise.all(
      (response || []).map(async (space) => {
        try {
          const profile = await api<MemoryWorksServiceContextEntry["profile"]>(
            `/api/memory/profiles/service/${encodeURIComponent(service)}?project_id=${encodeURIComponent(space.id)}`,
          );
          const total =
            (profile.current_facts || []).length +
            (profile.decisions || []).length +
            (profile.incidents || []).length +
            (profile.dependencies || []).length +
            (profile.owners || []).length +
            (profile.procedures || []).length;
          return total > 0 ? { project_id: space.id, project_name: space.name, profile } : null;
        } catch {
          return null;
        }
      }),
    );
    return entries.filter(Boolean) as MemoryWorksServiceContextEntry[];
  }, []);

  const tools = useMemoryWorksTools({
    enabled: spaces.length > 0 && Boolean(activeProjectId),
    spaces,
    activeProjectId,
    ask,
    inspectChanges,
    brief,
    recordOutcome,
    searchMemory,
    getMemory: (memoryId) => api<MemoryWorksUnit>(`/api/memory/units/${encodeURIComponent(memoryId)}`),
    getRelatedMemories: async (memoryId) => {
      const response = await api<{ related: MemoryWorksRelatedEntry[] }>(
        `/api/memory/units/${encodeURIComponent(memoryId)}/related`,
      );
      return response.related || [];
    },
    listIncidents: (projectId, service) => searchMemory(projectId, service || "", "incident", 20),
    getServiceContext,
    listDecisions: (projectId, limit) => searchMemory(projectId, "", "decision", limit ?? 10),
    proposeMemory: (input: MemoryWorksProposalInput) =>
      api<MemoryWorksProposal>("/api/memory/proposals", {
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
    listProposals: () => api<MemoryWorksProposal[]>("/api/memory/proposals").catch(() => []),
    canResolveProposals: isAdmin,
    resolveProposal: (proposalId, approved) =>
      api<MemoryWorksProposal>(`/api/memory/proposals/${encodeURIComponent(proposalId)}/resolve`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      }),
    proposeRepositoryRefresh: (projectId, reason) =>
      api<MemoryWorksRefreshRequest>("/api/repository-refresh-requests", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, reason }),
      }),
    listApprovals: async (projectId) => {
      // The backend already limits this list to requests inside projects the
      // caller can see, so it cannot surface anything unauthorized.
      const items = await api<MemoryWorksRefreshRequest[]>("/api/repository-refresh-requests");
      return items.filter((item) => item.project_id === projectId);
    },
    canResolveApprovals: isAdmin,
    resolveApproval: (requestId, approved) =>
      api<MemoryWorksRefreshRequest>(
        `/api/repository-refresh-requests/${encodeURIComponent(requestId)}/resolve`,
        { method: "POST", body: JSON.stringify({ approved }) },
      ),
  });

  /* The cross-space operations (project context, blockers, conflicts, plans)
     are registered alongside, under their own names, so an agent reaches the
     same capabilities Agent mode uses. */
  useEffect(() => {
    let alive = true;
    let dispose: () => void = () => undefined;
    registerOrgConsoleWebMCP()
      .then((registration) => {
        if (alive) dispose = registration.dispose;
        else registration.dispose();
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      dispose();
    };
  }, []);

  return tools;
}
