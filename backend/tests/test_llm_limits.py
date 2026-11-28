import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.activity import answering, wait_for_answers
from app.core.config import settings
from app.llm import LLMUnavailable, carry_budget, llm_budget, providers
from app.llm.limits import llm_slot, reset_limits


@pytest.fixture(autouse=True)
def fresh_limits():
    reset_limits()
    yield
    reset_limits()


def test_a_task_budget_refuses_calls_past_its_cap():
    with llm_budget(30, max_calls=2):
        with llm_slot():
            pass
        with llm_slot():
            pass
        with pytest.raises(LLMUnavailable, match="2 model calls"), llm_slot():
            pass


def test_a_task_budget_refuses_calls_after_its_time_runs_out():
    with llm_budget(0.5, max_calls=10):
        time.sleep(0.6)
        with pytest.raises(LLMUnavailable, match="run out"), llm_slot():
            pass


def test_a_nested_budget_counts_against_the_one_it_sits_in():
    with llm_budget(30, max_calls=1) as outer:
        with llm_budget(30, max_calls=5), llm_slot():
            pass
        assert outer.calls == 1
        with pytest.raises(LLMUnavailable), llm_slot():
            pass


def test_thread_pool_work_carries_the_budget():
    def call():
        with llm_slot():
            return True

    with llm_budget(30, max_calls=2), ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(carry_budget(call)) for _ in range(3)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except LLMUnavailable:
                outcomes.append(False)
    assert sorted(outcomes) == [False, True, True]


def test_concurrent_calls_wait_for_a_slot_and_give_up_within_their_budget(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_concurrent_requests", 1)
    holding = threading.Event()
    release = threading.Event()

    def hold():
        with llm_slot():
            holding.set()
            release.wait(5)

    worker = threading.Thread(target=hold)
    worker.start()
    holding.wait(5)
    started = time.monotonic()
    with (
        llm_budget(1.5, max_calls=3),
        pytest.raises(LLMUnavailable, match="limit for model requests"),
        llm_slot(),
    ):
        pass
    assert time.monotonic() - started < 3
    release.set()
    worker.join()


def test_the_per_minute_cap_refuses_once_spent(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_requests_per_minute", 2)
    with llm_budget(1.2, max_calls=10):
        with llm_slot():
            pass
        with llm_slot():
            pass
        with pytest.raises(LLMUnavailable), llm_slot():
            pass


def test_a_model_reply_that_trickles_in_is_cut_off_at_the_deadline(monkeypatch):
    """httpx's timeout bounds the gap between bytes; keep-alive whitespace never
    trips it. The total limit has to be checked as the body arrives."""

    class Trickle:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            while True:
                time.sleep(0.2)
                yield b" "

    monkeypatch.setattr(providers, "_open_stream", lambda *args, **kwargs: Trickle())
    started = time.monotonic()
    with pytest.raises(LLMUnavailable, match="in time"):
        providers._post_json("https://model.test", {}, {}, 0.6)
    assert time.monotonic() - started < 2


def test_imports_wait_for_an_answer_in_progress_but_not_forever(graph):
    """The register is in the database, so a worker process sees the API's answers."""
    finished = threading.Event()

    def answer():
        with answering():
            time.sleep(0.5)
        finished.set()

    threading.Thread(target=answer).start()
    time.sleep(0.05)
    waited = wait_for_answers(5)
    assert finished.is_set()
    assert 0.3 < waited < 2

    with answering():
        assert wait_for_answers(0.2) < 0.5
