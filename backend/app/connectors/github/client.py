from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from app.auth import ConnectorSecrets
from app.auth.vault import access_expired, expires_at_from
from app.connectors.base import (
    Connector,
    ConnectorAccount,
    ConnectorCapabilityError,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorResource,
    ConnectorTool,
    DataPolicy,
    ExecutionMode,
    OAuthConfig,
    RateLimitPolicy,
    RetryPolicy,
    SyncBatch,
    SyncOperation,
    SyncRecord,
    ToolKind,
    WebhookEvent,
    WebhookRequest,
    WebhookSubscription,
)
from app.core.config import settings

_GITHUB_MANIFEST = ConnectorManifest(
    id="github",
    name="GitHub",
    icon="github",
    version="1.0.0",
    execution_mode=ExecutionMode.CLOUD,
    oauth=OAuthConfig(
        authorization_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scopes=("repo", "read:org", "read:user", "user:email"),
        pkce_required=True,
    ),
    resources=(
        ConnectorResource("repository", "Repositories"),
        ConnectorResource("commit", "Commits"),
        ConnectorResource("issue", "Issues"),
        ConnectorResource("pull_request", "Pull requests"),
    ),
    tools=(
        ConnectorTool(
            "list_repositories",
            "List repositories visible to the delegated GitHub user.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "get_repository",
            "Get repository metadata visible to the delegated GitHub user.",
            ToolKind.READ,
        ),
        ConnectorTool(
            "search_issues",
            "Search issues visible to the delegated GitHub user.",
            ToolKind.READ,
        ),
    ),
    webhooks=(
        WebhookSubscription("push", "Repository push", "x-hub-signature-256"),
        WebhookSubscription("pull_request", "Pull request change", "x-hub-signature-256"),
        WebhookSubscription("issues", "Issue change", "x-hub-signature-256"),
    ),
    rate_limit=RateLimitPolicy(requests=4_500, window_seconds=3_600, burst=20),
    retry=RetryPolicy(max_attempts=6, base_delay_seconds=2, max_delay_seconds=300),
    data_policy=DataPolicy(
        residency="MemoryWorks workspace region",
        retention="Until source disconnect or workspace retention policy",
    ),
    package="orgmemory.connector.github",
)
GITHUB_MANIFEST = replace(_GITHUB_MANIFEST, signature=_GITHUB_MANIFEST.digest())


