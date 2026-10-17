"""Shared LangGraph pipeline state (TypedDict)."""
from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _merge_dict(left: dict, right: dict) -> dict:
    """Reducer: shallow-merge stat dictionaries returned by nodes."""
    out = dict(left or {})
    out.update(right or {})
    return out


def _extend_list(left: list, right: list) -> list:
    """Reducer: concatenate lists (e.g. accumulated errors)."""
    return (left or []) + (right or [])


class PipelineState(TypedDict, total=False):
    """State threaded through the weekly LangGraph pipeline.

    Heavy data lives in PostgreSQL/Qdrant; the state carries identifiers,
    lightweight summaries, and accumulated stats so the graph stays serializable.
    """

    run_id: str
    started_at: str
    lookback_days: int
    top_n: int

    # Article identifiers as they move through the funnel.
    article_ids: list[int]          # all newly persisted this run
    selected_ids: list[int]         # top-N after triage
    analyzed_ids: list[int]         # full-text analyzed
    appraised_ids: list[int]
    translated_ids: list[int]

    # Outputs from non-article agents.
    guideline_updates: list[dict[str, Any]]
    research_opportunities: list[dict[str, Any]]

    # Newsletter artifacts.
    newsletter_id: int
    newsletter_paths: dict[str, str]
    executive_summary: str
    dispatched: bool

    # Bookkeeping (reduced/accumulated).
    stats: Annotated[dict[str, Any], _merge_dict]
    errors: Annotated[list[str], _extend_list]
