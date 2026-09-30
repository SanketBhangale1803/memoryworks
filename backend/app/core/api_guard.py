"""Process-local API guard: request-body caps and sliding-window rate limits.

This is deliberately cheap and dependency-free. Behind a load balancer the
budget is per instance, which still caps the damage one runaway client can do
to a single worker; the durable connector queue has its own per-provider
pacing, so this layer only exists to shed abuse before it reaches handlers.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

# Paths excluded from rate limiting: cheap health/config surfaces that
# orchestrators and IDE extensions poll aggressively.
RATE_LIMIT_EXEMPT_PATHS = {
    "/api/health",
    "/api/health/graph",
    "/api/models",
    "/api/platforms",
    "/api/auth/providers",
}

_BODY_LIMIT_EXEMPT_PREFIXES = (
    # Large artifacts are streamed through python-multipart with its own
    # per-file caps; the guard only polices buffered JSON bodies.
    "/api/ingest/file",
    "/api/ingest/github",
)


class SlidingWindowLimiter:
    """Fixed-key sliding window. Bounded memory: stale buckets are evicted."""

    def __init__(self, max_keys: int = 10_000):
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._max_keys = max_keys

    def check(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        calls = self._calls[key]
        while calls and calls[0] <= now - window_seconds:
            calls.popleft()
        if len(calls) >= limit:
            retry_after = int(max(1, calls[0] + window_seconds - now))
            return False, retry_after
        if len(self._calls) > self._max_keys:
            self._evict(now, window_seconds)
        calls.append(now)
        return True, 0

    def _evict(self, now: float, window_seconds: int) -> None:
        stale = [
            key
            for key, calls in self._calls.items()
            if not calls or calls[-1] <= now - window_seconds
        ]
        for key in stale:
            del self._calls[key]
        if len(self._calls) > self._max_keys:
            self._calls.clear()


class APIGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):  # noqa: ANN001
        super().__init__(app)
        self._limiter = SlidingWindowLimiter()

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        path = request.url.path
        if not path.startswith("/api"):
            return await call_next(request)

        # 1. Body-size cap for buffered JSON payloads. A chunked body without a
        # declared length has no pre-readable size, so instead of buffering it
        # to count bytes we refuse it here; the streaming ingest endpoints
        # enforce their own caps while reading.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            size = int(declared)
            if size > settings.api_max_body_bytes and not path.startswith(
                _BODY_LIMIT_EXEMPT_PREFIXES
            ):
                return JSONResponse(
                    {"detail": "Request body exceeds the allowed size"},
                    status_code=413,
                )
        elif request.headers.get(
            "transfer-encoding", ""
        ).casefold() == "chunked" and not path.startswith(_BODY_LIMIT_EXEMPT_PREFIXES):
            return JSONResponse(
                {
                    "detail": "Chunked request bodies must declare Content-Length "
                    "or use the streaming ingest endpoints"
                },
                status_code=411,
            )

        # 2. Sliding-window rate limit per principal (or per client IP).
        if settings.api_rate_limit_enabled and path not in RATE_LIMIT_EXEMPT_PATHS:
            identity = self._identity(request)
            is_public = self._is_public(request)
            limit = (
                settings.api_rate_limit_public_per_minute
                if is_public
                else settings.api_rate_limit_per_minute
            )
            allowed, retry_after = self._limiter.check(identity, limit, 60)
            if not allowed:
                return JSONResponse(
                    {"detail": "Rate limit exceeded; slow down and retry"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)

    @staticmethod
    def _identity(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        if authorization:
            digest = hmac.new(
                settings.jwt_secret.encode(), authorization.encode(), hashlib.sha256
            ).hexdigest()[:32]
            return f"auth:{digest}"
        api_key = request.headers.get("x-api-key", "")
        if api_key:
            digest = hmac.new(
                settings.jwt_secret.encode(), api_key.encode(), hashlib.sha256
            ).hexdigest()[:32]
            return f"key:{digest}"
        cookie = request.cookies.get(settings.session_cookie_name, "")
        if cookie:
            digest = hmac.new(
                settings.jwt_secret.encode(), cookie.encode(), hashlib.sha256
            ).hexdigest()[:32]
            return f"cookie:{digest}"
        client = request.client.host if request.client else "unknown"
        return f"ip:{quote(client)}"

    @staticmethod
    def _is_public(request: Request) -> bool:
        """A request is 'public' when it carries no recognizable credential."""
        return not (
            request.headers.get("authorization")
            or request.headers.get("x-api-key")
            or request.cookies.get(settings.session_cookie_name)
        )
