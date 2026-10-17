"""Tests for the RAG subsystem: vector-store ids, indexing, and Q&A."""
from __future__ import annotations

from sqlalchemy import select

from app.database.models import Embedding
from app.rag import retriever, vector_store


def test_make_point_id_deterministic():
    a = vector_store.make_point_id("article", 1, 0)
    b = vector_store.make_point_id("article", 1, 0)
    c = vector_store.make_point_id("article", 1, 1)
    assert a == b
    assert a != c
    assert len(a) == 36  # uuid string


def test_index_text(monkeypatch, session):
    captured = {}

    def fake_embed(texts):
        captured["n"] = len(texts)
        return [[0.0, 0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever, "embed_texts", fake_embed)
    monkeypatch.setattr(retriever.vector_store, "ensure_collection", lambda *a, **k: "urology_knowledge")
    monkeypatch.setattr(retriever.vector_store, "upsert", lambda points, collection=None: len(points))

    text = "\n\n".join("Andrology paragraph. " * 30 for _ in range(4))
    count = retriever.index_text(session, source_type="article", source_id=7,
                                 title="T", text=text, article_id=7)
    session.commit()
    assert count > 0
    rows = session.scalars(select(Embedding).where(Embedding.article_id == 7)).all()
    assert len(rows) == count


def test_answer_question(monkeypatch):
    class Hit:
        def __init__(self):
            self.id = "1"
            self.score = 0.9
            self.payload = {"title": "TRT RCT", "text": "evidence", "source_type": "article", "article_id": 3}

    monkeypatch.setattr(retriever, "embed_query", lambda q: [0.1, 0.2, 0.3])
    monkeypatch.setattr(retriever.vector_store, "search", lambda *a, **k: [Hit()])
    monkeypatch.setattr(retriever, "complete", lambda *a, **k: "Resposta com [1].")

    ans = retriever.answer_question("Evidências de TRT?")
    assert "[1]" in ans.answer
    assert ans.sources[0]["title"] == "TRT RCT"


def test_answer_question_no_hits(monkeypatch):
    monkeypatch.setattr(retriever, "embed_query", lambda q: [0.1])
    monkeypatch.setattr(retriever.vector_store, "search", lambda *a, **k: [])
    ans = retriever.answer_question("pergunta")
    assert ans.sources == []
