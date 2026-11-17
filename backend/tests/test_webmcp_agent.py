"""The live WebMCP agent runner: tools, grounding, and the proposal boundary.

These tests script the model decisions and run the real executors against a
real workspace, so the loop's behavior — tool grounding, proposal gating, and
workspace-private sessions — is verified without any network call.
"""

import httpx
import pytest
from fastapi import HTTPException

from app.api.routes import (
    _authorize_project_for_principal,
    _authorize_workspace,
    _memory_related_core,
    _memory_search_core,
    _propose_memory_core,
    _service_context_core,
    _visible_runbooks_core,
)
from app.audit import AuditService
from app.auth.app_auth import create_dev_session, create_workspace, issue_session
from app.core.database import connect, row
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.llm.providers import ModelProvider
from app.memory.company import CompanyMemoryService
from app.webmcp_agent import AgentSessionStore, WebMCPAgentRunner


def _project(graph, workspace_id: str, name: str) -> str:
    project_id = IngestionService(graph, HCAGAdapter(graph), AuditService()).create_project(name)
    with connect() as conn:
        conn.execute("INSERT INTO workspace_projects VALUES (?,?)", (workspace_id, project_id))
    return project_id


def _workspace_session(email: str, name: str, workspace_name: str) -> dict:
    user = create_dev_session(email, name)
    workspace = create_workspace(workspace_name, user["token"])
    session = issue_session(user["user"]["id"], workspace["id"])
    principal = _authorize_workspace(f"Bearer {session['token']}")
    return {
        "principal": principal,
        "workspace_id": workspace["id"],
    }


def _executors():
    return WebMCPAgentRunner()._default_executors(
        _memory_search_core,
        _service_context_core,
        _visible_runbooks_core,
        _memory_related_core,
        _propose_memory_core,
        _authorize_project_for_principal,
    )


def test_agent_loop_grounds_answer_in_tools_and_proposes(graph):
    owner = _workspace_session("agent-owner@example.com", "Agent Owner", "Agent Workspace")
    principal = owner["principal"]
    project_id = _project(graph, owner["workspace_id"], "Agent project")
    memory = CompanyMemoryService(graph)
    incident = memory.create(
        project_id,
        "incident",
        "payments pool exhaustion",
        "Payments failed with PostgreSQL connection-pool exhaustion.",
        ["src_1"],
        0.9,
        {"project": project_id, "service": "payments"},
    )

    prompts: list[str] = []

    def scripted_llm(prompt: str):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {
                "thought": "Search previous incidents first.",
                "tool": "get_orgmemory_incidents",
                "arguments": {"service": "payments"},
            }
        if len(prompts) == 2:
            assert "OBSERVATION 1" in prompt and "payments pool exhaustion" in prompt
            return {
                "thought": "Found the precedent; answer and propose the recurrence.",
                "answer": "This matches the previous pool-exhaustion incident.",
                "memory_ids": [incident["id"]],
                "propose": {
                    "kind": "incident",
                    "subject": "payments pool exhaustion recurrence",
                    "content": "Symptoms match the earlier verified incident.",
                    "service": "payments",
                    "reason": "Verified against the remembered incident.",
                },
            }
        raise AssertionError("the loop should have stopped after the answer")

    runner = WebMCPAgentRunner(llm=scripted_llm)
    result = runner.run(
        principal=principal,
        question="Why is payments failing again?",
        project_id=project_id,
        exec_tool=_executors(),
    )

    assert result["answer"].startswith("This matches")
    assert result["memory_ids"] == [incident["id"]]
    assert [step["tool"] for step in result["steps"]] == ["get_orgmemory_incidents"]
    assert result["proposal"]["status"] == "pending_approval"
    assert result["proposal"]["subject"] == "payments pool exhaustion recurrence"

    # The proposal is real and queued for the human inbox — and nothing was
    # written to company memory by the agent itself.
    stored = row("SELECT status FROM memory_proposals WHERE id=?", (result["proposal"]["id"],))
    assert stored["status"] == "pending_approval"
    assert (
        row(
            "SELECT COUNT(*) AS n FROM memory_units WHERE project_id=? AND subject=?",
            (project_id, "payments pool exhaustion recurrence"),
        )["n"]
        == 0
    )


