"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  registerMemoryWorksTools,
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
  type MemoryWorksSpace,
  type MemoryWorksUnit,
  type MemoryWorksAnswer,
  type WebMCPActivity,
} from "@/lib/webmcp";

type HookOptions = {
  enabled: boolean;
  spaces: MemoryWorksSpace[];
  activeProjectId: string;
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
};

export type WebMCPStatus = "idle" | "registering" | "ready" | "unsupported" | "error";

export function useMemoryWorksTools(options: HookOptions) {
  const optionsRef = useRef(options);
  const [status, setStatus] = useState<WebMCPStatus>("idle");
  const [activity, setActivity] = useState<WebMCPActivity>();
  const [activityLog, setActivityLog] = useState<WebMCPActivity[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const stored = JSON.parse(window.sessionStorage.getItem("orgmemory.webmcp-activity") || "[]");
      return Array.isArray(stored) ? stored.slice(-24) : [];
    } catch {
      return [];
    }
  });
  const [toolCount, setToolCount] = useState(0);
  optionsRef.current = options;

  const spacesKey = useMemo(
    () => options.spaces.map((space) => `${space.id}:${space.name}:${space.repository || ""}`).join("|"),
    [options.spaces],
  );

  useEffect(() => {
    if (!options.enabled) {
      setStatus("idle");
      setToolCount(0);
      return;
    }

    let current = true;
    let dispose: () => void = () => undefined;
    setStatus("registering");

    registerMemoryWorksTools({
      spaces: optionsRef.current.spaces,
      getActiveProjectId: () => optionsRef.current.activeProjectId,
      ask: (...args) => optionsRef.current.ask(...args),
      inspectChanges: (...args) => optionsRef.current.inspectChanges(...args),
      brief: (...args) => optionsRef.current.brief(...args),
      recordOutcome: (...args) => optionsRef.current.recordOutcome(...args),
      searchMemory: (...args) => optionsRef.current.searchMemory(...args),
      getMemory: (...args) => optionsRef.current.getMemory(...args),
      getRelatedMemories: (...args) => optionsRef.current.getRelatedMemories(...args),
      listIncidents: (...args) => optionsRef.current.listIncidents(...args),
      getServiceContext: (...args) => optionsRef.current.getServiceContext(...args),
      listDecisions: (...args) => optionsRef.current.listDecisions(...args),
      proposeMemory: (...args) => optionsRef.current.proposeMemory(...args),
      listProposals: optionsRef.current.listProposals
        ? (...args) => optionsRef.current.listProposals!(...args)
        : undefined,
      canResolveProposals: optionsRef.current.canResolveProposals,
      resolveProposal: optionsRef.current.resolveProposal
        ? (...args) => optionsRef.current.resolveProposal!(...args)
        : undefined,
      proposeRepositoryRefresh: (...args) => optionsRef.current.proposeRepositoryRefresh(...args),
      listApprovals: optionsRef.current.listApprovals
        ? (...args) => optionsRef.current.listApprovals!(...args)
        : undefined,
      canResolveApprovals: optionsRef.current.canResolveApprovals,
      resolveApproval: optionsRef.current.resolveApproval
        ? (...args) => optionsRef.current.resolveApproval!(...args)
        : undefined,
      onActivity: (next) => {
        if (!current) return;
        setActivity(next);
        setActivityLog((events) => {
          const index = events.findIndex((event) => event.id === next.id);
          const updated = index === -1
            ? [...events, next]
            : events.map((event, eventIndex) => (eventIndex === index ? next : event));
          const bounded = updated.slice(-24);
          try {
            window.sessionStorage.setItem("orgmemory.webmcp-activity", JSON.stringify(bounded));
          } catch {
            /* Activity visibility must not depend on storage availability. */
          }
          return bounded;
        });
        window.dispatchEvent(new CustomEvent("orgmemory:webmcp-activity", { detail: next }));
      },
    })
      .then((registration) => {
        if (!current) {
          registration.dispose();
          return;
        }
        dispose = registration.dispose;
        setStatus(registration.supported ? "ready" : "unsupported");
        setToolCount(registration.toolCount);
      })
      .catch(() => {
        if (current) setStatus("error");
      });

    return () => {
      current = false;
      dispose();
    };
  // A session can hydrate before its workspace role arrives. Re-register when
  // that role becomes known so an admin receives the decision tools, while a
  // member never does; server-side authorization remains the final boundary.
  }, [options.enabled, options.canResolveApprovals, options.canResolveProposals, spacesKey]);

  return {
    status,
    activity,
    activityLog,
    toolCount,
    clearActivity: () => {
      setActivityLog([]);
      try {
        window.sessionStorage.removeItem("orgmemory.webmcp-activity");
      } catch {
        /* no-op */
      }
    },
  };
}
