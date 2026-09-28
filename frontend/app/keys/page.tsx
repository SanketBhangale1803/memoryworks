"use client";

import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api, formatDate } from "@/lib/api";

export default function Keys() {
  const [keys, setKeys] = useState<any[]>();
  const [name, setName] = useState("");
  const [created, setCreated] = useState<any>();
  const [error, setError] = useState("");

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

  async function revoke(id: string) {
    setError("");
    try {
      await api(`/api/keys/${id}`, {method:"DELETE"});
      load();
    } catch (requestError: any) {
      setError(requestError.message);
    }
  }

  return <Page eyebrow="Integrations" title="API keys" description="Keys for MCP clients and automation. The secret is shown once at creation; only a hash is stored.">
    <section className="card card-pad stack">
      <div className="row">
        <input value={name} onChange={event=>setName(event.target.value)} placeholder="Key name, e.g. Claude Desktop MCP"/>
        <button className="button" disabled={!name.trim()} onClick={create}>Create key</button>
      </div>
      {created && <div className="notice"><strong>Copy this key now — it will not be shown again.</strong><br/><code>{created.api_key}</code></div>}
    </section>
    {error && <div className="notice error" style={{marginTop:16}}>{error}</div>}
    <section className="card" style={{marginTop:16}}>
      <div className="section-head"><h2>Keys</h2><span className="badge">{keys?.length ?? 0}</span></div>
      {!keys && <div className="empty">Loading…</div>}
      {keys && !keys.length && <div className="empty">No API keys yet. Create one to connect an MCP client to this Runbook instance.</div>}
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
