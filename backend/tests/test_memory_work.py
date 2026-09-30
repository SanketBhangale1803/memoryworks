import pytest

from app.audit import AuditService
from app.core.config import settings
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.memory import CompanyBrainService
from app.retrieval import RetrievalService
from app.retrieval.semantic import get_reranker, get_semantic_provider
from app.work import MemoryWorkService


def _services(graph):
    settings.runbook_embedding_provider = "deterministic"
    settings.runbook_reranker_provider = "disabled"
    get_semantic_provider.cache_clear()
    get_reranker.cache_clear()
    audit = AuditService()
    hcag = HCAGAdapter(graph)
    ingestion = IngestionService(graph, hcag, audit)
    work = MemoryWorkService(
        RetrievalService(hcag, audit),
        CompanyBrainService(graph),
        audit,
    )
    return ingestion, work


def test_memory_work_creates_source_backed_portable_agent_packet(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Orion checkout")
    ingestion.ingest_item(
        project_id,
        "report",
        "Orion release plan",
        (
            "Release train Orion ships on Tuesday. Checkout deployment is blocked "
            "until database migration M42 completes. The Platform team owns M42."
        ),
        source_id="doc:orion-release",
        source_url="https://company.test/orion-release",
    )

    result = work.create(
        project_id,
        "Prepare a release readiness brief for Orion and checkout migration M42.",
        workspace_id="ws_test",
        requested_by="Test operator",
    )

    assert result["status"] == "completed"
    assert result["artifact_id"]
    assert result["context"]["evidence"]
    assert result["context"]["evidence"][0]["relevance"] > 0
    assert result["agent_packet"]["context"]["evidence"]
    assert result["agent_packet"]["constraints"]
    assert {step["step_type"] for step in result["steps"]} == {
        "context",
        "deliverable",
        "verification",
    }


def test_approved_slack_work_posts_exact_visible_message_and_records_permalink(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Launch communications")
    ingestion.ingest_item(
        project_id,
        "slack",
        "Launch channel decision",
        (
            "The launch update must be posted to Slack channel #launch after QA signs off. "
            "The update must mention the migration M42 blocker."
        ),
        source_id="slack:launch-decision",
    )

    result = work.create(
        project_id,
        "Post a Slack launch update about QA and migration M42.",
        workspace_id="ws_test",
        requested_by="Test operator",
    )
    action = next(step for step in result["steps"] if step["step_type"] == "connector_action")

    assert result["status"] == "awaiting_approval"
    assert action["status"] == "pending_approval"
    assert action["approval_required"] == 1
    assert "*MemoryWorks project update*" in action["input"]["message"]
    assert "Launch channel decision" in action["input"]["message"]
    with pytest.raises(ValueError, match="approved"):
        work.complete_step(result["id"], action["id"], {"message_ts": "1"}, "worker")

    class FakeSlack:
        def __init__(self):
            self.calls = []

        def post_message(self, channel_id, message):
            self.calls.append((channel_id, message))
            return {
                "channel_id": channel_id,
                "message_ts": "171234.567",
                "message": message,
                "source_url": "https://slack.test/archives/launch/p171234567",
            }

    slack = FakeSlack()
    reviewed_message = action["input"]["message"].replace(
        "*MemoryWorks project update*", "*Launch update*"
    )
    completed = work.approve_and_post_slack(
        result["id"],
        action["id"],
        "C_LAUNCH",
        reviewed_message,
        slack,
        "Approving owner",
    )

    assert slack.calls == [("C_LAUNCH", reviewed_message)]
    assert completed["status"] == "completed"
    posted = next(step for step in completed["steps"] if step["step_type"] == "connector_action")
    assert posted["output"]["message"] == reviewed_message
    assert posted["output"]["source_url"].startswith("https://slack.test/")
    assert any(event["event_type"] == "slack.posted" for event in completed["events"])
    assert (
        next(step for step in completed["steps"] if step["step_type"] == "verification")["status"]
        == "completed"
    )


def test_direct_team_notification_is_slack_action_only_and_waits_for_approval(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Team notifications")

    result = work.create(
        project_id,
        (
            "notify the team to meet at 8:00am in meeting room #18 regarding "
            "the discussion of new policies"
        ),
        workspace_id="ws_test",
        requested_by="Test operator",
    )

    action = next(step for step in result["steps"] if step["step_type"] == "connector_action")
    assert result["status"] == "awaiting_approval"
    assert result["target_connector"] == "slack"
    assert result["artifact_id"] == ""
    assert result["context"]["action_only"] is True
    assert result["context"]["evidence"] == []
    assert result["context"]["memory_units"] == []
    assert "no company memory was created" in result["context"]["answer"]
    assert action["status"] == "pending_approval"
    assert action["input"]["action_only"] is True
    assert action["input"]["message"] == (
        "*Team notification*\n\nHi everyone — please meet at 8:00am in meeting room #18 "
        "regarding the discussion of new policies."
    )


def test_direct_notification_rewrites_the_instruction_as_an_invitation(graph):
    _, work = _services(graph)

    message = work._direct_slack_message("notify the people to meet at 8")

    assert message == "*Team notification*\n\nHi everyone — please meet at 8."
    assert "notify the people" not in message.casefold()


def test_explicit_slack_update_copy_is_action_only_and_does_not_require_memory(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Launch announcement")

    result = work.create(
        project_id,
        "Draft a Slack update that we're launching Orgmemory officially",
        workspace_id="ws_test",
        requested_by="Test operator",
    )

    action = next(step for step in result["steps"] if step["step_type"] == "connector_action")
    assert result["status"] == "awaiting_approval"
    assert result["context"]["action_only"] is True
    assert action["input"]["message"] == (
        "*Team notification*\n\nHi everyone — We're launching Orgmemory officially."
    )


def test_failed_slack_post_is_visible_and_can_be_retried(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Retry communications")
    ingestion.ingest_item(
        project_id,
        "slack",
        "Launch source",
        "The launch update must mention that QA approved release R42.",
        source_id="slack:launch-source",
    )
    result = work.create(
        project_id,
        "Post a Slack update for release R42.",
        workspace_id="ws_test",
    )
    action = next(step for step in result["steps"] if step["step_type"] == "connector_action")

    class MissingScopeSlack:
        def post_message(self, channel_id, message):
            raise ValueError("Reconnect Slack and approve chat:write")

    failed = work.approve_and_post_slack(
        result["id"],
        action["id"],
        "C_RELEASE",
        action["input"]["message"],
        MissingScopeSlack(),
        "Test operator",
    )

    assert failed["status"] == "post_failed"
    failed_step = next(step for step in failed["steps"] if step["id"] == action["id"])
    assert failed_step["status"] == "failed"
    assert "chat:write" in failed_step["output"]["error"]


def test_previously_approved_slack_handoff_can_post_after_upgrade(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Existing approved work")
    ingestion.ingest_item(
        project_id,
        "doc",
        "Release status",
        "Release R9 passed QA and is ready for the launch channel.",
        source_id="doc:r9",
    )
    result = work.create(
        project_id,
        "Post a Slack update for release R9.",
        workspace_id="ws_test",
    )
    action = next(step for step in result["steps"] if step["step_type"] == "connector_action")
    approved = work.resolve_step(result["id"], action["id"], True, "Test operator")
    assert approved["status"] == "ready_for_worker"

    class FakeSlack:
        def post_message(self, channel_id, message):
            return {
                "channel_id": channel_id,
                "message_ts": "9.1",
                "message": message,
                "source_url": "https://slack.test/r9",
            }

    completed = work.approve_and_post_slack(
        result["id"],
        action["id"],
        "C_RELEASE",
        approved["steps"][2]["input"]["message"],
        FakeSlack(),
        "Test operator",
    )
    assert completed["status"] == "completed"


def test_memory_work_abstains_when_company_memory_is_insufficient(graph):
    ingestion, work = _services(graph)
    project_id = ingestion.create_project("Empty project")

    result = work.create(
        project_id,
        "Prepare a production launch plan for the lunar billing service.",
        workspace_id="ws_test",
    )

    assert result["status"] == "blocked_context"
    assert result["artifact_id"] == ""
    assert result["context"]["evidence"] == []
    assert result["steps"][0]["status"] == "blocked"
