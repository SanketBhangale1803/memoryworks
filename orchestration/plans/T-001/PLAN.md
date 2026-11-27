# T-001 — Persist live signals and issues; collect GitHub CI and PR state
Status: plan-ready

## Problem (with evidence)

GitHub's repository-scoped `sync()` emits only commit records and a commit-SHA cursor (`backend/app/connectors/github/client.py:161`, `backend/app/connectors/github/client.py:196`). Repository discovery is a separate branch (`backend/app/connectors/github/client.py:138`). The broader repository importer reads issue and PR sources (`backend/app/ingestion/repository.py:582`, `backend/app/ingestion/repository.py:628`); do not mistake that importer for the connector sync contract. Review retrieval already exists (`backend/app/connectors/github/client.py:391`).

The manifest subscribes only to push, pull_request and issues (`backend/app/connectors/github/client.py:77`). The legacy webhook background handler ingests a repository and runs change intelligence (`backend/app/api/routes.py:1127`, `backend/app/api/routes.py:1132`). Operational events must bypass that path. The generic sync applier otherwise ingests records as knowledge and memory (`backend/app/connectors/application.py:83`), including unknown resource types mapped to documents (`backend/app/connectors/application.py:42`).

Two existing boundaries matter: the legacy webhook project lookup searches across workspaces and takes the first match (`backend/app/api/routes.py:1109`); generic record replay keys omit project (`backend/app/connectors/sync.py:438`, `backend/app/core/database.py:212`). New signals must not inherit either behavior. Generic webhook fan-out currently uses historical sync jobs (`backend/app/connectors/sync.py:416`).

SQLite initialization executes additive DDL before column upgrades (`backend/app/core/database.py:641`). Sources and projects use team allow-lists (`backend/app/governance/scopes.py:94`, `backend/app/governance/scopes.py:146`). Memory reads can trim by teams (`backend/app/memory/company.py:178`). Ownership resolution scores typed ownership memories and extracts an owner deterministically (`backend/app/orgops/service.py:509`); its current `_units()` is untrimmed (`backend/app/orgops/service.py:1258`). The new read path must trim before scoring.

Read adjacent tests before implementation: supported record dispatch (`backend/tests/test_connector_sync_applier.py:57`), webhook replay (`backend/tests/test_connector_platform.py:176`), workspace credential isolation (`backend/tests/test_auth_connector_boundaries.py:61`), source trimming (`backend/tests/test_company_brain.py:60`), and org operations/approval fixtures (`backend/tests/test_org_operations.py:38`).

## Design

### Scope and modules

Extend `backend/app/core/database.py`, `connectors/application.py`, `connectors/sync.py`, `connectors/github/client.py`, `orgops/service.py`, `api/routes.py`, and `core/config.py`. Add two focused modules: `backend/app/orgops/signals.py` (storage, clustering, trimmed reads) and `backend/app/connectors/github/signals.py` (GitHub normalization and resumable polling). No new framework, frontend, public situation API, MCP tool, watch, or briefing change: those belong to T-003. T-002 uses the same storage API for workspace-only runtime signals.

### Additive DDL

Append the following tables and indexes to `SCHEMA`. All times are UTC ISO strings with fixed microsecond precision and `+00:00`; normalize inputs before comparing. IDs use `new_id`. JSON is canonical, sorted, compact JSON. No existing table/column changes or backfill are necessary.

