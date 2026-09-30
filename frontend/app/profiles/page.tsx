"use client";

import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api } from "@/lib/api";

export default function Profiles() {
  const [projects, setProjects] = useState<any[]>([]);
  const [project, setProject] = useState("");
  const [profile, setProfile] = useState<any>();
  const [skills, setSkills] = useState<any[]>([]);
  const [skillName, setSkillName] = useState("company-workflow");
  const [error, setError] = useState("");

  useEffect(() => {
    api<any[]>("/api/projects").then(items => {
      setProjects(items);
      if (items[0]) setProject(items[0].id);
    }).catch(exc => setError(exc.message));
  }, []);

  async function refresh(activeProject = project) {
    if (!activeProject) return;
    const [nextProfile, nextSkills] = await Promise.all([
      api(`/api/memory/profiles/project/${activeProject}`),
      api<any[]>(`/api/memory/skills?project_id=${activeProject}`),
    ]);
    setProfile(nextProfile);
    setSkills(nextSkills);
  }

  useEffect(() => { refresh(project).catch(exc => setError(exc.message)); }, [project]);

  async function compileSkill() {
    setError("");
    try {
      await api("/api/memory/skills/compile", {
        method: "POST",
        body: JSON.stringify({project_id: project, name: skillName}),
      });
      await refresh();
    } catch (exc: any) { setError(exc.message); }
  }

  const groups = [
    ["Current facts", profile?.current_facts],
    ["Remembered decisions", profile?.decisions],
    ["Policies", profile?.policies],
    ["Procedures", profile?.procedures],
    ["Owners", profile?.owners],
    ["Dependencies", profile?.dependencies],
  ];

  return <Page title="Company & Project Profiles" description="Profiles are assembled at request time from current source-backed memories within the caller’s authorized scope—not stored as static summary blobs." action={<select className="project-select" value={project} onChange={event => setProject(event.target.value)}>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}>
    {error && <div className="notice error">{error}</div>}
    <section className="panel">
      <div className="panel-head"><div><span className="panel-label">Dynamic project profile</span><h2>{profile?.name || "Select a project"}</h2></div><span>{profile?.sources?.length || 0} sources</span></div>
      <div className="panel-body grid two">{groups.map(([label, items]: any) => <article className="card card-pad" key={label}><span className="panel-label">{label}</span><h2>{items?.length || 0}</h2><p>{items?.[0]?.content || "No current source-backed memory in this category."}</p></article>)}</div>
    </section>
    <section className="panel">
      <div className="panel-head"><div><span className="panel-label">Executable company knowledge</span><h2>Agent skill specs</h2></div><div className="row"><input value={skillName} onChange={event => setSkillName(event.target.value)} aria-label="Skill name"/><button className="button secondary" onClick={compileSkill} disabled={!project || !skillName.trim()}>Compile from memory</button></div></div>
      <div className="panel-body">{skills.length ? skills.map((skill, index) => <article className="clean-source-row" key={skill.id}><span>{String(index + 1).padStart(2, "0")}</span><div><div className="row between"><strong>{skill.name}</strong><small>v{skill.version}</small></div><p>{skill.spec.steps.length} steps · {skill.spec.policies.length} policies · {skill.spec.evidence.length} evidence sources</p><span className={`badge ${skill.status === "current" ? "success" : "warning"}`}>{skill.status}</span></div></article>) : <div className="empty">Compile a skill after ingesting high-confidence procedures or policies.</div>}</div>
    </section>
  </Page>;
}
