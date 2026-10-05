import Link from "next/link";
import { BrandLockup, BrandMark } from "@/components/BrandLogo";
import HomeCommandOrb from "@/components/HomeCommandOrb";
import BriefingDemo from "@/components/landing/BriefingDemo";
import CodeTabs from "@/components/landing/CodeTabs";
import HeroStage from "@/components/landing/HeroStage";
import LandingMotion from "@/components/landing/LandingMotion";
import MemoryGraph from "@/components/landing/MemoryGraph";
import "./site.css";

/* The page is server-rendered text; every moving part is a small client island
   that loads after it. The 3D scenes fetch three.js only when idle or near. */

const EYEBROW = "The memory layer for engineering organizations";

const SOURCES = ["GitHub", "Slack", "Google Drive", "Notion", "Microsoft Teams", "PDFs & docs", "Websites"];
const AGENTS = ["Claude Code", "Cursor", "VS Code", "Claude", "ChatGPT", "Python SDK", "CLI", "MCP"];

const LOOP = [
  { tag: "Brief", title: "Context served", body: "The agent says what it is about to do and gets back the incidents, decisions, and constraints that apply — each with its source.", code: "get_orgmemory_briefing" },
  { tag: "Decide", title: "A person approves", body: "Anything consequential waits for a human: inline in the chat, or in Approvals. The verdict is advice, never authorization.", code: "requires_approval" },
  { tag: "Act", title: "Action taken", body: "The agent does the work in the editor or tool it already runs in. MemoryWorks never acts on its own.", code: "Claude Code · Cursor · SDK" },
  { tag: "Record", title: "Outcome observed", body: "What happened goes back into memory, so the next briefing knows what worked and what didn't.", code: "record_orgmemory_outcome" },
];

const TRUST = [
  { title: "Nothing changes without a person", body: "Agents can read, cross-check, and propose. Changes to memory or a connected tool wait for approval." },
  { title: "Source permissions carry through", body: "People and agents only see what the underlying source already lets them see." },
  { title: "Credentials stay on the server", body: "OAuth tokens never reach the browser or a model." },
  { title: "Every answer cites its source", body: "Each line links to the memory and the pull request, thread, or document behind it." },
];

