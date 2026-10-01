"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { BrandMark } from "@/components/BrandLogo";
import CommandMenu, { openCommandMenu, useCommandMenu } from "@/components/CommandMenu";
import { api } from "@/lib/api";
import { threads, useThreads } from "@/lib/threads";
import { SIDEBAR_PLACES, destinationFor } from "@/lib/workspaceMap";

/* The signed-in frame, laid out like an editor's: a short sidebar of places,
 * your chats, and what to do next — around whichever page is open.
 *
 * The sidebar deliberately does not list every page. Each place (Sources,
 * Memory, Approvals) opens on its main page and shows its other pages as tabs
 * there; everything is also one ⌘K away. Nobody has to read twenty links to
 * find the one they came for. */

const SETUP_DISMISSED_KEY = "memoryworks.setup.dismissed";
const CHATS_SHOWN = 8;

type FrameContext = { openNav: () => void };
const NavContext = createContext<FrameContext>({ openNav: () => undefined });

/* The menu button a page bar shows on narrow screens, where the sidebar is a drawer. */
export function NavToggle() {
  const { openNav } = useContext(NavContext);
  return (
    <button type="button" className="nav-toggle" onClick={openNav} aria-label="Open navigation">
      <span aria-hidden="true" />
    </button>
  );
}

