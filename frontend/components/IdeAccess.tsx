"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, reportApiFailure } from "@/lib/api";

/* Connecting an editor to company memory.
 *
 * An agent working inside Cursor, VS Code, or Claude Code reaches the same
 * memory as the chat, over MCP. Every snippet below is generated from this deployment's
 * own runtime settings — a self-hosted install shows its own URLs, not
 * localhost copied out of a README.
 *
 * The HTTP MCP server authenticates with OAuth only: it validates each bearer
 * at /api/oauth/introspect, which accepts MCP access tokens and nothing else,
 * and it never falls back to a workspace API key. So no snippet here carries a
 * key — the client discovers the authorization server from the URL and opens a
 * MemoryWorks sign-in. Keys are for the REST API, the SDK/CLI, and the local
 * stdio bridge. */

type Runtime = { mcp_http_url?: string; mcp_available?: boolean; mcp_oauth_issuer?: string; api_url?: string };

type Client = {
  id: string;
  label: string;
  file: string;
  note: string;
  snippet: (url: string) => string;
};

const CLIENTS: Client[] = [
  {
    id: "claude-code",
    label: "Claude Code",
    file: "Run once in your terminal",
    note: "Then run /mcp in Claude Code and choose Authenticate; a browser window opens to sign in and approve. Add --scope user to use it in every project.",
    snippet: (url) => `claude mcp add --transport http memoryworks ${url}`,
  },
  {
    id: "cursor",
    label: "Cursor",
    file: "~/.cursor/mcp.json  (or .cursor/mcp.json in the repo)",
    note: "Save, then choose Connect next to memoryworks in Cursor's MCP settings to sign in and approve.",
    snippet: (url) => JSON.stringify({ mcpServers: { memoryworks: { url } } }, null, 2),
  },
  {
    id: "vscode",
    label: "VS Code",
    file: ".vscode/mcp.json",
    note: "Start the server from the file or the MCP view; VS Code asks you to sign in and stores the token itself.",
    snippet: (url) =>
      JSON.stringify({ servers: { memoryworks: { type: "http", url } } }, null, 2),
  },
  {
    id: "claude",
    label: "Claude",
    file: "Settings → Connectors → Add custom connector",
    note: "Works in claude.ai and Claude Desktop. Paste the URL, choose Connect, and approve.",
    snippet: (url) => url,
  },
  {
    id: "chatgpt",
    label: "ChatGPT",
    file: "Settings → Apps & Connectors → Create",
    note: "If Create is missing, turn on Developer mode under Advanced settings. Choose OAuth authentication; ChatGPT opens MemoryWorks to sign in and approve.",
    snippet: (url) => `Name            MemoryWorks\nMCP server URL  ${url}\nAuthentication  OAuth`,
  },
  {
    id: "other",
    label: "Any MCP client",
    file: "Streamable HTTP endpoint",
    note: "Clients discover the authorization server from the URL (RFC 9728) and register themselves. Use the form below only if yours cannot.",
    snippet: (url) =>
      `Transport   streamable-http\nURL         ${url}\nAuth        OAuth 2.1 (PKCE, dynamic client registration)`,
  },
];

export default function IdeAccess() {
  const [runtime, setRuntime] = useState<Runtime>({});
  const [client, setClient] = useState(CLIENTS[0].id);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Runtime>("/api/settings/runtime")
      .then(setRuntime)
      .catch((exc) => reportApiFailure(exc, setError));
  }, []);

  const url = runtime.mcp_http_url || "http://localhost:8001/mcp";
  const active = useMemo(() => CLIENTS.find((item) => item.id === client)!, [client]);
  const snippet = active.snippet(url);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Clipboard is unavailable. Select the text and copy it manually.");
    }
  }

  return (
    <section className="ide-access">
      <div className="section-head">
        <div>
          <span className="panel-label">Editors and agents</span>
          <h2>Connect your IDE</h2>
        </div>
        <span>Same tools as the browser, over MCP</span>
      </div>

      <p className="subtle ide-intro">
        An agent inside your editor gets the same organizational memory the chat uses: search,
        incidents, decisions, dependencies, blockers, contradictions, and provenance. Reads run
        immediately; anything that would change company memory comes back here as a proposal and
        waits for a person.
      </p>

      {runtime.mcp_available === false && (
        <div className="notice" role="status">
          Hosted MCP isn&apos;t running on this deployment yet, so editors can&apos;t connect to{" "}
          <code>{url}</code>. The REST API and the local stdio bridge still work with a{" "}
          <Link href="/keys">workspace API key</Link>.
        </div>
      )}

      <div className="ide-tabs" role="tablist">
        {CLIENTS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={item.id === client}
            className={item.id === client ? "active" : ""}
            onClick={() => setClient(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="ide-panel">
        <div className="ide-panel-head">
          <code>{active.file}</code>
          <button className="text-button" onClick={() => copy(snippet)}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <pre className="ide-snippet">{snippet}</pre>
        <p className="subtle">{active.note}</p>
      </div>

      <div className="ide-key">
        <div>
          <strong>No key to paste</strong>
          <p className="subtle">
            Your editor signs in to MemoryWorks over OAuth the first time it connects, and you approve
            read or read-and-write access. Workspace API keys are for the REST API, the Python SDK and
            CLI, and the local stdio bridge — the MCP endpoint does not accept them.
          </p>
        </div>
        <div className="ide-key-actions">
          <Link className="text-button" href="/keys">
            API keys
          </Link>
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}

      <dl className="ide-facts">
        <div>
          <dt>Endpoint</dt>
          <dd><code>{url}</code></dd>
        </div>
        <div>
          <dt>Transport</dt>
          <dd>Streamable HTTP</dd>
        </div>
        <div>
          <dt>OAuth issuer</dt>
          <dd><code>{runtime.mcp_oauth_issuer || "—"}</code></dd>
        </div>
        <div>
          <dt>Scope</dt>
          <dd>Your workspace, trimmed to what you can already read</dd>
        </div>
      </dl>
    </section>
  );
}
