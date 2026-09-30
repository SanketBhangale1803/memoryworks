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

The client also reads `ORGMEMORY_API_URL` and `ORGMEMORY_API_KEY`. Use
`AsyncMemoryWorks` in asynchronous applications.

## CLI

```bash
export ORGMEMORY_API_URL=http://localhost:8000
export ORGMEMORY_API_KEY=om_live_...

orgmemory health
orgmemory projects
orgmemory ask prj_platform "What changed in checkout, and why?" --model claude
orgmemory memories prj_platform
orgmemory graph prj_platform
orgmemory swarm swarm_01J...
```

Run `orgmemory --help` or `orgmemory <command> --help` for all options.