```sql
CREATE TABLE IF NOT EXISTS live_issues (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  scope_constraints_json TEXT NOT NULL DEFAULT '[]',
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','critical')),
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 0 CHECK(occurrences >= 0),
  status TEXT NOT NULL CHECK(status IN ('open','resolved','muted')),
  latest_signal_id TEXT NOT NULL DEFAULT '',
  resolved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(workspace_id,fingerprint)
);
CREATE TABLE IF NOT EXISTS live_signals (
  id TEXT PRIMARY KEY,
  issue_id TEXT REFERENCES live_issues(id) ON DELETE CASCADE,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','critical')),
  state TEXT NOT NULL CHECK(state IN ('failure','success')),
  observed_at TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  evidence_url TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL,
  observation_key TEXT NOT NULL,
  generation_at TEXT NOT NULL,
  generation_id INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 1,
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  source_grants_json TEXT NOT NULL DEFAULT '{}',
  details_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(workspace_id,fingerprint,observation_key)
);
CREATE INDEX IF NOT EXISTS live_issues_scope_status
  ON live_issues(workspace_id,project_id,status,last_seen);
CREATE INDEX IF NOT EXISTS live_signals_issue_time
  ON live_signals(issue_id,observed_at);
CREATE INDEX IF NOT EXISTS live_signals_freshness
  ON live_signals(workspace_id,project_id,expires_at);
```

`project_id=NULL` means workspace-only; an empty input string is normalized to NULL. Project-scoped ingestion requires an exact `workspace_projects` pair. Github always requires a project and matching configured repository. `latest_signal_id` deliberately has no foreign key: signals and issues are inserted in one transaction and the issue points to an existing signal after the transaction commits. The success-before-failure case stores a signal with NULL issue_id; a later failure creates the issue, attaches all matching signals, and derives state from the newest observation, so a late red result cannot undo an already-recorded green result.

### Storage and state machine contract

In `orgops/signals.py` expose:

```python
def signal_fingerprint(*, workspace_id: str, project_id: str | None,
                       source: str, kind: str, subject: str,
                       lane: dict[str, str], source_ids: list[str]) -> str: ...

class SignalService:
    def __init__(self, company_memory, scopes=None): ...
    def observe(self, *, workspace_id: str, project_id: str | None,
                source: str, kind: str, subject: str, lane: dict[str, str],
                severity: str, state: str, observed_at: str,
                generation_at: str, generation_id: int, attempt: int,
                observation_key: str, source_ids: list[str],
                evidence_url: str = '', details: dict | None = None,
                collected_at: str | None = None, ttl_seconds: int = 86400) -> dict: ...
    def list_issues(self, *, workspace_id: str, project_ids: list[str],
                    allowed_team_ids: list[str] | None,
                    status: str = 'open', include_expired: bool = False,
                    now: str | None = None, limit: int = 100) -> list[dict]: ...
```

Fingerprint is `sha256(canonical_json([1, workspace_id, project_id or '', source, kind, subject, lane, sorted(set(source_ids))]).encode()).hexdigest()`. `lane` contains stable dimensions, never event id, SHA, timestamp, conclusion, or secrets. Generic callers must keep subject/source IDs/lane stable across failure and success. `observation_key` identifies the provider observation, independent of webhook delivery and poll cycle. Validate enum values, nonempty workspace/source/kind/subject/key, positive TTL (max 7 days), attempt >= 1, nonnegative generation ID, and timestamps; reject malformed input without writes. Store only sanitized detail fields, never raw payloads. Returns `{'signal_id': str, 'issue_id': str | None, 'created': bool, 'status': str | None}`.

Use `BEGIN IMMEDIATE` inside `connect()` for serialized insert/aggregate updates. Upsert a duplicate observation only to refresh `collected_at`/`expires_at` (max existing/new expiry); it does not increment occurrences or change provider order. Reject duplicate keys whose normalized state/order/fingerprint fields disagree. Both poll and webhook must normalize an observation identically.

Provider order is the lexicographic tuple `(generation_at, generation_id, attempt, observed_at, state_rank, observation_key)` with failure rank 0 and success rank 1. A newer execution started before an older one finishes still supersedes the older execution. `generation_at` is check `started_at`, workflow `created_at`, deployment `created_at`; observation time is check `completed_at`, workflow `updated_at`, deployment status `created_at`. Never use webhook arrival time for CI ordering. PR review state uses the deterministic snapshot timestamp defined below for both generation and observation time, generation ID=PR number, attempt=1.

