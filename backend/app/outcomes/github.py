"""Attach trusted GitHub observations to briefings for the same revision."""

from __future__ import annotations

import logging
import re

from app.connectors.base import SyncRecord
from app.core.database import connect

from .ledger import record_action_outcome

logger = logging.getLogger(__name__)
_PRODUCERS = {"workflow", "check", "deployment", "pull_request"}


def record_github_outcome(record: SyncRecord, *, workspace_id: str, project_id: str) -> bool:
    """Match a normalized observation after its project and repo are authorized."""
    meta = record.metadata
    details = meta.get("details") or {}
    producer = details.get("producer")
    state = meta.get("state")
    sha = str(details.get("head_sha") or "").lower()
    repository = str(meta.get("repository") or "").casefold()
    key = str(meta.get("observation_key") or "")
    observed_at = str(meta.get("observed_at") or "")
    evidence = str(meta.get("evidence_url") or "")
    if (
        not workspace_id
        or not project_id
        or producer not in _PRODUCERS
        or state not in {"success", "failure"}
        or not repository
        or not key
        or not evidence
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
    ):
        logger.info("Skipped GitHub outcome match because revision evidence was incomplete")
        return False

    signal = "ci" if producer in {"workflow", "check"} else "github"
    pr_number = meta.get("pr_number")
    with connect() as conn:
        # Select the briefing and reserve the receipt under the same write lock
        # so redeliveries cannot attach this observation to a newer briefing.
        conn.execute("BEGIN IMMEDIATE")
        replay = conn.execute(
            """SELECT 1 FROM outcome_events WHERE workspace_id=? AND project_id=?
               AND signal=? AND idempotency_key=? AND idempotency_key != ''""",
            (workspace_id, project_id, signal, key),
        ).fetchone()
        if replay:
            return False
        context = conn.execute(
            """SELECT id FROM context_events c
               WHERE workspace_id=:workspace_id AND project_id=:project_id AND github_sha=:sha
                 AND github_sha != '' AND github_repository=:repository
                 AND answer_scope='briefing'
                 AND julianday(c.created_at) <= julianday(:observed_at)
                 AND (
                     github_pr_number IS NULL OR github_pr_number=:pr_number
                     OR (:pr_number IS NULL AND NOT EXISTS (
                         SELECT 1 FROM context_events other
                         WHERE other.workspace_id=c.workspace_id AND other.project_id=c.project_id
                           AND other.github_sha=c.github_sha
                           AND other.github_repository=c.github_repository
                           AND other.answer_scope='briefing'
                           AND other.github_pr_number != c.github_pr_number
                           AND julianday(other.created_at) <= julianday(:observed_at)
                     ))
                 )
               ORDER BY (github_pr_number IS NOT NULL) DESC,
                        (surface IN ('github_check', 'github.com')) DESC,
                        created_at DESC, rowid DESC
               LIMIT 1""",
            {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "sha": sha,
                "repository": repository,
                "pr_number": pr_number,
                "observed_at": observed_at,
            },
        ).fetchone()
        if not context:
            logger.info("Skipped GitHub outcome match because no briefing matched the revision")
            return False
        if producer == "pull_request":
            action = "pr_merged" if state == "success" else "pr_closed"
            source = "pull request merge" if state == "success" else "pull request closure"
        else:
            action = f"{producer}_completed"
            source = {"workflow": "CI workflow", "check": "CI check", "deployment": "deployment"}[
                producer
            ]
        record_action_outcome(
            context_event_id=context["id"],
            workspace_id=workspace_id,
            action_type=action,
            outcome="succeeded" if state == "success" else "failed",
            signal=signal,
            actor="GitHub",
            surface="github.com",
            target=evidence,
            reason=f"GitHub {source} reported {state}: {evidence}",
            detail={"repository": meta.get("repository"), "head_sha": sha, "pr_number": pr_number},
            observed_at=observed_at,
            idempotency_key=key,
            _conn=conn,
        )
    return True
