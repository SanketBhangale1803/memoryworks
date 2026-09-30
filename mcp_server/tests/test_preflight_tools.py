from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


BRIEFING = {
    "briefing_id": "ctx_preflight",
    "task": "raise the payments worker limit",
    "service": "payments",
    "project_id": "prj_payments",
    "verdict": "requires_approval",
    "headline": "A prior incident and decision apply.",
    "consequential_action": "raising a limit",
    "must_read": [],
    "constraints": [],
    "prior_incidents": [],
    "blast_radius": [],
    "procedures": [],
    "precedents": [],
    "requires_approval": ["Raising a limit is consequential"],
    "safe_actions": ["Inspect the current configuration"],
    "open_questions": [],
    "memory_count": 2,
}


def _response(payload: dict, path: str) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", f"https://memory.test{path}"),
    )


def test_default_catalog_exposes_preflight_and_hides_legacy_tools() -> None:
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert "get_orgmemory_briefing" in by_name
    assert "record_orgmemory_outcome" in by_name
    assert not any(name.startswith("runbook_") for name in by_name)
    assert by_name["get_orgmemory_briefing"].annotations.readOnlyHint is True
    assert by_name["record_orgmemory_outcome"].annotations.idempotentHint is False


def test_briefing_uses_read_scope_and_preserves_the_backend_payload(
    monkeypatch,
) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        server,
        "get_access_token",
        lambda: SimpleNamespace(token="oauth_read", scopes=["read"]),
    )

    def request(method: str, url: str, **kwargs) -> httpx.Response:
        seen.update(method=method, url=url, **kwargs)
        return _response(BRIEFING, "/api/briefings")

    monkeypatch.setattr(server.httpx, "request", request)

    result = server.get_orgmemory_briefing(
        "raise the payments worker limit",
        service="payments",
        project_id="prj_payments",
        surface="cursor",
    )

    assert result == BRIEFING
    assert result["briefing_id"] == "ctx_preflight"
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/briefings")
    assert seen["headers"]["Authorization"] == "Bearer oauth_read"
    assert seen["json"] == {
        "task": "raise the payments worker limit",
        "service": "payments",
        "project_id": "prj_payments",
        "surface": "cursor",
    }


def test_outcome_requires_write_scope_and_closes_the_same_briefing(monkeypatch) -> None:
    receipt = {
        "briefing_id": "ctx_preflight",
        "action": {"id": "act_1", "action_type": "plan_changed"},
        "outcome": {"id": "out_1", "outcome": "succeeded", "reward": 1.0},
        "recorded": True,
    }
    seen: dict = {}
    monkeypatch.setattr(
        server,
        "get_access_token",
        lambda: SimpleNamespace(token="oauth_write", scopes=["write"]),
    )

    def request(method: str, url: str, **kwargs) -> httpx.Response:
        seen.update(method=method, url=url, **kwargs)
        return _response(receipt, "/api/briefings/outcome")

    monkeypatch.setattr(server.httpx, "request", request)

    result = server.record_orgmemory_outcome(
        "ctx_preflight",
        "plan_changed",
        "succeeded",
        target="acme/payments#418",
        surface="cursor",
        reason="The safer limit passed the scoped checks.",
        detail={"verification_scope": "test"},
    )

    assert result["briefing_id"] == "ctx_preflight"
    assert result["recorded"] is True
    assert seen["url"].endswith("/api/briefings/outcome")
    assert seen["json"]["detail"] == {"verification_scope": "test"}


def test_outcome_rejects_a_read_only_oauth_token(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "get_access_token",
        lambda: SimpleNamespace(token="oauth_read", scopes=["read"]),
    )

    try:
        server.record_orgmemory_outcome("ctx_preflight", "used", "unknown")
    except PermissionError as exc:
        assert "write" in str(exc)
    else:  # pragma: no cover - a network call here would violate the tool contract
        raise AssertionError("read-only token unexpectedly reported an outcome")