Each unique failure increments the lifetime issue occurrences, including out-of-order failures; first_seen=min failure observed time, last_seen=max failure observed time. The latest terminal observation determines open/resolved and latest evidence/severity. A newer failure reopens the same issue; resolved_at is the newest successful observation time or NULL when open. Muted issues stay muted while evidence updates; no mute API is introduced. Success alone creates no issue. Expiry is freshness, not evidence of recovery: it does not set resolved. Default reads omit an issue whose latest failure expires_at <= now; include_expired reads return it with `freshness='expired'`. Repeat polls renew freshness of the same currently observed failure without increasing occurrences. History is retained; retention/deletion is a separate task.

### Scope, ownership, and memory links

For a GitHub signal source IDs are always both `repository-metadata:{canonical_slug}` and `github-repository:{numeric_repository_id}`; PR-targeted observations additionally include `pull:{canonical_slug}:{number}`. These correspond to the importer metadata ID (`backend/app/ingestion/repository.py:388`), discovery record (`backend/app/connectors/github/client.py:142`), and PR source (`backend/app/ingestion/repository.py:628`). Repository names are casefolded; check names and environment names preserve case. Source IDs supplied by an API payload are never trusted: the adapter constructs them.

At observe time read source_scopes for each source **and project**; save `{source_id: sorted(team_ids)}`. Each nonempty list is an OR grant; multiple sources are AND constraints. Append distinct nonempty snapshot lists to the issue's `scope_constraints_json`, never union lists from different sources/observations. Persisted grants cannot silently broaden when a team/source is deleted. No new scope table or changes to ScopeService's existing behavior.

On every read require workspace equality, explicit caller project allow-list, exact workspace-project binding, project grants, every current source grant, and every historical nonempty snapshot constraint. `allowed_team_ids=None` is the existing trusted owner/admin convention (`backend/app/api/routes.py:304`), not a value a request body can select. `[]` sees only unscoped data. Workspace-only issues are included for the selected workspace even when project_ids is empty; their memory links and owner are empty. Recheck scope at read time, including project grants assigned after ingestion. Filter before pagination and counts. This intentionally hides an entire aggregate when its history requires a team the reader lacks; do not leak hidden occurrences, evidence, links or names through a partly visible issue.

Do not persist caller-independent owner text or linked-memory summaries: those can reveal narrower memory. Add `OrgOpsService.resolve_subject_owner(subject: str, service: str, ownership_units: list[dict]) -> dict` using exactly the existing `_terms`, `_score`, and `_owner_from_text` rules, returning `{'owner': str, 'evidence': list[str]}`. Refactor get_owner's memory branch to call this helper without changing its task branch or return shape. Preserve existing tie behavior for its ordered candidates. The new signal read path supplies only authorized latest ownership units from the same project, sorted by id to make ties stable.

For related memories, use the existing deterministic typed-memory search primitives: `CompanyMemoryService.list(... latest=True, allowed_team_ids=...)` and OrgOps `_terms`/`_score` (`backend/app/memory/company.py:178`, `backend/app/orgops/service.py:1300`). Additionally require all of each candidate's source_ids to pass `visible_source_ids`; exclude retired/expired memories. Score subject plus the allowlisted lane display label; select positive-score incident/decision units, descending score then id, max 5. Same-project only. Cache one authorized memory snapshot per project per list call, not across callers. Owner and related links are derived after issue trimming; missing matches return owner='' and []. Return related memory IDs plus `public_memory()` representations, and owner evidence IDs, all from this trimmed snapshot. No retrieval/LLM call occurs.

Issue JSON contract (internal, for T-003):

