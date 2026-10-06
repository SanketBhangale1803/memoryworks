"""Answers come before imports.

An import writes heavily to the graph; an answer has to read from it while
someone waits. Answers register here, and background imports pause between
files while any answer is in progress (up to a limit, so a steady stream of
questions cannot stall an import forever).

The register is a table in the shared database, not process memory, because
imports usually run in another process (``python -m app.worker``) from the API
that answers. A row older than ``STALE_AFTER`` is ignored: an answer whose
process died must not hold imports back.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta

from app.core.database import connect, new_id, row, utcnow

STALE_AFTER = timedelta(minutes=10)
POLL_SECONDS = 0.25


@contextmanager
def answering() -> Iterator[None]:
    token = new_id("answer")
    # Registering is best-effort: a locked database must never fail an answer.
    with suppress(Exception), connect() as conn:
        conn.execute("INSERT INTO answer_activity (id, started_at) VALUES (?,?)", (token, utcnow()))
    try:
        yield
    finally:
        with suppress(Exception), connect() as conn:
            conn.execute("DELETE FROM answer_activity WHERE id=?", (token,))


def answers_in_progress() -> int:
    cutoff = (datetime.now(UTC) - STALE_AFTER).isoformat()
    found = row("SELECT COUNT(*) AS n FROM answer_activity WHERE started_at>=?", (cutoff,))
    return int((found or {}).get("n") or 0)


def wait_for_answers(max_seconds: float) -> float:
    """Block while answers are in progress, at most ``max_seconds``; returns the wait."""
    started = time.monotonic()
    deadline = started + max(0.0, max_seconds)
    while time.monotonic() < deadline:
        try:
            if not answers_in_progress():
                break
        except Exception:  # noqa: BLE001 - never stall an import on a read error
            break
        time.sleep(POLL_SECONDS)
    return time.monotonic() - started
