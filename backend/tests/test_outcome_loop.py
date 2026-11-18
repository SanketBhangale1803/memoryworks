"""The context → action → outcome loop, and the corpus it accumulates."""

import pytest

from app.audit import AuditService
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.llm.providers import model_runtime
from app.outcomes import (
    export_training_records,
    record_action,
    record_context,
    record_outcome,
    stats,
)
from app.retrieval import RetrievalService

WORKSPACE = "wsp_test"


def _project(graph):
    hcag = HCAGAdapter(graph)
    project_id = IngestionService(graph, hcag, AuditService()).create_project("Loop")
    return hcag, project_id


def _served(project_id, *, lens="direct", candidates=2, answer="Because the config drifted."):
    """A representative /api/ask result, without needing a live model."""
    return {
        "answer": answer,
        "answer_sufficient": True,
        "answer_scope": "company_memory",
        "answer_kind": "diagnostic",
        "confidence": 0.71,
        "trust_score": {"score": 0.8},
        "model": {"used": "gemini"},
        "evidence": [{"chunk_id": "chunk-1", "source_id": "src:1"}],
        "context_envelope": {"id": "env_1"},
        "deliberation": {
            "selected_lens": lens,
            "candidate_count": candidates,
            "judged": True,
            "candidates": [
                {"lens": "direct", "selected": lens == "direct", "excerpt": "..."},
                {"lens": "causal", "selected": lens == "causal", "excerpt": "..."},
            ],
        },
        "handoff": {"files": ["app/checkout.py"]},
    }


def test_asking_records_the_context_that_was_served(graph):
    hcag, project_id = _project(graph)

    result = RetrievalService(hcag).ask(
        project_id, "hello", principal={"id": "usr_1", "active_workspace_id": WORKSPACE}
    )

    assert result["context_event_id"]
    assert stats(WORKSPACE)["contexts"] == 1


def test_stats_attribute_answers_to_the_provider_that_was_used(graph):
    _, project_id = _project(graph)
    for used in ("gemini", None):
        event_id = record_context(
            project_id=project_id,
            query="Why did checkout fail?",
            result={**_served(project_id), "model": model_runtime("glm", used)},
            workspace_id=WORKSPACE,
        )
        record_outcome(context_event_id=event_id, outcome="succeeded", workspace_id=WORKSPACE)

    assert {item["model_provider"]: item["served"] for item in stats(WORKSPACE)["by_model"]} == {
        "gemini": 1,
        "—": 1,
    }


def test_a_logging_failure_never_breaks_the_answer(graph, monkeypatch):
    hcag, project_id = _project(graph)
    monkeypatch.setattr(
        "app.outcomes.ledger.connect", lambda: (_ for _ in ()).throw(RuntimeError("disk full"))
    )

    result = RetrievalService(hcag).ask(
        project_id, "hello", principal={"id": "usr_1", "active_workspace_id": WORKSPACE}
    )

    assert result["answer"]
    assert result["context_event_id"] == ""


def test_the_full_triple_produces_a_labelled_training_record(graph):
    _, project_id = _project(graph)
    context_id = record_context(
        project_id=project_id,
        query="why is checkout failing?",
        result=_served(project_id),
        workspace_id=WORKSPACE,
    )

    action = record_action(
        context_event_id=context_id,
        action_type="handoff_copied",
        workspace_id=WORKSPACE,
        target="cursor",
    )
    record_outcome(
        context_event_id=context_id,
        outcome="succeeded",
        workspace_id=WORKSPACE,
        action_event_id=action["id"],
        signal="ci",
        reason="Checkout smoke test passed after the change.",
    )

    records = export_training_records(WORKSPACE)

    assert len(records) == 1
    record = records[0]
    assert record["label"] == "succeeded"
    assert record["reward"] == 1.0
    assert record["actions"][0]["action_type"] == "handoff_copied"
    assert record["handoff_files"] == ["app/checkout.py"]


