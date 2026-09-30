import type { Metadata } from "next";
import Link from "next/link";
import CodeBlock from "@/components/CodeBlock";
import PublicNav from "@/components/PublicNav";

export const metadata: Metadata = {
  title: "Docs — MemoryWorks",
  description:
    "Run MemoryWorks end to end: local setup, production deploy, authentication, model providers, the agent API, Python SDK, CLI, and MCP server.",
};

const sections = [
  ["quickstart", "Quickstart"],
  ["production", "Run in production"],
  ["authentication", "Authentication"],
  ["models", "Model providers"],
  ["connectors", "Connectors"],
  ["org-api", "Agent API"],
  ["core-api", "Core API"],
  ["python-sdk", "Python SDK"],
  ["cli", "CLI"],
  ["swarm", "Context swarm"],
  ["envelopes", "Context envelopes"],
  ["mcp", "MCP server"],
  ["testing", "Testing and CI"],
  ["security", "Security"],
] as const;

const quickstartDocker = `# 1. Configure once — every provider key is optional.
cp .env.example .env

# 2. Start everything: ArcadeDB, API, workspace, MCP server.
make memoryworks

# API       http://localhost:8000   (docs at /docs, health at /api/health)
# Workspace http://localhost:3000
# MCP       http://localhost:8001   (streamable HTTP)`;

const quickstartNative = `# No Docker? Run the two surfaces natively and use the in-process graph.
export GRAPH_BACKEND=memory

make backend    # uvicorn app.main:app --reload --port 8000
make frontend   # next dev on port 3000

# Optional: seed a sample organization to explore:
curl -X POST http://localhost:8000/api/org/scenario/seed \\
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
  -d '{"reset": false}'`;

const quickstartDemo = `# Load or reset a realistic demo organization inside Docker:
make demo     # load demo data
make reset    # wipe back to a clean workspace`;

const vercelDeploy = `# One repo, two services (vercel.json at the root):
#   frontend → Next.js app (root: frontend/)
#   backend  → FastAPI container (Dockerfile.vercel)

vercel link
vercel env add JWT_SECRET production          # required — signs sessions
vercel env add OPENROUTER_API_KEY production  # model for live agent sessions

# Canonical origin — every OAuth callback is derived from it:
vercel env add PUBLIC_BASE_URL production     # https://memoryworks.app
vercel env add FRONTEND_URL production        # https://memoryworks.app
vercel env add GRAPH_BACKEND production       # memory

# Vercel containers are stateless: SQLite and the in-memory graph reset when a
# new container starts. For durable data, use the single-VM deploy (deploy/oci).
# PUBLIC_DEMO_MODE=true is a stricter profile for a shared, disposable demo.

# Real sign-in providers (values from GitHub / Google consoles):
vercel env add GITHUB_CLIENT_ID production
vercel env add GITHUB_CLIENT_SECRET production
vercel env add GOOGLE_CLIENT_ID production
vercel env add GOOGLE_CLIENT_SECRET production

vercel --prod`;

const oauthCallbacks = `# GitHub OAuth app → Authorization callback URL (one per app)
https://memoryworks.app/api/auth/github/callback

# Google Cloud → Credentials → OAuth client → Authorized redirect URIs
https://memoryworks.app/api/auth/google/callback

# Locally the host is derived from the request, so the defaults just work:
#   http://localhost:8000/api/auth/github/callback
#   http://localhost:8000/api/auth/google/callback`;

const emailAuth = `# Passwordless email codes (people sign in without an OAuth provider)
EMAIL_AUTH_ENABLED=true
EMAIL_FROM=memory@company.com
SMTP_HOST=smtp.company.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...`;

const apiKeyExample = `# Create a workspace-scoped key in the UI (Sources → API keys),
# or with the endpoint:
curl -X POST "$ORGMEMORY_API_URL/api/keys" \\
  -H "Authorization: Bearer $SESSION_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "ci-agent"}'

# Agents then authenticate as a bearer token:
export ORGMEMORY_API_URL=https://your-host
export ORGMEMORY_API_KEY=om_live_...`;

