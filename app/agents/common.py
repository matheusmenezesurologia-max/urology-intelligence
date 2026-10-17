"""Shared helpers used across agents: persistence, prompts, auditing."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROMPTS_DIR
from app.database.models import Article, AuditLog, Author, Journal
from app.integrations.base import RawArticle
from app.utils.logger import get_logger

log = get_logger("agents")


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    """Load a prompt template from app/prompts/<name>.txt (cached)."""
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def audit(
    session: Session,
    run_id: str | None,
    agent: str,
    event: str,
    message: Optional[str] = None,
    level: str = "INFO",
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """Append an entry to the audit trail (best-effort)."""
    session.add(
        AuditLog(run_id=run_id, agent=agent, level=level, event=event, message=message, payload=payload)
    )
    session.flush()


def get_or_create_journal(session: Session, name: Optional[str]) -> Optional[Journal]:
    if not name:
        return None
    name = name.strip()[:512]
    journal = session.scalar(select(Journal).where(Journal.name == name))
    if journal is None:
        journal = Journal(name=name)
        session.add(journal)
        session.flush()
    return journal


def get_or_create_author(session: Session, name: str, affiliation: Optional[str], orcid: Optional[str]) -> Author:
    name = name.strip()[:512]
    stmt = select(Author).where(Author.full_name == name)
    if orcid:
        stmt = select(Author).where(Author.full_name == name, Author.orcid == orcid)
    author = session.scalar(stmt)
    if author is None:
        author = Author(full_name=name, affiliation=affiliation, orcid=orcid)
        session.add(author)
        session.flush()
    return author


def _find_existing(session: Session, raw: RawArticle) -> Optional[Article]:
    if raw.doi:
        found = session.scalar(select(Article).where(Article.doi == raw.doi))
        if found:
            return found
    if raw.pmid:
        return session.scalar(select(Article).where(Article.pmid == raw.pmid))
    return None


def persist_articles(session: Session, raw_articles: list[RawArticle], run_id: str) -> list[int]:
    """Insert new articles (deduplicated against the DB and within the batch).

    Returns the ids of articles newly persisted in this run. Articles already in
    the database (processed in a previous week) are skipped.
    """
    seen_keys: set[str] = set()
    new_ids: list[int] = []

    for raw in raw_articles:
        key = raw.dedup_key()
        if key in seen_keys:
            continue
        seen_keys.add(key)

        if _find_existing(session, raw) is not None:
            continue

        journal = get_or_create_journal(session, raw.journal)
        article = Article(
            doi=raw.doi,
            pmid=raw.pmid,
            pmcid=raw.pmcid,
            title=raw.title[:20000],
            abstract=raw.abstract,
            url=raw.url,
            publication_date=raw.publication_date,
            source=raw.source,
            run_id=run_id,
            raw_metadata=raw.raw or None,
        )
        # Add to the session before wiring relationships so back-population
        # (Journal.articles / association rows) sees a persistent object.
        session.add(article)
        article.journal = journal
        for ra in raw.authors[:50]:
            author = get_or_create_author(session, ra.name, ra.affiliation, ra.orcid)
            article.authors.append(author)
        session.flush()
        new_ids.append(article.id)

    log.info("persisted_articles", run_id=run_id, new=len(new_ids), received=len(raw_articles))
    return new_ids
