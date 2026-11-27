# MemoryWorks Python SDK

Typed Python access to MemoryWorks's source-backed organizational memory, plus the
`orgmemory` command-line interface.

## Install

During local development:

```bash
python -m pip install -e ./python_sdk
```

## Python

```python
from orgmemory import MemoryWorks

memory = MemoryWorks(
    base_url="http://localhost:8000",
    api_key="om_live_...",
)

context = memory.ask(
    project_id="prj_platform",
    query="What changed in checkout, and why?",
    model="claude",  # gpt, claude, gemini, grok, or kimi
)

print(context.answer)
print(context.compiled_context)

briefing = memory.get_briefing(
    task="Change the checkout deployment workflow",
    service="checkout",
    project_id="prj_platform",
    surface="ci-agent",
)

# Apply the briefing's constraints and use your own approval system when its
# verdict is requires_approval. Then close the durable outcome loop.
receipt = memory.record_briefing_outcome(
    briefing_id=briefing.briefing_id,
    action="deployment_workflow_changed",
    outcome="succeeded",
    target=".github/workflows/deploy.yml",
    surface="ci-agent",
)
print(receipt.status)
```

The client also reads `MEMORYWORKS_API_URL` and `MEMORYWORKS_API_KEY` (the older `ORGMEMORY_API_URL` and `ORGMEMORY_API_KEY` still work). Use
`AsyncMemoryWorks` in asynchronous applications.

## CLI

```bash
export MEMORYWORKS_API_URL=http://localhost:8000
export MEMORYWORKS_API_KEY=om_live_...

orgmemory health
orgmemory projects
orgmemory ask prj_platform "What changed in checkout, and why?" --model claude
orgmemory memories prj_platform
orgmemory graph prj_platform
orgmemory swarm swarm_01J...
```

### Briefings from scripts and hooks

`brief` and `outcome` wrap `get_briefing` and `record_briefing_outcome`, so a
script or hook never has to hand-roll the HTTP calls.

```bash
orgmemory brief "raise the payments worker limit" --service payments-service
# verdict: requires_approval
# briefing_id: ctx_...

orgmemory outcome ctx_... config_changed --outcome succeeded \
  --reason "Load test passed in staging." --detail '{"verification_scope": "staging"}'
```

A briefing is advice, not authorization: `requires_approval` is not an
approval and `no_memory` is not permission. `--outcome` takes only `succeeded`,
`failed`, `partial`, `abandoned`, or `unknown`. Add `--json` for the full
response.

Two more commands run briefings automatically:

- `orgmemory hook claude-code` / `orgmemory hook cursor` — editor pre-tool hooks.
  See [`deploy/agent-hooks`](../deploy/agent-hooks/README.md).
- `orgmemory github-check` — briefs a pull request and posts a Check and PR comment.
  See [`deploy/github-action`](../deploy/github-action/README.md).

Run `orgmemory --help` or `orgmemory <command> --help` for all options.
