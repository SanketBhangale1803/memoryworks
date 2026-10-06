"""A durable queue for slow work.

Repository ingestion, bulk GitHub imports, webhook processing, and approved
refreshes used to run on a thread of the API process that received the
request: they competed with answers for that process's CPU, and a restart lost
them silently. Each is now a row in ``background_jobs`` that any process can
claim:

* the API enqueues a job and, when ``BACKGROUND_WORK_ENABLED`` (one container
  doing everything, and tests), also runs it right after the response, as before;
* ``python -m app.worker`` claims queued jobs one at a time and runs them.

Claiming is a single conditional UPDATE, so a job runs once even when the API
and a worker both reach for it. A running job sends a heartbeat; one whose
heartbeat stops (its process died) is queued again, up to ``max_attempts``.
Payloads are JSON: a handler rebuilds what it needs (a connector, a principal)
from ids rather than receiving live objects.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.database import connect, new_id, row, utcnow

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]
_HANDLERS: dict[str, Handler] = {}

HEARTBEAT_SECONDS = 30
# A running job whose heartbeat is older than this belonged to a process that died.
STALE_AFTER = timedelta(minutes=5)


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return register


def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    background_tasks: Any = None,
    max_attempts: int = 2,
) -> str:
    """Record a job. Returns its id.

    With ``background_tasks`` (a FastAPI ``BackgroundTasks``) and background work
    enabled in this process, the job also runs right after the response.
    """
    job_id = new_id("bgjob")
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """INSERT INTO background_jobs
            (id,kind,payload_json,status,max_attempts,created_at,updated_at)
            VALUES (?,?,?,'queued',?,?,?)""",
            (job_id, kind, json.dumps(payload, default=str), max_attempts, now, now),
        )
    if settings.background_work_enabled:
        if background_tasks is not None:
            background_tasks.add_task(run_job, job_id)
        else:
            threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return job_id


def _claim(job_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with connect() as conn:
        claimed = conn.execute(
            """UPDATE background_jobs SET status='running', attempts=attempts+1,
            updated_at=?, heartbeat_at=? WHERE id=? AND status='queued'""",
            (now, now, job_id),
        ).rowcount
    return row("SELECT * FROM background_jobs WHERE id=?", (job_id,)) if claimed else None


def run_job(job_id: str) -> bool:
    """Run one job if it is still queued. Returns whether this call ran it."""
    job = _claim(job_id)
    if not job:
        return False
    fn = _HANDLERS.get(job["kind"])
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            with connect() as conn:
                conn.execute(
                    "UPDATE background_jobs SET heartbeat_at=? WHERE id=?", (utcnow(), job_id)
                )

    threading.Thread(target=beat, daemon=True).start()
    try:
        if fn is None:
            raise LookupError(f"No handler for background job kind {job['kind']!r}")
        fn(json.loads(job["payload_json"] or "{}"))
        _finish(job_id, "succeeded", "")
    except Exception as exc:  # noqa: BLE001 - a failed job is recorded, never raised
        logger.exception("Background job %s (%s) failed", job_id, job["kind"])
        _finish(job_id, "failed", str(exc)[:2000])
    finally:
        stop.set()
    return True


def _finish(job_id: str, status: str, error: str) -> None:
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """UPDATE background_jobs SET status=?, error=?, updated_at=?, completed_at=?
            WHERE id=?""",
            (status, error, now, now, job_id),
        )


def requeue_stale() -> int:
    """Queue again the jobs whose process died mid-run; fail those out of attempts."""
    cutoff = (datetime.now(UTC) - STALE_AFTER).isoformat()
    now = utcnow()
    with connect() as conn:
        retried = conn.execute(
            """UPDATE background_jobs SET status='queued', updated_at=?
            WHERE status='running' AND heartbeat_at<? AND attempts<max_attempts""",
            (now, cutoff),
        ).rowcount
        conn.execute(
            """UPDATE background_jobs SET status='failed', updated_at=?, completed_at=?,
            error='Interrupted when the process running it stopped, and out of retries.'
            WHERE status='running' AND heartbeat_at<? AND attempts>=max_attempts""",
            (now, now, cutoff),
        )
    return retried


def run_next() -> bool:
    """Claim and run the oldest queued job. Returns whether one ran."""
    requeue_stale()
    nxt = row("SELECT id FROM background_jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
    return bool(nxt) and run_job(nxt["id"])


def get(job_id: str) -> dict[str, Any] | None:
    return row("SELECT * FROM background_jobs WHERE id=?", (job_id,))
