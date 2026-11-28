"""Persist finished console runs while active runs stream from their worker."""

import json
import logging
import sqlite3
from typing import Any

from app.core.database import connect, row
from app.webmcp_agent import AgentSessionStore

logger = logging.getLogger(__name__)


class OrgAgentSessionStore(AgentSessionStore):
    def get(self, run_id: str, workspace_id: str = "") -> dict[str, Any] | None:
        if not workspace_id:
            return None
        record = super().get(run_id, workspace_id)
        if record is not None:
            return record
        saved = row(
            "SELECT session_json FROM org_agent_sessions WHERE id=? AND workspace_id=?",
            (run_id, workspace_id),
        )
        return json.loads(saved["session_json"]) if saved else None

    def update(self, run_id: str, **fields: Any) -> None:
        if fields.get("status") not in {"complete", "cancelled", "error"}:
            super().update(run_id, **fields)
            return
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return
            finished = {**record, **fields}
            try:
                with connect() as conn:
                    conn.execute(
                        "INSERT INTO org_agent_sessions (id,workspace_id,session_json) VALUES (?,?,?)",
                        (run_id, finished["workspace_id"], json.dumps(finished, default=str)),
                    )
            except sqlite3.Error:
                logger.exception("Could not persist org agent session %s", run_id)
                self._runs[run_id] = finished
            else:
                del self._runs[run_id]