type Setup = { sources: number; githubConnected: boolean; teammates: number; keys: number };

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
  const router = useRouter();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pending, setPending] = useState(0);
  const [setup, setSetup] = useState<Setup>();
  const [setupDismissed, setSetupDismissed] = useState(true);
  const [allChats, setAllChats] = useState(false);
  const [ephemeral, setEphemeral] = useState(false);
  const command = useCommandMenu();
  const history = useThreads();
  const isAdmin = user?.role === "owner" || user?.role === "admin";
  const group = destinationFor(pathname)?.group;
  const workspace = user?.workspaces?.find((item: any) => item.id === user?.active_workspace_id);
  const onChat = pathname === "/workspace";

  useEffect(() => {
    if (user?.active_workspace_id) threads.load(user.active_workspace_id);
  }, [user?.active_workspace_id]);

  useEffect(() => {
    try {
      setSetupDismissed(window.localStorage.getItem(SETUP_DISMISSED_KEY) === "1");
    } catch {
      setSetupDismissed(false);
    }
  }, []);

  // A deployment without durable storage loses what people add on restart, so
  // say so rather than let a demo pass for the real thing.
  useEffect(() => {
    api<{ storage?: string }>("/api/health")
      .then((health) => setEphemeral(health.storage === "ephemeral"))
      .catch(() => undefined);
  }, []);

  useEffect(() => setDrawerOpen(false), [pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  // Recounted on every navigation, so deciding something on /approvals clears
  // the badge — and connecting a source ticks the checklist — by the time you
  // are anywhere else.
  useEffect(() => {
    let live = true;
    const waiting = (items: any) =>
      (Array.isArray(items) ? items : []).filter((item) => item?.status === "pending_approval").length;
    Promise.all([
      api<any[]>("/api/memory/proposals").catch(() => []),
      api<any[]>("/api/repository-refresh-requests").catch(() => []),
      api<{ plans: any[] }>("/api/org/plans?status=pending_approval").catch(() => ({ plans: [] })),
      api<any[]>("/api/projects").catch(() => []),
      api<any[]>("/api/connectors").catch(() => []),
      api<any[]>("/api/keys").catch(() => []),
      isAdmin && user?.active_workspace_id
        ? api<any[]>(`/api/workspaces/${user.active_workspace_id}/members`).catch(() => [])
        : Promise.resolve([]),
    ]).then(([proposals, requests, plans, projects, connectors, keys, members]) => {
      if (!live) return;
      setPending(waiting(proposals) + waiting(requests) + waiting(plans?.plans));
      setSetup({
        sources: Array.isArray(projects) ? projects.length : 0,
        githubConnected: (connectors || []).some(
          (item: any) => item.provider === "github" && item.connected,
        ),
        teammates: Array.isArray(members) ? members.length : 0,
        keys: Array.isArray(keys) ? keys.filter((key: any) => !key.revoked_at).length : 0,
      });
    });
    return () => {
      live = false;
    };
  }, [pathname, isAdmin, user?.active_workspace_id]);

  /* Three steps, each with the one action that completes it. The third fits
     the person: an owner's next move is bringing the team in; everyone else's
     is using this memory from their own editor. */
  const steps = useMemo(() => {
    if (!setup) return [];
    return [
      {
        label: setup.githubConnected && !setup.sources ? "Choose repositories" : "Connect a source",
        done: setup.sources > 0,
        href: setup.githubConnected ? "/ingest?source=github" : "/connectors",
      },
      { label: "Ask your first question", done: history.threads.length > 0, href: "/workspace" },
      isAdmin
        ? { label: "Invite a teammate", done: setup.teammates > 1, href: "/account" }
        : { label: "Use it from your editor", done: setup.keys > 0, href: "/integrations" },
    ];
  }, [setup, history.threads.length, isAdmin]);
  const doneCount = steps.filter((step) => step.done).length;
  const nextStep = steps.find((step) => !step.done);
  const showSetup = Boolean(setup) && !setupDismissed && Boolean(nextStep);

  function dismissSetup() {
    setSetupDismissed(true);
    try {
      window.localStorage.setItem(SETUP_DISMISSED_KEY, "1");
    } catch {
      /* the card simply returns next visit */
    }
  }

  function openChat(id: string) {
    threads.open(id);
    if (!onChat) router.push("/workspace");
    setDrawerOpen(false);
  }

  function newChat() {
    threads.startNew();
    if (!onChat) router.push("/workspace");
    setDrawerOpen(false);
  }

  const chats = allChats ? history.threads : history.threads.slice(0, CHATS_SHOWN);
  const initials = (user?.display_name || user?.email || "MW")
    .split(/\s+/)
    .map((part: string) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <NavContext.Provider value={{ openNav: () => setDrawerOpen(true) }}>
      <div className="nav-frame" data-drawer={drawerOpen ? "open" : "closed"}>
        <aside className="nav-side" aria-label="Workspace navigation">
          <div className="nav-brand">
            <Link href="/workspace" aria-label="MemoryWorks home" onClick={() => threads.startNew()}>
              <BrandMark />
              <span>
                <strong>MemoryWorks</strong>
                <small>{workspace?.name || "Your workspace"}</small>
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

          <nav className="nav-primary" aria-label="Main">
            <button type="button" className="nav-item" onClick={newChat}>
              <Icon name="plus" />
              <span>New chat</span>
            </button>
            <button type="button" className="nav-item" onClick={openCommandMenu}>
              <Icon name="search" />
              <span>Search</span>
              <kbd>⌘K</kbd>
            </button>
            {SIDEBAR_PLACES.map((place) => (
              <Link
                key={place.href}
                href={place.href}
                className={`nav-item ${group === place.group ? "active" : ""}`}
                aria-current={group === place.group ? "page" : undefined}
              >
                <Icon name={place.group} />
                <span>{place.label}</span>
                {place.group === "Review" && pending > 0 && (
                  <em aria-label={`${pending} waiting`}>{pending}</em>
                )}
              </Link>
            ))}
          </nav>

          <section className="nav-chats" aria-label="Chats">
            <p className="nav-label">Chats</p>
            {chats.length ? (
              <ul>
                {chats.map((chat) => {
                  const active = onChat && chat.id === history.activeId;
                  return (
                    <li key={chat.id} className={active ? "active" : ""}>
                      <button
                        type="button"
                        onClick={() => openChat(chat.id)}
                        aria-current={active ? "page" : undefined}
                        title={chat.title}
                      >
                        {chat.mode === "agent" && <i className="nav-chat-agent" aria-label="Agent" />}
                        <span>{chat.title}</span>
                        <small>{ago(chat.updatedAt)}</small>
                      </button>
                      <button
                        type="button"
                        className="nav-chat-delete"
                        onClick={() => threads.remove(chat.id)}
                        aria-label={`Delete “${chat.title}”`}
                      >
                        ×
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="nav-empty">Your conversations will appear here.</p>
            )}
            {history.threads.length > CHATS_SHOWN && (
              <button type="button" className="nav-more" onClick={() => setAllChats((open) => !open)}>
                {allChats ? "Show fewer" : `Show all ${history.threads.length}`}
              </button>
            )}
          </section>

          {showSetup && nextStep && (
            <section className="nav-setup" aria-label="Getting started">
              <header>
                <strong>Getting started</strong>
                <span>
                  {doneCount}/{steps.length}
                  <Ring value={doneCount / steps.length} />
                </span>
                <button type="button" onClick={dismissSetup} aria-label="Hide getting started">
                  ×
                </button>
              </header>
              <Link className="nav-setup-action" href={nextStep.href}>
                {nextStep.label}
              </Link>
            </section>
          )}

          {ephemeral && (
            <p
              className="nav-storage-note"
              role="status"
              title="This deployment keeps data on temporary storage. Connections and memories can disappear when the server restarts or redeploys."
            >
              <i aria-hidden="true" />
              Temporary storage · data may reset
            </p>
          )}

          <div className="nav-account">
            <Link href="/account" className="nav-account-id">
              <span className="nav-avatar">{initials}</span>
              <span>
                <strong>{user?.display_name || user?.email || "Account"}</strong>
                <small>{user?.role || "member"}</small>
              </span>
            </Link>
            <Link href="/settings" className="nav-gear" aria-label="Settings" title="Settings">
              <Icon name="gear" />
            </Link>
          </div>
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

function ago(timestamp: number) {
  const minutes = Math.round((Date.now() - timestamp) / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function Ring({ value }: { value: number }) {
  const circumference = 2 * Math.PI * 6;
  return (
    <svg className="nav-ring" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" />
      <circle cx="8" cy="8" r="6" strokeDasharray={`${value * circumference} ${circumference}`} />
    </svg>
  );
}

/* Line icons, drawn once here rather than pulled from an icon library for five glyphs. */
function Icon({ name }: { name: string }) {
  const paths: Record<string, React.ReactNode> = {
    plus: <path d="M8 3v10M3 8h10" />,
    search: (
      <>
        <circle cx="7" cy="7" r="4.25" />
        <path d="m10.2 10.2 3 3" />
      </>
    ),
    Sources: (
      <>
        <path d="M6.5 9.5 9.5 6.5" />
        <path d="M7.5 4.5 9 3a2.5 2.5 0 0 1 3.5 3.5L11 8" />
        <path d="M8.5 11.5 7 13a2.5 2.5 0 0 1-3.5-3.5L5 8" />
      </>
    ),
    Memory: (
      <>
        <ellipse cx="8" cy="4" rx="5" ry="1.8" />
        <path d="M3 4v8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8V4" />
        <path d="M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8" />
      </>
    ),
    Review: (
      <>
        <rect x="2.75" y="2.75" width="10.5" height="10.5" rx="2.25" />
        <path d="m5.5 8.2 1.7 1.7 3.3-3.6" />
      </>
    ),
    gear: (
      <>
        <circle cx="8" cy="8" r="2" />
        <path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1M11.3 4.7l1.1-1.1" />
      </>
    ),
  };
  return (
    <svg className="nav-icon" viewBox="0 0 16 16" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}
