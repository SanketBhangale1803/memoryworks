"use client";
import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api, formatDate, reportApiFailure } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
export default function Audit(){
  const [items,setItems]=useState<AuditEvent[]>([]);
  const [loaded,setLoaded]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{api<AuditEvent[]>("/api/audit").then(setItems).catch(exc=>reportApiFailure(exc,setError)).finally(()=>setLoaded(true));},[]);
  return <Page title="Audit log" description="Immutable application history for ingestion, retrieval, extraction, and approvals.">
    {error&&<div className="notice error">{error}</div>}
    <div className="card">{items.length?<table className="table"><thead><tr><th>Event</th><th>Actor</th><th>Project</th><th>Time</th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><strong>{item.summary}</strong><div className="subtle">{item.event_type}</div></td><td>{item.actor}</td><td><code>{item.project_id||"workspace"}</code></td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table>:loaded&&!error?<div className="empty">Audit events appear here as the product is used.</div>:!loaded?<div className="empty">Loading…</div>:null}</div>
  </Page>;
}