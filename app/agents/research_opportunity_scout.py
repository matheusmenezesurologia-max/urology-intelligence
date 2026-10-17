"""Agent 9 — Research Opportunity Scout.

Synthesizes the week's analyzed evidence to surface knowledge gaps, conflicting
findings, and topics lacking RCTs, then proposes concrete research themes
(systematic review, meta-analysis, dissertation, master's, doctorate).
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app.agents.common import audit
from app.config import settings
from app.database.models import Analysis, Article, ClinicalInterpretation
from app.database.session import session_scope
from app.utils.llm import LLMError, complete_json
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("research_opportunity_scout")
AGENT = "research_opportunity_scout"

SYSTEM = """You are a urology/andrology research strategist advising a clinician-scientist
focused on andrology, sexual medicine, male infertility and reproduction. Given a
digest of this week's analyzed studies, identify research opportunities.

Return STRICT JSON with exactly these keys:
{
  "knowledge_gaps": ["..."],
  "conflicting_results": ["..."],
  "topics_without_rcts": ["..."],
  "opportunities": {
     "systematic_review": ["..."],
     "meta_analysis": ["..."],
     "dissertation": ["..."],
     "masters": ["..."],
     "doctorate": ["..."]
  }
}
Be specific and feasible; prioritize andrology and male reproduction. Base every
item on the provided digest. Output ONLY the JSON object."""


def _build_digest(items: list[dict]) -> str:
    lines = []
    for it in items:
        struct = it.get("structured") or {}
        lines.append(json.dumps({
            "title": it["title"],
            "design": it.get("study_design"),
            "sample_size": struct.get("sample_size"),
            "primary_outcome": struct.get("primary_outcome"),
            "conclusions": struct.get("conclusions"),
            "limitations": struct.get("limitations"),
            "guideline_relationship": it.get("guideline_relationship"),
        }, ensure_ascii=False))
    return "\n".join(lines)


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    ids = state.get("analyzed_ids", []) or state.get("selected_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            rows = session.execute(
                select(
                    Article.id, Article.title, Article.study_design,
                    Analysis.structured, ClinicalInterpretation.guideline_relationship,
                )
                .join(Analysis, Analysis.article_id == Article.id)
                .outerjoin(ClinicalInterpretation, ClinicalInterpretation.article_id == Article.id)
                .where(Article.id.in_(ids))
            ).all() if ids else []
            items = [
                {
                    "id": r.id, "title": r.title, "study_design": r.study_design,
                    "structured": r.structured, "guideline_relationship": r.guideline_relationship,
                }
                for r in rows
            ]

        opportunities: dict = {}
        if items:
            try:
                opportunities = complete_json(
                    SYSTEM,
                    "This week's analyzed studies:\n" + _build_digest(items),
                    model=settings.analysis_model,
                    max_tokens=1500,
                )
            except LLMError as exc:
                log.warning("opportunity_scan_failed", error=str(exc))
                opportunities = {}

        with session_scope() as session:
            audit(session, run_id, AGENT, "opportunities_complete",
                  message=f"{len(items)} studies scanned", payload=opportunities or None)

    log.info("research_opportunity_scout_done", studies=len(items))
    return {
        "research_opportunities": [opportunities] if opportunities else [],
        "stats": {"opportunity_studies": len(items)},
    }
