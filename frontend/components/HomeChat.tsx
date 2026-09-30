"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { BrandMark } from "@/components/BrandLogo";
import { api } from "@/lib/api";

type Model = {
  id: string;
  label: string;
  company: string;
  model: string;
  configured: boolean;
  default: boolean;
};

/* Shown until /api/models answers. Order matches the providers the backend
   knows how to call, so the picker never changes shape after it loads. */
const fallbackModels: Model[] = [
  { id: "glm", label: "GLM", company: "Z.AI via OpenRouter", model: "GLM 5.3 Flash", configured: false, default: true },
  { id: "gpt", label: "GPT", company: "OpenAI", model: "gpt-4o-mini", configured: false, default: false },
  { id: "claude", label: "Claude", company: "Anthropic", model: "Claude Sonnet", configured: false, default: false },
  { id: "gemini", label: "Gemini", company: "Google", model: "Gemini 3.6 Flash", configured: false, default: false },
  { id: "grok", label: "Grok", company: "xAI", model: "Grok 4.5", configured: false, default: false },
  { id: "kimi", label: "Kimi", company: "Moonshot AI", model: "Kimi K2.6", configured: false, default: false },
];

const lanes = [
  { code: "01", name: "Source scouts", detail: "Search every authorized system in parallel.", result: "3 systems · 11 candidates" },
  { code: "02", name: "Graph foragers", detail: "Walk services, owners, decisions, and code.", result: "9 edges traversed" },
  { code: "03", name: "Truth historians", detail: "Separate what is current from what expired.", result: "1 conflict resolved" },
  { code: "04", name: "Context compiler", detail: "Deduplicate into one cited envelope.", result: "3,842 tokens" },
];

/* Short chip labels, full questions when asked — three long sentences will not
   sit on one row, and the chips read better as topics anyway. */
const seeds = [
  { chip: "Why is the site down?", question: "The website is down. Find out why." },
  { chip: "What changed in checkout?", question: "What changed in checkout this morning?" },
  { chip: "Who owns billing?", question: "Who owns billing, and what should I check first?" },
];

type Scenario = { answer: ReactNode; cites: string[]; act: string; grounded: string };

const scenarios: Record<string, Scenario> = {
  [seeds[0].question]: {
    answer: (
      <>
        Checkout is failing because production still points at the retired{" "}
        <code>PAYMENTS_V1_URL</code>. The migration decision in <code>#payments</code> moved traffic
        to V2, but PR #128 only updated staging — so the two environments disagree.
      </>
    ),
    cites: ["GitHub · PR #128", "Slack · #payments", "Postmortem · checkout"],
    act: "Update the production config reference, run the checkout smoke test, and post the result back to the incident thread.",
    grounded: "94% grounded",
  },
  [seeds[1].question]: {
    answer: (
      <>
        PR #128 moved checkout staging onto the V2 payments endpoint at 09:14. The diff never
        touched production, and the release note still describes V1 as current — that note is now
        stale rather than wrong.
      </>
    ),
    cites: ["GitHub · PR #128 diff", "Deploy history", "Release note v4.2"],
    act: "Reconcile the release note with the actual rollout state and request approval from the checkout owner.",
    grounded: "91% grounded",
  },
  [seeds[2].question]: {
    answer: (
      <>
        Billing belongs to the Payments Platform team, with Maya Chen as escalation owner. The
        current billing playbook says to check ledger lag and webhook delivery before restarting
        anything — a restart clears the queue and hides the cause.
      </>
    ),
    cites: ["Service catalog", "Playbook · billing", "Decision · ownership"],
    act: "Open the current billing checklist, gather the two safe diagnostics, and notify the recorded owner with cited context.",
    grounded: "96% grounded",
  },
};

const unknownScenario: Scenario = {
  answer: (
    <>
      This public preview cannot see inside your company. In a connected workspace, the same
      question would search only the sources you are permitted to read, and MemoryWorks would
      withhold an answer entirely rather than guess from thin evidence.
    </>
  ),
  cites: ["No company sources connected yet"],
  act: "Open a workspace, connect an authorized source, and run this question against your real company memory.",
  grounded: "preview",
};

