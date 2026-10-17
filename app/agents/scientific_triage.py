"""Agent 2 — Scientific Triage.

Scores each newly fetched article (0-100) from title + abstract using an LLM,
applies clinical-priority and learned topic weights, and selects the Top-N.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.agents.common import audit, load_prompt
from app.config import PRIORITY_WEIGHTS, settings
from app.database.models import Article, TopicWeight
from app.database.session import session_scope
from app.utils.llm import LLMError, complete_json
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import ARTICLES_SELECTED, track_agent
from app.workflows.state import PipelineState

log = get_logger("scientific_triage")
AGENT = "scientific_triage"


def _priority_multiplier(text: str, priority_area: str) -> float:
    blob = f"{priority_area} {text}".lower()
    best = 0.5  # default for unrelated topics
    for keyword, weight in PRIORITY_WEIGHTS.items():
        if keyword in blob:
            best = max(best, weight)
    return best


def _triage_one(item: dict, prompt: str) -> dict:
    user = (
        f"Title: {item['title']}\n"
        f"Journal: {item.get('journal') or 'n/a'}\n"
        f"Abstract: {item.get('abstract') or '(no abstract available)'}"
    )
    try:
        result = complete_json(prompt, user, model=settings.triage_model, max_tokens=500)
    except LLMError as exc:
        log.warning("triage_failed", article_id=item["id"], error=str(exc))
        result = {"relevance_score": 0, "study_design": "other", "priority_area": "other", "rationale": "triage error"}
    result["id"] = item["id"]
    return result


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    top_n = state.get("top_n", settings.top_n_articles)
    bind_run(run_id, AGENT)
    prompt = load_prompt("triage")

    with track_agent(AGENT):
        # 1) Read article fields (main thread).
        article_ids = state.get("article_ids", [])
        with session_scope() as session:
            rows = session.execute(
                select(Article.id, Article.title, Article.abstract, Article.journal_id)
                .where(Article.id.in_(article_ids))
            ).all() if article_ids else []
            items = [{"id": r.id, "title": r.title, "abstract": r.abstract} for r in rows]
            # attach journal name
            id_to_journal = {}
            for a in session.scalars(select(Article).where(Article.id.in_(article_ids))):
                id_to_journal[a.id] = a.journal.name if a.journal else None
            for it in items:
                it["journal"] = id_to_journal.get(it["id"])
            topic_weights = {tw.topic: tw.weight for tw in session.scalars(select(TopicWeight))}

        if not items:
            log.info("triage_no_articles")
            return {"selected_ids": [], "stats": {"triaged": 0, "selected": 0}}

        # 2) Score concurrently (pure LLM, no DB).
        with ThreadPoolExecutor(max_workers=settings.max_concurrent_llm) as pool:
            scored = list(pool.map(lambda it: _triage_one(it, prompt), items))

        # 3) Persist scores, compute adjusted ranking, select Top-N (main thread).
        with session_scope() as session:
            ranking: list[tuple[int, float]] = []
            for res in scored:
                article = session.get(Article, res["id"])
                if article is None:
                    continue
                base = float(res.get("relevance_score") or 0)
                priority_area = str(res.get("priority_area") or "other")
                multiplier = _priority_multiplier(f"{article.title} {article.abstract or ''}", priority_area)
                topic_w = topic_weights.get(priority_area, 1.0)
                adjusted = base * multiplier * topic_w

                article.relevance_score = round(adjusted, 2)
                article.study_design = str(res.get("study_design") or "")[:128]
                article.triage_rationale = res.get("rationale")
                ranking.append((article.id, adjusted))

            ranking.sort(key=lambda x: x[1], reverse=True)
            threshold = settings.triage_relevance_threshold
            eligible = [aid for aid, score in ranking if score >= threshold] or [aid for aid, _ in ranking]
            selected_ids = eligible[:top_n]

            for aid in selected_ids:
                article = session.get(Article, aid)
                if article:
                    article.is_selected = True

            audit(
                session,
                run_id,
                AGENT,
                "triage_complete",
                message=f"selected {len(selected_ids)} of {len(ranking)}",
                payload={"selected_ids": selected_ids, "threshold": threshold},
            )

    ARTICLES_SELECTED.inc(len(selected_ids))
    log.info("triage_done", triaged=len(scored), selected=len(selected_ids))
    return {"selected_ids": selected_ids, "stats": {"triaged": len(scored), "selected": len(selected_ids)}}
