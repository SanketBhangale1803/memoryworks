"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import CommandMenu, { openCommandMenu, useCommandMenu } from "@/components/CommandMenu";
import { RunbookMark } from "@/components/RunbookLogo";
import { api } from "@/lib/api";
import {
  DESTINATIONS,
  GROUP_ORDER,
  destinationFor,
  type DestinationGroup,
} from "@/lib/workspaceMap";

/* The signed-in frame: a sidebar that lists every page, around whatever page is open.
 *
 * ⌘K reaches everything, but only for people who know it exists. The sidebar is
 * the same registry laid out where anyone can see it, so no part of the product
 * depends on a shortcut to be found. It reads lib/workspaceMap.ts and nothing
 * else: adding a destination there adds it here. */

const COLLAPSED_KEY = "orgmemory.nav.collapsed";

type FrameContext = { openNav: () => void; inFrame: boolean };
const NavContext = createContext<FrameContext>({ openNav: () => undefined, inFrame: false });

/* For pages that also render outside the frame (the public WebMCP demo): inside
   it, the brand and the way back belong to the sidebar, not the page. */
export function useInWorkspaceFrame() {
  return useContext(NavContext).inFrame;
}

/* The menu button a page bar shows on narrow screens, where the sidebar is a drawer. */
export function NavToggle() {
  const { openNav } = useContext(NavContext);
  return (
    <button type="button" className="nav-toggle" onClick={openNav} aria-label="Open navigation">
      <span aria-hidden="true" />
    </button>
  );
}

export default function WorkspaceFrame({
  user,
  ownsCommandMenu,
  children,
}: {
  user: any;
  /* The chat mounts its own menu (it can answer a typed question in place),
     so the frame only mounts one for the pages that do not. */
  ownsCommandMenu: boolean;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<DestinationGroup[]>([]);
  const [pending, setPending] = useState(0);
  const command = useCommandMenu();
  const isAdmin = user?.role === "owner" || user?.role === "admin";
  const current = destinationFor(pathname)?.href;
  const workspace = user?.workspaces?.find((item: any) => item.id === user?.active_workspace_id);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(COLLAPSED_KEY) || "[]");
      if (Array.isArray(saved)) setCollapsed(saved);
    } catch {
      /* A remembered layout is a convenience; the default shows everything. */
    }
  }, []);

  useEffect(() => setDrawerOpen(false), [pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  // Recounted on every navigation, so deciding something on /approvals clears
  // the badge by the time you are anywhere else.
  useEffect(() => {
    let live = true;
    const waiting = (items: any[]) =>
      (Array.isArray(items) ? items : []).filter((item) => item?.status === "pending_approval").length;
    Promise.all([
      api<any[]>("/api/memory/proposals").catch(() => []),
      api<any[]>("/api/repository-refresh-requests").catch(() => []),
    ]).then(([proposals, requests]) => {
      if (live) setPending(waiting(proposals) + waiting(requests));
    });
    return () => {
      live = false;
    };
  }, [pathname]);

  const toggleGroup = useCallback((group: DestinationGroup) => {
    setCollapsed((previous) => {
      const next = previous.includes(group)
        ? previous.filter((item) => item !== group)
        : [...previous, group];
      try {
        window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next));
      } catch {
        /* Storage can be unavailable in private windows; the toggle still works. */
      }
      return next;
    });
  }, []);

  const groups = useMemo(
    () =>
      GROUP_ORDER.map((group) => ({
        group,
        items: DESTINATIONS.filter(
          (item) => item.group === group && (!item.adminOnly || isAdmin),
        ),
      })).filter((entry) => entry.items.length),
    [isAdmin],
  );

  const initials = (user?.display_name || user?.email || "OM")
    .split(/\s+/)
    .map((part: string) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <NavContext.Provider value={{ openNav: () => setDrawerOpen(true), inFrame: true }}>
      <div className="nav-frame" data-drawer={drawerOpen ? "open" : "closed"}>
        <aside className="nav-side" aria-label="Workspace navigation">
          <div className="nav-brand">
            <Link href="/workspace" aria-label="OrgMemory home">
              <RunbookMark />
              <span>
                <strong>OrgMemory</strong>
                <small>{workspace?.name || "Company brain"}</small>
              </span>
            </Link>
            <button
              type="button"
              className="nav-close"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close navigation"
            >
              ×
            </button>
          </div>

          <button type="button" className="nav-search" onClick={openCommandMenu}>
            <span>Search or jump to…</span>
            <kbd>⌘K</kbd>
          </button>

          <nav className="nav-groups">
            {groups.map(({ group, items }) => {
              const closed = collapsed.includes(group);
              const holdsCurrent = items.some((item) => item.href === current);
              return (
                <section key={group} className="nav-group">
                  <button
                    type="button"
                    className="nav-group-head"
                    aria-expanded={!closed || holdsCurrent}
                    onClick={() => toggleGroup(group)}
                  >
                    <span>{group}</span>
                    <i aria-hidden="true" />
                  </button>
                  {/* The group holding the open page never hides it. */}
                  {(!closed || holdsCurrent) && (
                    <ul>
                      {items
                        .filter((item) => !closed || item.href === current)
                        .map((item) => (
                          <li key={item.href}>
                            <Link
                              href={item.href}
                              className={item.href === current ? "active" : ""}
                              aria-current={item.href === current ? "page" : undefined}
                              title={item.summary}
                            >
                              <span>{item.navLabel || item.title}</span>
                              {item.href === "/approvals" && pending > 0 && (
                                <em aria-label={`${pending} waiting`}>{pending}</em>
                              )}
                            </Link>
                          </li>
                        ))}
                    </ul>
                  )}
                </section>
              );
            })}
          </nav>

          <Link href="/account" className="nav-account">
            <span className="nav-avatar">{initials}</span>
            <span>
              <strong>{user?.display_name || user?.email || "Account"}</strong>
              <small>{user?.role || "member"}</small>
            </span>
          </Link>
        </aside>

        <button
          type="button"
          className="nav-scrim"
          aria-label="Close navigation"
          tabIndex={-1}
          onClick={() => setDrawerOpen(false)}
        />

        <div className="nav-main">{children}</div>
      </div>
      {ownsCommandMenu && (
        <CommandMenu
          open={command.open}
          onClose={command.close}
          isAdmin={isAdmin}
          pendingApprovals={pending}
        />
      )}
    </NavContext.Provider>
  );
}
