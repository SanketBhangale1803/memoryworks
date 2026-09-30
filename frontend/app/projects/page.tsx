"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import Page from "@/components/Page";
import { api, formatDate } from "@/lib/api";
export default function Projects() {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api<any[]>("/api/projects").then(setItems); }, []);
  return <Page title="Spaces" description="Each repository or project you connect becomes a space. Ask across all of them, or pick one in the chat." action={<Link href="/ingest" className="button">Add a source</Link>}><div className="card">{items.length ? <table className="table"><thead><tr><th>Space</th><th>Repository</th><th>Knowledge</th><th>Created</th></tr></thead><tbody>{items.map(item => <tr key={item.id}><td><strong>{item.name}</strong><div className="subtle">{item.id}</div></td><td>{item.repository || "Uploaded knowledge"}</td><td>{item.knowledge_items}</td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table> : <div className="empty">No projects yet. Ingest a repository or run <code>make demo</code>.</div>}</div></Page>;
}
