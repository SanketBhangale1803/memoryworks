# MemoryWorks — 3-minute demo script (production)

For the OpenAI WebMCP Challenge submission, recorded against the live
deployment at `https://orgmemory.vercel.app`. Judged on WebMCP Leverage,
Execution, Potential Impact, and Creativity & Ambition — equally weighted.
Timings assume ~150 wpm; narration is already cut to fit.

The story in one line: **a real person signs in, an agent reads the company's
memory, proposes a fix, and a person approves it — every step visible, nothing
applied without a human.**

---

## Before you hit record

1. **Fresh session.** Sign out, then sign in with GitHub once. If it's your
   first login on the production workspace, click **Load the scenario** in the
   `/webmcp` console *before* recording so beat 3 starts warm. The scenario
   needs to be NOT READY (security task open) — if you already reconciled it
   in an earlier take, click **Reset** (or re-seed with `{"reset": true}`) so
   the conflict is back.
2. **Warm every page** you'll show: `/`, `/login`, `/workspace`, `/webmcp`.
   Nothing may compile or load on camera.
3. **Browser:** 1440×900, bookmarks bar hidden, only one window. Zoom the
   system UI one notch if the console text reads small on your screen.
4. **Record** with QuickTime (File → New Screen Recording) or Screen Studio
   for auto-zoom. Record the whole take twice; pick the calmer one.
5. **Mic:** the narration matters more than the pixels. Record audio in a
   quiet room; re-read any line you stumble on rather than restarting.

Contingencies:
- If a model step is slow, **don't talk over it** — the live "running" state
  with the model's thought on screen *is* the demo. Read the thought aloud.
- If the provider rate-limits, the console says so honestly and the guided
  fallback still runs the same real tools. If it happens on camera, say:
  "even without a model, the tool loop is real — watch."

---

## 0:00–0:20 · The problem (landing page)

**Screen:** `/` — hold on the hero two seconds, then a slow scroll past the
platform cards.

> "Every engineering org already knows why its payments service failed last
> time. That knowledge is in a postmortem nobody reads, a Slack thread nobody
> can find, and one engineer's head. So when an AI agent shows up to change
> something, it starts from zero — and repeats the outage you already had.
>
> MemoryWorks is the memory layer for engineering organizations — and through
> WebMCP, the browser hands that memory to agents before they act."

**Land on:** the tool counter — 21 tools, 14 read-only, 6 human-governed.

---

## 0:20–0:40 · Real sign-in (production)

**Screen:** `/login` — click **Continue with GitHub**, let the OAuth round
trip play, land in `/workspace`.

> "This is the deployed product, not a localhost. Real sign-in with my real
> GitHub identity — my source permissions are the boundary. And my session
> cookie never leaves the browser: when a browser agent connects, it borrows
> this page's authenticated session inside its permission boundary. Agents
> never receive credentials."

**Land on:** your name and role in the workspace.

---

## 0:40–1:00 · The console registers itself as a tool provider

**Screen:** `/webmcp` — point at the header badge, then the right rail.

> "This page is itself a Model Context Provider. It registered sixteen
> organizational tools on document.modelContext — the same handlers the UI
> calls, no parallel demo path. Read tools run immediately. Write tools are
> approval-gated. And approving is *not* a tool — that's a person, in the
> workspace. That asymmetry is deliberate."

**Land on:** the WebMCP surface card — Read / Write / **Approve: no tool. A
person only.**

---

## 1:00–1:30 · A question runs real tools, live

**Screen:** click the **Catch me up** suggestion. Let the tool calls land one
at a time — read one thought and one summary out loud. Then let the briefing
render.

> "Every row is a real call: the model chose it, the thought above it is the
> model's, the milliseconds are real. One question, four spaces, and the
> answer is grounded — every claim cites the memory it came from."

**Land on:** the briefing — decisions on record, the blocker, next best
action, with citations.

---

## 1:30–2:15 · The centerpiece: "fix it"

**Screen:** type **fix it** in the composer. Let it run. Do not narrate every
step — pick the proposal moment and the approval.

> "Now the part that matters. 'Fix it' — no context, no menu. The model
> reads the workspace, finds the one blocker, finds the contradiction — the
> tracker says open, but a go/no-go meeting already settled it — and proposes
> the fix *by reference*: the resolution it submits is the exact one the
> system computed, not a re-typed guess.
>
> And look — it stops. The plan is right here in the answer: proposed,
> nothing applied. I'm the approval step."

**Click Approve.** The plan flips to Applied; the readiness board on the
right recomputes from **NOT READY** to **READY**.

> "Approved by a person. The board on the right recomputed from stored state —
> the launch is unblocked. The agent did the reading; the human did the
> deciding."

---

## 2:15–2:35 · Follow-ups that aren't pre-written

**Screen:** point at the suggestion chips under the composer — they changed
after the last answer. Click one (e.g. **"Show the proposed change waiting
for approval"** or **"Who settled the OAuth approval in Launch?"**).

> "These next questions aren't canned. They're drafted from what this session
> actually found — the conflict it saw, the person on record, the plan it
> filed. The console keeps up with the conversation."

---

## 2:35–2:50 · Built for agents outside the page

**Screen:** the **Live WebMCP activity** card, then (optional, only if
pre-connected) a Chrome browser agent calling `get_orgmemory_readiness` and
its call appearing in the card.

> "Anything speaking WebMCP — Chrome's built-in agent support, any MCP
> client — can connect to this URL and call the same tools. Foreign agent
> traffic shows here, separate from my own. Read tools stream data; write
> tools stop at exactly the same approval card I just clicked."

*(If you don't connect an external agent on camera, keep the sentence and cut
the optional shot — the activity card alone carries it.)*

---

## 2:50–3:00 · Close

**Screen:** back to the readiness board, **READY**, then hold on the logo.

> "Briefing, proposal, human decision, recorded outcome — that loop is the
> product. Anyone can ingest the same Slack and GitHub. Nobody can copy the
> record of which context actually produced correct action here.
>
> MemoryWorks — company memory your agents can actually use, at
> orgmemory.vercel.app."

---

## 60-second cut (if the form demands it)

Keep: 0:20–0:40 (real sign-in) → 0:40–1:00 (WebMCP surface card) → 1:30–2:15
("fix it" + approve + NOT READY→READY) → 2:50–3:00 (close). Drop the
catch-up beat and the follow-up chips beat.

## Upload checklist

- 1080p minimum, ≤ 200 MB (compress if needed; the UI is high-contrast so
  H.264 at ~6 Mbps reads fine)
- Title: `MemoryWorks — WebMCP: company memory your agents can use`
- Description first line: the one-line story above + repo link +
  `https://orgmemory.vercel.app`
- Caption the "fix it" beat — judges often watch muted
- In the submission form, put the WebMCP surface card (beat 3) in the first
  two screenshots: WebMCP Leverage is a quarter of the score and it should be
  legible without pressing play
