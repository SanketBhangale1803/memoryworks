"""The background worker: ``python -m app.worker``.

Runs the slow half of MemoryWorks (see :mod:`app.background`) in its own
process, beside an API started with ``BACKGROUND_WORK_ENABLED=false``. Both
read the same database, so the API only records work and this process does it.
More than one worker may run: claiming a job is atomic, and a connector sync
job is leased before it runs.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from app import background
from app.api.routes import graph
from app.core.config import settings
from app.core.database import init_db
from app.core.logging import configure_logging

logger = logging.getLogger("app.worker")


async def run() -> None:
    settings.assert_safe_for_environment()
    configure_logging()
    init_db()
    with suppress(Exception):
        graph.initialize()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)
    tasks = background.start(sanitize=True)
    logger.info("MemoryWorks worker started (%d loops)", len(tasks))
    await stopping.wait()
    logger.info("MemoryWorks worker stopping")
    await background.stop(tasks)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
