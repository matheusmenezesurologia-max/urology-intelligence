"""Embedding generation via OpenAI text-embedding-3-large."""
from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.utils.logger import get_logger
from app.utils.metrics import LLM_TOKENS
from app.utils.retry import llm_retry

log = get_logger("embeddings")

# Conservative batch size; the API accepts more but this keeps payloads small.
BATCH_SIZE = 96


@lru_cache(maxsize=1)
def _client():  # pragma: no cover - requires live OpenAI client
    from openai import OpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY required for embeddings")
    return OpenAI(api_key=settings.openai_api_key, timeout=settings.http_timeout_seconds)


@llm_retry
def _embed_batch(texts: list[str]) -> list[list[float]]:  # pragma: no cover - network
    resp = _client().embeddings.create(model=settings.embedding_model, input=texts)
    if resp.usage:
        LLM_TOKENS.labels(provider="openai", kind="embedding").inc(resp.usage.total_tokens)
    # API preserves input order.
    return [item.embedding for item in resp.data]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts, batching transparently."""
    cleaned = [(t or " ").replace("\n", " ").strip()[:8000] or " " for t in texts]
    vectors: list[list[float]] = []
    for i in range(0, len(cleaned), BATCH_SIZE):
        batch = cleaned[i : i + BATCH_SIZE]
        vectors.extend(_embed_batch(batch))
    log.info("embedded", count=len(vectors), dim=settings.embedding_dim)
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]
