"""The agent's change proposals land in a space, and a failed one is never reported as filed.

A two-repository workspace exposed both: the model proposed tasks without a
space_id, every attempt was refused, and the run still ended "The changes are
waiting for a person to approve" — sending someone to look for a plan that did
not exist.
"""

from __future__ import annotations

import pytest

from app.orgops.agent import _place_operations
from app.webmcp_agent import WebMCPAgentRunner

SPACES = [
    {"id": "proj_a", "name": "memoryworks", "repository": "https://github.com/acme/memoryworks"},
    {"id": "proj_b", "name": "SAP-AI-PRs", "repository": "https://github.com/acme/SAP-AI-PRs"},
]


def test_a_space_named_instead_of_identified_is_translated():
    placed = _place_operations(
        [{"op": "create_task", "title": "Fix retries", "space_id": "memoryworks"}], SPACES, ""
    )
    assert placed[0]["space_id"] == "proj_a"


def test_a_repository_path_also_identifies_the_space():
    placed = _place_operations(
        [
            {
                "op": "add_memory",
                "space_id": "acme/SAP-AI-PRs",
                "type": "fact",
                "title": "t",
                "content": "c",
            }
        ],
        SPACES,
        "",
    )
    assert placed[0]["space_id"] == "proj_b"


def test_the_top_level_space_fills_operations_that_omit_one():
    placed = _place_operations([{"op": "create_task", "title": "Fix retries"}], SPACES, "proj_b")
    assert placed[0]["space_id"] == "proj_b"


def test_a_single_space_is_never_ambiguous():
    placed = _place_operations([{"op": "create_task", "title": "Fix retries"}], SPACES[:1], "")
    assert placed[0]["space_id"] == "proj_a"


def test_an_ambiguous_omission_is_refused_with_the_valid_choices():
    with pytest.raises(ValueError) as refused:
        _place_operations([{"op": "create_task", "title": "Fix retries"}], SPACES, "")
    assert "proj_a (memoryworks)" in str(refused.value)
    assert "proj_b (SAP-AI-PRs)" in str(refused.value)


def test_a_space_outside_the_callers_access_is_refused():
    with pytest.raises(ValueError, match="not one you can change"):
        _place_operations(
            [{"op": "create_task", "title": "x", "space_id": "proj_elsewhere"}], SPACES, ""
        )


def test_update_task_is_located_by_its_task_not_a_space():
    placed = _place_operations([{"op": "update_task", "task_id": "task_1"}], SPACES, "")
    assert "space_id" not in placed[0]


def test_a_refused_proposal_is_reported_as_not_filed():
    def llm(prompt: str):
        return {
            "thought": "propose",
            "tool": "propose_orgmemory_changes",
            "arguments": {"summary": "s", "operations": [{"op": "create_task", "title": "x"}]},
        }

    def exec_tool(principal, name, arguments):
        raise ValueError("create_task needs space_id: the project_id of the space it belongs to.")

    result = WebMCPAgentRunner(llm=llm).run(
        principal={"id": "u1", "active_workspace_id": "w1"},
        question="propose a fix",
        exec_tool=exec_tool,
        list_spaces=lambda _: [{"project_id": "proj_a", "name": "memoryworks"}],
        max_steps=2,
    )

    assert "waiting for a person to approve" not in result["answer"]
    assert "nothing is waiting for approval" in result["answer"]
    assert "needs space_id" in result["answer"]
    assert result["proposal"] is None


def test_a_fragment_that_fits_two_spaces_is_refused_not_guessed():
    with pytest.raises(ValueError, match="not one you can change"):
        _place_operations([{"op": "create_task", "title": "x", "space_id": "acme"}], SPACES, "")
