"""Tests for the FastAPI application endpoints."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.database.models import Newsletter
from app.database.session import session_scope
from app.rag.retriever import RagAnswer


@pytest.fixture
def client(monkeypatch):
    import app.main as main

    # Neutralize background side effects during the app lifespan.
    monkeypatch.setattr(main, "start_scheduler", lambda: None)
    monkeypatch.setattr(main, "shutdown_scheduler", lambda: None)
    monkeypatch.setattr(main.vector_store, "ensure_collection", lambda *a, **k: "c")

    with TestClient(main.app) as c:
        yield c


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "urology-intelligence"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] == "up"
    assert "version" in body


def test_feedback_recorded(client):
    resp = client.post("/feedback", json={"event_type": "liked", "topic": "andrology"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "recorded"


def test_feedback_unknown_article(client):
    resp = client.post("/feedback", json={"event_type": "opened", "article_id": 99999})
    assert resp.status_code == 404


def test_rag_query(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(
        main.retriever, "answer_question",
        lambda q, **k: RagAnswer(question=q, answer="resposta [1]", sources=[{"n": 1, "title": "X"}]),
    )
    resp = client.post("/rag/query", json={"question": "Evidências de micro-TESE?"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "resposta [1]"


def test_trigger_run(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "run_pipeline", lambda run_id=None: None)
    resp = client.post("/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert "run_id" in resp.json()


def test_latest_newsletter(client):
    # No newsletter yet -> 404
    assert client.get("/newsletters/latest").status_code == 404
    with session_scope() as s:
        s.add(Newsletter(run_id="r", issue_date=dt.date.today(), title="Issue",
                         content_markdown="# Hi", executive_summary="resumo"))
    resp = client.get("/newsletters/latest")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Issue"