const modelConfig = `OPENROUTER_API_KEY=...          # GLM (default)
GLM_MODEL=z-ai/glm-5.3-flash
GLM_BASE_URL=https://openrouter.ai/api/v1

OPENAI_API_KEY=...              # GPT
ANTHROPIC_API_KEY=...           # Claude
GOOGLE_API_KEY=...              # Gemini
XAI_API_KEY=...                 # Grok
KIMI_API_KEY=...                # Kimi

ORG_MEMORY_DEFAULT_MODEL_PROVIDER=glm

# With no model key at all, every agent run falls back to a deterministic
# policy that still calls the real tools — the product never hard-fails.`;

const agentStream = `# Agent mode in the chat streams one real, model-driven session. Every step
# is a tool call against the workspace; changes stop at a plan for a person.

curl -N -X POST "https://your-host/api/org/ask/stream" \\
  -H "Content-Type: application/json" -b "session cookie" \\
  -d '{"question": "Are we ready to launch?", "space_ids": []}'

# One NDJSON event per line:
{"type":"start","id":"agtsess_…","model":"glm"}
{"type":"step","step":{"tool":"get_orgmemory_readiness","thought":"…","summary":"NOT READY — 1 blocker."}}
{"type":"done","session":{…,"answer":"…","proposal":{"status":"pending_approval"}}}`;

const orgApiRows = [
  ["GET", "/api/org/spaces", "List the spaces (projects) the caller can see."],
  ["GET", "/api/org/context", "Cross-space state: decisions, open work, blockers, next best action."],
  ["GET", "/api/org/readiness", "Launch checklist computed from the dependency graph."],
  ["GET", "/api/org/blockers", "Root causes of a stall only — not every open item."],
  ["GET", "/api/org/conflicts", "Tracked state vs. newer contradicting records, with a drafted resolution."],
  ["GET", "/api/org/reasoning-chain", "The recorded chain behind a decision, walked by graph edges."],
  ["GET", "/api/org/provenance/:id", "Sources and relationships behind one memory."],
  ["GET", "/api/org/dependency-graph", "Work items and REQUIRED_FOR edges across spaces."],
  ["POST", "/api/org/ask/stream", "Run a model-driven agent session; NDJSON steps stream back."],
  ["POST", "/api/org/followups", "Draft the next questions from what a session found."],
  ["POST", "/api/org/plans", "Propose changes. Nothing applies until a person approves."],
  ["POST", "/api/org/plans/:id/approve", "Human approval — the only write path to apply."],
  ["POST", "/api/org/watches", "Standing checks (blockers, conflicts, staleness) on an interval."],
  ["POST", "/api/org/scenario/seed", "Load the seven-space demo organization."],
] as const;

const coreApiRows = [
  ["GET", "/api/health", "Check API and dependency health."],
  ["GET", "/api/auth/me", "The signed-in principal and workspaces."],
  ["GET", "/api/projects", "List projects visible to the caller."],
  ["POST", "/api/projects", "Create a project memory boundary."],
  ["POST", "/api/ingest/upload", "Add a document or source payload."],
  ["POST", "/api/ask", "Compile evidence and answer a question."],
  ["GET", "/api/models", "List GLM, GPT, Claude, Gemini, Grok, and Kimi readiness."],
  ["GET", "/api/keys", "List workspace API keys."],
  ["POST", "/api/keys", "Issue a workspace-scoped agent key."],
  ["GET", "/api/connectors/catalog", "List live and planned company-memory sources."],
  ["GET", "/api/memory/units", "Inspect retrievable memory units."],
  ["GET", "/api/memory/graph/summary", "Inspect the organizational graph."],
  ["GET", "/api/memory/context/:id", "Read a compiled context envelope."],
  ["GET", "/api/memory/swarm/:runId", "Inspect a context-swarm trace."],
] as const;

const pythonExample = `from orgmemory import MemoryWorks

memory = MemoryWorks(
    base_url="http://localhost:8000",
    api_key="om_live_...",
)

context = memory.ask(
    project_id="prj_platform",
    query="What changed in checkout, and why?",
)

print(context.answer)
print(context.compiled_context)

# Pass the source-backed context to any model or agent.
agent.run(context.compiled_context)`;

