import Link from "next/link";
import HomeCommandOrb from "@/components/HomeCommandOrb";
import "./site.css";

/* The mark, as drawn: light on black. Its alpha comes from its own brightness,
   so on the dark site it composites back to exactly the glow that was drawn. */
function Mark({ className = "", eager = false }: { className?: string; eager?: boolean }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={`mw-mark ${className}`.trim()}
      src="/memoryworks/mark.png"
      alt=""
      aria-hidden="true"
      loading={eager ? "eager" : "lazy"}
      decoding="async"
    />
  );
}

const PILLARS = [
  {
    n: "01",
    title: "Incidents, decisions, and owners stay tied to evidence.",
    body: "Code, pull requests, Slack threads, and documents become a time-aware memory. Every fact points back to where it came from, and older truths are kept, not overwritten.",
  },
  {
    n: "02",
    title: "Agents get briefed before they act.",
    body: "Before an agent changes a service, it asks what this company already learned: the constraints, the incidents, what else it touches. It gets a verdict, not a pile of search results.",
  },
  {
    n: "03",
    title: "Agents investigate. People authorize.",
    body: "Agents can read, cross-check, and propose. Nothing they propose changes company memory or a connected tool until a person approves it.",
  },
];

const LOOP = [
  { tag: "Brief", title: "Context served", body: "The agent states what it is about to do and gets the relevant history back.", code: "get_orgmemory_briefing" },
  { tag: "Decide", title: "A person approves", body: "Anything consequential waits for a human — inline in the chat, or in Approvals.", code: "requires_approval" },
  { tag: "Act", title: "Action taken", body: "The agent does the work, in the editor or the tool it already runs in.", code: "Cursor · Claude Code" },
  { tag: "Record", title: "Outcome observed", body: "What happened goes back into memory, so the next briefing knows what worked.", code: "record_orgmemory_outcome" },
];

export default function HomePage() {
  return (
    <main className="mw-site" id="top">
      <header className="mw-nav">
        <div className="wrap">
          <Link href="/" className="mw-brand" aria-label="MemoryWorks home">
            <Mark eager />
            MemoryWorks
          </Link>
          <nav aria-label="Public navigation">
            <a href="#how">How it works</a>
            <a href="#loop">The loop</a>
            <Link href="/docs">Docs</Link>
          </nav>
          <div className="mw-nav-right">
            <Link href="/login">Log in</Link>
            <Link href="/login" className="mw-btn primary small">Open workspace</Link>
          </div>
        </div>
      </header>

      <section className="mw-hero">
        <div className="wrap">
          <div className="mw-hero-mark">
            <Mark className="glow" eager />
            <Mark eager />
          </div>
          <span className="mw-eyebrow"><i />The memory layer for engineering organizations</span>
          <h1>
            Give every engineering change its <span className="grad">full company context.</span>
          </h1>
          <p className="lede">
            MemoryWorks remembers every incident, decision, owner, and dependency your team already
            learned — and briefs people and AI agents before they change something.
          </p>
          <div className="mw-cta">
            <Link href="/login" className="mw-btn primary">Open workspace <span aria-hidden="true">↗</span></Link>
            <Link href="/docs" className="mw-btn ghost">Read the docs</Link>
          </div>
          <HomeCommandOrb />
        </div>
      </section>

      <section className="mw-section" id="how">
        <div className="wrap mw-moment">
          <div>
            <span className="mw-kicker">The moment it matters</span>
            <h2>Your agent is about to restart payments. It has done this before.</h2>
            <p className="sub">
              Before it acts, it asks MemoryWorks. The answer isn&rsquo;t a search result — it&rsquo;s the
              constraint it would break, the outage it would repeat, and a clear signal to stop and get a person.
            </p>
          </div>
          <div>
            <p className="mw-terminal"><b>agent ›</b>restart the payments connection pool</p>
            <article className="mw-brief" aria-label="Example briefing">
              <div className="mw-brief-head">
                <span><Mark />MemoryWorks briefing</span>
                <b>REQUIRES APPROVAL</b>
              </div>
              <h3>This changes production state for payments.</h3>
              <dl>
                <div><dt>Prior incident</dt><dd>payments outage: pool exhaustion</dd></div>
                <div><dt>Constraint</dt><dd>cap payments worker concurrency</dd></div>
                <div><dt>Blast radius</dt><dd>payments shares the PostgreSQL cluster</dd></div>
              </dl>
              <small>Every line links to the memory and source it came from.</small>
            </article>
          </div>
        </div>
      </section>

      <section className="mw-section">
        <div className="wrap">
          <span className="mw-kicker">What it does</span>
          <h2>One memory, for the people and the agents doing the work.</h2>
          <div className="mw-pillars">
            {PILLARS.map((pillar) => (
              <article className="mw-pillar" key={pillar.n}>
                <span>{pillar.n}</span>
                <h3>{pillar.title}</h3>
                <p>{pillar.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mw-section" id="loop">
        <div className="wrap">
          <span className="mw-kicker">The loop</span>
          <h2>Context served, action taken, <span className="grad">outcome observed.</span></h2>
          <p className="sub">
            Anyone can index the same Slack and the same repositories. What only your workspace builds up
            is the record of which context actually led to the right action.
          </p>
          <ol className="mw-loop">
            {LOOP.map((step) => (
              <li key={step.tag}>
                <b>{step.tag}</b>
                <strong>{step.title}</strong>
                <p>{step.body}</p>
                <code>{step.code}</code>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mw-section">
        <div className="wrap">
          <span className="mw-kicker">Where it connects</span>
          <h2>Brings in what your team knows. Answers wherever your agents work.</h2>
          <div className="mw-connect">
            <div>
              <h3>Sources</h3>
              <div className="mw-chips">
                {["GitHub", "Slack", "Google Drive", "Notion", "Microsoft Teams", "PDFs & docs", "Websites"].map((item) => <span key={item}>{item}</span>)}
              </div>
            </div>
            <div>
              <h3>Agents and editors</h3>
              <div className="mw-chips">
                {["Claude Code", "Cursor", "VS Code", "Claude", "ChatGPT", "Python SDK", "CLI"].map((item) => <span key={item}>{item}</span>)}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="mw-close">
        <div className="wrap">
          <Mark />
          <h2>What does your team already know?</h2>
          <p>Connect one source and ask. Sign in with GitHub and your repositories are ready to pick.</p>
          <div className="mw-cta">
            <Link href="/login" className="mw-btn primary">Open workspace <span aria-hidden="true">↗</span></Link>
          </div>
        </div>
      </section>

      <footer className="mw-foot">
        <div className="wrap">
          <Link href="/" className="mw-brand"><Mark />MemoryWorks</Link>
          <span>Source-backed memory for engineering teams.</span>
          <nav>
            <Link href="/docs">Docs</Link>
            <Link href="/login">Log in</Link>
            <a href="#top">Back to top ↑</a>
          </nav>
        </div>
      </footer>
    </main>
  );
}
