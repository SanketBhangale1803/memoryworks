"""Agent-facing organizational operations.

These are the endpoints behind the WebMCP tool surface. Reads run immediately and
never touch an LLM. Writes never apply directly: an agent can only propose a plan,
and a person approves it. Capability and authorization stay separate.
"""

from __future__ import annotations

import json
import queue
import time
from threading import Thread

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.database import row
from app.llm.providers import configured_model, generate_grounded_json
from app.orgops import OrgOpsService, WatchService
from app.orgops.agent import build_org_executor, org_guided_decider, org_tool_catalog
from app.orgops.seed import reset_launch_scenario, seed_launch_scenario
from app.webmcp_agent import AgentSessionStore, WebMCPAgentRunner

from .routes import (
    _authenticate,
    _authorize_project_for_principal,
    _authorize_workspace,
    _authorized_space_ids,
    _memory_search_core,
    audit,
    company_memory,
    ingestion,
    logger,
)

org_router = APIRouter(prefix="/api/org", tags=["organization"])
orgops = OrgOpsService(company_memory)
watches = WatchService(orgops)
org_sessions = AgentSessionStore()
# Same loop the browser-tool agent uses; only the catalog differs, so a question
# typed into the console is answered by a model choosing tools, not by a script.
org_agent = WebMCPAgentRunner(catalog=org_tool_catalog, guided=org_guided_decider)


def _spaces(principal: dict, requested: str = "") -> list[str]:
    """Narrow the caller's authorized spaces to the ones they asked for."""
    allowed = _authorized_space_ids(principal)
    if not requested:
        return allowed
    wanted = [item.strip() for item in requested.split(",") if item.strip()]
    unknown = [item for item in wanted if item not in allowed]
    if unknown:
        raise HTTPException(403, f"No access to space {unknown[0]}")
    return wanted


class PlanOperation(BaseModel):
    op: str
    task_id: str = ""
    space_id: str = ""
    title: str = ""
    description: str = ""
    content: str = ""
    type: str = ""
    status: str = ""
    owner: str = ""
    priority: str = ""
    reason: str = ""
    depends_on: list[str] = Field(default_factory=list)
    source_memory_ids: list[str] = Field(default_factory=list)


class PlanRequest(BaseModel):
    space_id: str = ""
    summary: str = ""
    operations: list[PlanOperation] = Field(default_factory=list)


class SeedRequest(BaseModel):
    reset: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    space_ids: list[str] = Field(default_factory=list)
    model: str | None = None


class FollowupsRequest(BaseModel):
    question: str = ""
    answer: str = ""
    summaries: list[str] = Field(default_factory=list)
    space_ids: list[str] = Field(default_factory=list)


class WatchRequest(BaseModel):
    name: str = "Organizational watch"
    space_ids: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    interval_seconds: int = 900


# ------------------------------------------------------------------- reads


