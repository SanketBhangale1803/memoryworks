"""A real agent loop over the page's WebMCP tool surface.

External browser agents reach MemoryWorks through `document.modelContext`. This
module runs the same experience server-side with the same tool names, the same
authorization, and the same approval boundary, so the demo can show — step by
step, with live evidence — exactly what an agent gets when it discovers
MemoryWorks on the web.

The loop is deliberately tool-first: every claim in the final answer must trace
to a tool observation, and the only write tool is a proposal that persists
nothing until a person approves it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.llm import llm_budget
from app.llm.providers import configured_model, generate_grounded_json

MAX_STEPS = 8
MAX_OBSERVATION_CHARS = 2200
RATE_LIMIT_ATTEMPTS = 2
RATE_LIMIT_BACKOFF_SECONDS = 1.5

LlmCall = Callable[[str], dict[str, Any] | None]


def agent_tool_catalog() -> list[dict[str, Any]]:
    """The tool surface an agent discovers on the page, in wire format."""
    return [
        {
            "name": "search_orgmemory",
            "description": (
                "Search current company memory by query and optional kind "
                "(incident, decision, fact, dependency, procedure...). Start here."
            ),
            "arguments": ["query", "project_id (optional)", "type (optional)", "limit (optional)"],
        },
        {
            "name": "get_orgmemory_incidents",
            "description": "Previous incident memories, optionally filtered by service name.",
            "arguments": ["service (optional)", "project_id (optional)"],
        },
        {
            "name": "get_orgmemory_service_context",
            "description": (
                "Assembled context for a service: facts, owners, dependencies, "
                "decisions, procedures, and incident history."
            ),
            "arguments": ["service", "project_id (optional)"],
        },
        {
            "name": "get_orgmemory_dependencies",
            "description": "Remembered dependencies for a service, for blast-radius reasoning.",
            "arguments": ["service", "project_id (optional)"],
        },
        {
            "name": "get_orgmemory_decisions",
            "description": "Remembered architecture and operational decisions.",
            "arguments": ["project_id (optional)", "limit (optional)"],
        },
        {
            "name": "get_orgmemory_runbook",
            "description": "The runbook the organization validated for a service and issue.",
            "arguments": ["service", "issue (optional)"],
        },
        {
            "name": "get_orgmemory_related_memories",
            "description": (
                "Follow the memory graph around one memory: updates, contradictions, "
                "and same-subject history."
            ),
            "arguments": ["memory_id"],
        },
        {
            "name": "propose_orgmemory_incident",
            "description": (
                "Propose recording a VERIFIED incident into company memory. This "
                "persists nothing: it queues a proposal a person must approve."
            ),
            "arguments": ["subject", "content", "service (optional)", "reason (optional)"],
        },
    ]


def _observation(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > MAX_OBSERVATION_CHARS:
        return text[:MAX_OBSERVATION_CHARS] + " …(truncated)"
    return text


class WebMCPAgentRunner:
    """Executes one agent session: question → tools → grounded answer."""

    def __init__(
        self,
        llm: LlmCall | None = None,
        catalog: Callable[[], list[dict[str, Any]]] | None = None,
        guided: Callable[[str, list[dict], str], LlmCall] | None = None,
    ):
        # Injectable for tests; production uses the configured model provider.
        self._llm = llm
        # The loop is the same whichever surface it drives; only the tools an
        # agent can see differ. Passing the catalog in keeps one loop rather
        # than a second copy that drifts.
        self._catalog = catalog or agent_tool_catalog
        # The fallback has to speak the same catalog. A guided policy naming
        # tools this surface does not expose produces "Unknown tool" on screen,
        # which is worse than no fallback at all.
        self._guided = guided or self._guided_decider

    def run(self, **kwargs: Any) -> dict:
        """Run one Agent session (see :meth:`_run`) under its own model budget:
        at most one call per step plus a few retries, and a wall-clock limit."""
        with llm_budget(settings.org_agent_llm_budget_seconds, settings.org_agent_max_llm_calls):
            return self._run(**kwargs)

    def _run(
        self,
        *,
        principal: dict,
        question: str,
        project_id: str = "",
        model: str | None = None,
        exec_tool: Callable[[dict, str, dict], tuple[str, Any]] | None = None,
        list_spaces: Callable[[dict], list[dict]] | None = None,
        on_step: Callable[[dict], None] | None = None,
        max_steps: int = MAX_STEPS,
    ) -> dict:
        # Imported here so the module can be loaded without the API layer.
        from app.api.routes import (
            _authorize_project_for_principal,
            _memory_related_core,
            _memory_search_core,
            _propose_memory_core,
            _service_context_core,
            _visible_runbooks_core,
        )

        exec_tool = exec_tool or self._default_executors(
            _memory_search_core,
            _service_context_core,
            _visible_runbooks_core,
            _memory_related_core,
            _propose_memory_core,
            _authorize_project_for_principal,
        )
        list_spaces = list_spaces or self._default_list_spaces()

        spaces = list_spaces(principal)
        default_project = project_id or (spaces[0]["project_id"] if spaces else "")
        steps: list[dict] = []
        transcript = ""
        proposal = None
        llm = self._llm or self._provider_call(model)
        mode = "model" if (self._llm or configured_model(model)) else "guided"

        for step_index in range(1, max_steps + 1):
            prompt = self._prompt(question, spaces, transcript, max_steps)
            started = time.monotonic()
            try:
                decision = llm(prompt)
            except RuntimeError:
                # The show must go on: if the model is unreachable mid-session,
                # fall back to the guided policy for the rest of the session.
                # Tools, observations, and the proposal boundary stay completely
                # real; only step selection becomes scripted, and the run is
                # labeled as guided.
                if mode == "model" and self._llm is None:
                    mode = "guided"
                    llm = self._guided(question, spaces, default_project)
                    decision = llm(prompt)
                else:
                    raise
            if not decision:
                raise RuntimeError("The configured model did not return a usable decision.")
            thought = str(decision.get("thought") or "")
            if decision.get("answer"):
                answer = str(decision["answer"])
                memory_ids = decision.get("memory_ids") or []
                propose_spec = decision.get("propose") or None
                if isinstance(propose_spec, dict) and default_project:
                    try:
                        _, proposal_payload = exec_tool(
                            principal,
                            "propose_orgmemory_incident",
                            {
                                "project_id": default_project,
                                **{
                                    key: str(value)
                                    for key, value in propose_spec.items()
                                    if value is not None
                                },
                            },
                        )
                        proposal = proposal_payload
                    except Exception:
                        proposal = None
                return {
                    "answer": answer,
                    "memory_ids": [str(item) for item in memory_ids],
                    "steps": steps,
                    "proposal": proposal,
                    "thoughts": [step.get("thought") for step in steps] + [thought],
                    "mode": mode,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            tool_name = str(decision.get("tool") or "")
            arguments = decision.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            if not tool_name:
                transcript += "\nSYSTEM: Respond with a tool call or a final answer."
                continue
            try:
                summary, structured = exec_tool(principal, tool_name, arguments)
            except Exception as exc:
                summary, structured = f"Tool error: {exc}", {}
            observation = _observation(structured)
            transcript += (
                f"\nTOOL CALL {step_index}: {tool_name} {json.dumps(arguments)}\n"
                # The summary is the tool's own one-line account of what it
                # found. Observations get truncated mid-JSON; this does not, so
                # both the model and the guided fallback can rely on it.
                f"SUMMARY {step_index}: {summary}\n"
                f"OBSERVATION {step_index}: {observation}"
            )
            steps.append(
                {
                    "tool": tool_name,
                    "arguments": arguments,
                    "summary": summary,
                    "thought": thought,
                    "observation": observation[:600],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            if on_step:
                on_step(steps[-1])
            if tool_name == "propose_orgmemory_changes" and "waiting for a person" in summary:
                # The write path's contract is to stop at a person. Once a plan
                # is filed, the session's work is done whether or not the model
                # spends its next turn saying so — answer now, grounded in the
                # observations, instead of risking the step budget.
                findings = [
                    step["summary"]
                    for step in steps[:-1]
                    if step["tool"] != "propose_orgmemory_changes" and step.get("summary")
                ]
                basis = " Basis: " + " ".join(findings[-2:]).rstrip(".") + "." if findings else ""
                memory_ids = list(
                    dict.fromkeys(
                        match.group(0) for match in re.finditer(r"mem_[a-z0-9]+", transcript)
                    )
                )[:6]
                return {
                    "answer": (
                        f"{summary}{basis} The proposed changes are shown below — approving "
                        "or declining them is a person's call, not the agent's."
                    ),
                    "memory_ids": memory_ids,
                    "steps": steps,
                    "proposal": structured if isinstance(structured, dict) else None,
                    "thoughts": [step.get("thought") for step in steps] + [thought],
                    "mode": mode,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
        # Budget exhausted without an answer. If the run did real work, report
        # what it actually found rather than claiming nothing was grounded.
        summaries = [
            step["summary"]
            for step in steps
            if step.get("summary") and not str(step["summary"]).startswith("Tool error")
        ]
        proposal_errors = [
            str(step["summary"]).removeprefix("Tool error: ")
            for step in steps
            if step["tool"] == "propose_orgmemory_changes"
            and str(step.get("summary") or "").startswith("Tool error")
        ]
        if proposal_errors:
            # A successful proposal ends the run above, so reaching here means
            # every attempt was refused. Saying the changes await approval would
            # send someone looking for a plan that does not exist.
            answer = (
                "I could not file the proposed changes, so nothing is waiting for "
                f"approval and nothing was changed. The last attempt failed: {proposal_errors[-1]}"
                + (" What I found before that: " + " ".join(summaries[-3:]) if summaries else "")
            )
        else:
            answer = (
                "I could not ground a complete answer within the tool budget. "
                + ("What the tools did find: " + " ".join(summaries[-2:]))
                if summaries
                else "I could not ground an answer within the tool budget. The observations above show exactly what was searched."
            )
        return {
            "answer": answer,
            "memory_ids": [],
            "steps": steps,
            "proposal": None,
            "thoughts": [step.get("thought") for step in steps],
            "mode": mode,
            "elapsed_ms": 0,
        }

    def _guided_decider(
        self, question: str, spaces: list[dict], default_project: str = ""
    ) -> LlmCall:
        """Scripted step selection for when no model is reachable.

        Every fact in the composed answer is extracted from real tool
        observations already in the transcript; nothing is canned except the
        order of the tool calls.
        """

        def decide(prompt: str) -> dict[str, Any]:
            called = set(re.findall(r"TOOL CALL \d+: (\w+)", prompt))
            # Observations are truncated mid-JSON sometimes, so value charset is
            # bounded and obviously-corrupt matches are discarded.
            services = [
                service
                for service in re.findall(r'"service":\s*"([^"\n{}]{1,60})"', prompt)
                if service and "…" not in service and "truncated" not in service
            ]
            service = services[0] if services else ""
            memory_ids = list(dict.fromkeys(re.findall(r'"id":\s*"(mem_[a-z0-9]+)"', prompt)))
            steps_taken = len(re.findall(r"TOOL CALL \d+:", prompt))

            if not steps_taken:
                return {
                    "thought": "Guided: search company memory for the question terms.",
                    "tool": "search_orgmemory",
                    "arguments": {
                        "query": question[:120],
                        **({"project_id": default_project} if default_project else {}),
                        "limit": 8,
                    },
                }
            if service and "get_orgmemory_incidents" not in called:
                return {
                    "thought": f"Guided: pull previous incidents for {service}.",
                    "tool": "get_orgmemory_incidents",
                    "arguments": {
                        "service": service,
                        **({"project_id": default_project} if default_project else {}),
                    },
                }
            if service and "get_orgmemory_service_context" not in called:
                return {
                    "thought": f"Guided: assemble the full context for {service}.",
                    "tool": "get_orgmemory_service_context",
                    "arguments": {"service": service},
                }
            if "get_orgmemory_decisions" not in called:
                return {
                    "thought": "Guided: check what was already decided.",
                    "tool": "get_orgmemory_decisions",
                    "arguments": {
                        **({"project_id": default_project} if default_project else {}),
                        "limit": 5,
                    },
                }
            incident_subjects = list(
                dict.fromkeys(re.findall(r'"type":\s*"incident",\s*"subject":\s*"([^"]+)"', prompt))
            )
            decision_subjects = list(
                dict.fromkeys(re.findall(r'"type":\s*"decision",\s*"subject":\s*"([^"]+)"', prompt))
            )
            incident_count = len(incident_subjects)
            answer_parts = []
            if service:
                answer_parts.append(f"Company memory recognizes the service {service}.")
            if incident_count:
                answer_parts.append(
                    f"{incident_count} previous incident(s) are on record: "
                    + "; ".join(incident_subjects)
                    + "."
                )
            if decision_subjects:
                answer_parts.append(
                    f"Relevant decisions on record: {'; '.join(decision_subjects[:2])}."
                )
            answer_parts.append(
                "The service context above lists the remembered facts, owners, dependencies, decisions, and first-response procedures — start from the procedure on record before improvising."
            )
            answer = " ".join(answer_parts)

            propose = None
            if incident_count and re.search(
                r"\b(again|failing|outage|alert|broken|down|recurrence)\b", question, re.I
            ):
                propose = {
                    "kind": "incident",
                    "subject": f"{service or 'service'} incident recurrence: matches remembered incidents",
                    "content": (
                        f"The reported question (“{question[:200]}”) matches {incident_count} "
                        "verified previous incident(s) already on record in company memory."
                    ),
                    "service": service,
                    "reason": "Guided run: grounded only in incident memories observed via tools.",
                }
            return {
                "thought": "Guided: observations collected; compose the grounded answer.",
                "answer": answer,
                "memory_ids": memory_ids[:6],
                "propose": propose,
            }

        return decide

    def _prompt(self, question: str, spaces: list[dict], transcript: str, max_steps: int) -> str:
        tools = "\n".join(
            f"- {tool['name']}({', '.join(tool['arguments'])}): {tool['description']}"
            for tool in self._catalog()
        )
        return f"""You are a browser AI agent on a MemoryWorks page. The page exposes WebMCP tools for company memory.