```json
{
  "id": "iss_example", "workspace_id": "wsp_example", "project_id": "prj_example",
  "source": "github", "kind": "ci_failure", "subject": "acme/api",
  "fingerprint": "sha256-hex", "severity": "error", "status": "open",
  "first_seen": "2026-10-08T10:00:00.000000+00:00",
  "last_seen": "2026-10-08T10:00:00.000000+00:00", "occurrences": 1,
  "resolved_at": null, "freshness": "fresh", "owner": "", "owner_evidence": [],
  "related_memory_ids": [], "related_memories": [],
  "latest_signal": {"id": "sig_example", "state": "failure", "evidence_url": "https://github.com/acme/api/actions/runs/101", "observed_at": "2026-10-08T10:00:00.000000+00:00", "expires_at": "2026-10-09T10:01:00.000000+00:00", "details": {"producer": "workflow", "workflow_id": 7, "target": "default", "head_sha": "abc", "conclusion": "failure"}}
}
```

Order default lists by severity critical/error/warning/info, last_seen descending, id ascending. Validate status and clamp limit 1..500. Unknown/out-of-scope projects yield no data, not widened scope.

### Normalized GitHub records

`resource_type='signal'`, UPSERT only; metadata contains the observe keyword fields except workspace/project/collected_at. Trusted job context supplies workspace/project and collection time. `record.id` is `github-signal:{fingerprint-independent target}:{producer}:{provider_id}:{attempt}:{state}:{observed_at}` constructed by canonical JSON hashing to avoid separator collisions; `record.version` is the same observation hash. Record content is empty. title is a sanitized display label; source_url is a canonical GitHub evidence URL. In `MemoryWorksSyncApplier.__call__`, dispatch signal records **before** generic ingestion; ignore metadata project_id and team_ids, validate job binding/repository, call SignalService.observe, return its result with status='signal_recorded'. No knowledge_items, memory_units, graph nodes, source revisions or ledger outcomes are created.

In SyncEngine._apply_once, signal records go straight to the applier without connector_applied_records reservation; their transaction owns project-aware idempotency. All other resource types retain the existing replay contract. An unassigned signal returns status='unassigned' without storage; a mismatched supplied project/workspace/repository raises ValueError.

GitHub normalization rules:

| Producer | Failure/success | Stable lane | Observation identity |
|---|---|---|---|
| check | completed failure, cancelled, timed_out, action_required / completed success | producer=check, target=default or pr:number, app_id, check_name | check id, started_at, completed_at, conclusion |
| workflow | completed failure, cancelled, timed_out, action_required / completed success | producer=workflow, target, workflow_id | run id, run_attempt, updated_at, conclusion |
| deployment | failure,error / success | producer=deployment, environment, task | deployment id, status id, state |
| PR stalled | open, non-draft, age >= N days and no submitted review / reviewed, draft, closed, or age < N | producer=pr_review, target=pr:number | PR number, effective state, deterministic snapshot time |

All CI uses kind=ci_failure; deployments kind=deploy; PR kind=pr_stalled. Failure severity error for CI/deploy, warning for PR; success severity info. A submitted review has submitted_at and state other than PENDING, including COMMENTED/CHANGES_REQUESTED/DISMISSED; it means someone reviewed, not approved. N is `settings.github_pr_stalled_days: int = 7` with >=1 validation. No conclusion neutral/skipped/stale, queued/in_progress state, missing timestamp, API failure, absent review page, or empty collection is a successful observation. PR snapshots emit at most one observation per UTC day **per effective state**, with timestamp=max(UTC day-start of collection, fresh PR updated_at, latest submitted_at among reviews). The key is canonical JSON hashing of [PR number, effective state, timestamp]. Evaluate effective state in this exact order: closed, draft, reviewed, then stalled if age >= N, otherwise young. Use that timestamp for generation_at and observed_at in both webhook reconciliation and polling. Determine failure/success only after all review pages, and closed state from a fresh PR GET. PR checks on closed PRs expire; PR-stalled issues resolve on a verified close/review.