export default function HomeChat() {
  const [models, setModels] = useState<Model[]>(fallbackModels);
  const [selected, setSelected] = useState("gpt");
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [asked, setAsked] = useState("");
  const [phase, setPhase] = useState(-1);
  const timer = useRef<number | undefined>(undefined);
  const picker = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ models: Model[]; default: string }>("/api/models")
      .then((payload) => {
        if (!payload.models?.length) return;
        setModels(payload.models);
        const preferred =
          payload.models.find((item) => item.configured && item.default) ||
          payload.models.find((item) => item.configured) ||
          payload.models.find((item) => item.id === payload.default) ||
          payload.models[0];
        if (preferred) setSelected(preferred.id);
      })
      .catch(() => undefined);
    return () => window.clearInterval(timer.current);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!picker.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onEscape);
    };
  }, [menuOpen]);

  const activeModel = useMemo(
    () => models.find((item) => item.id === selected) || models[0],
    [models, selected],
  );
  const scenario = scenarios[asked] || unknownScenario;

  /* Branches resolve one at a time so the swarm reads as parallel work
     settling, rather than a progress bar counting to four. */
  const ask = useCallback((prompt: string) => {
    const question = prompt.trim();
    if (!question) return;
    setAsked(question);
    setDraft("");
    setPhase(0);
    window.clearInterval(timer.current);
    timer.current = window.setInterval(() => {
      setPhase((value) => {
        if (value >= lanes.length) {
          window.clearInterval(timer.current);
          return lanes.length;
        }
        return value + 1;
      });
    }, 700);
  }, []);

  const settled = phase >= lanes.length;

  return (
    <>
      <section className="home-chat" aria-label="Ask the MemoryWorks company brain">
        <header className="chat-bar">
          <div className="chat-bar-id">
            <BrandMark />
            <strong>MemoryWorks</strong>
            <small>Company brain</small>
          </div>

          <div className="model-picker" ref={picker}>
            <button
              className="model-trigger"
              aria-expanded={menuOpen}
              aria-haspopup="listbox"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <i className={activeModel?.configured ? "on" : ""} />
              <small>Model</small>
              <span>{activeModel?.label}</span>
              <em>▾</em>
            </button>
            {menuOpen && (
              <div className="model-menu" role="listbox">
                {models.map((model) => (
                  <button
                    key={model.id}
                    role="option"
                    aria-selected={model.id === selected}
                    className={`${model.configured ? "ready" : ""} ${model.id === selected ? "picked" : ""}`}
                    onClick={() => {
                      setSelected(model.id);
                      setMenuOpen(false);
                    }}
                  >
                    <i />
                    <div>
                      <strong>{model.label}</strong>
                      <small>{model.company} · {model.model}</small>
                    </div>
                    <em>{model.configured ? "Ready" : "Add key"}</em>
                  </button>
                ))}
                <p>Models answer only from retrieved, authorized evidence. Add a key in your workspace to switch a model from preview to live.</p>
              </div>
            )}
          </div>
        </header>

        <div className="chat-stage">
          {!asked ? (
            <div className="chat-rest">
              <span className="chat-rest-mark" aria-hidden="true">
                <i /><i /><i />
                <BrandMark />
              </span>
              <strong>Ask your company anything.</strong>
              <p>
                A question here fans out to specialist subagents across every connected system, then
                comes back as one answer you can trace to its sources.
              </p>
            </div>
          ) : (
            <div className="chat-turn">
              <div className="chat-asked">
                <span>YOU</span>
                <p>{asked}</p>
              </div>

              <div className="chat-swarm">
                <div className="swarm-head">
                  <span>Subagent swarm</span>
                  <small className={settled ? "done" : ""}>
                    <i />
                    {settled ? "Context compiled" : "Foraging in parallel"}
                  </small>
                </div>
                <div className="swarm-lanes">
                  {lanes.map((lane, index) => {
                    const state = phase > index ? "settled" : phase === index ? "working" : "";
                    return (
                      <article key={lane.code} className={`swarm-lane ${state}`}>
                        <header>
                          <span>{lane.code}</span>
                          <i className="swarm-dot" />
                        </header>
                        <strong>{lane.name}</strong>
                        <p>{lane.detail}</p>
                        <em>
                          {phase > index ? lane.result : phase === index ? "working…" : "queued"}
                        </em>
                      </article>
                    );
                  })}
                </div>
              </div>

              {settled && (
                <div className="chat-answer">
                  <div className="answer-head">
                    <span><BrandMark /> MemoryWorks</span>
                    <span className="answer-grounded">
                      <i />
                      {scenario.grounded} · {activeModel?.label}
                    </span>
                  </div>
                  <p>{scenario.answer}</p>
                  <div className="answer-cites">
                    {scenario.cites.map((cite, index) => (
                      <span key={cite} style={{ ["--c" as string]: index }}>{cite}</span>
                    ))}
                  </div>
                  <div className="answer-act">
                    <span>Prepared · waiting for approval</span>
                    <p>{scenario.act}</p>
                    <button>Review the proposed fix <span aria-hidden="true">→</span></button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <footer className="chat-compose">
          <div className="chat-field">
            <textarea
              rows={2}
              value={draft}
              placeholder="Why is the checkout service returning 502s since this morning?"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  ask(draft);
                }
              }}
              aria-label="Ask MemoryWorks a question"
            />
            <button
              className="chat-send"
              onClick={() => ask(draft)}
              disabled={!draft.trim()}
              aria-label="Ask"
            >
              ↑
            </button>
          </div>
          <div className="chat-seeds">
            <span>Try</span>
            {seeds.map((seed) => (
              <button key={seed.chip} onClick={() => ask(seed.question)}>{seed.chip}</button>
            ))}
            <Link href="/login">Ask your own memory →</Link>
          </div>
        </footer>
      </section>
      <p className="chat-note">
        A worked example on public data. Connect a workspace to run the same loop over your company.
      </p>
    </>
  );
}
