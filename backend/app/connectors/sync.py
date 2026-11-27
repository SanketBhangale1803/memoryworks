from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.audit import AuditService
from app.auth.vault import OAuthTokenVault
from app.core.activity import wait_for_answers
from app.core.config import settings
from app.core.database import connect, new_id, row, rows, utcnow

from .base import SyncRecord, WebhookEvent, WebhookRequest
from .registry import ConnectorRegistry, get_connector_registry

ApplyRecord = Callable[[SyncRecord, dict[str, Any]], dict[str, Any] | None]

# A 'running' job older than this is assumed to come from a dead worker and is
# reclaimed back into the retry queue.
JOB_LEASE_MINUTES = 10


class ConnectorRateLimiter:
    """Process-local limiter backed by manifest policy.

    Provider responses can still request durable retry through SyncBatch's
    retry_after_seconds; this limiter prevents one worker from causing a burst.
    """

    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def acquire(self, key: str, requests: int, window_seconds: int) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                calls = self._calls[key]
                while calls and calls[0] <= now - window_seconds:
                    calls.popleft()
                if len(calls) < requests:
                    calls.append(now)
                    return
                delay = max(0.01, calls[0] + window_seconds - now)
            time.sleep(min(delay, 1.0))

    def try_acquire(self, key: str, requests: int, window_seconds: int) -> float:
        """Reserve one call without sleeping; return 0 when available.

        Returns the positive delay in seconds when the window is exhausted,
        so the caller can surface it through the durable throttle path
        instead of blocking a worker for a quota window.
        """
        with self._lock:
            now = time.monotonic()
            calls = self._calls[key]
            while calls and calls[0] <= now - window_seconds:
                calls.popleft()
            if len(calls) < requests:
                calls.append(now)
                return 0.0
            return max(0.0, calls[0] + window_seconds - now)