GitHub check and workflow lanes remain separate: they expose different provider evidence and cannot be assumed equivalent. Successful workflow A never resolves workflow B, a different PR, or a check run. Default-branch failures across SHAs cluster, and PR failures across synchronize events cluster per PR. Use allowlisted numeric provider ids and canonical GitHub URLs for evidence, never log/target URLs supplied by providers. Details allow only producer, target, workflow_id, app_id, check_name, environment, task, head_sha, conclusion, PR number, age_days and label. Sanitize text using `sanitize_for_index` (`backend/app/ingestion/safety.py:32`), limit labels/names to 200 characters; drop tokens, URLs containing credentials/query/fragment, raw descriptions, review bodies, logs and annotations. Generic observe details also recursively sanitize and remove credential-bearing URLs; T-002 may add error text through the same sanitizer.

### Polling and rate-limit contract

Add `poll_signals(connector, cursor: dict, *, now: str | None = None) -> SyncBatch` and pure `normalize_terminal(...) -> tuple[SyncRecord,...]` in github/signals.py. `GitHubConnector.sync` keeps repository discovery unchanged; for repository sync, run the existing commit page once at cycle start, then enter signal polling through `cursor['signals']`. A `signals_only=true` cursor skips commit records. Do not gate operational polling on last_commit_sha: reruns/reviews change without a commit. No new scheduler: a repository sync invocation runs one complete snapshot; subsequent invocations start another snapshot. Existing SyncBatch has_more/next_cursor resumes queued pages (`backend/app/connectors/base.py:214`, `backend/app/connectors/sync.py:334`).

Persist cursor version 1, phase, page, cycle_started_at, repository metadata (id/default branch), default SHA, PR targets (number/head SHA/merge_commit_sha/base repo/head repo/state/draft/created_at/updated_at), target_index, producer_index, review_seen flag, latest deployments by (environment,task), and optional allowlisted webhook hints. Snapshot the default head with GET /repos/{slug}/branches/{encoded_default_branch}. Enumerate open PRs with GET /repos/{slug}/pulls?state=open&per_page=100&page=P. Branch/repo path segments are quoted; slugs must match exactly two safe GitHub name segments. Never follow provider-supplied URLs.

For each target (default first, PRs sorted by number), first refresh a PR via GET /pulls/{number}, enumerate reviews and emit its review-state observation; then enumerate check runs on current head SHA and, for PRs, non-null merge_commit_sha in the base repo using /repos/{slug}/commits/{sha}/check-runs?filter=all&per_page=100&page=P. A fork head is queried against its head repo only after verifying its id/full_name from the base repository's PR GET; its observation inherits the base PR source constraints, and no independent fork project is created. Enumerate workflow runs for each distinct target SHA using /repos/{slug}/actions/runs?head_sha={sha}&per_page=100&page=P, extracting workflow_runs. Filter results to the snapshot target; do not use branch-name-only matching for PRs. Multiple head/merge paths producing the same provider event are deduplicated by observation identity. Review enumeration reads all pages to obtain the latest submitted_at; do not stop at the first submitted review. If a PR closes during scanning, emit a verified PR success and skip its remaining CI work. Missing branch/head/fork checks yield no observation for that producer, never green.

Enumerate /repos/{slug}/deployments?per_page=100&page=P to completion, retaining only greatest (created_at,id) per (environment,task), then read each selected deployment's /statuses?per_page=100&page=P until the newest terminal success/failure/error is found or pages end. A newer nonterminal status supersedes older terminal status for that deployment: emit nothing while pending/in_progress/queued, rather than declaring the prior result current. inactive is neither failure nor success. Deployments are repo/environment scoped and are not restricted to the default branch.

