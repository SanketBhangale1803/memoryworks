from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.app_auth import me_from_token
from app.core.config import settings
from app.core.database import connect, new_id, row, utcnow

oauth_router = APIRouter()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _validate_redirect_uri(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("OAuth redirect URIs must use HTTPS or an explicit loopback host")


def register_mcp_client(
    name: str,
    redirect_uris: list[str],
    scopes: list[str],
    *,
    created_by: str = "",
) -> dict[str, Any]:
    allowed_scopes = sorted(set(scopes) & {"read", "write"})
    if not allowed_scopes or "read" not in allowed_scopes:
        raise ValueError("MCP clients require at least the read scope")
    for uri in redirect_uris:
        _validate_redirect_uri(uri)
    client_id = f"om_mcp_{secrets.token_urlsafe(18)}"
    record_id, now = new_id("mcpclient"), utcnow()
    with connect() as conn:
        conn.execute(
            """INSERT INTO mcp_oauth_clients
            (id,client_id,name,client_secret_hash,redirect_uris_json,
             allowed_scopes_json,created_by,created_at,revoked_at)
            VALUES (?,?,?,'',?,?,?,?,NULL)""",
            (
                record_id,
                client_id,
                name.strip(),
                json.dumps(redirect_uris),
                json.dumps(allowed_scopes),
                created_by,
                now,
            ),
        )
    return {
        "client_id": client_id,
        "client_name": name.strip(),
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": " ".join(allowed_scopes),
    }


def issue_access_token(
    *, client_id: str, user_id: str, workspace_id: str, scopes: list[str]
) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.mcp_oauth_access_token_minutes)
    token = jwt.encode(
        {
            "iss": settings.mcp_oauth_issuer_url.rstrip("/"),
            "aud": settings.mcp_public_url.rstrip("/") + "/mcp",
            "sub": user_id,
            "workspace_id": workspace_id,
            "client_id": client_id,
            "scope": " ".join(sorted(set(scopes))),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": new_id("mcptoken"),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return token, int((expires - now).total_seconds())


def decode_mcp_access_token(token: str) -> dict[str, Any] | None:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.mcp_public_url.rstrip("/") + "/mcp",
            issuer=settings.mcp_oauth_issuer_url.rstrip("/"),
        )
    except jwt.PyJWTError:
        return None
    if not claims.get("sub") or not claims.get("workspace_id"):
        return None
    claims["scopes"] = str(claims.get("scope") or "").split()
    return claims


def principal_from_mcp_token(token: str) -> dict[str, Any] | None:
    claims = decode_mcp_access_token(token)
    if not claims:
        return None
    membership = row(
        """SELECT wm.role,u.email,u.display_name FROM workspace_members wm
        JOIN users u ON u.id=wm.user_id
        WHERE wm.workspace_id=? AND wm.user_id=? AND wm.status='active'""",
        (claims["workspace_id"], claims["sub"]),
    )
    if not membership:
        return None
    return {
        "id": claims["sub"],
        "email": membership["email"],
        "display_name": membership["display_name"],
        "active_workspace_id": claims["workspace_id"],
        "role": membership["role"],
        "auth_type": "mcp_oauth",
        "oauth_client_id": claims.get("client_id", ""),
        "oauth_scopes": claims["scopes"],
    }


@oauth_router.get("/.well-known/oauth-authorization-server")
def oauth_server_metadata():
    issuer = settings.mcp_oauth_issuer_url.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/oauth/authorize",
        "token_endpoint": issuer + "/oauth/token",
        "revocation_endpoint": issuer + "/oauth/revoke",
        "registration_endpoint": issuer + "/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["read", "write"],
    }


@oauth_router.get("/.well-known/oauth-protected-resource")
def oauth_resource_metadata():
    return {
        "resource": settings.mcp_public_url.rstrip("/") + "/mcp",
        "authorization_servers": [settings.mcp_oauth_issuer_url.rstrip("/")],
        "scopes_supported": ["read", "write"],
        "bearer_methods_supported": ["header"],
    }