def test_export_carries_a_judge_example_when_candidates_competed(graph):
    _, project_id = _project(graph)
    context_id = record_context(
        project_id=project_id,
        query="why is checkout failing?",
        result=_served(project_id, lens="causal"),
        workspace_id=WORKSPACE,
    )
    record_outcome(context_event_id=context_id, outcome="failed", workspace_id=WORKSPACE)

    judge_example = export_training_records(WORKSPACE)[0]["judge_example"]

    # This is the supervised signal for replacing the judge with a small model:
    # the same input the judge saw, the lens it picked, and whether that worked.
    assert judge_example["selected_lens"] == "causal"
    assert len(judge_example["candidates"]) == 2
    assert judge_example["label"] == "failed"
    assert judge_example["reward"] == -1.0


def test_unlabelled_contexts_are_excluded_unless_asked_for(graph):
    _, project_id = _project(graph)
    record_context(
        project_id=project_id,
        query="who owns billing?",
        result=_served(project_id),
        workspace_id=WORKSPACE,
    )

    assert export_training_records(WORKSPACE) == []
    assert len(export_training_records(WORKSPACE, labelled_only=False)) == 1


def test_the_latest_outcome_wins_when_an_observation_is_revised(graph):
    _, project_id = _project(graph)
    context_id = record_context(
        project_id=project_id,
        query="fix the checkout config",
        result=_served(project_id),
        workspace_id=WORKSPACE,
    )

    record_outcome(context_event_id=context_id, outcome="succeeded", workspace_id=WORKSPACE)
    record_outcome(
        context_event_id=context_id,
        outcome="failed",
        workspace_id=WORKSPACE,
        reason="Reverted an hour later.",
    )

    assert export_training_records(WORKSPACE)[0]["label"] == "failed"
    assert stats(WORKSPACE)["success_rate"] == 0.0


def test_stats_report_how_much_of_the_loop_is_actually_closed(graph):
    _, project_id = _project(graph)
    closed = record_context(
        project_id=project_id, query="a", result=_served(project_id), workspace_id=WORKSPACE
    )
    record_context(
        project_id=project_id, query="b", result=_served(project_id), workspace_id=WORKSPACE
    )
    record_outcome(context_event_id=closed, outcome="succeeded", workspace_id=WORKSPACE)

    report = stats(WORKSPACE)

    assert report["contexts"] == 2
    assert report["closed_rate"] == 0.5
    assert report["success_rate"] == 1.0
    assert report["trainable_examples"] == 1


def test_lens_breakdown_is_the_signal_for_which_reading_wins(graph):
    _, project_id = _project(graph)
    for lens, outcome in (("direct", "succeeded"), ("direct", "succeeded"), ("causal", "failed")):
        context_id = record_context(
            project_id=project_id,
            query="why is checkout failing?",
            result=_served(project_id, lens=lens),
            workspace_id=WORKSPACE,
        )
        record_outcome(context_event_id=context_id, outcome=outcome, workspace_id=WORKSPACE)

    by_lens = {item["selected_lens"]: item for item in stats(WORKSPACE)["by_lens"]}

    assert by_lens["direct"]["success_rate"] == 1.0
    assert by_lens["causal"]["success_rate"] == 0.0


def test_outcomes_never_cross_a_workspace_boundary(graph):
    _, project_id = _project(graph)
    context_id = record_context(
        project_id=project_id,
        query="internal incident detail",
        result=_served(project_id),
        workspace_id=WORKSPACE,
    )
    record_outcome(context_event_id=context_id, outcome="succeeded", workspace_id=WORKSPACE)

    assert export_training_records("wsp_someone_else") == []
    with pytest.raises(LookupError):
        record_action(
            context_event_id=context_id,
            action_type="accepted",
            workspace_id="wsp_someone_else",
        )


def test_an_unknown_outcome_value_is_rejected(graph):
    _, project_id = _project(graph)
    context_id = record_context(
        project_id=project_id, query="a", result=_served(project_id), workspace_id=WORKSPACE
    )

    with pytest.raises(ValueError):
        record_outcome(context_event_id=context_id, outcome="mostly_fine", workspace_id=WORKSPACE)
