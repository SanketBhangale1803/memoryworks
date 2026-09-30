# Graph Model

ArcadeDB is the source of truth for knowledge relationships. The schema is
created idempotently by `backend/app/graph/migrations.py`; run
`make arcade-init` after changes. Every project-scoped vertex carries
`project_id` (indexed), and every vertex has a unique `id`.

## Vertex types

Structure: `Organization`, `Project`, `Repository`, `Branch`, `Commit`,
`Directory`, `File`, `Language`, `Package`, `Dependency`, `Module`,
`Import`, `Function`, `Class`, `Endpoint`.

Operations: `Service`, `EnvironmentVariable`, `ConfigKey`, `DockerService`,
`Workflow`, `Job`, `Step`, `Script`, `Command`, `LogLine`, `ErrorPattern`.

Collaboration: `Issue`, `PullRequest`, `Label`, `User`, `Comment`,
`SlackChannel`, `SlackMessage`.

Knowledge: `KnowledgeItem`, `KnowledgeChunk`, `ContextWindow`,
`EvidenceSource`.

Execution (legacy — from the product's earlier runbook direction; no UI, slated
for removal): `Runbook`, `RunbookStep`, `ApprovalPolicy`, `AgentAction`.

Intelligence (Fable 5 upgrade): `OperationalMemory`, `ServiceOwner`,
`BlastRadius`, `TrustScore`, `RunbookDriftSignal`, `OperationalAssertion`,
`ChangeImpact`.

## Edge types

Structure: `ORG_HAS_REPO`, `PROJECT_HAS_REPO`, `PROJECT_HAS_SERVICE`,
`REPO_HAS_BRANCH`, `REPO_HAS_COMMIT`, `REPO_HAS_DIRECTORY`,
`REPO_HAS_FILE`, `REPO_HAS_ISSUE`, `REPO_HAS_PULL_REQUEST`,
`DIRECTORY_HAS_FILE`, `FILE_WRITTEN_IN_LANGUAGE`, `FILE_IMPORTS_FILE`,
`FILE_IMPORTS_MODULE`, `FILE_DEFINES_FUNCTION`, `FILE_DEFINES_CLASS`,
`FILE_DEFINES_ENDPOINT`, `PACKAGE_HAS_DEPENDENCY`.

Configuration and services: `FILE_REFERENCES_ENV_VAR`,
`FILE_REFERENCES_CONFIG_KEY`, `FILE_MENTIONS_SERVICE`,
`SERVICE_DEFINED_IN_FILE`, `SERVICE_DEPENDS_ON_SERVICE`,
`SERVICE_USES_ENV_VAR`, `SERVICE_HAS_DOCKER_CONFIG`.

CI/CD: `REPO_HAS_WORKFLOW`, `WORKFLOW_HAS_JOB`, `JOB_HAS_STEP`,
`STEP_RUNS_COMMAND`.

Collaboration: `ISSUE_MENTIONS_SERVICE`, `ISSUE_REFERENCES_FILE`,
`ISSUE_HAS_COMMENT`, `ISSUE_HAS_LABEL`, `PR_TOUCHES_FILE`,
`PR_REFERENCES_ISSUE`, `SLACK_MESSAGE_MENTIONS_SERVICE`,
`SLACK_MESSAGE_REFERENCES_FILE`.

Diagnostics: `LOG_CONTAINS_ERROR_PATTERN`,
`ERROR_PATTERN_RELATED_TO_SERVICE`, `SERVICE_MENTIONED_IN`.

Knowledge: `FILE_HAS_CHUNK`, `CHUNK_DERIVED_FROM_FILE`,
`CHUNK_DERIVED_FROM`, `CHUNK_REFERENCES_SERVICE`,
`KNOWLEDGE_ITEM_DERIVED_FROM`.

Execution: `RUNBOOK_USES_SOURCE`, `RUNBOOK_APPLIES_TO_SERVICE`,
`RUNBOOK_HAS_STEP`, `ACTION_REQUIRES_APPROVAL`, `ACTION_APPROVED_BY`.

Intelligence (Fable 5 upgrade): `SERVICE_OWNED_BY`,
`SERVICE_AFFECTS_SERVICE`, `RUNBOOK_HAS_DRIFT_SIGNAL`,
`OPERATIONAL_MEMORY_BACKED_BY`, `TRUST_SCORE_DERIVED_FROM`,
`ASSERTION_ABOUT_SUBJECT`, `ASSERTION_AFFECTS_RUNBOOK`,
`ASSERTION_AFFECTS_RUNBOOK_STEP`, `ASSERTION_SUPPORTED_BY`,
`CHANGE_IMPACT_FOR_ASSERTION`, `CHANGE_IMPACT_TOUCHES_SOURCE`,
`CHANGE_IMPACT_AFFECTS_RUNBOOK`, `CHANGE_IMPACT_AFFECTS_RUNBOOK_STEP`, and
`DRIFT_SIGNAL_AFFECTS_ASSERTION`.

## ID conventions

- Project: `prj_<hex>` · Service: `<project_id>:<service_name>`
- File: `file:<project_id>:<path>` · Env var: `env:<project_id>:<NAME>`
- Chunk/Item: `chunk_<hex>` / `item_<hex>`
- Context window: `win:<project_id>:<domain>.<subdomain>`
- Drift signal: `drift_<hex>` · Memory: `mem_<hex>`
- Assertion: `assert_<hex>` · Change impact: `impact_<hex>`

## Query surfaces

- `GET /api/health/graph` — connectivity + global counts
- `GET /api/projects/{id}/graph/summary|nodes|edges` — explorer data
- `GET /api/projects/{id}/graph/service/{name}` — service neighborhood
- `GET /api/projects/{id}/graph/file?path=` — file neighborhood
- `GET /api/projects/{id}/graph/trace?chunk_ids=` — retrieval provenance
- `GET /api/projects/{id}/graph/blast-radius/{name}` — dependency traversal
  (dependencies, direct and second-hop dependents, env vars, runbooks,
  owners), computed from edges only.
- `GET /api/projects/{id}/assertions` and
  `GET /api/projects/{id}/change-impacts` — workspace-authorized reliability
  views. Impact paths are persisted as ArcadeDB edges, while decision state is
  audited locally.
