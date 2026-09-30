"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import GitHubIcon from "@/components/icons/GitHubIcon";
import BrandLogo from "@/components/BrandLogo";
import { API, api } from "@/lib/api";

type Providers = {
  github: boolean;
  google: boolean;
  email: boolean;
  development: boolean;
  public_demo?: boolean;
  details?: Record<string, { configured: boolean; setup: string }>;
};

export default function Login() {
  const [providers, setProviders] = useState<Providers>();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [emailStage, setEmailStage] = useState<"request" | "verify">("request");
  const [developmentCode, setDevelopmentCode] = useState("");
  const [showDev, setShowDev] = useState(false);
  const [displayName, setDisplayName] = useState("Demo User");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Providers>("/api/auth/providers").then(setProviders).catch((exc) => setError(exc.message));
    const oauthError = new URLSearchParams(window.location.search).get("error");
    if (oauthError) setError(oauthError);
  }, []);

  async function enterDemo(identity: "google" | "github" | "guest" | "email") {
    setBusy(true);
    setError("");
    const name = identity === "email" ? displayName || "Email demo user" : "Guest operator";
    try {
      await api("/api/auth/demo-login", {
        method: "POST",
        body: JSON.stringify({
          identity: identity === "email" ? "email" : "guest",
          display_name: name,
        }),
      });
      window.location.assign("/workspace");
    } catch (exc: any) {
      setError(exc.message);
      setBusy(false);
    }
  }

  async function requestCode() {
    if (!email.trim()) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result: any = await api("/api/auth/email/request", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setEmailStage("verify");
      if (result.development_code) {
        setDevelopmentCode(result.development_code);
        setCode(result.development_code);
        setMessage("Development mode: the one-time code is filled in below.");
      } else {
        setMessage(`A one-time code was sent to ${email}.`);
      }
    } catch (exc: any) {
      setError(exc.message);
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode() {
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/email/verify", {
        method: "POST",
        body: JSON.stringify({ email, code }),
      });
      window.location.assign("/workspace");
    } catch (exc: any) {
      setError(exc.message);
      setBusy(false);
    }
  }

  async function devLogin() {
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/dev-login", {
        method: "POST",
        body: JSON.stringify({
          email: email || "demo@memoryworks.local",
          display_name: displayName,
        }),
      });
      window.location.assign("/workspace");
    } catch (exc: any) {
      setError(exc.message);
      setBusy(false);
    }
  }

  return (
    <main className="om-home login-page">
      <header className="login-nav">
        <Link href="/"><BrandLogo /></Link>
        <Link href="/">Back to MemoryWorks</Link>
      </header>

      <section className="login-stage">
        <aside>
          <span className="home-eyebrow"><i /> One calm place for company context</span>
          <h1>Bring your company memory with you.</h1>
          <p>
            Sign in once, connect the systems your team already uses, and ask across code,
            conversations, documents, decisions, and history.
          </p>
          <div className="login-memory-lines" aria-hidden="true">
            <span><i>GH</i><b>Repository truth</b><em /></span>
            <span><i>SL</i><b>Team decisions</b><em /></span>
            <span><i>GD</i><b>Company documents</b><em /></span>
            <span><i>O</i><b>Unified context</b></span>
          </div>
        </aside>

        <div className="login-card">
          <div>
            <span className="home-eyebrow">Workspace access</span>
            <h2>Log in to MemoryWorks</h2>
            <p>Use your work identity. Your source permissions remain the boundary.</p>
          </div>

          {error && <div className="login-message error">{error}</div>}
          {message && <div className="login-message">{message}</div>}

          <div className="oauth-stack">
              <div className="oauth-stack">
                <a
                  className={`login-oauth ${!providers?.google ? "disabled" : ""}`}
                  href={providers?.google ? `${API}/api/auth/google/start` : undefined}
                  aria-disabled={!providers?.google}
                >
                  <span className="provider-g">G</span>
                  <strong>Continue with Google</strong>
                  <span>→</span>
                </a>
                <a
                  className={`login-oauth ${!providers?.github ? "disabled" : ""}`}
                  href={providers?.github ? `${API}/api/auth/github/start` : undefined}
                  aria-disabled={!providers?.github}
                >
                  <span><GitHubIcon size={18} /></span>
                  <strong>Continue with GitHub</strong>
                  <span>→</span>
                </a>
                {providers?.github && (
                  <small className="login-legal">
                    GitHub sign-in also connects your repositories, so you can pick what to
                    remember right after — no second authorization.
                  </small>
                )}
              </div>
              {providers && !providers.github && (
                <small className="login-legal">
                  GitHub sign-in activates once {`GITHUB_CLIENT_ID`} / {`GITHUB_CLIENT_SECRET`} are
                  set on the deployment and this origin is a registered OAuth callback URL.
                </small>
              )}
          </div>

          <div className="login-divider"><span>or use work email</span></div>

          {emailStage === "request" ? (
            <div className="email-login-form">
              <label htmlFor="login-email">Work email</label>
              <div>
                <input
                  id="login-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@company.com"
                />
                <button disabled={busy || !email.trim() || providers?.email === false} onClick={requestCode}>
                  {busy ? "Sending…" : "Email me a code"}
                </button>
              </div>
            </div>
          ) : (
            <div className="email-login-form">
              <div className="email-code-head">
                <label htmlFor="login-code">Six-digit code</label>
                <button className="plain" onClick={() => { setEmailStage("request"); setCode(""); }}>
                  Change email
                </button>
              </div>
              <div>
                <input
                  id="login-code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  placeholder="000000"
                  aria-describedby={developmentCode ? "development-code-note" : undefined}
                />
                <button disabled={busy || code.length !== 6} onClick={verifyCode}>
                  {busy ? "Checking…" : "Enter workspace"}
                </button>
              </div>
              {developmentCode && <small id="development-code-note">Local code: {developmentCode}</small>}
            </div>
          )}

          {providers && (!providers.github || !providers.google) && (
            <details className="provider-setup">
              <summary>Why is a provider unavailable?</summary>
              {!providers.github && <p><strong>GitHub:</strong> {providers.details?.github?.setup}</p>}
              {!providers.google && <p><strong>Google:</strong> {providers.details?.google?.setup}</p>}
              <Link href="/docs#authentication">Open the exact OAuth setup guide →</Link>
            </details>
          )}

          {(providers?.public_demo || providers?.development) && (
            <div className="dev-access">
              {providers?.public_demo && (
                <button className="button" onClick={() => void enterDemo("guest")} disabled={busy}>
                  {busy ? "Opening demo…" : "Enter the public demo"}
                </button>
              )}
              <button className="text-button" onClick={() => setShowDev(!showDev)}>
                {showDev ? "Hide" : "Use"} local development access
              </button>
              {showDev && (
                <div className="stack">
                  <div className="field">
                    <label>Display name</label>
                    <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
                  </div>
                  <button className="button" onClick={devLogin} disabled={busy}>
                    {busy ? "Signing in…" : "Enter local workspace"}
                  </button>
                </div>
              )}
            </div>
          )}

          <small className="login-legal">
            {providers?.public_demo
                ? "The public demo shares one seeded workspace. Provider sign-ins use your real identity; demo access never requests an external account."
                : "MemoryWorks never sends source credentials to the browser or to a model."}
          </small>
        </div>
      </section>
    </main>
  );
}
