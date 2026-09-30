import Link from "next/link";
import HomeCommandOrb from "@/components/HomeCommandOrb";
import TraceField from "@/components/TraceField";
import MemoryPhotoStudy from "@/components/MemoryPhotoStudy";
import { ORG_READ_TOOLS, ORG_TOOL_NAMES, ORG_WRITE_TOOLS } from "@/lib/orgTools";
import "./trace.css";

function Mark() { return <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M2 26V6h7v14h7V6h7v14h7v6H16v-6H9v6Z" fill="currentColor" /></svg>; }

export default function HomePage() {
  return <main className="trace-home" id="top">
    <a className="trace-skip" href="#main-content">Skip to content</a>
    <header className="trace-nav">
      <Link href="/" className="trace-brand" aria-label="MemoryWorks home"><Mark />memoryworks</Link>
      <nav aria-label="Public navigation"><a href="#how">The approach</a><Link href="/docs">Docs ↗</Link></nav>
      <Link href="/login" className="trace-login">Log in <span>↗</span></Link>
    </header>

    <section className="trace-hero" id="main-content" data-memory-stage="0">
      <div className="trace-hero-label"><span className="trace-cross">✳</span><span>The memory layer for engineering organizations</span><span>01 / A LIVING RECORD</span></div>
      <div className="trace-hero-composition">
        <div className="trace-hero-content">
          <h1>Memory leaves<br />a <em>trace.</em></h1>
          <div className="trace-hero-bottom"><div><h2>Give every engineering change its full company context.</h2><p>Incidents, decisions, owners, and dependencies. MemoryWorks connects what your team knows to the people and AI agents about to act.</p><Link href="/login" className="trace-button">Open workspace <span>↗</span></Link><a className="trace-text-link" href="#how">Follow the trace ↓</a></div></div>
          <div className="trace-hero-footnote"><span>SCATTER / CONNECT / RECALL</span><span aria-hidden="true">↳</span></div>
        </div>
        <MemoryPhotoStudy />
      </div>
    </section>

    <div className="trace-context-strip" aria-label="From sources to useful company context"><span>GitHub / Slack / Files</span><span aria-hidden="true">↳</span><strong>What your team knows.<br />Ready for what comes next.</strong><a href="#how">See the connections ↓</a></div>

    <div className="trace-narrative" id="how">
      <aside className="trace-visual"><TraceField /></aside>
      <div className="trace-chapters">
        <section className="trace-chapter" data-memory-stage="0"><span className="trace-kicker">01 / GATHER THE FRAGMENTS</span><h2>The answer is<br />already somewhere.</h2><p>In the incident report. In a decision buried in a thread. In the person who remembers why.</p><p>Bring code, postmortems, conversations, and docs into one time-aware memory graph.</p><div className="trace-source-list"><span>GitHub <b>↘</b></span><span>Slack <b>↘</b></span><span>Files &amp; exports <b>↘</b></span></div><small>Incidents, decisions, and owners stay tied to evidence.</small></section>
        <section className="trace-chapter" data-memory-stage="1"><span className="trace-kicker">02 / MAKE THE CONNECTION</span><h2>Keep the knowledge.<br />Keep the why.</h2><p>A fact becomes useful when you can see where it came from and what it affects. Promoted facts cite their sources; uncertain information stays searchable.</p><div className="trace-evidence"><span>DECISION</span><strong>Retry policy</strong><span>↳ SOURCE</span><strong>Incident postmortem</strong><span>↳ RELATIONSHIP</span><strong>Service · owner · dependency</strong></div><small>Illustrative relationships, not live workspace data.</small></section>
        <section className="trace-chapter" data-memory-stage="2"><span className="trace-kicker">03 / RECALL WHAT MATTERS</span><h2>A better place<br />to begin.</h2><p>Agents get briefed before they act. Retrieve the relevant decisions, constraints, prior incidents, and dependencies before changing a service.</p><p>Context served, action taken, outcome observed. Record what happened so the next decision has more to work with.</p><Link href="/docs" className="trace-text-link">Explore source-backed briefings ↗</Link><div className="trace-agents">For people. And the agents alongside them.<br /><strong>Cursor / Claude / Codex / VS Code</strong></div></section>
      </div>
    </div>

    <section className="trace-photo-section">
      <figure className="trace-photo"><svg viewBox="0 0 1200 700" role="img" aria-label="Sunlight traces a path through a dense forest, rendered in black and ivory."><defs><filter id="two-ink-photo" colorInterpolationFilters="sRGB"><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncR type="discrete" tableValues="0 1"/><feFuncG type="discrete" tableValues="0 1"/><feFuncB type="discrete" tableValues="0 1"/></feComponentTransfer><feColorMatrix type="matrix" values=".9176 0 0 0 .03137 0 .9059 0 0 .03137 0 0 .8784 0 .03137 0 0 0 1 0"/></filter></defs><image href="/memoryworks/forest.jpg" width="1200" height="700" preserveAspectRatio="xMidYMid slice" filter="url(#two-ink-photo)"/></svg><figcaption><span>FIELD NOTE / 001</span><span>Nothing grows in isolation.</span><a href="https://images.unsplash.com/photo-1441974231531-c6227db76b6e" aria-label="Photography source on Unsplash">Photography / Unsplash ↗</a></figcaption></figure>
      <div className="trace-photo-copy"><span className="trace-kicker">CONNECTED, WITH INTENTION</span><h2>More context.<br />Human control.</h2><p>Agents investigate. People authorize. Changes to company memory enter a scoped approval queue for a person to review.</p><Link href="/login" className="trace-text-link">See it in your workspace ↗</Link><p className="trace-tool-count">{ORG_TOOL_NAMES.length} agent tools / {ORG_READ_TOOLS.length} read-only / {ORG_WRITE_TOOLS.length} human-governed</p></div>
    </section>

    <section className="trace-ask"><div><span className="trace-kicker">START WITH A QUESTION</span><h2>What does your<br />team already know?</h2><p>Bring a question into your workspace. Follow the evidence from there.</p></div><HomeCommandOrb /></section>
    <footer className="trace-footer"><div><Link href="/" className="trace-brand"><Mark />memoryworks</Link><p>Source-backed memory.<br />For whatever comes next.</p></div><div><Link href="/docs">Documentation ↗</Link><Link href="/login">Enter workspace ↗</Link></div><a href="#top" className="trace-back">Back to the beginning ↑</a><div className="trace-footer-wordmark" aria-hidden="true">memoryworks<span>↳</span></div><div className="trace-footer-bottom"><span>MEMORY LEAVES A TRACE.</span><span>KEEP WHAT MATTERS. ↳</span></div></footer>
  </main>;
}
