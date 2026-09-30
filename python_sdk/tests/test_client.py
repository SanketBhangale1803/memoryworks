from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from orgmemory import AsyncMemoryWorks, MemoryWorks, MemoryWorksAPIError

ASK_PAYLOAD = {
    "answer": "Checkout moved to the ledger after incident 48.",
    "answer_sufficient": True,
    "confidence": 0.91,
    "trust_score": {"score": 0.88, "level": "high"},
    "retrieval_trace": {"scope_mode": "project"},
    "related_services": ["checkout"],
    "memory_units": [{"id": "mem_1", "type": "decision"}],
    "evidence": [
        {
            "chunk_id": "chunk_1",
            "source_type": "doc",
            "source_title": "Incident 48 review",
            "source_url": "https://example.test/review",
            "snippet": "The team moved checkout to the new ledger.",
            "confidence": 0.93,
            "project_id": "prj_platform",
        }
    ],
    "context_envelope": {
        "id": "ctx_1",
        "project_id": "prj_platform",
        "compiled_context": {"text": "Source-backed context"},
        "evidence_ids": ["chunk_1"],
        "activation_run_ids": ["swarm_1"],
        "token_budget": 6000,
    },
}

BRIEFING_PAYLOAD = {
    "briefing_id": "ctx_preflight",
    "task": "raise the payments worker limit",
    "service": "payments",
    "project_id": "prj_platform",
    "verdict": "requires_approval",
    "headline": "A prior incident and decision apply.",
    "consequential_action": "raising a limit",
    "must_read": [
        {
            "memory_id": "mem_incident",
            "type": "incident",
            "subject": "payments pool exhaustion",
            "content": "The worker limit exhausted the shared pool.",
            "why_it_matters": "This has gone wrong before",
            "service": "payments",
            "project_id": "prj_platform",
            "confidence": 0.94,
            "sources": 2,
        }
    ],
    "constraints": [],
    "prior_incidents": [],
    "blast_radius": [],
    "procedures": [],
    "precedents": [],
    "requires_approval": ["Raising a limit is consequential"],
    "safe_actions": ["Inspect the current configuration"],
    "open_questions": ["Which approver owns this service?"],
    "memory_count": 1,
    "failure_state": "none",
    "instrumentation_status": "recorded",
}


def test_ask_is_typed_and_sends_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ask"
        assert request.headers["authorization"] == "Bearer om_test"
        assert b'"model":"claude"' in request.read()
        return httpx.Response(200, json=ASK_PAYLOAD)

    with MemoryWorks(
        base_url="https://memory.test",
        api_key="om_test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.ask("prj_platform", "What changed?", model="claude")

    assert result.answer.startswith("Checkout moved")
    assert result.context_envelope_id == "ctx_1"
    assert result.compiled_context == {"text": "Source-backed context"}
    assert result.swarm_run_ids == ("swarm_1",)
    assert result.evidence[0].source_title == "Incident 48 review"


def test_api_error_includes_detail() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, json={"detail": "Project access denied"})
    )
    with (
        MemoryWorks(base_url="https://memory.test", transport=transport) as client,
        pytest.raises(MemoryWorksAPIError) as raised,
    ):
        client.projects()

    assert raised.value.status_code == 403
    assert raised.value.message == "Project access denied"


def test_preflight_and_outcome_are_typed_and_preserve_structured_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/briefings":
            return httpx.Response(200, json=BRIEFING_PAYLOAD)
        assert request.url.path == "/api/briefings/outcome"
        return httpx.Response(
            200,
            json={
                "briefing_id": "ctx_preflight",
                "action": {"id": "act_1", "action_type": "plan_changed"},
                "outcome": {"id": "out_1", "outcome": "succeeded", "reward": 1.0},
                "recorded": True,
            },
        )

    with MemoryWorks(
        base_url="https://memory.test",
        api_key="om_test",
        transport=httpx.MockTransport(handler),
    ) as client:
        briefing = client.get_briefing(
            "raise the payments worker limit",
            service="payments",
            project_id="prj_platform",
            surface="github_check",
        )
        receipt = client.record_briefing_outcome(
            briefing.briefing_id,
            "plan_changed",
            outcome="succeeded",
            target="acme/payments#418",
            surface="github_check",
            detail={"verification_scope": "fixture"},
        )

    assert briefing.verdict == "requires_approval"
    assert briefing.must_read[0].memory_id == "mem_incident"
    assert briefing.requires_approval == ("Raising a limit is consequential",)
    assert briefing.failure_state == "none"
    assert briefing.raw["instrumentation_status"] == "recorded"
    assert receipt.recorded is True
    assert receipt.action_type == "plan_changed"
    assert receipt.outcome == "succeeded"
    assert json.loads(requests[0].content)["surface"] == "github_check"
    assert json.loads(requests[1].content)["detail"] == {
        "verification_scope": "fixture"
    }


def test_async_client() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/health"
            return httpx.Response(200, json={"status": "ok", "product": "MemoryWorks"})

        async with AsyncMemoryWorks(
            base_url="https://memory.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            result = await client.health()
        assert result["status"] == "ok"

    asyncio.run(run())


def test_async_preflight_client() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/briefings":
                return httpx.Response(200, json=BRIEFING_PAYLOAD)
            return httpx.Response(
                200,
                json={
                    "briefing_id": "ctx_preflight",
                    "action": {"id": "act_1", "action_type": "accepted"},
                    "outcome": {"id": "out_1", "outcome": "partial", "reward": 0.5},
                    "recorded": True,
                },
            )

        async with AsyncMemoryWorks(
            base_url="https://memory.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            briefing = await client.get_briefing(
                "raise the payments worker limit",
                service="payments",
                project_id="prj_platform",
            )
            receipt = await client.record_briefing_outcome(
                briefing.briefing_id,
                "accepted",
                outcome="partial",
            )

        assert briefing.briefing_id == "ctx_preflight"
        assert receipt.outcome == "partial"
        assert receipt.reward == 0.5

    asyncio.run(run())


def test_pre_rename_client_names_still_import():
    from orgmemory import AsyncOrgMemory, OrgMemory, OrgMemoryAPIError

    assert OrgMemory is MemoryWorks
    assert AsyncOrgMemory is AsyncMemoryWorks
    assert OrgMemoryAPIError is MemoryWorksAPIError
