"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import Page from "@/components/Page";
import GitHubIcon from "@/components/icons/GitHubIcon";
import { API, api } from "@/lib/api";

type SourceKind = "paste" | "github" | "slack" | "website" | "files";

const DOCUMENT_ACCEPT = ".md,.markdown,.txt,.json,.yaml,.yml,.toml,.xml,.csv,.tsv,.log,.pdf,.docx,.xlsx,.pptx,.odt,.rtf,.html,.htm,.eml,.py,.js,.ts,.tsx,.jsx,.go,.rs,.java,.sh,.sql";

const sourceChoices = [
  {id: "paste", icon: "✦", title: "Paste knowledge", note: "Fastest", description: "A decision, policy, report, or any useful context."},
  {id: "files", icon: "📎", title: "Upload documents", note: "PDF · Office", description: "PDF, Word, Excel, PowerPoint, HTML, mail exports, code, and text."},
  {id: "website", icon: "🌐", title: "Ingest a website", note: "Public URL", description: "A web page or a hosted document becomes searchable memory."},
  {id: "github", icon: "github", title: "Connect a repository", note: "Automatic", description: "Code, docs, issues, pull requests, and ownership."},
  {id: "slack", icon: "SL", title: "Remember a channel", note: "Continuous", description: "Team decisions, conventions, and conversations."},
] as const;

