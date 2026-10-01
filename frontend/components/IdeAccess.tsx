"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

/* Connecting an editor to company memory.
 *
 * An agent working inside Cursor, VS Code, or Claude Code reaches the same
 * memory as the chat, over MCP. Every snippet below is generated from this deployment's
 * own runtime settings — a self-hosted install shows its own URLs, not
 * localhost copied out of a README. */

type Runtime = { mcp_http_url?: string; mcp_available?: boolean; mcp_oauth_issuer?: string; api_url?: string };
type ApiKey = { id: string; name: string; key_prefix: string; created_at: string };

const KEY_PLACEHOLDER = "YOUR_MEMORYWORKS_API_KEY";

type Client = {
  id: string;
  label: string;
  file: string;
  note: string;
  snippet: (url: string, key: string) => string;
};

const CLIENTS: Client[] = [
  {
    id: "cursor",
    label: "Cursor",
    file: "~/.cursor/mcp.json  (or .cursor/mcp.json in the repo)",
    note: "Reload the MCP servers list in Cursor settings after saving.",
    snippet: (url, key) =>
      JSON.stringify(
        {
          mcpServers: {
            memoryworks: {
              url,
              headers: { Authorization: `Bearer ${key}` },
            },
          },
        },
        null,
        2,
      ),
  },
  {
    id: "vscode",
    label: "VS Code",
    file: ".vscode/mcp.json",
    note: "VS Code prompts for the key on first use and stores it in its own secret store.",
    snippet: (url) =>
      JSON.stringify(
        {
          inputs: [
            {
              id: "memoryworks-key",
              type: "promptString",
              description: "MemoryWorks API key",
              password: true,
            },
          ],
          servers: {
            memoryworks: {
              type: "http",
              url,
              headers: { Authorization: "Bearer ${input:memoryworks-key}" },
            },
          },
        },
        null,
        2,
      ),
  },
  {
    id: "claude-code",
    label: "Claude Code",
    file: "Run once in your terminal",
    note: "Adds the server for this project. Use --scope user to add it everywhere.",
    snippet: (url, key) =>
      `claude mcp add --transport http memoryworks ${url} \\\n  --header "Authorization: Bearer ${key}"`,
  },
  {
    id: "claude-desktop",
    label: "Claude Desktop",
    file: "claude_desktop_config.json",
    note: "Restart Claude Desktop after saving.",
    snippet: (url, key) =>
      JSON.stringify(
        {
          mcpServers: {
            memoryworks: {
              command: "npx",
              args: ["-y", "mcp-remote", url, "--header", `Authorization: Bearer ${key}`],
            },
          },
        },
        null,
        2,
      ),
  },
  {
    id: "other",
    label: "Any MCP client",
    file: "Streamable HTTP endpoint",
    note: "OAuth is also supported; the issuer is shown below for clients that discover it.",
    snippet: (url, key) =>
      `Transport   streamable-http\nURL         ${url}\nAuth        Authorization: Bearer ${key}`,
  },
];

export default function IdeAccess() {
  const [runtime, setRuntime] = useState<Runtime>({});
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [client, setClient] = useState(CLIENTS[0].id);
  const [issued, setIssued] = useState("");
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Runtime>("/api/settings/runtime").then(setRuntime).catch(() => undefined);
    api<ApiKey[]>("/api/keys").then(setKeys).catch(() => undefined);
  }, []);

  const url = runtime.mcp_http_url || "http://localhost:8001/mcp";
  const active = useMemo(() => CLIENTS.find((item) => item.id === client)!, [client]);
  const snippet = active.snippet(url, issued || KEY_PLACEHOLDER);

  async function issueKey() {
    setCreating(true);
    setError("");
    try {
      const created = await api<{ api_key: string }>("/api/keys", {
        method: "POST",
        body: JSON.stringify({ name: `${active.label} — editor access` }),
      });
      setIssued(created.api_key);
      setKeys(await api<ApiKey[]>("/api/keys").catch(() => keys));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not issue a key.");
    } finally {
      setCreating(false);
    }
  }

  async function copy(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(""), 1600);
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
          <code>{url}</code>. Keys issued here already work with the API.
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
          <button className="text-button" onClick={() => copy(snippet, "snippet")}>
            {copied === "snippet" ? "Copied" : "Copy"}
          </button>
        </div>
        <pre className="ide-snippet">{snippet}</pre>
        <p className="subtle">{active.note}</p>
      </div>

      <div className="ide-key">
        <div>
          <strong>{issued ? "Your new key — copy it now" : "Workspace API key"}</strong>
          <p className="subtle">
            {issued
              ? "This is the only time the full key is shown. It is already filled into the snippet above."
              : keys.length
                ? `${keys.length} key${keys.length === 1 ? "" : "s"} issued. Existing keys cannot be read back, so issue a new one for a new editor.`
                : "No keys issued yet. An editor needs one to authenticate."}
          </p>
          {issued && <code className="ide-secret">{issued}</code>}
        </div>
        <div className="ide-key-actions">
          {issued ? (
            <button className="button secondary" onClick={() => copy(issued, "key")}>
              {copied === "key" ? "Copied" : "Copy key"}
            </button>
          ) : (
            <button className="button" onClick={issueKey} disabled={creating}>
              {creating ? "Issuing…" : "Issue a key"}
            </button>
          )}
          <Link className="text-button" href="/keys">
            Manage keys
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
