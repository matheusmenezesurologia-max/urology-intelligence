"""Tests for utility modules: chunker, circuit breaker, llm helpers, pdf parser."""
from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from app.rag.chunker import chunk_text
from app.utils import llm
from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError, State


def test_chunk_text_basic():
    text = "\n\n".join(f"Paragraph number {i}. " * 20 for i in range(6))
    chunks = chunk_text(text, target_chars=400, overlap_chars=50)
    assert len(chunks) > 1
    assert all(c.text for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_splits_long_paragraph():
    long_para = " ".join(f"Sentence {i} about andrology." for i in range(200))
    chunks = chunk_text(long_para, target_chars=300)
    assert len(chunks) > 1


def test_circuit_breaker_opens_and_recovers():
    cb = CircuitBreaker("svc", failure_threshold=2, recovery_timeout=0.0)

    def boom():
        raise ValueError("fail")

    with pytest.raises(ValueError):
        cb.call(boom)
    with pytest.raises(ValueError):
        cb.call(boom)
    assert cb.state == State.OPEN

    # recovery_timeout=0 -> immediately half-open, allowing a trial call
    assert cb.call(lambda: "ok") == "ok"
    assert cb.state == State.CLOSED


def test_circuit_breaker_blocks_when_open():
    cb = CircuitBreaker("svc2", failure_threshold=1, recovery_timeout=1000.0)
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert cb.state == State.OPEN
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "never runs")


def test_circuit_breaker_decorator():
    cb = CircuitBreaker("svc3", failure_threshold=5)

    @cb
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


@pytest.mark.parametrize(
    "model,provider",
    [
        ("gpt-4o-mini", "openai"),
        ("o1-preview", "openai"),
        ("claude-3-5-sonnet-20241022", "anthropic"),
        ("text-embedding-3-large", "openai"),
        ("unknown-model", "anthropic"),
    ],
)
def test_provider_routing(model, provider):
    assert llm._provider_for(model) == provider


def test_extract_json_variants():
    assert llm._extract_json('{"a": 1}') == '{"a": 1}'
    assert llm._extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert llm._extract_json('Here it is: {"a": 1} done').strip().startswith("{")


def test_extract_json_failure():
    with pytest.raises(llm.LLMError):
        llm._extract_json("no json here")


def test_complete_json_parses(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"relevance_score": 80}')
    out = llm.complete_json("sys", "user")
    assert out["relevance_score"] == 80


def test_pdf_parser_roundtrip(tmp_path):
    from app.utils.pdf_parser import parse_pdf

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction\nThis study evaluates micro-TESE outcomes.")
    doc.save(str(pdf_path))
    doc.close()

    parsed = parse_pdf(pdf_path)
    assert parsed.page_count == 1
    assert "micro-TESE" in parsed.text
    assert parsed.char_count > 0


def test_pdf_parser_missing_file(tmp_path):
    from app.utils.pdf_parser import parse_pdf

    with pytest.raises(FileNotFoundError):
        parse_pdf(tmp_path / "nope.pdf")


def test_complete_routes_by_model(monkeypatch):
    monkeypatch.setattr(llm, "_openai_complete", lambda *a, **k: "OPENAI")
    monkeypatch.setattr(llm, "_anthropic_complete", lambda *a, **k: "ANTHROPIC")
    assert llm.complete("s", "u", model="gpt-4o-mini") == "OPENAI"
    assert llm.complete("s", "u", model="claude-3-5-sonnet-20241022") == "ANTHROPIC"


def test_embed_texts(monkeypatch):
    from app.rag import embeddings

    monkeypatch.setattr(embeddings, "_embed_batch", lambda texts: [[1.0, 2.0] for _ in texts])
    vecs = embeddings.embed_texts(["a", "b", "c"])
    assert len(vecs) == 3
    assert embeddings.embed_query("q") == [1.0, 2.0]


def test_guidelines_base_helpers():
    from app.integrations.guidelines_base import _clean_text, _match_topic

    assert _clean_text("<b>Male  infertility</b>") == "Male infertility"
    assert _match_topic("EAU sexual health guideline", ["sexual"]) == "sexual"
    assert _match_topic("random cardiology text", ["sexual", "androl"]) is None
