"""The slow half of MemoryWorks, as one set of loops.

Connector syncs, queued jobs (repository ingestion, bulk imports, webhooks,
refreshes), standing watches, and the startup backfill. The API runs these
in-process when ``BACKGROUND_WORK_ENABLED`` is true; otherwise
``python -m app.worker`` runs them in their own process (and container), so an
import never competes with an answer for the API's CPU.

Every loop outlives a failed iteration: a raised error (a transient lock, a
broken connector) is logged and the loop continues, or the deployment would
silently stop syncing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from app import jobs
from app.api.org_routes import watches
from app.api.routes import connector_sync, graph, hcag
from app.core.config import settings
from app.ingestion.maintenance import sanitize_existing_index

logger = logging.getLogger(__name__)


async def _forever(name: str, step: Callable[[], object], idle_seconds: float) -> None:
    while True:
        busy = False
        try:
            busy = bool(await asyncio.to_thread(step))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - see module docstring
            logger.warning("%s iteration failed: %s", name, exc)
        # Drain a backlog without pausing; idle politely when there is none.
        await asyncio.sleep(0 if busy else idle_seconds)


def _run_watches() -> None:
    watches.run_due()  # never "busy": watches are due on their own schedule


def start(*, sanitize: bool = False) -> list[asyncio.Task]:
    """Start every background loop on the running event loop; returns the tasks."""

    async def maintenance() -> None:
        # Legacy-vector migration can touch thousands of chunks; it runs off the
        # request path so health checks and answers are available immediately.
        if sanitize:
            with suppress(Exception):
                await asyncio.to_thread(sanitize_existing_index, graph, hcag)
        await asyncio.to_thread(hcag.backfill_existing_chunks)

    loops: list[Awaitable[None]] = [
        maintenance(),
        _forever("background job", jobs.run_next, max(1, settings.connector_sync_poll_seconds)),
        # Standing watches only ever record a finding and draft a plan; applying
        # one still needs a person.
        _forever("watch", _run_watches, max(30, settings.org_watch_poll_seconds)),
    ]
    if settings.connector_sync_worker_enabled:
        loops.append(
            _forever(
                "connector sync",
                connector_sync.run_once,
                max(1, settings.connector_sync_poll_seconds),
            )
        )
    return [asyncio.create_task(loop) for loop in loops]


async def stop(tasks: list[asyncio.Task], timeout: float = 10.0) -> None:
    """Cancel the loops. Work already handed to a thread cannot be interrupted;
    wait for it at most ``timeout`` seconds (Docker's own stop grace period) —
    an interrupted job is picked up again by requeue_stale."""
    for task in tasks:
        task.cancel()
    if tasks:
        with suppress(Exception):
            await asyncio.wait(tasks, timeout=timeout)
