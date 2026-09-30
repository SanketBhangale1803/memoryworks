# Conference readiness

Runbook's public story is **persistent, verified operational intelligence**: the company explains its systems once through connected evidence, HCAG maintains the active context, and every operational conclusion stays traceable to current sources and governance.

## What is real

- Repository, GitHub, Slack, upload, and incident-import paths enter the same ingestion service.
- ArcadeDB stores project-scoped entities, evidence chunks, relationships, context windows, runbooks, assertions, and change impacts.
- SQLite stores application identity, workspace membership, ingestion jobs, approved operational memory, audit state, and durable HCAG continuity.
- HCAG's structured planner classifies retrieval intent and exposes its plan, boundary, resolved query, and context reuse in the response.
- Answers abstain when evidence is insufficient. Confidence and trust are calculated from retrieved evidence; related files/issues/PRs are never added unless retrieval surfaced them.
- AgentGate separates read-only investigation from actions that require human approval. This release does not contain a shell executor.

## Presenter validation

```bash
make conference-check
docker compose up --build -d arcadedb backend frontend
make runtime-check
```

Expected: 44 backend tests, 3 frontend tests, clean Python/TypeScript linting, a successful optimized Next.js build, valid Compose configuration, healthy API, connected ArcadeDB, and an HTTP 200 frontend.

Use an inspectable repository rather than the optional sample corpus. Confirm that **Command center** reports evidence for that project, then ask one answerable and one deliberately unsupported question. The supported question must show original source links; the unsupported question must show `Conclusion withheld` with no citations or actions.

For continuity, ask an explicit service question and then a short follow-up. The second retrieval trace must show `context_reused: true`, the same active service, and an incremented persisted query count.

## Production boundary

Runbook now refuses `ENVIRONMENT=production` when development login, the default JWT secret, demo mode, local command execution, or a non-HTTPS frontend origin is configured. Production CORS excludes localhost. Project context, investigation, reliability decisions, and runbook extraction use workspace-aware authorization; viewers cannot mutate reliability state or extract procedures.

That is a secure application boundary, not a claim of completed enterprise deployment. A real deployment still needs managed secrets, TLS termination, backups and restore drills, an external identity provider, ArcadeDB/SQLite durability appropriate to the topology, monitoring, rate limiting at the gateway, vulnerability scanning, and a disaster-recovery runbook. Do not market the local Compose topology itself as production infrastructure.

## Pitch

> Runbook is the operational context layer for a company. It remembers how systems work, proves where every conclusion came from, detects when code changes invalidate procedure, and prevents AI agents from crossing an action boundary without approval.
