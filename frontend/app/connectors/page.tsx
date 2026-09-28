"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import Page from "@/components/Page";
import { API, api, formatDate } from "@/lib/api";

/* Sources: every system OrgMemory can learn from, in one grid.
 *
 * The directory and the connectable list used to be two separate sections, so
 * the same provider appeared twice and "can I connect this?" meant reading
 * both. Each provider is now one card whose button says exactly what happens
 * next: connect it (OAuth), add something from it (import), set it up (agent
 * surfaces), or wait for it (not built yet). OAuth still returns here with
 * ?connected=<provider>, and the next step from there is choosing what to read. */

const sampleTools = JSON.stringify(
  [
    {
      name: "search",
      description: "Search this MCP server",
      kind: "read",
      risk_level: "low",
      input_schema: { type: "object", properties: { query: { type: "string" } } },
    },
  ],
  null,
  2,
);

/* Providers imported directly rather than authorized: each opens the import
   page with that source already chosen. */
const IMPORT_SOURCE: Record<string, string> = {
  uploads: "files",
  web: "website",
  custom_rest_source: "paste",
};
/* Providers that are ways for agents to reach OrgMemory, not sources of memory. */
const AGENT_SURFACES = new Set(["mcp", "api_sdk_cli", "local_desktop_extension"]);
/* OAuth sources whose next step after connecting is choosing what to import. */
const IMPORT_AFTER_CONNECT: Record<string, string> = { github: "github", slack: "slack" };

type Card = {
  provider: string;
  label: string;
  category: string;
  reads: string[];
  status: "connected" | "connect" | "setup_needed" | "import" | "agent" | "soon";
  account?: { display_name: string; updated_at: string };
  connector?: any;
};

