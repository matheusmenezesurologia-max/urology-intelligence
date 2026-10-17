"""Agent 8 — Guideline vs Evidence Comparator.

For each interpreted article, retrieves the most relevant indexed guideline
passages (RAG) and asks whether the new evidence confirms, contradicts, extends,
or suggests a future change to standing recommendations.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.agents.common import audit, load_prompt
from app.config import settings
from app.database.models import Analysis, Article
from app.database.session import session_scope
from app.rag import vector_store
from app.rag.embeddings import embed_query
from app.utils.llm import LLMError, complete_json
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("guideline_comparator")
AGENT = "guideline_comparator"


def _clamp(value, lo: float = 0.0, hi: float = 10.0) -> float | None:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return None


def _compare_one(item: dict, prompt: str) -> dict:
    query = f"{item['title']} {(item.get('structured') or {}).get('conclusions', '')}"
    guideline_context = "Nenhum trecho de guideline indexado foi encontrado."
    if settings.has_openai:  # RAG retrieval needs OpenAI embeddings
        try:
            hits = vector_store.search(embed_query(query), top_k=4, source_types=["guideline"])
            if hits:
                guideline_context = "\n\n".join(
                    f"- {h.payload.get('title')}: {h.payload.get('text', '')[:600]}" for h in hits
                )
        except Exception as exc:  # vector store / embedding hiccup -> proceed without context
            log.warning("guideline_retrieval_failed", article_id=item["id"], error=str(exc))

    user = (
        "Study analysis:\n"
        + json.dumps({"title": item["title"], "analysis": item.get("structured") or {}}, ensure_ascii=False, indent=2)
        + "\n\nRelevant current guideline excerpts:\n"
        + guideline_context
    )
    try:
        result = complete_json(prompt, user, model=settings.analysis_model, max_tokens=700)
    except LLMError as exc:
        log.warning("comparison_failed", article_id=item["id"], error=str(exc))
        result = {}
    result["id"] = item["id"]
    return result


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    prompt = load_prompt("guideline_comparison")
    ids = state.get("translated_ids", []) or state.get("analyzed_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            rows = session.execute(
                select(Article.id, Article.title, Analysis.structured)
                .join(Analysis, Analysis.article_id == Article.id)
                .where(Article.id.in_(ids))
            ).all() if ids else []
            items = [{"id": r.id, "title": r.title, "structured": r.structured} for r in rows]

        results: list[dict] = []
        if items:
            with ThreadPoolExecutor(max_workers=settings.max_concurrent_llm) as pool:
                results = list(pool.map(lambda it: _compare_one(it, prompt), items))

        compared = 0
        with session_scope() as session:
            for res in results:
                article = session.get(Article, res["id"])
                if not article or not article.interpretation:
                    continue
                interp = article.interpretation
                interp.guideline_relationship = str(res.get("relationship") or "")[:64] or None
                interp.guideline_alignment_score = _clamp(res.get("alignment_score"))
                notes = res.get("notes")
                affected = res.get("affected_guidelines")
                if affected:
                    notes = f"{notes}\nGuidelines: {', '.join(affected)}" if notes else f"Guidelines: {', '.join(affected)}"
                interp.guideline_comparison_notes = notes
                compared += 1
            audit(session, run_id, AGENT, "comparison_complete", message=f"{compared} comparisons")

    log.info("guideline_comparator_done", compared=compared)
    return {"stats": {"guideline_comparisons": compared}}
