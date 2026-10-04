"use client";

import Link from "next/link";
import { useEffect } from "react";
import { API, api } from "@/lib/api";

/* An import asked for in the chat ("import the docs from my Google Drive").
   The server starts it and returns this; the card follows the job until it
   lands, so the reply reads as done or failed rather than "started". */
export type ChatImport = {
  matched: true;
  source: string;
  status: "running" | "queued" | "succeeded" | "failed" | "needs_connection" | "unsupported" | "nothing";
  message: string;
  label?: string;
  files?: { id: string; name: string; url: string }[];
  job?: { kind: "ingest" | "sync"; id: string };
  action?: { label: string; href: string };
  result?: { memories?: number; chunks?: number; warnings?: string[]; failed?: string[] };
};

/* Messages worth sending to the import endpoint. The server decides what is
   really an import; this only saves a round trip on ordinary questions. */
export const IMPORT_HINT = /\b(import|ingest|index|add|sync|pull|bring|load|learn|remember)\b/i;

const POLL_MS = 2500;
const SHOWN_FILES = 8;

export function ImportTurn({
  state,
  onUpdate,
}: {
  state: ChatImport;
  onUpdate: (next: ChatImport) => void;
}) {
  // Follows the job from here rather than from the request that started it,
  // so a reload mid-import picks the progress back up.
  useEffect(() => {
    if (!state.job || (state.status !== "running" && state.status !== "queued")) return;
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      try {
        const next = await follow(state);
        if (stopped) return;
        if (next) onUpdate(next);
        else window.setTimeout(tick, POLL_MS);
      } catch {
        if (!stopped) window.setTimeout(tick, POLL_MS * 2);
      }
    };
    const timer = window.setTimeout(tick, POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.job?.id, state.status]);

  const working = state.status === "running" || state.status === "queued";
  const files = state.files || [];
  const failed = new Set(state.result?.failed || []);
  const href = state.action?.href || "";

  return (
    <div className={`ws-import ${state.status}`} role="status" aria-live="polite">
      <p className="ws-import-message">
        {working && <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>}
        {state.message}
      </p>
      {files.length > 0 && (
        <ul className="ws-import-files">
          {files.slice(0, SHOWN_FILES).map((file) => (
            <li key={file.id} className={failed.has(file.id) ? "failed" : ""}>
              {file.url ? <a href={file.url} target="_blank" rel="noreferrer">{file.name}</a> : file.name}
              {failed.has(file.id) && <small> · couldn’t be read</small>}
            </li>
          ))}
          {files.length > SHOWN_FILES && <li className="more">and {files.length - SHOWN_FILES} more</li>}
        </ul>
      )}
      {!!state.result?.warnings?.length && (
        <ul className="ws-import-warnings">
          {state.result.warnings.slice(0, 5).map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      {state.action && (
        href.startsWith("/api/")
          ? <a className="button secondary" href={`${API}${href}`}>{state.action.label}</a>
          : <Link className="button secondary" href={href}>{state.action.label}</Link>
      )}
    </div>
  );
}

/* One look at the job. Returns the finished import, or null while it runs. */
async function follow(state: ChatImport): Promise<ChatImport | null> {
  const job = state.job!;
  if (job.kind === "ingest") {
    const record = await api<any>(`/api/ingest/jobs/${job.id}`);
    if (record.status === "failed") {
      return { ...state, status: "failed", message: `Couldn’t import ${state.label}: ${record.error || "the import failed"}.` };
    }
    if (record.status !== "succeeded") return null;
    const result = record.result || {};
    const memories = result.memory_units_created ?? 0;
    const files = result.files_scanned;
    return {
      ...state,
      status: "succeeded",
      message:
        `Imported ${state.label}` +
        (files !== undefined ? `: ${files} file${files === 1 ? "" : "s"} read, ` : ": ") +
        `${memories} memor${memories === 1 ? "y" : "ies"} recorded. Ask about it now.`,
      result: { memories, chunks: result.knowledge_chunks_created },
    };
  }
  const record = await api<any>(`/api/connector-sync-jobs/${job.id}`);
  // A grant that needs reconnecting won't recover on retry; say so now.
  if (record.status === "failed" || /reconnect/i.test(record.last_error || "")) {
    return {
      ...state,
      status: "failed",
      message: record.last_error || "The import failed.",
      action: /reconnect/i.test(record.last_error || "")
        ? { label: "Open Sources", href: "/connectors" }
        : state.action,
    };
  }
  if (record.status !== "succeeded") return null;
  const failures: string[] = record.cursor?.failures || [];
  const failedIds = failures.map((line) => line.match(/^file (\S+):/)?.[1]).filter(Boolean) as string[];
  const total = state.files?.length || 0;
  const imported = total - new Set(failedIds).size;
  return {
    ...state,
    status: "succeeded",
    message:
      `Imported ${imported} of ${total} file${total === 1 ? "" : "s"} from ${state.label || "the source"}.` +
      (imported ? " Ask about them now." : ""),
    result: { failed: failedIds },
  };
}