export default function Ingest() {
  const [kind, setKind] = useState<SourceKind>("paste");
  const [projects, setProjects] = useState<any[]>([]);
  const [repositories, setRepositories] = useState<any[]>([]);
  const [channels, setChannels] = useState<any[]>([]);
  const [connections, setConnections] = useState<any[]>([]);
  const [teams, setTeams] = useState<any[]>([]);
  const [project, setProject] = useState("__new__");
  const [newProject, setNewProject] = useState("Company memory");
  const [team, setTeam] = useState("");
  const [repo, setRepo] = useState("");
  const [repoName, setRepoName] = useState("");
  const [channel, setChannel] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fileProgress, setFileProgress] = useState("");
  const [sourceType, setSourceType] = useState("doc");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<any>();
  /* Indexing every repository at once is the normal first move after
     connecting GitHub, so it belongs beside the one-repository picker rather
     than only in the API. */
  const [bulk, setBulk] = useState<{ queued: number } | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  // Sources links here with ?source=github (or files, website, slack, paste), so
  // connecting a system lands on choosing what to read from it, not a menu.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("source");
    if (sourceChoices.some(choice => choice.id === requested)) setKind(requested as SourceKind);
  }, []);

  useEffect(() => {
    api<any[]>("/api/projects").then(items => {
      setProjects(items);
      if (items[0]) setProject(items[0].id);
    });
    api<any[]>("/api/connectors").then(setConnections);
    api<{result?: {repositories?: any[]}}>("/api/connectors/github/tools/list_repositories", {
      method: "POST",
      body: JSON.stringify({arguments: {}}),
    }).then(response => setRepositories(response.result?.repositories || [])).catch(() => undefined);
    api<any[]>("/api/connectors/slack/resources").then(items => {
      setChannels(items);
      if (items[0]) setChannel(items[0].id);
    }).catch(() => undefined);
    api<any>("/api/auth/me")
      .then(me => api<any[]>(`/api/workspaces/${me.active_workspace_id}/teams`))
      .then(setTeams).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!busy) { setPhase(0); return; }
    const timer = window.setInterval(() => setPhase(value => Math.min(value + 1, 2)), 850);
    return () => window.clearInterval(timer);
  }, [busy]);

  const connected = (provider: string) => connections.some(item => item.provider === provider && item.connected);
  const selectedRepo = useMemo(() => repositories.find(item => item.clone_url === repo), [repositories, repo]);

  async function ensureProject() {
    if (project !== "__new__") return project;
    const created: any = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({name: newProject.trim() || "Company memory", team_ids: team ? [team] : []}),
    });
    setProjects(items => [created, ...items]);
    setProject(created.id);
    return created.id;
  }

  async function buildMemory() {
    setBusy(true);
    setError("");
    setResult(undefined);
    try {
      const team_ids = team ? [team] : [];
      let response: any;
      if (kind === "github") {
        response = await api("/api/ingest/github", {
          method: "POST",
          body: JSON.stringify({repo_url_or_path: repo, project_name: repoName || selectedRepo?.full_name || selectedRepo?.name, team_ids}),
        });
      } else if (kind === "website") {
        const projectId = await ensureProject();
        response = await api("/api/ingest/website", {
          method: "POST",
          body: JSON.stringify({project_id: projectId, url: websiteUrl.trim(), team_ids}),
        });
      } else if (kind === "files") {
        const projectId = await ensureProject();
        const aggregate: any = {chunks_created: 0, memory_units_created: 0, warnings: [] as string[], project_id: projectId};
        for (let index = 0; index < files.length; index += 1) {
          const file = files[index];
          setFileProgress(`Uploading ${file.name} (${index + 1}/${files.length})…`);
          const form = new FormData();
          form.append("project_id", projectId);
          form.append("file", file);
          const uploaded: any = await api("/api/ingest/file", {method: "POST", body: form});
          aggregate.chunks_created += uploaded.chunks_created ?? 0;
          aggregate.memory_units_created += uploaded.memory_units_created ?? 0;
          aggregate.warnings.push(...(uploaded.warnings || []));
          aggregate.format = uploaded.format;
        }
        setFileProgress("");
        response = aggregate;
      } else if (kind === "slack") {
        const projectId = await ensureProject();
        response = await api("/api/ingest/slack", {
          method: "POST",
          body: JSON.stringify({project_id: projectId, channel_id: channel, limit: 200, team_ids}),
        });
      } else {
        const projectId = await ensureProject();
        response = await api("/api/ingest/upload", {
          method: "POST",
          body: JSON.stringify({project_id: projectId, source_type: sourceType, title: title.trim() || "Company knowledge", content, team_ids, artifact_type: sourceType === "report" ? "report" : ""}),
        });
      }
      setPhase(2);
      setResult(response);
    } catch (exc: any) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  async function indexEverything() {
    setBulkBusy(true);
    setError("");
    try {
      const response = await api<{ repositories_queued: number }>("/api/ingest/github/all", {
        method: "POST",
        body: JSON.stringify({ include_archived: false }),
      });
      setBulk({ queued: response.repositories_queued });
    } catch (cause: any) {
      setError(cause.message);
    } finally {
      setBulkBusy(false);
    }
  }

  const canBuild = kind === "paste"
    ? Boolean(content.trim() && (project !== "__new__" || newProject.trim()))
    : kind === "github"
      ? Boolean(repo && (repoName || selectedRepo))
      : kind === "website"
        ? Boolean(/^https?:\/\/.+\..+|^[^\s]+\.[^\s]+/.test(websiteUrl.trim()) && (project !== "__new__" || newProject.trim()))
        : kind === "files"
          ? Boolean(files.length && (project !== "__new__" || newProject.trim()))
          : Boolean(channel && (project !== "__new__" || newProject.trim()));
  const memoryCount = result?.memory_units_created ?? result?.memory_unit_ids?.length ?? 0;

  return <Page title="Add knowledge" description="Pick what MemoryWorks should remember. It reads it, links it, and keeps it tied to the source.">
    <section className="memory-builder">
      <div className="builder-progress" aria-label="Memory creation progress">
        <span className="active"><i>1</i>Choose</span><b/><span className={busy || result ? "active" : ""}><i>2</i>Remember</span><b/><span className={result ? "active" : ""}><i>3</i>Ask</span>
      </div>

      {!result ? <>
        <div className="source-choice-grid">
          {sourceChoices.map(choice => <button key={choice.id} className={`source-choice ${kind === choice.id ? "selected" : ""}`} onClick={() => {setKind(choice.id);setError("");}}>
            <span className="source-choice-icon">{choice.icon === "github" ? <GitHubIcon size={24}/> : choice.icon}</span>
            <span><small>{choice.note}</small><strong>{choice.title}</strong><p>{choice.description}</p></span>
            <i className="choice-check">✓</i>
          </button>)}
        </div>


        <div className="builder-card">
          {kind === "paste" && <div className="quick-memory-form">
            <div className="builder-title"><span className="spark-orbit"><i/><i/><i/></span><div><h2>What should your company remember?</h2><p>Paste it as written. Only confident, source-backed statements become memory.</p></div></div>
            <textarea autoFocus aria-label="Company knowledge" value={content} onChange={event => setContent(event.target.value)} placeholder="Paste a real decision, policy, ownership note, project brief, or report from your company…" />
            <input aria-label="Source title" value={title} onChange={event => setTitle(event.target.value)} placeholder="Optional source title" />
          </div>}

          {kind === "website" && <div className="quick-memory-form">
            <div className="builder-title"><span className="source-hero-icon">🌐</span><div><h2>Ingest a website</h2><p>Public pages and hosted documents (PDF, DOCX, and more) are fetched, converted to text, and indexed with a link back to the source.</p></div></div>
            <input autoFocus aria-label="Website URL" value={websiteUrl} onChange={event => setWebsiteUrl(event.target.value)} placeholder="https://company.example.com/handbook or https://docs.example.com/architecture.pdf" />
            <p className="privacy-note"><i/> Public URLs only. Private addresses are refused; redirects are validated on every hop.</p>
          </div>}

          {kind === "files" && <div className="quick-memory-form">
            <div className="builder-title"><span className="source-hero-icon">📎</span><div><h2>Upload documents</h2><p>PDF, Word, Excel, PowerPoint, OpenDocument, HTML, mail exports, CSV/JSON/YAML, and source files. Page, slide, and sheet structure is preserved.</p></div></div>
            <label className="file-drop">
              <input type="file" multiple accept={DOCUMENT_ACCEPT} onChange={event => setFiles(Array.from(event.target.files || []))} />
              {files.length ? <><strong>{files.length} file{files.length === 1 ? "" : "s"} selected</strong><span>{files.map(file => file.name).join(", ").slice(0, 120)}{files.map(file => file.name).join(", ").length > 120 ? "…" : ""}</span></> : <><strong>Choose files</strong><span>or drop them here — up to 100MB per file</span></>}
            </label>
            {fileProgress && <p className="privacy-note">{fileProgress}</p>}
          </div>}

          {kind === "github" && <div className="quick-memory-form">
            <div className="builder-title"><span className="source-hero-icon"><GitHubIcon size={27}/></span><div><h2>Choose a repository</h2><p>MemoryWorks reads the repository and builds its project memory automatically.</p></div></div>
            {!connected("github") ? <div className="builder-connect"><strong>Give MemoryWorks access to your repositories</strong><p>Authorize once, then pick repositories right here. Private repositories are supported.</p><a className="button" href={`${API}/api/connectors/github/auth/start`}>Continue with GitHub →</a></div> : <><select aria-label="GitHub repository" value={repo} onChange={event => {setRepo(event.target.value);const match=repositories.find(item=>item.clone_url===event.target.value);setRepoName(match?.full_name || match?.name || "");}}><option value="">Select a repository…</option>{repositories.map(item => <option key={item.id} value={item.clone_url}>{item.full_name}{item.private ? " · Private" : ""}</option>)}</select><p className="privacy-note"><i/> Private repositories supported. Existing source permissions are preserved.</p>
              <div className="bulk-index">
                <div>
                  <strong>Or index everything you have access to</strong>
                  <span>
                    {bulk
                      ? `${bulk.queued} repositor${bulk.queued === 1 ? "y" : "ies"} queued. Each becomes its own memory space as it finishes.`
                      : `${repositories.length} repositor${repositories.length === 1 ? "y" : "ies"} visible to this workspace's GitHub grant.`}
                  </span>
                </div>
                <button className="button secondary" disabled={bulkBusy || !repositories.length} onClick={indexEverything}>
                  {bulkBusy ? "Queueing…" : bulk ? "Queue again" : "Index all repositories"}
                </button>
              </div></>}
          </div>}

          {kind === "slack" && <div className="quick-memory-form">
            <div className="builder-title"><span className="source-hero-icon slack">SL</span><div><h2>Choose a Slack channel</h2><p>Remember team decisions and conventions with links back to each message.</p></div></div>
            {!connected("slack") ? <div className="builder-connect"><strong>Connect Slack once</strong><p>Choose which channels MemoryWorks may read.</p><a className="button" href={`${API}/api/connectors/slack/auth/start`}>Connect Slack →</a></div> : <select aria-label="Slack channel" value={channel} onChange={event => setChannel(event.target.value)}>{channels.map(item => <option key={item.id} value={item.id}>#{item.name}{item.is_private ? " · Private" : ""}</option>)}</select>}
          </div>}

          {kind !== "github" && <div className="builder-project-row"><label>Save to</label><select value={project} onChange={event => setProject(event.target.value)}><option value="__new__">New memory space</option>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>{project === "__new__" && <input aria-label="New memory space name" value={newProject} onChange={event => setNewProject(event.target.value)} />}</div>}

          <details className="builder-options"><summary>Options <span>Team visibility and source type</span></summary><div>{teams.length ? <div className="field"><label>Visible to</label><select value={team} onChange={event => setTeam(event.target.value)}><option value="">Everyone in this workspace</option>{teams.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div> : null}{kind === "paste" && <div className="field"><label>Source type</label><select value={sourceType} onChange={event => setSourceType(event.target.value)}>{["doc","report","slack_export","incident","log","text"].map(value => <option key={value}>{value.replace(/_/g," ")}</option>)}</select></div>}</div></details>

          {error && <div className="notice error">{error}</div>}
          {busy ? <div className="memory-building"><div className="memory-pulse"><i/><i/><i/></div><div><strong>{kind === "files" && fileProgress ? fileProgress : ["Reading the source…","Extracting atomic memory…","Linking the memory graph…"][phase]}</strong><span>Source → chunks → memory → relationships</span></div></div> : <button className="button builder-submit" disabled={!canBuild} onClick={buildMemory}>{kind === "paste" ? "Remember this" : kind === "github" ? "Build repository memory" : kind === "website" ? "Ingest this page" : kind === "files" ? "Remember these files" : "Remember this channel"}<span>→</span></button>}
        </div>
      </> : <section className="memory-success">
        <div className="success-rings"><i/><i/><i/><span>✓</span></div>
        <span className="panel-label">Memory is ready</span>
        <h2>MemoryWorks learned from this source.</h2>
        <p>{memoryCount} atomic memories and {result.chunks_created ?? result.knowledge_chunks_created ?? 0} evidence chunks are now available to your agents.</p>
        <div className="success-stats"><div><strong>{memoryCount}</strong><span>Memories</span></div><div><strong>{result.source_revision?.version || 1}</strong><span>Source version</span></div><div><strong>{result.change_set?.conflicts?.length || 0}</strong><span>Conflicts</span></div></div>
        <div className="success-actions"><Link className="button" href="/workspace">Ask your memory →</Link><Link className="button secondary" href="/memories">See memories</Link><button className="text-button" onClick={() => {setResult(undefined);setContent("");setTitle("");}}>Add another source</button></div>
      </section>}
    </section>

  </Page>;
}
