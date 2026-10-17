"""Qdrant vector store wrapper."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import settings
from app.utils.logger import get_logger

log = get_logger("vector_store")

# Stable namespace so re-indexing the same chunk yields the same point id.
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


def make_point_id(source_type: str, source_id: str | int, chunk_index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{source_type}:{source_id}:{chunk_index}"))


@dataclass
class SearchHit:
    id: str
    score: float
    payload: dict[str, Any]


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:  # pragma: no cover - requires live Qdrant
    api_key = settings.qdrant_api_key or None
    return QdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=settings.http_timeout_seconds)


def ensure_collection(name: str | None = None, dim: int | None = None) -> str:  # pragma: no cover - requires live Qdrant
    """Create the collection if it does not exist; return its name."""
    name = name or settings.qdrant_collection
    dim = dim or settings.embedding_dim
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
        )
        # Payload indexes for fast filtering.
        for field, schema in (
            ("source_type", qmodels.PayloadSchemaType.KEYWORD),
            ("article_id", qmodels.PayloadSchemaType.INTEGER),
            ("topic", qmodels.PayloadSchemaType.KEYWORD),
        ):
            try:
                client.create_payload_index(name, field_name=field, field_schema=schema)
            except Exception:  # pragma: no cover - index may already exist
                pass
        log.info("qdrant_collection_created", name=name, dim=dim)
    return name


def upsert(points: list[tuple[str, list[float], dict[str, Any]]], collection: str | None = None) -> int:  # pragma: no cover - requires live Qdrant
    """Upsert ``(id, vector, payload)`` tuples. Returns the number of points."""
    if not points:
        return 0
    collection = ensure_collection(collection)
    client = get_client()
    client.upsert(
        collection_name=collection,
        points=[
            qmodels.PointStruct(id=pid, vector=vec, payload=payload)
            for pid, vec, payload in points
        ],
    )
    log.info("qdrant_upsert", collection=collection, count=len(points))
    return len(points)


def search(
    vector: list[float],
    top_k: int = 8,
    *,
    collection: str | None = None,
    source_types: list[str] | None = None,
) -> list[SearchHit]:  # pragma: no cover - requires live Qdrant
    collection = ensure_collection(collection)
    client = get_client()
    query_filter = None
    if source_types:
        query_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="source_type", match=qmodels.MatchAny(any=source_types))]
        )
    results = client.search(
        collection_name=collection,
        query_vector=vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    return [SearchHit(id=str(r.id), score=r.score, payload=r.payload or {}) for r in results]


def delete_by_article(article_id: int, collection: str | None = None) -> None:  # pragma: no cover - requires live Qdrant
    collection = ensure_collection(collection)
    client = get_client()
    client.delete(
        collection_name=collection,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="article_id", match=qmodels.MatchValue(value=article_id))]
            )
        ),
    )
