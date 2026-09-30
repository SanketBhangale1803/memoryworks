"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  DESTINATIONS,
  GROUP_BLURB,
  GROUP_ORDER,
  searchDestinations,
  type Destination,
  type DestinationGroup,
} from "@/lib/workspaceMap";

/* ⌘K, everywhere, over one registry.
 *
 * This is the answer to "I don't know what's going on": there is exactly one
 * thing to learn, it is always the same keystroke, and it lists the whole
 * product. A page that is hard to find is a bug in the registry, not a reason
 * for another navigation bar. */

type Row =
  | { kind: "destination"; destination: Destination }
  | { kind: "ask"; question: string };

export default function CommandMenu({
  open,
  onClose,
  isAdmin = false,
  onAsk,
  pendingApprovals = 0,
}: {
  open: boolean;
  onClose: () => void;
  isAdmin?: boolean;
  /* Present in the chat, absent on satellite pages. When absent, typing a
     question routes to the chat and lets it pick the text up there, so the
     behaviour is the same from anywhere. */
  onAsk?: (question: string) => void;
  pendingApprovals?: number;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const rows = useMemo<Row[]>(() => {
    const destinations = searchDestinations(query, isAdmin).map(
      (destination): Row => ({ kind: "destination", destination }),
    );
    const question = query.trim();
    // A query with a space in it is far more likely to be a question than a
    // page name, so it leads. One word stays a navigation query.
    const looksLikeQuestion = question.length > 2 && /\s/.test(question);
    return looksLikeQuestion
      ? [{ kind: "ask", question }, ...destinations]
      : [...destinations, ...(question.length > 2 ? [{ kind: "ask" as const, question }] : [])];
  }, [query, isAdmin]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    // The dialog mounts hidden, so focus has to wait a frame to land.
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => setActive(0), [query]);

  useEffect(() => {
    if (!open) return;
    const row = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;

  function run(row: Row) {
    onClose();
    if (row.kind === "ask") {
      if (onAsk) {
        onAsk(row.question);
        return;
      }
      try {
        window.sessionStorage.setItem("memoryworks.pending-question", row.question);
      } catch {
        /* The question is a convenience; navigation must happen regardless. */
      }
      router.push("/workspace");
      return;
    }
    router.push(row.destination.href);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      setActive((index) => (rows.length ? (index + 1) % rows.length : 0));
      return;
    }
    if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      setActive((index) => (rows.length ? (index - 1 + rows.length) % rows.length : 0));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const row = rows[active];
      if (row) run(row);
    }
  }

  // Grouping is only meaningful while browsing. Once someone types, the ranking
  // is the point and headers would fight it.
  const grouped = !query.trim();
  let cursor = -1;

  return (
    <div className="cmdk-scrim" onClick={onClose} role="presentation">
      <div
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Command menu"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="cmdk-input">
          <span aria-hidden="true">✦</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Ask a question, or jump anywhere…"
            aria-label="Ask a question or search the workspace"
            autoComplete="off"
            spellCheck={false}
          />
          <kbd>esc</kbd>
        </div>

        <div className="cmdk-list" ref={listRef} role="listbox" aria-label="Results">
          {!rows.length && (
            <p className="cmdk-empty">
              Nothing matches “{query}”. Press <kbd>Enter</kbd> to ask it as a question instead.
            </p>
          )}

          {grouped
            ? GROUP_ORDER.map((group) => {
                const items = DESTINATIONS.filter(
                  (item) => item.group === group && (!item.adminOnly || isAdmin),
                );
                if (!items.length) return null;
                return (
                  <section key={group}>
                    <header className="cmdk-group">
                      <strong>{group}</strong>
                      <span>{GROUP_BLURB[group as DestinationGroup]}</span>
                    </header>
                    {items.map((destination) => {
                      cursor += 1;
                      return (
                        <DestinationRow
                          key={destination.href}
                          destination={destination}
                          index={cursor}
                          active={cursor === active}
                          badge={
                            destination.href === "/approvals" && pendingApprovals > 0
                              ? pendingApprovals
                              : 0
                          }
                          onHover={setActive}
                          onSelect={() => run({ kind: "destination", destination })}
                        />
                      );
                    })}
                  </section>
                );
              })
            : rows.map((row, index) =>
                row.kind === "ask" ? (
                  <AskRow
                    key="ask"
                    question={row.question}
                    index={index}
                    active={index === active}
                    onHover={setActive}
                    onSelect={() => run(row)}
                  />
                ) : (
                  <DestinationRow
                    key={row.destination.href}
                    destination={row.destination}
                    index={index}
                    active={index === active}
                    badge={
                      row.destination.href === "/approvals" && pendingApprovals > 0
                        ? pendingApprovals
                        : 0
                    }
                    onHover={setActive}
                    onSelect={() => run(row)}
                  />
                ),
              )}
        </div>

        <footer className="cmdk-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
          <em>{DESTINATIONS.length} places · one keystroke</em>
        </footer>
      </div>
    </div>
  );
}

function DestinationRow({
  destination,
  index,
  active,
  badge,
  onHover,
  onSelect,
}: {
  destination: Destination;
  index: number;
  active: boolean;
  badge: number;
  onHover: (index: number) => void;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      data-index={index}
      className={`cmdk-row ${active ? "active" : ""}`}
      onMouseMove={() => onHover(index)}
      onClick={onSelect}
    >
      <span className="cmdk-row-main">
        <strong>{destination.title}</strong>
        <small>{destination.summary}</small>
      </span>
      {badge > 0 && <em className="cmdk-badge">{badge}</em>}
      <code>{destination.href}</code>
    </button>
  );
}

function AskRow({
  question,
  index,
  active,
  onHover,
  onSelect,
}: {
  question: string;
  index: number;
  active: boolean;
  onHover: (index: number) => void;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      data-index={index}
      className={`cmdk-row cmdk-ask ${active ? "active" : ""}`}
      onMouseMove={() => onHover(index)}
      onClick={onSelect}
    >
      <span className="cmdk-row-main">
        <strong>Ask company memory</strong>
        <small>“{question}”</small>
      </span>
      <code>↵</code>
    </button>
  );
}

/* One listener for the whole app. Every surface that wants the menu renders
   <CommandMenu> and calls this, rather than each re-deriving the shortcut. */
const OPEN_EVENT = "memoryworks:open-command-menu";

/* Opens whichever menu the current page mounted, from anywhere — the sidebar's
   search button has no menu of its own to hold. */
export function openCommandMenu() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

export function useCommandMenu() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
      }
    }
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_EVENT, onOpen);
    };
  }, []);
  return { open, setOpen, close: () => setOpen(false) };
}