const cliExample = `export ORGMEMORY_API_URL=http://localhost:8000
export ORGMEMORY_API_KEY=om_live_...

orgmemory health
orgmemory projects
orgmemory project-create --name "Checkout platform"
orgmemory ask prj_platform "What changed in checkout, and why?"
orgmemory memories prj_platform
orgmemory graph prj_platform
orgmemory swarm swarm_01J...`;

const ingestExample = `orgmemory ingest prj_platform \\
  --file ./incident-review.md \\
  --source-type doc \\
  --title "Checkout incident review"

# Or send inline content
orgmemory ingest prj_platform \\
  --content "Release 48 moved checkout to the new ledger." \\
  --source-type other`;

const mcpConfig = `# Claude Desktop / any MCP client (stdio):
{
  "mcpServers": {
    "memoryworks": {
      "command": "mcp_server/.venv/bin/python",
      "args": ["mcp_server/server.py", "--transport", "stdio"],
      "env": { "MEMORYWORKS_API_URL": "http://localhost:8000",
               "MEMORYWORKS_API_KEY": "om_live_..." }
    }
  }
}`;

const testSuite = `make test   # backend pytest · frontend node:test · SDK pytest
make lint   # ruff · black · tsc
make ci     # test + lint + next build`;

const coreCurl = `curl -X POST "https://your-host/api/ask" \\
  -H "Authorization: Bearer $ORGMEMORY_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "project_id": "prj_platform",
    "query": "What changed in checkout, and why?",
    "token_budget": 8000
  }'`;

const swarmResponse = `{
  "run_id": "swarm_01J...",
  "status": "completed",
  "specialists": {
    "activation": "ranked candidate memories",
    "graph": "traversed related entities and decisions",
    "temporal": "resolved what was true at query time"
  },
  "compiler": "deduplicated, scoped context with evidence"
}`;

