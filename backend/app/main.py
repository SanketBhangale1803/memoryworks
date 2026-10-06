from __future__ import annotations

from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    # Imports, syncs, webhooks, watches, and the backfill run here only when
    # this process also does background work; otherwise `python -m app.worker`
    # runs them beside the API (see app.background).
    tasks = background.start(sanitize=True) if settings.background_work_enabled else []
    yield
    await background.stop(tasks)


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
