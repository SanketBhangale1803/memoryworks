"""Answers and Agent runs that a person can stop.

Each streamed answer (and each Agent run) registers a stop signal under its id
and its owner. ``POST .../cancel`` sets it; the work checks it at every step and
inside every model call (app.llm.limits.raise_if_cancelled) and ends within a
moment, closing the model's connection instead of running on unseen.

Process-local, like the answers themselves: a stop reaches the API process that
is running the answer (see docs/SCALING.md for the multi-instance version).
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_active: dict[str, tuple[str, threading.Event]] = {}


def open_stream(stream_id: str, owner_id: str) -> threading.Event:
    event = threading.Event()
    with _lock:
        _active[stream_id] = (owner_id, event)
    return event


def cancel(stream_id: str, owner_id: str) -> bool:
    """Stop a stream its owner started. Returns whether one was found."""
    with _lock:
        found = _active.get(stream_id)
    if not found or found[0] != owner_id:
        return False
    found[1].set()
    return True


def close_stream(stream_id: str) -> None:
    with _lock:
        _active.pop(stream_id, None)
