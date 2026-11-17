from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import background
from app.api.org_routes import org_router
from app.api.routes import company_memory, graph, ingestion, router
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
from app.graph.arcade_client import ArcadeDBUnavailable
from app.orgops.seed import seed_launch_scenario

logger = logging.getLogger(__name__)


def _warm_indexes() -> None:
    warm = getattr(graph, "warm_indexes", None)  # the ArcadeDB store only
    if warm:
        with suppress(Exception):
            warm()


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
    # Imports, syncs, webhooks, watches, and the backfill run here only when
    # this process also does background work; otherwise `python -m app.worker`
    # runs them beside the API (see app.background).
    tasks = background.start(sanitize=True) if settings.background_work_enabled else []
    # Answers rank from per-project indexes held in this process; build them
    # before the first question instead of during it.
    threading.Thread(target=_warm_indexes, daemon=True, name="warm-indexes").start()
    yield
    await background.stop(tasks)


api = FastAPI(title="MemoryWorks API", version="1.0.0", lifespan=lifespan)
allowed_origins = [settings.frontend_url.rstrip("/")]
if settings.environment.casefold() != "production":
    # Browsers treat localhost and 127.0.0.1 as different origins. Accept both
    # during local development so a loopback URL cannot make every API request
    # look like a network failure. Production remains restricted to FRONTEND_URL.
    for loopback_origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        if loopback_origin not in allowed_origins:
            allowed_origins.append(loopback_origin)

api.add_middleware(AuthenticationBoundaryMiddleware)
# Shed abuse before authentication. Rate limits are per instance, per
# principal (or client IP).
api.add_middleware(APIGuardMiddleware)
api.include_router(router)
api.include_router(org_router)
api.include_router(oauth_router)


@api.exception_handler(ArcadeDBUnavailable)
async def graph_unavailable(_: Request, exc: ArcadeDBUnavailable) -> JSONResponse:
    logger.error("Could not reach the graph database", exc_info=exc)
    return JSONResponse(
        {"detail": "The graph database is not reachable. Check that ArcadeDB is running."},
        status_code=503,
    )


@api.exception_handler(Exception)
async def internal_error(_: Request, __: Exception) -> JSONResponse:
    # ServerErrorMiddleware re-raises after sending this response so Uvicorn
    # can log the original exception.
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@api.get("/")
def root():
    return {
        "name": "MemoryWorks",
        "tagline": "The persistent memory layer for company AI agents.",
        "docs": "/docs",
    }


# Wrap the entire ASGI app so even responses from ServerErrorMiddleware
# go through the same CORS policy as ordinary responses.
app = CORSMiddleware(
    api,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
