"""Synchronous and asynchronous MemoryWorks API clients."""

from __future__ import annotations

import os
from types import TracebackType
from typing import Any, TypeVar

import httpx

from .exceptions import MemoryWorksAPIError
from .models import (
    AskResponse,
    BriefingOutcomeReceipt,
    BriefingResponse,
    ContextEnvelope,
    OutcomeLabel,
)

Json = dict[str, Any] | list[Any]
ClientT = TypeVar("ClientT", bound="MemoryWorks")
AsyncClientT = TypeVar("AsyncClientT", bound="AsyncMemoryWorks")


def _base_url(value: str | None) -> str:
    return (value or os.getenv("ORGMEMORY_API_URL") or "http://localhost:8000").rstrip(
        "/"
    )


def _api_key(value: str | None) -> str:
    return value if value is not None else os.getenv("ORGMEMORY_API_KEY", "")


def _headers(api_key: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "orgmemory-python/0.1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _decode(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.is_error:
        if isinstance(payload, dict):
            message = str(
                payload.get("detail")
                or payload.get("message")
                or response.reason_phrase
            )
        else:
            message = str(payload or response.reason_phrase)
        raise MemoryWorksAPIError(response.status_code, message, payload)
    return payload


class MemoryWorks:
    """Synchronous MemoryWorks client.

    The client owns its HTTP connection and can be used as a context manager.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = _base_url(base_url)
        self.api_key = _api_key(api_key)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=_headers(self.api_key),
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self: ClientT) -> ClientT:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return _decode(self._client.request(method, path, **kwargs))

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def models(self) -> dict[str, Any]:
        return self._request("GET", "/api/models")

    def projects(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/projects")

    def create_project(
        self,
        name: str,
        *,
        team_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/projects",
            json={"name": name, "team_ids": team_ids or []},
        )

    def ingest_source(
        self,
        project_id: str,
        content: str,
        *,
        title: str,
        source_type: str = "doc",
        source_url: str = "",
        source_id: str | None = None,
        team_ids: list[str] | None = None,
        artifact_type: str = "",
        artifact_name: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/ingest/upload",
            json={
                "project_id": project_id,
                "source_type": source_type,
                "title": title,
                "content": content,
                "source_url": source_url,
                "source_id": source_id,
                "team_ids": team_ids or [],
                "artifact_type": artifact_type,
                "artifact_name": artifact_name,
            },
        )

    def ask(
        self,
        project_id: str,
        query: str,
        *,
        token_budget: int = 6000,
        model: str | None = None,
    ) -> AskResponse:
        payload = self._request(
            "POST",
            "/api/ask",
            json={
                "project_id": project_id,
                "query": query,
                "token_budget": token_budget,
                "model": model,
            },
        )
        return AskResponse.from_dict(payload)

    def get_briefing(
        self,
        task: str,
        *,
        service: str = "",
        project_id: str = "",
        surface: str = "sdk",
    ) -> BriefingResponse:
        """Return deterministic, cited context before a consequential action."""
        payload = self._request(
            "POST",
            "/api/briefings",
            json={
                "task": task,
                "service": service,
                "project_id": project_id,
                "surface": surface,
            },
        )
        return BriefingResponse.from_dict(payload)

    def record_briefing_outcome(
        self,
        briefing_id: str,
        action: str,
        *,
        outcome: OutcomeLabel = "unknown",
        target: str = "",
        surface: str = "sdk",
        reason: str = "",
        detail: dict[str, Any] | None = None,
    ) -> BriefingOutcomeReceipt:
        """Append the action and observed outcome for a served briefing."""
        payload = self._request(
            "POST",
            "/api/briefings/outcome",
            json={
                "briefing_id": briefing_id,
                "action": action,
                "outcome": outcome,
                "target": target,
                "surface": surface,
                "reason": reason,
                "detail": detail or {},
            },
        )
        return BriefingOutcomeReceipt.from_dict(payload)

    def list_memories(
        self,
        project_id: str,
        *,
        memory_type: str = "",
        latest: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"project_id": project_id}
        if memory_type:
            params["type"] = memory_type
        if latest is not None:
            params["latest"] = latest
        return self._request("GET", "/api/memory/units", params=params)

    def graph_summary(self, project_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/memory/graph/summary",
            params={"project_id": project_id},
        )

    def company_profile(self, project_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/memory/profiles/company",
            params={"project_id": project_id},
        )

    def project_profile(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/memory/profiles/project/{project_id}")

    def service_profile(self, project_id: str, service_name: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/memory/profiles/service/{service_name}",
            params={"project_id": project_id},
        )

    def context_envelope(self, envelope_id: str) -> ContextEnvelope:
        return ContextEnvelope.from_dict(
            self._request("GET", f"/api/memory/context/{envelope_id}")
        )

    def swarm_run(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/memory/swarm/{run_id}")

    def create_work(self, project_id: str, objective: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/work",
            json={"project_id": project_id, "objective": objective},
        )

    def list_work(
        self,
        *,
        project_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/work",
            params={"project_id": project_id, "limit": limit},
        )

    def get_work(self, work_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/work/{work_id}")


class AsyncMemoryWorks:
    """Asynchronous MemoryWorks client."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = _base_url(base_url)
        self.api_key = _api_key(api_key)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=_headers(self.api_key),
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self: AsyncClientT) -> AsyncClientT:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return _decode(await self._client.request(method, path, **kwargs))

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/health")

    async def models(self) -> dict[str, Any]:
        return await self._request("GET", "/api/models")

    async def projects(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/projects")

    async def create_project(
        self,
        name: str,
        *,
        team_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/projects",
            json={"name": name, "team_ids": team_ids or []},
        )

    async def ingest_source(
        self,
        project_id: str,
        content: str,
        *,
        title: str,
        source_type: str = "doc",
        source_url: str = "",
        source_id: str | None = None,
        team_ids: list[str] | None = None,
        artifact_type: str = "",
        artifact_name: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/ingest/upload",
            json={
                "project_id": project_id,
                "source_type": source_type,
                "title": title,
                "content": content,
                "source_url": source_url,
                "source_id": source_id,
                "team_ids": team_ids or [],
                "artifact_type": artifact_type,
                "artifact_name": artifact_name,
            },
        )

    async def ask(
        self,
        project_id: str,
        query: str,
        *,
        token_budget: int = 6000,
        model: str | None = None,
    ) -> AskResponse:
        payload = await self._request(
            "POST",
            "/api/ask",
            json={
                "project_id": project_id,
                "query": query,
                "token_budget": token_budget,
                "model": model,
            },
        )
        return AskResponse.from_dict(payload)

    async def get_briefing(
        self,
        task: str,
        *,
        service: str = "",
        project_id: str = "",
        surface: str = "sdk",
    ) -> BriefingResponse:
        """Return deterministic, cited context before a consequential action."""
        payload = await self._request(
            "POST",
            "/api/briefings",
            json={
                "task": task,
                "service": service,
                "project_id": project_id,
                "surface": surface,
            },
        )
        return BriefingResponse.from_dict(payload)

    async def record_briefing_outcome(
        self,
        briefing_id: str,
        action: str,
        *,
        outcome: OutcomeLabel = "unknown",
        target: str = "",
        surface: str = "sdk",
        reason: str = "",
        detail: dict[str, Any] | None = None,
    ) -> BriefingOutcomeReceipt:
        """Append the action and observed outcome for a served briefing."""
        payload = await self._request(
            "POST",
            "/api/briefings/outcome",
            json={
                "briefing_id": briefing_id,
                "action": action,
                "outcome": outcome,
                "target": target,
                "surface": surface,
                "reason": reason,
                "detail": detail or {},
            },
        )
        return BriefingOutcomeReceipt.from_dict(payload)

    async def list_memories(
        self,
        project_id: str,
        *,
        memory_type: str = "",
        latest: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"project_id": project_id}
        if memory_type:
            params["type"] = memory_type
        if latest is not None:
            params["latest"] = latest
        return await self._request("GET", "/api/memory/units", params=params)

    async def graph_summary(self, project_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/memory/graph/summary",
            params={"project_id": project_id},
        )

    async def company_profile(self, project_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/api/memory/profiles/company",
            params={"project_id": project_id},
        )

    async def project_profile(self, project_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/memory/profiles/project/{project_id}")

    async def service_profile(
        self,
        project_id: str,
        service_name: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/memory/profiles/service/{service_name}",
            params={"project_id": project_id},
        )

    async def context_envelope(self, envelope_id: str) -> ContextEnvelope:
        return ContextEnvelope.from_dict(
            await self._request("GET", f"/api/memory/context/{envelope_id}")
        )

    async def swarm_run(self, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/memory/swarm/{run_id}")

    async def create_work(self, project_id: str, objective: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/work",
            json={"project_id": project_id, "objective": objective},
        )

    async def list_work(
        self,
        *,
        project_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/api/work",
            params={"project_id": project_id, "limit": limit},
        )

    async def get_work(self, work_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/work/{work_id}")
