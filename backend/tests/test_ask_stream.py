"""/api/ask/stream narrates an answer while it is being produced.

Someone waiting on a silent spinner for ten seconds cannot tell whether the
system is working or stuck. The stream reports each stage as it starts and the
answer's words as the model writes them, then ends with exactly what /api/ask
returns — so the preview can never be mistaken for a different answer.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app

FAKE_MODEL = SimpleNamespace(id="glm", label="GLM", model="z-ai/glm-test")
RETRY_DECISION = (
    "Decision (2026-11-10): The payments team moved webhook retries from a fixed 5s "
    "interval to exponential backoff with jitter (max 10 minutes), because fixed "
    "retries caused a retry storm during the Nov 3 outage. Owner: Priya Shah. "
    "The checkout-api service must call the idempotency endpoint before every retry."
)


def _signed_in_project(
    client: TestClient,
    *,
    title: str = "docs/checkout.md",
    content: str = "Checkout retries failed card charges three times before alerting.",
) -> tuple[dict[str, str], str]:
    token = client.post(
        "/api/auth/dev-login",
        json={"email": "stream@example.com", "display_name": "Stream"},
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post("/api/projects", json={"name": "Checkout"}, headers=headers).json()
    client.post(
        "/api/ingest/upload",
        json={
            "project_id": project["id"],
            "source_type": "doc",
            "title": title,
            "content": content,
        },
        headers=headers,
    ).raise_for_status()
    return headers, project["id"]


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_steps_and_words_arrive_before_the_final_answer(graph, monkeypatch):
    def fake_stream(prompt, provider_id=None, *, on_text, on_reasoning=None, **_):
        on_reasoning("The doc states the retry count.")
        for piece in ["Checkout retries ", "failed charges ", "three times [S1]."]:
            on_text(piece)
        return "Checkout retries failed charges three times [S1].", FAKE_MODEL

    monkeypatch.setattr("app.retrieval.reasoner.stream_text", fake_stream)
    monkeypatch.setattr("app.retrieval.service.configured_model", lambda _=None: FAKE_MODEL)

    with TestClient(app) as client:
        headers, project_id = _signed_in_project(client)
        response = client.post(
            "/api/ask/stream",
            json={"project_id": project_id, "query": "how many times does checkout retry?"},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = _events(response)
    kinds = [event["type"] for event in events]
    labels = [event["label"] for event in events if event["type"] == "step"]

    assert "Searching connected sources" in labels
    assert "Writing the answer" in labels
    assert "thinking" in kinds
    assert [event["text"] for event in events if event["type"] == "text"] == [
        "Checkout retries ",
        "failed charges ",
        "three times [S1].",
    ]
    # Words stream before the answer is final, and the stream ends on it.
    assert kinds.index("text") < kinds.index("done")
    assert kinds[-1] == "done"
    final = events[-1]["answer"]
    assert final["answer_sufficient"]
    assert "three times" in final["answer"]
    assert "[S1]" not in final["answer"], "citations are resolved to source titles"


def test_step_details_name_sources_never_machinery(graph, monkeypatch):
    monkeypatch.setattr(
        "app.retrieval.reasoner.stream_text",
        lambda prompt, provider_id=None, *, on_text, **_: (
            on_text("ok [S1]") or "ok [S1]",
            FAKE_MODEL,
        ),
    )
    monkeypatch.setattr("app.retrieval.service.configured_model", lambda _=None: FAKE_MODEL)

    with TestClient(app) as client:
        headers, project_id = _signed_in_project(client)
        events = _events(
            client.post(
                "/api/ask/stream",
                json={"project_id": project_id, "query": "how does checkout retry?"},
                headers=headers,
            )
        )

    narrated = " ".join(
        f"{event['label']} {event.get('detail', '')}" for event in events if event["type"] == "step"
    )
    assert "docs/checkout.md" in narrated
    for machinery in ("chunk", "score", "token", "swarm", "envelope"):
        assert machinery not in narrated.casefold()


def test_without_a_model_the_stream_still_ends_with_an_answer(graph, monkeypatch):
    monkeypatch.setattr("app.retrieval.service.configured_model", lambda _=None: None)

    with TestClient(app) as client:
        headers, project_id = _signed_in_project(client)
        events = _events(
            client.post(
                "/api/ask/stream",
                json={"project_id": project_id, "query": "how many times does checkout retry?"},
                headers=headers,
            )
        )

    assert events[-1]["type"] == "done"
    assert "text" not in {event["type"] for event in events}


@pytest.mark.parametrize(
    ("query", "expects_rationale"),
    [
        ("Who owns the webhook retry change?", False),
        ("Why did we change webhook retries, and who owns it?", True),
    ],
)
def test_ask_stream_grounds_the_decisions_explicit_owner(graph, query, expects_rationale):
    title = "Webhook retry decision"
    with TestClient(app) as client:
        headers, project_id = _signed_in_project(client, title=title, content=RETRY_DECISION)
        events = _events(
            client.post(
                "/api/ask/stream",
                json={"project_id": project_id, "query": query, "scope": "workspace"},
                headers=headers,
            ).raise_for_status()
        )

    assert events[-1]["type"] == "done"
    final = events[-1]["answer"]
    assert final["answer_sufficient"]
    assert "Priya Shah" in final["answer"]
    assert ("retry storm" in final["answer"]) == expects_rationale
    assert f"[{title}]" in final["answer"]
    assert [item["source_title"] for item in final["evidence"]] == [title]
    assert "Owner: Priya Shah." in final["evidence"][0]["exact_chunk"]
    assert final["memory_units"][0]["type"] == "decision"


@pytest.mark.parametrize(
    "query",
    [
        "Who owns the invoice webhook retry change?",
        "Who owns the Nov 3 outage?",
        "Who owns the checkout-api idempotency endpoint?",
        "Who owns the checkout timeout change?",
        "Why did we change the chargeback window, and who owns it?",
    ],
)
def test_ask_stream_refuses_when_the_decision_does_not_support_the_question(graph, query):
    content = (
        f"{RETRY_DECISION} "
        "Decision: The checkout timeout was set to 30 seconds to prevent long waits. "
        "Decision: The chargeback window was set to 14 days. Owner: Mei Tan."
    )
    with TestClient(app) as client:
        headers, project_id = _signed_in_project(client, content=content)
        events = _events(
            client.post(
                "/api/ask/stream",
                json={"project_id": project_id, "query": query, "scope": "workspace"},
                headers=headers,
            ).raise_for_status()
        )

    assert events[-1]["type"] == "done"
    final = events[-1]["answer"]
    assert not final["answer_sufficient"]
    assert final["answer"] == "I do not have enough company memory to answer this confidently."
    assert final["evidence"] == []