def test_agent_session_store_is_workspace_private():
    store = AgentSessionStore()
    run_id = store.create("Why is payments failing again?", "gemini", "wsp_one")

    record = store.get(run_id, "wsp_one")
    assert record and record["status"] == "running"

    # A different workspace can never read another workspace's agent trace.
    assert store.get(run_id, "wsp_two") is None

    store.append_step(run_id, {"tool": "search_orgmemory", "summary": "2 matched"})
    store.update(run_id, status="complete", answer="Grounded answer.")
    final = store.get(run_id, "wsp_one")
    assert final["status"] == "complete"
    assert len(final["steps"]) == 1


def test_agent_tools_reject_unauthorized_writes(graph):
    owner = _workspace_session("agent-writes@example.com", "Agent Writes", "Writes Workspace")
    stranger = _workspace_session("agent-stranger@example.com", "Stranger", "Stranger Workspace")
    project_id = _project(graph, owner["workspace_id"], "Writes project")

    with pytest.raises(HTTPException) as exc:
        _executors()(
            stranger["principal"],
            "propose_orgmemory_incident",
            {
                "project_id": project_id,
                "subject": "unauthorized proposal",
                "content": "A stranger must not propose into this project.",
            },
        )
    assert exc.value.status_code == 403


def test_service_context_and_related_cores_respect_authorization(graph):
    owner = _workspace_session("agent-ctx@example.com", "Agent Ctx", "Ctx Workspace")
    principal = owner["principal"]
    project_id = _project(graph, owner["workspace_id"], "Ctx project")
    memory = CompanyMemoryService(graph)
    old = memory.create(
        project_id,
        "fact",
        "payments workers",
        "Payments runs 16 workers.",
        ["a"],
        0.8,
        {"project": project_id, "service": "payments"},
    )
    new = memory.create(
        project_id,
        "fact",
        "payments workers",
        "Payments now runs 4 workers.",
        ["b"],
        0.9,
        {"project": project_id, "service": "payments"},
    )

    entries = _service_context_core(principal, "payments")
    assert len(entries) == 1
    assert entries[0]["project_id"] == project_id
    assert entries[0]["profile"]["current_facts"]

    related = _memory_related_core(principal, new["id"])
    assert any(entry["memory"]["id"] == old["id"] for entry in related["related"])


def test_a_run_that_spends_every_step_on_tools_still_answers():
    # Production, 2026-10-06: "find the open problems in SAP-AI-PRs, who owns
    # each, and draft a plan" spent all 8 turns calling tools and ended with
    # "I could not ground a complete answer ... 12 matching memories. 0
    # matching memories." The model now gets a turn that may only answer.
    prompts: list[str] = []

    def llm(prompt: str):
        prompts.append(prompt)
        if "NO tool calls left" in prompt:
            return {"thought": "answer", "answer": "Two open problems; Priya owns the retry bug."}
        return {"thought": "look", "tool": "find_orgmemory_blockers", "arguments": {}}

    result = WebMCPAgentRunner(llm=llm).run(
        principal={"id": "u1", "active_workspace_id": "w1"},
        question="find the open problems and who owns them",
        exec_tool=lambda principal, name, arguments: ("2 blockers", {"blockers": [1, 2]}),
        list_spaces=lambda _: [{"project_id": "proj_a", "name": "SAP-AI-PRs"}],
        max_steps=3,
    )

    assert result["answer"] == "Two open problems; Priya owns the retry bug."
    assert len(result["steps"]) == 3
    assert "3 of 3 tool calls left" in prompts[0]
    assert "LAST tool call" in prompts[2]


def test_a_model_that_never_answers_gets_every_finding_listed():
    def llm(prompt: str):
        return {"thought": "look", "tool": "search_orgmemory_records", "arguments": {"q": "x"}}

    found = iter(["12 matching memories", "3 owners", "0 matching memories"])
    result = WebMCPAgentRunner(llm=llm).run(
        principal={"id": "u1", "active_workspace_id": "w1"},
        question="find the open problems",
        exec_tool=lambda principal, name, arguments: (next(found), {}),
        list_spaces=lambda _: [{"project_id": "proj_a", "name": "SAP-AI-PRs"}],
        max_steps=3,
    )

    assert len(result["steps"]) == 3  # the answer-only turn ran no tool
    for summary in ("12 matching memories", "3 owners", "0 matching memories"):
        assert summary in result["answer"]


