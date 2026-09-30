"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { openCommandMenu } from "@/components/CommandMenu";
import { NavToggle } from "@/components/WorkspaceFrame";
import { api } from "@/lib/api";
import { destinationFor, hubTabs } from "@/lib/workspaceMap";

/* The bar on top of every page that is not the chat: which page this is, the
 * other pages in the same place as tabs, and ⌘K. On the Sources pages it also
 * answers the first question anyone has there — what is already connected. */
export default function PageBar({ pathname }: { pathname: string }) {
  const destination = destinationFor(pathname);
  const tabs = hubTabs(pathname);
  const [connected, setConnected] = useState<{ on: number; total: number }>();

  useEffect(() => {
    if (destination?.group !== "Sources") return;
    let live = true;
    api<any[]>("/api/connectors")
      .then((items) => {
        if (live) setConnected({ on: items.filter((item) => item.connected).length, total: items.length });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [destination?.group, pathname]);

  return (
    <div className="page-top">
      <header className="ws-bar page-bar">
        <div className="page-bar-id">
          <NavToggle />
          <strong>{destination?.title || "MemoryWorks"}</strong>
        </div>
        <div className="ws-controls">
          {connected && (
            <span className={`page-status ${connected.on ? "on" : ""}`}>
              {connected.on} of {connected.total} sources connected
            </span>
          )}
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
      {tabs.length > 0 && (
        <nav className="hub-nav hub-tabs" aria-label={destination?.group}>
          {tabs.map((tab) => (
            <Link
              key={tab.href}
              href={tab.href}
              className={tab.href === destination?.href ? "active" : ""}
              aria-current={tab.href === destination?.href ? "page" : undefined}
            >
              {tab.navLabel || tab.title}
            </Link>
          ))}
        </nav>
      )}
    </div>
  );
}
