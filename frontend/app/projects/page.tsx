"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import Page from "@/components/Page";
import { api, formatDate, reportApiFailure } from "@/lib/api";
import type { Project } from "@/lib/types";
export default function Projects() {
  const [items, setItems] = useState<Project[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Project[]>("/api/projects")
      .then(setItems)
      .catch((exc) => reportApiFailure(exc, setError))
      .finally(() => setLoaded(true));
  }, []);
  return <Page title="Spaces" description="Each repository or project you connect becomes a space. Ask across all of them, or pick one in the chat." action={<Link href="/ingest" className="button">Add a source</Link>}>
    {error && <div className="notice error">{error}</div>}
    <div className="card">{items.length ? <table className="table"><thead><tr><th>Space</th><th>Repository</th><th>Knowledge</th><th>Created</th></tr></thead><tbody>{items.map(item => <tr key={item.id}><td><strong>{item.name}</strong><div className="subtle">{item.id}</div></td><td>{item.repository || "Uploaded knowledge"}</td><td>{item.knowledge_items ?? 0}</td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table> : loaded && !error ? <div className="empty">No projects yet. Ingest a repository or run <code>make demo</code>.</div> : !loaded ? <div className="empty">Loading…</div> : null}</div>
  </Page>;
}