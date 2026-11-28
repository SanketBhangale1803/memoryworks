"""Answer "which repos are connected?" from the workspace's own records.

That question took 97 seconds and came back as quoted lines from documents:
nothing in company memory says which repositories are connected, so the answer
path searched every chunk of every repository for the words. The records that
do say it — the workspace's memory spaces and its live connections — answer it
in milliseconds. Only questions about what is connected or imported land here;
"what repos use Redis?" is about their contents and goes to search.
"""

from __future__ import annotations

import re
from typing import Any

from app.connectors.status import PROVIDER_NAMES
from app.core.database import rows

from .conversation import _reply

_THINGS = (
    r"(?:repos?|repositories|projects|sources|data sources|integrations|connections|memory spaces)"
)
_STATE = (
    r"(?:connected|imported|indexed|ingested|added|synced|linked|available"
    r"|in (?:memory|memoryworks|the workspace))"
)
_PATTERNS = [
    # which repos are connected / what sources have been imported / which projects do you have
    re.compile(
        rf"\b(?:which|what)\s+{_THINGS}\s+(?:are|have been|has been|is|do you have|did (?:i|we))?\s*"
        rf"(?:{_STATE}|connected)\b"
    ),
    re.compile(rf"\b(?:which|what)\s+{_THINGS}\s+do (?:you|we) have\b"),
    # list my repos / show the connected sources
    re.compile(
        rf"^(?:please\s+)?(?:list|show)(?:\s+me)?(?:\s+all)?(?:\s+(?:my|the|our|connected|imported))*\s+{_THINGS}\b"
    ),
    # what's connected / what is connected
    re.compile(r"\bwhat(?:s| is| are| have you)\s+(?:been\s+)?(?:connected|imported)\b"),
]


def is_inventory_question(query: str) -> bool:
    text = " ".join(query.casefold().replace("'", "").replace("’", "").split())
    return any(pattern.search(text) for pattern in _PATTERNS)


def inventory_reply(query: str, project_ids: list[str], workspace_id: str) -> dict[str, Any] | None:
    if not is_inventory_question(query):
        return None
    placeholders = ",".join("?" for _ in project_ids) or "''"
    spaces = rows(
        f"""SELECT p.id, p.name, p.repository,
                   (SELECT COUNT(*) FROM knowledge_items k WHERE k.project_id = p.id) AS sources,
                   (SELECT MAX(created_at) FROM knowledge_items k WHERE k.project_id = p.id) AS newest
            FROM projects p WHERE p.id IN ({placeholders}) ORDER BY p.name""",
        tuple(project_ids),
    )
    connections = rows(
        """SELECT provider, display_name, status, revoked_at FROM oauth_token_grants
           WHERE workspace_id=? ORDER BY provider""",
        (workspace_id,),
    )
    repositories = [space for space in spaces if space.get("repository")]
    others = [space for space in spaces if not space.get("repository")]

    def line(space: dict[str, Any]) -> str:
        name = space.get("repository") or space.get("name")
        sources = int(space.get("sources") or 0)
        newest = str(space.get("newest") or "")[:10]
        detail = f"{sources} source{'' if sources == 1 else 's'} in memory"
        return f"- **{name}** — {detail}" + (f", last import {newest}" if newest else "")

    parts: list[str] = []
    if repositories:
        count = len(repositories)
        parts.append(f"**{count} {'repository' if count == 1 else 'repositories'} in memory**")
        parts.extend(line(space) for space in repositories)
    else:
        parts.append(
            "No repositories are in memory yet. Add one on **Add knowledge → Connect a repository**."
        )
    if others:
        parts.append("\n**Other memory spaces**")
        parts.extend(line(space) for space in others)
    live = {}
    for grant in connections:
        if grant.get("revoked_at") or str(grant.get("status") or "") not in {
            "",
            "active",
            "connected",
        }:
            continue
        live.setdefault(grant["provider"], grant.get("display_name") or "")
    if live:
        parts.append("\n**Connected sources**")
        parts.extend(
            f"- {PROVIDER_NAMES.get(provider, provider)}" + (f" ({account})" if account else "")
            for provider, account in live.items()
        )
    parts.append("\nManage them on **Sources**; add more on **Add knowledge**.")
    return _reply("\n".join(parts))
