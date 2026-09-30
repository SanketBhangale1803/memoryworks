"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Page from "@/components/Page";
import { api } from "@/lib/api";

/* Identity, membership, and — for whoever owns this workspace — its people.
   Adding a teammate here drops them straight into the company brain: they sign
   in with their own account, land inside this workspace, and their approval
   requests reach the admins inline in the chat. */

type Member = {
  id: string;
  user_id?: string;
  email: string;
  display_name?: string;
  role: string;
  status: string;
};

const ROLES = ["member", "viewer", "admin", "owner"];

export default function Account() {
  const [user, setUser] = useState<any>();
  const [error, setError] = useState("");
  const [members, setMembers] = useState<Member[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [peopleError, setPeopleError] = useState("");

  useEffect(() => {
    api("/api/auth/me").then(setUser).catch((exc) => setError(exc.message));
  }, []);

  const isAdmin = Boolean(user && (user.role === "owner" || user.role === "admin"));

  useEffect(() => {
    if (!isAdmin || !user?.active_workspace_id) return;
    api<Member[]>(`/api/workspaces/${user.active_workspace_id}/members`)
      .then(setMembers)
      .catch(() => undefined);
  }, [isAdmin, user?.active_workspace_id]);

  async function loadMembers() {
    try {
      const items = await api<Member[]>(
        `/api/workspaces/${user.active_workspace_id}/members`,
      );
      setMembers(items);
    } catch {
      /* the roster refreshes on the next invite or reload either way */
    }
  }

  async function invite() {
    if (!email.trim()) return;
    setBusy(true);
    setNote("");
    setPeopleError("");
    try {
      const result = await api<{ invite_delivery?: string }>(
        `/api/workspaces/${user.active_workspace_id}/members/invite`,
        {
          method: "POST",
          body: JSON.stringify({ email: email.trim(), role }),
        },
      );
      const invited = email.trim();
      setEmail("");
      setNote(
        result.invite_delivery === "email"
          ? `Invitation email sent to ${invited}. They join this workspace when they sign in with that address.`
          : `Added ${invited}. No mail server is configured, so share ${window.location.origin}/login with them — signing in with that email drops them straight into this workspace.`,
      );
      await loadMembers();
    } catch (exc: any) {
      setPeopleError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    localStorage.removeItem("runbook_token");
    window.location.assign("/login");
  }

  return (
    <Page title="Account" description="Your identity, active workspace, and who else belongs here.">
      {error && <div className="notice error">{error}</div>}
      {user && (
        <div className="account-layout">
          <section className="card card-pad stack">
            <div className="account-identity">
              <span>
                {(user.display_name || "?")
                  .split(/\s+/)
                  .map((part: string) => part[0])
                  .join("")
                  .slice(0, 2)
                  .toUpperCase()}
              </span>
              <div>
                <h2>{user.display_name}</h2>
                <p>{user.email}</p>
              </div>
            </div>
            <div className="account-detail"><span>Identity provider</span><strong>{user.auth_provider}</strong></div>
            <div className="account-detail"><span>Workspace role</span><strong>{user.role}</strong></div>
            <button className="button secondary" onClick={signOut}>Sign out</button>
          </section>

          <div className="stack" style={{ gap: 18 }}>
            <section className="card">
              <div className="section-head"><div><span className="panel-label">Workspace access</span><h2>Memberships</h2></div></div>
              <div>
                {user.workspaces.map((workspace: any) => (
                  <div className="workspace-row" key={workspace.id}>
                    <div>
                      <strong>{workspace.name}</strong>
                      <small>{workspace.slug}</small>
                    </div>
                    <span className={`badge ${workspace.id === user.active_workspace_id ? "success" : ""}`}>
                      {workspace.id === user.active_workspace_id ? "Active" : workspace.role}
                    </span>
                  </div>
                ))}
              </div>
            </section>

            <section className="card">
              <div className="section-head">
                <div>
                  <span className="panel-label">Your team</span>
                  <h2>People</h2>
                </div>
                <Link href="/approvals">Approvals →</Link>
              </div>

              {isAdmin ? (
                <>
                  <div className="invite-form">
                    <input
                      type="email"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      onKeyDown={(event) => event.key === "Enter" && void invite()}
                      placeholder="teammate@company.com"
                      aria-label="Invite by email"
                    />
                    <select value={role} onChange={(event) => setRole(event.target.value)} aria-label="Workspace role">
                      {ROLES.map((value) => (
                        <option key={value} value={value}>{value}</option>
                      ))}
                    </select>
                    <button className="button" disabled={busy || !email.trim()} onClick={() => void invite()}>
                      Add person
                    </button>
                  </div>
                  <p className="invite-hint">
                    New people get an invitation email and land inside this workspace when they
                    sign in — no manual setup. Ask them to connect a repository afterwards and
                    every refresh request they raise shows up in your workspace.
                  </p>
                  {note && <div className="notice">{note}</div>}
                  {peopleError && <div className="notice error">{peopleError}</div>}
                  {members.length > 0 && (
                    <div className="member-list">
                      {members.map((member) => (
                        <div className="workspace-row" key={member.id}>
                          <div>
                            <strong>{member.display_name || member.email}</strong>
                            <small>{member.email}</small>
                          </div>
                          <span className={`badge ${member.status === "active" ? "success" : "warning"}`}>
                            {member.role} · {member.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="empty">
                  Only the workspace owner and admins manage people. Ask an admin to send you an
                  invitation.
                </div>
              )}
            </section>
          </div>
        </div>
      )}
    </Page>
  );
}