class SyncEngine:
    def __init__(
        self,
        apply_record: ApplyRecord,
        registry: ConnectorRegistry | None = None,
        audit: AuditService | None = None,
    ):
        self.apply_record = apply_record
        self.registry = registry or get_connector_registry()
        self.audit = audit or AuditService()
        self.rate_limiter = ConnectorRateLimiter()

    def _resolve_connector(self, provider: str, vault: OAuthTokenVault):
        """Built-in and installed packages first, then workspace registrations."""
        try:
            return self.registry.get(provider, vault)
        except KeyError:
            from .runtime import custom_connector_from_record

            record = row(
                """SELECT * FROM custom_connectors
                WHERE workspace_id=? AND provider=? AND status='active' AND revoked_at IS NULL""",
                (vault.workspace_id, provider),
            )
            if not record:
                raise
            return custom_connector_from_record(record, vault)

    def enqueue(
        self,
        provider: str,
        workspace_id: str,
        user_id: str,
        resource_id: str,
        *,
        project_id: str = "",
        cursor: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        manifest = self._resolve_connector(
            provider, OAuthTokenVault(workspace_id, user_id)
        ).manifest
        cursor = {**(cursor or {}), "resource_id": resource_id}
        key = (
            idempotency_key
            or hashlib.sha256(
                f"{provider}:{resource_id}:{json.dumps(cursor, sort_keys=True)}".encode()
            ).hexdigest()
        )
        existing = row(
            """SELECT * FROM connector_sync_jobs
            WHERE workspace_id=? AND provider=? AND idempotency_key=?""",
            (workspace_id, provider, key),
        )
        if existing:
            return self._decode_job(existing)
        job_id, now = new_id("sync"), utcnow()
        with connect() as conn:
            conn.execute(
                """INSERT INTO connector_sync_jobs
                (id,workspace_id,user_id,provider,resource_id,project_id,cursor_json,
                 status,attempts,max_attempts,next_attempt_at,last_error,idempotency_key,
                 created_at,updated_at,completed_at)
                VALUES (?,?,?,?,?,?,?,'queued',0,?,?, '',?,?,?,NULL)""",
                (
                    job_id,
                    workspace_id,
                    user_id,
                    provider,
                    resource_id,
                    project_id,
                    json.dumps(cursor),
                    manifest.retry.max_attempts,
                    now,
                    key,
                    now,
                    now,
                ),
            )
        self.audit.record(
            "connector.sync.queued",
            f"Queued {provider} sync for {resource_id}",
            project_id or None,
            user_id,
            {"job_id": job_id, "workspace_id": workspace_id, "provider": provider},
        )
        return self.get(job_id) or {}

    def receive_webhook(
        self,
        provider: str,
        workspace_id: str,
        request: WebhookRequest,
    ) -> dict[str, Any]:
        grant = row(
            """SELECT user_id FROM oauth_token_grants
            WHERE workspace_id=? AND provider=? AND status='connected'
            ORDER BY updated_at DESC LIMIT 1""",
            (workspace_id, provider),
        )
        if not grant:
            legacy = row(
                """SELECT user_id FROM workspace_connector_accounts
                WHERE workspace_id=? AND provider=? AND status='connected'
                ORDER BY updated_at DESC LIMIT 1""",
                (workspace_id, provider),
            )
            grant = legacy
        if not grant or not grant.get("user_id"):
            raise ValueError(f"No delegated {provider} grant is active for this workspace")
        user_id = grant["user_id"]
        connector = self.registry.get(provider, OAuthTokenVault(workspace_id, user_id))
        event = connector.handle_webhook(request)
        if event.challenge:
            return {"challenge": event.challenge}
        payload_hash = hashlib.sha256(request.body).hexdigest()
        github_projects: list[str] | None = None
        if provider == "github":
            # GitHub-only: fan out to explicit workspace-bound project
            # matches instead of historical jobs or an empty-project
            # fallback, so other providers' behavior provably does not change.
            from app.connectors.github.signals import github_project_ids

            github_projects = github_project_ids(workspace_id, event.resource_id)
            if not github_projects:
                raise ValueError(
                    "No MemoryWorks project in this workspace is connected "
                    f"to GitHub repository {event.resource_id!r}"
                )
        delivery_row = row(
            """SELECT * FROM connector_webhook_deliveries
            WHERE workspace_id=? AND provider=? AND delivery_id=?""",
            (workspace_id, provider, event.delivery_id),
        )
        if delivery_row:
            if delivery_row["payload_hash"] != payload_hash:
                raise ValueError("Webhook delivery ID was replayed with a different payload")
            if provider == "github" and delivery_row["status"] == "retryable":
                # A retryable GitHub delivery retries the enqueue below using
                # the existing idempotency keys instead of swallowing it.
                delivery_pk = delivery_row["id"]
            else:
                return {
                    "accepted": False,
                    "replayed": True,
                    "delivery_id": event.delivery_id,
                    "status": delivery_row["status"],
                }
        else:
            delivery_pk, now = new_id("delivery"), utcnow()
            try:
                with connect() as conn:
                    conn.execute(
                        """INSERT INTO connector_webhook_deliveries
                        (id,workspace_id,provider,delivery_id,payload_hash,event_type,resource_id,
                         status,attempts,last_error,received_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,'processing',0,'',?,?)""",
                        (
                            delivery_pk,
                            workspace_id,
                            provider,
                            event.delivery_id,
                            payload_hash,
                            event.event_type,
                            event.resource_id,
                            now,
                            now,
                        ),
                    )
            except Exception as exc:
                if "UNIQUE constraint failed" not in str(exc):
                    raise
                # A concurrent delivery won the insert; reread and compare.
                delivery_row = row(
                    """SELECT * FROM connector_webhook_deliveries
                    WHERE workspace_id=? AND provider=? AND delivery_id=?""",
                    (workspace_id, provider, event.delivery_id),
                )
                if not delivery_row:
                    raise
                if delivery_row["payload_hash"] != payload_hash:
                    raise ValueError(
                        "Webhook delivery ID was replayed with a different payload"
                    ) from exc
                if provider == "github" and delivery_row["status"] == "retryable":
                    delivery_pk = delivery_row["id"]
                else:
                    return {
                        "accepted": False,
                        "replayed": True,
                        "delivery_id": event.delivery_id,
                        "status": delivery_row["status"],
                    }
        try:
            applied = self._apply_webhook_records(
                provider, workspace_id, user_id, event, delivery_pk, github_projects
            )
            jobs = []
            if not event.records and event.resource_id:
                if provider == "github":
                    # GitHub-only: enqueue a signals_only snapshot per
                    # matching project with a project-aware webhook key.
                    # Verified polling (not the payload) decides state.
                    from app.connectors.github.signals import sanitize_poll_hints

                    hints = sanitize_poll_hints((event.cursor or {}).get("hints"))
                    for project_id in github_projects or []:
                        jobs.append(
                            self.enqueue(
                                provider,
                                workspace_id,
                                user_id,
                                event.resource_id,
                                project_id=project_id,
                                cursor={
                                    "repository": event.resource_id,
                                    "signals_only": True,
                                    "signals": {"version": 1, "hints": hints},
                                },
                                idempotency_key=f"webhook:{event.delivery_id}:{project_id}",
                            )
                        )
                else:
                    subscriptions = (
                        rows(
                            """SELECT DISTINCT project_id FROM connector_sync_jobs
                            WHERE workspace_id=? AND provider=? AND resource_id=? AND project_id!=''""",
                            (workspace_id, provider, event.resource_id),
                        )
                        or [{"project_id": ""}]
                    )
                    for subscription in subscriptions:
                        jobs.append(
                            self.enqueue(
                                provider,
                                workspace_id,
                                user_id,
                                event.resource_id,
                                project_id=subscription["project_id"],
                                cursor=event.cursor,
                                idempotency_key=f"webhook:{event.delivery_id}:{subscription['project_id']}",
                            )
                        )
            with connect() as conn:
                conn.execute(
                    """UPDATE connector_webhook_deliveries
                    SET status='succeeded',attempts=1,updated_at=? WHERE id=?""",
                    (utcnow(), delivery_pk),
                )
            self.audit.record(
                "connector.webhook.applied",
                f"Applied {provider} webhook {event.event_type}",
                actor=user_id,
                payload={
                    "workspace_id": workspace_id,
                    "provider": provider,
                    "delivery_id": event.delivery_id,
                    "records_applied": applied,
                    "sync_jobs": [item.get("id") for item in jobs],
                },
            )
            return {
                "accepted": True,
                "replayed": False,
                "delivery_id": event.delivery_id,
                "records_applied": applied,
            }
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    """UPDATE connector_webhook_deliveries
                    SET status='retryable',attempts=1,last_error=?,updated_at=? WHERE id=?""",
                    (str(exc), utcnow(), delivery_pk),
                )
            raise

    def run_once(self, limit: int = 10) -> int:
        now = datetime.now(UTC)
        # Reclaim leases from workers that died mid-run: a 'running' job whose
        # update timestamp is older than the lease window goes back to the
        # retry queue, so one crashed instance cannot stall a resource forever.
        lease_expiry = (now - timedelta(minutes=JOB_LEASE_MINUTES)).isoformat()
        stale = rows(
            """SELECT id FROM connector_sync_jobs
            WHERE status='running' AND updated_at<=? LIMIT ?""",
            (lease_expiry, limit),
        )
        for item in stale:
            with connect() as conn:
                conn.execute(
                    """UPDATE connector_sync_jobs SET status='retrying',updated_at=?
                    WHERE id=? AND status='running'""",
                    (now.isoformat(), item["id"]),
                )
        due = rows(
            """SELECT id FROM connector_sync_jobs
            WHERE status IN ('queued','retrying') AND next_attempt_at<=?
            ORDER BY next_attempt_at,created_at LIMIT ?""",
            (now.isoformat(), limit),
        )
        processed = 0
        for item in due:
            # Someone waiting on an answer reads the same graph an import writes;
            # let the answer go first.
            wait_for_answers(settings.connector_sync_yield_to_answers_seconds)
            with connect() as conn:
                claimed = conn.execute(
                    """UPDATE connector_sync_jobs SET status='running',updated_at=?
                    WHERE id=? AND status IN ('queued','retrying')""",
                    (now.isoformat(), item["id"]),
                ).rowcount
            if claimed:
                self._process(item["id"])
                processed += 1
        return processed

    def _process(self, job_id: str) -> None:
        job = row("SELECT * FROM connector_sync_jobs WHERE id=?", (job_id,))
        if not job:
            return
        vault = OAuthTokenVault(job["workspace_id"], job["user_id"])
        connector = self._resolve_connector(job["provider"], vault)
        account = vault.account(job["provider"])
        if not account:
            self._retry(job, ValueError("Delegated OAuth grant is disconnected"))
            return
        policy = connector.manifest.rate_limit
        self.rate_limiter.acquire(
            f"{job['workspace_id']}:{job['provider']}",
            policy.requests,
            policy.window_seconds,
        )
        try:
            batch = connector.sync(account, json.loads(job["cursor_json"] or "{}"))
            applied = 0
            for record in batch.records:
                if self._apply_once(
                    record,
                    {
                        "job_id": job_id,
                        "workspace_id": job["workspace_id"],
                        "user_id": job["user_id"],
                        "provider": job["provider"],
                        "resource_id": job["resource_id"],
                        "project_id": job["project_id"],
                    },
                ):
                    applied += 1
            now = utcnow()
            if batch.has_more:
                next_time = (
                    datetime.now(UTC)
                    + timedelta(seconds=max(0, int(batch.retry_after_seconds or 0)))
                ).isoformat()
                status, completed = "queued", None
            else:
                next_time, status, completed = now, "succeeded", now
            with connect() as conn:
                conn.execute(
                    # attempts counts consecutive failures: a successful page
                    # resets it, so a long import is not failed outright by its
                    # first error after many good pages.
                    """UPDATE connector_sync_jobs SET cursor_json=?,status=?,
                    attempts=0,next_attempt_at=?,last_error='',updated_at=?,completed_at=?
                    WHERE id=?""",
                    (
                        json.dumps(batch.next_cursor),
                        status,
                        next_time,
                        now,
                        completed,
                        job_id,
                    ),
                )
            self.audit.record(
                "connector.sync.completed" if status == "succeeded" else "connector.sync.page",
                f"Synced {job['provider']} resource {job['resource_id']}",
                job["project_id"] or None,
                job["user_id"],
                {
                    "job_id": job_id,
                    "workspace_id": job["workspace_id"],
                    "records_seen": len(batch.records),
                    "records_applied": applied,
                    "has_more": batch.has_more,
                },
            )
        except Exception as exc:
            self._retry(job, exc)

    def _retry(self, job: dict[str, Any], exc: Exception) -> None:
        connector = self._resolve_connector(
            job["provider"], OAuthTokenVault(job["workspace_id"], job["user_id"])
        )
        attempts = int(job["attempts"]) + 1
        terminal = attempts >= int(job["max_attempts"])
        delay = min(
            connector.manifest.retry.max_delay_seconds,
            connector.manifest.retry.base_delay_seconds * (2 ** max(0, attempts - 1)),
        )
        next_attempt = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        with connect() as conn:
            conn.execute(
                """UPDATE connector_sync_jobs SET status=?,attempts=?,next_attempt_at=?,
                last_error=?,updated_at=?,completed_at=? WHERE id=?""",
                (
                    "failed" if terminal else "retrying",
                    attempts,
                    next_attempt,
                    str(exc),
                    utcnow(),
                    utcnow() if terminal else None,
                    job["id"],
                ),
            )
        self.audit.record(
            "connector.sync.failed" if terminal else "connector.sync.retrying",
            f"{job['provider']} sync {'failed' if terminal else 'will retry'}",
            job["project_id"] or None,
            job["user_id"],
            {"job_id": job["id"], "attempt": attempts, "error": str(exc)},
        )

    def _apply_webhook_records(
        self,
        provider: str,
        workspace_id: str,
        user_id: str,
        event: WebhookEvent,
        delivery_pk: str,
        github_projects: list[str] | None = None,
    ) -> int:
        if provider == "github" and github_projects is not None:
            # GitHub-only: apply to explicit workspace-bound matches, never
            # to historical jobs or an empty-project fallback.
            projects = [{"project_id": project_id} for project_id in github_projects]
        else:
            projects = rows(
                """SELECT DISTINCT project_id FROM connector_sync_jobs
                WHERE workspace_id=? AND provider=? AND resource_id=? AND project_id!=''""",
                (workspace_id, provider, event.resource_id),
            )
        applied = 0
        for record in event.records:
            for project in projects or [{"project_id": ""}]:
                if self._apply_once(
                    record,
                    {
                        "delivery_id": delivery_pk,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "provider": provider,
                        "resource_id": event.resource_id,
                        "project_id": project["project_id"],
                    },
                ):
                    applied += 1
        return applied

    def _apply_once(self, record: SyncRecord, context: dict[str, Any]) -> bool:
        # Signal records own project-aware idempotency in their storage
        # transaction; they must not use the generic applied-record table whose
        # keys omit project and would collapse fan-out/replay across projects.
        if record.resource_type == "signal":
            self.apply_record(record, context)
            return True
        content_hash = hashlib.sha256(record.content.encode()).hexdigest()
        applied_id = new_id("applied")
        try:
            with connect() as conn:
                conn.execute(
                    """INSERT INTO connector_applied_records
                    (id,workspace_id,provider,record_id,record_version,operation,
                     content_hash,applied_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        applied_id,
                        context["workspace_id"],
                        context["provider"],
                        record.id,
                        record.version,
                        record.operation.value,
                        content_hash,
                        utcnow(),
                    ),
                )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return False
            raise
        try:
            self.apply_record(record, context)
        except Exception:
            # Release the reservation when the downstream memory transaction
            # fails so the durable retry can finish this exact revision.
            with connect() as conn:
                conn.execute("DELETE FROM connector_applied_records WHERE id=?", (applied_id,))
            raise
        return True

    def get(self, job_id: str) -> dict[str, Any] | None:
        record = row("SELECT * FROM connector_sync_jobs WHERE id=?", (job_id,))
        return self._decode_job(record) if record else None

    def list(self, workspace_id: str, status: str = "") -> list[dict[str, Any]]:
        return [
            self._decode_job(item)
            for item in rows(
                """SELECT * FROM connector_sync_jobs WHERE workspace_id=?
                AND (?='' OR status=?) ORDER BY created_at DESC""",
                (workspace_id, status, status),
            )
        ]

    @staticmethod
    def _decode_job(record: dict[str, Any]) -> dict[str, Any]:
        output = dict(record)
        output["cursor"] = json.loads(output.pop("cursor_json") or "{}")
        return output
