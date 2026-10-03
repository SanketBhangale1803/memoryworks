"""What is actually happening with a workspace's connected sources, right now.

"Why is the Google Drive connection failing?" was answered from company memory,
which knows how the connector is built and nothing about whether it works: no
document records that a consent screen was blocked an hour ago. The facts that
answer it live in the system's own tables — which connections exist and when
they expire, which connection attempts were started and whether the person ever
came back from the provider, and how the last imports went. This module reads
them and says what they mean.

Nothing here reads or returns a secret: tokens stay encrypted in the vault, and
only their existence, status, and timestamps are reported.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import connect, rows, utcnow

# Each connection attempt's state is valid for this long (OAuthStateStore), so
# its start time is its expiry minus this.
ATTEMPT_TTL = timedelta(minutes=10)

PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    # Not bare "google": "Sign in with Google" is a different flow from Drive.
    "google_drive": ("google drive", "gdrive", "g drive", "drive", "google docs"),
    "github": ("github", "git hub"),
    "slack": ("slack",),
    "notion": ("notion",),
    "teams": ("microsoft teams", "ms teams", "teams"),
}
PROVIDER_NAMES = {
    "google_drive": "Google Drive",
    "github": "GitHub",
    "slack": "Slack",
    "notion": "Notion",
    "teams": "Microsoft Teams",
}

# Explicit connection vocabulary only: "what did Slack say about access control"
# is a question about Slack's contents, not about the Slack connection.
_STATUS_QUESTION = re.compile(
    r"\b(connect(?:ed|ion|ions|ing|or|ors)?|integrat\w*|sync(?:ed|ing|s)?|"
    r"import(?:ed|ing|s)?|oauth|authori[sz]\w*|sign[- ]?in|log[- ]?in|consent|"
    r"access (?:blocked|denied)|reconnect\w*|disconnect\w*)\b",
    re.I,
)
_PROBLEM_OR_STATE = re.compile(
    r"\b(fail\w*|broken|break\w*|error\w*|not working|doesn'?t work|isn'?t working|"
    r"blocked|denied|stuck|why|status|state|recent|show|list|which|what|working|"
    r"expired?|disconnected|connected)\b",
    re.I,
)

OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS connector_auth_outcomes (
  state TEXT PRIMARY KEY, workspace_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL,
  outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
)
"""


def _ensure_outcomes_table() -> None:
    # A new table, so CREATE TABLE IF NOT EXISTS is a complete migration here.
    with connect() as conn:
        conn.execute(OUTCOMES_DDL)


def record_auth_outcome(
    state: str, provider: str, outcome: str, detail: str = "", workspace_id: str = ""
) -> None:
    """Remember how a connection attempt ended: connected, denied, or failed.

    Best effort — recording an outcome must never be the reason a callback fails.
    """
    try:
        _ensure_outcomes_table()
        if not workspace_id:
            flow = rows("SELECT workspace_id FROM oauth_flows WHERE state=?", (state,))
            workspace_id = str(flow[0]["workspace_id"]) if flow else ""
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO connector_auth_outcomes VALUES (?,?,?,?,?,?)",
                (state, workspace_id, provider, outcome, detail[:500], utcnow()),
            )
    except Exception:  # noqa: BLE001
        pass


def mentioned_providers(query: str) -> list[str]:
    lowered = f" {query.casefold()} "
    found = []
    for provider, aliases in PROVIDER_ALIASES.items():
        if any(re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered) for alias in aliases):
            found.append(provider)
    # "Google Drive" also contains "google"; GitHub sign-in is a different thing.
    return found


def is_connection_question(query: str) -> bool:
    """A question about whether a source's connection works, not about its contents."""
    providers = mentioned_providers(query)
    if not providers:
        return bool(
            re.search(r"\b(connections|connected sources|integrations|my sources)\b", query, re.I)
            and _PROBLEM_OR_STATE.search(query)
        )
    return bool(_STATUS_QUESTION.search(query) and _PROBLEM_OR_STATE.search(query))


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _ago(moment: datetime | None, now: datetime) -> str:
    if not moment:
        return "at an unknown time"
    seconds = int((now - moment).total_seconds())
    if seconds < 0:
        return f"in {_span(-seconds)}"
    return f"{_span(seconds)} ago"


