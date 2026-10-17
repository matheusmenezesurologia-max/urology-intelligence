"""High-level RAG operations: indexing and question answering."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import Embedding
from app.rag import vector_store
from app.rag.chunker import chunk_text
from app.rag.embeddings import embed_query, embed_texts
from app.utils.llm import complete
from app.utils.logger import get_logger

log = get_logger("retriever")

RAG_SYSTEM_PROMPT = (
    "You are a urology and andrology scientific assistant. Answer the clinician's "
    "question using ONLY the provided context passages. Cite sources inline as "
    "[n] matching the passage numbers. If the context is insufficient, say so "
    "explicitly. Be precise, evidence-focused, and concise. Reply in Portuguese (pt-BR)."
)


@dataclass
class RagAnswer:
    question: str
    answer: str
    sources: list[dict[str, Any]]


def index_text(
    session: Session,
    *,
    source_type: str,
    source_id: str | int,
    title: str,
    text: str,
    topic: Optional[str] = None,
    article_id: Optional[int] = None,
) -> int:
    """Chunk, embed, and upsert ``text`` into Qdrant; record bookkeeping rows."""
    # Embeddings require OpenAI; skip RAG indexing cleanly when it is not configured
    # (e.g. Claude-only setups) instead of erroring per chunk.
    if not settings.has_openai:
        log.debug("rag_indexing_skipped_no_openai", source_type=source_type, source_id=str(source_id))
        return 0
    chunks = chunk_text(text)
    if not chunks:
        return 0

    vectors = embed_texts([c.text for c in chunks])
    collection = vector_store.ensure_collection()

    points: list[tuple[str, list[float], dict[str, Any]]] = []
    rows: list[Embedding] = []
    for chunk, vector in zip(chunks, vectors):
        point_id = vector_store.make_point_id(source_type, source_id, chunk.index)
        payload = {
            "source_type": source_type,
            "source_id": str(source_id),
            "article_id": article_id,
            "title": title,
            "topic": topic,
            "chunk_index": chunk.index,
            "text": chunk.text,
        }
        points.append((point_id, vector, payload))
        rows.append(
            Embedding(
                article_id=article_id,
                point_id=point_id,
                collection=collection,
                chunk_index=chunk.index,
                chunk_text=chunk.text,
                source_type=source_type,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
            )
        )

    # Idempotency: drop prior bookkeeping rows for this article before reinserting.
    if article_id is not None:
        session.execute(
            delete(Embedding).where(
                Embedding.article_id == article_id, Embedding.source_type == source_type
            )
        )
    vector_store.upsert(points, collection)
    session.add_all(rows)
    session.flush()
    log.info("indexed_text", source_type=source_type, source_id=str(source_id), chunks=len(chunks))
    return len(chunks)


def answer_question(
    question: str,
    *,
    top_k: int = 8,
    source_types: Optional[list[str]] = None,
) -> RagAnswer:
    """Retrieve relevant passages and synthesize a cited answer."""
    if not settings.has_openai:
        return RagAnswer(
            question=question,
            answer="A busca semântica (RAG) está indisponível: requer embeddings da OpenAI, "
                   "que não estão configurados neste ambiente (modo somente-Claude).",
            sources=[],
        )
    query_vec = embed_query(question)
    hits = vector_store.search(query_vec, top_k=top_k, source_types=source_types)

    if not hits:
        return RagAnswer(question=question, answer="Não há contexto indexado suficiente para responder.", sources=[])

    context_blocks = []
    sources = []
    for i, hit in enumerate(hits, start=1):
        payload = hit.payload
        title = payload.get("title", "(sem título)")
        text = payload.get("text", "")
        context_blocks.append(f"[{i}] ({payload.get('source_type')}) {title}\n{text}")
        sources.append(
            {
                "n": i,
                "title": title,
                "source_type": payload.get("source_type"),
                "article_id": payload.get("article_id"),
                "score": round(hit.score, 4),
            }
        )

    user_prompt = (
        f"Pergunta clínica:\n{question}\n\n"
        f"Passagens de contexto:\n\n" + "\n\n---\n\n".join(context_blocks)
    )
    answer = complete(
        RAG_SYSTEM_PROMPT,
        user_prompt,
        model=settings.analysis_model,
        temperature=0.1,
        max_tokens=1200,
    )
    log.info("rag_answered", question=question[:80], hits=len(hits))
    return RagAnswer(question=question, answer=answer, sources=sources)