export default function HomePage() {
  return (
    <main className="mw-site" id="top">
      <LandingMotion />

      <header className="mw-nav">
        <div className="wrap">
          <Link href="/" className="mw-brand" aria-label="MemoryWorks home">
            <BrandLockup />
          </Link>
          <nav aria-label="Public navigation">
            <a href="#how">How it works</a>
            <a href="#product">Product</a>
            <a href="#loop">The loop</a>
            <a href="#developers">Developers</a>
            <Link href="/docs">Docs</Link>
          </nav>
          <div className="mw-nav-right">
            <Link href="/login">Log in</Link>
            <Link href="/login" className="mw-btn primary small">Open workspace</Link>
          </div>
        </div>
        <span className="mw-progress" aria-hidden="true" />
      </header>

      {/* ---- hero */}
      <section className="mw-hero">
        <div className="mw-hero-glow" aria-hidden="true"><i /><i /><i /></div>
        <div className="mw-hero-grid" aria-hidden="true" />
        <div className="wrap mw-hero-inner">
          <div className="mw-hero-copy">
            <span className="mw-eyebrow mw-rise" style={{ ["--d" as string]: "0ms" }}><i />{EYEBROW}</span>
            <h1 aria-label="Give every engineering change its full company context.">
              <span className="mw-line" aria-hidden="true"><span style={{ ["--d" as string]: "80ms" }}>Give every engineering change its </span></span><span className="mw-line" aria-hidden="true"><span className="grad" style={{ ["--d" as string]: "200ms" }}>full company context.</span></span>
            </h1>
            <p className="lede mw-rise" style={{ ["--d" as string]: "340ms" }}>
              MemoryWorks remembers every incident, decision, owner, and dependency your team already
              learned — and briefs people and AI agents before they change something.
            </p>
            <div className="mw-cta mw-rise" style={{ ["--d" as string]: "440ms" }}>
              <Link href="/login" className="mw-btn primary" data-magnetic>Open workspace <span aria-hidden="true">↗</span></Link>
              <Link href="/docs" className="mw-btn ghost">Read the docs</Link>
            </div>
            <ul className="mw-hero-facts mw-rise" style={{ ["--d" as string]: "540ms" }}>
              <li><b>Cited</b> every fact links to its source</li>
              <li><b>Time-aware</b> old truths kept, not overwritten</li>
              <li><b>Approved</b> people authorize what agents change</li>
            </ul>
          </div>
          <HeroStage />
        </div>
        <a href="#how" className="mw-scroll-cue" aria-label="Scroll to how it works"><span /></a>
      </section>

      {/* ---- what it connects */}
      <section className="mw-strip" aria-label="Integrations">
        <p>Learns from the tools your team already uses · briefs the agents you already run</p>
        <div className="mw-marquee">
          <div className="mw-marquee-track">
            {[0, 1].map((copy) => (
              <ul key={copy} aria-hidden={copy === 1 ? true : undefined}>
                {[...SOURCES, ...AGENTS].map((name) => <li key={name}>{name}</li>)}
              </ul>
            ))}
          </div>
        </div>
      </section>

      {/* ---- the moment it matters */}
      <section className="mw-section" id="how">
        <div className="wrap mw-moment">
          <div data-reveal>
            <span className="mw-kicker">Before it acts</span>
            <h2>Your agent is about to change something your team has changed before.</h2>
            <p className="sub">
              Before it acts, it asks MemoryWorks. The answer isn&rsquo;t a list of search results. It
              names the constraint the change would break, the outage it would repeat, and whether to
              stop and get a person.
            </p>
            <ul className="mw-checks">
              <li>One call before any consequential change</li>
              <li>A verdict: proceed, proceed with context, or requires approval</li>
              <li>Every line cited to the source it came from</li>
            </ul>
          </div>
          <div data-reveal>
            <BriefingDemo />
          </div>
        </div>
      </section>

      {/* ---- what it does */}
      <section className="mw-section" id="product">
        <div className="wrap">
          <div className="mw-head" data-reveal>
            <span className="mw-kicker">What it does</span>
            <h2>One memory, for the people and the agents doing the work.</h2>
          </div>
          <div className="mw-bento" data-stagger>
            <article className="mw-tile span-4" data-tilt>
              <div className="mw-tile-copy">
                <h3>Source-backed, time-aware memory</h3>
                <p>Code, pull requests, threads, and documents become one memory. Every fact points to where it came from — and when something changes, the old truth is kept and marked superseded, not overwritten.</p>
              </div>
              <div className="mw-fact" aria-hidden="true">
                <div className="mw-fact-row is-current">
                  <span className="mw-fact-dot" />
                  <div>
                    <strong>auth-service signs tokens with RS256</strong>
                    <small>Current · since Aug 2026</small>
                  </div>
                  <ul><li>GitHub · PR #412</li><li>Slack · #identity</li></ul>
                </div>
                <div className="mw-fact-row is-old">
                  <span className="mw-fact-dot" />
                  <div>
                    <strong>auth-service signs tokens with HS256</strong>
                    <small>Superseded · kept for history</small>
                  </div>
                  <ul><li>Notion · ADR-009</li></ul>
                </div>
              </div>
            </article>

            <article className="mw-tile span-2" data-tilt>
              <div className="mw-tile-copy">
                <h3>A verdict, not search results</h3>
                <p>Every briefing ends in a call an agent can act on.</p>
              </div>
              <ul className="mw-verdicts" aria-label="Briefing verdicts">
                <li className="v-proceed"><b>Proceed</b>nothing on record says wait</li>
                <li className="v-proceed_with_context"><b>With context</b>go ahead, knowing this</li>
                <li className="v-requires_approval"><b>Requires approval</b>a person decides</li>
                <li className="v-none"><b>No memory</b>nothing known — not a yes</li>
              </ul>
            </article>

            <article className="mw-tile span-2" data-tilt>
              <div className="mw-tile-copy">
                <h3>Owners, as of today</h3>
                <p>Who owns a service now, who did before, and who to wake up.</p>
              </div>
              <div className="mw-owners" aria-hidden="true">
                <div><span className="mw-av a1">SP</span><b>search-api</b><small>Search platform</small></div>
                <div><span className="mw-av a2">ID</span><b>auth-service</b><small>Identity</small></div>
                <div><span className="mw-av a3">DI</span><b>ingest-workers</b><small>Data infra</small></div>
              </div>
            </article>

            <article className="mw-tile span-2" data-tilt>
              <div className="mw-tile-copy">
                <h3>Impact before change</h3>
                <p>What else a change touches: services, shared databases, downstream jobs.</p>
              </div>
              <svg className="mw-deps" viewBox="0 0 260 120" aria-hidden="true">
                <defs>
                  <linearGradient id="mwDep" x1="0" x2="1"><stop offset="0" stopColor="#50a8fc" /><stop offset="1" stopColor="#f485ad" /></linearGradient>
                </defs>
                <path d="M54 30 C110 30 110 60 130 60 M54 90 C110 90 110 60 130 60 M130 60 C150 60 150 30 206 30 M130 60 C150 60 150 90 206 90" fill="none" stroke="url(#mwDep)" strokeWidth="1.5" />
                <g className="mw-deps-nodes">
                  <rect x="4" y="18" width="62" height="24" rx="12" /><text x="35" y="34">ingest</text>
                  <rect x="4" y="78" width="62" height="24" rx="12" /><text x="35" y="94">billing</text>
                  <rect x="100" y="46" width="60" height="28" rx="14" className="hub" /><text x="130" y="64">db-main</text>
                  <rect x="194" y="18" width="62" height="24" rx="12" /><text x="225" y="34">reports</text>
                  <rect x="194" y="78" width="62" height="24" rx="12" /><text x="225" y="94">alerts</text>
                </g>
              </svg>
            </article>

            <article className="mw-tile span-2" data-tilt>
              <div className="mw-tile-copy">
                <h3>Learns from outcomes</h3>
                <p>Each briefing&rsquo;s result is recorded, so the next one knows what worked.</p>
              </div>
              <ol className="mw-ledger" aria-hidden="true">
                <li><span className="ok" />retries · notifications<em>succeeded</em></li>
                <li><span className="bad" />pool resize · ingest<em>failed</em></li>
                <li><span className="ok" />key rotation · auth<em>succeeded</em></li>
              </ol>
            </article>
          </div>
        </div>
      </section>

      {/* ---- the graph */}
      <section className="mw-section mw-graph-section" id="graph">
        <div className="wrap">
          <div className="mw-head center" data-reveal>
            <span className="mw-kicker">How it fits</span>
            <h2>Everything your team knows, wired to every agent that acts on it.</h2>
            <p className="sub">Sources become kinds of memory; memory briefs the agents; outcomes flow back in. Drag the graph, or hover a node to see what it carries.</p>
          </div>
          <div data-reveal>
            <MemoryGraph />
          </div>
        </div>
      </section>

      {/* ---- the loop, pinned while it plays */}
      <section className="mw-loop-section" id="loop">
        <div className="mw-loop-pin">
          <div className="wrap mw-loop-grid">
            <div>
              <span className="mw-kicker">The loop</span>
              <h2>Context served, action taken, <span className="grad">outcome observed.</span></h2>
              <p className="sub">
                Anyone can index the same Slack and the same repositories. What only your workspace builds
                up is the record of which context actually led to the right action.
              </p>
              <ol className="mw-steps">
                {LOOP.map((step, i) => (
                  <li key={step.tag} data-step={i}>
                    <b>{String(i + 1).padStart(2, "0")} · {step.tag}</b>
                    <strong>{step.title}</strong>
                    <p>{step.body}</p>
                  </li>
                ))}
              </ol>
            </div>
            <div className="mw-ring" aria-hidden="true">
              <svg viewBox="0 0 400 400">
                <defs>
                  <linearGradient id="mwRing" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#50a8fc" />
                    <stop offset=".4" stopColor="#a168fa" />
                    <stop offset=".7" stopColor="#f485ad" />
                    <stop offset="1" stopColor="#fecb91" />
                  </linearGradient>
                </defs>
                <circle cx="200" cy="200" r="150" className="track" />
                <circle cx="200" cy="200" r="150" className="fill" pathLength="1" />
              </svg>
              {LOOP.map((step, i) => (
                <span key={step.tag} className={`mw-ring-node n${i}`} data-step={i}>{step.tag}</span>
              ))}
              <div className="mw-ring-core">
                <BrandMark />
                {LOOP.map((step, i) => <code key={step.tag} data-step={i}>{step.code}</code>)}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ---- developers */}
      <section className="mw-section" id="developers">
        <div className="wrap mw-dev">
          <div data-reveal>
            <span className="mw-kicker">Built for developers</span>
            <h2>Plug it into the agents you already run.</h2>
            <p className="sub">An MCP server for assistants, a Python SDK and CLI for everything else, and the same API underneath.</p>
            <ol className="mw-dev-steps">
              <li><b>1</b><div><strong>Connect a source</strong><span>Sign in with GitHub and your repositories are ready to pick. Add Slack, Drive, Notion, or uploads.</span></div></li>
              <li><b>2</b><div><strong>Issue a workspace key</strong><span>Scoped to one workspace, for CLIs, SDKs, and MCP clients.</span></div></li>
              <li><b>3</b><div><strong>Agents ask before they act</strong><span>One briefing call before a consequential change; one outcome call after.</span></div></li>
            </ol>
            <Link href="/docs" className="mw-link">Read the docs <span aria-hidden="true">→</span></Link>
          </div>
          <div data-reveal>
            <CodeTabs />
          </div>
        </div>
      </section>

      {/* ---- trust */}
      <section className="mw-section mw-trust">
        <div className="wrap mw-trust-grid">
          <div data-reveal>
            <span className="mw-kicker">Safe by design</span>
            <h2>Agents investigate. People authorize.</h2>
          </div>
          <div className="mw-trust-list" data-stagger>
            {TRUST.map((item) => (
              <div key={item.title}>
                <strong>{item.title}</strong>
                <p>{item.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---- close */}
      <section className="mw-close">
        <div className="mw-close-glow" aria-hidden="true" data-parallax="-18" />
        <div className="wrap" data-reveal>
          <div className="mw-close-mark"><BrandMark /></div>
          <h2>What does your team already know?</h2>
          <p>Connect one source and ask. Sign in with GitHub and your repositories are ready to pick.</p>
          <HomeCommandOrb />
        </div>
      </section>

      <footer className="mw-foot">
        <div className="wrap">
          <div className="mw-foot-brand">
            <Link href="/" className="mw-brand" aria-label="MemoryWorks home"><BrandLockup /></Link>
            <p>Source-backed memory for engineering teams and the agents working alongside them.</p>
          </div>
          <nav aria-label="Product">
            <h4>Product</h4>
            <a href="#how">How it works</a>
            <a href="#product">What it does</a>
            <a href="#loop">The loop</a>
          </nav>
          <nav aria-label="Developers">
            <h4>Developers</h4>
            <Link href="/docs">Documentation</Link>
            <Link href="/docs#mcp">MCP server</Link>
            <Link href="/docs#python-sdk">Python SDK</Link>
          </nav>
          <nav aria-label="Account">
            <h4>Account</h4>
            <Link href="/login">Log in</Link>
            <Link href="/login">Open workspace</Link>
            <a href="#top">Back to top ↑</a>
          </nav>
        </div>
        <div className="wrap mw-foot-base">
          <span>© 2026 MemoryWorks</span>
          <span>memoryworks.app</span>
        </div>
      </footer>
    </main>
  );
}