Each poll batch performs **one data API request**, returns records from that page, and saves the next phase/page; has_more=true and retry_after_seconds=1 until done. Commit page is its own batch. No hidden `_api_all` loops, no 20-page truncation, no SHA/time watermark that skips a still-running execution. This scans current monitored heads, not the repository's entire historic CI; observed old failures stay historical/expire when their head is no longer current. All deployment pages remain resumable. Record observations oldest generation first within a page; database ordering makes cross-page/event order irrelevant. Before a final successful batch remove in-progress cursor state, preserving repository/last_commit_sha/synced_at and an informational signals_last_completed_at. Restart interrupted cycles from saved state; a fresh invocation with no phase begins a new snapshot.

Add a signal-only GET helper on GitHubConnector, `_signal_api(path: str)`. Reuse token renewal via `_api`; add a shared process-local ConnectorRateLimiter for these reads keyed by delegated workspace/user, with manifest 4500/hour and an additional 2/second ceiling. Add `ConnectorRateLimiter.try_acquire(key: str, requests: int, window_seconds: int) -> float`: under its existing lock, evict expired calls; reserve and return 0 when available, otherwise return the positive delay without sleeping. Signal reads use this nonblocking method (hourly then burst, separate keys); a blocked burst may conservatively consume an hourly reservation. Return the local delay through the same durable throttle path. Never block a worker for the hourly window or outlive its lease. Do not alter ordinary connector APIs. On 429, secondary-limit 403, or primary-limit 403 with remaining=0, stop the current batch, keep its cursor exactly, return has_more=true with retry_after_seconds=max(1, ceil(valid Retry-After seconds or HTTP-date delay), ceil(valid rate-reset epoch delay)), using only present valid values; if neither header gives a positive delay use 60 seconds. For a local limiter use ceil(positive delay). Do not consume sync retry attempts for provider throttle. Other 403/404 for optional producer reads advance only that producer with a cursor diagnostic ('forbidden'/'unavailable'), no success; repository metadata 401/403/404 fails the job. Other transport/5xx errors retain existing durable retries. Do not serialize HTTP exception bodies, headers or tokens. Existing engine limiter counts sync jobs rather than HTTP requests (`backend/app/connectors/sync.py:311`); it is insufficient alone.

