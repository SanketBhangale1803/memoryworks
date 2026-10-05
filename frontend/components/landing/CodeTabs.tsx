"use client";

import { useState } from "react";

/* The same snippets the docs use, with neutral example questions. */
const TABS = [
  {
    id: "mcp",
    label: "MCP",
    file: "mcp.json",
    code: `{
  "mcpServers": {
    "memoryworks": {
      "command": "mcp_server/.venv/bin/python",
      "args": ["mcp_server/server.py", "--transport", "stdio"],
      "env": {
        "MEMORYWORKS_API_URL": "https://memoryworks.app",
        "MEMORYWORKS_API_KEY": "om_live_..."
      }
    }
  }
}`,
  },
  {
    id: "python",
    label: "Python",
    file: "agent.py",
    code: `from orgmemory import MemoryWorks

memory = MemoryWorks(
    base_url="https://memoryworks.app",
    api_key="om_live_...",
)

context = memory.ask(
    project_id="prj_platform",
    query="What changed in the auth service this week, and why?",
)

# Pass source-backed context to any model or agent.
agent.run(context.compiled_context)`,
  },
  {
    id: "cli",
    label: "CLI",
    file: "terminal",
    code: `export ORGMEMORY_API_URL=https://memoryworks.app
export ORGMEMORY_API_KEY=om_live_...

orgmemory projects
orgmemory ask prj_platform "Who owns the ingest pipeline?"
orgmemory ingest prj_platform --file ./incident-review.md --source-type doc`,
  },
];

export default function CodeTabs() {
  const [active, setActive] = useState(TABS[0].id);
  const [copied, setCopied] = useState(false);
  const tab = TABS.find((t) => t.id === active) ?? TABS[0];

  async function copy() {
    try {
      await navigator.clipboard.writeText(tab.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // clipboard blocked: nothing to do
    }
  }

  return (
    <div className="mw-code">
      <div className="mw-code-bar">
        <div role="tablist" aria-label="Integration examples">
          {TABS.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={t.id === active} className={t.id === active ? "is-active" : ""} onClick={() => setActive(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
        <span className="mw-code-file">{tab.file}</span>
        <button type="button" className="mw-code-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre role="tabpanel" data-lenis-prevent><code>{tab.code}</code></pre>
    </div>
  );
}
