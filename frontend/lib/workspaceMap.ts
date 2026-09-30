/* One registry of every place in the product.
 *
 * Everything that navigates reads this file: the sidebar, the command menu, the
 * page title bar, and the tab strip on each hub. Adding a route here makes it
 * reachable everywhere at once, and forgetting to add one is visible
 * immediately because the page loses its title.
 *
 * The product is one chat with three places behind it — where knowledge comes
 * from, what memory holds, and what is waiting on a person. Each group is one
 * of those places; the sidebar shows the group's first page and the hub tabs
 * show the rest, so nobody has to scan twenty links to find one.
 */

export type DestinationGroup = "Chat" | "Sources" | "Memory" | "Review" | "Settings";

export type Destination = {
  href: string;
  /* What the title bar shows on the page itself. */
  title: string;
  /* The shorter name the tabs and sidebar use when the title would wrap. */
  navLabel?: string;
  /* What it does, in the command menu. Written for someone who has not seen the
     page yet, which rules out restating the title with different words. */
  summary: string;
  group: DestinationGroup;
  /* Extra search terms — what someone would type when they do not know the name
     we chose. "logs" finds the audit trail; "repo" finds memory spaces. */
  keywords?: string[];
  /* Owners and admins only. Server-side authorization is still the boundary;
     this only stops us advertising a door that will not open. */
  adminOnly?: boolean;
};

export const DESTINATIONS: Destination[] = [
  {
    href: "/workspace",
    title: "Chat",
    summary: "Ask your company anything, or let the agent work across every space.",
    group: "Chat",
    keywords: ["home", "ask", "question", "chat", "search", "agent"],
  },
  {
    href: "/work",
    title: "Hand off work",
    summary: "Turn an outcome you describe into a source-backed brief for an AI coding agent.",
    group: "Chat",
    keywords: ["handoff", "packet", "agent", "task", "brief", "memory work"],
  },
  {
    href: "/loop",
    title: "What worked",
    summary: "Which answers people acted on, and which of those actually worked.",
    group: "Chat",
    keywords: ["outcomes", "ledger", "feedback", "loop", "skills", "precedent"],
  },

  {
    href: "/connectors",
    title: "Sources",
    summary: "Connect GitHub, Slack, Google Drive, Notion, and the other systems your team uses.",
    group: "Sources",
    keywords: ["integrations", "github", "slack", "oauth", "connect", "sources"],
  },
  {
    href: "/ingest",
    title: "Add knowledge",
    summary: "Pick a repository, upload documents, paste a decision, or read a web page.",
    group: "Sources",
    keywords: ["upload", "import", "github", "repository", "file", "paste", "new"],
  },
  {
    href: "/jobs",
    title: "Sync status",
    navLabel: "Sync",
    summary: "What is being read into memory right now, and anything that failed.",
    group: "Sources",
    keywords: ["status", "queue", "sync", "progress", "jobs", "ingestion", "failed"],
  },
  {
    href: "/integrations",
    title: "AI tools",
    summary: "Use this memory from Claude Code, Cursor, VS Code, Claude, or ChatGPT.",
    group: "Sources",
    keywords: ["mcp", "claude", "chatgpt", "cursor", "vscode", "ide", "editor", "client"],
  },
  {
    href: "/keys",
    title: "API keys",
    summary: "Issue and revoke keys for the SDK, CLI, and MCP clients.",
    group: "Sources",
    keywords: ["token", "credentials", "api", "sdk"],
  },

  {
    href: "/memories",
    title: "Memory",
    navLabel: "Memories",
    summary: "Every fact, decision, owner, and dependency MemoryWorks learned, with its source.",
    group: "Memory",
    keywords: ["facts", "units", "browse", "knowledge", "memories"],
  },
  {
    href: "/graph",
    title: "Graph",
    summary: "How services, people, repositories, and decisions connect.",
    group: "Memory",
    keywords: ["entities", "relationships", "map", "visual", "blast radius"],
  },
  {
    href: "/profiles",
    title: "Profiles",
    summary: "The current picture of the company, a project, a repository, or a service.",
    group: "Memory",
    keywords: ["company", "service", "summary", "current"],
  },
  {
    href: "/projects",
    title: "Spaces",
    summary: "The repositories and projects memory is organised into.",
    group: "Memory",
    keywords: ["repos", "repositories", "spaces", "projects"],
  },
  {
    href: "/conflicts",
    title: "Conflicts",
    summary: "Where two sources disagree about the same thing.",
    group: "Memory",
    keywords: ["contradictions", "disagree", "stale"],
  },

  {
    href: "/approvals",
    title: "Approvals",
    summary: "Everything waiting on a person: proposed memories, agent changes, and refreshes.",
    group: "Review",
    keywords: ["inbox", "pending", "approve", "deny", "proposals", "review"],
  },
  {
    href: "/audit",
    title: "Audit log",
    summary: "The record of everything read, written, approved, and denied.",
    group: "Review",
    keywords: ["history", "log", "events", "compliance", "trail"],
  },

  {
    href: "/settings",
    title: "Settings",
    summary: "Model keys and how this workspace answers.",
    group: "Settings",
    keywords: ["preferences", "config", "model", "keys"],
  },
  {
    href: "/account",
    title: "Account & people",
    navLabel: "People",
    summary: "Your identity, your workspaces, and who else is in this one.",
    group: "Settings",
    keywords: ["profile", "team", "members", "invite", "role", "logout"],
  },
];

