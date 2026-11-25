from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from urllib.parse import urlparse

import jwt
from starlette.requests import Request

from app.core.config import settings
from app.core.database import connect, utcnow

from .vault import OAuthTokenVault

FLOW_STATE_ISSUER = "orgmemory-oauth-flow"


def sign_flow_state(payload: dict) -> str:
    """An OAuth `state` that carries its own flow, cryptographically.

    The hosted deployment runs several stateless containers over per-container
    SQLite, so a flow row written by /start is invisible to /callback. Signing
    the flow into the state token (PKCE verifier included) makes the round
    trip portable across instances; the database tombstone still provides
    best-effort single-use inside one container.
    """
    payload = {
        **payload,
        "iss": FLOW_STATE_ISSUER,
        "aud": FLOW_STATE_ISSUER,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=10),
    }
    payload["nonce"] = token_urlsafe(8)
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def read_flow_state(state: str) -> dict | None:
    try:
        claims = jwt.decode(
            state,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=FLOW_STATE_ISSUER,
            audience=FLOW_STATE_ISSUER,
        )
    except jwt.PyJWTError:
        return None
    return claims


def _is_local_base(url: str) -> bool:
    return "localhost" in url or "127.0.0.1" in url


def public_base_url(request: Request) -> str:
    """The scheme://host the browser actually used to reach this deployment.

    Behind a proxy (Vercel rewrites, Compose fronting, cloud load balancers)
    the container sees an internal host, so the forwarded headers are the
    source of truth. An explicitly configured non-local ``PUBLIC_BASE_URL``
    always wins, which keeps signed redirects stable when a fixed domain is
    known.
    """
    configured = (settings.public_base_url or "").strip().rstrip("/")
    if configured and not _is_local_base(configured):
        return configured
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    proto = proto or request.url.scheme
    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = host or request.headers.get("host", "")
    if host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


DEFAULT_SIGNED_IN_PATH = "/workspace"


def safe_next_path(value: str | None, default: str = DEFAULT_SIGNED_IN_PATH) -> str:
    """Return ``value`` only when it is a same-origin relative path.

    ``next`` survives a sign-in round trip, so it must never become an open
    redirect: absolute URLs, scheme-relative ``//host`` paths, backslash
    tricks browsers normalise to ``//``, and control characters are refused.
    """
    candidate = (value or "").strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
        or len(candidate) > 2048
    ):
        return default
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default
    return candidate


def oauth_redirect_uri(request: Request, configured: str, callback_path: str) -> str:
    """The redirect_uri for one OAuth round trip.

    A configured production URL is used verbatim. A configured or default
    localhost value is replaced by the host the request actually arrived on,
    so a deployment works without baking its domain into the environment —
    while staying byte-identical between the /start and /callback legs,
    because both legs run on the same public host.
    """
    candidate = (configured or "").strip()
    if candidate and not _is_local_base(candidate):
        return candidate
    return public_base_url(request) + callback_path


def frontend_redirect(request: Request, path: str) -> str:
    """Redirect the browser to the frontend surface it came from."""
    configured = (settings.frontend_url or "").strip().rstrip("/")
    base = configured if configured and not _is_local_base(configured) else public_base_url(request)
    return f"{base}{path}"


class ConnectorSecrets(OAuthTokenVault):
    """Backward-compatible name for the production OAuth token vault."""


class OAuthStateStore:
    def create(
        self,
        provider: str,
        *,
        intent: str = "connect",
        workspace_id: str = "",
        user_id: str = "",
        use_pkce: bool = False,
        redirect_uri: str = "",
        next_path: str = "",
    ) -> dict[str, str]:
        state = sign_flow_state(
            {
                "p": provider,
                "i": intent,
                "w": workspace_id,
                "u": user_id,
                "v": token_urlsafe(64) if use_pkce else "",
                # Keep the callback byte-identical across the authorization
                # and token-exchange legs, including on stateless hosts.
                "redirect_uri": redirect_uri,
                # Where to land after sign-in; signed, and re-checked on use.
                "n": safe_next_path(next_path, ""),
            }
        )
        verifier = read_flow_state(state).get("v", "") if read_flow_state(state) else ""
        expires = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        with connect() as conn:
            conn.execute(
                "INSERT INTO oauth_flows VALUES (?,?,?,?,?,?,?,NULL)",
                (state, provider, intent, workspace_id, user_id, verifier, expires),
            )
        challenge = ""
        if verifier:
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return {
            "state": state,
            "code_challenge": challenge,
            "redirect_uri": redirect_uri,
        }

    def consume(self, provider: str, state: str) -> dict:
        # Portable path first: the signed state decodes on any container,
        # even one that never saw the /start request. A tombstone row (this
        # container or another that shares the file) makes it single-use.
        claims = read_flow_state(state)
        if claims:
            if self._state_is_used(state):
                raise ValueError("OAuth state is invalid or expired")
            self._tombstone(state)
            return {
                "state": state,
                "provider": claims.get("p", ""),
                "intent": claims.get("i", "connect"),
                "workspace_id": claims.get("w", ""),
                "user_id": claims.get("u", ""),
                "code_verifier": claims.get("v", ""),
                "redirect_uri": claims.get("redirect_uri", ""),
                "next_path": claims.get("n", ""),
                "expires_at": datetime.now(UTC).isoformat(),
            }
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_flows WHERE state=? AND provider=?",
                (state, provider),
            ).fetchone()
            if (
                not row
                or row["used_at"]
                or datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC)
            ):
                raise ValueError("OAuth state is invalid or expired")
            conn.execute("UPDATE oauth_flows SET used_at=? WHERE state=?", (utcnow(), state))
            return dict(row)

    def _state_is_used(self, state: str) -> bool:
        try:
            with connect() as conn:
                row = conn.execute(
                    "SELECT used_at FROM oauth_flows WHERE state=?", (state,)
                ).fetchone()
            return bool(row and row["used_at"])
        except Exception:  # noqa: BLE001 - a read failure must not block login
            return False

    def _tombstone(self, state: str) -> None:
        # Best-effort single-use marker. A second container replaying the
        # state is bounded by the provider's single-use authorization code,
        # so this only needs to be best-effort.
        try:
            now = utcnow()
            with connect() as conn:
                marked = conn.execute(
                    "UPDATE oauth_flows SET used_at=? WHERE state=? AND used_at IS NULL",
                    (now, state),
                ).rowcount
                if not marked:
                    conn.execute(
                        "INSERT OR IGNORE INTO oauth_flows VALUES (?,?,?,?,?,?,?,?)",
                        (state, "consumed", "", "", "", "", datetime.now(UTC).isoformat(), now),
                    )
        except Exception:  # noqa: BLE001 - tombstones must never fail a login
            pass
