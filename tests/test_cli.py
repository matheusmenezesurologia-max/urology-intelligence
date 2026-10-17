"""Tests for the command-line interface."""
from __future__ import annotations

from app import cli


def test_build_parser():
    parser = cli.build_parser()
    args = parser.parse_args(["ask", "question text", "--top-k", "3"])
    assert args.command == "ask"
    assert args.question == "question text"
    assert args.top_k == 3


def test_cmd_init_db():
    assert cli.cmd_init_db(object()) == 0


def test_cmd_run(monkeypatch, capsys):
    from app.workflows import weekly_pipeline

    monkeypatch.setattr(weekly_pipeline, "run_pipeline",
                        lambda *a, **k: {"run_id": "x", "stats": {"n": 1}, "errors": []})
    assert cli.main(["run"]) == 0
    out = capsys.readouterr().out
    assert "run_id" in out


def test_cmd_ask(monkeypatch, capsys):
    from app.rag import retriever

    monkeypatch.setattr(
        retriever, "answer_question",
        lambda q, **k: retriever.RagAnswer(question=q, answer="A", sources=[{"n": 1, "title": "T", "source_type": "article", "score": 0.5}]),
    )
    assert cli.main(["ask", "pergunta"]) == 0
    out = capsys.readouterr().out
    assert "Fontes:" in out
