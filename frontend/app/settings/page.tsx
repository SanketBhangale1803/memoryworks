"use client";

import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api, errorMessage } from "@/lib/api";
import type { AuthUser, Project, RuntimeSettings, Team } from "@/lib/types";

export default function Settings() {
  const [settings, setSettings] = useState<RuntimeSettings>();
  const [me, setMe] = useState<AuthUser>();
  const [teams, setTeams] = useState<Team[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [repairProject, setRepairProject] = useState("");
  const [repairing, setRepairing] = useState(false);
  const [repairMessage, setRepairMessage] = useState("");
  const [teamName, setTeamName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<RuntimeSettings>("/api/settings/runtime").then(setSettings).catch((exc) => setError(errorMessage(exc)));
    api<Project[]>("/api/projects").then(items => {
      setProjects(items);
      if (items[0]) setRepairProject(items[0].id);
    }).catch((exc) => setError(errorMessage(exc)));
    api<AuthUser>("/api/auth/me").then(user => {
      setMe(user);
      return api<Team[]>(`/api/workspaces/${user.active_workspace_id}/teams`);
    }).then(setTeams).catch((exc) => setError(errorMessage(exc)));
  }, []);

  async function createTeam() {
    if (!teamName.trim() || !me?.active_workspace_id) return;
    try {
      await api(`/api/workspaces/${me.active_workspace_id}/teams`, {
        method: "POST",
        body: JSON.stringify({name: teamName}),
      });
      setTeamName("");
      setTeams(await api<Team[]>(`/api/workspaces/${me.active_workspace_id}/teams`));
    } catch (exc) { setError(errorMessage(exc)); }
  }

  async function repairMemory() {
    if (!repairProject) return;
    setRepairing(true);
    setError("");
    setRepairMessage("");
    try {
      const result = await api<{ cleanup?: { retained_sources?: number }; current_memories?: number }>(`/api/projects/${repairProject}/memory/repair`, {
        method: "POST",
        body: JSON.stringify({repository_only: false, clear_work_history: false}),
      });
      setRepairMessage(`Memory rebuilt from ${result.cleanup?.retained_sources || 0} current sources. ${result.current_memories || 0} atomic memories passed the current extraction rules.`);
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setRepairing(false);
    }
  }

  return (
    <Page title="Settings" description="Runtime safety, authentication, and infrastructure configuration.">
      {error && <div className="notice error">{error}</div>}
      <div className="grid two">
        <section className="card card-pad stack">
          <h2>Context governance</h2>
          <SettingRow label="Source-backed answers" enabled />
          <SettingRow label="Team scope enforcement" enabled />
          <p className="subtle">
            Context is security-trimmed before retrieval and records its source version vector.
          </p>
        </section>
        <section className="card card-pad stack">
          <h2>Memory and policy</h2>
          <div className="row between">
            <span>Graph backend</span>
            <strong>{settings?.graph_backend || "arcadedb"}</strong>
          </div>
          <div className="row between">
            <span>ArcadeDB database</span>
            <strong>{settings?.arcadedb_database || "orgmemory"}</strong>
          </div>
          <SettingRow label="HCAG routing" enabled={settings?.hcag_enabled} />
          <SettingRow label="Temporal memory" enabled />
        </section>
        <section className="card card-pad stack">
          <h2>Teams and memory scope</h2>
          <p className="subtle">Restrict projects and sources so agents only receive context their team is allowed to use.</p>
          {teams.map(team => <div className="row between" key={team.id}><span>{team.name}</span><strong>{team.member_count} members · {team.project_count} projects</strong></div>)}
          <div className="row"><input aria-label="Team name" value={teamName} onChange={event => setTeamName(event.target.value)} placeholder="Platform"/><button className="button secondary" onClick={createTeam} disabled={!teamName.trim()}>Create team</button></div>
        </section>
        <section className="card card-pad stack">
          <h2>Application auth</h2>
          <SettingRow label="Dev login" enabled={settings?.auth_dev_mode} />
          <div className="row between">
            <span>Environment</span>
            <strong>{settings?.environment || "development"}</strong>
          </div>
        </section>
        <section className="card card-pad stack">
          <h2>Connector auth</h2>
          <SettingRow label="GitHub OAuth configured" enabled={settings?.github_oauth_configured} />
          <SettingRow label="Slack OAuth configured" enabled={settings?.slack_oauth_configured} />
          <SettingRow label="GitHub live memory updates" enabled={settings?.github_live_updates} />
          <SettingRow label="Slack live memory updates" enabled={settings?.slack_live_updates} />
        </section>
        <section className="card card-pad stack">
          <h2>Memory health</h2>
          <p className="subtle">Re-run current repository, document, and Slack sources through the latest extraction and HCAG indexing rules. Original evidence is preserved.</p>
          <select aria-label="Project to repair" value={repairProject} onChange={event => setRepairProject(event.target.value)}>{projects.map(project => <option value={project.id} key={project.id}>{project.name}</option>)}</select>
          <button className="button secondary" disabled={!repairProject || repairing} onClick={repairMemory}>{repairing ? "Rebuilding memory…" : "Rebuild current memory"}</button>
          {repairMessage && <div className="notice">{repairMessage}</div>}
        </section>
      </div>
    </Page>
  );
}

function SettingRow({ label, enabled }: { label: string; enabled?: boolean }) {
  return (
    <div className="row between">
      <span>{label}</span>
      <span className={`badge ${enabled ? "success" : ""}`}>{enabled ? "Enabled" : "Disabled"}</span>
    </div>
  );
}
