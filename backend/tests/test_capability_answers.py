"""Questions about what MemoryWorks itself can do.

"Do you have the ability to ingest documents from GD?" found nothing in company
memory, fell through to a general-knowledge model call told the question was
*not* about the user's company, and on a busy server timed out. It is answered
from the product's own sources now, with no search and no model call.
"""

import pytest

from app.audit import AuditService
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.retrieval import RetrievalService
from app.retrieval.capabilities import capability_reply


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Do you have the ability to ingest documents from GD?", "Import from Google Drive"),
        ("How do I select Google Drive files in MemoryWorks UI?", "Import from Google Drive"),
        ("can you import from notion?", "Notion"),
        ("Can MemoryWorks read our Slack channels?", "Remember a channel"),
        ("can you pull in jira tickets?", "Not yet"),
    ],
)
def test_questions_about_what_memoryworks_can_bring_in(question, expected):
    reply = capability_reply(question)
    assert reply and expected in reply["answer"]
    assert reply["answer_scope"] == "assistant"


@pytest.mark.parametrize(
    "question",
    [
        "how do we add a file upload to the checkout page?",
        "how do I connect to the production database?",
        "why is the ingest service failing?",
        "can you tell me who owns the payments api?",
        "import the first 3 files from my drive",
    ],
)
def test_questions_about_the_company_are_left_to_the_answer_path(question):
    assert capability_reply(question) is None


def test_a_how_question_gets_steps_not_a_yes():
    reply = capability_reply("How do I select Google Drive files in MemoryWorks UI?")
    assert reply and not reply["answer"].startswith("Yes")


def test_capability_questions_spend_no_model_call(graph, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a capability question must not call a model")

    monkeypatch.setattr("app.retrieval.reasoner.stream_text", refuse)
    monkeypatch.setattr("app.retrieval.reasoner.generate_grounded_json", refuse)
    monkeypatch.setattr("app.retrieval.conversation.generate_grounded_json", refuse)
    hcag = HCAGAdapter(graph)
    audit = AuditService()
    project_id = IngestionService(graph, hcag, audit).create_project("memoryworks")

    result = RetrievalService(hcag, audit).ask(
        project_id, "Do you have the ability to ingest documents from GD?"
    )

    assert "Import from Google Drive" in result["answer"]


def test_a_slow_answer_during_imports_says_so(graph):
    from app.api.routes import _busy_message, connector_sync
    from app.auth.app_auth import create_dev_session, create_workspace

    session = create_dev_session("busy@example.com", "Busy")
    workspace = create_workspace("Busy workspace", session["token"])
    assert "took too long" in _busy_message(workspace["id"])

    connector_sync.enqueue(
        "google_drive",
        workspace["id"],
        session["user"]["id"],
        "selected-files",
        cursor={"file_ids": ["a", "b"]},
    )
    message = _busy_message(workspace["id"])
    assert "1 import is still running" in message