function monogram(name: string) {
  return name
    .split(/[\s&]+/)
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function toCards(connectors: any[], catalog: any[]): Card[] {
  const byProvider = new Map(connectors.map((item) => [item.provider, item]));
  const cards: Card[] = catalog.map((entry) => {
    const connector = byProvider.get(entry.provider);
    const base = {
      provider: entry.provider,
      label: entry.label,
      category: entry.category || "Other",
      reads: entry.memory || [],
    };
    if (connector) {
      const account = connector.accounts?.find((item: any) => item.status === "connected");
      const status = connector.connected ? "connected" : connector.available ? "connect" : "setup_needed";
      return { ...base, status, account, connector };
    }
    if (entry.provider in IMPORT_SOURCE) return { ...base, status: "import" };
    if (AGENT_SURFACES.has(entry.provider)) return { ...base, status: "agent" };
    return { ...base, status: entry.status === "live" ? "import" : "soon" };
  });
  // A connector the catalog does not list (a custom MCP package) still gets a card.
  for (const connector of connectors) {
    if (cards.some((card) => card.provider === connector.provider)) continue;
    const account = connector.accounts?.find((item: any) => item.status === "connected");
    cards.push({
      provider: connector.provider,
      label: connector.manifest?.name || connector.provider,
      category: "Custom MCP servers",
      reads: (connector.manifest?.resources || []).map((item: any) => item.label),
      status: connector.connected ? "connected" : "connect",
      account,
      connector,
    });
  }
  const order: Record<Card["status"], number> = {
    connected: 0,
    connect: 1,
    setup_needed: 2,
    import: 3,
    agent: 4,
    soon: 5,
  };
  return cards.sort((a, b) => order[a.status] - order[b.status]);
}

export default function Sources() {
  const [connectors, setConnectors] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [coverage, setCoverage] = useState<any>();
  const [loaded, setLoaded] = useState(false);
  const [justConnected, setJustConnected] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [registering, setRegistering] = useState(false);
  const [custom, setCustom] = useState({
    name: "",
    serverUrl: "",
    version: "1.0.0",
    clientId: "",
    authorizationUrl: "",
    tokenUrl: "",
    scopes: "read",
    tools: sampleTools,
  });

  const load = () =>
    Promise.all([
      api<any[]>("/api/connectors"),
      api<any[]>("/api/connectors/catalog"),
      api("/api/connectors/coverage").catch(() => undefined),
    ]).then(([nextConnectors, nextCatalog, nextCoverage]) => {
      setConnectors(nextConnectors);
      setCatalog(nextCatalog);
      setCoverage(nextCoverage);
      setLoaded(true);
    });

  useEffect(() => {
    load().catch((exc) => setError(exc.message));
    const params = new URLSearchParams(window.location.search);
    if (params.get("connected")) setJustConnected(params.get("connected")!);
    if (params.get("error")) setError(params.get("error")!);
  }, []);

  const cards = useMemo(() => toCards(connectors, catalog), [connectors, catalog]);
  const filters = useMemo(
    () => ["All", "Connected", ...Array.from(new Set(cards.map((card) => card.category)))],
    [cards],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return cards.filter((card) => {
      if (filter === "Connected" && card.status !== "connected") return false;
      if (filter !== "All" && filter !== "Connected" && card.category !== filter) return false;
      if (!needle) return true;
      return [card.label, card.category, ...card.reads].some((value) =>
        value.toLowerCase().includes(needle),
      );
    });
  }, [cards, filter, query]);

  const connectedLabel = cards.find((card) => card.provider === justConnected)?.label || justConnected;

  async function disconnect(card: Card) {
    if (!window.confirm(`Disconnect ${card.label} for your delegated account?`)) return;
    try {
      await api(`/api/connectors/${encodeURIComponent(card.provider)}`, { method: "DELETE" });
      setMessage(`${card.label} disconnected.`);
      setJustConnected("");
      await load();
    } catch (exc: any) {
      setError(exc.message);
    }
  }

  async function revokeRegistration(provider: string) {
    if (!window.confirm("Revoke this custom MCP registration for the whole workspace?")) return;
    try {
      await api(`/api/connectors/custom/registrations/${encodeURIComponent(provider)}`, {
        method: "DELETE",
      });
      setMessage("Custom MCP registration revoked.");
      await load();
    } catch (exc: any) {
      setError(exc.message);
    }
  }

  async function registerCustom(event: FormEvent) {
    event.preventDefault();
    setRegistering(true);
    setError("");
    try {
      const tools = JSON.parse(custom.tools);
      await api("/api/connectors/custom/registrations", {
        method: "POST",
        body: JSON.stringify({
          name: custom.name,
          server_url: custom.serverUrl,
          version: custom.version,
          oauth: {
            client_id: custom.clientId,
            authorization_url: custom.authorizationUrl,
            token_url: custom.tokenUrl,
            scopes: custom.scopes.split(/[\s,]+/).filter(Boolean),
            pkce_required: true,
          },
          manifest: { icon: "plug", resources: [], tools },
        }),
      });
      setMessage("Custom MCP server pinned. Connect it to authorize your own account.");
      setCustom((value) => ({ ...value, name: "", serverUrl: "", clientId: "" }));
      await load();
    } catch (exc: any) {
      setError(exc.message);
    } finally {
      setRegistering(false);
    }
  }

  return (
    <Page
      eyebrow="Integrations"
      title="Sources"
      description="Connect the systems your team already works in. OrgMemory reads them with your own permissions and keeps every memory tied to where it came from."
    >
      {justConnected && (
        <div className="notice next-step">
          <div>
            <strong>{connectedLabel} is connected.</strong>
            <span>Next, choose what OrgMemory should read from it.</span>
          </div>
          <Link
            className="button"
            href={`/ingest${IMPORT_AFTER_CONNECT[justConnected] ? `?source=${IMPORT_AFTER_CONNECT[justConnected]}` : ""}`}
          >
            Choose what to import →
          </Link>
        </div>
      )}
      {message && <div className="notice">{message}</div>}
      {error && <div className="notice error">{error}</div>}

      <div className="source-toolbar">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search sources — GitHub, Slack, Jira, PDFs…"
          aria-label="Search sources"
        />
        <div className="source-filters" role="tablist" aria-label="Filter sources">
          {filters.map((name) => (
            <button
              key={name}
              role="tab"
              aria-selected={filter === name}
              className={filter === name ? "active" : ""}
              onClick={() => setFilter(name)}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      {loaded && !visible.length && (
        <p className="subtle source-empty">
          Nothing matches that. <Link href="/ingest?source=paste">Paste it in directly →</Link>
        </p>
      )}

      <div className="source-grid">
        {visible.map((card) => (
          <SourceCard
            key={card.provider}
            card={card}
            onDisconnect={() => disconnect(card)}
            onRevoke={() => revokeRegistration(card.provider)}
          />
        ))}
      </div>

      <section className="security-callout">
        <div>
          <span className="panel-label">Credential boundary</span>
          <h2>One person, one delegated grant</h2>
        </div>
        <p>
          OAuth tokens never reach the browser. Production uses KMS envelope encryption bound to
          workspace, user, and provider. Every write carries an idempotency key, waits for
          approval, and produces an audit event.
        </p>
      </section>

      {coverage?.sources?.length > 0 && (
        <details className="source-more">
          <summary>What OrgMemory can search from each source</summary>
          <div className="coverage-grid">
            {coverage.sources.map((source: any) => (
              <article key={source.provider}>
                <div className="row between">
                  <strong>{source.provider}</strong>
                  <span>{source.connected ? "Connected" : "Not connected"}</span>
                </div>
                <dl>
                  {Object.entries(source.indexed).map(([name, value]: any) => (
                    <div key={name}>
                      <dt>{name.replace(/_/g, " ")}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
                <small>Refresh: {source.refresh_mode}</small>
              </article>
            ))}
          </div>
        </details>
      )}

      <details className="source-more">
        <summary>Register a remote MCP server (admins)</summary>
        <p className="subtle">
          OrgMemory pins its URL, version, OAuth settings, and tool manifest. Private-network and
          non-HTTPS targets are rejected by the cloud gateway.
        </p>
        <form className="stack" onSubmit={registerCustom}>
          <div className="grid two">
            <label className="field">
              <span>Name</span>
              <input required value={custom.name} onChange={(event) => setCustom({ ...custom, name: event.target.value })} />
            </label>
            <label className="field">
              <span>Streamable HTTP URL</span>
              <input required type="url" placeholder="https://connector.example/mcp" value={custom.serverUrl} onChange={(event) => setCustom({ ...custom, serverUrl: event.target.value })} />
            </label>
          </div>
          <div className="grid two">
            <label className="field">
              <span>OAuth authorization URL</span>
              <input required type="url" value={custom.authorizationUrl} onChange={(event) => setCustom({ ...custom, authorizationUrl: event.target.value })} />
            </label>
            <label className="field">
              <span>OAuth token URL</span>
              <input required type="url" value={custom.tokenUrl} onChange={(event) => setCustom({ ...custom, tokenUrl: event.target.value })} />
            </label>
          </div>
          <div className="grid two">
            <label className="field">
              <span>OAuth client ID</span>
              <input required value={custom.clientId} onChange={(event) => setCustom({ ...custom, clientId: event.target.value })} />
            </label>
            <label className="field">
              <span>Least-privilege scopes</span>
              <input required value={custom.scopes} onChange={(event) => setCustom({ ...custom, scopes: event.target.value })} />
            </label>
          </div>
          <label className="field">
            <span>Pinned tools (JSON)</span>
            <textarea rows={10} value={custom.tools} onChange={(event) => setCustom({ ...custom, tools: event.target.value })} />
          </label>
          <button className="button" disabled={registering}>
            {registering ? "Validating…" : "Register and pin MCP server"}
          </button>
        </form>
      </details>
    </Page>
  );
}

function SourceCard({
  card,
  onDisconnect,
  onRevoke,
}: {
  card: Card;
  onDisconnect: () => void;
  onRevoke: () => void;
}) {
  const provider = encodeURIComponent(card.provider);
  const tools = card.connector?.manifest?.tools || [];
  const reads = tools.filter((tool: any) => tool.kind === "read").length;
  const writes = tools.filter((tool: any) => tool.kind === "write").length;
  const state: Record<Card["status"], string> = {
    connected: "Connected",
    connect: "Ready to connect",
    setup_needed: "Needs admin setup",
    import: "Import",
    agent: "For agents",
    soon: "Coming soon",
  };

  return (
    <article className={`source-card ${card.status}`}>
      <header>
        <span className="source-logo" aria-hidden="true">
          {monogram(card.label)}
        </span>
        <div>
          <h2>{card.label}</h2>
          <small>{card.category}</small>
        </div>
        <span className={`source-state ${card.status}`}>
          <i />
          {state[card.status]}
        </span>
      </header>

      {card.reads.length > 0 && <p className="source-reads">{card.reads.join(" · ")}</p>}

      {card.account && (
        <p className="source-account">
          As <strong>{card.account.display_name}</strong> · updated {formatDate(card.account.updated_at)}
        </p>
      )}
      {card.connector && (
        <p className="source-tools">
          {reads} read tool{reads === 1 ? "" : "s"} · {writes} write tool{writes === 1 ? "" : "s"}
          {writes ? ", each waiting for approval" : ""}
        </p>
      )}

      <footer>
        {card.status === "connected" && (
          <>
            <Link
              className="button"
              href={`/ingest${IMPORT_AFTER_CONNECT[card.provider] ? `?source=${IMPORT_AFTER_CONNECT[card.provider]}` : ""}`}
            >
              Choose what to import
            </Link>
            <a className="text-button" href={`${API}/api/connectors/${provider}/auth/start`}>
              Reconnect
            </a>
            <button className="text-button danger-text" onClick={onDisconnect}>
              Disconnect
            </button>
          </>
        )}
        {card.status === "connect" && (
          <a className="button" href={`${API}/api/connectors/${provider}/auth/start`}>
            Connect {card.label}
          </a>
        )}
        {card.status === "setup_needed" && (
          <span className="subtle">An admin has to add this provider’s OAuth app first.</span>
        )}
        {card.status === "import" && (
          <Link className="button secondary" href={`/ingest?source=${IMPORT_SOURCE[card.provider] || "paste"}`}>
            Add from {card.label}
          </Link>
        )}
        {card.status === "agent" && (
          <Link className="button secondary" href="/integrations">
            Set up
          </Link>
        )}
        {card.status === "soon" && <span className="subtle">Not available yet</span>}
        {card.provider.startsWith("custom.") && (
          <button className="text-button danger-text" onClick={onRevoke}>
            Revoke package
          </button>
        )}
      </footer>
    </article>
  );
}