export const GROUP_ORDER: DestinationGroup[] = ["Chat", "Sources", "Memory", "Review", "Settings"];

export const GROUP_BLURB: Record<DestinationGroup, string> = {
  Chat: "Ask, or hand work to an agent.",
  Sources: "Where knowledge comes from, and where your tools reach it.",
  Memory: "What MemoryWorks knows, and where it came from.",
  Review: "What needs a person's decision.",
  Settings: "You, your team, and how this workspace runs.",
};

/* The sidebar's short list: one entry per place, pointing at the place's first page. */
export const SIDEBAR_PLACES: { group: DestinationGroup; label: string; href: string }[] = [
  { group: "Sources", label: "Sources", href: "/connectors" },
  { group: "Memory", label: "Memory", href: "/memories" },
  { group: "Review", label: "Approvals", href: "/approvals" },
];

export function destinationFor(pathname: string): Destination | undefined {
  const exact = DESTINATIONS.find((item) => item.href === pathname);
  if (exact) return exact;
  return DESTINATIONS.filter((item) => item.href !== "/" && pathname.startsWith(`${item.href}/`))
    .sort((left, right) => right.href.length - left.href.length)
    .at(0);
}

export function titleFor(pathname: string): string {
  return destinationFor(pathname)?.title || "";
}

/* The sibling pages shown as tabs on a hub page — everything in its group. */
export function hubTabs(pathname: string): Destination[] {
  const group = destinationFor(pathname)?.group;
  if (!group || group === "Chat") return [];
  const tabs = DESTINATIONS.filter((item) => item.group === group);
  return tabs.length > 1 ? tabs : [];
}

/* Ranked so an exact title match always wins over a keyword brush. Someone who
   types "graph" wants the graph, not every page that mentions one. */
export function searchDestinations(query: string, isAdmin: boolean): Destination[] {
  const allowed = DESTINATIONS.filter((item) => !item.adminOnly || isAdmin);
  const needle = query.trim().toLowerCase();
  if (!needle) return allowed;
  const scored = allowed
    .map((item) => ({ item, score: score(item, needle) }))
    .filter((entry) => entry.score > 0);
  scored.sort((left, right) => right.score - left.score);
  return scored.map((entry) => entry.item);
}

function score(item: Destination, needle: string): number {
  const title = item.title.toLowerCase();
  if (title === needle) return 100;
  if (title.startsWith(needle)) return 80;
  if (title.includes(needle)) return 60;
  if (item.href.includes(needle)) return 50;
  if (item.keywords?.some((keyword) => keyword.startsWith(needle))) return 40;
  if (item.keywords?.some((keyword) => keyword.includes(needle))) return 25;
  if (item.summary.toLowerCase().includes(needle)) return 10;
  return 0;
}
