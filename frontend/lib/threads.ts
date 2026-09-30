"use client";

import { useSyncExternalStore } from "react";

/* Chat history, shared by the sidebar and the chat.
 *
 * Conversations live in this browser, per workspace, the way an editor keeps
 * its recent chats: the sidebar lists them and the chat reads and writes them,
 * so both need one store rather than two copies that drift. Capped so a long
 * history cannot outgrow the storage quota, and every storage access is
 * guarded — a private window or a full disk must never break asking. */

export type ThreadMode = "ask" | "agent";

export type ChatThread = {
  id: string;
  title: string;
  /* The memory space the thread was scoped to; empty means all memory. */
  projectId: string;
  scope: "workspace" | "project";
  mode: ThreadMode;
  turns: any[];
  updatedAt: number;
};

const PREFIX = "memoryworks.threads";
const LEGACY_PREFIX = "orgmemory.thread.";
const MAX_THREADS = 30;
const MAX_TURNS = 40;

type State = { workspace: string; threads: ChatThread[]; activeId: string };

let state: State = { workspace: "", threads: [], activeId: "" };
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function storageKey(workspace: string) {
  return `${PREFIX}.${workspace}`;
}

function read(workspace: string): ChatThread[] {
  try {
    const stored = window.localStorage.getItem(storageKey(workspace));
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) return parsed;
    }
    return migrateLegacy();
  } catch {
    return [];
  }
}

/* Before chats had a history, each memory space kept one running thread. Those
   become ordinary chats the first time this store loads, so nothing anyone
   asked disappears with the redesign. */
function migrateLegacy(): ChatThread[] {
  const migrated: ChatThread[] = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (!key?.startsWith(LEGACY_PREFIX)) continue;
    try {
      const turns = JSON.parse(window.localStorage.getItem(key) || "[]");
      if (!Array.isArray(turns) || !turns.length) continue;
      migrated.push({
        id: newThreadId(),
        title: titleFrom(turns[0]?.question || "Earlier chat"),
        projectId: key.slice(LEGACY_PREFIX.length),
        scope: "workspace",
        mode: "ask",
        turns: turns.map((turn: any) => ({ mode: "ask", ...turn })),
        updatedAt: Date.now() - index,
      });
    } catch {
      /* one unreadable legacy thread does not stop the rest */
    }
  }
  return migrated;
}

function write() {
  if (!state.workspace) return;
  try {
    window.localStorage.setItem(storageKey(state.workspace), JSON.stringify(state.threads));
  } catch {
    /* a full or disabled store must not break the conversation */
  }
}

export function newThreadId() {
  return `t_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

export function titleFrom(question: string) {
  const text = question.replace(/\s+/g, " ").trim();
  return text.length > 60 ? `${text.slice(0, 57)}…` : text || "New chat";
}

export const threads = {
  load(workspace: string) {
    if (!workspace || workspace === state.workspace) return;
    state = { workspace, threads: read(workspace), activeId: "" };
    emit();
  },

  /* Create-or-replace. The newest chat sorts first, like any editor's history. */
  save(thread: ChatThread) {
    const trimmed = { ...thread, turns: thread.turns.slice(-MAX_TURNS), updatedAt: Date.now() };
    const rest = state.threads.filter((item) => item.id !== thread.id);
    state = { ...state, threads: [trimmed, ...rest].slice(0, MAX_THREADS), activeId: thread.id };
    write();
    emit();
  },

  /* Patch a stored thread without opening it: an answer that lands after the
     person has moved to another chat must not pull them back. */
  update(id: string, change: (thread: ChatThread) => ChatThread) {
    const current = state.threads.find((item) => item.id === id);
    if (!current) return;
    const next = { ...change(current), updatedAt: Date.now() };
    next.turns = next.turns.slice(-MAX_TURNS);
    state = { ...state, threads: state.threads.map((item) => (item.id === id ? next : item)) };
    write();
    emit();
  },

  remove(id: string) {
    state = {
      ...state,
      threads: state.threads.filter((item) => item.id !== id),
      activeId: state.activeId === id ? "" : state.activeId,
    };
    write();
    emit();
  },

  open(id: string) {
    state = { ...state, activeId: id };
    emit();
  },

  /* A new chat is an empty screen; nothing is stored until the first question. */
  startNew() {
    state = { ...state, activeId: "" };
    emit();
  },

  get: () => state,
};

function subscribe(listener: () => void) {
  listeners.add(listener);
  // Another tab asking a question updates this tab's history too.
  const onStorage = (event: StorageEvent) => {
    if (event.key === storageKey(state.workspace)) {
      state = { ...state, threads: read(state.workspace) };
      emit();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

const serverState: State = { workspace: "", threads: [], activeId: "" };

export function useThreads(): State {
  return useSyncExternalStore(subscribe, threads.get, () => serverState);
}
