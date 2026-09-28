"use client";

import Link from "next/link";
import { openCommandMenu } from "@/components/CommandMenu";
import { NavToggle } from "@/components/WorkspaceFrame";
import { destinationFor } from "@/lib/workspaceMap";

/* The bar on top of every page that is not the chat: where you are, what the
   page is for, and the two ways out — the sidebar (a drawer on narrow screens)
   and ⌘K. Navigation itself lives in the frame, not here. */
export default function ChatBackBar({ title, pathname }: { title: string; pathname: string }) {
  const destination = destinationFor(pathname);
  return (
    <header className="ws-bar page-bar">
      <div className="page-bar-id">
        <NavToggle />
        <span>
          {destination && <small>{destination.group}</small>}
          <strong>{title}</strong>
        </span>
      </div>
      <div className="ws-controls">
        <Link className="ws-pill quiet" href="/workspace">
          <span>Ask a question</span>
        </Link>
        <button
          type="button"
          className="ws-pill ws-jump"
          onClick={openCommandMenu}
          title="Jump anywhere, or ask a question"
        >
          <span>Jump to…</span>
          <kbd>⌘K</kbd>
        </button>
      </div>
    </header>
  );
}
