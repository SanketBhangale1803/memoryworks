"use client";

import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api, formatDate } from "@/lib/api";

export default function Keys() {
  const [keys, setKeys] = useState<any[]>();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<any>();
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  async function load() {
    try {
      setKeys(await api("/api/keys"));
    } catch (requestError: any) {
      setError(requestError.message);
    }
  }
  useEffect(() => { load(); }, []);

  async function create() {
    setError("");
    try {
      const result: any = await api("/api/keys", {method:"POST", body:JSON.stringify({name})});
      setCreated(result);
      setName("");
      load();
    } catch (requestError: any) {
      setError(requestError.message);
    }
  }

  async function copyKey(secret: string) {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Clipboard is unavailable. Click the key to select it, then press ⌘C.");
    }
  }

  async function revoke(id: string) {
    setError("");
    try {
      await api(`/api/keys/${id}`, {method:"DELETE"});
      load();
    } catch (requestError: any) {
      setError(requestError.message);
    }
  }

  return <Page title="API keys" description="Keys for MCP clients and automation. The secret is shown once at creation; only a hash is stored.">
    <section className="card card-pad stack">
      <div className="row">
        <input value={name} onChange={event=>setName(event.target.value)} placeholder="Key name, e.g. Claude Desktop MCP"/>
        <button className="button" disabled={!name.trim()} onClick={create}>Create key</button>
      </div>
      {created && <div className="notice key-reveal">
        <strong>Copy this key now — it will not be shown again.</strong>
        <div className="copy-field">
          <code>{created.api_key}</code>
          <button className="button secondary" onClick={() => copyKey(created.api_key)}>{copied ? "Copied" : "Copy key"}</button>
        </div>
      </div>}
    </section>
    {error && <div className="notice error" style={{marginTop:16}}>{error}</div>}
    <section className="card" style={{marginTop:16}}>
      <div className="section-head"><h2>Keys</h2><span className="badge">{keys?.length ?? 0}</span></div>
      {!keys && <div className="empty">Loading…</div>}
      {keys && !keys.length && <div className="empty">No API keys yet. Create one to use MemoryWorks from an editor, the SDK, or the CLI.</div>}
      {keys && keys.length > 0 && <table className="table">
        <thead><tr><th>Name</th><th>Prefix</th><th>Created</th><th>Last used</th><th>Status</th><th></th></tr></thead>
        <tbody>{keys.map(key => <tr key={key.id}>
          <td>{key.name}</td>
          <td><code>{key.key_prefix}…</code></td>
          <td>{formatDate(key.created_at)}</td>
          <td>{formatDate(key.last_used_at)}</td>
          <td><span className={`badge ${key.status === "active" ? "success" : "danger"}`}>{key.status}</span></td>
          <td>{key.status === "active" && <button className="button danger" onClick={()=>revoke(key.id)}>Revoke</button>}</td>
        </tr>)}</tbody>
      </table>}
    </section>
  </Page>;
}
