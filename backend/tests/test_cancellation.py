"""A person can stop an answer or an Agent run mid-way.

Stopping ends the work at its next step or inside its current model call, and
closes the model's connection instead of letting it run on unseen.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.core import streams
from app.core.config import settings
from app.llm import AnswerCancelled, cancellable, providers, raise_if_cancelled
from app.llm.limits import cancel_event, llm_slot, reset_limits


def test_a_stopped_model_reply_closes_mid_body(monkeypatch):
    stop = threading.Event()

    class Slow:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            for _ in range(100):
                time.sleep(0.02)
                yield b" "
                stop.set()  # the person presses Stop after the first bytes

    monkeypatch.setattr(providers, "_open_stream", lambda *a, **k: Slow())
    with pytest.raises(AnswerCancelled), cancellable(stop):
        providers._post_json("https://model.test", {}, {}, 30)


def test_a_stopped_model_call_hangs_up_before_any_reply():
    # A model called without streaming sends nothing until it has finished.
    # Stop must still end the call at once, not when the model gets round to it.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    accepted: list[socket.socket] = []
    threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True).start()
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()

    started = time.monotonic()
    with pytest.raises(AnswerCancelled), cancellable(stop):
        providers._post_json(f"http://127.0.0.1:{server.getsockname()[1]}/v1", {}, {}, 30)
    assert time.monotonic() - started < 2
    for conn in accepted:
        conn.close()
    server.close()


def test_waiting_for_a_model_slot_ends_when_stopped(monkeypatch):
    reset_limits()
    monkeypatch.setattr(settings, "llm_max_concurrent_requests", 1)
    stop = threading.Event()
    held = threading.Event()
    release = threading.Event()

    def hold():
        with llm_slot():
            held.set()
            release.wait(5)

    worker = threading.Thread(target=hold)
    worker.start()
    held.wait(5)
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    with pytest.raises(AnswerCancelled), cancellable(stop), llm_slot():
        pass
    assert time.monotonic() - started < 2
    release.set()
    worker.join()
    reset_limits()


def test_only_the_owner_can_stop_a_stream():
    event = streams.open_stream("answer_test", "user_a")
    assert streams.cancel("answer_test", "user_b") is False and not event.is_set()
    assert streams.cancel("answer_test", "user_a") is True and event.is_set()
    streams.close_stream("answer_test")
    assert streams.cancel("answer_test", "user_a") is False


def test_org_stream_session_is_readable_from_a_fresh_registry(graph, monkeypatch):
    from app.api import org_routes
    from app.main import app

    def agent(**kwargs):
        kwargs["on_step"]({"summary": "Found the source"})
        return {"answer": "The source is indexed.", "memory_ids": ["mem_example"]}

    monkeypatch.setattr(org_routes.org_agent, "run", agent)
    client = TestClient(app)
    login = client.post("/api/auth/dev-login", json={"email": "agent-history@example.com"})
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    project = client.post("/api/projects", json={"name": "Agent history"}, headers=headers)
    response = client.post(
        "/api/org/ask/stream",
        json={"question": "What did we index?", "space_ids": [project.json()["id"]]},
        headers=headers,
    )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [item["type"] for item in events] == ["start", "step", "done"]
    session = events[-1]["session"]

    monkeypatch.setattr(org_routes, "org_sessions", type(org_routes.org_sessions)())
    path = f"/api/org/ask/{session['id']}"
    assert client.get(path, headers=headers).json() == session

    other = client.post("/api/workspaces", json={"name": "Other Agent Workspace"}, headers=headers)
    assert other.status_code == 200
    other_login = client.post("/api/auth/dev-login", json={"email": "agent-history@example.com"})
    assert other_login.json()["user"]["active_workspace_id"] == other.json()["id"]
    other_headers = {"Authorization": f"Bearer {other_login.json()['token']}"}
    assert client.get(path, headers=other_headers).status_code == 404


def test_a_streamed_answer_ends_with_cancelled_when_stopped(graph, monkeypatch):
    from app.api import routes
    from app.main import app

    def slow_ask(*args, on_event=None, **kwargs):
        # narrates a step every 50 ms until stopped
        for index in range(200):
            on_event("step", {"label": f"step {index}", "detail": ""})
            time.sleep(0.05)
        return {"answer": "never"}

    monkeypatch.setattr(routes.retrieval, "ask", slow_ask)
    client = TestClient(app)
    token = client.post(
        "/api/auth/dev-login", json={"email": "stop@example.com", "display_name": "Stopper"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    project = client.post("/api/projects", json={"name": "Stoppable"}, headers=headers).json()

    user_id = client.get("/api/auth/me", headers=headers).json()["id"]

    # TestClient buffers a streaming response to completion, so read the
    # route's own stream as it is produced instead.
    from app.api.schemas import AskRequest

    response = routes.ask_stream(
        AskRequest(project_id=project["id"], query="anything"), authorization=f"Bearer {token}"
    )
    events: list[str] = []

    async def consume() -> None:
        async for chunk in response.body_iterator:
            event = json.loads(chunk)
            events.append(event["type"])
            if event["type"] == "start":
                threading.Timer(0.3, streams.cancel, args=(event["id"], user_id)).start()

    asyncio.run(consume())
    assert events[0] == "start"
    assert events[-1] == "cancelled"
    assert 2 <= events.count("step") < 50


def test_polled_agent_run_can_be_stopped_by_its_owner(graph, monkeypatch):
    from app.api import org_routes
    from app.main import app

    started = threading.Event()

    def slow_agent(**kwargs):
        stop = cancel_event()
        started.set()
        assert stop is not None and stop.wait(5)
        raise_if_cancelled()
        raise AssertionError("A stopped Agent must not continue")

    monkeypatch.setattr(org_routes.org_agent, "run", slow_agent)
    client = TestClient(app)
    login = client.post(
        "/api/auth/dev-login", json={"email": "agent-stop@example.com", "display_name": "Owner"}
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    project = client.post("/api/projects", json={"name": "Stoppable agent"}, headers=headers)
    run = client.post(
        "/api/org/ask",
        json={"question": "What should we do next?", "space_ids": [project.json()["id"]]},
        headers=headers,
    )
    path = f"/api/org/ask/{run.json()['id']}"
    assert started.wait(2)
    assert client.post(f"{path}/cancel", headers=headers).json() == {"cancelled": True}

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        record = client.get(path, headers=headers).json()
        if record["status"] != "running":
            break
        time.sleep(0.01)
    assert record["status"] == "cancelled"
    assert record["answer"] == ""
