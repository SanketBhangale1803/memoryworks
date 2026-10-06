"""Caps on model requests.

Every model call in the backend goes through :func:`llm_slot`, which applies two
kinds of limit:

* **Process-wide.** At most ``LLM_MAX_CONCURRENT_REQUESTS`` calls in flight and
  ``LLM_MAX_REQUESTS_PER_MINUTE`` started per minute, whoever asked. An import
  that extracts memory, an Agent run, and five people asking at once share them.
* **Per task.** A task — one answer, one Agent run — opens a :func:`llm_budget`
  with a wall-clock limit and a maximum number of calls. Its calls share both;
  once either runs out, further calls are refused instead of queued.

A refused or timed-out call raises :class:`LLMUnavailable`. Every caller already
treats a failed model call as "no model answer" and falls back to deterministic
grounding, so a cap degrades an answer rather than hanging it.

Budgets live in a context variable. Work handed to a thread pool must carry it
along with :func:`carry_budget`, or its calls would escape the task's limits.
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.core.config import settings

T = TypeVar("T")


class LLMUnavailable(RuntimeError):
    """A model call was refused by a cap or ran out of time."""


@dataclass
class LLMBudget:
    deadline: float
    max_calls: int
    calls: int = 0
    parent: LLMBudget | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def remaining_seconds(self) -> float:
        return self.deadline - time.monotonic()

    def reserve_call(self) -> None:
        # The enclosing task is asked first, so a refusal there is not counted here.
        if self.parent:
            self.parent.reserve_call()
        with self._lock:
            if self.calls >= self.max_calls:
                raise LLMUnavailable(f"This task already made {self.max_calls} model calls.")
            if self.remaining_seconds() <= 1:
                raise LLMUnavailable("This task's time for model calls has run out.")
            self.calls += 1


_budget: contextvars.ContextVar[LLMBudget | None] = contextvars.ContextVar(
    "llm_budget", default=None
)


def current_budget() -> LLMBudget | None:
    return _budget.get()


@contextmanager
def llm_budget(seconds: float, max_calls: int) -> Iterator[LLMBudget]:
    """Bound the model calls made inside the block. Nested budgets also count
    against, and never outlast, the budget they sit in."""
    parent = _budget.get()
    deadline = time.monotonic() + max(0.0, seconds)
    if parent:
        deadline = min(deadline, parent.deadline)
    budget = LLMBudget(deadline=deadline, max_calls=max(0, max_calls), parent=parent)
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


def carry_budget(fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap ``fn`` so it runs under the caller's budget on another thread."""
    context = contextvars.copy_context()

    def run(*args: Any, **kwargs: Any) -> T:
        return context.copy().run(fn, *args, **kwargs)

    return run


class _Gate:
    """Process-wide concurrency and rate limit."""

    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._in_flight = 0
        self._started: deque[float] = deque()

    def acquire(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                now = time.monotonic()
                while self._started and now - self._started[0] >= 60:
                    self._started.popleft()
                concurrent_ok = self._in_flight < max(1, settings.llm_max_concurrent_requests)
                rate_ok = len(self._started) < max(1, settings.llm_max_requests_per_minute)
                if concurrent_ok and rate_ok:
                    self._in_flight += 1
                    self._started.append(now)
                    return
                wait = deadline - now
                if wait <= 0:
                    raise LLMUnavailable(
                        "MemoryWorks is at its limit for model requests right now."
                    )
                # A rate-limited caller wakes when the oldest start leaves the window.
                if not rate_ok and self._started:
                    wait = min(wait, 60 - (now - self._started[0]) + 0.01)
                self._lock.wait(wait)

    def release(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._lock.notify_all()

    def reset(self) -> None:
        with self._lock:
            self._in_flight = 0
            self._started.clear()
            self._lock.notify_all()


_gate = _Gate()


@contextmanager
def llm_slot() -> Iterator[float]:
    """Hold a slot for one model call; yields the seconds that call may take."""
    budget = _budget.get()
    if budget:
        budget.reserve_call()
        allowed = budget.remaining_seconds()
    else:
        allowed = settings.llm_request_max_seconds
    allowed = min(allowed, settings.llm_request_max_seconds)
    started = time.monotonic()
    _gate.acquire(timeout=max(0.0, allowed))
    try:
        yield max(1.0, allowed - (time.monotonic() - started))
    finally:
        _gate.release()


def reset_limits() -> None:
    """Forget in-flight and recent calls (tests)."""
    _gate.reset()
