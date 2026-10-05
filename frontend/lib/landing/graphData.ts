/* The memory graph's nodes and edges. No three.js here, so the landing page
   can render the labels and panel before the 3D scene has loaded. */

export type GraphGroup = "core" | "source" | "memory" | "agent";
export type GraphNode = { id: string; label: string; group: GraphGroup; detail: string };
export type GraphEdge = [string, string];

export const GRAPH_NODES: GraphNode[] = [
  { id: "core", label: "MemoryWorks", group: "core", detail: "One time-aware memory, every fact tied to its source." },
  { id: "github", label: "GitHub", group: "source", detail: "Pull requests, commits, reviews, and the code they changed." },
  { id: "slack", label: "Slack", group: "source", detail: "The threads where decisions were actually argued out." },
  { id: "drive", label: "Google Drive", group: "source", detail: "Design docs, runbooks, and postmortems." },
  { id: "notion", label: "Notion", group: "source", detail: "Specs, ADRs, and team wikis." },
  { id: "teams", label: "Microsoft Teams", group: "source", detail: "Channels and chats, with their permissions intact." },
  { id: "docs", label: "PDFs & docs", group: "source", detail: "Anything uploaded, parsed and cited back to the page." },
  { id: "incidents", label: "Incidents", group: "memory", detail: "What broke, why, and what fixed it." },
  { id: "decisions", label: "Decisions", group: "memory", detail: "What was chosen, by whom, and what it ruled out." },
  { id: "owners", label: "Owners", group: "memory", detail: "Who owns a service now — and who did before." },
  { id: "dependencies", label: "Dependencies", group: "memory", detail: "What else a change touches." },
  { id: "constraints", label: "Constraints", group: "memory", detail: "The limits a change must not cross." },
  { id: "outcomes", label: "Outcomes", group: "memory", detail: "What happened after an agent acted on a briefing." },
  { id: "claude", label: "Claude Code", group: "agent", detail: "Briefed over MCP before it edits or deploys." },
  { id: "cursor", label: "Cursor", group: "agent", detail: "The same memory, inside the editor." },
  { id: "vscode", label: "VS Code", group: "agent", detail: "Ask in the editor, cite the source." },
  { id: "chatgpt", label: "ChatGPT", group: "agent", detail: "Company context for general assistants." },
  { id: "sdk", label: "Python SDK", group: "agent", detail: "Compile context for your own agents." },
];

export const GRAPH_EDGES: GraphEdge[] = [
  ["github", "incidents"], ["github", "decisions"], ["github", "dependencies"], ["github", "owners"],
  ["slack", "decisions"], ["slack", "incidents"], ["slack", "owners"],
  ["drive", "constraints"], ["drive", "incidents"], ["drive", "decisions"],
  ["notion", "decisions"], ["notion", "constraints"], ["notion", "dependencies"],
  ["teams", "decisions"], ["teams", "owners"],
  ["docs", "constraints"], ["docs", "incidents"],
  ["incidents", "core"], ["decisions", "core"], ["owners", "core"], ["dependencies", "core"], ["constraints", "core"], ["outcomes", "core"],
  ["core", "claude"], ["core", "cursor"], ["core", "vscode"], ["core", "chatgpt"], ["core", "sdk"],
  ["claude", "outcomes"], ["cursor", "outcomes"], ["sdk", "outcomes"],
];

export const GROUP_NAME: Record<GraphGroup, string> = { core: "The memory", source: "Source", memory: "Kind of memory", agent: "Agent surface" };