@pytest.mark.parametrize("final_turn", ["answer", "tool", "failure"])
def test_filed_plan_gets_one_answer_only_turn(final_turn):
    proposal = {"id": "prop_plan", "status": "pending_approval"}
    answer = "Priya should repair retries based on mem_retry. The plan awaits approval."
    prompts = []
    tool_calls = []

    def llm(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {"tool": "search_orgmemory", "arguments": {}}
        if len(prompts) == 2:
            return {"tool": "propose_orgmemory_changes", "arguments": {}}
        assert "NO tool calls left" in prompt
        assert "mem_retry" in prompt and "prop_plan" in prompt
        if final_turn == "failure":
            raise RuntimeError("Model unavailable")
        if final_turn == "tool":
            return {"tool": "propose_orgmemory_changes", "arguments": {}}
        return {"answer": answer, "memory_ids": ["mem_retry"], "propose": {"kind": "incident"}}

    def execute(principal, name, arguments):
        tool_calls.append(name)
        if name == "search_orgmemory":
            return "Priya owns retries.", {"id": "mem_retry"}
        return "Plan filed and waiting for a person to approve.", proposal

    result = WebMCPAgentRunner(llm=llm).run(
        principal={},
        question="Make a plan for fixing retries.",
        exec_tool=execute,
        list_spaces=lambda _: [{"project_id": "proj_a"}],
        max_steps=2,
    )

    assert len(prompts) == 3
    assert tool_calls == ["search_orgmemory", "propose_orgmemory_changes"]
    assert result["proposal"] == proposal
    assert result["memory_ids"] == ["mem_retry"]
    if final_turn == "answer":
        assert result["answer"] == answer
    else:
        assert "waiting for a person to approve" in result["answer"]
        assert "Priya owns retries" in result["answer"]


@pytest.mark.parametrize("unavailable", ["unconfigured", "http_error", "empty_reply"])
def test_agent_uses_guided_tools_when_model_is_unavailable(monkeypatch, unavailable):
    from app import webmcp_agent

    provider = ModelProvider("gpt", "GPT", "OpenAI", "test", "test", "openai_compatible")
    monkeypatch.setattr(
        webmcp_agent,
        "configured_model",
        lambda _: None if unavailable == "unconfigured" else provider,
    )

    def generate(*args):
        assert unavailable != "unconfigured"
        if unavailable == "http_error":
            raise httpx.ConnectError("Model unavailable")
        return None

    monkeypatch.setattr(webmcp_agent, "generate_grounded_json", generate)
    answer = WebMCPAgentRunner().run(
        principal={},
        question="What did we decide?",
        model="gpt",
        list_spaces=lambda _: [{"project_id": "proj_a"}],
        exec_tool=lambda *args: ("1 matching memory", {"id": "mem_decision"}),
    )

    assert answer["mode"] == "guided"
    assert answer["answer"]
    assert answer["memory_ids"] == ["mem_decision"]
    assert answer["steps"][0]["tool"] == "search_orgmemory"


def test_runs_started_together_get_separate_sessions(monkeypatch):
    # Run ids were the start time in milliseconds: two Agent runs started in
    # the same millisecond shared one session and each showed the other's steps.
    import app.webmcp_agent as agent_module

    monkeypatch.setattr(agent_module.time, "time", lambda: 1_790_000_000.123)
    store = AgentSessionStore()
    first = store.create("which repos are connected?", "glm", "w1")
    second = store.create("who made the last commit?", "glm", "w1")

    assert first != second
    store.append_step(first, {"tool": "get_orgmemory_connections"})
    assert store.get(second, "w1")["steps"] == []


def test_guided_agent_uses_observations_for_service_names():
    result = WebMCPAgentRunner().run(
        principal={},
        question='Does company memory know "service": "fictional"?',
        list_spaces=lambda _: [{"project_id": "prj_empty"}],
        exec_tool=lambda *args: ("0 matching memories", {"results": []}),
    )

    assert result["mode"] == "guided"
    assert result["answer"] == "I did not find enough company memory to answer."
    assert [step["tool"] for step in result["steps"]] == [
        "search_orgmemory",
        "get_orgmemory_decisions",
    ]
    assert result["memory_ids"] == []
