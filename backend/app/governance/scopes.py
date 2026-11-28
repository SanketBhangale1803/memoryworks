from __future__ import annotations

from contextlib import nullcontext
from sqlite3 import Connection
from typing import Any

from app.core.database import connect, new_id, row, rows, utcnow


class ScopeService:
    """Team ownership and visibility boundaries for company memory.

    Existing unscoped projects and sources remain workspace-visible. As soon as
    a project or source is assigned to teams, membership becomes an allow-list.
    """

    def create_team(
        self, workspace_id: str, name: str, parent_team_id: str | None = None
    ) -> dict[str, Any]:
        now = utcnow()
        slug = self._slug(name)
        existing = row("SELECT * FROM teams WHERE workspace_id=? AND slug=?", (workspace_id, slug))
        team_id = existing["id"] if existing else new_id("team")
        with connect() as conn:
            conn.execute(
                """INSERT INTO teams(id,workspace_id,name,slug,parent_team_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,parent_team_id=excluded.parent_team_id,updated_at=excluded.updated_at""",
                (
                    team_id,
                    workspace_id,
                    name.strip(),
                    slug,
                    parent_team_id,
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
        return row("SELECT * FROM teams WHERE id=?", (team_id,)) or {}

    def add_member(self, team_id: str, user_id: str, role: str = "member") -> dict[str, Any]:
        if role not in {"lead", "member", "viewer"}:
            raise ValueError("Unsupported team role")
        if not row("SELECT id FROM teams WHERE id=?", (team_id,)):
            raise ValueError("Team not found")
        if not row("SELECT id FROM users WHERE id=?", (user_id,)):
            raise ValueError("User not found")
        with connect() as conn:
            conn.execute(
                """INSERT INTO team_members(team_id,user_id,role,created_at) VALUES (?,?,?,?)
                ON CONFLICT(team_id,user_id) DO UPDATE SET role=excluded.role""",
                (team_id, user_id, role, utcnow()),
            )
        return {"team_id": team_id, "user_id": user_id, "role": role}

    def assign_project(
        self,
        project_id: str,
        team_id: str,
        access_level: str = "read",
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        if access_level not in {"read", "write", "owner"}:
            raise ValueError("Unsupported project access level")
        with nullcontext(connection) if connection is not None else connect() as conn:
            conn.execute(
                """INSERT INTO project_teams(project_id,team_id,access_level,created_at)
                VALUES (?,?,?,?) ON CONFLICT(project_id,team_id) DO UPDATE SET
                access_level=excluded.access_level""",
                (project_id, team_id, access_level, utcnow()),
            )
        return {"project_id": project_id, "team_id": team_id, "access_level": access_level}

    def list_teams(self, workspace_id: str) -> list[dict[str, Any]]:
        return rows(
            """SELECT t.*,count(DISTINCT tm.user_id) member_count,
            count(DISTINCT pt.project_id) project_count FROM teams t
            LEFT JOIN team_members tm ON tm.team_id=t.id
            LEFT JOIN project_teams pt ON pt.team_id=t.id
            WHERE t.workspace_id=? GROUP BY t.id ORDER BY t.name""",
            (workspace_id,),
        )

    def team_ids_for_user(self, workspace_id: str, user_id: str) -> list[str]:
        return [
            item["id"]
            for item in rows(
                """SELECT t.id FROM teams t JOIN team_members tm ON tm.team_id=t.id
                WHERE t.workspace_id=? AND tm.user_id=?""",
                (workspace_id, user_id),
            )
        ]

    def project_team_ids(self, project_id: str) -> list[str]:
        return [
            item["team_id"]
            for item in rows("SELECT team_id FROM project_teams WHERE project_id=?", (project_id,))
        ]

    def can_access_project(
        self, project_id: str, team_ids: list[str], *, write: bool = False
    ) -> bool:
        grants = rows(
            "SELECT team_id,access_level FROM project_teams WHERE project_id=?", (project_id,)
        )
        if not grants:
            return True
        allowed = set(team_ids)
        levels = {"write", "owner"} if write else {"read", "write", "owner"}
        return any(item["team_id"] in allowed and item["access_level"] in levels for item in grants)

    def bind_source(self, project_id: str, source_id: str, team_ids: list[str]) -> None:
        if not team_ids:
            return
        now = utcnow()
        with connect() as conn:
            for team_id in sorted(set(team_ids)):
                conn.execute(
                    "INSERT OR IGNORE INTO source_scopes VALUES (?,?,?,?)",
                    (source_id, team_id, project_id, now),
                )

    def bind_memory_from_source(self, project_id: str, memory_id: str, source_id: str) -> None:
        grants = rows("SELECT team_id FROM source_scopes WHERE source_id=?", (source_id,))
        if not grants:
            return
        now = utcnow()
        with connect() as conn:
            for grant in grants:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_scopes VALUES (?,?,?,?)",
                    (memory_id, grant["team_id"], project_id, now),
                )

    def visible_memory_ids(self, project_id: str, team_ids: list[str] | None) -> set[str] | None:
        if team_ids is None:
            return None
        scoped = rows(
            "SELECT memory_id,team_id FROM memory_scopes WHERE project_id=?", (project_id,)
        )
        if not scoped:
            return None
        allowed = set(team_ids)
        restricted_ids = {item["memory_id"] for item in scoped}
        visible_restricted = {item["memory_id"] for item in scoped if item["team_id"] in allowed}
        all_ids = {
            item["id"]
            for item in rows("SELECT id FROM memory_units WHERE project_id=?", (project_id,))
        }
        return (all_ids - restricted_ids) | visible_restricted

    def visible_source_ids(
        self, project_id: str, candidate_ids: set[str], team_ids: list[str] | None
    ) -> set[str]:
        if team_ids is None or not candidate_ids:
            return candidate_ids
        placeholders = ",".join("?" for _ in candidate_ids)
        scoped = rows(
            f"SELECT source_id,team_id FROM source_scopes WHERE project_id=? AND source_id IN ({placeholders})",
            (project_id, *candidate_ids),
        )
        restricted = {item["source_id"] for item in scoped}
        allowed = set(team_ids)
        return (candidate_ids - restricted) | {
            item["source_id"] for item in scoped if item["team_id"] in allowed
        }

    @staticmethod
    def _slug(value: str) -> str:
        return (
            "-".join(
                part
                for part in "".join(c.lower() if c.isalnum() else " " for c in value).split()
                if part
            )[:80]
            or "team"
        )
