from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class SemanticProvider(Protocol):
    model_name: str
    version: int
    semantic: bool

    def embed_text(self, text: str) -> list[float]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class DeterministicProvider:
    """Stable test/degraded provider. It is never reported as semantic search."""

    model_name = "deterministic-hash-96"
    version = 1
    semantic = False

    @staticmethod
    def _embed(text: str) -> list[float]:
        import hashlib
        import re

        vector = [0.0] * 96
        for token in re.findall(r"[A-Za-z0-9_./:@-]+", text.casefold()):
            value = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            vector[value % 96] += -1.0 if value & 1 else 1.0
        magnitude = math.sqrt(sum(item * item for item in vector))
        return [item / magnitude for item in vector] if magnitude else vector

    def embed_text(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]


class FastEmbedProvider:
    version = 1
    semantic = True

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self.model_name = settings.runbook_embedding_model
        self._model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=str(settings.runbook_embedding_cache_dir),
            threads=max(1, settings.runbook_model_threads),
            local_files_only=True,
        )

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_query(self, text: str) -> list[float]:
        return list(next(iter(self._model.query_embed(text))).tolist())

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [
            list(vector.tolist())
            for vector in self._model.passage_embed(
                texts, batch_size=max(1, settings.runbook_embedding_batch_size)
            )
        ]


class OpenAIProvider:
    version = 1
    semantic = True

    def __init__(self) -> None:
        from openai import OpenAI

        self.model_name = settings.runbook_openai_embedding_model
        self._client = OpenAI(api_key=settings.openai_api_key)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model_name, input=texts)
        return [list(item.embedding) for item in response.data]

    def embed_text(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts) if texts else []


@lru_cache(maxsize=1)
def get_semantic_provider() -> SemanticProvider:
    configured = settings.runbook_embedding_provider.casefold()
    if configured == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI embeddings selected but OPENAI_API_KEY is not configured")
        return OpenAIProvider()
    if configured == "fastembed":
        try:
            return FastEmbedProvider()
        except Exception as exc:
            logger.warning(
                "FastEmbed model is not available locally; using deterministic retrieval: %s",
                exc,
            )
            return DeterministicProvider()
    return DeterministicProvider()


class CrossEncoderReranker:
    def __init__(self) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model_name = settings.runbook_reranker_model
        self._model = TextCrossEncoder(
            model_name=self.model_name,
            cache_dir=str(settings.runbook_embedding_cache_dir),
            threads=max(1, settings.runbook_model_threads),
            local_files_only=True,
        )

    def logits(self, query: str, documents: list[str]) -> list[float]:
        """Uncalibrated relevance, comparable across queries for one model."""
        return [
            float(value)
            for value in self._model.rerank(
                query,
                documents,
                batch_size=max(1, min(8, settings.runbook_embedding_batch_size)),
            )
        ]

    def score(self, query: str, documents: list[str]) -> list[float]:
        raw = [
            float(value)
            for value in self._model.rerank(
                query,
                documents,
                batch_size=max(1, min(8, settings.runbook_embedding_batch_size)),
            )
        ]
        if not raw:
            return []
        # Cross-encoder logits are model-specific, not calibrated probabilities.
        # Calibrate against this candidate pack so the score reflects how much
        # more relevant a chunk is than the other semantically retrieved chunks.
        ordered = sorted(raw)
        middle = len(ordered) // 2
        median = (
            ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
        return [
            1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, (value - median) * 1.35)))) for value in raw
        ]


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoderReranker | None:
    if settings.runbook_reranker_provider.casefold() != "fastembed":
        return None
    try:
        return CrossEncoderReranker()
    except Exception as exc:
        logger.warning(
            "FastEmbed reranker is not available locally; continuing without reranking: %s",
            exc,
        )
        return None


def retrieval_runtime() -> dict[str, object]:
    provider = get_semantic_provider()
    reranker = get_reranker()
    return {
        "embedding_provider": settings.runbook_embedding_provider,
        "embedding_model": provider.model_name,
        "semantic": provider.semantic,
        "reranker": reranker.model_name if reranker else "disabled",
        "vector_store": "arcadedb_exact_cosine",
    }
