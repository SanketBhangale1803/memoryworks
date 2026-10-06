"""Generate the MemoryWorks agent-harness architecture as SVG inside an HTML wrapper."""
from html import escape
W, H = 1640, 1072
out = []
add = out.append

def box(x, y, w, h, *, hot=False, soft=False, r=12):
    cls = "box hot" if hot else ("box soft" if soft else "box")
    add(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>')

def title(x, y, text, num=None):
    if num is not None:
        add(f'<circle class="num" cx="{x+12}" cy="{y-5}" r="11"/>')
        add(f'<text class="num-t" x="{x+12}" y="{y-1}" text-anchor="middle">{num}</text>')
        x += 30
    add(f'<text class="title" x="{x}" y="{y}">{escape(text)}</text>')

def lines(x, y, items, step=19, cls="body"):
    for i, t in enumerate(items):
        c = cls
        if t.startswith("`"):
            c, t = "mono", t.strip("`")
        add(f'<text class="{c}" x="{x}" y="{y + i*step}">{escape(t)}</text>')

def chip(x, y, text, hot=False):
    w = 9 + len(text) * 7.1
    add(f'<rect class="chip{" hot" if hot else ""}" x="{x}" y="{y}" width="{w:.0f}" height="24" rx="12"/>')
    add(f'<text class="chip-t{" hot" if hot else ""}" x="{x + w/2:.0f}" y="{y+16}" text-anchor="middle">{escape(text)}</text>')
    return w

def arrow(points, *, hot=False, dashed=False, both=False):
    cls = "ln" + (" hot" if hot else "") + (" dash" if dashed else "")
    m = "ah" if hot else "a"
    pts = " ".join(f"{a},{b}" for a, b in points)
    start = f' marker-start="url(#{m})"' if both else ""
    add(f'<polyline class="{cls}" points="{pts}" marker-end="url(#{m})"{start}/>')

def label(x, y, text, *, hot=False, anchor="middle", rotate=None):
    t = f' transform="rotate({rotate} {x} {y})"' if rotate is not None else ""
    add(f'<text class="lbl{" hot" if hot else ""}" x="{x}" y="{y}" text-anchor="{anchor}"{t}>{escape(text)}</text>')

# ---- heading
add('<text class="h1" x="40" y="52">MemoryWorks agent harness</text>')
add('<text class="sub" x="40" y="80">How a coding agent works on MemoryWorks with the orgmemory-harness skill, and where MemoryWorks sits in that loop.</text>')

# ---- host frame
add('<rect class="frame" x="40" y="104" width="1120" height="924" rx="18"/>')
add('<text class="frame-l" x="62" y="138">CODING AGENT HOST · CLAUDE CODE · CURSOR · CODEX</text>')

# row A
box(64, 152, 340, 168); title(84, 186, "Instructions", 1)
lines(84, 216, ["AGENTS.md and repository rules", "SKILL.md: orgmemory-harness", "Task contract: outcome, component,", "acceptance check, overlapping edits"])
box(424, 152, 340, 168); title(444, 186, "Context delivery", 2)
lines(444, 216, ["Passive: task contract, current code,", "a few source-backed memories", "Active: one file, citation, or log,", "fetched only to settle a named question"])
box(784, 152, 352, 168); title(804, 186, "Context management", 3)
lines(804, 216, ["Packet: intent, facts, decisions,", "changed files, checks, next action", "Compaction keeps citations and blockers", "Memory budget ≤ ~20k tokens (a cap)"])

# row B left
box(64, 344, 300, 184); title(84, 378, "Tool interfaces", 4)
lines(84, 408, ["Repository read and edit", "Shell checks and test runners", "MemoryWorks MCP, discovered first:", "`get_orgmemory_briefing", "`record_orgmemory_outcome", "`orgmemory_ask · search · profiles"])
box(64, 546, 300, 128); title(84, 580, "Reusable procedures", 9)
lines(84, 610, ["Skill steps, repository map,", "execution protocol, check list", "Changed only by a reviewed fix"])

# row B centre: orchestration state machine
box(384, 344, 520, 330); title(404, 378, "Orchestration · run states", 7)
nodes = [("scoping", 404, 82), ("ready", 502, 72), ("executing", 590, 96), ("verifying", 702, 96), ("complete", 814, 74)]
for name, x, w in nodes:
    hot = name == "complete"
    add(f'<rect class="state{" hot" if hot else ""}" x="{x}" y="414" width="{w}" height="40" rx="20"/>')
    add(f'<text class="state-t{" hot" if hot else ""}" x="{x + w/2}" y="439" text-anchor="middle">{name}</text>')
for (a, ax, aw), (b, bx, bw) in zip(nodes, nodes[1:]):
    arrow([(ax + aw, 434), (bx - 2, 434)])
arrow([(750, 454), (750, 494), (638, 494), (638, 457)])
label(694, 512, "repair, using the failure evidence")
add('<rect class="state warn" x="404" y="588" width="88" height="40" rx="20"/>')
add('<text class="state-t" x="448" y="613" text-anchor="middle">blocked</text>')
add('<rect class="state" x="514" y="588" width="112" height="40" rx="20"/>')
add('<text class="state-t" x="570" y="613" text-anchor="middle">revalidate</text>')
arrow([(448, 548), (448, 585)], dashed=True)
label(448, 542, "from any active state")
arrow([(492, 608), (511, 608)])
arrow([(570, 588), (570, 540), (538, 540), (538, 457)])
label(652, 612, "compare saved HEAD, diff,", anchor="start")
label(652, 628, "and contract with reality", anchor="start")
lines(404, 658, ["A saved approval covers only its original scope and revision."], cls="note")

# row B right
box(924, 344, 212, 166); title(944, 378, "Execution env.", 5)
lines(944, 408, ["Current checkout", "Worktree when isolation", "helps; the host sandbox", "owns network, credentials,", "and subprocesses"])
box(924, 528, 212, 146); title(944, 562, "Specialists", 8)
lines(944, 592, ["Optional, only when", "authorized. Task card:", "objective, owned paths,", "checks, return contract"])

# row C
box(64, 698, 540, 306); title(84, 732, "Durable state", 6)
lines(84, 762, ["`data/harness-runs/<run-id>/checkpoint.yaml"])
cx, cy = 84, 782
row_w = 0
for text in ["task contract", "HEAD + diff fingerprint", "capabilities", "routing: keep current model",
             "orgmemory: briefing_id · verdict", "outcome_sync", "verification[]", "failures[]", "next_action"]:
    w = 9 + len(text) * 7.1
    if cx + w > 584:
        cx, cy = 84, cy + 34
    chip(cx, cy, text, hot=text.startswith("orgmemory"))
    cx += w + 8
lines(84, 906, ["Resume from the earliest invalidated step; never repeat", "a completed external write because of an old status.",
                "Never stores secrets, customer records, or private source."], cls="body")

box(624, 698, 512, 306); title(644, 732, "Verification & observability", 10)
lines(644, 762, ["Evidence: command · cwd · exit code · revision covered", "UI work: inspect the rendered page as well",
                 "Failure ratchet: symptom → cause → repair → regression test", "A local pass is not production success"])
add('<text class="lbl" x="644" y="858">OUTCOME LABELS (PRODUCT VOCABULARY)</text>')
cx = 644
for text in ["succeeded", "failed", "partial", "abandoned", "unknown"]:
    cx += chip(cx, 870, text, hot=True) + 8
lines(644, 922, ["Workflow state and outcome label are separate:", "a run can be complete with an outcome of partial."], cls="body")

# ---- MemoryWorks
add('<rect class="mw" x="1196" y="104" width="404" height="700" rx="18"/>')
add('<text class="frame-l hot" x="1218" y="138">MEMORYWORKS · API + MCP</text>')
box(1220, 152, 356, 176, hot=True); add('<text class="title" x="1240" y="186">Pre-action briefing</text>')
lines(1240, 210, ["POST /api/briefings · no model call", "same intent, same verdict"])
cx, cy = 1240, 248
for text in ["proceed", "proceed_with_context", "requires_approval", "no_memory"]:
    w = 9 + len(text) * 7.1
    if cx + w > 1560:
        cx, cy = 1240, cy + 32
    chip(cx, cy, text)
    cx += w + 8
lines(1240, 316, ["Cited memories · opens a ledger row"], cls="note")
box(1220, 358, 356, 96, soft=True); add('<text class="title" x="1240" y="392">Approvals</text>')
lines(1240, 416, ["requires_approval holds the action;", "a person decides here"])
box(1220, 484, 356, 96, soft=True); add('<text class="title" x="1240" y="518">Outcome ledger</text>')
lines(1240, 542, ["context → action → outcome, tied to", "the briefing that came before it"])
box(1220, 610, 356, 112, soft=True); add('<text class="title" x="1240" y="644">Learned skills</text>')
lines(1240, 668, ["precedent from verified runs; retired", "when it precedes failures; feeds", "the next briefing"])
arrow([(1398, 328), (1398, 356)], hot=True)
arrow([(1398, 580), (1398, 608)], hot=True)
label(1406, 598, "distils", hot=True, anchor="start")
arrow([(1576, 666), (1588, 666), (1588, 240), (1578, 240)], hot=True)
add('<text class="note" x="1220" y="752">Agents can read, propose, and record. Changing</text>')
add('<text class="note" x="1220" y="771">memory, plans, or a connected tool waits for a person.</text>')

box(1296, 850, 252, 90, soft=True)
add('<text class="title" x="1316" y="882">A person</text>')
lines(1316, 904, ["approves or declines, on Approvals", "or inline in the chat"])
arrow([(1220, 406), (1208, 406), (1208, 886), (1294, 886)])

# ---- crossings between harness and MemoryWorks
arrow([(594, 152), (594, 118), (1468, 118), (1468, 150)], hot=True, both=True)
label(900, 112, "get_orgmemory_briefing(task, service)  ⇄  verdict + cited memories", hot=True)
arrow([(1136, 790), (1170, 790), (1170, 532), (1218, 532)], hot=True)
label(1189, 660, "record_orgmemory_outcome", hot=True, rotate=-90)

add('<text class="note" x="40" y="1056">Numbers follow the ten harness primitives in .agents/skills/orgmemory-harness/SKILL.md. Violet: the MemoryWorks loop the harness plugs into.</text>')

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="MemoryWorks agent harness: a coding agent host with ten primitives briefs itself from MemoryWorks before acting, holds approval-gated actions for a person, and records outcomes that become learned skills.">
<defs>
<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path fill="#6b6780" d="M0 0 L10 5 L0 10 z"/></marker>
<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path fill="#4f2cc7" d="M0 0 L10 5 L0 10 z"/></marker>
</defs>
<rect width="{W}" height="{H}" fill="#faf9fc"/>
{chr(10).join(out)}
</svg>'''

html = '''<title>harness</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
<style>
html,body{margin:0;padding:0;background:#faf9fc}
svg{display:block}
.h1{font:700 28px "IBM Plex Sans",sans-serif;fill:#17151f}
.sub{font:400 15px "IBM Plex Sans",sans-serif;fill:#5d5970}
.frame{fill:#ffffff;stroke:#cfc9dd;stroke-width:1.4;stroke-dasharray:7 6}
.mw{fill:#fbf9ff;stroke:#4f2cc7;stroke-width:1.6}
.frame-l{font:500 11.5px "IBM Plex Mono",monospace;letter-spacing:.06em;fill:#6b6780}
.frame-l.hot{fill:#4f2cc7}
.box{fill:#faf9fc;stroke:#d9d4e6;stroke-width:1.2}
.box.hot{fill:#f1ecfe;stroke:#4f2cc7;stroke-width:1.6}
.box.soft{fill:#ffffff;stroke:#b9aee6;stroke-width:1.2}
.title{font:600 16px "IBM Plex Sans",sans-serif;fill:#17151f}
.body{font:400 13.5px "IBM Plex Sans",sans-serif;fill:#3f3b4f}
.note{font:400 12.5px "IBM Plex Sans",sans-serif;fill:#6b6780}
.mono{font:500 12.5px "IBM Plex Mono",monospace;fill:#4f2cc7}
.num{fill:#f1ecfe;stroke:#4f2cc7;stroke-width:1.2}
.num-t{font:600 12px "IBM Plex Mono",monospace;fill:#4f2cc7}
.state{fill:#ffffff;stroke:#b4adc6;stroke-width:1.2}
.state.hot{fill:#f1ecfe;stroke:#4f2cc7}
.state.warn{fill:#fff6ef;stroke:#d6915c}
.state-t{font:500 13px "IBM Plex Mono",monospace;fill:#17151f}
.state-t.hot{fill:#4f2cc7}
.chip{fill:#f3f1f8;stroke:#e1dcec}
.chip.hot{fill:#f1ecfe;stroke:#c9bdf5}
.chip-t{font:500 12px "IBM Plex Mono",monospace;fill:#3f3b4f}
.chip-t.hot{fill:#4f2cc7}
.ln{fill:none;stroke:#6b6780;stroke-width:1.4}
.ln.hot{stroke:#4f2cc7;stroke-width:1.6}
.ln.dash{stroke-dasharray:5 5}
.lbl{font:500 11.5px "IBM Plex Mono",monospace;fill:#6b6780;paint-order:stroke;stroke:#ffffff;stroke-width:6px;stroke-linejoin:round}
.lbl.hot{fill:#4f2cc7}
</style>
''' + svg
import sys
open(sys.argv[1], "w").write(html)
open(sys.argv[2], "w").write(svg)
