"""Pure GitHub signal normalization (no network, no LLM)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.connectors.base import SyncBatch, SyncOperation, SyncRecord
from app.core.config import settings
from app.ingestion.safety import sanitize_for_index

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}
_SUCCESS_CONCLUSIONS = {"success"}
_NON_OBSERVATIONS = {"neutral", "skipped", "stale"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(parts: Any) -> str:
    return hashlib.sha256(_canonical(parts).encode()).hexdigest()


def _clean_text(value: str) -> str:
    text, _ = sanitize_for_index(str(value or ""))
    text = text.strip()
    if len(text) > 200:
        text = text[:200]
    return text


def _validate_repository(repository: dict) -> tuple[int, str, str]:
    if not isinstance(repository, dict):
        raise ValueError("invalid repository")
    try:
        repo_id = int(repository.get("id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid repository id") from exc
    full_name = str(repository.get("full_name") or "")
    default_branch = str(repository.get("default_branch") or "")
    if not full_name or not _SLUG_RE.match(full_name):
        raise ValueError("invalid repository slug")
    if not default_branch:
        raise ValueError("invalid repository default branch")
    return repo_id, full_name, default_branch


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def _record_id(
    target: str, producer: str, provider_id: str, attempt: int, state: str, observed_at: str
) -> str:
    digest = _hash([target, producer, str(provider_id), int(attempt), state, observed_at])
    return f"github-signal:{digest}"


def normalize_terminal(
    producer: str,
    payload: dict,
    *,
    repository: dict,
    target: str,
    pr: dict | None = None,
    now: str | None = None,
) -> tuple[SyncRecord, ...]:
    repo_id, slug, _default_branch = _validate_repository(repository)
    if target not in ("default",) and not re.match(r"^pr:\d+$", target):
        # Target must be default or pr:number; unknown targets emit nothing.
        return ()
    canonical_slug = slug.casefold()
    if producer == "workflow":
        return _normalize_workflow(payload, repository, repo_id, slug, canonical_slug, target)
    if producer == "check":
        return _normalize_check(payload, repository, repo_id, slug, canonical_slug, target)
    if producer == "deployment":
        return _normalize_deployment(payload, repository, repo_id, slug, canonical_slug, target)
    if producer == "pr_review":
        return _normalize_pr_review(
            payload, repository, repo_id, slug, canonical_slug, target, pr, now
        )
    return ()


def _normalize_workflow(
    payload: dict, repository: dict, repo_id: int, slug: str, canonical_slug: str, target: str
) -> tuple[SyncRecord, ...]:
    try:
        run_id = int(payload.get("id"))
        workflow_id = int(payload.get("workflow_id"))
    except (TypeError, ValueError):
        return ()
    run_attempt = payload.get("run_attempt", 1)
    try:
        run_attempt = int(run_attempt)
    except (TypeError, ValueError):
        return ()
    status = str(payload.get("status") or "")
    conclusion = str(payload.get("conclusion") or "")
    created_at = str(payload.get("created_at") or "")
    updated_at = str(payload.get("updated_at") or "")
    head_sha = str(payload.get("head_sha") or "")
    if not created_at or not updated_at or status != "completed":
        return ()
    if conclusion in _FAILURE_CONCLUSIONS:
        state, severity, kind = "failure", "error", "ci_failure"
    elif conclusion in _SUCCESS_CONCLUSIONS:
        state, severity, kind = "success", "info", "ci_failure"
    else:
        return ()
    if not _parse_dt(created_at) or not _parse_dt(updated_at):
        return ()
    lane = {"producer": "workflow", "target": target, "workflow_id": str(workflow_id)}
    observation_key = _hash([run_id, run_attempt, updated_at, conclusion])
    evidence = f"https://github.com/{slug}/actions/runs/{run_id}"
    details = {
        "producer": "workflow",
        "target": target,
        "workflow_id": workflow_id,
        "head_sha": _clean_text(head_sha)[:40],
        "conclusion": _clean_text(conclusion),
    }
    return (
        _build_record(
            target=target,
            producer="workflow",
            provider_id=str(run_id),
            attempt=run_attempt,
            state=state,
            observed_at=updated_at,
            generation_at=created_at,
            generation_id=run_id,
            observation_key=observation_key,
            repository=slug,
            repo_id=repo_id,
            pr_number=_pr_number_from_target(target),
            source="github",
            kind=kind,
            subject=canonical_slug,
            lane=lane,
            severity=severity,
            evidence_url=evidence,
            details=details,
        ),
    )


def _normalize_check(
    payload: dict, repository: dict, repo_id: int, slug: str, canonical_slug: str, target: str
) -> tuple[SyncRecord, ...]:
    try:
        check_id = int(payload.get("id"))
    except (TypeError, ValueError):
        return ()
    name = str(payload.get("name") or "")
    app = payload.get("app") or {}
    try:
        app_id = (
            int(app.get("id"))
            if isinstance(app, dict) and app.get("id") is not None
            else int(payload.get("app_id"))
        )
    except (TypeError, ValueError):
        return ()
    status = str(payload.get("status") or "")
    conclusion = str(payload.get("conclusion") or "")
    started_at = str(payload.get("started_at") or "")
    completed_at = str(payload.get("completed_at") or "")
    head_sha = str(payload.get("head_sha") or "")
    if not started_at or not completed_at or status != "completed":
        return ()
    if conclusion in _FAILURE_CONCLUSIONS:
        state, severity = "failure", "error"
    elif conclusion in _SUCCESS_CONCLUSIONS:
        state, severity = "success", "info"
    else:
        return ()
    if not _parse_dt(started_at) or not _parse_dt(completed_at):
        return ()
    if not name:
        return ()
    lane = {
        "producer": "check",
        "target": target,
        "app_id": str(app_id),
        "check_name": _clean_text(name),
    }
    observation_key = _hash([check_id, started_at, completed_at, conclusion])
    evidence = f"https://github.com/{slug}/runs/{check_id}"
    details = {
        "producer": "check",
        "target": target,
        "app_id": app_id,
        "check_name": _clean_text(name),
        "head_sha": _clean_text(head_sha)[:40],
        "conclusion": _clean_text(conclusion),
    }
    return (
        _build_record(
            target=target,
            producer="check",
            provider_id=str(check_id),
            attempt=1,
            state=state,
            observed_at=completed_at,
            generation_at=started_at,
            generation_id=check_id,
            observation_key=observation_key,
            repository=slug,
            repo_id=repo_id,
            pr_number=_pr_number_from_target(target),
            source="github",
            kind="ci_failure",
            subject=canonical_slug,
            lane=lane,
            severity=severity,
            evidence_url=evidence,
            details=details,
        ),
    )


def _normalize_deployment(
    payload: dict, repository: dict, repo_id: int, slug: str, canonical_slug: str, target: str
) -> tuple[SyncRecord, ...]:
    deployment = payload.get("deployment") or {}
    status_obj = payload.get("deployment_status") or {}
    if not isinstance(deployment, dict) or not isinstance(status_obj, dict):
        return ()
    try:
        deployment_id = int(deployment.get("id"))
        status_id = int(status_obj.get("id"))
    except (TypeError, ValueError):
        return ()
    state_raw = str(status_obj.get("state") or "")
    created_at = str(deployment.get("created_at") or "")
    status_created = str(status_obj.get("created_at") or "")
    environment = str(deployment.get("environment") or "")
    task = str(deployment.get("task") or "deploy")
    sha = str(deployment.get("sha") or "")
    if not created_at or not status_created or not environment:
        return ()
    if state_raw in ("failure", "error"):
        state, severity = "failure", "error"
    elif state_raw == "success":
        state, severity = "success", "info"
    else:
        return ()
    if not _parse_dt(created_at) or not _parse_dt(status_created):
        return ()
    lane = {
        "producer": "deployment",
        "environment": _clean_text(environment),
        "task": _clean_text(task),
    }
    observation_key = _hash([deployment_id, status_id, state_raw])
    evidence = f"https://github.com/{slug}/deployments"
    details = {
        "producer": "deployment",
        "environment": _clean_text(environment),
        "task": _clean_text(task),
        "head_sha": _clean_text(sha)[:40],
        "conclusion": _clean_text(state_raw),
    }
    return (
        _build_record(
            target=target,
            producer="deployment",
            provider_id=str(deployment_id),
            attempt=1,
            state=state,
            observed_at=status_created,
            generation_at=created_at,
            generation_id=deployment_id,
            observation_key=observation_key,
            repository=slug,
            repo_id=repo_id,
            pr_number=None,
            source="github",
            kind="deploy",
            subject=canonical_slug,
            lane=lane,
            severity=severity,
            evidence_url=evidence,
            details=details,
        ),
    )


def _normalize_pr_review(
    payload: dict,
    repository: dict,
    repo_id: int,
    slug: str,
    canonical_slug: str,
    target: str,
    pr: dict | None,
    now: str | None,
) -> tuple[SyncRecord, ...]:
    fresh = (payload.get("pull_request") or {}) if isinstance(payload, dict) else {}
    reviews = (payload.get("reviews") or []) if isinstance(payload, dict) else []
    if pr is not None:
        fresh = pr
    try:
        number = int(fresh.get("number"))
    except (TypeError, ValueError):
        return ()
    state_raw = str(fresh.get("state") or "")
    draft = bool(fresh.get("draft", False))
    created_at = str(fresh.get("created_at") or "")
    updated_at = str(fresh.get("updated_at") or "")
    if not created_at or not updated_at:
        return ()
    created_dt = _parse_dt(created_at)
    if not created_dt:
        return ()
    now_dt = _parse_dt(now) if now else datetime.now(UTC)
    if not now_dt:
        return ()
    # Latest submitted review (non-PENDING with submitted timestamp).
    latest_submitted: datetime | None = None
    has_review = False
    if isinstance(reviews, list):
        for rev in reviews:
            if not isinstance(rev, dict):
                continue
            rev_state = str(rev.get("state") or "")
            submitted = str(rev.get("submitted_at") or rev.get("submittedAt") or "")
            if rev_state.upper() == "PENDING" or not submitted:
                continue
            dt = _parse_dt(submitted)
            if not dt:
                continue
            has_review = True
            if latest_submitted is None or dt > latest_submitted:
                latest_submitted = dt
    # Effective state in exact order: closed, draft, reviewed, stalled, young.
    if state_raw == "closed":
        effective = "closed"
    elif draft:
        effective = "draft"
    elif has_review:
        effective = "reviewed"
    else:
        threshold = int(getattr(settings, "github_pr_stalled_days", 7) or 7)
        age_days = (now_dt - created_dt).total_seconds() / 86400
        effective = "stalled" if age_days >= threshold else "young"
    # Deterministic snapshot timestamp.
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [day_start, _parse_dt(updated_at) or day_start]
    if latest_submitted:
        candidates.append(latest_submitted)
    snapshot = max(candidates)
    snapshot_iso = _iso(snapshot)
    if effective == "stalled":
        state, severity, kind = "failure", "warning", "pr_stalled"
    else:
        state, severity, kind = "success", "info", "pr_stalled"
    try:
        age_days_int = max(0, int((now_dt - created_dt).total_seconds() // 86400))
    except Exception:
        age_days_int = 0
    lane = {"producer": "pr_review", "target": f"pr:{number}"}
    observation_key = _hash([number, effective, snapshot_iso])
    evidence = f"https://github.com/{slug}/pull/{number}"
    details = {
        "producer": "pr_review",
        "target": f"pr:{number}",
        "pr_number": number,
        "age_days": age_days_int,
        "label": _clean_text(effective),
    }
    return (
        _build_record(
            target=f"pr:{number}",
            producer="pr_review",
            provider_id=str(number),
            attempt=1,
            state=state,
            observed_at=snapshot_iso,
            generation_at=snapshot_iso,
            generation_id=number,
            observation_key=observation_key,
            repository=slug,
            repo_id=repo_id,
            pr_number=number,
            source="github",
            kind=kind,
            subject=canonical_slug,
            lane=lane,
            severity=severity,
            evidence_url=evidence,
            details=details,
        ),
    )


def _pr_number_from_target(target: str) -> int | None:
    if target.startswith("pr:"):
        try:
            return int(target.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


def _build_record(
    *,
    target: str,
    producer: str,
    provider_id: str,
    attempt: int,
    state: str,
    observed_at: str,
    generation_at: str,
    generation_id: int,
    observation_key: str,
    repository: str,
    repo_id: int,
    pr_number: int | None,
    source: str,
    kind: str,
    subject: str,
    lane: dict[str, str],
    severity: str,
    evidence_url: str,
    details: dict[str, Any],
) -> SyncRecord:
    rid = _record_id(target, producer, provider_id, attempt, state, observed_at)
    title = _clean_text(f"{producer} {state} {subject} {target}")[:200]
    metadata: dict[str, Any] = {
        "source": source,
        "kind": kind,
        "subject": subject,
        "lane": lane,
        "severity": severity,
        "state": state,
        "observed_at": observed_at,
        "generation_at": generation_at,
        "generation_id": generation_id,
        "attempt": attempt,
        "observation_key": observation_key,
        "evidence_url": evidence_url,
        "details": details,
        "repository": repository,
        "repo_id": repo_id,
    }
    if pr_number is not None:
        metadata["pr_number"] = pr_number
    return SyncRecord(
        id=rid,
        resource_type="signal",
        operation=SyncOperation.UPSERT,
        version=observation_key,
        title=title,
        content="",
        source_url=evidence_url,
        updated_at=observed_at,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Resumable signal polling.
#
# poll_signals drives one complete current-head snapshot across many engine
# batches. Each call performs at most one data API request, returns the
# records from that page, and saves the next phase/page in cursor["signals"].
# Local-only transitions (phase changes, producer skips, review finalization
# after the last fetched page) advance without a request. This scans current
# monitored heads, not repository history; absence never resolves CI.
# ---------------------------------------------------------------------------

_SIGNALS_VERSION = 1
_PER_PAGE = 100

# Operational webhook events reconcile through verified polling, never through
# repository ingestion or an LLM.
OPERATIONAL_WEBHOOK_EVENTS = frozenset(
    {"check_run", "workflow_run", "deployment_status", "pull_request_review"}
)


class _SignalThrottle(Exception):
    """Signal polling must back off; carries only retry seconds."""

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"github signal reads throttled for {retry_after_seconds}s")
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class _SignalProducerUnavailable(Exception):
    """An optional producer read failed; carries only a sanitized diagnostic."""

    def __init__(self, diagnostic: str):
        super().__init__(f"github signal producer unavailable: {diagnostic}")
        self.diagnostic = diagnostic


def _normalize_slug(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "github.com" in text:
        path = text.split("github.com", 1)[1].strip("/").removesuffix(".git")
        slug = path.split("?", 1)[0].split("#", 1)[0].strip("/")
        parts = slug.split("/")
        if len(parts) < 2:
            return slug.casefold()
        return "/".join(parts[:2]).casefold()
    return text.split("?", 1)[0].split("#", 1)[0].strip("/").casefold()


def github_project_ids(workspace_id: str, repository: str) -> list[str]:
    """Project ids in this workspace whose configured repository matches exactly."""
    from app.core.database import rows

    wanted = _normalize_slug(repository)
    if not wanted or not _SLUG_RE.match(wanted):
        return []
    matched: list[str] = []
    for project in rows(
        """SELECT p.id, p.repository FROM projects p
        JOIN workspace_projects wp ON wp.project_id = p.id
        WHERE wp.workspace_id = ?""",
        (workspace_id,),
    ):
        if _normalize_slug(str(project.get("repository") or "")) == wanted:
            matched.append(str(project["id"]))
    return sorted(matched)


def sanitize_poll_hints(hints: Any) -> list[dict[str, Any]]:
    """Keep only allowlisted webhook hint fields for signal polling cursors."""
    clean: list[dict[str, Any]] = []
    if not isinstance(hints, list):
        return clean
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        kind = hint.get("kind")
        if kind in ("check", "workflow"):
            try:
                hint_id = int(hint.get("id"))
            except (TypeError, ValueError):
                continue
            if hint_id <= 0:
                continue
            entry: dict[str, Any] = {"kind": kind, "id": hint_id}
            branch = str(hint.get("branch") or "")[:200]
            if branch:
                entry["branch"] = branch
            sha = str(hint.get("sha") or "")[:40]
            if sha:
                entry["sha"] = sha
            try:
                pr_number = int(hint.get("pr_number"))
            except (TypeError, ValueError):
                pr_number = 0
            if pr_number > 0:
                entry["pr_number"] = pr_number
            clean.append(entry)
        elif kind == "deployment":
            try:
                deployment_id = int(hint.get("id"))
            except (TypeError, ValueError):
                continue
            if deployment_id > 0:
                clean.append({"kind": "deployment", "id": deployment_id})
        elif kind == "pr":
            try:
                number = int(hint.get("number"))
            except (TypeError, ValueError):
                continue
            if number > 0:
                clean.append({"kind": "pr", "number": number})
    return clean


def _fresh_snapshot() -> dict[str, Any]:
    return {
        "version": _SIGNALS_VERSION,
        "phase": "repo",
        "page": 1,
        "cycle_started_at": "",
        "repository": {},
        "default_sha": "",
        "open_prs": [],
        "targets": [],
        "target_index": 0,
        "producer": "",
        "sha_role": "",
        "review": None,
        "selections": {},
        "hinted_deployments": [],
        "deployments": [],
        "deployment_index": 0,
        "status_page": 1,
        "hints": [],
        "hint_index": 0,
        "diagnostics": {},
    }


def _target_label(target: dict[str, Any]) -> str:
    if target.get("kind") == "pr":
        return f"pr:{int(target.get('number') or 0)}"
    return "default"


def _sort_key_generation(record: SyncRecord) -> tuple:
    meta = record.metadata or {}
    parsed = _parse_dt(str(meta.get("generation_at") or ""))
    epoch = parsed.timestamp() if parsed else 0.0
    try:
        generation_id = int(meta.get("generation_id") or 0)
    except (TypeError, ValueError):
        generation_id = 0
    try:
        attempt = int(meta.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    return (epoch, generation_id, attempt, record.id)


def _as_list(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get(key, [])
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def _extract_pr_fields(item: dict[str, Any]) -> dict[str, Any]:
    head = item.get("head") or {}
    base = item.get("base") or {}
    head_repo = head.get("repo") or {}
    base_repo = base.get("repo") or {}
    try:
        number = int(item.get("number"))
    except (TypeError, ValueError):
        number = 0
    try:
        head_repo_id = int(head_repo.get("id")) if head_repo.get("id") is not None else 0
    except (TypeError, ValueError):
        head_repo_id = 0
    try:
        base_repo_id = int(base_repo.get("id")) if base_repo.get("id") is not None else 0
    except (TypeError, ValueError):
        base_repo_id = 0
    return {
        "kind": "pr",
        "number": number,
        "head_sha": str(head.get("sha") or ""),
        "merge_sha": item.get("merge_commit_sha"),
        "head_repo": str(head_repo.get("full_name") or ""),
        "head_repo_id": head_repo_id,
        "base_repo": str(base_repo.get("full_name") or ""),
        "base_repo_id": base_repo_id,
        "state": str(item.get("state") or ""),
        "draft": bool(item.get("draft", False)),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def _hint_target(
    hint: dict[str, Any], open_prs: list[dict[str, Any]], default_branch: str
) -> str | None:
    """Verify a check/workflow hint against snapshot context; None rejects it."""
    pr_number = hint.get("pr_number")
    if pr_number not in (None, ""):
        try:
            wanted = int(pr_number)
        except (TypeError, ValueError):
            return None
        for target in open_prs:
            if int(target.get("number") or 0) == wanted:
                return f"pr:{wanted}"
        return None
    branch = str(hint.get("branch") or "")
    if branch and branch == default_branch:
        return "default"
    return None


def poll_signals(connector, cursor: dict[str, Any], *, now: str | None = None) -> SyncBatch:
    """Perform at most one data request of a resumable GitHub signal snapshot."""
    slug = _normalize_slug(str(cursor.get("repository") or cursor.get("resource_id") or ""))
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError("invalid github repository for signal polling")
    raw = cursor.get("signals")
    if isinstance(raw, dict) and int(raw.get("version") or 0) != _SIGNALS_VERSION:
        raise ValueError("unsupported signal cursor version")
    # Never mutate the caller's cursor: throttling must return it unchanged.
    output = copy.deepcopy(dict(cursor))
    sig = copy.deepcopy(dict(raw)) if isinstance(raw, dict) else _fresh_snapshot()
    sig.setdefault("version", _SIGNALS_VERSION)
    for key, default in (
        ("phase", "repo"),
        ("page", 1),
        ("open_prs", []),
        ("targets", []),
        ("target_index", 0),
        ("producer", ""),
        ("sha_role", ""),
        ("review", None),
        ("selections", {}),
        ("hinted_deployments", []),
        ("deployments", []),
        ("deployment_index", 0),
        ("status_page", 1),
        ("hints", []),
        ("hint_index", 0),
        ("diagnostics", {}),
    ):
        sig.setdefault(key, copy.deepcopy(default))
    sig.setdefault("cycle_started_at", "")
    if not sig["cycle_started_at"]:
        sig["cycle_started_at"] = _iso(datetime.now(UTC)) if now is None else now
    moment = now or _iso(datetime.now(UTC))

    records: list[SyncRecord] = []
    try:
        while True:
            step = _plan_step(sig, slug)
            kind = step[0]
            if kind == "done":
                return _final_batch(output, records, moment)
            if kind == "local":
                if step[1](sig, slug, moment) == "done":
                    return _final_batch(output, records, moment)
                continue
            _, path, on_success, on_skip = step
            try:
                payload = connector._signal_api(path)
            except _SignalProducerUnavailable as exc:
                # One failed optional read ends this batch; the next batch
                # resumes past it. A batch never performs two data requests.
                on_skip(sig, exc.diagnostic)
                output["signals"] = sig
                return SyncBatch(tuple(records), output, has_more=True, retry_after_seconds=1)
            on_success(payload, sig, records, slug, moment)
            output["signals"] = sig
            return SyncBatch(tuple(records), output, has_more=True, retry_after_seconds=1)
    except _SignalThrottle as exc:
        # Provider or local pacing: keep the cursor exactly, retry later.
        # Sync retry attempts are not consumed for provider throttle.
        return SyncBatch(
            (), dict(cursor), has_more=True, retry_after_seconds=exc.retry_after_seconds
        )


def _final_batch(output: dict[str, Any], records: list[SyncRecord], moment: str) -> SyncBatch:
    output.pop("signals", None)
    output["signals_last_completed_at"] = moment
    return SyncBatch(tuple(records), output, has_more=False)


def _repo_meta(sig: dict[str, Any]) -> dict[str, Any]:
    repo = sig.get("repository") or {}
    if not isinstance(repo, dict) or not repo.get("full_name"):
        raise ValueError("signal snapshot lost repository metadata")
    return repo


def _note(sig: dict[str, Any], key: str, diagnostic: str) -> None:
    if diagnostic in ("forbidden", "unavailable"):
        sig.setdefault("diagnostics", {})[str(key)] = diagnostic


def _plan_step(sig: dict[str, Any], slug: str):
    """Decide the next polling action without performing any request."""
    phase = sig.get("phase")
    if phase == "repo":
        return ("request", f"/repos/{slug}", _got_repo, _fail_snapshot)
    if phase == "branch":
        branch = str((_repo_meta(sig).get("default_branch")) or "")
        return (
            "request",
            f"/repos/{slug}/branches/{quote(branch, safe='')}",
            _got_branch,
            _fail_snapshot,
        )
    if phase == "prs":
        return (
            "request",
            f"/repos/{slug}/pulls?state=open&per_page={_PER_PAGE}&page={int(sig.get('page') or 1)}",
            _got_prs,
            _fail_snapshot,
        )
    if phase == "hints":
        hints = sig.get("hints") or []
        index = int(sig.get("hint_index") or 0)
        while index < len(hints) and not _hint_needs_request(hints[index]):
            index += 1
        sig["hint_index"] = index
        if index >= len(hints):
            return ("local", _enter_targets)
        return ("request", _hint_path(slug, hints[index]), _got_hint, _skip_hint)
    if phase == "targets":
        return _plan_target_step(sig, slug)
    if phase == "deployments":
        return (
            "request",
            f"/repos/{slug}/deployments?per_page={_PER_PAGE}&page={int(sig.get('page') or 1)}",
            _got_deployments,
            _skip_deployments,
        )
    if phase == "statuses":
        selections = sig.get("deployments") or []
        if int(sig.get("deployment_index") or 0) >= len(selections):
            return ("local", _finish_cycle)
        selection = selections[int(sig.get("deployment_index") or 0)]
        return (
            "request",
            f"/repos/{slug}/deployments/{int(selection['id'])}/statuses"
            f"?per_page={_PER_PAGE}&page={int(sig.get('status_page') or 1)}",
            _got_statuses,
            _skip_status,
        )
    return ("done",)


def _fail_snapshot(sig: dict[str, Any], diagnostic: str) -> None:
    # Core inventory reads (repository, branch, open PRs) fail the job when
    # the provider refuses them; only optional producers advance with a note.
    raise ValueError(f"github signal snapshot unavailable: {diagnostic}")


def _got_repo(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    repo_id, full_name, default_branch = _validate_repository(payload)
    sig["repository"] = {"id": repo_id, "full_name": full_name, "default_branch": default_branch}
    sig["phase"] = "branch"


def _got_branch(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    sha = ""
    if isinstance(payload, dict):
        commit = payload.get("commit") or {}
        if isinstance(commit, dict):
            sha = str(commit.get("sha") or "")
    # A missing branch head yields no observation for default CI, never green.
    sig["default_sha"] = sha
    sig["phase"] = "prs"
    sig["page"] = 1
    sig["open_prs"] = []


def _got_prs(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    if not isinstance(payload, list):
        raise ValueError("unexpected open-PR inventory shape")
    for item in payload:
        if not isinstance(item, dict):
            continue
        fields = _extract_pr_fields(item)
        if fields["number"] > 0:
            sig["open_prs"].append(fields)
    if len(payload) >= _PER_PAGE:
        sig["page"] = int(sig.get("page") or 1) + 1
    else:
        sig["phase"] = "hints"
        sig["hint_index"] = 0


def _hint_needs_request(hint: Any) -> bool:
    return isinstance(hint, dict) and hint.get("kind") in ("check", "workflow", "deployment")


def _hint_path(slug: str, hint: dict[str, Any]) -> str:
    kind = hint.get("kind")
    try:
        hint_id = int(hint.get("id"))
    except (TypeError, ValueError):
        hint_id = 0
    if kind == "check":
        return f"/repos/{slug}/check-runs/{hint_id}"
    if kind == "workflow":
        return f"/repos/{slug}/actions/runs/{hint_id}"
    return f"/repos/{slug}/deployments/{hint_id}"


def _got_hint(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    hints = sig.get("hints") or []
    hint = (
        hints[int(sig.get("hint_index") or 0)]
        if int(sig.get("hint_index") or 0) < len(hints)
        else {}
    )
    kind = hint.get("kind") if isinstance(hint, dict) else None
    repo = _repo_meta(sig)
    default_branch = str(repo.get("default_branch") or "")
    if kind in ("check", "workflow") and isinstance(payload, dict):
        target = _hint_target(hint, sig.get("open_prs") or [], default_branch)
        if target is not None:
            producer = "check" if kind == "check" else "workflow"
            for record in normalize_terminal(producer, payload, repository=repo, target=target):
                records.append(record)
    elif kind == "deployment" and isinstance(payload, dict):
        try:
            deployment_id = int(payload.get("id"))
        except (TypeError, ValueError):
            deployment_id = 0
        if deployment_id > 0:
            known = {int(item.get("id") or 0) for item in sig.get("hinted_deployments") or []}
            if deployment_id not in known:
                sig["hinted_deployments"].append(
                    {
                        "id": deployment_id,
                        "environment": str(payload.get("environment") or ""),
                        "task": str(payload.get("task") or "deploy"),
                        "created_at": str(payload.get("created_at") or ""),
                        "sha": str(payload.get("sha") or ""),
                    }
                )
    # Failed or mismatched hint reads emit nothing; every hint advances.
    sig["hint_index"] = int(sig.get("hint_index") or 0) + 1


def _skip_hint(sig: dict[str, Any], diagnostic: str) -> None:
    _note(sig, "hint", diagnostic)
    sig["hint_index"] = int(sig.get("hint_index") or 0) + 1


def _enter_targets(sig: dict[str, Any], slug: str, moment: str) -> None:
    targets: list[dict[str, Any]] = [{"kind": "default"}]
    for entry in sorted(sig.get("open_prs") or [], key=lambda item: int(item.get("number") or 0)):
        targets.append(dict(entry))
    seen = {int(item.get("number") or 0) for item in targets if item.get("kind") == "pr"}
    hinted_numbers: list[int] = []
    for hint in sig.get("hints") or []:
        if not isinstance(hint, dict) or hint.get("kind") != "pr":
            continue
        try:
            number = int(hint.get("number"))
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in seen:
            seen.add(number)
            hinted_numbers.append(number)
    # Closed PR hints enter the verified-PR phase even when absent from open
    # inventory, so stalled issues resolve on a verified close/review.
    for number in sorted(hinted_numbers):
        targets.append({"kind": "pr", "number": number, "hinted": True})
    sig["targets"] = targets
    sig["target_index"] = 0
    sig["producer"] = ""
    sig["sha_role"] = ""
    sig["page"] = 1
    sig["review"] = None
    sig["phase"] = "targets"


def _target_producers(target: dict[str, Any]) -> list[tuple[str, str]]:
    if target.get("kind") != "pr":
        return [("checks", "head"), ("workflows", "head")]
    sequence = [("pr_get", ""), ("reviews", ""), ("checks", "head")]
    merge_sha = target.get("merge_sha") or ""
    if merge_sha and merge_sha != (target.get("head_sha") or ""):
        sequence.append(("checks", "merge"))
    sequence.append(("workflows", "head"))
    if merge_sha and merge_sha != (target.get("head_sha") or ""):
        sequence.append(("workflows", "merge"))
    return sequence


def _current_target(sig: dict[str, Any]) -> dict[str, Any]:
    targets = sig.get("targets") or []
    return targets[int(sig.get("target_index") or 0)]


def _advance_producer(sig: dict[str, Any]) -> None:
    producers = _target_producers(_current_target(sig))
    try:
        position = producers.index((sig.get("producer") or "", sig.get("sha_role") or ""))
    except ValueError:
        position = -1
    if position + 1 >= len(producers):
        _next_target(sig)
    else:
        sig["producer"], sig["sha_role"] = producers[position + 1]
        sig["page"] = 1


def _next_target(sig: dict[str, Any]) -> None:
    sig["target_index"] = int(sig.get("target_index") or 0) + 1
    sig["producer"] = ""
    sig["sha_role"] = ""
    sig["page"] = 1
    sig["review"] = None


def _skip_producer(sig: dict[str, Any], diagnostic: str) -> None:
    target = _current_target(sig)
    _note(sig, f"{sig.get('producer')}:{_target_label(target)}:{sig.get('sha_role')}", diagnostic)
    _advance_producer(sig)


def _skip_target(sig: dict[str, Any], diagnostic: str) -> None:
    _note(sig, f"target:{_target_label(_current_target(sig))}", diagnostic)
    _next_target(sig)


def _target_sha(target: dict[str, Any], role: str, default_sha: str) -> str:
    if target.get("kind") != "pr":
        return default_sha
    if role == "merge":
        return str(target.get("merge_sha") or "")
    return str(target.get("head_sha") or "")


def _target_repo_slug(target: dict[str, Any], role: str, slug: str) -> str:
    # A fork head is queried against its head repo only after verifying its
    # id/full_name from the base repository's PR GET (stored on the target);
    # the observation still inherits the base PR source constraints.
    if target.get("kind") == "pr" and role == "head" and target.get("head_repo"):
        candidate = _normalize_slug(str(target.get("head_repo") or ""))
        if candidate and _SLUG_RE.match(candidate):
            return candidate
    return slug


def _plan_target_step(sig: dict[str, Any], slug: str):
    while True:
        targets = sig.get("targets") or []
        index = int(sig.get("target_index") or 0)
        if index >= len(targets):
            return ("local", _enter_deployments)
        target = targets[index]
        producers = _target_producers(target)
        try:
            position = producers.index((sig.get("producer") or "", sig.get("sha_role") or ""))
        except ValueError:
            position = -1
        if position < 0:
            sig["producer"], sig["sha_role"] = producers[0]
            sig["page"] = 1
            position = 0
        producer, role = producers[position]
        if producer == "pr_get":
            return (
                "request",
                f"/repos/{slug}/pulls/{int(target.get('number') or 0)}",
                _got_pr,
                _skip_target,
            )
        if producer == "reviews":
            if not isinstance(sig.get("review"), dict):
                _advance_producer(sig)
                continue
            return (
                "request",
                f"/repos/{slug}/pulls/{int(target.get('number') or 0)}/reviews"
                f"?per_page={_PER_PAGE}&page={int(sig.get('page') or 1)}",
                _got_reviews,
                _skip_producer,
            )
        sha = _target_sha(target, role, str(sig.get("default_sha") or ""))
        if not sha:
            # Missing branch/head/fork checks yield no observation, never green.
            _advance_producer(sig)
            continue
        qslug = _target_repo_slug(target, role, slug)
        page = int(sig.get("page") or 1)
        if producer == "checks":
            return (
                "request",
                f"/repos/{qslug}/commits/{quote(sha, safe='')}/check-runs"
                f"?filter=all&per_page={_PER_PAGE}&page={page}",
                _got_checks,
                _skip_producer,
            )
        return (
            "request",
            f"/repos/{qslug}/actions/runs?head_sha={quote(sha, safe='')}"
            f"&per_page={_PER_PAGE}&page={page}",
            _got_workflows,
            _skip_producer,
        )


def _got_pr(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError("unexpected pull-request shape")
    repo = _repo_meta(sig)
    target = _current_target(sig)
    fields = _extract_pr_fields(payload)
    if fields["number"] <= 0 or fields["number"] != int(target.get("number") or 0):
        _note(sig, f"target:{_target_label(target)}", "unavailable")
        _next_target(sig)
        return
    target.update(fields)
    label = _target_label(target)
    if fields["state"] == "closed":
        # A verified close resolves PR-stalled issues; remaining CI for this
        # target is skipped and its open checks expire.
        for record in normalize_terminal(
            "pr_review",
            {"pull_request": fields, "reviews": []},
            repository=repo,
            target=label,
            now=moment,
        ):
            records.append(record)
        _next_target(sig)
        return
    sig["review"] = {"fresh": fields, "triples": []}
    sig["producer"] = "reviews"
    sig["sha_role"] = ""
    sig["page"] = 1


def _got_reviews(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    if not isinstance(payload, list):
        raise ValueError("unexpected review-list shape")
    repo = _repo_meta(sig)
    review = sig.get("review") or {}
    triples = review.get("triples") or []
    for item in payload:
        if not isinstance(item, dict):
            continue
        submitted = str(item.get("submitted_at") or item.get("submittedAt") or "")
        if not submitted:
            continue
        try:
            review_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        # Only submitted timestamp/state/id triples are accumulated, never bodies.
        triples.append([review_id, str(item.get("state") or ""), submitted])
    review["triples"] = triples
    sig["review"] = review
    if len(payload) >= _PER_PAGE:
        sig["page"] = int(sig.get("page") or 1) + 1
        return
    # All review pages are in: determine failure/success only now.
    fresh = review.get("fresh") or {}
    label = _target_label(_current_target(sig))
    synthetic = [
        {"id": triple[0], "state": triple[1], "submitted_at": triple[2]} for triple in triples
    ]
    for record in normalize_terminal(
        "pr_review",
        {"pull_request": fresh, "reviews": synthetic},
        repository=repo,
        target=label,
        now=moment,
    ):
        records.append(record)
    sig["review"] = None
    _advance_producer(sig)


def _emit_page_records(
    items: list[dict[str, Any]],
    producer: str,
    repository: dict,
    target_label: str,
    queried_sha: str,
    records: list,
) -> None:
    fresh: list[SyncRecord] = []
    for item in items:
        if producer == "workflow":
            # Filter to the snapshot target; never branch-name-only matching.
            if str(item.get("head_sha") or "") != queried_sha:
                continue
        elif queried_sha and str(item.get("head_sha") or "") not in ("", queried_sha):
            continue
        for record in normalize_terminal(
            producer, item, repository=repository, target=target_label
        ):
            fresh.append(record)
    # Oldest generation first within a page; the store orders across pages.
    fresh.sort(key=_sort_key_generation)
    records.extend(fresh)


def _got_checks(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    runs = _as_list(payload, "check_runs")
    repo = _repo_meta(sig)
    target = _current_target(sig)
    sha = _target_sha(target, sig.get("sha_role") or "", str(sig.get("default_sha") or ""))
    _emit_page_records(runs, "check", repo, _target_label(target), sha, records)
    if len(runs) >= _PER_PAGE:
        sig["page"] = int(sig.get("page") or 1) + 1
    else:
        _advance_producer(sig)


def _got_workflows(
    payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str
) -> None:
    runs = _as_list(payload, "workflow_runs")
    repo = _repo_meta(sig)
    target = _current_target(sig)
    sha = _target_sha(target, sig.get("sha_role") or "", str(sig.get("default_sha") or ""))
    _emit_page_records(runs, "workflow", repo, _target_label(target), sha, records)
    if len(runs) >= _PER_PAGE:
        sig["page"] = int(sig.get("page") or 1) + 1
    else:
        _advance_producer(sig)


def _enter_deployments(sig: dict[str, Any], slug: str, moment: str) -> None:
    sig["phase"] = "deployments"
    sig["page"] = 1
    sig["selections"] = {}


def _selection_key(environment: str, task: str) -> str:
    return f"{environment}\x00{task}"


def _newer_deployment(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_dt = _parse_dt(str(first.get("created_at") or ""))
    second_dt = _parse_dt(str(second.get("created_at") or ""))
    if first_dt and second_dt:
        if first_dt != second_dt:
            return first_dt > second_dt
        return int(first.get("id") or 0) > int(second.get("id") or 0)
    return str(first.get("created_at") or "") > str(second.get("created_at") or "")


def _got_deployments(
    payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str
) -> None:
    if not isinstance(payload, list):
        raise ValueError("unexpected deployment-list shape")
    selections = sig.get("selections") or {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            deployment_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        environment = str(item.get("environment") or "")
        if deployment_id <= 0 or not environment:
            continue
        candidate = {
            "id": deployment_id,
            "environment": environment,
            "task": str(item.get("task") or "deploy"),
            "created_at": str(item.get("created_at") or ""),
            "sha": str(item.get("sha") or ""),
        }
        key = _selection_key(environment, candidate["task"])
        current = selections.get(key)
        if current is None or _newer_deployment(candidate, current):
            selections[key] = candidate
    sig["selections"] = selections
    if len(payload) >= _PER_PAGE:
        sig["page"] = int(sig.get("page") or 1) + 1
        return
    sig["deployments"] = _sorted_selections(selections, sig.get("hinted_deployments") or [])
    sig["deployment_index"] = 0
    sig["status_page"] = 1
    sig["phase"] = "statuses"


def _sorted_selections(
    selections: dict[str, dict[str, Any]], hinted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = dict(selections)
    known_ids = {int(item.get("id") or 0) for item in merged.values()}
    for item in hinted:
        if not isinstance(item, dict):
            continue
        try:
            deployment_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if deployment_id <= 0 or deployment_id in known_ids:
            continue
        environment = str(item.get("environment") or "")
        if not environment:
            continue
        known_ids.add(deployment_id)
        key = _selection_key(environment, str(item.get("task") or "deploy"))
        current = merged.get(key)
        if current is None or _newer_deployment(item, current):
            merged[key] = item
    return sorted(
        merged.values(),
        key=lambda item: (str(item.get("environment") or ""), str(item.get("task") or "")),
    )


def _skip_deployments(sig: dict[str, Any], diagnostic: str) -> None:
    _note(sig, "deployments", diagnostic)
    sig["deployments"] = _sorted_selections({}, sig.get("hinted_deployments") or [])
    sig["deployment_index"] = 0
    sig["status_page"] = 1
    sig["phase"] = "statuses"


def _got_statuses(payload: Any, sig: dict[str, Any], records: list, slug: str, moment: str) -> None:
    selections = sig.get("deployments") or []
    selection = selections[int(sig.get("deployment_index") or 0)]
    repo = _repo_meta(sig)
    items = [
        item for item in (payload if isinstance(payload, list) else []) if isinstance(item, dict)
    ]
    # Statuses arrive newest first. A newer nonterminal status supersedes
    # older terminal results: emit nothing while pending rather than declare
    # the prior result current. inactive is neither failure nor success.
    if items:
        newest = items[0]
        state = str(newest.get("state") or "")
        if state in ("failure", "error", "success"):
            for record in normalize_terminal(
                "deployment",
                {"deployment": selection, "deployment_status": newest},
                repository=repo,
                target="default",
            ):
                records.append(record)
    sig["deployment_index"] = int(sig.get("deployment_index") or 0) + 1
    sig["status_page"] = 1


def _skip_status(sig: dict[str, Any], diagnostic: str) -> None:
    selections = sig.get("deployments") or []
    selection = selections[int(sig.get("deployment_index") or 0)] if selections else {}
    _note(sig, f"status:{selection.get('id', '')}", diagnostic)
    sig["deployment_index"] = int(sig.get("deployment_index") or 0) + 1
    sig["status_page"] = 1


def _finish_cycle(sig: dict[str, Any], slug: str, moment: str) -> str:
    return "done"
