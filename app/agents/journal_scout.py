"""Agent 1 — Journal Scout.

Monitors the target journals and priority topics across PubMed, CrossRef,
Semantic Scholar and OpenAlex for the last N days, merges/deduplicates the
results, and persists new articles to PostgreSQL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.agents.common import audit, persist_articles
from app.config import PRIORITY_QUERY_TERMS, TARGET_JOURNALS, settings
from app.database.session import session_scope
from app.integrations import crossref, openalex, pubmed, semantic_scholar
from app.integrations.base import RawArticle
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("journal_scout")
AGENT = "journal_scout"


def _merge(articles: list[RawArticle]) -> list[RawArticle]:
    """Deduplicate by identity, filling missing fields from secondary copies."""
    merged: dict[str, RawArticle] = {}
    for art in articles:
        key = art.dedup_key()
        if key not in merged:
            merged[key] = art
            continue
        base = merged[key]
        base.doi = base.doi or art.doi
        base.pmid = base.pmid or art.pmid
        base.pmcid = base.pmcid or art.pmcid
        base.abstract = base.abstract or art.abstract
        base.journal = base.journal or art.journal
        base.url = base.url or art.url
        base.publication_date = base.publication_date or art.publication_date
        if not base.authors and art.authors:
            base.authors = art.authors
        if art.raw.get("open_access_pdf") and not base.raw.get("open_access_pdf"):
            base.raw["open_access_pdf"] = art.raw["open_access_pdf"]
    return list(merged.values())


def _fetch_all(lookback_days: int) -> tuple[list[RawArticle], dict[str, int]]:
    sources = {
        "pubmed": lambda: pubmed.search_recent(TARGET_JOURNALS, PRIORITY_QUERY_TERMS, lookback_days),
        "crossref": lambda: crossref.search_recent(PRIORITY_QUERY_TERMS, lookback_days),
        "semantic_scholar": lambda: semantic_scholar.search_recent(PRIORITY_QUERY_TERMS, lookback_days),
        "openalex": lambda: openalex.search_recent(PRIORITY_QUERY_TERMS, lookback_days),
    }
    collected: list[RawArticle] = []
    per_source: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = {pool.submit(fn): name for name, fn in sources.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results = future.result()
                collected.extend(results)
                per_source[name] = len(results)
            except Exception as exc:  # one source failing must not abort the run
                per_source[name] = 0
                log.error("source_failed", source=name, error=str(exc))
    return collected, per_source


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    lookback = state.get("lookback_days", settings.lookback_days)
    bind_run(run_id, AGENT)

    with track_agent(AGENT):
        raw, per_source = _fetch_all(lookback)
        merged = _merge(raw)
        with session_scope() as session:
            new_ids = persist_articles(session, merged, run_id)
            audit(
                session,
                run_id,
                AGENT,
                "scout_complete",
                message=f"{len(new_ids)} new articles from {len(merged)} unique candidates",
                payload={"per_source": per_source, "unique": len(merged), "new": len(new_ids)},
            )

    log.info("journal_scout_done", new=len(new_ids), per_source=per_source)
    return {
        "article_ids": new_ids,
        "stats": {"fetched_per_source": per_source, "unique_candidates": len(merged), "new_articles": len(new_ids)},
    }
