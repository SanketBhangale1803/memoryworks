from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.org_routes import org_router, watches
from app.api.routes import company_memory, connector_sync, graph, hcag, ingestion, router
from app.auth.app_auth import (
    PUBLIC_DEMO_IDENTITIES,
    PUBLIC_DEMO_WORKSPACE_ID,
    ensure_public_demo_identity,
)
from app.auth.mcp_oauth import oauth_router
from app.auth.middleware import AuthenticationBoundaryMiddleware
from app.core.api_guard import APIGuardMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.core.logging import configure_logging
from app.ingestion.maintenance import sanitize_existing_index
from app.orgops.seed import seed_launch_scenario


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.assert_safe_for_environment()
    configure_logging()
    init_db()
    with suppress(Exception):
        graph.initialize()
    if settings.public_demo_mode:
        # Every serverless instance has its own disposable SQLite database.
        # Recreate the same deterministic demo identities and fixture at cold
        # start so a signed session remains useful after load balancing.
        for identity, (_, display_name) in PUBLIC_DEMO_IDENTITIES.items():
            ensure_public_demo_identity(identity, display_name)
        seed_launch_scenario(
            PUBLIC_DEMO_WORKSPACE_ID,
            ingestion.create_project,
            company_memory,
        )
    with suppress(Exception):
        sanitize_existing_index(graph, hcag)
    # Legacy-vector migration can touch thousands of chunks. Run it in a
    # background worker so health checks and the read path become available
    # immediately; mixed-model chunks remain safe because dense scoring only
    # compares vectors produced by the active model.
    backfill_task = asyncio.create_task(asyncio.to_thread(hcag.backfill_existing_chunks))
    sync_worker_task = None
    if settings.connector_sync_worker_enabled:

        async def run_connector_sync_worker() -> None:
            # The worker outlives any single failed job: a raised error (a
            # transient DB lock, a broken connector) must not kill the loop,
            # or the whole deployment silently stops syncing.
            while True:
                try:
                    await asyncio.to_thread(connector_sync.run_once)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "connector sync worker iteration failed: %s", exc
                    )
                await asyncio.sleep(max(1, settings.connector_sync_poll_seconds))

        sync_worker_task = asyncio.create_task(run_connector_sync_worker())

    # Standing watches are the autonomous half of the product: the same
    # organizational checks, run without anyone asking. They only ever record a
    # finding and draft a plan — applying one still needs a person.
    async def run_watch_worker() -> None:
        while True:
            with suppress(Exception):
                await asyncio.to_thread(watches.run_due)
            await asyncio.sleep(max(30, settings.org_watch_poll_seconds))

    watch_worker_task = asyncio.create_task(run_watch_worker())
    yield
    if not backfill_task.done():
        backfill_task.cancel()
    if sync_worker_task and not sync_worker_task.done():
        sync_worker_task.cancel()
    if not watch_worker_task.done():
        watch_worker_task.cancel()


app = FastAPI(title="MemoryWorks API", version="1.0.0", lifespan=lifespan)
allowed_origins = [settings.frontend_url.rstrip("/")]
if settings.environment.casefold() != "production":
    # Browsers treat localhost and 127.0.0.1 as different origins. Accept both
    # during local development so a loopback URL cannot make every API request
    # look like a network failure. Production remains restricted to FRONTEND_URL.
    for loopback_origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        if loopback_origin not in allowed_origins:
            allowed_origins.append(loopback_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthenticationBoundaryMiddleware)
# The API guard is the outermost layer: abuse is shed before authentication
# work happens. Rate limits are per instance, per principal (or client IP).
app.add_middleware(APIGuardMiddleware)
app.include_router(router)
app.include_router(org_router)
app.include_router(oauth_router)


@app.get("/")
def root():
    return {
        "name": "MemoryWorks",
        "tagline": "The persistent memory layer for company AI agents.",
        "docs": "/docs",
    }
