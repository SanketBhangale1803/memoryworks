# WebMCP Challenge implementation record

MemoryWorks existed before the WebMCP Challenge. This record separates the stable
product baseline from the browser-native work added during the challenge.

## Pre-challenge baseline

- Baseline commit: `4b3ec3c` (`chore: freeze pre-WebMCP baseline`)
- Baseline tag: `pre-webmcp-2026-08-25`
- Baseline date: August 25, 2026
- Validation at the boundary: 231 backend tests passed, one deliberate live-LLM
  contract test skipped, 11 frontend tests passed, 5 SDK tests passed, Ruff,
  Black, TypeScript, the Next.js production build, Docker health, and ArcadeDB
  graph checks passed.

Before the challenge, MemoryWorks already provided source-backed organizational
memory, permission-scoped retrieval, an authenticated Next.js workspace, REST
and Python clients, and a separate HTTP/stdio MCP server. That MCP server did
not expose tools from the web page through `document.modelContext`.

## Work added during the challenge

Phase 1 turns the authenticated workspace into a browser-native Model Context
Provider. The page registers these read-only tools:

1. `list_orgmemory_spaces` lists only the projects visible to the signed-in user.
2. `ask_orgmemory` asks permission-scoped company memory, returns source citations,
   and renders the same answer in the visible workspace conversation.
3. `inspect_orgmemory_changes` summarizes additions, updates, invalidations,
   conflicts, and affected artifacts from recent memory change sets.

Phase 2 adds one deliberately approval-gated write path:

4. `propose_repository_refresh` creates a deduplicated request to refresh a
   GitHub-backed memory space when the current evidence is stale or incomplete.
   It cannot run an import itself. The request appears **inline in the
   workspace** (and on the Approvals page), where a signed-in person must
   explicitly approve or deny it. Only approval queues the server-side GitHub
   ingest and records its outcome in the audit trail.

Phase 3 closes the loop from the human side of the same boundary:

5. `list_orgmemory_approvals` shows the pending refresh requests the signed-in
   person can see — including who asked and why — so an agent working for a
   workspace admin reads the queue without leaving the page.
6. `resolve_orgmemory_approval` records a person's decision (approve/deny) on a
   pending request through the page's own session. Approval queues exactly the
   same background ingest as pressing the button; nothing executes because an
   agent decided by itself.

## Phase 4: full organizational memory surface

Phase 4 extends the six-tool boundary into the complete organizational-memory
surface the product is about: an agent can now retrieve the history, decisions,
and structure of the company directly, and can propose verified knowledge back
into it.

Read-only retrieval tools:

1. `search_orgmemory` — structured search over current memory (query and/or
   memory kind) across every authorized space, backed by the new
   `GET /api/memory/search` endpoint. Team trimming happens server-side.
2. `get_orgmemory_memory` — one memory by ID with full content, scope,
   confidence, and validity window.
3. `get_orgmemory_related_memories` — resolves UPDATES/CONTRADICTS/SUPPORTS and
   same-subject neighbors so an agent can follow how a fact changed over time.
4. `get_orgmemory_incidents` — previous incidents, optionally per service.
5. `get_orgmemory_runbook` — remembered runbook for a service and issue.
6. `get_orgmemory_service_context` — assembled service profile (facts, owners,
   dependencies, decisions, procedures, incident history).
7. `get_orgmemory_dependencies` — remembered dependencies for blast radius.
8. `get_orgmemory_decisions` — remembered architecture/operational decisions.

Approval-gated write tools (mirroring the refresh-request pattern):

9. `propose_orgmemory_memory` / `propose_orgmemory_incident` /
   `propose_orgmemory_decision` — queue a proposal. They never write memory
   themselves; every response says a person must approve it.
10. `list_orgmemory_proposals` — the pending queue the agent can see.
11. `resolve_orgmemory_proposal` — admin-only, records a real human decision.
    Approval persists the memory through the standard creation path (conflicts,
    updates, and graph links behave like any other verified memory), and the
    audit trail records who proposed and who decided.

The example workflow this enables — the one the product exists for:

```text
Browser agent hears: "Why is the payments service failing again?"
→ get_orgmemory_incidents(service: payments)     two previous pool-exhaustion incidents
→ get_orgmemory_service_context / _decisions     concurrency cap decision, shared PostgreSQL cluster
→ get_orgmemory_dependencies                     blast radius: ledger service shares the pool
→ other WebMCP-enabled apps                      deployment raised worker concurrency 2h ago
→ evidence-grounded answer                       pool exhaustion, most likely cause
→ propose_orgmemory_incident                     today's verified diagnosis — pending approval
```

