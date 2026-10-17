"""Agent 4 — Full Text Analyzer.

Reads each selected article's PDF (falling back to the abstract), extracts a
structured JSON analysis via LLM, persists it to the ``analyses`` table, and
indexes the full text into the RAG vector store.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.agents.common import audit
from app.config import settings
from app.database.models import Analysis, Article
from app.database.session import session_scope
from app.rag import retriever
from app.utils.llm import LLMError, complete_json
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.utils.pdf_parser import parse_pdf
from app.workflows.state import PipelineState

log = get_logger("full_text_analyzer")
AGENT = "full_text_analyzer"

ANALYSIS_SYSTEM = """You are a meticulous urology/andrology research analyst. You receive the full
text (or abstract) of a scientific article. Extract a faithful, structured
summary. Do not invent data; if a field is not reported, use null (or an empty
string for text fields).

Return STRICT JSON with exactly these keys:
{
  "objective": "<study objective>",
  "methodology": "<design and methods>",
  "inclusion_criteria": "<inclusion criteria>",
  "exclusion_criteria": "<exclusion criteria>",
  "population": "<population / setting>",
  "sample_size": <integer or null>,
  "primary_outcome": "<primary outcome>",
  "secondary_outcomes": "<secondary outcomes>",
  "results": "<key quantitative results with effect sizes / p-values where reported>",
  "limitations": "<limitations>",
  "conclusions": "<authors' conclusions>",
  "summary": "<concise 4-6 sentence structured summary for a specialist>"
}
Output ONLY the JSON object."""

MAX_TEXT_CHARS = 60_000


def _analyze_one(item: dict) -> dict:
    text = item["abstract"] or ""
    used_full_text = False
    if item["pdf_path"]:
        try:
            parsed = parse_pdf(item["pdf_path"])
            if parsed.char_count > 500:
                text = parsed.text
                used_full_text = True
        except Exception as exc:  # corrupt/unreadable PDF -> fall back to abstract
            log.warning("pdf_parse_failed", article_id=item["id"], error=str(exc))

    if not text.strip():
        return {"id": item["id"], "structured": None, "text": "", "used_full_text": False}

    user = f"Title: {item['title']}\n\nFull text:\n{text[:MAX_TEXT_CHARS]}"
    try:
        structured = complete_json(ANALYSIS_SYSTEM, user, model=settings.analysis_model, max_tokens=3000)
    except LLMError as exc:
        log.warning("analysis_failed", article_id=item["id"], error=str(exc))
        structured = None
    return {"id": item["id"], "structured": structured, "text": text, "used_full_text": used_full_text}


def _coerce_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    selected_ids = state.get("selected_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            articles = session.scalars(select(Article).where(Article.id.in_(selected_ids))).all() if selected_ids else []
            items = [
                {"id": a.id, "title": a.title, "abstract": a.abstract, "pdf_path": a.pdf_path}
                for a in articles
            ]

        results: list[dict] = []
        if items:
            with ThreadPoolExecutor(max_workers=settings.max_concurrent_llm) as pool:
                results = list(pool.map(_analyze_one, items))

        analyzed_ids: list[int] = []
        with session_scope() as session:
            for res in results:
                structured = res["structured"]
                if not structured:
                    continue
                article = session.get(Article, res["id"])
                if not article:
                    continue
                analysis = article.analysis or Analysis(article_id=article.id)
                analysis.objective = structured.get("objective")
                analysis.methodology = structured.get("methodology")
                analysis.inclusion_criteria = structured.get("inclusion_criteria")
                analysis.exclusion_criteria = structured.get("exclusion_criteria")
                analysis.population = structured.get("population")
                analysis.sample_size = _coerce_int(structured.get("sample_size"))
                analysis.primary_outcome = structured.get("primary_outcome")
                analysis.secondary_outcomes = structured.get("secondary_outcomes")
                analysis.results = structured.get("results")
                analysis.limitations = structured.get("limitations")
                analysis.conclusions = structured.get("conclusions")
                analysis.summary = structured.get("summary")
                analysis.structured = structured
                if article.analysis is None:
                    session.add(analysis)
                session.flush()
                analyzed_ids.append(article.id)

                # Index into RAG (best-effort).
                try:
                    retriever.index_text(
                        session,
                        source_type="article",
                        source_id=article.id,
                        title=article.title,
                        text=res["text"] or (analysis.summary or ""),
                        topic=article.study_design,
                        article_id=article.id,
                    )
                except Exception as exc:  # embedding/Qdrant failure must not lose the analysis
                    log.warning("rag_index_failed", article_id=article.id, error=str(exc))

            audit(session, run_id, AGENT, "analysis_complete",
                  message=f"{len(analyzed_ids)} articles analyzed",
                  payload={"analyzed_ids": analyzed_ids})

    log.info("full_text_analyzer_done", analyzed=len(analyzed_ids))
    return {"analyzed_ids": analyzed_ids, "stats": {"analyzed": len(analyzed_ids)}}
