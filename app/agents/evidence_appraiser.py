"""Agent 5 — Evidence Appraiser.

Critically appraises each analyzed article and assigns 0-10 scores for clinical
impact, evidence quality, practice-change probability and andrology relevance,
plus a risk-of-bias judgement.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.agents.common import audit, load_prompt
from app.config import settings
from app.database.models import Analysis, Article
from app.database.session import session_scope
from app.utils.llm import LLMError, complete_json
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("evidence_appraiser")
AGENT = "evidence_appraiser"


def _clamp(value, lo: float = 0.0, hi: float = 10.0) -> float | None:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return None


def _appraise_one(item: dict, prompt: str) -> dict:
    payload = {
        "title": item["title"],
        "study_design": item.get("study_design"),
        "analysis": item.get("structured") or {},
    }
    user = "Structured study analysis:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        result = complete_json(prompt, user, model=settings.appraisal_model, max_tokens=900)
    except LLMError as exc:
        log.warning("appraisal_failed", article_id=item["id"], error=str(exc))
        result = {}
    result["id"] = item["id"]
    return result


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    prompt = load_prompt("appraisal")
    analyzed_ids = state.get("analyzed_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            rows = session.execute(
                select(Article.id, Article.title, Article.study_design, Analysis.structured)
                .join(Analysis, Analysis.article_id == Article.id)
                .where(Article.id.in_(analyzed_ids))
            ).all() if analyzed_ids else []
            items = [
                {"id": r.id, "title": r.title, "study_design": r.study_design, "structured": r.structured}
                for r in rows
            ]

        results: list[dict] = []
        if items:
            with ThreadPoolExecutor(max_workers=settings.max_concurrent_llm) as pool:
                results = list(pool.map(lambda it: _appraise_one(it, prompt), items))

        appraised_ids: list[int] = []
        with session_scope() as session:
            for res in results:
                article = session.get(Article, res["id"])
                if not article or not article.analysis:
                    continue
                a = article.analysis
                a.clinical_impact_score = _clamp(res.get("clinical_impact_score"))
                a.evidence_quality_score = _clamp(res.get("evidence_quality_score"))
                a.practice_change_probability = _clamp(res.get("practice_change_probability"))
                a.andrology_relevance_score = _clamp(res.get("andrology_relevance_score"))
                a.risk_of_bias = str(res.get("risk_of_bias") or "")[:32] or None
                a.appraisal_notes = res.get("appraisal_notes")
                appraised_ids.append(article.id)
            audit(session, run_id, AGENT, "appraisal_complete",
                  message=f"{len(appraised_ids)} articles appraised")

    log.info("evidence_appraiser_done", appraised=len(appraised_ids))
    return {"appraised_ids": appraised_ids, "stats": {"appraised": len(appraised_ids)}}
