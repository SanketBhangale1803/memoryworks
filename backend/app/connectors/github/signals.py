"""Pure GitHub signal normalization (no network, no LLM)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from app.connectors.base import SyncOperation, SyncRecord
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
