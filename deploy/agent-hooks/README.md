# Editor hooks: brief the agent before it acts

These hooks serve a MemoryWorks briefing **before** a coding agent runs a
consequential shell command or edits a file, using the same
`POST /api/briefings` endpoint that the `get_orgmemory_briefing` MCP tool wraps.
The briefing goes back to the agent with its `briefing_id` and an instruction to
report what happened through `record_orgmemory_outcome` (MCP) or
`orgmemory outcome` (CLI).

A briefing is advice, not authorization. The hooks never answer `allow`.

## Install

```sh
pip install ./python_sdk          # provides the `orgmemory` command
export MEMORYWORKS_API_URL=https://api.memoryworks.app
export MEMORYWORKS_API_KEY=...    # a workspace API key; keep it in your secret store
export MEMORYWORKS_SERVICE=payments-service   # optional, recommended
export MEMORYWORKS_PROJECT_ID=...             # optional
```

Workspace API keys work on the REST API used here. They are **not** accepted by
hosted HTTP MCP, which is OAuth-only.

### Claude Code

Merge [`claude-code-settings.json`](claude-code-settings.json) into
`.claude/settings.json` (project) or `~/.claude/settings.json` (user).

| Step | What the hook does |
|---|---|
| Read-only command (`ls`, `pytest`, `git status`), or any non-edit tool | Nothing. Prints no output. |
| Consequential command (`git push`, `kubectl apply`, `terraform apply`, `npm publish`, migrations, `rm -rf`, …) | Serves a briefing (`surface="claude_code"`) and adds it to the agent's context. |
| First edit of a file in a session | Serves one briefing for that file. Set `MEMORYWORKS_HOOK_BRIEF_EDITS=false` to brief commands only. |
| Verdict `requires_approval` | Also answers `permissionDecision: "ask"`, so the person at the keyboard decides. Repeats of the same step keep asking without opening another ledger row. |
| API down, key missing, or `no_memory` | Adds a note that constraints are unknown and that this is not permission. Does not block. |

With no `permissionDecision`, Claude Code's normal permission rules apply
unchanged.

### Cursor

Copy [`cursor-hooks.json`](cursor-hooks.json) to `.cursor/hooks.json` (project)
or `~/.cursor/hooks.json` (user). Cursor runs it before matching shell commands.

Cursor documents no "no decision" answer for `beforeShellExecution`, so the hook
always answers `permission: "ask"` with the briefing in `agent_message` and a
one-line verdict in `user_message`. Keep the `matcher` narrow, or every command
will need a click.

## After the step

```sh
orgmemory outcome <briefing_id> edit_applied --outcome succeeded \
  --reason "Local tests passed; not a production observation." \
  --detail '{"verification_scope": "local"}'
```

Outcomes are a closed vocabulary: `succeeded`, `failed`, `partial`,
`abandoned`, `unknown`.

## Dedupe state

The Claude Code hook remembers which steps it already briefed, per session, in
a small JSON file under the system temp directory (or
`MEMORYWORKS_HOOK_STATE_DIR`). It holds step keys, briefing ids, verdicts, and
headlines — never keys or tokens. Entries expire after 12 hours.