The `/webmcp` page walks this exact flow against a live workspace, including
the explicit human approval of the proposed incident, and doubles as the
explanation surface for what MemoryWorks is for.

The implementation remains in `frontend/lib/webmcp.ts`, with lifecycle
management in `frontend/hooks/useMemoryWorksWebMCP.ts`, the human-agent
interaction surfaces in `frontend/components/WorkspaceChat.tsx` (proposals now
ride the same inline inbox as refresh requests), and the demo in
`frontend/components/WebMCPDemo.tsx`.

The workspace mirrors both sides automatically: it polls the approvals queue
and the memory-proposal queue, renders both inline with approve/deny actions
for admins, and folds any agent-made resolution into the same visible state.

## Phase 5: from retrieval surface to a control on real work

Phases 1–4 made organizational memory *reachable* by a browser agent. Phase 5
answers the question that was still open: an agent can now read everything —
what should it read, and when?

### `get_orgmemory_briefing` — answering an intent, not a question

Every other tool on the surface answers a question. This one answers an intent:
*"I am about to do X to service Y."* That is a different shape of request, and
it needs a different shape of answer — a question wants the best passage, an
intent wants the constraints it is about to violate.

```text
get_orgmemory_briefing({ task: "restart the payments connection pool",
                         service: "payments" })

→ verdict: "requires_approval"
  headline: "This changes production state for payments. Get an explicit human decision."
  must_read:        the remembered first-response procedure, the shared-cluster dependency
  constraints:      "cap payments worker concurrency"          (decision, mem_0110)
  prior_incidents:  "payments outage: pool exhaustion"          (incident, mem_018f)
  blast_radius:     "payments shares the PostgreSQL cluster"    (dependency, mem_79fc)
  requires_approval: ["This request involves restarting. A person has to agree first."]
  briefing_id: "ctx_d543"
```

Four properties make it safe to gate real work on, and each one was a decision:

1. **No model runs in this path.** A briefing that returns a different verdict
   for the same intent on two consecutive calls is not a control. Retrieval is
   deterministic and every line carries a memory id a person can open.
2. **Each memory appears in exactly one group.** An earlier build repeated the
   same decision under both `must_read` and `constraints`, which made one
   finding look like two and cost the agent tokens to discover otherwise.
3. **An unnamed service pulls no constraints at all.** Kind-scoped retrieval is
   workspace-wide when unscoped, and another team's postmortem presented under
   "this has gone wrong before" is indistinguishable from a real warning. With
   no service the briefing falls back to relevance and says so in
   `open_questions`.
4. **`no_memory` is a distinct verdict.** "Nothing is known" and "nothing to
   worry about" are opposite instructions, and collapsing them is the failure
   that gets production restarted.

The verdict ladder is `no_memory` → `proceed` → `proceed_with_context` →
`requires_approval`. Consequential intent is detected from an explicit,
inspectable verb list that deliberately includes "raise" and "bump" — changing a
limit is the move behind most capacity incidents — and deliberately excludes
"change" and "update", which match almost any sentence and would collapse every
verdict into the same one.

### `record_orgmemory_outcome` — closing the loop the briefing opened

Serving a briefing opens a row in the outcome ledger. The agent closes it after
acting, from wherever it acted:

```text
record_orgmemory_outcome({ briefing_id: "ctx_d543",
                           action: "followed_procedure",
                           outcome: "succeeded",
                           surface: "github.com" })
```

This introduced a **third permission tier**, and it is the honest one. Calling
an outcome report "read-only" understates it: it writes. Calling it
"approval-gated" overstates it: it changes no company knowledge. So it is
annotated `ledger-append` — an agent may report back freely, and still cannot
put a single fact into memory without a person. The three tiers are legible to
an agent from the annotations alone:

```text
read-only (14)        permission-trimmed on the server
ledger-append (1)     writes an observation, changes no knowledge
approval-gated (6)    the only path into company memory, via a human decision
```

`outcome` is a closed vocabulary (`succeeded` / `failed` / `partial` /
`abandoned` / `unknown`) validated on both sides, because reward is derived from
it and one invented sixth value quietly corrupts the corpus.