class GitHubConnector(Connector):
    manifest = GITHUB_MANIFEST

    def __init__(self, secrets: ConnectorSecrets | None = None):
        self.secrets = secrets or ConnectorSecrets()

    def token(self) -> str | None:
        account = self.secrets.account("github")
        if account and account.access_token:
            # GitHub App user tokens last eight hours; OAuth App tokens never
            # expire and carry no expiry, so this only fires for the former.
            if access_expired(account):
                return self._renew() or account.access_token
            return account.access_token
        return settings.github_token if not self.secrets.workspace_id else None

    def _renew(self) -> str | None:
        if not self.secrets.workspace_id or not self.secrets.user_id:
            return None
        renewed = self.secrets.renew("github", _refresh_exchange)
        return renewed.access_token if renewed else None

    def connection_statuses(self) -> list[dict[str, Any]]:
        return self.secrets.status(self.manifest.id)

    def oauth_configured(self) -> bool:
        return bool(settings.github_client_id and settings.github_client_secret)

    def authorize(self, user: dict[str, Any], scopes: list[str]) -> str:
        flow = user.get("flow") or user
        wanted = " ".join(scopes or list(self.manifest.oauth.scopes if self.manifest.oauth else ()))
        return self.oauth_url(
            flow,
            scopes=wanted,
            redirect_uri=str(flow.get("redirect_uri") or ""),
        )

    def complete_authorization(self, code: str, flow: dict[str, Any]) -> dict[str, Any]:
        return self.complete_oauth(code, flow)

    def discover(self, account: ConnectorAccount | None = None) -> list[dict[str, Any]]:
        return self.list_repositories()

    def sync(self, account: ConnectorAccount, cursor: dict[str, Any] | None = None) -> SyncBatch:
        cursor = dict(cursor or {})
        repository = str(cursor.get("repository") or "")
        if not repository:
            records = tuple(
                SyncRecord(
                    id=f"github-repository:{item.get('id')}",
                    resource_type="repository",
                    operation=SyncOperation.UPSERT,
                    version=str(item.get("pushed_at") or item.get("updated_at") or ""),
                    title=str(item.get("full_name") or item.get("name") or ""),
                    content=str(item.get("description") or ""),
                    source_url=str(item.get("html_url") or ""),
                    updated_at=str(item.get("updated_at") or ""),
                    metadata={
                        "repository": item.get("full_name"),
                        "clone_url": item.get("clone_url"),
                        "default_branch": item.get("default_branch"),
                        "private": bool(item.get("private")),
                    },
                )
                for item in self.list_repositories()
            )
            return SyncBatch(records, {"synced_at": datetime.now(UTC).isoformat()})

        previous_sha = str(cursor.get("last_commit_sha") or "")
        commits = self.recent_commits(repository, 100)
        fresh: list[dict[str, Any]] = []
        for commit in commits:
            if str(commit.get("sha") or "") == previous_sha:
                break
            fresh.append(commit)
        records = tuple(
            SyncRecord(
                id=f"commit-source:{repository}:{item.get('sha')}",
                resource_type="commit",
                operation=SyncOperation.UPSERT,
                version=str(item.get("sha") or ""),
                title=str(((item.get("commit") or {}).get("message") or "").splitlines()[0]),
                content="\n".join(
                    (
                        f"Repository: {repository}",
                        f"Commit SHA: {item.get('sha') or ''}",
                        "Author: "
                        + str(
                            (item.get("author") or {}).get("login")
                            or (((item.get("commit") or {}).get("author") or {}).get("name"))
                            or "unknown"
                        ),
                        "Committed at: "
                        + str((((item.get("commit") or {}).get("author") or {}).get("date")) or ""),
                        f"Message: {(item.get('commit') or {}).get('message') or ''}",
                    )
                ),
                source_url=str(item.get("html_url") or ""),
                updated_at=str(((item.get("commit") or {}).get("author") or {}).get("date") or ""),
                metadata={"repository": repository, "sha": item.get("sha")},
            )
            for item in reversed(fresh)
        )
        next_cursor = {
            **cursor,
            "repository": repository,
            "last_commit_sha": (
                str(commits[0].get("sha") or previous_sha) if commits else previous_sha
            ),
            "synced_at": datetime.now(UTC).isoformat(),
        }
        return SyncBatch(records, next_cursor)

    def search(self, account: ConnectorAccount, query: str, **filters: Any) -> list[dict[str, Any]]:
        repository = str(filters.get("repository") or "")
        qualifier = f" repo:{repository}" if repository else ""
        result = self._api("GET", f"/search/issues?q={urlencode({'q': query + qualifier})[2:]}")
        return list(result.get("items") or [])

    def execute(
        self,
        account: ConnectorAccount,
        action: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        tool = self.manifest.tool(action)
        if tool.kind == ToolKind.WRITE:
            raise ConnectorCapabilityError("The built-in GitHub connector is read-only")
        if action == "list_repositories":
            return {"repositories": self.list_repositories()}
        if action == "get_repository":
            return self.repository(str(arguments.get("repository") or ""))
        if action == "search_issues":
            return {
                "items": self.search(
                    account,
                    str(arguments.get("query") or ""),
                    repository=arguments.get("repository") or "",
                )
            }
        raise ConnectorCapabilityError(f"Unsupported GitHub action {action!r}")

    def handle_webhook(self, request: WebhookRequest) -> WebhookEvent:
        if not settings.github_webhook_secret:
            raise ValueError("GitHub webhook verification is not configured")
        expected = (
            "sha256="
            + hmac.new(
                settings.github_webhook_secret.encode(), request.body, hashlib.sha256
            ).hexdigest()
        )
        signature = request.headers.get("x-hub-signature-256", "")
        if not signature or not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid GitHub webhook signature")
        payload = json.loads(request.body)
        repository = str((payload.get("repository") or {}).get("full_name") or "")
        delivery_id = (
            request.headers.get("x-github-delivery") or hashlib.sha256(request.body).hexdigest()
        )
        after = str(
            ((payload.get("pull_request") or {}).get("head") or {}).get("sha")
            or payload.get("after")
            or ""
        )
        return WebhookEvent(
            delivery_id=delivery_id,
            event_type=request.headers.get("x-github-event", "push"),
            resource_id=repository,
            cursor={"repository": repository, "webhook_after": after},
        )

    def revoke(self, account: ConnectorAccount) -> None:
        self.secrets.disconnect(self.manifest.id)

    def health(self, account: ConnectorAccount | None = None) -> ConnectorHealth:
        if not self.token():
            return ConnectorHealth(self.manifest.id, "disconnected", datetime.now(UTC).isoformat())
        started = time.monotonic()
        try:
            self._api("GET", "/user")
            return ConnectorHealth(
                self.manifest.id,
                "healthy",
                datetime.now(UTC).isoformat(),
                int((time.monotonic() - started) * 1_000),
            )
        except Exception as exc:
            return ConnectorHealth(
                self.manifest.id,
                "degraded",
                datetime.now(UTC).isoformat(),
                int((time.monotonic() - started) * 1_000),
                str(exc),
            )

    def oauth_url(
        self,
        flow: dict[str, str],
        scopes: str = "repo read:org read:user user:email",
        redirect_uri: str = "",
    ) -> str:
        if not settings.github_client_id:
            raise ValueError("GitHub OAuth is not configured")
        params = {
            "client_id": settings.github_client_id,
            "redirect_uri": redirect_uri or settings.github_redirect_uri,
            "scope": scopes,
            "state": flow["state"],
            "prompt": "select_account",
        }
        if flow.get("code_challenge"):
            params.update(
                {"code_challenge": flow["code_challenge"], "code_challenge_method": "S256"}
            )
        return "https://github.com/login/oauth/authorize?" + urlencode(params)

    def complete_oauth(self, code: str, flow: dict, redirect_uri: str = "") -> dict:
        exchange = {
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
            "redirect_uri": redirect_uri
            or flow.get("redirect_uri")
            or settings.github_redirect_uri,
        }
        if flow.get("code_verifier"):
            exchange["code_verifier"] = flow["code_verifier"]
        response = httpx.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data=exchange,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            error = str(payload.get("error") or "unknown_error")
            raise ValueError(f"GitHub OAuth token exchange failed: {error}")
        user = self._api("GET", "/user", token=token)
        email = user.get("email")
        if not email:
            emails = self._api("GET", "/user/emails", token=token)
            primary = next((item for item in emails if item.get("primary")), None)
            verified = next((item for item in emails if item.get("verified")), None)
            email = (primary or verified or {}).get("email")
        email = email or f"{user['login']}@users.noreply.github.com"
        return {
            "token": token,
            "external_id": str(user["id"]),
            "login": user["login"],
            "display_name": user.get("name") or user["login"],
            "email": email,
            "avatar_url": user.get("avatar_url", ""),
            "scope": payload.get("scope", ""),
            # Present only for GitHub Apps with expiring user tokens.
            "refresh_token": payload.get("refresh_token", ""),
            "expires_at": expires_at_from(payload),
        }

    def list_repositories(self) -> list[dict[str, Any]]:
        return self._api_all(
            "/user/repos?per_page=100&sort=updated&visibility=all&affiliation=owner,collaborator,organization_member"
        )

    def repository(self, slug: str) -> dict[str, Any]:
        return self._api("GET", f"/repos/{slug}")

    def recent_commits(self, slug: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._api("GET", f"/repos/{slug}/commits?per_page={min(max(limit, 1), 100)}")

    def commit(self, slug: str, sha: str) -> dict[str, Any]:
        return self._api("GET", f"/repos/{slug}/commits/{sha}")

    def compare(self, slug: str, before: str, after: str) -> dict[str, Any]:
        return self._api("GET", f"/repos/{slug}/compare/{before}...{after}")

    def list_issues(self, slug: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self._api_all(f"/repos/{slug}/issues?state=all&per_page=100")
            if "pull_request" not in item
        ]

    def issue_comments(self, slug: str, number: int) -> list[dict[str, Any]]:
        return self._api_all(f"/repos/{slug}/issues/{number}/comments?per_page=100")

    def list_pull_requests(self, slug: str) -> list[dict[str, Any]]:
        return self._api_all(f"/repos/{slug}/pulls?state=all&per_page=100")

    def pull_request_files(self, slug: str, number: int) -> list[dict[str, Any]]:
        return self._api("GET", f"/repos/{slug}/pulls/{number}/files?per_page=100")

    def pull_request_review_comments(self, slug: str, number: int) -> list[dict[str, Any]]:
        return self._api_all(f"/repos/{slug}/pulls/{number}/comments?per_page=100")

    def pull_request_reviews(self, slug: str, number: int) -> list[dict[str, Any]]:
        return self._api_all(f"/repos/{slug}/pulls/{number}/reviews?per_page=100")

    def clone(self, source: str, target: Path) -> None:
        token = self.token()
        env = None
        askpass = None
        if token:
            askpass = target.parent / ".runbook-askpass.sh"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in *Username*) echo x-access-token;; *) echo "$RUNBOOK_GITHUB_TOKEN";; esac\n'
            )
            askpass.chmod(0o700)
            env = {
                **os.environ,
                "GIT_ASKPASS": str(askpass),
                "GIT_TERMINAL_PROMPT": "0",
                "RUNBOOK_GITHUB_TOKEN": token,
            }
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", source, str(target)],
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
        finally:
            if askpass:
                askpass.unlink(missing_ok=True)
        if result.returncode:
            raise ValueError(f"Repository clone failed: {result.stderr.strip()[-400:]}")

    @staticmethod
    def slug(source: str) -> str | None:
        if "github.com" not in source:
            return None
        return urlparse(source).path.strip("/").removesuffix(".git")

    def _api(self, method: str, path: str, token: str | None = None):
        stored = token is None
        token = token or self.token()
        if not token:
            raise ValueError("GitHub is not connected")
        response = _send(method, path, token)
        if response.status_code == 401 and stored and self.secrets.workspace_id:
            # GitHub refused the saved grant: it expired, was revoked, or was
            # replaced by a newer authorization. Renew it if GitHub issued a
            # refresh token; otherwise stop showing GitHub as connected.
            renewed = self._renew()
            if not renewed:
                self.secrets.mark_expired("github")
                raise ValueError(RECONNECT_MESSAGE)
            response = _send(method, path, renewed)
        response.raise_for_status()
        return response.json()

    def _api_all(self, path: str, max_pages: int = 20) -> list[dict[str, Any]]:
        """Follow GitHub collection pages so organization inventories are complete."""
        output: list[dict[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, max_pages + 1):
            batch = self._api("GET", f"{path}{separator}page={page}")
            if not isinstance(batch, list):
                break
            output.extend(batch)
            if len(batch) < 100:
                break
        return output


RECONNECT_MESSAGE = (
    "GitHub no longer accepts MemoryWorks' saved access (it expired or was revoked). "
    "Reconnect GitHub on the Sources page."
)


def _send(method: str, path: str, token: str) -> httpx.Response:
    return httpx.request(
        method,
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )


def _refresh_exchange(refresh_token: str) -> dict[str, Any]:
    response = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