@org_router.get("/spaces")
def list_spaces(authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    spaces = orgops.list_spaces(_spaces(principal))
    return {"count": len(spaces), "spaces": spaces}


@org_router.get("/spaces/{space_id}")
def get_space(space_id: str, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    _authorize_project_for_principal(principal, space_id)
    try:
        return orgops.get_space(space_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@org_router.get("/search")
def search_memory(
    query: str = Query(default="", max_length=400),
    space_ids: str = "",
    memory_type: str = "",
    limit: int = Query(10, ge=1, le=50),
    authorization: str | None = Header(default=None),
):
    """Same retrieval the product uses, exposed with the agent-facing shape."""
    principal = _authenticate(authorization)
    targets = _spaces(principal, space_ids)
    single = targets[0] if len(targets) == 1 else ""
    result = _memory_search_core(principal, query, project_id=single, type=memory_type, limit=limit)
    results = [
        {**orgops.public_memory(item), "score": item.get("score")}
        for item in result["results"]
        if not space_ids or item["project_id"] in targets
    ]
    return {
        "query": query,
        "searched_spaces": len(targets),
        "count": len(results),
        "results": results,
    }


@org_router.get("/recent-changes")
def recent_changes(
    since: str = "",
    space_ids: str = "",
    limit: int = Query(40, ge=1, le=100),
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    return orgops.get_recent_changes(_spaces(principal, space_ids), since=since, limit=limit)


@org_router.get("/decisions")
def decisions(
    space_ids: str = "",
    status: str = "",
    since: str = "",
    limit: int = Query(40, ge=1, le=100),
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    return orgops.get_decisions(
        _spaces(principal, space_ids), status=status, since=since, limit=limit
    )


@org_router.get("/tasks")
def tasks(
    space_ids: str = "",
    assignee: str = "",
    priority: str = "",
    status: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    return orgops.get_open_tasks(
        _spaces(principal, space_ids), assignee=assignee, priority=priority, status=status
    )


@org_router.get("/tasks/{task_id}/dependencies")
def task_dependencies(task_id: str, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    record = row("SELECT project_id FROM org_tasks WHERE id=?", (task_id,))
    if not record:
        raise HTTPException(404, "Task not found")
    _authorize_project_for_principal(principal, record["project_id"])
    return orgops.get_task_dependencies(task_id)


@org_router.get("/people")
def people(
    person_id: str = "",
    query: str = "",
    space_ids: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    return orgops.get_people_context(
        principal.get("active_workspace_id", ""),
        _spaces(principal, space_ids),
        person_id=person_id,
        query=query,
    )


@org_router.get("/owner/{object_id}")
def owner(object_id: str, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    try:
        return orgops.get_owner(_spaces(principal), object_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@org_router.get("/context")
def project_context(
    space_ids: str = "",
    primary_space_id: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    return orgops.get_project_context(
        _spaces(principal, space_ids), primary_space_id=primary_space_id
    )


@org_router.get("/provenance/{memory_id}")
def provenance(memory_id: str, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    unit = company_memory.get(memory_id)
    if not unit:
        raise HTTPException(404, "Memory not found")
    _authorize_project_for_principal(principal, unit["project_id"])
    return orgops.get_provenance(memory_id)


@org_router.get("/reasoning-chain")
def reasoning_chain(
    topic: str = Query(default="", max_length=400),
    space_ids: str = "",
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    if not topic.strip():
        raise HTTPException(400, "topic is required")
    return orgops.get_reasoning_chain(_spaces(principal, space_ids), topic)


@org_router.get("/dependency-graph")
def dependency_graph(space_ids: str = "", authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    return orgops.get_dependency_graph(_spaces(principal, space_ids))


@org_router.get("/blockers")
def blockers(space_ids: str = "", authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    return orgops.find_blockers(_spaces(principal, space_ids))


@org_router.get("/conflicts")
def conflicts(
    space_ids: str = "", topic: str = "", authorization: str | None = Header(default=None)
):
    principal = _authenticate(authorization)
    return orgops.find_conflicts(_spaces(principal, space_ids), topic=topic)


@org_router.get("/stale")
def stale(
    space_ids: str = "",
    topic: str = "",
    max_age_days: int = Query(90, ge=1, le=3650),
    authorization: str | None = Header(default=None),
):
    principal = _authenticate(authorization)
    return orgops.find_stale_information(
        _spaces(principal, space_ids), topic=topic, max_age_days=max_age_days
    )


@org_router.get("/readiness")
def readiness(space_ids: str = "", authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    return orgops.get_readiness(_spaces(principal, space_ids))


# ------------------------------------------------------------ gated writes


@org_router.post("/plans")
def propose_plan(request: PlanRequest, authorization: str | None = Header(default=None)):
    """Record what an agent wants to change. Nothing is applied until approval."""
    principal = _authorize_workspace(authorization)
    allowed = _authorized_space_ids(principal)
    operations = [operation.model_dump() for operation in request.operations]
    for operation in operations:
        target = operation.get("space_id") or ""
        if target and target not in allowed:
            raise HTTPException(403, f"No access to space {target}")
        if operation.get("task_id"):
            record = row("SELECT project_id FROM org_tasks WHERE id=?", (operation["task_id"],))
            if not record:
                raise HTTPException(404, f"Task {operation['task_id']} not found")
            if record["project_id"] not in allowed:
                raise HTTPException(403, "No access to that task")
    try:
        plan = orgops.propose_plan(
            principal["active_workspace_id"],
            str(principal["id"]),
            request.space_id,
            request.summary,
            operations,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(
        "org.plan.proposed",
        plan["summary"] or "Agent proposed organizational changes",
        request.space_id,
        actor=str(principal["id"]),
        payload={"plan_id": plan["id"], "operations": len(plan["operations"])},
    )
    return plan


@org_router.get("/plans")
def list_plans(status: str = "", authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    plans = orgops.list_plans(principal.get("active_workspace_id", ""), status=status)
    return {"count": len(plans), "plans": plans}


@org_router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    if principal["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Owner or admin session required to apply changes")
    try:
        plan = orgops.approve_plan(plan_id, principal["active_workspace_id"], str(principal["id"]))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    audit.record(
        "org.plan.approved",
        plan["summary"] or "Approved agent-proposed changes",
        plan["space_id"],
        actor=str(principal["id"]),
        payload={"plan_id": plan_id, "results": plan["results"]},
    )
    return plan


@org_router.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    if principal["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Owner or admin session required")
    try:
        plan = orgops.reject_plan(plan_id, principal["active_workspace_id"], str(principal["id"]))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return plan


# ------------------------------------------------------------------ scenario


@org_router.post("/scenario/seed")
def seed_scenario(request: SeedRequest, authorization: str | None = Header(default=None)):
    """Create the multi-space launch scenario, or rewind it to run again."""
    principal = _authorize_workspace(authorization)
    if principal["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Owner or admin session required")
    workspace_id = principal["active_workspace_id"]
    result = seed_launch_scenario(
        workspace_id,
        ingestion.create_project,
        company_memory,
        ingest_item=ingestion.ingest_item,
    )
    if request.reset:
        result["reset"] = reset_launch_scenario(workspace_id)
    return result


# ------------------------------------------------------------- standing watch


@org_router.get("/watches")
def list_watches(authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    items = watches.list(principal.get("active_workspace_id", ""))
    return {"count": len(items), "watches": items}


@org_router.post("/watches")
def create_watch(request: WatchRequest, authorization: str | None = Header(default=None)):
    """Run the organizational checks on an interval instead of on request."""
    principal = _authorize_workspace(authorization)
    if principal["role"] == "viewer":
        raise HTTPException(403, "Viewer role cannot create a watch")
    space_ids = _spaces(principal, ",".join(request.space_ids))
    try:
        watch = watches.create(
            principal["active_workspace_id"],
            str(principal["id"]),
            request.name,
            space_ids,
            request.checks,
            request.interval_seconds,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(
        "org.watch.created",
        f"Started watch: {watch['name']}",
        actor=str(principal["id"]),
        payload={"watch_id": watch["id"], "spaces": len(space_ids)},
    )
    return watch


@org_router.post("/watches/{watch_id}/run")
def run_watch(watch_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    try:
        watch = watches.get(watch_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    # A watch reads only spaces the caller can already reach.
    _spaces(principal, ",".join(watch["space_ids"]))
    return watches.run(watch_id, str(principal["id"]))


@org_router.post("/watches/{watch_id}/findings/{finding_id}/resolve")
def resolve_finding(
    watch_id: str, finding_id: str, authorization: str | None = Header(default=None)
):
    principal = _authorize_workspace(authorization)
    try:
        return watches.resolve_finding(finding_id, principal["active_workspace_id"])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@org_router.delete("/watches/{watch_id}")
def delete_watch(watch_id: str, authorization: str | None = Header(default=None)):
    principal = _authorize_workspace(authorization)
    if principal["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Owner or admin session required")
    watches.delete(watch_id, principal["active_workspace_id"])
    return {"status": "deleted"}


# ------------------------------------------------------------- agent console


def _org_executor(principal: dict, space_ids: list[str]):
    """Bind the tool surface to one caller's authorized spaces."""

    def resolve(_: dict) -> list[str]:
        return space_ids

    def search(caller: dict, query: str, memory_type: str, limit: int) -> dict:
        result = _memory_search_core(caller, query, type=memory_type, limit=limit)
        results = [
            {**orgops.public_memory(item), "score": item.get("score")}
            for item in result["results"]
            if item["project_id"] in space_ids
        ]
        return {"query": query, "count": len(results), "results": results}

    def propose(caller: dict, summary: str, space_id: str, operations: list[dict]) -> dict:
        target = space_id if space_id in space_ids else (space_ids[0] if space_ids else "")
        return orgops.propose_plan(
            caller["active_workspace_id"],
            str(caller["id"]),
            target,
            summary,
            operations,
            origin="console",
        )

    return build_org_executor(orgops, resolve, search, propose)


def _run_org_session(run_id: str, principal: dict, question: str, space_ids: list[str], model: str):
    def on_step(step: dict) -> None:
        org_sessions.append_step(run_id, step)

    try:
        result = org_agent.run(
            principal=principal,
            question=question,
            model=model or None,
            exec_tool=_org_executor(principal, space_ids),
            list_spaces=lambda _: [
                {"project_id": space["id"], "name": space["name"]}
                for space in orgops.list_spaces(space_ids)
            ],
            on_step=on_step,
        )
        org_sessions.update(
            run_id,
            status="complete",
            answer=result["answer"],
            memory_ids=result["memory_ids"],
            proposal=result.get("proposal"),
            thoughts=result.get("thoughts", []),
            mode=result.get("mode", "model"),
        )
    except Exception as exc:
        logger.exception("Organizational agent session failed")
        org_sessions.update(run_id, status="error", error=str(exc))


@org_router.post("/ask")
def ask_org(request: AskRequest, authorization: str | None = Header(default=None)):
    """Answer a free-text question by letting a model drive the tool surface.

    Returns immediately. The console polls while tool calls land one at a time,
    which is the point: what shows up is the agent's own sequence, not a
    sequence the page decided in advance.
    """
    principal = _authenticate(authorization)
    space_ids = _spaces(principal, ",".join(request.space_ids))
    if not space_ids:
        raise HTTPException(400, "No authorized spaces to search")
    model = configured_model(request.model or None)
    run_id = org_sessions.create(
        request.question, model.id if model else "", principal.get("active_workspace_id", "")
    )
    audit.record(
        "org.agent_session.started",
        f"Console question: {request.question[:80]}",
        actor=str(principal["id"]),
        payload={"session_id": run_id, "spaces": len(space_ids)},
    )
    Thread(
        target=_run_org_session,
        args=(run_id, principal, request.question, space_ids, request.model or ""),
        daemon=True,
    ).start()
    return org_sessions.get(run_id, principal.get("active_workspace_id", ""))


@org_router.get("/ask/{run_id}")
def org_session(run_id: str, authorization: str | None = Header(default=None)):
    principal = _authenticate(authorization)
    record = org_sessions.get(run_id, principal.get("active_workspace_id", ""))
    if not record:
        raise HTTPException(404, "Session not found")
    return record


@org_router.post("/ask/stream")
def ask_org_stream(request: AskRequest, authorization: str | None = Header(default=None)):
    """Stream one agent session as NDJSON over a single request.

    The session registry is process-local, so a console that polls
    ``/ask/{run_id}`` can reach a different instance mid-run and lose the
    trace entirely. Running the same loop inside the request keeps the run,
    its tool calls, and its trace on one instance — and lets the console
    render each step the moment it lands. ``POST /ask`` stays for SDK and
    single-instance deployments.
    """
    principal = _authenticate(authorization)
    space_ids = _spaces(principal, ",".join(request.space_ids))
    if not space_ids:
        raise HTTPException(400, "No authorized spaces to search")
    model = configured_model(request.model or None)
    run_id = org_sessions.create(
        request.question, model.id if model else "", principal.get("active_workspace_id", "")
    )
    audit.record(
        "org.agent_session.started",
        f"Console question: {request.question[:80]}",
        actor=str(principal["id"]),
        payload={"session_id": run_id, "spaces": len(space_ids), "transport": "stream"},
    )

    events: queue.Queue[tuple[str, dict]] = queue.Queue()

    def on_step(step: dict) -> None:
        org_sessions.append_step(run_id, step)
        events.put(("step", step))

    def worker() -> None:
        try:
            result = org_agent.run(
                principal=principal,
                question=request.question,
                model=request.model or None,
                exec_tool=_org_executor(principal, space_ids),
                list_spaces=lambda _: [
                    {"project_id": space["id"], "name": space["name"]}
                    for space in orgops.list_spaces(space_ids)
                ],
                on_step=on_step,
            )
            org_sessions.update(
                run_id,
                status="complete",
                answer=result["answer"],
                memory_ids=result["memory_ids"],
                proposal=result.get("proposal"),
                thoughts=result.get("thoughts", []),
                mode=result.get("mode", "model"),
            )
            events.put(
                (
                    "done",
                    org_sessions.get(run_id, principal.get("active_workspace_id", "")) or {},
                )
            )
        except Exception as exc:  # noqa: BLE001 - the stream must end with an event
            logger.exception("Organizational agent session failed")
            org_sessions.update(run_id, status="error", error=str(exc))
            events.put(("error", {"message": str(exc)}))

    Thread(target=worker, daemon=True).start()

    def stream():
        yield json.dumps({"type": "start", "id": run_id, "model": model.id if model else ""}) + "\n"
        deadline = time.monotonic() + 300
        while True:
            try:
                # Pings every 10 s: a proxy that sees a quiet connection for
                # longer may close it mid-run, which the browser reports only
                # as "network error".
                kind, payload = events.get(timeout=10)
            except queue.Empty:
                if time.monotonic() > deadline:
                    org_sessions.update(run_id, status="error", error="Timed out")
                    yield json.dumps(
                        {"type": "error", "message": "The agent run timed out."}
                    ) + "\n"
                    return
                yield json.dumps({"type": "ping"}) + "\n"
                continue
            if kind == "done":
                yield json.dumps({"type": "done", "session": payload}, default=str) + "\n"
                return
            if kind == "error":
                yield (
                    json.dumps(
                        {
                            "type": "error",
                            "message": payload.get("message") or "The agent could not finish.",
                        }
                    )
                    + "\n"
                )
                return
            yield json.dumps({"type": "step", "step": payload}, default=str) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


_FOLLOWUP_PROMPT = """You suggest the next question for an operator of an organizational-memory console.

THEY JUST ASKED: {question}
THE AGENT ANSWERED: {answer}
THE AGENT'S TOOLS FOUND:
{findings}

Draft exactly 3 follow-up questions this operator would realistically ask next,
given these specific findings. Rules:
- Under 60 characters each, phrased as a request or question to the console.
- Name the actual task, person, service, or decision from the findings.
- Never generic ("tell me more"), never about topics the findings do not cover.
- At most one question may be about approving or reconciling a proposed change.

Reply with ONLY: {{"suggestions": ["...", "...", "..."]}}"""


@org_router.post("/followups")
def org_followups(request: FollowupsRequest, authorization: str | None = Header(default=None)):
    """The next questions, drafted from what this session actually found.

    Progressive on purpose: the suggestions come from the model reading the
    live findings, and when no model is reachable they are derived from the
    workspace's own state — blockers, contradictions, owners — so the console
    never falls back to a fixed script.
    """
    principal = _authenticate(authorization)
    space_ids = _spaces(principal, ",".join(request.space_ids))
    findings = "\n".join(f"- {item}" for item in request.summaries[-6:] if item.strip()) or (
        "- (no tool findings recorded)"
    )
    try:
        result = generate_grounded_json(
            _FOLLOWUP_PROMPT.format(
                question=request.question[:400],
                answer=(request.answer or "(none)")[:600],
                findings=findings,
            )
        )
    except Exception:  # noqa: BLE001 - suggestions must never break the console
        result = None
    suggestions: list[str] = []
    if result and isinstance(result[0], dict):
        raw = result[0].get("suggestions")
        if isinstance(raw, list):
            suggestions = [str(item).strip()[:80] for item in raw if str(item).strip()][:3]
    if len(suggestions) < 3:
        fallback = _deterministic_followups(space_ids, request.question)
        for item in fallback:
            if item not in suggestions:
                suggestions.append(item)
            if len(suggestions) == 3:
                break
    return {"suggestions": suggestions, "source": "model" if result else "workspace"}


def _deterministic_followups(space_ids: list[str], question: str) -> list[str]:
    """Follow-ups derived from the workspace's own state, not a script."""
    ideas: list[str] = []
    conflicts = orgops.find_conflicts(space_ids).get("conflicts", [])
    if conflicts:
        task_title = conflicts[0]["task"]["title"]
        ideas.append(f"Reconcile “{task_title}”")
        ideas.append(f"Who owns “{task_title}”?")
    blockers = orgops.find_blockers(space_ids).get("blockers", [])
    if blockers:
        blocker = blockers[0]["task"]
        ideas.append(f"What unblocks “{blocker['title']}”?")
        if blocker.get("owner"):
            ideas.append(f"What is {blocker['owner']} waiting on?")
    readiness = orgops.get_readiness(space_ids)
    if readiness.get("outstanding"):
        ideas.append("Are we ready to launch yet?")
    # People come from workspace membership; with no workspace bound here the
    # people branch simply contributes nothing.
    people = orgops.get_people_context("", space_ids).get("people", [])
    if people:
        owner = people[0]
        if owner.get("open_tasks"):
            ideas.append(f"What is on {owner['name']}'s plate?")
    if not ideas:
        ideas = ["What changed recently?", "What is unresolved?"]
    return ideas[:3]
