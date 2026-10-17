"""Tests for the LangGraph weekly pipeline: resilience wrapper and orchestration."""
from __future__ import annotations

from app.workflows import weekly_pipeline as wp


def test_build_graph_compiles():
    graph = wp.build_graph()
    assert graph is not None
    assert wp.WEEKLY_GRAPH is not None


def test_resilient_success():
    wrapped = wp._resilient("ok", lambda s: {"stats": {"a": 1}}, timeout=5, retries=1)
    out = wrapped({"run_id": "r"})
    assert out == {"stats": {"a": 1}}


def test_resilient_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(wp.time, "sleep", lambda *_: None)  # no backoff delay in test
    calls = {"n": 0}

    def flaky(state):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return {"stats": {"recovered": True}}

    wrapped = wp._resilient("flaky", flaky, timeout=5, retries=3)
    out = wrapped({"run_id": "r"})
    assert out["stats"]["recovered"] is True
    assert calls["n"] == 2


def test_resilient_captures_error(monkeypatch):
    monkeypatch.setattr(wp.time, "sleep", lambda *_: None)

    def boom(state):
        raise ValueError("kaboom")

    wrapped = wp._resilient("boom", boom, timeout=5, retries=2)
    out = wrapped({"run_id": "r"})
    assert "errors" in out
    assert any("kaboom" in e for e in out["errors"])


def test_resilient_timeout():
    import time as _t

    wrapped = wp._resilient("slow", lambda s: _t.sleep(0.3) or {}, timeout=0, retries=1)
    out = wrapped({"run_id": "r"})
    assert "errors" in out
    assert any("timeout" in e for e in out["errors"])


def test_run_pipeline_bookkeeping(monkeypatch):
    class FakeGraph:
        def invoke(self, initial, config=None):
            return {**initial, "stats": {"new_articles": 2}, "errors": []}

    monkeypatch.setattr(wp, "WEEKLY_GRAPH", FakeGraph())
    state = wp.run_pipeline(run_id="testrun1")
    assert state["run_id"] == "testrun1"
    assert state["stats"]["new_articles"] == 2
