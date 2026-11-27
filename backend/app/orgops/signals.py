"""Live signal storage, clustering, and trimmed reads.

No LLM runs in this module. All ordering is deterministic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core.database import connect, new_id, utcnow
from app.ingestion.safety import sanitize_for_index
from app.orgops.service import _terms

SEVERITIES = ("info", "warning", "error", "critical")
STATES = ("failure", "success")
STATUSES = ("open", "resolved", "muted")
MAX_TTL_SECONDS = 7 * 24 * 3600

_SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _normalize_ts(value: str) -> str:
    if not value or not isinstance(value, str):
        raise ValueError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed.isoformat(timespec="microseconds")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _strip_url_query_fragment(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("http://") and not stripped.startswith("https://"):
        return text
    if (
        "?" not in stripped and "#" not in stripped and "@" not in stripped.split("/")[2]
        if "/" in stripped
        else True
    ):
        # Fast path: no query/fragment/credentials; still sanitize via caller.
        pass
    try:
        parts = urlparse(stripped)
    except Exception:
        return text
    if not parts.scheme or not parts.netloc:
        return text
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "<redacted>@"
    # Drop query and fragment; never retain supplied query strings.
    if parts.query or parts.fragment or "@" in parts.netloc:
        return f"{parts.scheme}://{netloc}{parts.path}"
    return text


def _sanitize_string(value: str) -> str:
    cleaned, _ = sanitize_for_index(value)
    cleaned = _strip_url_query_fragment(cleaned)
    # Limit labels/names; generic details never carry raw payloads.
    if len(cleaned) > 200:
        cleaned = cleaned[:200]
    return cleaned


def _sanitize_details(details: dict | None) -> dict:
    if not details:
        return {}
    if not isinstance(details, dict):
        raise ValueError("invalid details")
    output: dict[str, Any] = {}
    for key, item in details.items():
        if not isinstance(key, str):
            raise ValueError("invalid details key")
        output[_sanitize_string(key)[:200]] = _sanitize_value(item)
    return output


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:50]]
    if isinstance(value, dict):
        return {str(k)[:200]: _sanitize_value(v) for k, v in list(value.items())[:50]}
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _sanitize_string(str(value))


def _sanitize_evidence_url(url: str) -> str:
    if not url:
        return ""
    text, _ = sanitize_for_index(str(url))
    text = text.strip()
    try:
        parts = urlparse(text)
    except Exception:
        return text[:2000]
    if parts.scheme in ("http", "https") and parts.netloc:
        netloc = parts.netloc
        if "@" in netloc:
            netloc = netloc.split("@", 1)[1]
            text = f"{parts.scheme}://{netloc}{parts.path}"
        else:
            # Drop query/fragment from supplied URLs.
            text = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return text[:2000]


def signal_fingerprint(
    *,
    workspace_id: str,
    project_id: str | None,
    source: str,
    kind: str,
    subject: str,
    lane: dict[str, str],
    source_ids: list[str],
) -> str:
    if not isinstance(lane, dict):
        raise ValueError("invalid lane")
    clean_lane = {str(k): str(v) for k, v in lane.items()}
    payload = [
        1,
        workspace_id,
        project_id or "",
        source,
        kind,
        subject,
        clean_lane,
        sorted(set(str(s) for s in (source_ids or []))),
    ]
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _provider_order_key(sig: dict[str, Any]) -> tuple:
    state = str(sig.get("state") or "")
    rank = 0 if state == "failure" else 1
    return (
        str(sig.get("generation_at") or ""),
        int(sig.get("generation_id") or 0),
        int(sig.get("attempt") or 0),
        str(sig.get("observed_at") or ""),
        rank,
        str(sig.get("observation_key") or ""),
    )


class SignalService:
    def __init__(self, company_memory, scopes=None):
        self.memory = company_memory
        if scopes is None:
            from app.governance.scopes import ScopeService

            self.scopes = ScopeService()
        else:
            self.scopes = scopes

    def observe(
        self,
        *,
        workspace_id: str,
        project_id: str | None,
        source: str,
        kind: str,
        subject: str,
        lane: dict[str, str],
        severity: str,
        state: str,
        observed_at: str,
        generation_at: str,
        generation_id: int,
        attempt: int,
        observation_key: str,
        source_ids: list[str],
        evidence_url: str = "",
        details: dict | None = None,
        collected_at: str | None = None,
        ttl_seconds: int = 86400,
    ) -> dict:
        if not workspace_id or not isinstance(workspace_id, str):
            raise ValueError("invalid workspace_id")
        if not source or not kind or not subject:
            raise ValueError("invalid source/kind/subject")
        if not observation_key:
            raise ValueError("invalid observation_key")
        if severity not in SEVERITIES:
            raise ValueError("invalid severity")
        if state not in STATES:
            raise ValueError("invalid state")
        if not isinstance(ttl_seconds, int) or ttl_seconds <= 0 or ttl_seconds > MAX_TTL_SECONDS:
            raise ValueError("invalid ttl_seconds")
        if not isinstance(attempt, int) or attempt < 1:
            raise ValueError("invalid attempt")
        if not isinstance(generation_id, int) or generation_id < 0:
            raise ValueError("invalid generation_id")
        if not isinstance(lane, dict):
            raise ValueError("invalid lane")
        if source_ids is None or not isinstance(source_ids, list):
            raise ValueError("invalid source_ids")
        for sid in source_ids:
            if not isinstance(sid, str) or not sid:
                raise ValueError("invalid source_id")

        observed_norm = _normalize_ts(observed_at)
        generation_norm = _normalize_ts(generation_at)
        collected_norm = _normalize_ts(collected_at) if collected_at else _normalize_ts(utcnow())
        collected_dt = _parse_ts(collected_norm)
        assert collected_dt is not None
        from datetime import timedelta as _td

        expires_norm = (collected_dt + _td(seconds=ttl_seconds)).isoformat(timespec="microseconds")

        norm_project: str | None = None
        if project_id not in (None, ""):
            norm_project = str(project_id)
            if not norm_project:
                raise ValueError("invalid project_id")
        else:
            norm_project = None

        fingerprint = signal_fingerprint(
            workspace_id=workspace_id,
            project_id=norm_project,
            source=source,
            kind=kind,
            subject=subject,
            lane={str(k): str(v) for k, v in lane.items()},
            source_ids=[str(s) for s in source_ids],
        )
        sanitized_details = _sanitize_details(details)
        clean_evidence = _sanitize_evidence_url(evidence_url or "")
        source_ids_sorted = sorted(set(str(s) for s in source_ids))
        source_ids_json = _canonical(source_ids_sorted)

        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Validate project binding before any write.
            if norm_project is not None:
                proj = conn.execute(
                    "SELECT id FROM projects WHERE id=?", (norm_project,)
                ).fetchone()
                if not proj:
                    raise ValueError("unknown project")
                bind = conn.execute(
                    "SELECT 1 FROM workspace_projects WHERE workspace_id=? AND project_id=?",
                    (workspace_id, norm_project),
                ).fetchone()
                if not bind:
                    raise ValueError("project not in workspace")

            # Snapshot source grants inside the transaction.
            grants: dict[str, list[str]] = {}
            for sid in source_ids_sorted:
                if norm_project is None:
                    grants[sid] = []
                else:
                    team_rows = conn.execute(
                        "SELECT team_id FROM source_scopes WHERE project_id=? AND source_id=?",
                        (norm_project, sid),
                    ).fetchall()
                    grants[sid] = sorted({r[0] for r in team_rows})
            grants_json = _canonical({k: sorted(v) for k, v in grants.items()})
            details_json = _canonical(sanitized_details)

            existing = conn.execute(
                "SELECT * FROM live_signals WHERE workspace_id=? AND fingerprint=? AND observation_key=?",
                (workspace_id, fingerprint, observation_key),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["state"]) != state
                    or str(existing["generation_at"]) != generation_norm
                    or int(existing["generation_id"]) != int(generation_id)
                    or int(existing["attempt"]) != int(attempt)
                    or str(existing["observed_at"]) != observed_norm
                ):
                    raise ValueError("conflicting duplicate observation")
                new_collected = max(str(existing["collected_at"]), collected_norm)
                new_expires = max(str(existing["expires_at"]), expires_norm)
                conn.execute(
                    "UPDATE live_signals SET collected_at=?, expires_at=? WHERE id=?",
                    (new_collected, new_expires, str(existing["id"])),
                )
                issue_id = str(existing["issue_id"]) if existing["issue_id"] else ""
                if issue_id:
                    issue = conn.execute(
                        "SELECT * FROM live_issues WHERE id=?", (issue_id,)
                    ).fetchone()
                    if issue is not None:
                        return {
                            "signal_id": str(existing["id"]),
                            "issue_id": str(issue["id"]),
                            "created": False,
                            "status": str(issue["status"]),
                        }
                # Signal had no issue; an issue may have been created later.
                later = conn.execute(
                    "SELECT * FROM live_issues WHERE workspace_id=? AND fingerprint=?",
                    (workspace_id, fingerprint),
                ).fetchone()
                if later is not None:
                    conn.execute(
                        "UPDATE live_signals SET issue_id=? WHERE id=?",
                        (str(later["id"]), str(existing["id"])),
                    )
                    return {
                        "signal_id": str(existing["id"]),
                        "issue_id": str(later["id"]),
                        "created": False,
                        "status": str(later["status"]),
                    }
                return {
                    "signal_id": str(existing["id"]),
                    "issue_id": None,
                    "created": False,
                    "status": None,
                }

            signal_id = new_id("sig")
            conn.execute(
                """INSERT INTO live_signals
                (id,issue_id,workspace_id,project_id,source,kind,subject,severity,state,
                 observed_at,collected_at,expires_at,evidence_url,fingerprint,observation_key,
                 generation_at,generation_id,attempt,source_ids_json,source_grants_json,details_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_id,
                    None,
                    workspace_id,
                    norm_project,
                    source,
                    kind,
                    subject,
                    severity,
                    state,
                    observed_norm,
                    collected_norm,
                    expires_norm,
                    clean_evidence,
                    fingerprint,
                    observation_key,
                    generation_norm,
                    int(generation_id),
                    int(attempt),
                    source_ids_json,
                    grants_json,
                    details_json,
                ),
            )

            if state == "success":
                issue = conn.execute(
                    "SELECT * FROM live_issues WHERE workspace_id=? AND fingerprint=?",
                    (workspace_id, fingerprint),
                ).fetchone()
                if issue is None:
                    return {
                        "signal_id": signal_id,
                        "issue_id": None,
                        "created": True,
                        "status": None,
                    }
                conn.execute(
                    "UPDATE live_signals SET issue_id=? WHERE id=?",
                    (str(issue["id"]), signal_id),
                )
                self._refresh_issue(
                    conn, str(issue["id"]), workspace_id, fingerprint, collected_norm
                )
                refreshed = conn.execute(
                    "SELECT * FROM live_issues WHERE id=?", (str(issue["id"]),)
                ).fetchone()
                return {
                    "signal_id": signal_id,
                    "issue_id": str(refreshed["id"]),
                    "created": True,
                    "status": str(refreshed["status"]),
                }

            # state == failure
            issue = conn.execute(
                "SELECT * FROM live_issues WHERE workspace_id=? AND fingerprint=?",
                (workspace_id, fingerprint),
            ).fetchone()
            if issue is None:
                issue_id = new_id("iss")
                constraints: list[list[str]] = []
                for sid in source_ids_sorted:
                    team_list = grants.get(sid, [])
                    if team_list and team_list not in constraints:
                        constraints.append(team_list)
                constraints_json = _canonical(sorted(constraints))
                conn.execute(
                    """INSERT INTO live_issues
                    (id,workspace_id,project_id,source,kind,subject,fingerprint,
                     source_ids_json,scope_constraints_json,severity,
                     first_seen,last_seen,occurrences,status,latest_signal_id,
                     resolved_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        issue_id,
                        workspace_id,
                        norm_project,
                        source,
                        kind,
                        subject,
                        fingerprint,
                        source_ids_json,
                        constraints_json,
                        severity,
                        observed_norm,
                        observed_norm,
                        1,
                        "open",
                        signal_id,
                        None,
                        collected_norm,
                        collected_norm,
                    ),
                )
                # Attach earlier successes (and this failure) to the new issue.
                conn.execute(
                    "UPDATE live_signals SET issue_id=? WHERE workspace_id=? AND fingerprint=?",
                    (issue_id, workspace_id, fingerprint),
                )
                self._refresh_issue(conn, issue_id, workspace_id, fingerprint, collected_norm)
                refreshed = conn.execute(
                    "SELECT * FROM live_issues WHERE id=?", (issue_id,)
                ).fetchone()
                return {
                    "signal_id": signal_id,
                    "issue_id": str(refreshed["id"]),
                    "created": True,
                    "status": str(refreshed["status"]),
                }

            conn.execute(
                "UPDATE live_signals SET issue_id=? WHERE id=?",
                (str(issue["id"]), signal_id),
            )
            self._refresh_issue(conn, str(issue["id"]), workspace_id, fingerprint, collected_norm)
            refreshed = conn.execute(
                "SELECT * FROM live_issues WHERE id=?", (str(issue["id"]),)
            ).fetchone()
            return {
                "signal_id": signal_id,
                "issue_id": str(refreshed["id"]),
                "created": True,
                "status": str(refreshed["status"]),
            }

    def _refresh_issue(
        self, conn, issue_id: str, workspace_id: str, fingerprint: str, now_norm: str
    ) -> None:
        sigs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM live_signals WHERE workspace_id=? AND fingerprint=?",
                (workspace_id, fingerprint),
            ).fetchall()
        ]
        if not sigs:
            return
        failures = [s for s in sigs if str(s.get("state")) == "failure"]
        successes = [s for s in sigs if str(s.get("state")) == "success"]
        occurrences = len(failures)
        if failures:
            first_seen = min(str(s.get("observed_at")) for s in failures)
            last_seen = max(str(s.get("observed_at")) for s in failures)
        else:
            # No failures (should not happen for an issue, but keep stable).
            existing = conn.execute(
                "SELECT first_seen,last_seen FROM live_issues WHERE id=?", (issue_id,)
            ).fetchone()
            first_seen = str(existing["first_seen"]) if existing else now_norm
            last_seen = str(existing["last_seen"]) if existing else now_norm
        latest = max(sigs, key=_provider_order_key)
        newest_success_at = max(str(s.get("observed_at")) for s in successes) if successes else None
        current = conn.execute("SELECT * FROM live_issues WHERE id=?", (issue_id,)).fetchone()
        if current is None:
            return
        prev_status = str(current["status"])
        if prev_status == "muted":
            next_status = "muted"
        elif str(latest.get("state")) == "failure":
            next_status = "open"
        else:
            next_status = "resolved"
        if next_status == "open":
            resolved_at = None
        else:
            resolved_at = newest_success_at
            if prev_status == "muted" and next_status == "muted":
                # Keep prior resolved_at unless a newer success exists.
                prior = current["resolved_at"]
                if prior and newest_success_at and str(prior) > str(newest_success_at):
                    resolved_at = str(prior)
        # Union source ids and accumulate distinct nonempty constraints.
        union_ids: set[str] = set()
        for s in sigs:
            try:
                ids = json.loads(s.get("source_ids_json") or "[]")
            except (TypeError, ValueError):
                ids = []
            for sid in ids:
                union_ids.add(str(sid))
        try:
            existing_constraints = json.loads(current["scope_constraints_json"] or "[]")
        except (TypeError, ValueError):
            existing_constraints = []
        constraint_set = {tuple(c) for c in existing_constraints if isinstance(c, list)}
        for s in sigs:
            try:
                g = json.loads(s.get("source_grants_json") or "{}")
            except (TypeError, ValueError):
                g = {}
            if isinstance(g, dict):
                for team_list in g.values():
                    if isinstance(team_list, list) and team_list:
                        constraint_set.add(tuple(sorted(str(t) for t in team_list)))
        constraints = sorted([list(t) for t in constraint_set])
        conn.execute(
            """UPDATE live_issues SET source_ids_json=?, scope_constraints_json=?,
               severity=?, first_seen=?, last_seen=?, occurrences=?, status=?,
               latest_signal_id=?, resolved_at=?, updated_at=? WHERE id=?""",
            (
                _canonical(sorted(union_ids)),
                _canonical(constraints),
                str(latest.get("severity") or "error"),
                first_seen,
                last_seen,
                int(occurrences),
                next_status,
                str(latest.get("id")),
                resolved_at,
                now_norm,
                issue_id,
            ),
        )

    def list_issues(
        self,
        *,
        workspace_id: str,
        project_ids: list[str],
        allowed_team_ids: list[str] | None,
        status: str = "open",
        include_expired: bool = False,
        now: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        if status not in STATUSES:
            raise ValueError("invalid status")
        limit = max(1, min(500, int(limit)))
        now_norm = _normalize_ts(now) if now else _normalize_ts(utcnow())
        requested = [str(p) for p in (project_ids or []) if str(p)]
        with connect() as conn:
            bound_rows = conn.execute(
                "SELECT project_id FROM workspace_projects WHERE workspace_id=?",
                (workspace_id,),
            ).fetchall()
            bound = {str(r[0]) for r in bound_rows}
            allowed_projects = [p for p in requested if p in bound]
            allowed_set = set(allowed_projects)
            issue_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM live_issues WHERE workspace_id=? AND status=?",
                    (workspace_id, status),
                ).fetchall()
            ]
        output: list[dict] = []
        snapshots: dict[str, list[dict]] = {}
        for issue in issue_rows:
            proj = issue.get("project_id")
            # Workspace-only rows are included even when project_ids is empty.
            if proj is None:
                pass
            else:
                if str(proj) not in allowed_set:
                    continue
            if not self._issue_visible(issue, allowed_team_ids):
                continue
            freshness, latest_sig = self._issue_freshness(issue, now_norm)
            if freshness == "expired" and not include_expired:
                continue
            enriched = self._enrich_issue(
                issue, latest_sig, freshness, allowed_team_ids, now_norm, snapshots
            )
            output.append(enriched)
        output.sort(
            key=lambda item: (
                _SEVERITY_ORDER.get(str(item.get("severity")), 9),
                _invert_time(str(item.get("last_seen") or "")),
                str(item.get("id") or ""),
            )
        )
        return output[:limit]

    def _issue_visible(self, issue: dict, allowed_team_ids: list[str] | None) -> bool:
        from app.core.database import row as _row
        from app.core.database import rows as _rows

        proj = issue.get("project_id")
        # Project grants (rechecked at read time).
        if proj is not None:
            grants = _rows("SELECT team_id FROM project_teams WHERE project_id=?", (str(proj),))
            if grants:
                if allowed_team_ids is None:
                    pass
                elif not set(allowed_team_ids) & {g["team_id"] for g in grants}:
                    return False
            # Every current source grant.
            try:
                source_ids = json.loads(issue.get("source_ids_json") or "[]")
            except (TypeError, ValueError):
                source_ids = []
            for sid in source_ids:
                scoped = _rows(
                    "SELECT team_id FROM source_scopes WHERE project_id=? AND source_id=?",
                    (str(proj), str(sid)),
                )
                if scoped:
                    if allowed_team_ids is None:
                        continue
                    if not set(allowed_team_ids) & {g["team_id"] for g in scoped}:
                        return False
        else:
            # Workspace-only: check source scopes without project? None expected.
            pass
        # Historical snapshot constraints (AND across history).
        try:
            constraints = json.loads(issue.get("scope_constraints_json") or "[]")
        except (TypeError, ValueError):
            constraints = []
        for team_list in constraints:
            if not isinstance(team_list, list) or not team_list:
                continue
            if allowed_team_ids is None:
                continue
            if not set(allowed_team_ids) & set(str(t) for t in team_list):
                return False
        # Exact workspace-project binding recheck.
        if proj is not None:
            bind = _row(
                "SELECT 1 AS ok FROM workspace_projects WHERE workspace_id=? AND project_id=?",
                (str(issue.get("workspace_id")), str(proj)),
            )
            if not bind:
                return False
        return True

    def _issue_freshness(self, issue: dict, now_norm: str) -> tuple[str, dict | None]:
        from app.core.database import rows as _rows

        sigs = _rows(
            "SELECT * FROM live_signals WHERE workspace_id=? AND fingerprint=?",
            (str(issue.get("workspace_id")), str(issue.get("fingerprint"))),
        )
        if not sigs:
            return "fresh", None
        failures = [s for s in sigs if str(s.get("state")) == "failure"]
        latest = max(
            (dict(s) for s in sigs),
            key=_provider_order_key,
        )
        if failures:
            latest_failure = max(
                (dict(s) for s in failures),
                key=_provider_order_key,
            )
            expires = str(latest_failure.get("expires_at") or "")
            if expires and expires <= now_norm:
                return "expired", latest
        else:
            expires = str(latest.get("expires_at") or "")
            if expires and expires <= now_norm:
                return "expired", latest
        return "fresh", latest

    def _enrich_issue(
        self,
        issue: dict,
        latest_sig: dict | None,
        freshness: str,
        allowed_team_ids: list[str] | None,
        now_norm: str,
        snapshots: dict[str, list[dict]],
    ) -> dict:
        proj = issue.get("project_id")
        owner = ""
        owner_evidence: list[str] = []
        related_ids: list[str] = []
        related: list[dict] = []
        if proj is not None and latest_sig is not None:
            # One authorized memory snapshot per project per request.
            if str(proj) not in snapshots:
                units = self.memory.list(
                    str(proj), latest=True, limit=2000, allowed_team_ids=allowed_team_ids
                )
                snapshots[str(proj)] = units
            units = snapshots[str(proj)]
            now_dt = _parse_ts(now_norm)
            current_units: list[dict] = []
            for unit in units:
                valid_from = _parse_ts(unit.get("valid_from") or unit.get("created_at"))
                valid_to = _parse_ts(unit.get("valid_to"))
                if valid_from and now_dt and valid_from > now_dt:
                    continue
                if valid_to and now_dt and valid_to <= now_dt:
                    continue
                # Every memory source must be visible before enrichment.
                candidate_sources = set(unit.get("source_ids") or [])
                if candidate_sources:
                    visible = self.scopes.visible_source_ids(
                        str(proj), candidate_sources, allowed_team_ids
                    )
                    if set(candidate_sources) - set(visible):
                        continue
                current_units.append(unit)
            ownership = sorted(
                [u for u in current_units if str(u.get("type")) == "ownership"],
                key=lambda u: str(u.get("id") or ""),
            )
            # Owner reuses the existing orgops rules via the shared helper.
            lane_label = ""
            try:
                det = json.loads((latest_sig or {}).get("details_json") or "{}")
            except (TypeError, ValueError):
                det = {}
            if isinstance(det, dict):
                lane_label = " ".join(str(v) for v in det.values() if isinstance(v, str))
            from app.orgops.service import OrgOpsService as _OrgOps

            _ops = _OrgOps(self.memory)
            resolved = _ops.resolve_subject_owner(
                str(issue.get("subject", "")), lane_label, ownership
            )
            owner = str(resolved.get("owner") or "")
            owner_evidence = [str(v) for v in (resolved.get("evidence") or [])]
            # Related incident/decision links reuse the same term scoring.
            terms = _terms(f"{issue.get('subject', '')} {lane_label}")
            scored: list[tuple[float, dict]] = []
            for unit in current_units:
                if str(unit.get("type")) not in ("incident", "decision"):
                    continue
                score = _OrgOps._score(unit, terms)
                if score > 0:
                    scored.append((score, unit))
            scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
            for _, unit in scored[:5]:
                related_ids.append(str(unit.get("id")))
                related.append(self._public_memory(unit, str(proj)))
        latest_payload = None
        if latest_sig is not None:
            try:
                det = json.loads(latest_sig.get("details_json") or "{}")
            except (TypeError, ValueError):
                det = {}
            latest_payload = {
                "id": str(latest_sig.get("id")),
                "state": str(latest_sig.get("state")),
                "evidence_url": str(latest_sig.get("evidence_url") or ""),
                "observed_at": str(latest_sig.get("observed_at")),
                "expires_at": str(latest_sig.get("expires_at")),
                "details": det if isinstance(det, dict) else {},
            }
        try:
            scope_constraints = json.loads(issue.get("scope_constraints_json") or "[]")
        except (TypeError, ValueError):
            scope_constraints = []
        return {
            "id": str(issue.get("id")),
            "workspace_id": str(issue.get("workspace_id")),
            "project_id": issue.get("project_id"),
            "source": str(issue.get("source")),
            "kind": str(issue.get("kind")),
            "subject": str(issue.get("subject")),
            "fingerprint": str(issue.get("fingerprint")),
            "severity": str(issue.get("severity")),
            "status": str(issue.get("status")),
            "first_seen": str(issue.get("first_seen")),
            "last_seen": str(issue.get("last_seen")),
            "occurrences": int(issue.get("occurrences") or 0),
            "resolved_at": issue.get("resolved_at"),
            "freshness": freshness,
            "owner": owner,
            "owner_evidence": owner_evidence,
            "related_memory_ids": related_ids,
            "related_memories": related,
            "latest_signal": latest_payload,
            "scope_constraints": scope_constraints,
        }

    def _public_memory(self, unit: dict, project_id: str) -> dict:
        from app.core.database import row as _row

        record = _row("SELECT name FROM projects WHERE id=?", (project_id,))
        return {
            "id": unit.get("id"),
            "space_id": unit.get("project_id"),
            "space_name": (record or {}).get("name", ""),
            "type": unit.get("type"),
            "title": unit.get("subject"),
            "content": unit.get("content"),
            "scope": unit.get("scope") or {},
            "confidence": unit.get("confidence"),
            "source_ids": unit.get("source_ids") or [],
            "created_at": unit.get("created_at"),
            "updated_at": unit.get("updated_at"),
            "valid_from": unit.get("valid_from"),
            "valid_to": unit.get("valid_to"),
        }


def _invert_time(value: str) -> str:
    # Sort last_seen descending with a string key.
    return "".join(chr(0x10FFFF - ord(c)) for c in value)
