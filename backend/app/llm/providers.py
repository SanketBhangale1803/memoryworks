from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class ModelProvider:
    id: str
    label: str
    company: str
    model: str
    api_key: str
    family: str
    base_url: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _providers() -> list[ModelProvider]:
    return [
        ModelProvider(
            "glm",
            "GLM",
            "Z.AI",
            settings.glm_model,
            settings.openrouter_api_key.strip(),
            "openai_compatible",
            settings.glm_base_url.rstrip("/"),
        ),
        ModelProvider(
            "gpt",
            "GPT",
            "OpenAI",
            settings.openai_model,
            settings.openai_api_key,
            "openai_compatible",
            "https://api.openai.com/v1",
        ),
        ModelProvider(
            "claude",
            "Claude",
            "Anthropic",
            settings.anthropic_model,
            settings.anthropic_api_key,
            "anthropic",
        ),
        ModelProvider(
            "gemini",
            "Gemini",
            "Google",
            settings.gemini_model,
            settings.google_api_key,
            "gemini",
        ),
        ModelProvider(
            "grok",
            "Grok",
            "xAI",
            settings.grok_model,
            settings.xai_api_key,
            "openai_compatible",
            "https://api.x.ai/v1",
        ),
        ModelProvider(
            "kimi",
            "Kimi",
            "Moonshot AI",
            settings.kimi_model,
            settings.kimi_api_key,
            "openai_compatible",
            settings.kimi_base_url.rstrip("/"),
        ),
    ]


def model_catalog() -> list[dict[str, Any]]:
    default_id = settings.org_memory_default_model_provider.casefold()
    return [
        {
            "id": provider.id,
            "label": provider.label,
            "company": provider.company,
            "model": provider.model,
            "configured": provider.configured,
            "default": provider.id == default_id,
        }
        for provider in _providers()
    ]


def configured_model(requested: str | None = None) -> ModelProvider | None:
    providers = _providers()
    requested_id = (requested or settings.org_memory_default_model_provider).casefold()
    selected = next((item for item in providers if item.id == requested_id), None)
    if requested and selected:
        return selected if selected.configured else None
    if selected and selected.configured:
        return selected
    return next((item for item in providers if item.configured), None)


def model_runtime(requested: str | None, used_provider: str | None = None) -> dict[str, Any]:
    requested_id = (requested or settings.org_memory_default_model_provider).casefold()
    providers = _providers()
    requested_model = next((item for item in providers if item.id == requested_id), None)
    used_model = next((item for item in providers if item.id == used_provider), None)
    return {
        "requested": requested_id,
        "provider": used_model.id if used_model else requested_id,
        "label": (
            (used_model or requested_model).label if used_model or requested_model else requested_id
        ),
        "model": (used_model or requested_model).model if used_model or requested_model else "",
        "configured": bool(requested_model and requested_model.configured),
        "used": bool(used_model),
        "mode": "model_synthesis" if used_model else "deterministic_grounding",
    }


def generate_grounded_json(
    prompt: str,
    provider_id: str | None = None,
) -> tuple[dict[str, Any], ModelProvider] | None:
    provider = configured_model(provider_id)
    if not provider:
        return None
    if provider.family == "anthropic":
        payload = _anthropic(provider, prompt)
    elif provider.family == "gemini":
        payload = _gemini(provider, prompt)
    else:
        payload = _openai_compatible(provider, prompt)
    parsed = _parse_json(payload)
    return (parsed, provider) if parsed else None


def _openai_compatible(provider: ModelProvider, prompt: str) -> str:
    headers, payload = _openai_compatible_request(provider, prompt)
    payload["response_format"] = {"type": "json_object"}
    response = httpx.post(
        f"{provider.base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return str(response.json()["choices"][0]["message"]["content"] or "")


def _openai_compatible_request(
    provider: ModelProvider, prompt: str
) -> tuple[dict[str, str], dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if provider.id == "kimi":
        payload.update(
            {
                "thinking": {"type": "disabled"},
                "max_tokens": 2048,
            }
        )
    elif provider.id == "glm":
        if settings.glm_reasoning_effort:
            payload["reasoning"] = {"effort": settings.glm_reasoning_effort}
    else:
        payload["temperature"] = 0
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    if provider.id == "glm":
        headers.update(
            {
                "HTTP-Referer": settings.frontend_url,
                "X-OpenRouter-Title": "MemoryWorks",
            }
        )
    return headers, payload


def stream_text(
    prompt: str,
    provider_id: str | None = None,
    *,
    on_text: Callable[[str], None],
    on_reasoning: Callable[[str], None] | None = None,
    deadline_seconds: float = 90.0,
) -> tuple[str, ModelProvider] | None:
    """Generate plain text, handing each piece to ``on_text`` as it is predicted.

    OpenAI-compatible providers (GLM via OpenRouter among them) stream token by
    token, and a reasoning model's thinking is passed to ``on_reasoning`` as it
    arrives. Other families answer in one piece, delivered as a single chunk, so
    callers never need a second code path. Returns None when no model is
    configured; raises on transport errors and when the deadline passes, because
    the caller has already shown part of an answer and must know it is incomplete.
    """
    provider = configured_model(provider_id)
    if not provider:
        return None
    if provider.family == "anthropic":
        text = _anthropic(provider, prompt)
        on_text(text)
        return text, provider
    if provider.family == "gemini":
        text = _gemini(provider, prompt)
        on_text(text)
        return text, provider

    headers, payload = _openai_compatible_request(provider, prompt)
    payload["stream"] = True
    deadline = time.monotonic() + deadline_seconds
    pieces: list[str] = []
    with httpx.stream(
        "POST",
        f"{provider.base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(60, connect=10),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise TimeoutError("The model did not finish its answer in time.")
            # Server-sent events: "data: {...}" per chunk, ": comment" keep-alives
            # while the provider is still thinking, and "data: [DONE]" at the end.
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise RuntimeError(str(chunk["error"].get("message") or chunk["error"]))
            choices = chunk.get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices else {}
            thinking = delta.get("reasoning") or delta.get("reasoning_content")
            if thinking and on_reasoning:
                on_reasoning(str(thinking))
            content = delta.get("content")
            if content:
                pieces.append(str(content))
                on_text(str(content))
    return "".join(pieces), provider


def _anthropic(provider: ModelProvider, prompt: str) -> str:
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": provider.model,
            "max_tokens": 1600,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    response.raise_for_status()
    blocks = response.json().get("content", [])
    return "\n".join(
        str(block.get("text") or "") for block in blocks if block.get("type") == "text"
    )


def _gemini(provider: ModelProvider, prompt: str) -> str:
    response = httpx.post(
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{provider.model}:generateContent",
        headers={
            "x-goog-api-key": provider.api_key,
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
            },
        },
        timeout=60,
    )
    response.raise_for_status()
    candidates = response.json().get("candidates", [])
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    return "\n".join(str(part.get("text") or "") for part in parts)


def _parse_json(value: str) -> dict[str, Any] | None:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
