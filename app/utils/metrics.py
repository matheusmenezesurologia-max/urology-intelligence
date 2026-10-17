"""Prometheus metrics registry and helpers.

These metrics are exposed at ``/metrics`` by the FastAPI app and scraped by
Prometheus (see monitoring/prometheus/prometheus.yml).
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

# --- Pipeline-level ---------------------------------------------------------
PIPELINE_RUNS = Counter(
    "ui_pipeline_runs_total", "Weekly pipeline runs", ["status"]
)
PIPELINE_DURATION = Histogram(
    "ui_pipeline_duration_seconds",
    "End-to-end pipeline duration",
    buckets=(30, 60, 120, 300, 600, 1200, 1800, 3600),
)
PIPELINE_LAST_SUCCESS = Gauge(
    "ui_pipeline_last_success_timestamp", "Unix ts of last successful pipeline run"
)

# --- Agent-level ------------------------------------------------------------
AGENT_RUNS = Counter(
    "ui_agent_runs_total", "Agent executions", ["agent", "status"]
)
AGENT_DURATION = Histogram(
    "ui_agent_duration_seconds",
    "Per-agent execution duration",
    ["agent"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
)

# --- Domain counters --------------------------------------------------------
ARTICLES_FETCHED = Counter("ui_articles_fetched_total", "Articles fetched", ["source"])
ARTICLES_SELECTED = Counter("ui_articles_selected_total", "Articles selected after triage")
PDFS_DOWNLOADED = Counter("ui_pdfs_downloaded_total", "PDFs downloaded", ["source"])
GUIDELINE_UPDATES = Counter("ui_guideline_updates_total", "Guideline updates detected", ["society"])
NEWSLETTERS_SENT = Counter("ui_newsletters_sent_total", "Newsletters dispatched", ["channel", "status"])

# --- External dependencies --------------------------------------------------
EXTERNAL_REQUESTS = Counter(
    "ui_external_requests_total", "Outbound API requests", ["service", "status"]
)
LLM_TOKENS = Counter("ui_llm_tokens_total", "LLM tokens consumed", ["provider", "kind"])
CIRCUIT_STATE = Gauge(
    "ui_circuit_breaker_state", "Circuit breaker state (0 closed, 1 open, 2 half)", ["name"]
)


@contextmanager
def track_agent(agent: str):
    """Context manager timing an agent and recording success/failure counters."""
    start = time.perf_counter()
    try:
        yield
    except Exception:
        AGENT_RUNS.labels(agent=agent, status="error").inc()
        raise
    else:
        AGENT_RUNS.labels(agent=agent, status="success").inc()
    finally:
        AGENT_DURATION.labels(agent=agent).observe(time.perf_counter() - start)
