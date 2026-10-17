"""SQLAlchemy ORM models for Urology Intelligence.

Entity map
----------
Journal 1—* Article *—* Author (via article_authors)
Article 1—1 Analysis            (full-text structured extraction)
Article 1—1 ClinicalInterpretation
Article 1—* Embedding           (vector-store bookkeeping)
Article *—1 Newsletter          (issue an article appeared in)
Guideline (versioned, society-scoped)
UserFeedback  (personalized-learning signal)
AuditLog      (pipeline / system event trail)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

# Portable JSON: JSONB on PostgreSQL (production), generic JSON on SQLite (tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")

# --------------------------------------------------------------------------- #
# Association table: Article <-> Author (many-to-many, ordered)
# --------------------------------------------------------------------------- #
article_authors = Table(
    "article_authors",
    Base.metadata,
    Column("article_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("author_id", ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True),
    Column("position", Integer, nullable=False, default=0),
)


class Journal(Base, TimestampMixin):
    __tablename__ = "journals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512), unique=True, index=True, nullable=False)
    issn: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    impact_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="journal")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Journal {self.name!r}>"


class Author(Base, TimestampMixin):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    orcid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    affiliation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    articles: Mapped[list["Article"]] = relationship(
        secondary=article_authors, back_populates="authors"
    )

    __table_args__ = (UniqueConstraint("full_name", "orcid", name="uq_author_name_orcid"),)


class Article(Base, TimestampMixin):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doi: Mapped[Optional[str]] = mapped_column(String(256), unique=True, index=True, nullable=True)
    pmid: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True, nullable=True)
    pmcid: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    publication_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(64), default="pubmed", nullable=False)
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)

    journal_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("journals.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Triage outputs (Agent 2) ---
    relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    triage_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    study_design: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # --- PDF collection (Agent 3) ---
    pdf_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    has_full_text: Mapped[bool] = mapped_column(Boolean, default=False)

    # Pipeline run correlation
    run_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)

    # --- Relationships ---
    journal: Mapped[Optional["Journal"]] = relationship(back_populates="articles")
    authors: Mapped[list["Author"]] = relationship(
        secondary=article_authors, back_populates="articles", order_by=article_authors.c.position
    )
    analysis: Mapped[Optional["Analysis"]] = relationship(
        back_populates="article", uselist=False, cascade="all, delete-orphan"
    )
    interpretation: Mapped[Optional["ClinicalInterpretation"]] = relationship(
        back_populates="article", uselist=False, cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["Embedding"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["UserFeedback"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )

    newsletter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("newsletters.id", ondelete="SET NULL"), nullable=True
    )
    newsletter: Mapped[Optional["Newsletter"]] = relationship(back_populates="articles")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Article {self.doi or self.pmid or self.id}: {self.title[:48]!r}>"


class Analysis(Base, TimestampMixin):
    """Structured full-text extraction (Agent 4) + appraisal scores (Agent 5)."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Agent 4 — full-text structured JSON
    objective: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    methodology: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inclusion_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exclusion_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    population: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    primary_outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    secondary_outcomes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    results: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    limitations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conclusions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Agent 5 — evidence appraisal (0–10 scales)
    clinical_impact_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    practice_change_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    andrology_relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_of_bias: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    appraisal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    article: Mapped["Article"] = relationship(back_populates="analysis")


class ClinicalInterpretation(Base, TimestampMixin):
    """Agent 6 (translation) + Agent 8 (guideline comparison) outputs."""

    __tablename__ = "clinical_interpretations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    changes_practice: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    what_to_do_differently: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    applies_to_patients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    not_applies_to_patients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    implementation_barriers: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_level: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Guideline comparison (Agent 8)
    guideline_relationship: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    guideline_alignment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    guideline_comparison_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    article: Mapped["Article"] = relationship(back_populates="interpretation")


class Guideline(Base, TimestampMixin):
    """Versioned clinical guideline tracked by Agent 7."""

    __tablename__ = "guidelines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    society: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # AUA, EAU, ...
    title: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(String(256), index=True, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_update: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        UniqueConstraint("society", "title", "version", name="uq_guideline_version"),
    )


class Newsletter(Base, TimestampMixin):
    """Weekly issue produced by Agent 11."""

    __tablename__ = "newsletters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    issue_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    markdown_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    html_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    executive_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispatched: Mapped[bool] = mapped_column(Boolean, default=False)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    articles: Mapped[list["Article"]] = relationship(back_populates="newsletter")


class Embedding(Base, TimestampMixin):
    """Bookkeeping for vectors stored in Qdrant (the vector itself lives there)."""

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    point_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    collection: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), default="article")  # article|guideline|newsletter
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)

    article: Mapped[Optional["Article"]] = relationship(back_populates="embeddings")


class UserFeedback(Base, TimestampMixin):
    """Personalized-learning signal (Agent 10)."""

    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # opened|ignored|liked|disliked|saved
    topic: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    read_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # -1, 0, +1 ... 1..5
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    article: Mapped[Optional["Article"]] = relationship(back_populates="feedback")


class TopicWeight(Base, TimestampMixin):
    """Learned per-topic relevance multiplier, adjusted by Agent 10."""

    __tablename__ = "topic_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    observations: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base, TimestampMixin):
    """Append-only event trail for pipeline runs and system actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    agent: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)
