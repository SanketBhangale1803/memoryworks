"""The organizational-operations tool surface, as an agent sees it.

The console's four suggested questions and anything a person types go through
the same path: a model reads this catalog, picks a tool, sees the observation,
and picks the next one. Nothing here decides in advance which tools a question
should use — that is the difference between a demo and a recording.

Reads execute immediately. The one write tool files a plan and stops, which is
why an agent can be given the whole surface without being given authority.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.connectors.status import (
    PROVIDER_NAMES,
    is_connection_question,
    mentioned_providers,
    status_answer,
)

# Tool descriptions are written for the model, not for a docs page: each says
# what the tool answers and when to reach for it, because a vague description is
# what makes an agent call six tools to answer a one-tool question.
ORG_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_orgmemory_project_context",
        "description": (
            "Everything current across the authorized spaces at once: decisions, open "
            "work, unresolved questions, recent changes, blockers, and the next best "
            "action. Best first call when someone needs to be caught up."
        ),
        "arguments": [],
    },
    {
        "name": "search_orgmemory_records",
        "description": (
            "Search company memory by free text and optional type (decision, incident, "
            "policy, dependency, ownership, fact, open_question). Use for a specific topic."
        ),
        "arguments": ["query", "memory_type (optional)", "limit (optional)"],
    },
    {
        "name": "get_orgmemory_readiness",
        "description": (
            "Compute launch readiness from the dependency graph: every checklist item "
            "resolved to done, ready, open, or blocked. Use for 'are we ready' questions."
        ),
        "arguments": [],
    },
    {
        "name": "find_orgmemory_blockers",
        "description": (
            "Only the root causes of a stall — unfinished work that other work waits on "
            "and whose own prerequisites are already met. Use for 'what is blocking'."
        ),
        "arguments": [],
    },
    {
        "name": "find_orgmemory_conflicts",
        "description": (
            "Work items whose tracked state disagrees with a newer record the item "
            "itself cites — a review still open after a meeting approved it."
        ),
        "arguments": ["topic (optional)"],
    },
    {
        "name": "get_orgmemory_reasoning_chain",
        "description": (
            "The ordered chain of records that produced a decision, walked through "
            "recorded relationships. Use for 'why' questions."
        ),
        "arguments": ["topic"],
    },
    {
        "name": "get_orgmemory_dependency_graph",
        "description": "Every work item and the REQUIRED_FOR edges between them.",
        "arguments": [],
    },
    {
        "name": "get_orgmemory_tasks",
        "description": "Open work, optionally filtered by owner or priority.",
        "arguments": ["assignee (optional)", "priority (optional)", "status (optional)"],
    },
    {
        "name": "get_orgmemory_decisions",
        "description": "Decisions on record, newest first.",
        "arguments": ["status (optional)"],
    },
    {
        "name": "get_orgmemory_recent_changes",
        "description": "What was created or revised recently, newest first.",
        "arguments": ["since (optional ISO timestamp)"],
    },
    {
        "name": "get_orgmemory_people",
        "description": "Who is here, what memory says they own, and their open work.",
        "arguments": ["query (optional)"],
    },
    {
        "name": "get_orgmemory_owner",
        "description": "The recorded owner of one task or memory id, with its evidence.",
        "arguments": ["object_id"],
    },
    {
        "name": "get_orgmemory_provenance",
        "description": "The sources and relationships behind one memory id.",
        "arguments": ["memory_id"],
    },
    {
        "name": "find_orgmemory_stale",
        "description": "Records older than a threshold that nothing newer supersedes.",
        "arguments": ["topic (optional)", "max_age_days (optional)"],
    },
    {
        "name": "get_orgmemory_connections",
        "description": (
            "LIVE state of connected sources (GitHub, Google Drive, Slack, Notion, Teams): "
            "which are connected and when they expire, recent connection attempts and "
            "whether they came back, how the last imports went, and a diagnosis. Use for "
            "any question about a connection, sign-in, sync or import failing — company "
            "memory cannot answer those."
        ),
        "arguments": ["provider (optional: github, google_drive, slack, notion, teams)"],
    },
    {
        "name": "propose_orgmemory_changes",
        "description": (
            "Submit changes for human approval. Applies NOTHING. To reconcile a "
            "conflict, pass its conflict_id — the recorded resolution is copied "
            "by reference, never retyped. Otherwise pass explicit operations, each "
            "exactly one of: create_task {space_id, title, description, owner, "
            "priority}, update_task {task_id, status, owner, priority, reason}, "
            "add_memory {space_id, type, title, content}. space_id is the project_id "
            "of the space the change belongs to, from AUTHORIZED SPACES. Never "
            "update_task_status or close_memory."
        ),
        "arguments": ["summary", "operations (or conflict_id)"],
    },
]


def org_tool_catalog() -> list[dict[str, Any]]:
    return ORG_AGENT_TOOLS


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


_SPACED_OPS = {"create_task", "add_memory"}


def _place_operations(operations: list, spaces: list[dict], default_space: str) -> list[dict]:
    """Give every new task or memory a space the caller may write to.

    Models routinely leave space_id out, or write the space's name instead of its
    id. Neither is ambiguous when only one space fits, so fill or translate it;
    when it is ambiguous, refuse with the valid choices, which is what lets the
    next attempt succeed instead of repeating the same mistake.
    """
    by_id = {str(space["id"]): space for space in spaces}
    by_name = {str(space.get("name") or "").casefold(): space for space in spaces}
    by_name.update(
        {
            str(space.get("repository") or "").casefold().rstrip("/"): space
            for space in spaces
            if space.get("repository")
        }
    )
    fallback = default_space if default_space in by_id else ""
    if not fallback and len(by_id) == 1:
        fallback = next(iter(by_id))
    choices = "; ".join(f"{space['id']} ({space.get('name') or 'unnamed'})" for space in spaces)

    placed: list[dict] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("each operation must be an object")
        operation = dict(operation)
        if str(operation.get("op") or "") in _SPACED_OPS:
            given = _text(operation.get("space_id"))
            if given and given not in by_id:
                needle = given.casefold().rstrip("/")
                match = by_name.get(needle)
                if not match:
                    # "SAP-AI-PRs" for "https://github.com/acme/SAP-AI-PRs" is fine;
                    # a fragment that fits two spaces is a guess, so it is refused.
                    partial = {
                        str(space["id"]): space
                        for name, space in by_name.items()
                        if name and (name.endswith("/" + needle) or needle in name)
                    }
                    match = next(iter(partial.values())) if len(partial) == 1 else None
                if not match:
                    raise ValueError(
                        f"{operation['op']} names space {given!r}, which is not one you can "
                        f"change. Use one of these project_ids: {choices}"
                    )
                given = str(match["id"])
            given = given or fallback
            if not given:
                raise ValueError(
                    f"{operation['op']} needs space_id: the project_id of the space it "
                    f"belongs to. Use one of: {choices}"
                )
            operation["space_id"] = given
        placed.append(operation)
    return placed


def build_org_executor(
    orgops: Any,
    resolve_spaces: Callable[[dict], list[str]],
    search_records: Callable[[dict, str, str, int], dict],
    propose: Callable[[dict, str, str, list[dict]], dict],
) -> Callable[[dict, str, dict], tuple[str, Any]]:
    """Map a tool name onto the same service calls the HTTP routes use.

    The executor never widens scope: `resolve_spaces` returns exactly the spaces
    the caller is already authorized for, so a tool the model invents arguments
    for still cannot read past that boundary.
    """

    def execute(principal: dict, name: str, arguments: dict) -> tuple[str, Any]:
        spaces = resolve_spaces(principal)
        count = lambda items: len(items)  # noqa: E731 - reads better inline below

        if name == "get_orgmemory_project_context":
            data = orgops.get_project_context(spaces)
            return (
                f"{data['memory_count']} memories across {count(data['spaces'])} spaces: "
                f"{count(data['decisions'])} decisions, {count(data['open_tasks'])} open items, "
                f"{count(data['blockers'])} blockers.",
                data,
            )

        if name == "search_orgmemory_records":
            query = _text(arguments.get("query"))
            if not query:
                raise ValueError("query is required")
            data = search_records(
                principal,
                query,
                _text(arguments.get("memory_type")),
                _int(arguments.get("limit"), 10),
            )
            return f"{data['count']} matching memories.", data

        if name == "get_orgmemory_readiness":
            data = orgops.get_readiness(spaces)
            return (
                f"{data['status']} — {data['blocker_count']} blocker(s), "
                f"{count(data['checklist'])} checklist items.",
                data,
            )

        if name == "find_orgmemory_blockers":
            data = orgops.find_blockers(spaces)
            if not data["count"]:
                return "Nothing is blocking.", data
            top = data["blockers"][0]
            return (
                f"{data['count']} blocker(s) — {top['task']['title']} ({top['severity']}), "
                f"holding {count(top['blocks'])} item(s).",
                data,
            )

        if name == "find_orgmemory_conflicts":
            data = orgops.find_conflicts(spaces, topic=_text(arguments.get("topic")))
            if not data["count"]:
                return "No contradictions found.", data
            top = data["conflicts"][0]
            return (
                f"{data['count']} conflict(s) — “{top['task']['title']}” is "
                f"{top['tracked_state']}, but {top['source']['space_name']} already settled it.",
                data,
            )

        if name == "get_orgmemory_reasoning_chain":
            topic = _text(arguments.get("topic"))
            if not topic:
                raise ValueError("topic is required")
            data = orgops.get_reasoning_chain(spaces, topic)
            return f"{count(data['steps'])} steps of recorded reasoning.", data

        if name == "get_orgmemory_dependency_graph":
            data = orgops.get_dependency_graph(spaces)
            return f"{data['node_count']} work items, {data['edge_count']} dependencies.", data

        if name == "get_orgmemory_tasks":
            data = orgops.get_open_tasks(
                spaces,
                assignee=_text(arguments.get("assignee")),
                priority=_text(arguments.get("priority")),
                status=_text(arguments.get("status")),
            )
            return f"{data['count']} open item(s).", data

        if name == "get_orgmemory_decisions":
            data = orgops.get_decisions(spaces, status=_text(arguments.get("status")))
            return f"{data['count']} decision(s) on record.", data

        if name == "get_orgmemory_recent_changes":
            since = (
                _text(arguments.get("since")) or (datetime.now(UTC) - timedelta(days=7)).isoformat()
            )
            data = orgops.get_recent_changes(spaces, since=since)
            return f"{data['count']} change(s) on record.", data

        if name == "get_orgmemory_people":
            data = orgops.get_people_context(
                principal.get("active_workspace_id", ""),
                spaces,
                query=_text(arguments.get("query")),
            )
            return f"{data['count']} person/people.", data

        if name == "get_orgmemory_owner":
            object_id = _text(arguments.get("object_id"))
            if not object_id:
                raise ValueError("object_id is required")
            data = orgops.get_owner(spaces, object_id)
            return (f"Owner: {data['owner']}." if data.get("owner") else "No recorded owner."), data

        if name == "get_orgmemory_provenance":
            memory_id = _text(arguments.get("memory_id"))
            if not memory_id:
                raise ValueError("memory_id is required")
            data = orgops.get_provenance(memory_id)
            return (
                f"{count(data['sources'])} source(s), {count(data['relations'])} relationship(s).",
                data,
            )

        if name == "find_orgmemory_stale":
            data = orgops.find_stale_information(
                spaces,
                topic=_text(arguments.get("topic")),
                max_age_days=_int(arguments.get("max_age_days"), 90),
            )
            return f"{data['count']} aging record(s).", data

        if name == "get_orgmemory_connections":
            workspace_id = str(principal.get("active_workspace_id") or "")
            if not workspace_id:
                raise ValueError("No active workspace for this caller")
            requested = _text(arguments.get("provider")).casefold().replace(" ", "_")
            providers = (
                [requested]
                if requested in PROVIDER_NAMES
                else mentioned_providers(requested)
                if requested
                else []
            )
            data = status_answer(workspace_id, providers)
            reports = data.pop("_reports")
            findings = [finding for report in reports for finding in report["findings"]]
            return (
                " ".join(findings[:3]) or "No source has been connected or attempted yet.",
                {"reports": reports, "summary": data["answer"]},
            )

        if name == "propose_orgmemory_changes":
            operations = arguments.get("operations")
            conflict_id = _text(arguments.get("conflict_id"))
            if conflict_id:
                # Reconciliation by reference: copy the system-computed
                # resolution verbatim rather than trusting a model to retype
                # it from a truncated observation. This is what keeps the
                # plan a person approves identical to the evidence found.
                conflict = orgops.get_conflict(spaces, conflict_id)
                operations = [conflict["resolution"]]
                if isinstance(operations[0], dict) and not operations[0].get("space_id"):
                    operations[0] = {**operations[0], "space_id": conflict["task"]["space_id"]}
            if isinstance(operations, str):
                # Models sometimes hand back JSON as a string. Accepting it is
                # kinder than failing a step the agent got substantially right.
                try:
                    operations = json.loads(operations)
                except ValueError:
                    operations = []
            if isinstance(operations, dict):
                operations = [operations]
            if not isinstance(operations, list) or not operations:
                raise ValueError(
                    "operations must be a non-empty list, or pass conflict_id to reconcile"
                )
            operations = _place_operations(
                operations, orgops.list_spaces(spaces), _text(arguments.get("space_id"))
            )
            plan = propose(
                principal,
                _text(arguments.get("summary")) or "Agent-proposed changes",
                _text(arguments.get("space_id")),
                operations,
            )
            return (
                f"{count(plan['operations'])} change(s) proposed and waiting for a person. "
                "Nothing applied.",
                plan,
            )

        raise ValueError(f"Unknown tool {name}")

    return execute


# ---------------------------------------------------------------- fallback

_READINESS_RE = re.compile(r"\b(ready|launch|ship|go[- ]live|status)\b", re.I)
_WHY_RE = re.compile(r"\b(why|reason|because|rationale|how come)\b", re.I)
_CATCHUP_RE = re.compile(r"\b(catch me up|caught up|joined|onboard|overview|what matters)\b", re.I)
_RECONCILE_RE = re.compile(r"\b(reconcile|fix it|resolve|handle it|unblock)\b", re.I)
_BLOCKER_RE = re.compile(r"\b(block|blocker|blocking|stuck|stalled|holding)\b", re.I)

# Which tools answer which shape of question, when a model cannot be reached.
_SEQUENCES: dict[str, list[str]] = {
    "readiness": [
        "get_orgmemory_readiness",
        "find_orgmemory_blockers",
        "find_orgmemory_conflicts",
    ],
    "why": ["get_orgmemory_reasoning_chain", "find_orgmemory_blockers"],
    "catchup": [
        "get_orgmemory_project_context",
        "get_orgmemory_recent_changes",
        "get_orgmemory_people",
    ],
    "reconcile": ["find_orgmemory_conflicts", "propose_orgmemory_changes"],
    "blockers": ["find_orgmemory_blockers", "get_orgmemory_readiness"],
    "search": ["search_orgmemory_records", "get_orgmemory_project_context"],
    "connections": ["get_orgmemory_connections"],
}


def _classify(question: str) -> str:
    # Checked first: "the Drive connection is failing, fix it" is about the live
    # connection, which no memory tool can see.
    if is_connection_question(question):
        return "connections"
    if _RECONCILE_RE.search(question):
        return "reconcile"
    if _CATCHUP_RE.search(question):
        return "catchup"
    if _WHY_RE.search(question):
        return "why"
    if _READINESS_RE.search(question):
        return "readiness"
    if _BLOCKER_RE.search(question):
        return "blockers"
    return "search"


def org_guided_decider(question: str, spaces: list[dict], default_project: str = "") -> Any:
    """Deterministic tool selection for when the model is unreachable.

    Free-tier providers rate-limit hard, and a demo that dies mid-question is
    worse than one that says how it chose. Only the order is scripted: every
    tool call, observation, and sentence in the answer below comes from the
    same live tools the model would have called.
    """
    intent = _classify(question)
    sequence = _SEQUENCES[intent]

    def decide(prompt: str) -> dict[str, Any]:
        called = set(re.findall(r"TOOL CALL \d+: (\w+)", prompt))
        for tool in sequence:
            if tool in called:
                continue
            arguments: dict[str, Any] = {}
            if tool == "get_orgmemory_reasoning_chain":
                arguments = {"topic": question[:160]}
            elif tool == "search_orgmemory_records":
                arguments = {"query": question[:120], "limit": 8}
            elif tool == "propose_orgmemory_changes":
                match = re.search(r'"id": "(conflict_[a-z0-9_]+)"', prompt)
                if not match:
                    # Nothing to reconcile: the conflicts step already said so,
                    # so finalize from what the tools found instead of filing
                    # an empty plan.
                    summaries = re.findall(r"SUMMARY \d+: (.+)", prompt)
                    return {
                        "thought": "Guided: no conflict remains to reconcile.",
                        "answer": " ".join(part.strip() for part in summaries if part.strip())
                        or "Nothing to reconcile.",
                        "memory_ids": [],
                        "propose": None,
                    }
                arguments = {
                    "summary": "Reconcile the tracked state with the record that settled it",
                    "conflict_id": match.group(1),
                }
            return {
                "thought": f"No model reachable — guided {intent} sequence: {tool}.",
                "tool": tool,
                "arguments": arguments,
            }

        # Compose from the tools' own summaries: they are short, complete
        # sentences that survive observation truncation intact.
        summaries = re.findall(r"SUMMARY \d+: (.+)", prompt)
        memory_ids = list(dict.fromkeys(re.findall(r'"(?:id)":\s*"(mem_[a-z0-9]+)"', prompt)))
        parts = [line.strip() for line in summaries if line.strip()]
        if not parts:
            parts = ["The tools returned nothing for this question."]
        return {
            "thought": "Guided: observations collected; compose the grounded answer.",
            "answer": " ".join(parts),
            "memory_ids": memory_ids[:6],
            "propose": None,
        }

    return decide
