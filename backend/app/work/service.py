from __future__ import annotations

import json
import re
from typing import Any

from app.audit import AuditService
from app.core.database import connect, decode, new_id, row, rows, utcnow
from app.memory.brain import CompanyBrainService
from app.retrieval import RetrievalService

CONNECTOR_TERMS: dict[str, tuple[str, ...]] = {
    "slack": ("slack", "channel", "thread"),
    "github": ("github", "pull request", "issue", "repository", "repo", "commit"),
    "email": ("email", "gmail", "outlook", "inbox"),
    "calendar": ("calendar", "meeting", "schedule", "invite"),
    "document": ("document", "brief", "report", "memo", "spreadsheet"),
    "terminal": ("terminal", "shell", "command", "script", "deploy"),
}
WRITE_TERMS = {
    "announce",
    "approve",
    "change",
    "comment",
    "create",
    "delete",
    "deploy",
    "merge",
    "message",
    "notify",
    "post",
    "publish",
    "remind",
    "reply",
    "schedule",
    "send",
    "tell",
    "update",
    "write",
}
HIGH_RISK_TERMS = {
    "delete",
    "deploy",
    "merge",
    "payment",
    "production",
    "rotate",
    "secret",
}


class MemoryWorkService:
    """Turn company memory into a portable, approval-aware work packet.

    MemoryWorks owns context, evidence, and policy. A connected worker owns the
    actual tool loop and reports its result back through ``complete_step``.
    """

    def __init__(
        self,
        retrieval: RetrievalService,
        brain: CompanyBrainService,
        audit: AuditService | None = None,
    ):
        self.retrieval = retrieval
        self.brain = brain
        self.audit = audit or AuditService()

    def create(
        self,
        project_id: str,
        objective: str,
        *,
        workspace_id: str = "",
        requested_by: str = "",
        workspace_project_ids: list[str] | None = None,
        principal: dict[str, Any] | None = None,
        allowed_team_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        objective = " ".join(objective.split())
        work_id = new_id("work")
        connector = self._connector(objective)
        external_action = self._requires_external_action(objective, connector)
        direct_slack_notification = connector == "slack" and self._is_direct_notification(objective)
        if direct_slack_notification:
            context = {
                "answer": (
                    "Slack notification prepared from your instruction. Nothing has been "
                    "posted, and no company memory was created."
                ),
                "answer_sufficient": True,
                "confidence": 1.0,
                "trust_score": {
                    "score": 1.0,
                    "level": "user_instruction",
                    "reason": "The message is derived only from the user's instruction.",
                    "factors": {},
                    "contradictions": [],
                },
                "evidence": [],
                "memory_units": [],
                "related_entities": [],
                "updates": [],
                "conflicts": [],
                "retrieval_trace": {
                    "scope_mode": "action_only",
                    "scope_reason": (
                        "A direct user-authored notification does not require company-memory "
                        "retrieval."
                    ),
                },
                "context_envelope": {},
                "action_only": True,
            }
        else:
            context = self.retrieval.ask(
                project_id,
                objective,
                workspace_project_ids,
                principal=principal,
                allowed_team_ids=allowed_team_ids,
                token_budget=8000,
            )
        evidence = context.get("evidence") or []
        sufficient = direct_slack_notification or (
            bool(evidence)
            and bool(context.get("answer_sufficient"))
            and float(context.get("confidence") or 0) > 0
            and not str(context.get("answer") or "").startswith(
                "I do not have enough company memory"
            )
        )
        envelope_id = str((context.get("context_envelope") or {}).get("id") or "")
        now = utcnow()

        bounded_context = {
            "action_only": direct_slack_notification,
            "context_envelope_id": envelope_id,
            "answer": context.get("answer", ""),
            "answer_sufficient": bool(context.get("answer_sufficient")),
            "confidence": context.get("confidence", 0),
            "trust_score": context.get("trust_score", {}),
            "evidence": evidence[:20],
            "memory_units": (context.get("memory_units") or [])[:40],
            "related_entities": context.get("related_entities") or [],
            "updates": (context.get("updates") or [])[:20],
            "conflicts": (context.get("conflicts") or [])[:20],
            "retrieval_trace": context.get("retrieval_trace") or {},
        }
        status = (
            "blocked_context"
            if not sufficient
            else "awaiting_approval" if external_action else "completed"
        )
        with connect() as conn:
            conn.execute(
                """INSERT INTO memory_work
                (id,workspace_id,project_id,objective,status,requested_by,target_connector,
                 context_envelope_id,artifact_id,confidence,context_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    work_id,
                    workspace_id,
                    project_id,
                    objective,
                    status,
                    requested_by,
                    connector,
                    envelope_id,
                    "",
                    float(context.get("confidence") or 0),
                    json.dumps(bounded_context),
                    now,
                    now,
                ),
            )
        self._step(
            work_id,
            1,
            "context",
            "Activate company context",
            "HCAG selected authorized memories, entities, and source evidence for this outcome.",
            "completed" if sufficient else "blocked",
            output={
                "confidence": context.get("confidence", 0),
                "sources": [item.get("source_title", "") for item in evidence],
                "context_envelope_id": envelope_id,
                "action_only": direct_slack_notification,
            },
        )
        if not sufficient:
            self._event(
                work_id,
                "context.blocked",
                "No source-backed context met the work threshold.",
                {"objective": objective},
            )
            return self.get(work_id) or {}

        artifact = (
            {}
            if direct_slack_notification
            else self._save_work_package(work_id, project_id, objective, bounded_context)
        )
        if artifact:
            with connect() as conn:
                conn.execute(
                    "UPDATE memory_work SET artifact_id=?,updated_at=? WHERE id=?",
                    (artifact.get("id", ""), utcnow(), work_id),
                )
        self._step(
            work_id,
            2,
            "deliverable",
            (
                "Prepare exact Slack message"
                if direct_slack_notification
                else "Prepare evidence-backed work package"
            ),
            (
                "Prepared an editable Slack message only from the user's instruction."
                if direct_slack_notification
                else "Created a portable brief containing the outcome, current company context, and exact sources."
            ),
            "completed",
            output={
                "artifact_id": artifact.get("id", ""),
                "artifact": artifact,
                "action_only": direct_slack_notification,
            },
        )
        if external_action:
            risk = "high" if self._contains_any(objective, HIGH_RISK_TERMS) else "medium"
            self._step(
                work_id,
                3,
                "connector_action",
                (
                    "Post approved Slack message"
                    if connector == "slack"
                    else f"Hand off to {connector.title() if connector else 'connected worker'}"
                ),
                (
                    "Review the exact channel and message, then approve the Slack post."
                    if connector == "slack"
                    else "A connected worker may perform this consequential step only after human approval."
                ),
                "pending_approval",
                approval_required=True,
                risk_level=risk,
                connector=connector,
                input={
                    "objective": objective,
                    "context_envelope_id": envelope_id,
                    "artifact_id": artifact.get("id", ""),
                    "message": (
                        (
                            self._direct_slack_message(objective)
                            if direct_slack_notification
                            else self._slack_message(bounded_context)
                        )
                        if connector == "slack"
                        else ""
                    ),
                    "action_only": direct_slack_notification,
                },
            )
            self._step(
                work_id,
                4,
                "verification",
                "Verify outcome and remember the result",
                "The worker reports its result and evidence so MemoryWorks can retain the outcome.",
                "waiting",
                connector=connector,
            )
        else:
            self._step(
                work_id,
                3,
                "verification",
                "Verify evidence coverage",
                "The requested knowledge deliverable was completed without changing an external system.",
                "completed",
                output={
                    "source_count": len(evidence),
                    "memory_count": len(bounded_context["memory_units"]),
                },
            )
        self._event(
            work_id,
            "work.created",
            "Memory Work assembled an evidence-backed execution packet.",
            {
                "status": status,
                "connector": connector,
                "artifact_id": artifact.get("id", ""),
            },
        )
        self.audit.record(
            "memory_work.created",
            f"Created memory work: {objective[:180]}",
            project_id,
            requested_by or "orgmemory",
            {"work_id": work_id, "status": status, "connector": connector},
        )
        return self.get(work_id) or {}

    def approve_and_post_slack(
        self,
        work_id: str,
        step_id: str,
        channel_id: str,
        message: str,
        connector: Any,
        resolved_by: str,
    ) -> dict[str, Any]:
        step = row(
            "SELECT * FROM memory_work_steps WHERE id=? AND work_id=?",
            (step_id, work_id),
        )
        if not step:
            raise ValueError("Work step not found")
        if step["connector"] != "slack" or not step["approval_required"]:
            raise ValueError("This is not an approval-gated Slack step")
        if step["status"] not in {"pending_approval", "approved", "failed"}:
            raise ValueError("Slack step is not awaiting approval")
        self._reject_self_approval(work_id, resolved_by)
        prepared = decode(step).get("input") or {}
        exact_message = (message or prepared.get("message") or "").strip()
        channel_id = channel_id.strip()
        if not channel_id:
            raise ValueError("Choose a Slack channel before approving")
        if not exact_message:
            raise ValueError("The Slack message is empty")

        action_input = {
            **prepared,
            "channel_id": channel_id,
            "message": exact_message,
        }
        now = utcnow()
        with connect() as conn:
            claimed = conn.execute(
                """UPDATE memory_work_steps SET status='posting',input_json=?,
                resolved_by=?,updated_at=? WHERE id=?
                AND status IN ('pending_approval','approved','failed')""",
                (json.dumps(action_input), resolved_by, now, step_id),
            )
            if claimed.rowcount != 1:
                raise ValueError("Slack message is already posting or completed")
            conn.execute(
                "UPDATE memory_work SET status='posting',updated_at=? WHERE id=?",
                (now, work_id),
            )
        self._event(
            work_id,
            "slack.posting",
            "Approval recorded. Posting the reviewed message to Slack.",
            {"step_id": step_id, "channel_id": channel_id, "resolved_by": resolved_by},
        )

        try:
            output = connector.post_message(channel_id, exact_message)
        except Exception as exc:
            error = str(exc)
            with connect() as conn:
                conn.execute(
                    """UPDATE memory_work_steps SET status='failed',output_json=?,
                    updated_at=? WHERE id=?""",
                    (
                        json.dumps(
                            {
                                "error": error,
                                "channel_id": channel_id,
                                "message": exact_message,
                            }
                        ),
                        utcnow(),
                        step_id,
                    ),
                )
                conn.execute(
                    "UPDATE memory_work SET status='post_failed',updated_at=? WHERE id=?",
                    (utcnow(), work_id),
                )
            self._event(
                work_id,
                "slack.failed",
                "Slack did not accept the approved message.",
                {"step_id": step_id, "channel_id": channel_id, "error": error},
            )
            self.audit.record(
                "memory_work.slack_failed",
                "Approved Slack message failed to post",
                (row("SELECT project_id FROM memory_work WHERE id=?", (work_id,)) or {}).get(
                    "project_id"
                ),
                resolved_by,
                {"work_id": work_id, "step_id": step_id, "error": error},
            )
            return self.get(work_id) or {}

        completed_at = utcnow()
        with connect() as conn:
            conn.execute(
                """UPDATE memory_work_steps SET status='completed',output_json=?,
                updated_at=? WHERE id=?""",
                (json.dumps(output), completed_at, step_id),
            )
            conn.execute(
                """UPDATE memory_work_steps SET status='completed',output_json=?,
                updated_at=? WHERE work_id=? AND step_type='verification'
                AND status='waiting'""",
                (
                    json.dumps(
                        {
                            "verified_by": "slack_api",
                            "message_ts": output.get("message_ts", ""),
                            "source_url": output.get("source_url", ""),
                        }
                    ),
                    completed_at,
                    work_id,
                ),
            )
            conn.execute(
                "UPDATE memory_work SET status='completed',updated_at=? WHERE id=?",
                (completed_at, work_id),
            )
        self._event(
            work_id,
            "slack.posted",
            "The approved message was posted to Slack.",
            {"step_id": step_id, **output},
        )
        work = row("SELECT project_id FROM memory_work WHERE id=?", (work_id,)) or {}
        self.audit.record(
            "memory_work.slack_posted",
            "Posted an approved MemoryWorks message to Slack",
            work.get("project_id"),
            resolved_by,
            {
                "work_id": work_id,
                "step_id": step_id,
                "channel_id": output.get("channel_id", channel_id),
                "message_ts": output.get("message_ts", ""),
                "source_url": output.get("source_url", ""),
            },
        )
        return self.get(work_id) or {}

    def list(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if project_id:
            records = rows(
                "SELECT * FROM memory_work WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            records = rows("SELECT * FROM memory_work ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._hydrate(item, include_context=False) for item in records]

    def get(self, work_id: str) -> dict[str, Any] | None:
        item = row("SELECT * FROM memory_work WHERE id=?", (work_id,))
        return self._hydrate(item, include_context=True) if item else None

    @staticmethod
    def _reject_self_approval(work_id: str, resolved_by: str) -> None:
        """The requester of an external action cannot approve it.

        Containment compares the stored requester label with the approving
        identity; server-derived principal IDs replace this in the
        authorization workstream.
        """
        work = row("SELECT requested_by FROM memory_work WHERE id=?", (work_id,))
        if work and resolved_by and (work["requested_by"] or "") == resolved_by:
            raise ValueError("The principal that requested this work cannot approve it")

    def resolve_step(
        self, work_id: str, step_id: str, approved: bool, resolved_by: str
    ) -> dict[str, Any]:
        step = row(
            "SELECT * FROM memory_work_steps WHERE id=? AND work_id=?",
            (step_id, work_id),
        )
        if not step:
            raise ValueError("Work step not found")
        if not step["approval_required"] or step["status"] != "pending_approval":
            raise ValueError("Work step is not awaiting approval")
        self._reject_self_approval(work_id, resolved_by)
        step_status = "approved" if approved else "denied"
        work_status = "ready_for_worker" if approved else "completed_draft_only"
        now = utcnow()
        with connect() as conn:
            conn.execute(
                "UPDATE memory_work_steps SET status=?,resolved_by=?,updated_at=? WHERE id=?",
                (step_status, resolved_by, now, step_id),
            )
            conn.execute(
                "UPDATE memory_work SET status=?,updated_at=? WHERE id=?",
                (work_status, now, work_id),
            )
        self._event(
            work_id,
            f"step.{step_status}",
            f"{step['title']} was {step_status}.",
            {"step_id": step_id, "resolved_by": resolved_by},
        )
        return self.get(work_id) or {}

    def complete_step(
        self,
        work_id: str,
        step_id: str,
        output: dict[str, Any],
        completed_by: str,
    ) -> dict[str, Any]:
        step = row(
            "SELECT * FROM memory_work_steps WHERE id=? AND work_id=?",
            (step_id, work_id),
        )
        if not step:
            raise ValueError("Work step not found")
        if step["approval_required"] and step["status"] != "approved":
            raise ValueError("Consequential work must be approved before completion")
        if step["status"] in {"denied", "completed"}:
            raise ValueError(f"Work step is already {step['status']}")
        now = utcnow()
        with connect() as conn:
            conn.execute(
                """UPDATE memory_work_steps SET status='completed',output_json=?,
                resolved_by=?,updated_at=? WHERE id=?""",
                (json.dumps(output), completed_by, now, step_id),
            )
            remaining = conn.execute(
                """SELECT count(*) FROM memory_work_steps WHERE work_id=?
                AND step_type='connector_action' AND status!='completed'""",
                (work_id,),
            ).fetchone()[0]
            if remaining == 0:
                conn.execute(
                    """UPDATE memory_work_steps SET status='completed',updated_at=?
                    WHERE work_id=? AND step_type='verification' AND status='waiting'""",
                    (now, work_id),
                )
                conn.execute(
                    "UPDATE memory_work SET status='completed',updated_at=? WHERE id=?",
                    (now, work_id),
                )
        self._event(
            work_id,
            "step.completed",
            f"{step['title']} completed and reported evidence.",
            {"step_id": step_id, "completed_by": completed_by, "output": output},
        )
        return self.get(work_id) or {}

    def _save_work_package(
        self, work_id: str, project_id: str, objective: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        evidence = context["evidence"]
        sources = [
            f"- [{item.get('source_title', 'Source')}]({item.get('source_url') or '#'})"
            for item in evidence
        ]
        updates = context.get("updates") or []
        conflicts = context.get("conflicts") or []
        content = (
            f"# Memory Work: {objective}\n\n"
            "## Outcome\n\n"
            f"{objective}\n\n"
            "## Activated company context\n\n"
            f"{context['answer']}\n\n"
            "## Context health\n\n"
            f"- Retrieval confidence: {float(context['confidence']):.0%}\n"
            f"- Current updates considered: {len(updates)}\n"
            f"- Conflicts considered: {len(conflicts)}\n\n"
            "## Source evidence\n\n"
            + ("\n".join(sources) if sources else "- No source evidence.")
            + "\n\n"
            "## Worker contract\n\n"
            "- Use only the supplied context envelope and authorized connectors.\n"
            "- Ask for approval before any external write or command.\n"
            "- Report the final result and exact evidence back to MemoryWorks.\n"
        )
        return self.brain.save_artifact(
            project_id,
            f"Memory Work · {objective[:110]}",
            "brief",
            content,
            list(
                dict.fromkeys(
                    str(item.get("source_id") or "") for item in evidence if item.get("source_id")
                )
            ),
            [item["id"] for item in context["memory_units"] if item.get("id")],
            str(context.get("context_envelope_id") or ""),
        )

    def _hydrate(self, item: dict[str, Any], *, include_context: bool) -> dict[str, Any]:
        payload = decode(item)
        if not include_context:
            payload.pop("context", None)
        payload["steps"] = [
            decode(step)
            for step in rows(
                "SELECT * FROM memory_work_steps WHERE work_id=? ORDER BY position",
                (item["id"],),
            )
        ]
        if include_context:
            for step in payload["steps"]:
                if (
                    step.get("connector") == "slack"
                    and step.get("step_type") == "connector_action"
                    and not (step.get("input") or {}).get("message")
                ):
                    step["input"] = {
                        **(step.get("input") or {}),
                        "message": self._slack_message(payload.get("context") or {}),
                    }
        payload["events"] = [
            decode(event)
            for event in rows(
                "SELECT * FROM memory_work_events WHERE work_id=? ORDER BY created_at",
                (item["id"],),
            )
        ]
        if include_context:
            payload["agent_packet"] = {
                "work_id": payload["id"],
                "objective": payload["objective"],
                "project_id": payload["project_id"],
                "context_envelope_id": payload["context_envelope_id"],
                "target_connector": payload["target_connector"],
                "context": payload.get("context", {}),
                "steps": payload["steps"],
                "constraints": [
                    "Use only authorized source-backed company context.",
                    "Do not perform consequential actions without approval.",
                    "Return the execution result and evidence to MemoryWorks.",
                ],
            }
        return payload

    def _step(
        self,
        work_id: str,
        position: int,
        step_type: str,
        title: str,
        description: str,
        status: str,
        *,
        approval_required: bool = False,
        risk_level: str = "low",
        connector: str = "",
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        now = utcnow()
        with connect() as conn:
            conn.execute(
                """INSERT INTO memory_work_steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_id("wstep"),
                    work_id,
                    position,
                    step_type,
                    title,
                    description,
                    status,
                    int(approval_required),
                    risk_level,
                    connector,
                    json.dumps(input or {}),
                    json.dumps(output or {}),
                    "",
                    now,
                    now,
                ),
            )

    def _event(self, work_id: str, event_type: str, summary: str, payload: dict[str, Any]) -> None:
        with connect() as conn:
            conn.execute(
                "INSERT INTO memory_work_events VALUES (?,?,?,?,?,?)",
                (
                    new_id("wevt"),
                    work_id,
                    event_type,
                    summary,
                    json.dumps(payload),
                    utcnow(),
                ),
            )

    @staticmethod
    def _connector(objective: str) -> str:
        lowered = objective.casefold()
        if re.search(r"\b(?:slack|channel)\b", lowered):
            return "slack"
        if re.search(r"\b(?:email|gmail|outlook|inbox)\b", lowered):
            return "email"
        if MemoryWorkService._is_direct_notification(objective):
            return "slack"
        if re.search(r"\b(?:calendar|schedule|book|invite)\b", lowered):
            return "calendar"
        return next(
            (
                connector
                for connector, terms in CONNECTOR_TERMS.items()
                if any(term in lowered for term in terms)
            ),
            "",
        )

    @staticmethod
    def _contains_any(objective: str, terms: set[str]) -> bool:
        words = set(re.findall(r"[a-z][a-z0-9_-]+", objective.casefold()))
        return bool(words & terms)

    @classmethod
    def _requires_external_action(cls, objective: str, connector: str) -> bool:
        return bool(connector and cls._contains_any(objective, WRITE_TERMS))

    @staticmethod
    def _is_direct_notification(objective: str) -> bool:
        lowered = objective.casefold()
        explicit_slack_copy = re.search(
            r"\b(?:draft|prepare|write|post|send)\s+(?:a\s+)?slack\s+"
            r"(?:message|update|announcement)\s+(?:that|saying)\b",
            lowered,
        )
        return bool(
            explicit_slack_copy
            or (
                re.search(
                    r"\b(?:notify|announce\s+to|message|tell|remind|send\s+(?:a\s+)?"
                    r"(?:notification|message|update)\s+to)\b",
                    lowered,
                )
                and re.search(r"\b(?:team|everyone|group|people|staff|colleagues)\b", lowered)
            )
        )

    @staticmethod
    def _direct_slack_message(objective: str) -> str:
        text = objective.strip().rstrip(".")
        match = re.match(
            r"(?i)^(?:please\s+)?(?:notify|message|tell|remind)\s+(?:the\s+)?"
            r"(?:team|everyone|group|people|staff|colleagues)\s+(?:that\s+)?(?P<body>.+)$",
            text,
        )
        if not match:
            match = re.match(
                r"(?i)^(?:please\s+)?(?:draft|prepare|write|post|send)\s+(?:a\s+)?"
                r"slack\s+(?:message|update|announcement)\s+(?:that|saying)\s+"
                r"(?P<body>.+)$",
                text,
            )
        body = (match.group("body") if match else text).strip()
        if body.casefold().startswith("to "):
            body = f"please {body[3:].strip()}"
        elif body:
            body = body[0].upper() + body[1:]
        return f"*Team notification*\n\nHi everyone — {body.rstrip('.')}."

    @staticmethod
    def _slack_message(context: dict[str, Any]) -> str:
        claims: list[str] = []
        selected_source_titles: set[str] = set()
        for memory in (context.get("memory_units") or [])[:8]:
            content = str(memory.get("content") or "").strip()
            if content and content not in claims:
                claims.append(content)
        code_markers = (
            "{",
            "}",
            "rgba(",
            "font-size:",
            "text-shadow:",
            "box-shadow:",
            "display:",
            "padding:",
            "margin:",
        )
        for raw_line in str(context.get("answer") or "").splitlines():
            source_match = re.search(r"\[([^\]]+)\]\s*$", raw_line)
            line = re.sub(r"^[-*•]\s*", "", raw_line.strip())
            line = re.sub(r"\s*\[[^\]]+\]\s*$", "", line).strip()
            line = line.strip("*_ ")
            if (
                not line
                or line.casefold() in {"source-backed answer", "current source-backed memory"}
                or any(marker in line.casefold() for marker in code_markers)
                or line in claims
            ):
                continue
            claims.append(line)
            if source_match:
                selected_source_titles.add(source_match.group(1))
            if len(claims) >= 8:
                break
        summary = "\n".join(f"• {claim}" for claim in claims)
        if not summary:
            summary = (
                "• MemoryWorks did not find enough current company memory to summarize "
                "this update confidently."
            )
        gaps = ""
        if not context.get("memory_units"):
            gaps = (
                "\n\n*Context gap*\n"
                "• No current atomic memories support the requested architecture, "
                "decision, ownership, or conflict summary."
            )
        conflicts = context.get("conflicts") or []
        if conflicts:
            gaps += (
                "\n\n*Needs review*\n"
                f"• {len(conflicts)} unresolved memory conflict"
                f"{'s' if len(conflicts) != 1 else ''} detected."
            )
        sources = []
        evidence = context.get("evidence") or []
        if selected_source_titles and not context.get("memory_units"):
            evidence = [
                item
                for item in evidence
                if str(item.get("source_title") or item.get("title") or "")
                in selected_source_titles
            ]
        for item in evidence[:6]:
            title = str(item.get("source_title") or item.get("title") or "Company source").replace(
                "|", "—"
            )
            url = str(item.get("source_url") or "")
            sources.append(f"• <{url}|{title}>" if url else f"• {title}")
        source_block = "\n".join(sources) if sources else "• No source link available"
        return (
            "*MemoryWorks project update*\n\n"
            f"{summary}"
            f"{gaps}\n\n"
            "*Source evidence*\n"
            f"{source_block}"
        ).strip()
