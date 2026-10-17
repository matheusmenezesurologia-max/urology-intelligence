"""Agent 6 — Clinical Practice Translator.

Turns each appraised study into concrete bedside guidance: does it change
practice, what to do differently, for whom it applies (and for whom not),
implementation barriers, and confidence.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.agents.common import audit, load_prompt
from app.config import settings
from app.database.models import Analysis, Article, ClinicalInterpretation
from app.database.session import session_scope
from app.utils.llm import LLMError, complete_json
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("clinical_practice_translator")
AGENT = "clinical_practice_translator"


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "sim", "1"}
    return None


def _translate_one(item: dict, prompt: str) -> dict:
    payload = {
        "title": item["title"],
        "analysis": item.get("structured") or {},
        "appraisal": {
            "clinical_impact_score": item.get("clinical_impact_score"),
            "evidence_quality_score": item.get("evidence_quality_score"),
            "practice_change_probability": item.get("practice_change_probability"),
            "risk_of_bias": item.get("risk_of_bias"),
            "appraisal_notes": item.get("appraisal_notes"),
        },
    }
    user = "Study dossier:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        result = complete_json(prompt, user, model=settings.analysis_model, max_tokens=1000)
    except LLMError as exc:
        log.warning("translation_failed", article_id=item["id"], error=str(exc))
        result = {}
    result["id"] = item["id"]
    return result


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    prompt = load_prompt("clinical_translation")
    appraised_ids = state.get("appraised_ids", []) or state.get("analyzed_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            rows = session.scalars(
                select(Article).where(Article.id.in_(appraised_ids))
            ).all() if appraised_ids else []
            items = []
            for a in rows:
                an: Analysis | None = a.analysis
                items.append({
                    "id": a.id,
                    "title": a.title,
                    "structured": an.structured if an else None,
                    "clinical_impact_score": an.clinical_impact_score if an else None,
                    "evidence_quality_score": an.evidence_quality_score if an else None,
                    "practice_change_probability": an.practice_change_probability if an else None,
                    "risk_of_bias": an.risk_of_bias if an else None,
                    "appraisal_notes": an.appraisal_notes if an else None,
                })

        results: list[dict] = []
        if items:
            with ThreadPoolExecutor(max_workers=settings.max_concurrent_llm) as pool:
                results = list(pool.map(lambda it: _translate_one(it, prompt), items))

        translated_ids: list[int] = []
        with session_scope() as session:
            for res in results:
                article = session.get(Article, res["id"])
                if not article:
                    continue
                interp = article.interpretation or ClinicalInterpretation(article_id=article.id)
                interp.changes_practice = _bool(res.get("changes_practice"))
                interp.what_to_do_differently = res.get("what_to_do_differently")
                interp.applies_to_patients = res.get("applies_to_patients")
                interp.not_applies_to_patients = res.get("not_applies_to_patients")
                interp.implementation_barriers = res.get("implementation_barriers")
                interp.confidence_level = str(res.get("confidence_level") or "")[:32] or None
                if article.interpretation is None:
                    session.add(interp)
                translated_ids.append(article.id)
            audit(session, run_id, AGENT, "translation_complete",
                  message=f"{len(translated_ids)} interpretations")

    log.info("clinical_practice_translator_done", translated=len(translated_ids))
    return {"translated_ids": translated_ids, "stats": {"translated": len(translated_ids)}}