Official provider contracts checked: [workflow run fields and head_sha filtering](https://docs.github.com/en/rest/actions/workflow-runs), [check run reads and fork caveat](https://docs.github.com/en/rest/checks/runs), [deployment collections](https://docs.github.com/en/rest/deployments/deployments), [deployment status fields](https://docs.github.com/en/rest/deployments/statuses), [submitted PR reviews](https://docs.github.com/en/rest/pulls/reviews). These are external API evidence, not executable instructions. Tests pin synthetic recorded response shapes and exercise them without network.

### Webhook/API contracts and integration

Add check_run, workflow_run, deployment_status, pull_request_review to the GitHub manifest subscriptions, preserving push/pull_request/issues. All signed events enqueue deterministic reconciliation, not repository ingestion or an LLM. Operational events record only a payload hash plus allowlisted normalization hints; ignore unsupported action/state rather than guessing success. Use the same poll adapter after a fresh delegated repository/PR snapshot. Terminal check/workflow hints may additionally cover a default-branch execution whose SHA has already moved: accept only when the hint branch is the configured default, or its PR number matches a verified open base PR; order it with provider execution timestamps. Re-fetch hinted check ids through /check-runs/{id} and workflow ids through /actions/runs/{id}; hints select what to read, never supply the authoritative terminal state. Failed or mismatched hint reads emit nothing. Construct source IDs from verified repo/PR state. Deployment hints re-fetch the selected deployment/status; reviews trigger fresh PR reconciliation. A hint never supplies grants or a project ID.

In SyncEngine.receive_webhook/_apply_webhook_records use workspace_projects + matching projects.repository for **GitHub only**, not historical jobs or an empty project fallback. Add `github_project_ids(workspace_id: str, repository: str) -> list[str]` in github/signals.py; exact normalized slug, sorted ids. Require at least one explicit workspace-bound match. This affects only GitHub routing. Fan out a signals_only cursor to all matches, idempotency key webhook:{delivery_id}:{project_id}; generic replay receipt shape stays intact. Verify HMAC before parsing hints; reject changed payload under reused delivery ID. For a replay of a retryable GitHub delivery, retry enqueue using existing idempotency keys rather than permanently swallowing a failed enqueue. Concurrent insert uniqueness is handled by reload/hash comparison.

POST /api/webhooks/github/{workspace_id} and POST /api/webhooks/github continue requiring the existing signature headers. Example workflow body shape is {"action":"completed","repository":{"id":42,"full_name":"acme/api","default_branch":"main"},"workflow_run":{"id":101,"workflow_id":7,"run_attempt":1,"head_branch":"main","head_sha":"abc","created_at":"...","updated_at":"...","status":"completed","conclusion":"failure","pull_requests":[]}}. The workspace-bound route preserves response {"accepted":true,"replayed":false,"delivery_id":"delivery-1","records_applied":0}, HTTP 202; replay returns accepted=false/replayed=true with existing status. No signal public write endpoint is introduced.

For the unbound legacy route's **operational** events, resolve the repository to exactly one workspace (multiple matching projects in that workspace are okay); reject ambiguity with HTTP 409 and advise the workspace-bound URL. Unknown repo 404. Forward to connector_sync.receive_webhook with original signed bytes/headers and return its receipt, without change_intelligence.observe. Keep legacy push/pull_request/issues response shape. Add optional workspace_id filtering to _github_webhook_project; require a unique workspace when unbound and select the lowest project id inside that workspace for the legacy change path; `_process_github_webhook_event` passes the supplied workspace, enqueues a signals_only sync for its project **before** its existing ingestion/change processing. Operational event handlers never enter that function's LLM path. A PR closed event is reconciled by a targeted verified PR GET even though it is absent from the open-PR inventory. Preserve signed payload hash replay and collect no source credentials in cursors.

The authenticated polling entrypoint is unchanged: POST /api/connectors/github/sync request {"resource_id":"acme/api","project_id":"prj_example","cursor":{"repository":"acme/api"},"idempotency_key":"manual-snapshot-1"} returns the existing sync-job object with status queued and cursor. It already checks project write authorization (`backend/app/api/routes.py:3598`). For GitHub, validate resource_id == cursor repository == project configured repository before enqueue; fill missing cursor repository from resource_id, reject mismatch. At the external sync route discard caller-supplied signals state, webhook hints and signals_only before enqueue; only internal worker/webhook code may construct those. Preserve the ordinary last_commit_sha compatibility input. Never let a caller point a private source's job at a broader unrelated project. Worker application revalidates the binding.

### Approvals and ledger

Signals are observations, not actions or company memory. Automatic issue resolution performs no external mutation. No promotion endpoint is part of T-001; any future first-class promotion must extend the existing human proposal flow and preserve issue source grants at approval. The existing proposal resolver creates source-less memory (`backend/app/api/routes.py:4080`), so it is **not** a safe automatic signal-promotion shortcut. This task must not call it, CompanyMemoryService.create, or ingestion for a signal. Existing human proposal/approval behavior remains intact (`backend/app/api/routes.py:3886`, `backend/app/api/routes.py:4069`). CI success is not evidence that a briefing-led action succeeded; do not write action/outcome ledger rows. Its five outcome labels and context/action linkage remain unchanged (`backend/app/outcomes/ledger.py:31`, `backend/app/outcomes/ledger.py:59`).

## Alternatives rejected

- A view over signals: cannot retain muted status, stable issue identity, success-before-failure ordering or lifetime counts cheaply. Persist aggregate rows transactionally.
- One fingerprint per SHA/run: separates successive failures and prevents newer green executions from resolving an ongoing problem.
- One fingerprint per repository: lets one workflow/PR resolve another and mixes access scopes.
- Reuse ingestion/change intelligence for operational events: creates memories and can invoke a model; use signal record dispatch.
- Snapshot-only ACL union or untrimmed owner caching: broadens derived history or exposes ownership memory. Enforce source/project constraints plus historical snapshots; derive enrichment per reader.
- Full history imports, commit-only cursors, or fetching all checks in a single job call: unnecessary historic work, missed reruns, and unbounded request bursts. Current-head snapshots with resumable single-request pages.
- Additional signal read HTTP endpoint or incident promotion UI: T-003 owns the read surfaces; promotion needs a separately reviewed ACL-preserving proposal change.

## Risks and containment

- Team changes/deletions: AND all snapshot constraints plus current source/project grants. Conservative hidden history is preferable to leaking aggregates; test reassignment and deleted source grants.
- Secrets/untrusted content: canonical provider links, allowlisted fields, sanitize before writes, no logs/review text/raw payload in signal storage/cursor. Signed payloads still count only as data.
- Rate limits/large repos: one data request per resumable page, process-local request pacing and provider-directed durable backoff. This does not claim a distributed quota limiter; multiple workers obey provider throttle. Cursor size grows with open PRs/environments; no arbitrary truncation that declares green.
- Pagination is a moving snapshot: state order rejects stale transitions; absence never resolves CI. Periodic invocations converge. No production collection schedule is claimed by this task.
- Expiry: disconnected/forbidden sources become stale, not resolved. T-003 must distinguish missing/stale observations from health.
- Existing DBs: new tables only; cold-start migration twice and preservation of preexisting rows are required. No drop/reset or historical memory conversion.
- Concurrent deliveries/retries: signal insert and issue updates share one immediate transaction; unique keys dedupe both paths per project. Success recorded first blocks stale red state. GitHub receipt retry remains enqueue-idempotent.

## Acceptance tests

From backend/, run `python -m pytest tests/test_live_signals.py tests/test_github_signals.py tests/test_github_signal_webhooks.py tests/test_connector_sync_applier.py tests/test_connector_platform.py tests/test_company_brain.py tests/test_org_operations.py tests/test_semantic_change_intelligence.py tests/test_auth_connector_boundaries.py tests/test_connector_token_renewal.py -q --tb=short`. Expected: all selected deterministic tests pass; only the existing opt-in live-model test may skip. No outbound requests; new fixtures mock httpx with a fail-on-unmatched transport and pin the clock.

Required new assertions: repeated red observations share one issue and count accurately; webhook+poll duplication does not increment; newer green resolves across SHA; older red/green arriving late cannot change latest state; success-before-failure creates resolved history; reopen/muted behavior; expiry hides without resolving; replay renews freshness; equal-time order; project/workspace/source/team trimming including history and private owner/memory links; NULL project runtime observations; idempotent legacy database upgrade; strict signal dispatch creates no knowledge/memory/ledger records; pagination >100 checks/PRs and interrupted resume; unchanged SHA rerun resolves; closed/reviewed PR resolves; 7-day boundary/draft/PENDING review rules; deployment failure/green/nonterminal transitions; optional 403 is not healthy; Retry-After/reset defers identical cursor with no retry-attempt increment; forks/merge SHAs are correctly assigned; two projects in one workspace both receive events; identical slug in two workspaces stays isolated; legacy operational ambiguity 409; bad signature/replayed changed payload fails; push still keeps its original response/change path.

Full CI commands are in SKILLS.md, copied from `.github/workflows/ci.yml:33`, `.github/workflows/ci.yml:58`, `.github/workflows/ci.yml:79`, `.github/workflows/ci.yml:95`. Expected: every lint/typecheck/test/build command exits 0; report actual outputs and environment limitations, never invent passes.

## Open questions

None blocking. Chosen defaults: PR stall threshold 7 days, signal TTL 24 hours, current-head CI collection, persisted lifetime issue identity, conservative historical ACL intersection. First-class promotion and automatic recurring polling schedule are outside this task; neither is required to prove the brief's polling/webhook persistence acceptance.
