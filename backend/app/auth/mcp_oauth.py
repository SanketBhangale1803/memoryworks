from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.app_auth import me_from_token
from app.auth.security import frontend_redirect
from app.core.config import settings
from app.core.database import connect, new_id, row, utcnow

oauth_router = APIRouter()

MCP_SCOPES = ("read", "write")
# A dynamically registered client may ask for every scope; what a token
# actually carries is still decided per authorization, by what was requested
# and approved on the consent screen.
DEFAULT_CLIENT_SCOPES = " ".join(MCP_SCOPES)
_CONSENT_PURPOSE = "mcp_oauth_consent"
_SCOPE_DESCRIPTIONS = {
    "read": "Read memories, answers, briefings, and sources in this workspace",
    "write": (
        "Record outcomes, propose memories, and start imports. Changes to memory and "
        "connector writes still wait for a person to approve them"
    ),
}


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
    allowed_scopes = sorted(set(scopes) & set(MCP_SCOPES))
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
        "scopes_supported": list(MCP_SCOPES),
    }


@oauth_router.get("/.well-known/oauth-protected-resource")
def oauth_resource_metadata():
    return {
        "resource": settings.mcp_public_url.rstrip("/") + "/mcp",
        "authorization_servers": [settings.mcp_oauth_issuer_url.rstrip("/")],
        "scopes_supported": list(MCP_SCOPES),
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
            str(payload.get("scope") or DEFAULT_CLIENT_SCOPES).split(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _authorization_request(
    *,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
) -> tuple[dict[str, Any], list[str]]:
    """Validate an authorization request; return the client and granted scopes.

    The granted scopes are the requested ones the client is registered for
    (RFC 6749 §3.3 lets the server issue fewer than asked). ``read`` must be
    among them: every MCP session reads.
    """
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
    requested = set((scope or "read").split())
    allowed = set(json.loads(client["allowed_scopes_json"]))
    granted = sorted(requested & allowed & set(MCP_SCOPES))
    if "read" not in granted:
        raise HTTPException(400, "Requested OAuth scopes are not allowed")
    return client, granted


def _consent_token(session: dict[str, Any], fields: dict[str, str]) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "purpose": _CONSENT_PURPOSE,
            "sub": session["id"],
            "workspace_id": session["active_workspace_id"],
            **fields,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _redirect_with(redirect_uri: str, params: dict[str, str]) -> RedirectResponse:
    return RedirectResponse(
        redirect_uri + ("&" if "?" in redirect_uri else "?") + urlencode(params),
        status_code=302,
    )


_CONSENT_HEADERS = {
    # The consent decision must never be clickjacked or cached.
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}


def _consent_page(
    *, client_name: str, redirect_uri: str, scopes: list[str], email: str, token: str
) -> HTMLResponse:
    host = urlparse(redirect_uri).netloc or redirect_uri
    items = "".join(
        f"<li><strong>{html.escape(name)}</strong> — {html.escape(_SCOPE_DESCRIPTIONS[name])}</li>"
        for name in scopes
    )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize {html.escape(client_name)} · MemoryWorks</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0b0f;color:#ececf1;
font:15px/1.5 system-ui,-apple-system,sans-serif;padding:16px;box-sizing:border-box}}
main{{max-width:440px;width:100%;background:#15151c;border:1px solid #2a2a35;border-radius:14px;padding:28px}}
h1{{font-size:20px;margin:0 0 8px}} p,li{{color:#b9b9c6}} ul{{padding-left:18px}}
code{{color:#ececf1}} .row{{display:flex;gap:10px;margin-top:20px}}
button{{flex:1;padding:10px 14px;border-radius:10px;border:1px solid #3a3a48;background:#1d1d26;
color:#ececf1;font:inherit;cursor:pointer}} button.allow{{background:#8b5cf6;border-color:#8b5cf6;color:#fff}}
</style></head><body><main>
<h1>Allow {html.escape(client_name)} to use MemoryWorks?</h1>
<p>Signed in as <code>{html.escape(email)}</code>. After you allow it, MemoryWorks sends
you back to <code>{html.escape(host)}</code>.</p>
<p>It will be able to:</p><ul>{items}</ul>
<form method="post" action="/oauth/authorize">
<input type="hidden" name="consent" value="{html.escape(token)}">
<div class="row">
<button type="submit" name="decision" value="deny">Cancel</button>
<button type="submit" name="decision" value="allow" class="allow">Allow</button>
</div></form>
<p><small>Only allow connectors you started yourself. You can revoke access at any time.</small></p>
</main></body></html>"""
    return HTMLResponse(body, headers=_CONSENT_HEADERS)


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
    client, granted = _authorization_request(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
    )
    session = me_from_token(request.cookies.get(settings.session_cookie_name, ""))
    if not session:
        # A relative path: it returns to this same origin whatever proxy or
        # scheme the request came through, and the login page only follows
        # same-origin paths.
        return_to = "/oauth/authorize?" + request.url.query
        return RedirectResponse(
            frontend_redirect(
                request,
                "/login?"
                + urlencode({"next": return_to, "error": "Sign in to authorize this connector"}),
            )
        )
    # Never approve silently: a client anyone can register must not turn one
    # click on a crafted link into a token for the signed-in person's workspace.
    token = _consent_token(
        session,
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(granted),
            "code_challenge": code_challenge,
            "state": state,
        },
    )
    return _consent_page(
        client_name=client["name"] or "An MCP client",
        redirect_uri=redirect_uri,
        scopes=granted,
        email=session.get("email") or "",
        token=token,
    )


@oauth_router.post("/oauth/authorize")
def approve_mcp_client(
    request: Request,
    consent: str = Form(...),
    decision: str = Form(...),
):
    session = me_from_token(request.cookies.get(settings.session_cookie_name, ""))
    if not session:
        raise HTTPException(401, "Sign in to authorize this connector")
    try:
        claims = jwt.decode(consent, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "This authorization request expired. Start again.") from exc
    if (
        claims.get("purpose") != _CONSENT_PURPOSE
        or claims.get("sub") != session["id"]
        or claims.get("workspace_id") != session["active_workspace_id"]
    ):
        raise HTTPException(400, "This authorization request belongs to another session")
    client_id, redirect_uri = str(claims["client_id"]), str(claims["redirect_uri"])
    # Re-validate: the client may have been revoked since the page was shown.
    _, granted = _authorization_request(
        response_type="code",
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=str(claims["code_challenge"]),
        code_challenge_method="S256",
        scope=str(claims["scope"]),
    )
    state = str(claims.get("state") or "")
    if decision != "allow":
        params = {"error": "access_denied"}
        if state:
            params["state"] = state
        return _redirect_with(redirect_uri, params)
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
                json.dumps(granted),
                str(claims["code_challenge"]),
                (now + timedelta(minutes=10)).isoformat(),
                now.isoformat(),
            ),
        )
    params = {"code": raw_code}
    if state:
        params["state"] = state
    return _redirect_with(redirect_uri, params)


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
