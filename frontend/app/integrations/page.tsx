"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import IdeAccess from "@/components/IdeAccess";
import Page from "@/components/Page";
import { API, api } from "@/lib/api";

/* AI tools & IDEs: every way an agent reaches this workspace.
 *
 * Ordered by how often each is needed. Most people connect an editor or coding
 * agent (a key and one pasted snippet), fewer register a hosted assistant over
 * OAuth, and almost nobody needs the local stdio bridge — so that one is folded
 * away rather than given equal weight. */

const readTools = [
  "orgmemory_ask",
  "orgmemory_search_memories",
  "orgmemory_get_company_profile",
  "orgmemory_get_memory_graph",
  "orgmemory_list_source_revisions",
];
const writeTools = ["orgmemory_request_connector_action"];

export default function AiTools() {
  const [mcpUrl, setMcpUrl] = useState(process.env.NEXT_PUBLIC_MCP_URL || "http://localhost:8001/mcp");
  const [name, setName] = useState("Claude / ChatGPT");
  const [redirectUri, setRedirectUri] = useState("");
  const [write, setWrite] = useState(false);
  const [registration, setRegistration] = useState<any>();
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api<{ mcp_http_url?: string }>("/api/settings/runtime")
      .then((runtime) => runtime.mcp_http_url && setMcpUrl(runtime.mcp_http_url))
      .catch(() => undefined);
  }, []);

  async function register(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      setRegistration(
        await api("/api/mcp/oauth/clients", {
          method: "POST",
          body: JSON.stringify({
            name,
            redirect_uris: [redirectUri],
            scopes: write ? ["read", "write"] : ["read"],
          }),
        }),
      );
    } catch (exc: any) {
      setError(exc.message);
    }
  }

  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(mcpUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Clipboard is unavailable. Select the URL and copy it manually.");
    }
  }

  const localConfig = JSON.stringify(
    {
      mcpServers: {
        memoryworks: {
          command: "make",
          args: ["-C", "/absolute/path/to/memoryworks", "mcp"],
          env: { MEMORYWORKS_API_URL: API, MEMORYWORKS_API_KEY: "om_replace_with_workspace_key" },
        },
      },
    },
    null,
    2,
  );

  return (
    <Page
      eyebrow="Integrations"
      title="AI tools & IDEs"
      description="Give Claude Code, Cursor, VS Code, Claude, ChatGPT, or any MCP client the same company memory the chat uses. Reads run immediately; anything that would change memory waits for a person."
    >
      {error && <div className="notice error">{error}</div>}

      <IdeAccess />

      <section className="card card-pad stack">
        <div className="section-head">
          <div>
            <span className="panel-label">Hosted assistants</span>
            <h2>Claude.ai, ChatGPT, and other remote MCP clients</h2>
          </div>
          <span className="badge success">OAuth</span>
        </div>
        <p className="subtle">
          Add this URL as a custom connector. The client signs in through MemoryWorks, so no key is
          pasted anywhere.
        </p>
        <div className="copy-field">
          <code>{mcpUrl}</code>
          <button className="button secondary" onClick={copyUrl}>
            {copied ? "Copied" : "Copy URL"}
          </button>
        </div>
        <dl className="clean-details">
          <div>
            <dt>Authorization server</dt>
            <dd>{API}</dd>
          </div>
          <div>
            <dt>OAuth metadata</dt>
            <dd>{API}/.well-known/oauth-authorization-server</dd>
          </div>
          <div>
            <dt>Protected resource</dt>
            <dd>{API}/.well-known/oauth-protected-resource</dd>
          </div>
        </dl>
        <div className="notice">
          <strong>Write scope creates requests, not side effects.</strong>
          <br />
          The remote agent can request an action, but a person must approve it in{" "}
          <Link href="/approvals">Approvals</Link> before the connector executes it.
        </div>
      </section>

      <details className="source-more">
        <summary>Register an OAuth client by hand</summary>
        <p className="subtle">
          Only for clients that cannot register themselves. Most connectors above do this for you.
        </p>
        <form className="stack" onSubmit={register}>
          <label className="field">
            <span>Client name</span>
            <input required value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="field">
            <span>Exact redirect URI</span>
            <input
              required
              type="url"
              placeholder="https://client.example/oauth/callback"
              value={redirectUri}
              onChange={(event) => setRedirectUri(event.target.value)}
            />
          </label>
          <label className="row">
            <input type="checkbox" checked={write} onChange={(event) => setWrite(event.target.checked)} />{" "}
            Allow approval-request tools (write scope)
          </label>
          <button className="button">Register OAuth client</button>
        </form>
        {registration && <pre className="trace">{JSON.stringify(registration, null, 2)}</pre>}
      </details>

      <details className="source-more">
        <summary>Which tools an agent gets</summary>
        <div className="grid two">
          <section className="card">
            <div className="section-head">
              <h2>Read tools</h2>
              <span className="badge success">read</span>
            </div>
            <div className="card-pad stack">
              {readTools.map((tool) => (
                <div className="row between" key={tool}>
                  <code>{tool}</code>
                  <span className="badge">untrusted sources isolated</span>
                </div>
              ))}
            </div>
          </section>
          <section className="card">
            <div className="section-head">
              <h2>Write-request tools</h2>
              <span className="badge warning">write</span>
            </div>
            <div className="card-pad stack">
              {writeTools.map((tool) => (
                <div className="row between" key={tool}>
                  <code>{tool}</code>
                  <span className="badge warning">approval + idempotency</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </details>

      <details className="source-more">
        <summary>Local stdio bridge</summary>
        <p className="subtle">
          For local agents, use a revocable workspace API key. Remote HTTP never falls back to this key.
        </p>
        <pre className="trace">{localConfig}</pre>
      </details>
    </Page>
  );
}