def _span(seconds: int) -> str:
    if seconds < 90:
        return "a minute" if seconds >= 45 else "moments"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 36:
        return f"{hours} hours"
    return f"{hours // 24} days"


def provider_status(
    workspace_id: str, provider: str, *, days: int = 14, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    _ensure_outcomes_table()

    connections = []
    for grant in rows(
        "SELECT display_name, status, token_expires_at, refresh_token_encrypted, scopes_json, "
        "created_at, updated_at, revoked_at FROM oauth_token_grants "
        "WHERE workspace_id=? AND provider=? ORDER BY updated_at DESC",
        (workspace_id, provider),
    ):
        expires = _parse(grant.get("token_expires_at"))
        connections.append(
            {
                "account": grant.get("display_name") or "",
                "status": "revoked" if grant.get("revoked_at") else grant.get("status") or "",
                "connected_at": grant.get("created_at") or "",
                "updated_at": grant.get("updated_at") or "",
                "expires_at": grant.get("token_expires_at") or "",
                "expired": bool(expires and expires < now),
                "can_refresh": bool(grant.get("refresh_token_encrypted")),
                "scopes": json.loads(grant.get("scopes_json") or "[]"),
            }
        )

    attempts = []
    for flow in rows(
        "SELECT f.state, f.intent, f.expires_at, f.used_at, o.outcome, o.detail "
        "FROM oauth_flows f LEFT JOIN connector_auth_outcomes o ON o.state = f.state "
        "WHERE f.workspace_id=? AND f.provider=? ORDER BY f.expires_at DESC LIMIT 20",
        (workspace_id, provider),
    ):
        expires = _parse(flow.get("expires_at"))
        started = expires - ATTEMPT_TTL if expires else None
        if started and started < since:
            continue
        if flow.get("outcome"):
            outcome = str(flow["outcome"])
        elif flow.get("used_at"):
            outcome = "returned"
        elif expires and expires < now:
            outcome = "never_returned"
        else:
            outcome = "in_progress"
        attempts.append(
            {
                "started_at": started.isoformat() if started else "",
                "outcome": outcome,
                "detail": str(flow.get("detail") or ""),
            }
        )

    imports = [
        {
            "status": job.get("status") or "",
            "error": job.get("last_error") or "",
            "updated_at": job.get("updated_at") or "",
        }
        for job in rows(
            "SELECT status, last_error, updated_at FROM connector_sync_jobs "
            "WHERE workspace_id=? AND provider=? ORDER BY updated_at DESC LIMIT 5",
            (workspace_id, provider),
        )
    ]
    imports += [
        {
            "status": job.get("status") or "",
            "error": job.get("error") or "",
            "updated_at": job.get("updated_at") or "",
            "items": job.get("knowledge_items_created") or 0,
        }
        for job in rows(
            "SELECT status, error, updated_at, knowledge_items_created FROM ingestion_jobs "
            "WHERE workspace_id=? AND source=? ORDER BY updated_at DESC LIMIT 5",
            (workspace_id, provider),
        )
    ]
    imports.sort(key=lambda item: item["updated_at"], reverse=True)

    return {
        "provider": provider,
        "name": PROVIDER_NAMES.get(provider, provider),
        "connections": connections,
        "attempts": attempts,
        "imports": imports[:5],
        "findings": _diagnose(provider, connections, attempts, imports, now),
        "checked_at": now.isoformat(),
    }


def _diagnose(
    provider: str,
    connections: list[dict],
    attempts: list[dict],
    imports: list[dict],
    now: datetime,
) -> list[str]:
    name = PROVIDER_NAMES.get(provider, provider)
    findings: list[str] = []
    live = [item for item in connections if item["status"] not in {"revoked", "disconnected"}]
    usable = [item for item in live if not item["expired"] or item["can_refresh"]]

    denied = [item for item in attempts if item["outcome"] == "denied"]
    failed = [item for item in attempts if item["outcome"] == "failed"]
    vanished = [item for item in attempts if item["outcome"] == "never_returned"]

    if denied:
        detail = denied[0]["detail"]
        findings.append(
            f"{name} refused the most recent connection"
            + (f" ({detail})" if detail else "")
            + ". The consent was declined or blocked on the provider's side."
        )
    if failed:
        findings.append(
            f"A connection attempt came back from {name} but could not be completed: "
            f"{failed[0]['detail'] or 'no detail was recorded'}."
        )
    if vanished and not usable:
        hint = (
            " For Google this is almost always the consent screen being blocked: an app in "
            "Testing mode only admits accounts listed as test users, and access to all of "
            "Drive is a restricted scope that needs Google's verification before anyone else "
            "can grant it."
            if provider == "google_drive"
            else " That happens when the provider blocks the consent screen or the window was "
            "closed before finishing."
        )
        findings.append(
            f"{len(vanished)} connection attempt{'s' if len(vanished) != 1 else ''} in this "
            f"period never came back from {name}: the person was sent to {name} and MemoryWorks "
            "never heard back." + hint
        )
    for item in live:
        if item["expired"] and not item["can_refresh"]:
            findings.append(
                f"The connection for {item['account'] or 'this account'} expired "
                f"{_ago(_parse(item['expires_at']), now)} and has no refresh token, so it must "
                "be reconnected."
            )
    if any(item["status"] == "revoked" for item in connections) and not usable:
        findings.append(f"The {name} connection was revoked. Reconnect it to resume.")
    if usable:
        failed_imports = [item for item in imports if item["status"] in {"failed", "error"}]
        if failed_imports:
            findings.append(
                f"The last import failed: {failed_imports[0]['error'] or 'no error text recorded'}."
            )
        elif not imports:
            findings.append(
                f"{name} is connected, but nothing has been imported from it yet — connecting "
                "grants access; choosing what to import is a separate step."
            )
    if not connections and not attempts:
        findings.append(f"{name} has never been connected in this workspace.")
    if not findings and usable:
        findings.append(f"{name} is connected and nothing on record indicates a problem.")
    return findings


def status_answer(workspace_id: str, providers: list[str], query: str = "") -> dict[str, Any]:
    """Markdown for the connection state of ``providers`` (all known ones when empty)."""
    now = datetime.now(UTC)
    reports = [
        provider_status(workspace_id, provider, now=now)
        for provider in providers or list(PROVIDER_NAMES)
    ]
    if not providers:
        reports = [report for report in reports if report["connections"] or report["attempts"]]
    lines: list[str] = []
    for report in reports:
        lines.append(f"**{report['name']}**")
        lines += [f"- {finding}" for finding in report["findings"]]
        for item in report["connections"][:3]:
            state = "expired" if item["expired"] and not item["can_refresh"] else item["status"]
            lines.append(
                f"- Connection: {item['account'] or 'unnamed account'} — {state}, connected "
                f"{_ago(_parse(item['connected_at']), now)}"
            )
        if report["attempts"]:
            counts: dict[str, int] = {}
            for item in report["attempts"]:
                counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
            words = {
                "connected": "connected",
                "returned": "came back",
                "denied": "denied",
                "failed": "failed",
                "never_returned": "never came back",
                "in_progress": "in progress",
            }
            summary = ", ".join(f"{count} {words.get(key, key)}" for key, count in counts.items())
            latest = _ago(_parse(report["attempts"][0]["started_at"]), now)
            lines.append(
                f"- Connection attempts in the last 14 days: {len(report['attempts'])} "
                f"({summary}); the latest started {latest}."
            )
        if report["imports"]:
            last = report["imports"][0]
            lines.append(
                f"- Last import: {last['status']} {_ago(_parse(last['updated_at']), now)}"
                + (f" — {last['error']}" if last.get("error") else "")
            )
        lines.append("")
    if not reports:
        lines = ["No source has been connected or attempted in this workspace yet."]
    return {
        "answer": "\n".join(lines).strip(),
        "likely_cause": next(
            (finding for report in reports for finding in report["findings"]), "Not applicable."
        ),
        "safe_actions": [],
        "approval_required": [],
        "sufficient": True,
        "supporting_chunk_ids": [],
        "answer_kind": "connection_status",
        "answer_scope": "system_state",
        "trust_score": {
            "score": 0.9,
            "level": "high",
            "reason": "Read from MemoryWorks's own connection records, not inferred from documents.",
        },
        "_reports": reports,
    }
