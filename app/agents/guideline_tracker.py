"""Agent 7 — Guideline Tracker.

Monitors AUA, EAU, ASRM, ISSM and ESHRE for new or updated guidelines/consensus
documents. Detects changes via content hashing, summarizes changed documents with
an LLM, persists them, and indexes summaries into the RAG store.
"""
from __future__ import annotations

import hashlib
import re

import httpx
from sqlalchemy import select

from app.agents.common import audit
from app.config import settings
from app.database.models import Guideline
from app.database.session import session_scope
from app.integrations import asrm, aua, eau, eshre, issm
from app.integrations.guidelines_base import GuidelineRecord
from app.rag import retriever
from app.utils.llm import LLMError, complete
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import GUIDELINE_UPDATES, track_agent
from app.workflows.state import PipelineState

log = get_logger("guideline_tracker")
AGENT = "guideline_tracker"

SOURCES = [aua, eau, asrm, issm, eshre]
MAX_PER_SOCIETY = 25
MAX_SUMMARIES = 15
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ALLTAGS_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"(19|20)\d{2}")

SUMMARY_SYSTEM = (
    "You are a urology guideline analyst. Summarize the clinical essence of the "
    "following guideline/consensus document text in 3-5 sentences (pt-BR), focused "
    "on andrology, sexual medicine, male infertility, hypogonadism and related "
    "recommendations. If the text is only a title/landing page, say so briefly."
)


def _fetch_text(url: str) -> str | None:  # pragma: no cover - network
    try:
        with httpx.Client(follow_redirects=True, timeout=settings.http_timeout_seconds,
                          headers={"User-Agent": "urology-intelligence/3.0"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError:
        return None
    ctype = resp.headers.get("content-type", "").lower()
    if "html" not in ctype:
        return None  # PDFs etc. — identity is still tracked via the URL hash
    text = _TAG_RE.sub(" ", resp.text)
    text = _ALLTAGS_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()[:8000]


def _hash(*parts: str) -> str:
    return hashlib.sha256("||".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:64]


def _detect_year(title: str) -> int | None:
    m = _YEAR_RE.search(title)
    return int(m.group(0)) if m else None


def _gather() -> list[GuidelineRecord]:  # pragma: no cover - network
    records: list[GuidelineRecord] = []
    for module in SOURCES:
        try:
            found = module.fetch_guidelines()[:MAX_PER_SOCIETY]
            records.extend(found)
        except Exception as exc:  # one society down must not stop the rest
            log.error("society_fetch_failed", society=getattr(module, "SOCIETY", "?"), error=str(exc))
    return records


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)

    with track_agent(AGENT):
        records = _gather()
        updates: list[dict] = []
        summaries_made = 0

        with session_scope() as session:
            for rec in records:
                page_text = _fetch_text(rec.url)
                content_hash = _hash(rec.title, rec.url, page_text or "")

                existing = session.scalar(
                    select(Guideline).where(Guideline.society == rec.society, Guideline.title == rec.title[:300])
                )
                if existing and existing.content_hash == content_hash:
                    continue  # unchanged

                is_update = existing is not None
                summary = None
                if page_text and summaries_made < MAX_SUMMARIES:
                    try:
                        summary = complete(SUMMARY_SYSTEM, page_text, model=settings.analysis_model,
                                           temperature=0.2, max_tokens=400)
                        summaries_made += 1
                    except LLMError as exc:
                        log.warning("guideline_summary_failed", url=rec.url, error=str(exc))

                if existing:
                    existing.url = rec.url
                    existing.content_hash = content_hash
                    existing.change_summary = "Conteúdo atualizado detectado." if is_update else None
                    existing.is_update = True
                    if summary:
                        existing.summary = summary
                    guideline = existing
                else:
                    guideline = Guideline(
                        society=rec.society,
                        title=rec.title[:300],
                        topic=rec.topic,
                        version=str(_detect_year(rec.title) or ""),
                        year=_detect_year(rec.title),
                        url=rec.url,
                        content_hash=content_hash,
                        summary=summary,
                        is_update=False,
                    )
                    session.add(guideline)
                session.flush()

                GUIDELINE_UPDATES.labels(society=rec.society).inc()
                updates.append({
                    "society": rec.society,
                    "title": rec.title,
                    "url": rec.url,
                    "topic": rec.topic,
                    "is_update": is_update,
                    "summary": summary,
                })

                if summary:
                    try:
                        retriever.index_text(
                            session,
                            source_type="guideline",
                            source_id=f"{rec.society}:{guideline.id}",
                            title=f"[{rec.society}] {rec.title}",
                            text=summary,
                            topic=rec.topic,
                        )
                    except Exception as exc:
                        log.warning("guideline_index_failed", url=rec.url, error=str(exc))

            audit(session, run_id, AGENT, "guideline_tracking_complete",
                  message=f"{len(updates)} new/updated of {len(records)} scanned",
                  payload={"updates": len(updates), "scanned": len(records)})

    log.info("guideline_tracker_done", updates=len(updates), scanned=len(records))
    return {"guideline_updates": updates, "stats": {"guideline_updates": len(updates), "guidelines_scanned": len(records)}}