QUESTION: {question}

AUTHORIZED SPACES: {json.dumps(spaces)}

TOOLS:
{tools}

CONVERSATION SO FAR:{transcript or " (nothing yet)"}

Decide the single next step. Reply with ONLY one JSON object:
- To call a tool: {{"thought": "one sentence", "tool": "<name>", "arguments": {{...}}}}
- To finish: {{"thought": "one sentence", "answer": "grounded answer citing what you found", "memory_ids": ["mem_..."], "propose": null}}
- Finish with a proposal ONLY if your observations VERIFIED a finding worth remembering: {{"answer": "...", "memory_ids": [...], "propose": {{"kind": "incident", "subject": "...", "content": "what happened, verified cause, resolution", "service": "...", "reason": "why this is verified"}}}}

Rules:
- Ground every claim in the observations. Name the memory_ids you used.
- You have at most {max_steps} tool calls total. Start with the most specific tool.
- To reconcile a conflict found by find_orgmemory_conflicts, call propose_orgmemory_changes with that conflict's id as conflict_id. Never retype a resolution's operations yourself.
- If you must pass explicit operations, each op is exactly one of create_task, update_task, add_memory. Nothing else is valid. Every create_task and add_memory needs space_id: the project_id of the space it belongs to, from AUTHORIZED SPACES.
- If a tool call fails, read its error and fix the arguments; do not repeat the same call unchanged.
- The moment propose_orgmemory_changes succeeds, give your final answer: say the changes are proposed and waiting for a person to approve. Do not re-verify or call more tools.
- Never invent spaces, memory ids, or facts. If evidence is missing, say so.
- Proposing saves nothing by itself; a person approves it on the page."""

    def _provider_call(self, model: str | None) -> LlmCall:
        def call(prompt: str) -> dict[str, Any] | None:
            provider = configured_model(model)
            if not provider:
                raise RuntimeError(
                    "No model key is configured. Add one in MemoryWorks settings to run a live agent."
                )
            # Free-tier providers rate-limit aggressively. A transient 429 is
            # worth one quick retry; an exhausted quota answers 429 instantly
            # and forever, and a long backoff there just leaves the page silent
            # for half a minute before the fallback it was always going to use.
            last_error: Exception | None = None
            for attempt in range(RATE_LIMIT_ATTEMPTS):
                if attempt:
                    time.sleep(RATE_LIMIT_BACKOFF_SECONDS * attempt)
                try:
                    # generate_grounded_json hands back (decision, provider);
                    # the loop only wants the decision.
                    result = generate_grounded_json(prompt, provider.id)
                    return result[0] if isinstance(result, tuple) else result
                except Exception as exc:
                    last_error = exc
                    if "429" not in str(exc) and "rate" not in str(exc).lower():
                        raise
            raise RuntimeError(f"The model provider kept rate-limiting the agent: {last_error}")

        return call

    def _default_list_spaces(self) -> Callable[[dict], list[dict]]:
        def list_spaces(principal: dict) -> list[dict]:
            from app.api.routes import _authorized_space_ids

            names = {
                item["id"]: item for item in _query_all("SELECT id,name,repository FROM projects")
            }
            return [
                {
                    "project_id": project_id,
                    "name": (names.get(project_id) or {}).get("name", ""),
                    "repository": (names.get(project_id) or {}).get("repository", ""),
                }
                for project_id in _authorized_space_ids(principal)
            ]

        return list_spaces

    def _default_executors(
        self,
        memory_search_core,
        service_context_core,
        visible_runbooks_core,
        memory_related_core,
        propose_memory_core,
        authorize_project,
    ) -> Callable[[dict, str, dict], tuple[str, Any]]:
        def summarize_search(payload: dict) -> tuple[str, Any]:
            results = payload.get("results", [])
            heads = "; ".join(f"[{item['type']}] {item['subject']}" for item in results[:3])
            return (
                f"{len(results)} memor{'y' if len(results) == 1 else 'ies'} matched"
                + (f": {heads}" if heads else "."),
                payload,
            )

        def exec_tool(principal: dict, tool_name: str, arguments: dict) -> tuple[str, Any]:
            if tool_name == "search_orgmemory":
                payload = memory_search_core(
                    principal,
                    str(arguments.get("query") or ""),
                    project_id=str(arguments.get("project_id") or ""),
                    type=str(arguments.get("type") or ""),
                    limit=int(arguments.get("limit") or 10),
                )
                return summarize_search(payload)
            if tool_name == "get_orgmemory_incidents":
                payload = memory_search_core(
                    principal,
                    str(arguments.get("service") or ""),
                    project_id=str(arguments.get("project_id") or ""),
                    type="incident",
                    limit=10,
                )
                return summarize_search(payload)
            if tool_name == "get_orgmemory_decisions":
                payload = memory_search_core(
                    principal,
                    "",
                    project_id=str(arguments.get("project_id") or ""),
                    type="decision",
                    limit=int(arguments.get("limit") or 10),
                )
                return summarize_search(payload)
            if tool_name == "get_orgmemory_dependencies":
                payload = memory_search_core(
                    principal,
                    str(arguments.get("service") or ""),
                    project_id=str(arguments.get("project_id") or ""),
                    type="dependency",
                    limit=10,
                )
                return summarize_search(payload)
            if tool_name == "get_orgmemory_service_context":
                entries = service_context_core(principal, str(arguments.get("service") or ""))
                facts = sum(len(entry["profile"].get("current_facts") or []) for entry in entries)
                decisions = sum(len(entry["profile"].get("decisions") or []) for entry in entries)
                incidents = sum(len(entry["profile"].get("incidents") or []) for entry in entries)
                return (
                    f"{len(entries)} space(s) know this service: {facts} facts, {decisions} decisions, {incidents} incidents.",
                    {"spaces": entries},
                )
            if tool_name == "get_orgmemory_runbook":
                records = visible_runbooks_core(
                    principal,
                    service=str(arguments.get("service") or ""),
                    issue=str(arguments.get("issue") or ""),
                )
                return (
                    f"{len(records)} runbook(s) found."
                    + (
                        f" First: {records[0].get('title') or records[0].get('key')}"
                        if records
                        else ""
                    ),
                    {"runbooks": records},
                )
            if tool_name == "get_orgmemory_related_memories":
                payload = memory_related_core(principal, str(arguments.get("memory_id") or ""))
                related = payload.get("related", [])
                return (
                    f"{len(related)} related memor{'y' if len(related) == 1 else 'ies'}.",
                    payload,
                )
            if tool_name == "propose_orgmemory_incident":
                project_id = str(arguments.get("project_id") or "")
                authorize_project(principal, project_id, write=True)
                proposal = propose_memory_core(
                    principal,
                    project_id,
                    "incident",
                    str(arguments.get("subject") or ""),
                    str(arguments.get("content") or ""),
                    service=str(arguments.get("service") or ""),
                    reason=str(arguments.get("reason") or ""),
                )
                return (
                    f"Proposal {proposal.get('id')} queued as {proposal.get('status')}. "
                    "Nothing is saved until a person approves it.",
                    proposal,
                )
            raise ValueError(f"Unknown tool: {tool_name}")

        return exec_tool


def _query_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    from app.core.database import rows

    return rows(sql, params)


class AgentSessionStore:
    """In-memory run registry so the console can watch steps land live."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, question: str, model_id: str, workspace_id: str = "") -> str:
        run_id = f"agtsess_{int(time.time() * 1000):x}"
        with self._lock:
            self._runs[run_id] = {
                "id": run_id,
                "workspace_id": workspace_id,
                "question": question,
                "model": model_id,
                "status": "running",
                "steps": [],
                "answer": "",
                "memory_ids": [],
                "proposal": None,
                "error": "",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        return run_id

    def get(self, run_id: str, workspace_id: str = "") -> dict[str, Any] | None:
        with self._lock:
            record = self._runs.get(run_id)
            if not record:
                return None
            if workspace_id and record.get("workspace_id") != workspace_id:
                # A session trace is workspace-private, like every other
                # workspace surface in the product.
                return None
            return dict(record)

    def update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record:
                record.update(fields)

    def append_step(self, run_id: str, step: dict) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                record["steps"] = [*record["steps"], step]
