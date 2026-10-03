"use client";

import { useEffect, useState } from "react";
import MarkdownAnswer from "@/components/MarkdownAnswer";
import { API, api } from "@/lib/api";

/* Ask mode, narrated.
 *
 * A sourced answer takes a few seconds: search, read, write, check. Behind a
 * silent spinner those seconds read as "stuck" — or worse, as a black box that
 * produced an answer from nowhere. So the server reports each stage as it
 * starts and the model's words as it writes them, and this renders both: the
 * trail of what happened, the model's thinking, and the answer forming.
 *
 * The draft is a preview. The answer that lands at the end is the one the
 * server checked against its sources, and it replaces the draft. */

export type TraceStep = { label: string; detail?: string; at: number };

export type Trace = {
  steps: TraceStep[];
  thinking: string;
  draft: string;
  started: number;
  ms?: number;
};

type StreamEvent =
  | { type: "step"; label: string; detail?: string }
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "draft_reset" }
  | { type: "done"; answer: any }
  | { type: "error"; message: string }
  | { type: "ping" };

export function emptyTrace(): Trace {
  return { steps: [], thinking: "", draft: "", started: Date.now() };
}

/* Fold one server event into the trace. Pure, so React state updates stay simple. */
export function applyEvent(trace: Trace, event: StreamEvent): Trace {
  switch (event.type) {
    case "step":
      return { ...trace, steps: [...trace.steps, { label: event.label, detail: event.detail, at: Date.now() }] };
    case "text":
      return { ...trace, draft: trace.draft + event.text };
    case "thinking":
      return { ...trace, thinking: trace.thinking + event.text };
    case "draft_reset":
      return { ...trace, draft: "", thinking: "" };
    default:
      return trace;
  }
}

/* POST /api/ask/stream and report every event; resolves with the final answer.
   A server without the stream (an older deployment) gets the plain request. */
export async function streamAnswer<T>(body: unknown, onEvent: (event: StreamEvent) => void): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}/api/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(`Cannot reach the MemoryWorks API at ${API}. Check that the backend is running and refresh the page.`);
  }
  if (response.status === 404 || response.status === 405) {
    return api<T>("/api/ask", { method: "POST", body: JSON.stringify(body) });
  }
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline !== -1) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
      if (!line) continue;
      let event: StreamEvent;
      try {
        event = JSON.parse(line);
      } catch {
        continue;
      }
      if (event.type === "done") return event.answer as T;
      if (event.type === "error") throw new Error(event.message || "MemoryWorks could not finish this answer.");
      onEvent(event);
    }
  }
  throw new Error("The answer was cut off before it finished. Try asking again.");
}

/* Citations stream as [S3] and are resolved to source names only once the
   answer is final, so the preview hides them rather than showing raw markers. */
function withoutDraftCitations(text: string) {
  return text.replace(/\s*\[S\d+\]/g, "");
}

export function AskTrace({ trace, running }: { trace: Trace; running: boolean }) {
  const [open, setOpen] = useState(running);
  // Open while it works, so the work is visible; folded once the answer lands.
  useEffect(() => setOpen(running), [running]);

  if (!trace.steps.length) return null;
  const current = trace.steps.at(-1)!;
  const seconds = ((trace.ms ?? Date.now() - trace.started) / 1000).toFixed(1);
  const thinking = trace.thinking.trim();

  return (
    <div className={`ws-steps ask ${open ? "open" : ""}`}>
      <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        {running ? (
          <>
            <span className="ws-working-dots" aria-hidden="true"><i /><i /><i /></span>
            {current.label}…
          </>
        ) : (
          <>
            Worked through {trace.steps.length} step{trace.steps.length === 1 ? "" : "s"}
            <small>{seconds}s</small>
          </>
        )}
        <em aria-hidden="true">{open ? "▴" : "▾"}</em>
      </button>
      {open && (
        <ol aria-live="polite">
          {trace.steps.map((step, index) => {
            const last = index === trace.steps.length - 1;
            return (
              <li key={`${step.label}-${index}`} className={running && last ? "running" : "done"}>
                <strong>{step.label}</strong>
                {step.detail && <p>{step.detail}</p>}
                {last && thinking && (
                  <p className="ws-step-thought">{running ? thinking.slice(-600) : thinking}</p>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

export function LiveDraft({ text }: { text: string }) {
  if (!text.trim()) return null;
  return (
    <div className="ws-answer">
      <div className="ws-reply ws-draft" aria-live="off">
        <MarkdownAnswer>{withoutDraftCitations(text)}</MarkdownAnswer>
        <span className="ws-caret" aria-hidden="true" />
      </div>
    </div>
  );
}
