"""Tests for the APScheduler integration."""
from __future__ import annotations

from app import scheduler as sched


def test_start_and_shutdown_scheduler(monkeypatch):
    # Make the job harmless and ensure the scheduler registers/starts/stops.
    monkeypatch.setattr(sched, "run_pipeline", lambda *a, **k: None)
    try:
        s = sched.start_scheduler()
        job = s.get_job(sched.JOB_ID)
        assert job is not None
        assert job.next_run_time is not None
        assert s.running is True
    finally:
        sched.shutdown_scheduler()
    assert sched._scheduler is None


def test_job_runs_pipeline(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(sched, "run_pipeline", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    sched._job()
    assert called["n"] == 1


def test_job_survives_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("fail")

    monkeypatch.setattr(sched, "run_pipeline", boom)
    # Should swallow the exception, not raise.
    sched._job()
