"""Weekly LangGraph pipeline.

Wires the twelve agents into a linear StateGraph:

    START -> journal_scout -> scientific_triage -> pdf_collector
          -> full_text_analyzer -> evidence_appraiser
          -> clinical_practice_translator -> guideline_tracker
          -> guideline_comparator -> research_opportunity_scout
          -> personalized_learning -> newsletter_editor
          -> newsletter_dispatcher -> END

Each node is wrapped with per-node timeout, bounded retries with backoff, and
exception capture so that a single failing agent degrades gracefully instead of
aborting the whole run.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Callable

from langgraph.graph import END, START, StateGraph

from app.agents import (
    clinical_practice_translator,
    evidence_appraiser,
    full_text_analyzer,
    guideline_comparator,
    guideline_tracker,
    journal_scout,
    newsletter_dispatcher,
    newsletter_editor,
    pdf_collector,
    personalized_learning,
    research_opportunity_scout,
    scientific_triage,
)
from app.agents.common import audit
from app.config import settings
from app.database.session import session_scope
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import PIPELINE_DURATION, PIPELINE_LAST_SUCCESS, PIPELINE_RUNS
from app.workflows.state import PipelineState

log = get_logger("weekly_pipeline")

# (name, node_callable, timeout_seconds, max_retries)
NODES: list[tuple[str, Callable[[PipelineState], dict], int, int]] = [
    ("journal_scout", journal_scout.node, 900, 2),
    ("scientific_triage", scientific_triage.node, 900, 2),
    ("pdf_collector", pdf_collector.node, 900, 1),
    ("full_text_analyzer", full_text_analyzer.node, 1200, 1),
    ("evidence_appraiser", evidence_appraiser.node, 900, 1),
    ("clinical_practice_translator", clinical_practice_translator.node, 900, 1),
    ("guideline_tracker", guideline_tracker.node, 900, 2),
    ("guideline_comparator", guideline_comparator.node, 900, 1),
    ("research_opportunity_scout", research_opportunity_scout.node, 600, 1),
    ("personalized_learning", personalized_learning.node, 300, 1),
    ("newsletter_editor", newsletter_editor.node, 900, 1),
    ("newsletter_dispatcher", newsletter_dispatcher.node, 300, 2),
]


def _resilient(name: str, fn: Callable[[PipelineState], dict], timeout: int, retries: int) -> Callable[[PipelineState], dict]:
    def wrapped(state: PipelineState) -> dict:
        bind_run(state["run_id"], name)
        last_error: str | None = None
        for attempt in range(1, retries + 1):
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(fn, state)
                    result = future.result(timeout=timeout)
                if attempt > 1:
                    log.info("node_recovered", node=name, attempt=attempt)
                return result or {}
            except FuturesTimeout:
                last_error = f"timeout after {timeout}s"
                log.error("node_timeout", node=name, attempt=attempt, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - capture & continue
                last_error = str(exc)
                log.error("node_error", node=name, attempt=attempt, error=last_error)
            if attempt < retries:
                time.sleep(min(2 ** attempt, 15))

        # All attempts failed: record and continue the pipeline.
        try:
            with session_scope() as session:
                audit(session, state.get("run_id"), name, "node_failed", message=last_error, level="ERROR")
        except Exception:  # pragma: no cover - audit must never raise
            pass
        return {"errors": [f"{name}: {last_error}"]}

    return wrapped


def build_graph():
    """Construct and compile the weekly pipeline StateGraph."""
    graph = StateGraph(PipelineState)

    for name, fn, timeout, retries in NODES:
        graph.add_node(name, _resilient(name, fn, timeout, retries))

    graph.add_edge(START, NODES[0][0])
    for (prev_name, *_), (next_name, *_) in zip(NODES, NODES[1:]):
        graph.add_edge(prev_name, next_name)
    graph.add_edge(NODES[-1][0], END)

    return graph.compile()


# Compile once at import; the graph is stateless and reusable across runs.
WEEKLY_GRAPH = build_graph()


def run_pipeline(run_id: str | None = None) -> PipelineState:
    """Execute one full weekly pipeline run and return the final state."""
    run_id = run_id or uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    bind_run(run_id, "pipeline")

    initial: PipelineState = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "lookback_days": settings.lookback_days,
        "top_n": settings.top_n_articles,
        "article_ids": [],
        "selected_ids": [],
        "stats": {},
        "errors": [],
    }

    log.info("pipeline_start", run_id=run_id)
    with session_scope() as session:
        audit(session, run_id, "pipeline", "pipeline_start", message="weekly run started")

    start_perf = time.perf_counter()
    try:
        final_state: PipelineState = WEEKLY_GRAPH.invoke(initial, config={"recursion_limit": 50})
    except Exception as exc:
        PIPELINE_RUNS.labels(status="error").inc()
        log.error("pipeline_failed", run_id=run_id, error=str(exc))
        with session_scope() as session:
            audit(session, run_id, "pipeline", "pipeline_failed", message=str(exc), level="ERROR")
        raise
    finally:
        PIPELINE_DURATION.observe(time.perf_counter() - start_perf)

    PIPELINE_RUNS.labels(status="success").inc()
    PIPELINE_LAST_SUCCESS.set(time.time())
    log.info("pipeline_complete", run_id=run_id, stats=final_state.get("stats"), errors=final_state.get("errors"))
    with session_scope() as session:
        audit(session, run_id, "pipeline", "pipeline_complete",
              message="weekly run finished", payload={"stats": final_state.get("stats"),
                                                       "errors": final_state.get("errors")})
    return final_state
