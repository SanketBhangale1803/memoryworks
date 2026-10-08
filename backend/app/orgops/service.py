"""Organizational operations over company memory.

Everything a human does by hand when knowledge is scattered — reconstructing a
project, tracing why a decision was made, finding what actually blocks a launch,
noticing that two spaces disagree — expressed as structured operations an agent
can call directly.

No LLM runs in this module. Every answer is derived from stored rows and returns
stable ids, so the same question produces the same result twice and every claim
can be opened back to its source.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import connect, new_id, row, rows, utcnow

# Statuses a task can hold. `blocked` is distinct from `open`: it means something
# else must finish first, which is what makes a dependency graph worth walking.
TASK_STATUSES = {"open", "in_progress", "blocked", "done", "cancelled"}
TASK_PRIORITIES = {"low", "normal", "high", "critical"}
OPEN_TASK_STATUSES = {"open", "in_progress", "blocked"}

# A decision that reads like an approval, and the words that say it is not one
# yet. Both lists are deliberately narrow: a false "approved" is worse than a
# missed one, because it would unblock a launch that is not actually clear.
_APPROVAL_RE = re.compile(
    r"\b(approved|approval granted|signed off|sign-off|cleared for|good to go|"
    r"green ?light(?:ed)?|ship it)\b",
    re.I,
)
_PENDING_RE = re.compile(
    r"\b(pending|not yet approved|not approved|awaiting|blocked on|on hold|"
    r"rejected|denied|needs (?:another|more) review)\b",
    re.I,
)

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "onto",
    "over",
    "our",
    "your",
    "their",
    "its",
    "was",
    "were",
    "are",
    "has",
    "have",
    "had",
    "not",
    "but",
    "all",
    "any",
    "can",
    "will",
    "must",
    "should",
    "does",
    "did",
    "complete",
    "completed",
    "update",
    "updates",
    "task",
    "tasks",
    "still",
    "before",
    "after",
    "team",
    "teams",
    "please",
    "need",
    "needs",
}


def _terms(text: str) -> set[str]:
    return {
        term
        for term in re.split(r"\W+", (text or "").casefold())
        if len(term) > 3 and term not in _STOPWORDS
    }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _decode_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


class OrgOpsService:
    """Structured reads and gated writes across a set of authorized spaces.

    The caller resolves authorization and hands in the space ids it already
    confirmed; this service never widens that set, so a tool cannot reach a
    space its caller cannot see.
    """

    def __init__(self, company_memory: Any):
        self.memory = company_memory
        self._space_names: dict[str, str] = {}

    # ---------------------------------------------------------------- spaces

    def list_spaces(self, space_ids: list[str]) -> list[dict]:
        if not space_ids:
            return []
        placeholders = ",".join("?" for _ in space_ids)
        records = rows(
            f"SELECT id,name,repository,status,created_at,updated_at "
            f"FROM projects WHERE id IN ({placeholders}) ORDER BY name",
            tuple(space_ids),
        )
        counts = self._memory_counts(space_ids)
        tasks = self._task_counts(space_ids)
        return [
            {
                "id": record["id"],
                "name": record["name"],
                "repository": record.get("repository") or "",
                "status": record.get("status") or "ready",
                "memory_count": counts.get(record["id"], {}).get("__total__", 0),
                "memory_types": {
                    key: value
                    for key, value in counts.get(record["id"], {}).items()
                    if key != "__total__"
                },
                "open_tasks": tasks.get(record["id"], 0),
                "updated_at": record.get("updated_at"),
            }
            for record in records
        ]

    def get_space(self, space_id: str, limit: int = 20) -> dict:
        record = row("SELECT * FROM projects WHERE id=?", (space_id,))
        if not record:
            raise LookupError("Space not found")
        units = self._units(space_id)
        units.sort(key=lambda unit: unit.get("updated_at") or "", reverse=True)
        counts = self._memory_counts([space_id]).get(space_id, {})
        return {
            "id": record["id"],
            "name": record["name"],
            "repository": record.get("repository") or "",
            "memory_count": counts.get("__total__", 0),
            "memory_types": {key: value for key, value in counts.items() if key != "__total__"},
            "open_tasks": self._task_counts([space_id]).get(space_id, 0),
            "recent_memories": [self.public_memory(unit) for unit in units[:limit]],
        }

    # -------------------------------------------------------------- memories

    def public_memory(self, unit: dict) -> dict:
        """One memory in the shape every tool returns it: id first, source attached."""
        scope = unit.get("scope") or _decode_json(unit.get("scope_json"), {})
        source_ids = unit.get("source_ids") or _decode_json(unit.get("source_ids_json"), [])
        return {
            "id": unit.get("id"),
            "space_id": unit.get("project_id"),
            "space_name": self._space_name(unit.get("project_id", "")),
            "type": unit.get("type"),
            "title": unit.get("subject"),
            "content": unit.get("content"),
            "scope": scope,
            "confidence": unit.get("confidence"),
            "source_ids": source_ids,
            "created_at": unit.get("created_at"),
            "updated_at": unit.get("updated_at"),
            "valid_from": unit.get("valid_from"),
            "valid_to": unit.get("valid_to"),
        }

    def get_recent_changes(self, space_ids: list[str], since: str = "", limit: int = 40) -> dict:
        cutoff = _parse_ts(since) or (datetime.now(UTC) - timedelta(days=7))
        changes: list[dict] = []
        for space_id in space_ids:
            for unit in self._units(space_id):
                stamp = _parse_ts(unit.get("updated_at")) or _parse_ts(unit.get("created_at"))
                if stamp and stamp >= cutoff:
                    entry = self.public_memory(unit)
                    entry["change"] = (
                        "created"
                        if (unit.get("created_at") == unit.get("updated_at"))
                        else "revised"
                    )
                    changes.append(entry)
        changes.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return {
            "since": cutoff.isoformat(),
            "space_ids": space_ids,
            "count": len(changes),
            "changes": changes[:limit],
        }

    def get_decisions(
        self, space_ids: list[str], status: str = "", since: str = "", limit: int = 40
    ) -> dict:
        cutoff = _parse_ts(since)
        decisions: list[dict] = []
        for space_id in space_ids:
            for unit in self._units(space_id, kind="decision"):
                stamp = _parse_ts(unit.get("updated_at"))
                if cutoff and stamp and stamp < cutoff:
                    continue
                entry = self.public_memory(unit)
                entry["decision_status"] = self._decision_status(unit)
                if status and entry["decision_status"] != status:
                    continue
                entry["superseded_by"] = self._superseded_by(unit["id"])
                decisions.append(entry)
        decisions.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return {"count": len(decisions), "decisions": decisions[:limit]}

    def _decision_status(self, unit: dict) -> str:
        if unit.get("valid_to"):
            return "superseded"
        text = f"{unit.get('subject', '')} {unit.get('content', '')}"
        if _PENDING_RE.search(text):
            return "pending"
        if _APPROVAL_RE.search(text):
            return "approved"
        return "current"

    def _superseded_by(self, memory_id: str) -> list[str]:
        return [
            record["from_memory_id"]
            for record in rows(
                "SELECT from_memory_id FROM memory_relationships "
                "WHERE to_memory_id=? AND relationship IN ('UPDATES','CONTRADICTS')",
                (memory_id,),
            )
        ]

    # ------------------------------------------------------------ provenance

    def get_provenance(self, memory_id: str) -> dict:
        unit = self.memory.get(memory_id)
        if not unit:
            raise LookupError("Memory not found")
        source_ids = unit.get("source_ids") or []
        sources: list[dict] = []
        for source_id in source_ids:
            item = row(
                "SELECT id,source_type,source_title,source_url,metadata_json,created_at "
                "FROM knowledge_items WHERE id=? OR source_id=? LIMIT 1",
                (source_id, source_id),
            )
            metadata = _decode_json((item or {}).get("metadata_json"), {})
            sources.append(
                {
                    "id": source_id,
                    "type": metadata.get("record_kind")
                    or (item or {}).get("source_type", "manual"),
                    "title": (item or {}).get("source_title", source_id),
                    "url": (item or {}).get("source_url", ""),
                    "captured_at": (item or {}).get("created_at", unit.get("created_at")),
                }
            )
        relations = [
            {
                "type": record["relationship"],
                "direction": "outgoing" if record["from_memory_id"] == memory_id else "incoming",
                "target_id": (
                    record["to_memory_id"]
                    if record["from_memory_id"] == memory_id
                    else record["from_memory_id"]
                ),
                "linked_at": record.get("created_at"),
            }
            for record in rows(
                "SELECT * FROM memory_relationships WHERE from_memory_id=? OR to_memory_id=?",
                (memory_id, memory_id),
            )
        ]
        for relation in relations:
            target = self.memory.get(relation["target_id"])
            relation["target_title"] = (target or {}).get("subject", "")
            relation["target_type"] = (target or {}).get("type", "")
        return {
            "memory": self.public_memory(unit),
            "sources": sources,
            "relations": relations,
            "derived_tasks": [
                self.public_task(task)
                for task in rows(
                    "SELECT * FROM org_tasks WHERE project_id=?", (unit["project_id"],)
                )
                if memory_id in _decode_json(task.get("source_memory_ids_json"), [])
            ],
        }

    def get_reasoning_chain(self, space_ids: list[str], topic: str, limit: int = 8) -> dict:
        """Order the evidence behind a topic the way the organization arrived at it.

        Not a similarity ranking: the chain follows the recorded relationships
        between memories, so what comes back is the sequence of steps — a
        requirement, a discussion, the decision it produced, the work it created
        — rather than a pile of things that mention the same words.
        """
        terms = _terms(topic)
        scored: list[tuple[float, dict]] = []
        for space_id in space_ids:
            for unit in self._units(space_id):
                score = self._score(unit, terms)
                if score > 0:
                    scored.append((score, unit))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if not scored:
            return {"topic": topic, "steps": [], "edges": []}
        # A chain is an argument, not a result list. Anything scoring well below
        # the best match is a passing mention and only makes the trace harder to
        # read, so it is dropped before the graph is walked.
        floor = scored[0][0] * 0.4
        seeds = [unit for score, unit in scored[:limit] if score >= floor]

        by_id = {unit["id"]: unit for unit in seeds}
        # Pull in directly linked memories even when their wording misses the
        # topic — a decision's justification rarely repeats the decision's words.
        for unit in list(seeds):
            for record in rows(
                "SELECT * FROM memory_relationships WHERE from_memory_id=? OR to_memory_id=?",
                (unit["id"], unit["id"]),
            ):
                for candidate_id in (record["from_memory_id"], record["to_memory_id"]):
                    if candidate_id in by_id:
                        continue
                    candidate = self.memory.get(candidate_id)
                    if candidate and candidate["project_id"] in space_ids:
                        by_id[candidate_id] = candidate

        edges = [
            {
                "from": record["from_memory_id"],
                "to": record["to_memory_id"],
                "type": record["relationship"],
            }
            for record in rows(
                "SELECT * FROM memory_relationships WHERE from_memory_id IN "
                f"({','.join('?' for _ in by_id)})",
                tuple(by_id),
            )
            if record["to_memory_id"] in by_id
        ]
        # Once the relationships are known, a node nobody links to is context,
        # not a step in the reasoning — provided a real chain survives without it.
        linked = {edge["from"] for edge in edges} | {edge["to"] for edge in edges}
        if len(linked) >= 3:
            by_id = {memory_id: unit for memory_id, unit in by_id.items() if memory_id in linked}
            edges = [edge for edge in edges if edge["from"] in by_id and edge["to"] in by_id]
        ordered = self._order_chain(by_id, edges)
        return {
            "topic": topic,
            "steps": [
                {
                    "position": index + 1,
                    "memory": self.public_memory(by_id[memory_id]),
                    "role": self._chain_role(by_id[memory_id]),
                }
                for index, memory_id in enumerate(ordered)
            ],
            "edges": edges,
        }

    @staticmethod
    def _order_chain(by_id: dict[str, dict], edges: list[dict]) -> list[str]:
        """Topological order over the evidence, oldest first when unconstrained."""
        incoming = {memory_id: 0 for memory_id in by_id}
        outgoing: dict[str, list[str]] = {memory_id: [] for memory_id in by_id}
        for edge in edges:
            if edge["from"] in by_id and edge["to"] in by_id:
                outgoing[edge["from"]].append(edge["to"])
                incoming[edge["to"]] += 1
        ready = sorted(
            (memory_id for memory_id, count in incoming.items() if count == 0),
            key=lambda memory_id: by_id[memory_id].get("created_at") or "",
        )
        ordered: list[str] = []
        while ready:
            memory_id = ready.pop(0)
            ordered.append(memory_id)
            for target in outgoing[memory_id]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
                    ready.sort(key=lambda item: by_id[item].get("created_at") or "")
        # A cycle in the relationship graph must not silently drop evidence.
        for memory_id in sorted(by_id, key=lambda item: by_id[item].get("created_at") or ""):
            if memory_id not in ordered:
                ordered.append(memory_id)
        return ordered

    @staticmethod
    def _chain_role(unit: dict) -> str:
        return {
            "policy": "requirement",
            "open_question": "question raised",
            "decision": "decision",
            "incident": "incident",
            "dependency": "dependency",
            "ownership": "owner",
            "procedure": "procedure",
        }.get(unit.get("type", ""), "context")

    # ------------------------------------------------------------------ people

    def get_people_context(
        self, workspace_id: str, space_ids: list[str], person_id: str = "", query: str = ""
    ) -> dict:
        members = rows(
            "SELECT u.id,u.email,u.display_name,m.role,m.status FROM workspace_members m "
            "JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?",
            (workspace_id,),
        )
        ownership: dict[str, list[dict]] = {}
        for space_id in space_ids:
            for unit in self._units(space_id, kind="ownership"):
                ownership.setdefault(unit["subject"].casefold(), []).append(
                    self.public_memory(unit)
                )

        tasks = self.get_open_tasks(space_ids).get("tasks", [])
        people: list[dict] = []
        for member in members:
            name = member.get("display_name") or member.get("email", "")
            owned = [task for task in tasks if task["owner"].casefold() == name.casefold()]
            areas = [
                entry
                for records in ownership.values()
                for entry in records
                if name.casefold() in entry["content"].casefold()
            ]
            people.append(
                {
                    "id": member["id"],
                    "name": name,
                    "email": member.get("email", ""),
                    "workspace_role": member.get("role", "member"),
                    "owns": [entry["title"] for entry in areas],
                    "open_tasks": owned,
                    "evidence": [entry["id"] for entry in areas],
                }
            )
        # Owners recorded in memory who have no workspace account yet are still
        # the person to talk to; leaving them out would hide the real answer.
        known = {person["name"].casefold() for person in people}
        for records in ownership.values():
            for entry in records:
                owner = self._owner_from_text(entry["content"])
                if owner and owner.casefold() not in known:
                    known.add(owner.casefold())
                    people.append(
                        {
                            "id": f"person_{re.sub(r'[^a-z0-9]+', '_', owner.casefold()).strip('_')}",
                            "name": owner,
                            "email": "",
                            "workspace_role": "recorded in memory",
                            "owns": [entry["title"]],
                            "open_tasks": [
                                task
                                for task in tasks
                                if task["owner"].casefold() == owner.casefold()
                            ],
                            "evidence": [entry["id"]],
                        }
                    )
        if person_id:
            people = [person for person in people if person["id"] == person_id]
        if query:
            needle = query.casefold()
            people = [
                person
                for person in people
                if needle in person["name"].casefold()
                or needle in person["email"].casefold()
                or any(needle in area.casefold() for area in person["owns"])
            ]
        return {"count": len(people), "people": people}

    @staticmethod
    def _owner_from_text(text: str) -> str:
        match = re.search(r"owned by ([A-Z][A-Za-z'-]+(?: [A-Z][A-Za-z'-]+)*)", text or "")
        return match.group(1).strip(" .,;") if match else ""

    def resolve_subject_owner(
        self, subject: str, service: str, ownership_units: list[dict]
    ) -> dict:
        terms = _terms(f"{subject or ''} {service or ''}")
        best: tuple[float, dict] | None = None
        for candidate in ownership_units:
            score = self._score(candidate, terms)
            if score > 0 and (best is None or score > best[0]):
                best = (score, candidate)
        if not best:
            return {"owner": "", "evidence": []}
        return {
            "owner": self._owner_from_text(best[1].get("content", ""))
            or best[1].get("subject", ""),
            "evidence": [best[1].get("id", "")],
        }

    def get_owner(self, space_ids: list[str], object_id: str) -> dict:
        task = row("SELECT * FROM org_tasks WHERE id=?", (object_id,))
        if task:
            return {
                "object_id": object_id,
                "object_type": "task",
                "owner": task.get("owner", ""),
                "evidence": _decode_json(task.get("source_memory_ids_json"), []),
            }
        unit = self.memory.get(object_id)
        if not unit:
            raise LookupError("No task or memory with that id")
        scope = unit.get("scope") or {}
        candidates: list[dict] = []
        for space_id in space_ids:
            candidates.extend(self._units(space_id, kind="ownership"))
        resolved = self.resolve_subject_owner(
            str(unit.get("subject", "")), str(scope.get("service", "")), candidates
        )
        if not resolved["owner"]:
            return {"object_id": object_id, "object_type": "memory", "owner": "", "evidence": []}
        best_id = resolved["evidence"][0] if resolved["evidence"] else ""
        best_unit = next((c for c in candidates if c.get("id") == best_id), None)
        if best_unit is None:
            return {"object_id": object_id, "object_type": "memory", "owner": "", "evidence": []}
        return {
            "object_id": object_id,
            "object_type": "memory",
            "owner": resolved["owner"],
            "evidence": resolved["evidence"],
            "source_memory": self.public_memory(best_unit),
        }

    # ------------------------------------------------------------------ tasks

    def public_task(self, task: dict) -> dict:
        return {
            "id": task["id"],
            "space_id": task["project_id"],
            "space_name": self._space_name(task["project_id"]),
            "title": task["title"],
            "description": task.get("description", ""),
            "status": task.get("status", "open"),
            "owner": task.get("owner", ""),
            "priority": task.get("priority", "normal"),
            "kind": task.get("kind", "task"),
            "depends_on": _decode_json(task.get("depends_on_json"), []),
            "source_memory_ids": _decode_json(task.get("source_memory_ids_json"), []),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }

    def get_open_tasks(
        self,
        space_ids: list[str],
        assignee: str = "",
        priority: str = "",
        status: str = "",
        limit: int = 100,
    ) -> dict:
        tasks = [self.public_task(task) for task in self._tasks(space_ids)]
        if status:
            tasks = [task for task in tasks if task["status"] == status]
        else:
            tasks = [task for task in tasks if task["status"] in OPEN_TASK_STATUSES]
        if assignee:
            tasks = [task for task in tasks if assignee.casefold() in task["owner"].casefold()]
        if priority:
            tasks = [task for task in tasks if task["priority"] == priority]
        order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        tasks.sort(key=lambda task: (order.get(task["priority"], 9), task["title"]))
        return {"count": len(tasks), "tasks": tasks[:limit]}

    def get_task_dependencies(self, task_id: str) -> dict:
        task = row("SELECT * FROM org_tasks WHERE id=?", (task_id,))
        if not task:
            raise LookupError("Task not found")
        graph = {item["id"]: item for item in self._tasks([task["project_id"]])}
        requires = [
            self.public_task(graph[dep])
            for dep in _decode_json(task.get("depends_on_json"), [])
            if dep in graph
        ]
        required_by = [
            self.public_task(other)
            for other in graph.values()
            if task_id in _decode_json(other.get("depends_on_json"), [])
        ]
        return {
            "task": self.public_task(task),
            "requires": requires,
            "required_by": required_by,
        }

    # ------------------------------------------------------- dependency graph

    def get_dependency_graph(self, space_ids: list[str]) -> dict:
        tasks = self._tasks(space_ids)
        by_id = {task["id"]: task for task in tasks}
        nodes = [
            {
                "id": task["id"],
                "kind": task.get("kind", "task"),
                "label": task["title"],
                "status": task.get("status", "open"),
                "owner": task.get("owner", ""),
                "priority": task.get("priority", "normal"),
                "space_id": task["project_id"],
                "space_name": self._space_name(task["project_id"]),
                "evidence": _decode_json(task.get("source_memory_ids_json"), []),
            }
            for task in tasks
        ]
        edges = [
            {"from": dep, "to": task["id"], "type": "REQUIRED_FOR"}
            for task in tasks
            for dep in _decode_json(task.get("depends_on_json"), [])
            if dep in by_id
        ]
        return {
            "space_ids": space_ids,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

    def find_blockers(self, space_ids: list[str]) -> dict:
        """Anything unfinished that something else is waiting on.

        A task nobody depends on is late work. A task other work is waiting on
        is a blocker, and the chain it sits under is why it matters — which is
        the part a person reconstructs by hand across several tools.
        """
        tasks = {task["id"]: task for task in self._tasks(space_ids)}
        dependents: dict[str, list[str]] = {task_id: [] for task_id in tasks}
        for task_id, task in tasks.items():
            for dep in _decode_json(task.get("depends_on_json"), []):
                if dep in dependents:
                    dependents[dep].append(task_id)

        blockers: list[dict] = []
        for task_id, task in tasks.items():
            if task.get("status") in {"done", "cancelled"} or not dependents[task_id]:
                continue
            # Only report the root of a stall. A deploy waiting on a review is a
            # consequence, not a blocker, and listing it as one is exactly the
            # noise that makes a human re-derive the answer by hand.
            unmet = [
                dep
                for dep in _decode_json(task.get("depends_on_json"), [])
                if dep in tasks and tasks[dep].get("status") not in {"done", "cancelled"}
            ]
            if unmet:
                continue
            # A step or goal whose gates have all cleared is waiting to run, not
            # waiting on a decision. Reporting it as a blocker would contradict
            # the readiness board, which correctly calls that state "ready".
            if task.get("kind") in {"step", "goal"}:
                continue
            chain = self._chain_to_goal(task_id, dependents, tasks)
            blocked = self._transitive_dependents(task_id, dependents)
            blockers.append(
                {
                    "task": self.public_task(task),
                    "blocks": [self.public_task(tasks[other]) for other in blocked],
                    "chain": chain,
                    "severity": (
                        "critical"
                        if task.get("priority") == "critical" or len(blocked) >= 2
                        else "high"
                    ),
                    "evidence": _decode_json(task.get("source_memory_ids_json"), []),
                }
            )
        blockers.sort(key=lambda item: (item["severity"] != "critical", -len(item["blocks"])))
        return {"count": len(blockers), "blockers": blockers}

    @staticmethod
    def _transitive_dependents(task_id: str, dependents: dict[str, list[str]]) -> list[str]:
        seen: list[str] = []
        queue = list(dependents.get(task_id, []))
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.append(current)
            queue.extend(dependents.get(current, []))
        return seen

    def _chain_to_goal(
        self, task_id: str, dependents: dict[str, list[str]], tasks: dict[str, dict]
    ) -> list[dict]:
        """The path from this task up to the outcome that ultimately waits on it."""
        chain = [task_id]
        current = task_id
        guard = 0
        while dependents.get(current) and guard < 20:
            guard += 1
            current = dependents[current][0]
            chain.append(current)
        return [
            {
                "id": item,
                "label": tasks[item]["title"],
                "status": tasks[item].get("status", "open"),
                "space_name": self._space_name(tasks[item]["project_id"]),
            }
            for item in chain
            if item in tasks
        ]

    # ---------------------------------------------------------- contradictions

    def find_conflicts(self, space_ids: list[str], topic: str = "") -> dict:
        """Where a task's tracked state and the organization's own record disagree.

        The comparison is anchored to the evidence a task already cites, not to
        words it happens to share with something else. A task is in conflict when
        a newer memory contradicts, updates, or restates the very record the task
        is tracking — and that newer memory reads as settled. That is precisely
        the case a person misses: the meeting happened, the tracker never moved.
        """
        topic_terms = _terms(topic) if topic else set()
        memories = {unit["id"]: unit for space_id in space_ids for unit in self._units(space_id)}

        conflicts: list[dict] = []
        for task in self._tasks(space_ids):
            if task.get("status") not in OPEN_TASK_STATUSES:
                continue
            evidence_ids = [
                memory_id
                for memory_id in _decode_json(task.get("source_memory_ids_json"), [])
                if memory_id in memories
            ]
            if not evidence_ids:
                continue
            if topic_terms:
                task_terms = _terms(f"{task['title']} {task.get('description', '')}")
                if not (task_terms & topic_terms):
                    continue
            task_stamp = _parse_ts(task.get("updated_at"))
            found: tuple[str, dict, list[str]] | None = None
            for evidence_id in evidence_ids:
                evidence = memories[evidence_id]
                for candidate, basis, shared in self._contradicting(evidence, memories):
                    stamp = _parse_ts(candidate.get("updated_at"))
                    if task_stamp and stamp and stamp <= task_stamp:
                        continue
                    found = (basis, candidate, shared)
                    break
                if found:
                    break
            if not found:
                continue
            basis, unit, shared = found
            conflicts.append(
                {
                    "id": f"conflict_{task['id']}",
                    "task": self.public_task(task),
                    "tracked_state": task.get("status"),
                    "tracked_source": self.public_memory(memories[evidence_ids[0]]),
                    "recorded_state": "settled",
                    "recorded_at": unit.get("updated_at"),
                    "basis": basis,
                    "source": self.public_memory(unit),
                    "matched_terms": shared,
                    "resolution": {
                        "op": "update_task",
                        "task_id": task["id"],
                        "status": "done",
                        "reason": (
                            f"“{unit['subject']}” ({self._space_name(unit['project_id'])}, "
                            f"{(unit.get('updated_at') or '')[:16]}) settles the record this task "
                            "tracks. The task was never updated."
                        ),
                        "source_memory_ids": [unit["id"]],
                    },
                }
            )
        return {"count": len(conflicts), "conflicts": conflicts}

    def get_conflict(self, space_ids: list[str], conflict_id: str) -> dict:
        """One conflict by id, with its recorded resolution verbatim.

        Agents reconcile through this reference instead of retyping a
        resolution from a truncated observation: the plan a person is asked to
        approve is the exact resolution the system computed, not a model's
        transcription of it.
        """
        if not conflict_id:
            raise ValueError("conflict_id is required")
        for conflict in self.find_conflicts(space_ids)["conflicts"]:
            if conflict["id"] == conflict_id:
                return conflict
        raise ValueError(f"No open conflict {conflict_id} in the authorized spaces")

    def _contradicting(
        self, evidence: dict, memories: dict[str, dict]
    ) -> list[tuple[dict, str, list[str]]]:
        """Newer, settled memories that speak to the same record as `evidence`."""
        results: list[tuple[dict, str, list[str]]] = []
        seen: set[str] = set()

        def settled(unit: dict) -> bool:
            text = f"{unit.get('subject', '')} {unit.get('content', '')}"
            return bool(_APPROVAL_RE.search(text)) and not _PENDING_RE.search(text)

        # A recorded contradiction is the strongest possible signal: the
        # organization already noticed these two statements cannot both hold.
        for record in rows(
            "SELECT * FROM memory_relationships WHERE (from_memory_id=? OR to_memory_id=?) "
            "AND relationship IN ('CONTRADICTS','UPDATES')",
            (evidence["id"], evidence["id"]),
        ):
            other_id = (
                record["to_memory_id"]
                if record["from_memory_id"] == evidence["id"]
                else record["from_memory_id"]
            )
            other = memories.get(other_id)
            if other and other_id not in seen and settled(other):
                seen.add(other_id)
                results.append((other, record["relationship"].casefold(), []))

        # Failing an explicit edge, a memory whose subject restates the tracked
        # record — same nouns, opposite conclusion — is the same situation with
        # nobody having drawn the link yet.
        evidence_terms = _terms(evidence.get("subject", ""))
        for other in memories.values():
            if other["id"] == evidence["id"] or other["id"] in seen or not settled(other):
                continue
            shared = evidence_terms & _terms(other.get("subject", ""))
            if len(shared) >= 3:
                seen.add(other["id"])
                results.append((other, "same subject", sorted(shared)))

        results.sort(key=lambda entry: entry[0].get("updated_at") or "", reverse=True)
        return results

    def find_stale_information(
        self, space_ids: list[str], topic: str = "", max_age_days: int = 90
    ) -> dict:
        cutoff = datetime.now(UTC) - timedelta(days=max(1, max_age_days))
        terms = _terms(topic) if topic else set()
        stale: list[dict] = []
        for space_id in space_ids:
            for unit in self._units(space_id):
                if terms and not (terms & _terms(f"{unit['subject']} {unit['content']}")):
                    continue
                stamp = _parse_ts(unit.get("updated_at"))
                if stamp and stamp < cutoff:
                    entry = self.public_memory(unit)
                    entry["age_days"] = (datetime.now(UTC) - stamp).days
                    entry["superseded_by"] = self._superseded_by(unit["id"])
                    stale.append(entry)
        stale.sort(key=lambda item: item["age_days"], reverse=True)
        return {"count": len(stale), "max_age_days": max_age_days, "stale": stale}

    # ------------------------------------------------------------- assembled

    def get_project_context(self, space_ids: list[str], primary_space_id: str = "") -> dict:
        """One call that replaces opening every space by hand."""
        spaces = self.list_spaces(space_ids)
        decisions = self.get_decisions(space_ids, limit=8)["decisions"]
        tasks = self.get_open_tasks(space_ids)["tasks"]
        recent = self.get_recent_changes(space_ids, limit=10)["changes"]
        questions: list[dict] = []
        for space_id in space_ids:
            questions.extend(
                self.public_memory(unit) for unit in self._units(space_id, kind="open_question")
            )
        blockers = self.find_blockers(space_ids)["blockers"]
        memory_total = sum(space["memory_count"] for space in spaces)
        return {
            "primary_space_id": primary_space_id or (space_ids[0] if space_ids else ""),
            "spaces": spaces,
            "memory_count": memory_total,
            "decisions": decisions,
            "open_tasks": tasks,
            "unresolved": questions,
            "recent_changes": recent,
            "blockers": blockers,
            "next_best_action": self._next_action(blockers, tasks),
        }

    @staticmethod
    def _next_action(blockers: list[dict], tasks: list[dict]) -> dict:
        if blockers:
            top = blockers[0]
            return {
                "action": top["task"]["title"],
                "why": (f"{len(top['blocks'])} other item(s) cannot proceed until this closes."),
                "task_id": top["task"]["id"],
                "owner": top["task"]["owner"],
            }
        if tasks:
            return {
                "action": tasks[0]["title"],
                "why": "Highest-priority item with nothing waiting on it.",
                "task_id": tasks[0]["id"],
                "owner": tasks[0]["owner"],
            }
        return {"action": "", "why": "Nothing open in these spaces.", "task_id": "", "owner": ""}

    def get_readiness(self, space_ids: list[str]) -> dict:
        """The launch checklist, computed rather than maintained.

        Readiness is judged over the goal's dependency closure, so work that is
        genuinely open but that nothing is waiting on does not hold a launch
        hostage, and a step whose gates have all cleared reads as ready rather
        than as still blocked.
        """
        tasks = {task["id"]: task for task in self._tasks(space_ids)}
        goals = [task for task in tasks.values() if task.get("kind") == "goal"]
        if not goals:
            goals = [
                task
                for task in tasks.values()
                if not any(
                    task["id"] in _decode_json(other.get("depends_on_json"), [])
                    for other in tasks.values()
                )
            ]
        closure: list[str] = []
        queue = [goal["id"] for goal in goals]
        while queue:
            current = queue.pop(0)
            if current in closure or current not in tasks:
                continue
            closure.append(current)
            queue.extend(_decode_json(tasks[current].get("depends_on_json"), []))

        # States are computed dependency-first, because whether something is
        # blocked depends on what its dependencies resolved to — not on the
        # status field somebody last remembered to edit.
        order: list[str] = []
        pending = list(closure)
        guard = 0
        while pending and guard < 200:
            guard += 1
            task_id = pending.pop(0)
            deps = [
                dep
                for dep in _decode_json(tasks[task_id].get("depends_on_json"), [])
                if dep in closure
            ]
            if all(dep in order for dep in deps):
                order.append(task_id)
            else:
                pending.append(task_id)
        for task_id in closure:
            if task_id not in order:
                order.append(task_id)

        state: dict[str, str] = {}
        unmet_by_task: dict[str, list[str]] = {}
        for task_id in order:
            task = tasks[task_id]
            deps = [dep for dep in _decode_json(task.get("depends_on_json"), []) if dep in tasks]
            unmet = [dep for dep in deps if state.get(dep, "open") in {"open", "blocked"}]
            unmet_by_task[task_id] = unmet
            if task.get("status") in {"done", "cancelled"}:
                state[task_id] = "done"
            elif unmet:
                state[task_id] = "blocked"
            elif task.get("kind") in {"step", "goal"}:
                # Every gate in front of it has cleared. It is waiting to run,
                # not waiting on anyone.
                state[task_id] = "ready"
            else:
                state[task_id] = "open"

        checklist = [
            {
                "id": task_id,
                "label": tasks[task_id]["title"],
                "kind": tasks[task_id].get("kind", "task"),
                "space_id": tasks[task_id]["project_id"],
                "space_name": self._space_name(tasks[task_id]["project_id"]),
                "owner": tasks[task_id].get("owner", ""),
                "state": state[task_id],
                "status": tasks[task_id].get("status", "open"),
                "blocked_by": [
                    {"id": dep, "label": tasks[dep]["title"]} for dep in unmet_by_task[task_id]
                ],
                "evidence": _decode_json(tasks[task_id].get("source_memory_ids_json"), []),
            }
            for task_id in order
        ]
        blockers = self.find_blockers(space_ids)["blockers"]
        outstanding = [entry for entry in checklist if entry["state"] in {"open", "blocked"}]
        return {
            "ready": not outstanding,
            "status": "READY" if not outstanding else "NOT READY",
            "goal": ({"id": goals[0]["id"], "label": goals[0]["title"]} if goals else None),
            "blocker_count": len([item for item in blockers if item["task"]["id"] in closure]),
            "outstanding": [entry["label"] for entry in outstanding],
            "checklist": checklist,
            "blockers": [item for item in blockers if item["task"]["id"] in closure],
        }

    # ------------------------------------------------------------ gated writes

    def propose_plan(
        self,
        workspace_id: str,
        user_id: str,
        space_id: str,
        summary: str,
        operations: list[dict],
        origin: str = "webmcp",
    ) -> dict:
        """Record what an agent wants to change. Nothing is applied here."""
        cleaned = [self._validate_operation(operation) for operation in operations]
        if not cleaned:
            raise ValueError("A plan needs at least one operation")
        plan_id, now = new_id("plan"), utcnow()
        with connect() as conn:
            conn.execute(
                "INSERT INTO org_action_plans "
                "(id,workspace_id,project_id,created_by,origin,summary,operations_json,"
                "results_json,status,created_at) VALUES (?,?,?,?,?,?,?,'[]','pending_approval',?)",
                (
                    plan_id,
                    workspace_id,
                    space_id,
                    user_id,
                    origin,
                    summary.strip(),
                    json.dumps(cleaned),
                    now,
                ),
            )
        return self.get_plan(plan_id)

    @staticmethod
    def _validate_operation(operation: dict) -> dict:
        op = str(operation.get("op", "")).strip()
        if op == "update_task":
            if not operation.get("task_id"):
                raise ValueError("update_task needs task_id")
            status = operation.get("status", "")
            if status and status not in TASK_STATUSES:
                raise ValueError(f"status must be one of {sorted(TASK_STATUSES)}")
            priority = operation.get("priority", "")
            if priority and priority not in TASK_PRIORITIES:
                raise ValueError(f"priority must be one of {sorted(TASK_PRIORITIES)}")
            return {
                "op": op,
                "task_id": operation["task_id"],
                "status": status,
                "owner": operation.get("owner", ""),
                "priority": priority,
                "description": operation.get("description", ""),
                "reason": operation.get("reason", ""),
                "source_memory_ids": list(operation.get("source_memory_ids") or []),
            }
        if op == "create_task":
            if not operation.get("title"):
                raise ValueError("create_task needs a title")
            if not operation.get("space_id"):
                raise ValueError("create_task needs a space_id")
            priority = operation.get("priority", "normal")
            if priority not in TASK_PRIORITIES:
                raise ValueError(f"priority must be one of {sorted(TASK_PRIORITIES)}")
            return {
                "op": op,
                "space_id": operation["space_id"],
                "title": operation["title"],
                "description": operation.get("description", ""),
                "owner": operation.get("owner", ""),
                "priority": priority,
                "depends_on": list(operation.get("depends_on") or []),
                "source_memory_ids": list(operation.get("source_memory_ids") or []),
                "reason": operation.get("reason", ""),
            }
        if op == "add_memory":
            for field in ("space_id", "type", "title", "content"):
                if not operation.get(field):
                    raise ValueError(f"add_memory needs {field}")
            return {
                "op": op,
                "space_id": operation["space_id"],
                "type": operation["type"],
                "title": operation["title"],
                "content": operation["content"],
                "source_memory_ids": list(operation.get("source_memory_ids") or []),
                "reason": operation.get("reason", ""),
            }
        raise ValueError("op must be one of create_task, update_task, add_memory")

    def get_plan(self, plan_id: str) -> dict:
        record = row("SELECT * FROM org_action_plans WHERE id=?", (plan_id,))
        if not record:
            raise LookupError("Plan not found")
        return self.public_plan(record)

    def public_plan(self, record: dict) -> dict:
        operations = _decode_json(record.get("operations_json"), [])
        return {
            "id": record["id"],
            "space_id": record.get("project_id", ""),
            "summary": record.get("summary", ""),
            "status": record.get("status", "pending_approval"),
            "origin": record.get("origin", ""),
            "operations": [
                {**operation, "preview": self._describe_operation(operation)}
                for operation in operations
            ],
            "results": _decode_json(record.get("results_json"), []),
            "created_at": record.get("created_at"),
            "resolved_at": record.get("resolved_at"),
            "resolved_by": record.get("resolved_by", ""),
        }

    def _describe_operation(self, operation: dict) -> str:
        if operation["op"] == "update_task":
            task = row("SELECT title FROM org_tasks WHERE id=?", (operation["task_id"],))
            title = (task or {}).get("title", operation["task_id"])
            if operation.get("status") == "done":
                return f"Mark “{title}” complete"
            if operation.get("status"):
                return f"Set “{title}” to {operation['status']}"
            return f"Update “{title}”"
        if operation["op"] == "create_task":
            return (
                f"Create {operation.get('priority', 'normal')}-priority task “{operation['title']}”"
            )
        return f"Record {operation['type']} “{operation['title']}” in company memory"

    def list_plans(self, workspace_id: str, status: str = "") -> list[dict]:
        if status:
            records = rows(
                "SELECT * FROM org_action_plans WHERE workspace_id=? AND status=? "
                "ORDER BY created_at DESC LIMIT 50",
                (workspace_id, status),
            )
        else:
            records = rows(
                "SELECT * FROM org_action_plans WHERE workspace_id=? "
                "ORDER BY created_at DESC LIMIT 50",
                (workspace_id,),
            )
        return [self.public_plan(record) for record in records]

    def approve_plan(self, plan_id: str, workspace_id: str, approver: str) -> dict:
        record = row("SELECT * FROM org_action_plans WHERE id=?", (plan_id,))
        if not record:
            raise LookupError("Plan not found")
        if record["workspace_id"] != workspace_id:
            raise PermissionError("Plan belongs to another workspace")
        if record["status"] != "pending_approval":
            return self.public_plan(record)
        results = [
            self._apply(operation, workspace_id)
            for operation in _decode_json(record.get("operations_json"), [])
        ]
        with connect() as conn:
            conn.execute(
                "UPDATE org_action_plans SET status='approved',results_json=?,resolved_at=?,"
                "resolved_by=? WHERE id=?",
                (json.dumps(results), utcnow(), approver, plan_id),
            )
        return self.get_plan(plan_id)

    def reject_plan(self, plan_id: str, workspace_id: str, approver: str) -> dict:
        record = row("SELECT * FROM org_action_plans WHERE id=?", (plan_id,))
        if not record:
            raise LookupError("Plan not found")
        if record["workspace_id"] != workspace_id:
            raise PermissionError("Plan belongs to another workspace")
        if record["status"] == "pending_approval":
            with connect() as conn:
                conn.execute(
                    "UPDATE org_action_plans SET status='denied',resolved_at=?,resolved_by=? "
                    "WHERE id=?",
                    (utcnow(), approver, plan_id),
                )
        return self.get_plan(plan_id)

    def _apply(self, operation: dict, workspace_id: str) -> dict:
        now = utcnow()
        if operation["op"] == "update_task":
            task = row("SELECT * FROM org_tasks WHERE id=?", (operation["task_id"],))
            if not task:
                return {"op": operation["op"], "ok": False, "error": "task not found"}
            sources = list(
                dict.fromkeys(
                    [
                        *_decode_json(task.get("source_memory_ids_json"), []),
                        *operation.get("source_memory_ids", []),
                    ]
                )
            )
            with connect() as conn:
                conn.execute(
                    "UPDATE org_tasks SET status=?,owner=?,priority=?,description=?,"
                    "source_memory_ids_json=?,updated_at=? WHERE id=?",
                    (
                        operation.get("status") or task["status"],
                        operation.get("owner") or task["owner"],
                        operation.get("priority") or task["priority"],
                        operation.get("description") or task["description"],
                        json.dumps(sources),
                        now,
                        task["id"],
                    ),
                )
            return {
                "op": operation["op"],
                "ok": True,
                "task_id": task["id"],
                "status": operation.get("status") or task["status"],
            }
        if operation["op"] == "create_task":
            task_id = new_id("task")
            with connect() as conn:
                conn.execute(
                    "INSERT INTO org_tasks (id,workspace_id,project_id,title,description,status,"
                    "owner,priority,kind,source_memory_ids_json,depends_on_json,external_key,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,'open',?,?,'task',?,?,'',?,?)",
                    (
                        task_id,
                        workspace_id,
                        operation["space_id"],
                        operation["title"],
                        operation.get("description", ""),
                        operation.get("owner", ""),
                        operation.get("priority", "normal"),
                        json.dumps(operation.get("source_memory_ids", [])),
                        json.dumps(operation.get("depends_on", [])),
                        now,
                        now,
                    ),
                )
            return {"op": operation["op"], "ok": True, "task_id": task_id}
        if operation["op"] == "add_memory":
            unit = self.memory.create(
                operation["space_id"],
                operation["type"],
                operation["title"],
                operation["content"],
                operation.get("source_memory_ids", []),
                0.9,
                {},
            )
            return {"op": operation["op"], "ok": True, "memory_id": unit.get("id", "")}
        return {"op": operation.get("op", ""), "ok": False, "error": "unsupported operation"}

    # ------------------------------------------------------------- internals

    def _units(self, space_id: str, kind: str = "") -> list[dict]:
        return self.memory.list(space_id, latest=True, kind=kind, limit=2000)

    def _tasks(self, space_ids: Iterable[str]) -> list[dict]:
        ids = list(space_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return rows(
            f"SELECT * FROM org_tasks WHERE project_id IN ({placeholders}) ORDER BY created_at",
            tuple(ids),
        )

    def _task_counts(self, space_ids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self._tasks(space_ids):
            if task.get("status") in OPEN_TASK_STATUSES:
                counts[task["project_id"]] = counts.get(task["project_id"], 0) + 1
        return counts

    def _memory_counts(self, space_ids: list[str]) -> dict[str, dict[str, int]]:
        if not space_ids:
            return {}
        placeholders = ",".join("?" for _ in space_ids)
        counts: dict[str, dict[str, int]] = {}
        for record in rows(
            f"SELECT project_id,type,COUNT(*) n FROM memory_units "
            f"WHERE project_id IN ({placeholders}) AND is_latest=1 GROUP BY project_id,type",
            tuple(space_ids),
        ):
            bucket = counts.setdefault(record["project_id"], {"__total__": 0})
            bucket[record["type"]] = record["n"]
            bucket["__total__"] += record["n"]
        return counts

    def _space_name(self, space_id: str) -> str:
        if space_id not in self._space_names:
            record = row("SELECT name FROM projects WHERE id=?", (space_id,))
            self._space_names[space_id] = (record or {}).get("name", "")
        return self._space_names[space_id]

    @staticmethod
    def _score(unit: dict, terms: set[str]) -> float:
        if not terms:
            return 0.0
        subject = _terms(unit.get("subject", ""))
        content = _terms(unit.get("content", ""))
        return len(terms & subject) * 3.0 + len(terms & content) * 1.0