@oauth_router.post("/oauth/register")
async def dynamic_client_registration(request: Request):
    if not settings.mcp_oauth_enable_dcr:
        raise HTTPException(403, "Dynamic client registration is disabled")
    payload = await request.json()
    try:
        return register_mcp_client(
            str(payload.get("client_name") or "MCP client"),
            list(payload.get("redirect_uris") or []),
            str(payload.get("scope") or "read").split(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@oauth_router.get("/oauth/authorize")
def authorize_mcp_client(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    scope: str = "read",
    state: str = "",
):
    if response_type != "code" or code_challenge_method != "S256" or not code_challenge:
        raise HTTPException(400, "Authorization code flow with PKCE S256 is required")
    client = row(
        "SELECT * FROM mcp_oauth_clients WHERE client_id=? AND revoked_at IS NULL",
        (client_id,),
    )
    if not client:
        raise HTTPException(400, "Unknown or revoked OAuth client")
    if redirect_uri not in json.loads(client["redirect_uris_json"]):
        raise HTTPException(400, "redirect_uri is not registered for this client")
    requested_scopes = sorted(set(scope.split()))
    allowed_scopes = set(json.loads(client["allowed_scopes_json"]))
    if not set(requested_scopes).issubset(allowed_scopes) or "read" not in requested_scopes:
        raise HTTPException(400, "Requested OAuth scopes are not allowed")
    session = me_from_token(request.cookies.get(settings.session_cookie_name, ""))
    if not session:
        return_to = str(request.url)
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}/login?"
            + urlencode({"next": return_to, "error": "Sign in to authorize this connector"})
        )
    raw_code = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with connect() as conn:
        conn.execute(
            """INSERT INTO mcp_oauth_codes
            (code_hash,client_id,user_id,workspace_id,redirect_uri,scopes_json,
             code_challenge,expires_at,used_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,NULL,?)""",
            (
                _hash(raw_code),
                client_id,
                session["id"],
                session["active_workspace_id"],
                redirect_uri,
                json.dumps(requested_scopes),
                code_challenge,
                (now + timedelta(minutes=10)).isoformat(),
                now.isoformat(),
            ),
        )
    params = {"code": raw_code}
    if state:
        params["state"] = state
    return RedirectResponse(
        redirect_uri + ("&" if "?" in redirect_uri else "?") + urlencode(params)
    )


@oauth_router.post("/oauth/token")
def exchange_mcp_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
):
    client = row(
        "SELECT * FROM mcp_oauth_clients WHERE client_id=? AND revoked_at IS NULL",
        (client_id,),
    )
    if not client:
        raise HTTPException(401, "invalid_client")
    if grant_type == "authorization_code":
        authorization = row("SELECT * FROM mcp_oauth_codes WHERE code_hash=?", (_hash(code),))
        if (
            not authorization
            or authorization["used_at"]
            or authorization["client_id"] != client_id
            or authorization["redirect_uri"] != redirect_uri
            or datetime.fromisoformat(authorization["expires_at"]) < datetime.now(UTC)
        ):
            raise HTTPException(400, "invalid_grant")
        if (
            _b64url(hashlib.sha256(code_verifier.encode()).digest())
            != authorization["code_challenge"]
        ):
            raise HTTPException(400, "invalid_grant")
        with connect() as conn:
            conn.execute(
                "UPDATE mcp_oauth_codes SET used_at=? WHERE code_hash=?",
                (utcnow(), authorization["code_hash"]),
            )
        user_id, workspace_id = authorization["user_id"], authorization["workspace_id"]
        scopes = json.loads(authorization["scopes_json"])
    elif grant_type == "refresh_token":
        current = row(
            "SELECT * FROM mcp_refresh_tokens WHERE token_hash=?", (_hash(refresh_token),)
        )
        if (
            not current
            or current["revoked_at"]
            or current["client_id"] != client_id
            or datetime.fromisoformat(current["expires_at"]) < datetime.now(UTC)
        ):
            raise HTTPException(400, "invalid_grant")
        user_id, workspace_id = current["user_id"], current["workspace_id"]
        scopes = json.loads(current["scopes_json"])
        with connect() as conn:
            conn.execute(
                "UPDATE mcp_refresh_tokens SET revoked_at=? WHERE token_hash=?",
                (utcnow(), current["token_hash"]),
            )
    else:
        # Service/client-credential grants are intentionally unsupported: every
        # MCP identity must resolve to a delegated MemoryWorks user.
        raise HTTPException(400, "unsupported_grant_type")

    access_token, expires_in = issue_access_token(
        client_id=client_id, user_id=user_id, workspace_id=workspace_id, scopes=scopes
    )
    new_refresh = secrets.token_urlsafe(40)
    now = datetime.now(UTC)
    with connect() as conn:
        conn.execute(
            """INSERT INTO mcp_refresh_tokens
            (token_hash,client_id,user_id,workspace_id,scopes_json,expires_at,
             created_at,revoked_at,rotated_to_hash)
            VALUES (?,?,?,?,?,?,?,NULL,NULL)""",
            (
                _hash(new_refresh),
                client_id,
                user_id,
                workspace_id,
                json.dumps(scopes),
                (now + timedelta(days=settings.mcp_oauth_refresh_token_days)).isoformat(),
                now.isoformat(),
            ),
        )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": new_refresh,
        "scope": " ".join(scopes),
    }


@oauth_router.post("/oauth/revoke")
def revoke_mcp_token(token: str = Form(...)):
    with connect() as conn:
        conn.execute(
            "UPDATE mcp_refresh_tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (utcnow(), _hash(token)),
        )
    return {}


@oauth_router.post("/api/oauth/introspect")
def introspect_mcp_token(request: Request):
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    claims = decode_mcp_access_token(token)
    if not claims:
        return {"active": False}
    return {
        "active": True,
        "client_id": claims.get("client_id", ""),
        "sub": claims["sub"],
        "workspace_id": claims["workspace_id"],
        "scope": claims.get("scope", ""),
        "exp": claims.get("exp"),
        "aud": claims.get("aud"),
    }
