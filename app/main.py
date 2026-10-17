"""FastAPI application: health, metrics, manual run, RAG query, feedback.

On startup it configures logging, ensures storage and the Qdrant collection, and
starts the APScheduler weekly job. The same image therefore serves the API and
runs the scheduler.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from app import __version__
from app.config import settings
from app.database.models import Article, Newsletter, UserFeedback
from app.database.session import get_session
from app.rag import retriever, vector_store
from app.scheduler import shutdown_scheduler, start_scheduler
from app.utils.logger import configure_logging, get_logger
from app.workflows.weekly_pipeline import run_pipeline

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.ensure_storage_dirs()
    log.info("api_starting", version=__version__, env=settings.app_env)
    try:
        vector_store.ensure_collection()
    except Exception as exc:  # Qdrant may still be warming up; not fatal for the API
        log.warning("qdrant_init_deferred", error=str(exc))
    try:
        start_scheduler()
    except Exception as exc:  # pragma: no cover
        log.error("scheduler_start_failed", error=str(exc))
    yield
    shutdown_scheduler()
    log.info("api_stopped")


app = FastAPI(
    title="Urology Intelligence",
    version=__version__,
    description="Autonomous scientific intelligence platform for andrology and urology.",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class RagQuery(BaseModel):
    question: str = Field(..., min_length=3)
    top_k: int = Field(8, ge=1, le=20)
    source_types: Optional[list[str]] = None


class FeedbackIn(BaseModel):
    event_type: str = Field(..., description="opened|ignored|liked|disliked|saved")
    article_id: Optional[int] = None
    topic: Optional[str] = None
    read_seconds: Optional[int] = None
    rating: Optional[int] = None
    note: Optional[str] = None


# --------------------------------------------------------------------------- #
# Health & metrics
# --------------------------------------------------------------------------- #
@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    status = {"status": "ok", "version": __version__, "db": "down", "qdrant": "down", "redis": "down"}
    try:
        session.execute(text("SELECT 1"))
        status["db"] = "up"
    except Exception as exc:
        status["status"] = "degraded"
        log.warning("health_db_down", error=str(exc))
    try:
        vector_store.get_client().get_collections()
        status["qdrant"] = "up"
    except Exception:
        status["status"] = "degraded"
    try:
        import redis

        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
        status["redis"] = "up"
    except Exception:
        status["status"] = "degraded"
    return status


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --------------------------------------------------------------------------- #
# Pipeline control
# --------------------------------------------------------------------------- #
@app.post("/run")
def trigger_run() -> dict:
    """Trigger a full weekly pipeline run asynchronously."""
    run_id = uuid.uuid4().hex[:12]

    def _runner() -> None:
        try:
            run_pipeline(run_id=run_id)
        except Exception as exc:  # logged inside the pipeline as well
            log.error("manual_run_failed", run_id=run_id, error=str(exc))

    threading.Thread(target=_runner, name=f"run-{run_id}", daemon=True).start()
    log.info("manual_run_started", run_id=run_id)
    return {"status": "started", "run_id": run_id}


# --------------------------------------------------------------------------- #
# RAG
# --------------------------------------------------------------------------- #
@app.post("/rag/query")
def rag_query(payload: RagQuery) -> dict:
    try:
        answer = retriever.answer_question(
            payload.question, top_k=payload.top_k, source_types=payload.source_types
        )
    except Exception as exc:
        log.error("rag_query_failed", error=str(exc))
        raise HTTPException(status_code=502, detail=f"RAG query failed: {exc}") from exc
    return {"question": answer.question, "answer": answer.answer, "sources": answer.sources}


# --------------------------------------------------------------------------- #
# Feedback (drives Agent 10 — personalized learning)
# --------------------------------------------------------------------------- #
@app.post("/feedback", status_code=201)
def submit_feedback(payload: FeedbackIn, session: Session = Depends(get_session)) -> dict:
    if payload.article_id is not None and session.get(Article, payload.article_id) is None:
        raise HTTPException(status_code=404, detail="article not found")
    fb = UserFeedback(
        article_id=payload.article_id,
        event_type=payload.event_type,
        topic=payload.topic,
        read_seconds=payload.read_seconds,
        rating=payload.rating,
        note=payload.note,
    )
    session.add(fb)
    session.commit()
    return {"status": "recorded", "id": fb.id}


# --------------------------------------------------------------------------- #
# Newsletters
# --------------------------------------------------------------------------- #
@app.get("/newsletters/latest")
def latest_newsletter(session: Session = Depends(get_session)) -> dict:
    nl = session.scalars(select(Newsletter).order_by(desc(Newsletter.issue_date), desc(Newsletter.id)).limit(1)).first()
    if nl is None:
        raise HTTPException(status_code=404, detail="no newsletter yet")
    return {
        "id": nl.id,
        "issue_date": nl.issue_date.isoformat(),
        "title": nl.title,
        "executive_summary": nl.executive_summary,
        "dispatched": nl.dispatched,
        "markdown": nl.content_markdown,
        "paths": {"markdown": nl.markdown_path, "html": nl.html_path, "pdf": nl.pdf_path},
    }


@app.get("/")
def root() -> dict:
    return {"service": "urology-intelligence", "version": __version__, "docs": "/docs"}
