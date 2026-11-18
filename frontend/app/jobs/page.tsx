"use client";

import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api, formatDate, reportApiFailure } from "@/lib/api";
import type { IngestJob } from "@/lib/types";

export default function IngestionJobs() {
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<IngestJob[]>("/api/ingest/jobs")
      .then(setJobs)
      .catch((exc) => reportApiFailure(exc, setError))
      .finally(() => setLoaded(true));
  }, []);

  return (
    <Page
      eyebrow="Integrations"
      title="Sync status"
      description="Source ingestion progress, scanned artifacts, graph writes, warnings, and failures."
    >
      {error && <div className="notice error">{error}</div>}
      <section className="card">
        {jobs.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Scanned</th>
                <th>Graph writes</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td>
                    <strong>{job.source}</strong>
                    <div className="subtle">{job.source_ref}</div>
                    {job.error && <div className="notice error">{job.error}</div>}
                  </td>
                  <td>
                    <span className={`badge ${job.status === "succeeded" ? "success" : job.status === "failed" ? "danger" : "info"}`}>
                      {job.status}
                    </span>
                  </td>
                  <td>{job.progress}%</td>
                  <td>
                    {job.source === "slack"
                      ? `${job.knowledge_items_created} messages · ${job.knowledge_chunks_created} chunks`
                      : `${job.files_scanned} files · ${job.issues_scanned} issues · ${job.pull_requests_scanned} PRs`}
                  </td>
                  <td>
                    {job.graph_nodes_created} nodes · {job.graph_edges_created} edges
                  </td>
                  <td>{formatDate(job.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : loaded && !error ? (
          <div className="empty">Ingestion jobs appear after a repo, Slack channel, or upload is ingested.</div>
        ) : !loaded ? (
          <div className="empty">Loading…</div>
        ) : null}
      </section>
    </Page>
  );
}
