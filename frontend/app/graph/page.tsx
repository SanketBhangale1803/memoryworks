"use client";

import { useEffect, useMemo, useState } from "react";
import Page from "@/components/Page";
import { api } from "@/lib/api";

type Project = { id: string; name: string };
type GraphSummary = {
  project_id: string;
  node_counts: Record<string, number>;
  edge_counts: Record<string, number>;
  total_nodes: number;
  total_edges: number;
  services: any[];
  files: any[];
};
type GraphView = { summary: GraphSummary; nodes: any[]; edges: any[] };

export default function RepoGraph() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [summary, setSummary] = useState<GraphSummary | null>(null);
  const [nodes, setNodes] = useState<any[]>([]);
  const [edges, setEdges] = useState<any[]>([]);
  const [nodeType, setNodeType] = useState("");
  const [selectedService, setSelectedService] = useState("");
  const [serviceGraph, setServiceGraph] = useState<any | null>(null);
  const [radius, setRadius] = useState<any | null>(null);
  const [selectedNode, setSelectedNode] = useState<any | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<Project[]>("/api/projects").then((items) => {
      setProjects(items);
      if (items[0]) setProjectId(items[0].id);
    });
  }, []);

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    api<GraphView>(`/api/projects/${projectId}/graph/view?limit=300`)
      .then((view) => {
        setSummary(view.summary);
        setNodes(view.nodes);
        setEdges(view.edges);
        setSelectedService(view.summary.services?.[0]?.name || "");
      })
      .catch((exc) => setError(exc.message))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !selectedService) {
      setServiceGraph(null);
      return;
    }
    api(`/api/projects/${projectId}/graph/service/${encodeURIComponent(selectedService)}`)
      .then(setServiceGraph)
      .catch(() => setServiceGraph(null));
    api(`/api/projects/${projectId}/graph/blast-radius/${encodeURIComponent(selectedService)}`)
      .then(setRadius)
      .catch(() => setRadius(null));
  }, [projectId, selectedService]);

  const filteredNodes = useMemo(
    () => nodes.filter((node) => !nodeType || node.node_type === nodeType),
    [nodes, nodeType],
  );
  const nodeTypes = Object.keys(summary?.node_counts || {}).sort();

  return (
    <Page
      eyebrow="Company memory"
      title="Memory Graph"
      description="The live structure behind MemoryWorks answers. Trace current memories, entities, and relationships back to source evidence."
      action={<select className="project-select" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>}
    >
      {loading && <div className="notice">Loading the current ArcadeDB graph…</div>}
      {error && <div className="notice error">{error}</div>}

      {summary ? (
        <>
          <div className="grid metrics graph-metrics">
            <Metric label="Graph nodes" value={summary.total_nodes} />
            <Metric label="Graph edges" value={summary.total_edges} />
            <Metric label="Services" value={summary.node_counts.Service || 0} />
            <Metric label="Files" value={summary.node_counts.File || 0} />
          </div>

          <section className="card live-graph-card">
            <div className="section-head">
              <div>
                <span className="panel-label">Live topology</span>
                <h2>ArcadeDB evidence graph</h2>
              </div>
              <span className="badge success">live query · {edges.length} edges loaded</span>
            </div>
            <EvidenceGraph
              nodes={nodes}
              edges={edges}
              selected={selectedNode}
              onSelect={setSelectedNode}
            />
          </section>

          <div className="grid two">
            <section className="card">
              <div className="section-head">
                <h2>What the graph contains</h2>
                <span className="badge info">ArcadeDB</span>
              </div>
              <div className="card-pad graph-bars">
                {Object.entries(summary.node_counts).map(([type, count]) => (
                  <div className="bar-row" key={type}>
                    <span>{type}</span>
                    <div className="bar-track">
                      <div
                        className="bar-fill"
                        style={{
                          width: `${Math.max(6, (count / Math.max(summary.total_nodes, 1)) * 100)}%`,
                        }}
                      />
                    </div>
                    <strong>{count}</strong>
                  </div>
                ))}
              </div>
            </section>

            <section className="card">
              <div className="section-head">
                <h2>Service context</h2>
                <span className="badge">{summary.services.length} services</span>
              </div>
              <div className="card-pad stack">
                <select
                  value={selectedService}
                  onChange={(event) => setSelectedService(event.target.value)}
                >
                  {summary.services.map((service) => (
                    <option key={service.id} value={service.name}>
                      {service.name}
                    </option>
                  ))}
                </select>
                {serviceGraph ? (
                  <div className="stack">
                    <div className="row between">
                      <span className="subtle">Connected edges</span>
                      <strong>{serviceGraph.edges?.length || 0}</strong>
                    </div>
                    <div className="row between">
                      <span className="subtle">Evidence chunks</span>
                      <strong>{serviceGraph.evidence?.length || 0}</strong>
                    </div>
                    {serviceGraph.evidence?.slice(0, 2).map((item: any) => (
                      <div className="source" key={item.chunk_id}>
                        <strong>{item.source_title}</strong>
                        <p>{item.snippet}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty">No service graph selected.</div>
                )}
              </div>
            </section>
          </div>

          {radius && (
            <section className="card" style={{ marginTop: 18 }}>
              <div className="section-head">
                <h2>Blast Radius — {radius.service_name}</h2>
                <span className="badge">graph edges only</span>
              </div>
              <div className="card-pad grid three">
                <div className="stack">
                  <h3>Depends on</h3>
                  {radius.dependencies.length ? (
                    radius.dependencies.map((name: string) => (
                      <span className="badge info" key={name}>{name}</span>
                    ))
                  ) : (
                    <p className="subtle">No dependency edges</p>
                  )}
                  <h3>Env vars</h3>
                  {radius.env_vars.length ? (
                    radius.env_vars.map((name: string) => <code key={name}>{name}</code>)
                  ) : (
                    <p className="subtle">No env var edges</p>
                  )}
                </div>
                <div className="stack">
                  <h3>Direct dependents</h3>
                  {radius.direct_dependents.length ? (
                    radius.direct_dependents.map((name: string) => (
                      <span className="badge warning" key={name}>{name}</span>
                    ))
                  ) : (
                    <p className="subtle">No dependent edges</p>
                  )}
                  <h3>Second hop</h3>
                  {radius.second_hop_dependents.length ? (
                    radius.second_hop_dependents.map((name: string) => (
                      <span className="badge" key={name}>{name}</span>
                    ))
                  ) : (
                    <p className="subtle">None</p>
                  )}
                </div>
                <div className="stack">
                  <h3>Impact</h3>
                  {radius.impact_statements.map((line: string) => (
                    <p className="subtle" key={line}>• {line}</p>
                  ))}
                </div>
              </div>
            </section>
          )}

          <div className="grid two" style={{ marginTop: 18 }}>
            <section className="card">
              <div className="section-head">
                <h2>File References</h2>
                <span className="badge">{summary.files.length} shown</span>
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Path</th>
                    <th>Language</th>
                    <th>Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.files.slice(0, 12).map((file) => (
                    <tr key={file.id || file.path}>
                      <td>
                        <code>{file.path}</code>
                      </td>
                      <td>{file.language || "—"}</td>
                      <td>{file.summary || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section className="card">
              <div className="section-head">
                <h2>Evidence Paths</h2>
                <span className="badge">{edges.length} edges</span>
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Relationship</th>
                    <th>From</th>
                    <th>To</th>
                  </tr>
                </thead>
                <tbody>
                  {edges.slice(0, 12).map((edge, index) => (
                    <tr key={`${edge.relationship}-${edge.from_id}-${edge.to_id}-${index}`}>
                      <td>{edge.relationship}</td>
                      <td>
                        <code>{edge.from_id}</code>
                      </td>
                      <td>
                        <code>{edge.to_id}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>

          <section className="card" style={{ marginTop: 18 }}>
            <div className="section-head">
              <h2>Graph Nodes</h2>
              <div className="row">
                <select value={nodeType} onChange={(event) => setNodeType(event.target.value)}>
                  <option value="">All node types</option>
                  {nodeTypes.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
                <span className="badge">{filteredNodes.length} nodes</span>
              </div>
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>ID</th>
                  <th>Name / path</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {filteredNodes.slice(0, 80).map((node) => (
                  <tr key={`${node.node_type}-${node.id}`}>
                    <td>{node.node_type}</td>
                    <td>
                      <code>{node.id}</code>
                    </td>
                    <td>{node.name || node.path || node.source_title || node.route || "—"}</td>
                    <td className="subtle">{node.summary || node.command || node.pattern || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : (
        <div className="empty">Ingest a repository to populate the graph explorer.</div>
      )}
    </Page>
  );
}

function EvidenceGraph({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: any[];
  edges: any[];
  selected: any | null;
  onSelect: (node: any) => void;
}) {
  const graph = useMemo(() => {
    const records = new Map(nodes.map((node) => [String(node.id), node]));
    const connectedIds: string[] = [];
    for (const edge of edges) {
      for (const id of [edge.from_id, edge.to_id]) {
        if (id && !connectedIds.includes(String(id))) connectedIds.push(String(id));
      }
    }
    const ids = connectedIds.slice(0, 86);
    const visibleNodes = ids.map((id, index) => {
      const record = records.get(id) || { id, node_type: inferNodeType(id) };
      const angle = index * 2.399963229728653;
      const radius = 42 + Math.sqrt(index + 1) * 37;
      return {
        ...record,
        x: 500 + Math.cos(angle) * Math.min(radius, 420),
        y: 270 + Math.sin(angle) * Math.min(radius * 0.55, 225),
      };
    });
    const visibleIds = new Set(visibleNodes.map((node) => String(node.id)));
    const visibleEdges = edges
      .filter((edge) => visibleIds.has(String(edge.from_id)) && visibleIds.has(String(edge.to_id)))
      .slice(0, 180);
    const byId = new Map(visibleNodes.map((node) => [String(node.id), node]));
    return { nodes: visibleNodes, edges: visibleEdges, byId };
  }, [nodes, edges]);

  if (!graph.nodes.length) {
    return <div className="empty">No connected ArcadeDB nodes were returned for this project.</div>;
  }

  return (
    <div className="live-graph-layout">
      <div className="live-graph-canvas">
        <svg viewBox="0 0 1000 540" role="img" aria-label="Live ArcadeDB project graph">
          <g className="graph-edge-layer">
            {graph.edges.map((edge, index) => {
              const from = graph.byId.get(String(edge.from_id));
              const to = graph.byId.get(String(edge.to_id));
              if (!from || !to) return null;
              return (
                <line
                  key={`${edge.relationship}-${edge.from_id}-${edge.to_id}-${index}`}
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                />
              );
            })}
          </g>
          <g className="graph-node-layer">
            {graph.nodes.map((node, index) => {
              const active = selected?.id === node.id;
              const core = ["Project", "Repository", "ContextWindow", "Service", "Package"].includes(node.node_type);
              return (
                <g
                  key={node.id}
                  className={`graph-node graph-node-${String(node.node_type).toLowerCase()} ${active ? "active" : ""}`}
                  transform={`translate(${node.x} ${node.y})`}
                  onClick={() => onSelect(node)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => event.key === "Enter" && onSelect(node)}
                >
                  <circle r={core ? 9 : 5.5} />
                  {(core || active || index < 14) && (
                    <text x={core ? 13 : 9} y={4}>{graphNodeLabel(node)}</text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <aside className="graph-inspector">
        <span className="panel-label">Node inspector</span>
        {selected ? (
          <>
            <strong>{graphNodeLabel(selected)}</strong>
            <span className="badge info">{selected.node_type}</span>
            <dl>
              {Object.entries(selected)
                .filter(([key, value]) => !["x", "y", "@rid", "@type", "node_type"].includes(key) && value !== "" && value != null)
                .slice(0, 8)
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
                  </div>
                ))}
            </dl>
          </>
        ) : (
          <p>Select a node to inspect the exact properties returned by ArcadeDB.</p>
        )}
        <div className="graph-legend">
          <span><i className="memory" />HCAG memory</span>
          <span><i className="source" />Source</span>
          <span><i className="structure" />Code structure</span>
        </div>
      </aside>
    </div>
  );
}

function inferNodeType(id: string) {
  if (id.startsWith("win:")) return "ContextWindow";
  if (id.startsWith("chunk_")) return "KnowledgeChunk";
  if (id.startsWith("item_")) return "KnowledgeItem";
  if (id.startsWith("file:")) return "File";
  if (id.startsWith("repo:")) return "Repository";
  if (id.startsWith("package:")) return "Package";
  if (id.startsWith("dependency:")) return "Dependency";
  if (id.startsWith("language:")) return "Language";
  return "ConnectedNode";
}

function graphNodeLabel(node: any) {
  const value = node.name || node.path || node.filename || node.source_title || node.id;
  const label = String(value).split("/").pop() || String(value);
  return label.length > 28 ? `${label.slice(0, 26)}…` : label;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <section className="card metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </section>
  );
}
