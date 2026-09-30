# Memory Work

Memory Work is MemoryWorks’s bridge from company knowledge to reliable AI execution.

## Product boundary

[OpenWorker](https://github.com/andrewyng/openworker) is a broad, local-first AI coworker: it owns an agent loop, tools, connectors, scheduled automations, and deliverable generation. MemoryWorks does not duplicate that runtime. It supplies the missing organizational layer a worker needs before it can act correctly:

- hierarchical team, workspace, project, repo, service, and person scope;
- current source-backed memories instead of unstructured document dumps;
- temporal updates, invalidations, and conflicts;
- reproducible HCAG context envelopes;
- action boundaries and human approval;
- durable artifacts and returned execution evidence.

The products can be complementary:

```text
MemoryWorks: What is true, who may know it, and why?
Worker:    Which tools should run to achieve the approved outcome?
```

No OpenWorker source code is copied or required. The integration boundary is the portable `agent_packet`.

## One-step user flow

1. Select a project and describe an outcome on **Memory Work**.
2. HCAG activates authorized company context.
3. MemoryWorks creates a revisioned evidence-backed brief.
4. Knowledge-only work completes immediately.
5. External writes pause for explicit approval.
6. Slack work shows the destination and exact editable message at the top of the page.
7. Approval posts through `chat.postMessage` and stores Slack’s timestamp and permalink.
8. Other workers can fetch portable packets by API or MCP and report their result evidence.

If no relevant source evidence exists, MemoryWorks creates a blocked record and does not fabricate a brief.

## Portable packet

```json
{
  "work_id": "work_xxx",
  "objective": "Post a Slack launch update",
  "project_id": "proj_xxx",
  "context_envelope_id": "ctx_xxx",
  "target_connector": "slack",
  "context": {
    "answer": "Current source-backed context...",
    "memory_units": [],
    "evidence": [],
    "updates": [],
    "conflicts": []
  },
  "steps": [],
  "constraints": [
    "Use only authorized source-backed company context.",
    "Do not perform consequential actions without approval.",
    "Return the execution result and evidence to MemoryWorks."
  ]
}
```

## HTTP API

```text
POST /api/work
GET  /api/work?project_id={project_id}
GET  /api/work/{work_id}
POST /api/work/{work_id}/steps/{step_id}/resolve
POST /api/work/{work_id}/steps/{step_id}/complete
```

Create:

```json
{
  "project_id": "proj_xxx",
  "objective": "Prepare a release brief and post the approved summary to Slack."
}
```

Approve:

```json
{
  "approved": true,
  "channel_id": "C012345",
  "message": "*The exact reviewed Slack message*"
}
```

Report a worker result:

```json
{
  "output": {
    "message_ts": "171234.567",
    "source_url": "https://company.slack.com/archives/launch/...",
    "summary": "Posted the approved release update."
  }
}
```

## Worker integration contract

A worker must:

1. fetch the packet immediately before execution;
2. respect the packet scope and constraints;
3. execute only steps whose state permits execution;
4. never interpret `pending_approval` as authorization;
5. return stable identifiers, URLs, changed resources, and a concise result;
6. avoid sending secrets or raw credentials back as evidence.

Future connector adapters can push packets directly to OpenWorker-like runtimes. The v1 API and MCP contract deliberately remain vendor-neutral.

Slack posting requires the `chat:write` OAuth scope. Connections created before
this permission was added must be reconnected once from **Add knowledge →
Connections**. MemoryWorks never posts until the exact message and channel are
visible and the user clicks **Approve & post**.
