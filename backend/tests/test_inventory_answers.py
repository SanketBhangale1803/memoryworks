""""Which repos are connected?" is answered from records, in milliseconds.

It took 97 seconds and came back as quoted lines from documents: the answer
path searched every chunk of every repository for the words.
"""

import pytest

from app.audit import AuditService
from app.auth.app_auth import create_dev_session, create_workspace
from app.auth.vault import OAuthTokenVault
from app.hcag_adapter import HCAGAdapter
from app.ingestion import IngestionService
from app.retrieval import RetrievalService
from app.retrieval.inventory import is_inventory_question


@pytest.mark.parametrize(
    "question",
    [
        "Okay good, which repos are connected?",
        "what repositories have been imported",
        "Which sources are connected?",
        "what projects do you have",
        "list my repos",
        "show me the connected sources",
        "what's connected?",
    ],
)
def test_questions_about_what_is_connected(question):
    assert is_inventory_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "what repos use Redis?",
        "which service owns billing?",
        "why is the ingest connection pool exhausted?",
        "how is the webhook handler connected to the queue?",
    ],
)
def test_questions_about_contents_go_to_search(question):
    assert not is_inventory_question(question)


def test_connected_repos_come_from_records_without_a_search(graph, monkeypatch):
    session = create_dev_session("inventory@example.com", "Owner")
    workspace = create_workspace("Inventory workspace", session["token"])
    OAuthTokenVault(workspace["id"], session["user"]["id"]).save("github", "ext", "octo", "token")
    hcag = HCAGAdapter(graph)
    audit = AuditService()
    ingestion = IngestionService(hcag.graph, hcag, audit)
    repo = ingestion.create_project("acme/api", repository="acme/api")
    notes = ingestion.create_project("General memory")
    ingestion.ingest_item(repo, "doc", "README", "The API serves the billing endpoints.")

    def no_search(*args, **kwargs):
        raise AssertionError("an inventory question must not search chunks")

    monkeypatch.setattr(graph, "retrieve_context", no_search)
    result = RetrievalService(hcag, audit).ask(
        repo,
        "Okay good, which repos are connected?",
        [repo, notes],
        principal={
            "id": session["user"]["id"],
            "active_workspace_id": workspace["id"],
            "role": "owner",
        },
    )

    answer = result["answer"]
    assert "1 repository in memory" in answer
    assert "**acme/api** — 1 source in memory" in answer
    assert "General memory" in answer
    assert "GitHub (octo)" in answer