export default function DocsPage() {
  return (
    <main className="brain-site om-site docs-site">
      <PublicNav compact />

      <div className="docs-layout">
        <aside className="docs-sidebar" aria-label="Documentation sections">
          <p>Documentation</p>
          <nav>
            {sections.map(([id, label]) => (
              <a key={id} href={`#${id}`}>
                {label}
              </a>
            ))}
          </nav>
          <div className="docs-sidebar__signal">
            <span aria-hidden="true" />
            Local-first quickstart
          </div>
        </aside>

        <article className="docs-content">
          <header className="docs-hero">
            <div className="om-eyebrow">
              <span />
              Developer documentation
            </div>
            <h1>
              Give your agents
              <br />
              <em>institutional memory.</em>
            </h1>
            <p>
              Connect company knowledge, retrieve the right evidence, and compile
              it into a context envelope your agents can trust — from the
              browser, the terminal, or any agent runtime.
            </p>
            <div className="docs-hero__actions">
              <a className="om-button" href="#quickstart">
                Start building
                <span aria-hidden="true">↘</span>
              </a>
              <Link className="om-button ghost" href="/workspace">
                Open workspace
              </Link>
            </div>
          </header>

          <section id="quickstart" className="docs-section">
            <div className="docs-kicker">01 / Quickstart</div>
            <h2>From zero to source-backed context.</h2>
            <p className="docs-lede">
              Everything runs from one repository. Docker is the default path;
              both surfaces also run natively.
            </p>
            <CodeBlock label="Docker (recommended)" language="bash">
              {quickstartDocker}
            </CodeBlock>
            <CodeBlock label="Native, without Docker" language="bash">
              {quickstartNative}
            </CodeBlock>
            <CodeBlock label="Demo data" language="bash">
              {quickstartDemo}
            </CodeBlock>
            <div className="docs-note">
              <strong>Default services</strong>
              <span>
                API at <code>http://localhost:8000</code> · Workspace at{" "}
                <code>http://localhost:3000</code> · Graph check via{" "}
                <code>make graph-check</code>
              </span>
            </div>
            <div className="docs-note">
              <strong>First sign-in</strong>
              <span>
                Open <code>http://localhost:3000/login</code>. With{" "}
                <code>AUTH_DEV_MODE=true</code> (the default in{" "}
                <code>.env.example</code>) a local development login is offered;
                GitHub, Google, and email sign-in activate as soon as their
                credentials below are set.
              </span>
            </div>
          </section>

          <section id="production" className="docs-section">
            <div className="docs-kicker">02 / Run in production</div>
            <h2>Same repo, two services.</h2>
            <p>
              The frontend is a Next.js app; the backend is a FastAPI container.
              <code>vercel.json</code> wires <code>/</code> to the frontend and{" "}
              <code>/api/*</code> to the backend, so cookies stay first-party.
            </p>
            <CodeBlock label="Vercel (CLI or Git integration)" language="bash">
              {vercelDeploy}
            </CodeBlock>
            <div className="docs-note">
              <strong>Required on the backend service</strong>
              <span>
                <code>JWT_SECRET</code>, a model key (<code>OPENROUTER_API_KEY</code>{" "}
                at minimum), and the OAuth credentials if sign-in buttons should
                be live. Environment changes apply only after a redeploy.
              </span>
            </div>
            <div className="docs-note">
              <strong>Self-hosted alternative</strong>
              <span>
                <code>deploy/oci</code> and <code>compose.production.yml</code>{" "}
                run the same stack as durable containers with persistent
                volumes — that profile keeps ArcadeDB-backed graph storage and
                long-running watches.
              </span>
            </div>
          </section>

          <section id="authentication" className="docs-section">
            <div className="docs-kicker">03 / Authentication</div>
            <h2>Work identity for people. Scoped keys for agents.</h2>
            <p>
              People sign in with GitHub, Google, or a passwordless email code.
              GitHub sign-in also requests repository access and stores it as
              that person&apos;s GitHub connection, so the next step is choosing
              repositories — not connecting GitHub again. SDK, CLI, MCP, and
              server-side agents use a workspace-scoped API key. The browser session is an HttpOnly cookie; SDK and CLI use
              bearer tokens. Nothing is stored in the browser.
            </p>
            <h3 className="docs-sub">Register the OAuth callbacks</h3>
            <CodeBlock label="Provider setup" language="bash">
              {oauthCallbacks}
            </CodeBlock>
            <p>
              Redirect URIs are derived from the request host automatically, so
              a deployment needs no baked-in domain. Setting{" "}
              <code>PUBLIC_BASE_URL</code> (non-local) pins them explicitly and
              wins over derivation. Add both credentials to{" "}
              <code>.env</code> or your Vercel environment, then restart or
              redeploy — the buttons on <code>/login</code> activate as soon as{" "}
              <code>GET /api/auth/providers</code> reports them configured.
            </p>
            <CodeBlock label="Browser sign-in providers" language="bash">
              {`GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
GITHUB_REDIRECT_URI=http://localhost:8000/api/auth/github/callback

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/api/auth/google/callback`}
            </CodeBlock>
            <CodeBlock label="Passwordless email in production" language="bash">
              {emailAuth}
            </CodeBlock>
            <div className="docs-note">
              <strong>Agents and automation</strong>
              <span>
                Workspace-scoped API keys authenticate CLIs, SDKs, MCP clients,
                and server-side agents. Create one under{" "}
                <code>POST /api/keys</code> and pass it as a bearer token.
              </span>
            </div>
            <CodeBlock label="Agent configuration" language="bash">
              {apiKeyExample}
            </CodeBlock>
          </section>

          <section id="models" className="docs-section">
            <div className="docs-kicker">04 / Model providers</div>
            <h2>One company context. Your choice of model.</h2>
            <p>
              MemoryWorks retrieves, scopes, and compiles the evidence before a
              model sees it. Configure any combination of providers; the
              default answers free-text questions and drives the agent loop
              that chooses tools step by step.
            </p>
            <CodeBlock label="Model configuration" language="bash">
              {modelConfig}
            </CodeBlock>
          </section>

          <section id="connectors" className="docs-section">
            <div className="docs-kicker">05 / Connectors</div>
            <h2>Make capability status explicit.</h2>
            <p>
              GitHub, Slack, uploads, the API, Python SDK, CLI, and MCP are
              live. Google Workspace, Gmail, Microsoft 365, Teams, Outlook, and
              Atlassian are the next adapter layer. The catalog endpoint
              exposes the same status shown in the workspace, so a planned
              integration is never presented as connected.
            </p>
            <div className="docs-object-grid">
              <div><code>live</code><span>Real authorization and ingestion or delivery path.</span></div>
              <div><code>next</code><span>Prioritized adapter; visible, but no fake connect action.</span></div>
              <div><code>planned</code><span>Roadmap source with a defined memory contract.</span></div>
              <div><code>source_and_channel</code><span>Can ingest a conversation and return an approved reply.</span></div>
            </div>
          </section>

          <section id="org-api" className="docs-section">
            <div className="docs-kicker">06 / Agent API</div>
            <h2>The cross-space operations Agent mode runs on.</h2>
            <p>
              Every agent step maps to one HTTP route under <code>/api/org</code>.
              Reads execute immediately. Writes only ever create a plan that
              waits for a person — there is deliberately no route an agent can
              use to approve its own change.
            </p>
            <div className="docs-api-table">
              {orgApiRows.map(([method, path, description]) => (
                <div className="docs-api-row" key={`${method}-${path}`}>
                  <code className={`docs-method docs-method--${method.toLowerCase()}`}>
                    {method}
                  </code>
                  <code>{path}</code>
                  <span>{description}</span>
                </div>
              ))}
            </div>
            <div className="docs-note">
              <strong>Streaming protocol</strong>
              <span>
                <code>/api/org/ask/stream</code> answers a free-text question by
                letting the model choose tools one at a time and returns NDJSON
                events: <code>start</code>, <code>step</code> (tool, arguments,
                thought, summary), <code>ping</code> keepalives, then{" "}
                <code>done</code> or <code>error</code>. A proposal ends the
                run — approving it is always a person&apos;s action.
              </span>
            </div>
            <CodeBlock label="Streaming agent session" language="bash">
              {agentStream}
            </CodeBlock>
          </section>

          <section id="core-api" className="docs-section">
            <div className="docs-kicker">07 / Core API</div>
            <h2>A small surface for a large memory.</h2>
            <CodeBlock label="Ask with HTTP" language="bash">
              {coreCurl}
            </CodeBlock>
            <div className="docs-api-table">
              {coreApiRows.map(([method, path, description]) => (
                <div className="docs-api-row" key={`${method}-${path}`}>
                  <code className={`docs-method docs-method--${method.toLowerCase()}`}>
                    {method}
                  </code>
                  <code>{path}</code>
                  <span>{description}</span>
                </div>
              ))}
            </div>
          </section>

          <section id="python-sdk" className="docs-section">
            <div className="docs-kicker">08 / Python SDK</div>
            <h2>Context assembly, typed in Python.</h2>
            <p>
              Install from this repository. The synchronous and asynchronous
              clients expose the same MemoryWorks primitives.
            </p>
            <CodeBlock label="Install" language="bash">
              {`python -m pip install -e ./python_sdk
# or via the Makefile: make sdk-install`}
            </CodeBlock>
            <CodeBlock label="agent.py" language="python">
              {pythonExample}
            </CodeBlock>
          </section>

          <section id="cli" className="docs-section">
            <div className="docs-kicker">09 / CLI</div>
            <h2>Inspect memory from your terminal.</h2>
            <p>
              The CLI ships with the Python SDK: query projects, ingest
              sources, inspect graph state, and review context-swarm runs.
            </p>
            <CodeBlock label="Query" language="bash">
              {cliExample}
            </CodeBlock>
            <CodeBlock label="Ingest" language="bash">
              {ingestExample}
            </CodeBlock>
          </section>

          <section id="swarm" className="docs-section docs-section--swarm">
            <div className="docs-kicker">10 / Context swarm</div>
            <h2>Specialists forage. One compiler decides.</h2>
            <p>
              A query activates a small ecosystem of retrieval specialists.
              Each searches a different memory surface — semantic candidates,
              graph relationships, and temporal state. A compiler agent
              deduplicates their findings, applies scope, and emits one bounded
              context envelope.
            </p>
            <div className="docs-flow" aria-label="Context swarm pipeline">
              <span>Query</span>
              <i>→</i>
              <span>Activation</span>
              <span>Graph</span>
              <span>Temporal</span>
              <i>→</i>
              <span className="docs-flow__final">Compiler</span>
            </div>
            <CodeBlock label="Swarm trace" language="json">
              {swarmResponse}
            </CodeBlock>
          </section>

          <section id="envelopes" className="docs-section">
            <div className="docs-kicker">11 / Context envelopes</div>
            <h2>The answer is not the artifact.</h2>
            <p>
              Every ask returns an answer plus its compiled context, evidence,
              retrieval diagnostics, and a durable context-envelope identifier.
              Your agent consumes the context while your product keeps the
              provenance.
            </p>
            <div className="docs-object-grid">
              <div>
                <code>answer</code>
                <span>Grounded synthesis for the current query.</span>
              </div>
              <div>
                <code>compiled_context</code>
                <span>Bounded context ready for a model call.</span>
              </div>
              <div>
                <code>evidence</code>
                <span>Source references and relevance metadata.</span>
              </div>
              <div>
                <code>context_envelope.id</code>
                <span>Durable handle for replay and inspection.</span>
              </div>
            </div>
          </section>

          <section id="mcp" className="docs-section">
            <div className="docs-kicker">12 / MCP server</div>
            <h2>Memory for tool-using agents outside the browser.</h2>
            <p>
              The same memory surface is exposed over MCP for assistants that
              are not browser-native: stdio for local clients, streamable HTTP
              for remote ones, with MCP OAuth for client registration.
            </p>
            <CodeBlock label="Run the server" language="bash">
              {`make mcp        # stdio transport
make mcp-http   # streamable HTTP on :8001`}
            </CodeBlock>
            <CodeBlock label="Client configuration" language="json">
              {mcpConfig}
            </CodeBlock>
          </section>

          <section id="testing" className="docs-section">
            <div className="docs-kicker">13 / Testing and CI</div>
            <h2>Every behavior above is under test.</h2>
            <p>
              The suite covers the org tools, the approval boundary, the agent
              runner with scripted models (no network), OAuth round trips,
              browser tool registration, and the SDK contract.
            </p>
            <CodeBlock label="Quality gates" language="bash">
              {`make test    # backend pytest + frontend tests + SDK tests
make lint    # ruff + black + tsc --noEmit
make ci      # test + lint + production build
make reset   # reset local data to a clean slate`}
            </CodeBlock>
          </section>

          <section id="security" className="docs-section">
            <div className="docs-kicker">14 / Security</div>
            <h2>Retrieval respects the caller.</h2>
            <p>
              Project boundaries, bearer authentication, and source metadata
              travel through retrieval. Keep API keys server-side, use a
              separate key per environment, and put the API behind TLS outside
              local development. Browser agents never receive credentials —
              they borrow the page&apos;s session, inside its permission
              boundary.
            </p>
            <div className="docs-checks">
              <span>✓ Scope before synthesis</span>
              <span>✓ Evidence on every answer</span>
              <span>✓ Writes stop at a person</span>
              <span>✓ Inspectable swarm traces</span>
              <span>✓ Durable context envelopes</span>
            </div>
          </section>

          <footer className="docs-footer">
            <span>Ready to wire memory into your agent?</span>
            <a href="#quickstart">Back to quickstart ↑</a>
          </footer>
        </article>

        <aside className="docs-toc" aria-label="On this page">
          <p>On this page</p>
          {sections.slice(0, 8).map(([id, label]) => (
            <a key={id} href={`#${id}`}>
              {label}
            </a>
          ))}
        </aside>
      </div>
    </main>
  );
}