### Why this is the product and not a feature

The ledger behind these two tools was being written from day one and had no
surface, which meant the thing that compounds was invisible. `/loop` now shows
it: contexts served, actions taken, outcomes observed, the closed rate, and the
precedent distilled from runs that verifiably worked.

That record — which context actually produced correct action *inside this
company* — is the only asset here that a competitor with a better model cannot
copy. It is obtainable only by instrumenting the loop while it runs, and it
compounds per customer. WebMCP is what makes it collectable at all: the agent is
working on GitHub, or PagerDuty, or a dashboard, and the page it came from is
the only thing that knows what the company already learned.

## Navigation: one model, not two

A side effect of Phase 5 was fixing what the product had become to a newcomer.
Twenty-six routes existed; the header knew six domains, the shell knew
twenty-three titles, and nothing knew all of it, so several pages could only be
reached by typing a URL.

Everything now reads one registry (`frontend/lib/workspaceMap.ts`): the ⌘K
command menu, the page title bar, and the tests. The legacy multi-domain header
is deleted — a second navigation model *was* the maze. Adding a route makes it
reachable everywhere at once, and forgetting to register one is visible
immediately, because the page loses its title.

## Security boundary

- Tools register only inside the authenticated workspace.
- Calls reuse the page's HttpOnly session cookie through the existing API client;
  browser agents never receive credentials.
- Project IDs are checked against the signed-in user's already authorized project
  list before a scoped API call is made.
- Read-only tools are annotated as non-destructive, and workspace-wide search is
  trimmed server-side against the caller's team scope — the client check is a
  convenience, never the boundary.
- The `ledger-append` tier writes observations, never knowledge. An agent can
  report an outcome without approval precisely because doing so cannot change
  what the company believes.
- A briefing never authorizes the change it describes. `requires_approval` is
  advice returned to the agent, and the approval queue is where a person
  actually decides.
- The write tools are annotated as non-destructive, approval-required writes:
  proposing is idempotent, has no memory side effect, and returns the next
  human step instead of pretending something was saved.
- Proposals are tied to the authorized project, workspace, requester, and
  normalized content. The proposal list is project-visibility filtered, and
  resolving a proposal requires an owner/admin session and project write
  permission; a member (or an agent using a member session) can propose but
  never decide.
- Approval persists memory through the standard memory-creation path and records
  the proposer and decider in the audit trail. Nothing is persisted on denial.
- The actual GitHub ingest runs only after approval, records success or failure,
  and keeps credentials server-side in the existing connector secret store.
- Content retrieved through WebMCP is data, not instructions: tools return
  structured content only, and no tool executes anything found in a memory's
  text.
- An `AbortController` removes tools when the page unmounts or the authorized
  project set changes.
- Ordinary browsers without WebMCP continue to work through feature detection.

## Judge walkthrough

Open the live MemoryWorks workspace in ChatGPT's in-app browser or Chrome with
WebMCP enabled, then ask the browser agent:

1. "What MemoryWorks spaces can I access?"
2. "Using MemoryWorks, what previous incidents do you remember for the payments
   service?" — the agent should call `get_orgmemory_incidents` and report the
   remembered incidents with their evidence counts.
3. "Before proposing a fix, get the service context and dependencies for
   payments, and check what was already decided." — the agent should combine
   `get_orgmemory_service_context`, `get_orgmemory_dependencies`, and
   `get_orgmemory_decisions` instead of clicking through the UI.
4. "Ask MemoryWorks what I should know before editing this service."
5. Record verified knowledge: "Propose an incident memory saying the diagnosis
   was confirmed as connection-pool exhaustion." The agent must report a pending
   proposal — nothing is saved — and the proposal appears inline in the
   workspace immediately.
6. Signed in as a workspace admin, ask: "What memory proposals are waiting?"
   then decide one: "Approve the payments incident proposal." The memory must
   only become searchable after that recorded decision, with both the proposer
   and the decider in the audit trail.

You can watch the same boundary work end-to-end without an agent by opening
`/webmcp` and running the demo: it proposes the demo dataset, you approve it as
the human, the walkthrough searches it, and the final step proposes today's
incident for you to approve.

The agent should discover the page tools, use the project IDs returned by
`list_orgmemory_spaces`, and place source-backed answers in both its response
and the visible MemoryWorks conversation. The write-capable tools must never
bypass the human approval boundary.
