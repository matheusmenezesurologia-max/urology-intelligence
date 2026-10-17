"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def _ts() -> tuple[sa.Column, sa.Column]:
    """Fresh timestamp columns per table (a Column instance binds to one Table)."""
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    # --- journals -------------------------------------------------------- #
    op.create_table(
        "journals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("issn", sa.String(32), nullable=True),
        sa.Column("publisher", sa.String(256), nullable=True),
        sa.Column("impact_factor", sa.Float(), nullable=True),
        *_ts(),
    )
    op.create_index("ix_journals_name", "journals", ["name"], unique=True)
    op.create_index("ix_journals_issn", "journals", ["issn"])

    # --- authors --------------------------------------------------------- #
    op.create_table(
        "authors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(512), nullable=False),
        sa.Column("orcid", sa.String(32), nullable=True),
        sa.Column("affiliation", sa.Text(), nullable=True),
        sa.UniqueConstraint("full_name", "orcid", name="uq_author_name_orcid"),
        *_ts(),
    )
    op.create_index("ix_authors_full_name", "authors", ["full_name"])
    op.create_index("ix_authors_orcid", "authors", ["orcid"])

    # --- newsletters ----------------------------------------------------- #
    op.create_table(
        "newsletters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("markdown_path", sa.Text(), nullable=True),
        sa.Column("html_path", sa.Text(), nullable=True),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("dispatched", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    op.create_index("ix_newsletters_run_id", "newsletters", ["run_id"])
    op.create_index("ix_newsletters_issue_date", "newsletters", ["issue_date"])

    # --- guidelines ------------------------------------------------------ #
    op.create_table(
        "guidelines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("society", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(256), nullable=True),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("is_update", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("raw_metadata", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("society", "title", "version", name="uq_guideline_version"),
        *_ts(),
    )
    op.create_index("ix_guidelines_society", "guidelines", ["society"])
    op.create_index("ix_guidelines_topic", "guidelines", ["topic"])
    op.create_index("ix_guidelines_content_hash", "guidelines", ["content_hash"])

    # --- topic_weights --------------------------------------------------- #
    op.create_table(
        "topic_weights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("observations", sa.Integer(), server_default="0", nullable=False),
        *_ts(),
    )
    op.create_index("ix_topic_weights_topic", "topic_weights", ["topic"], unique=True)

    # --- articles -------------------------------------------------------- #
    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("doi", sa.String(256), nullable=True),
        sa.Column("pmid", sa.String(32), nullable=True),
        sa.Column("pmcid", sa.String(32), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(64), server_default="pubmed", nullable=False),
        sa.Column("raw_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("journal_id", sa.Integer(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("triage_rationale", sa.Text(), nullable=True),
        sa.Column("study_design", sa.String(128), nullable=True),
        sa.Column("is_selected", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("pdf_path", sa.Text(), nullable=True),
        sa.Column("pdf_source", sa.String(64), nullable=True),
        sa.Column("has_full_text", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("newsletter_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["journal_id"], ["journals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["newsletter_id"], ["newsletters.id"], ondelete="SET NULL"),
        *_ts(),
    )
    op.create_index("ix_articles_doi", "articles", ["doi"], unique=True)
    op.create_index("ix_articles_pmid", "articles", ["pmid"], unique=True)
    op.create_index("ix_articles_pmcid", "articles", ["pmcid"])
    op.create_index("ix_articles_publication_date", "articles", ["publication_date"])
    op.create_index("ix_articles_journal_id", "articles", ["journal_id"])
    op.create_index("ix_articles_relevance_score", "articles", ["relevance_score"])
    op.create_index("ix_articles_is_selected", "articles", ["is_selected"])
    op.create_index("ix_articles_run_id", "articles", ["run_id"])

    # --- article_authors (association) ----------------------------------- #
    op.create_table(
        "article_authors",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["authors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id", "author_id"),
    )

    # --- analyses -------------------------------------------------------- #
    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("methodology", sa.Text(), nullable=True),
        sa.Column("inclusion_criteria", sa.Text(), nullable=True),
        sa.Column("exclusion_criteria", sa.Text(), nullable=True),
        sa.Column("population", sa.Text(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("primary_outcome", sa.Text(), nullable=True),
        sa.Column("secondary_outcomes", sa.Text(), nullable=True),
        sa.Column("results", sa.Text(), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("conclusions", sa.Text(), nullable=True),
        sa.Column("structured", postgresql.JSONB(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("clinical_impact_score", sa.Float(), nullable=True),
        sa.Column("evidence_quality_score", sa.Float(), nullable=True),
        sa.Column("practice_change_probability", sa.Float(), nullable=True),
        sa.Column("andrology_relevance_score", sa.Float(), nullable=True),
        sa.Column("risk_of_bias", sa.String(32), nullable=True),
        sa.Column("appraisal_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("article_id", name="uq_analyses_article"),
        *_ts(),
    )

    # --- clinical_interpretations ---------------------------------------- #
    op.create_table(
        "clinical_interpretations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("changes_practice", sa.Boolean(), nullable=True),
        sa.Column("what_to_do_differently", sa.Text(), nullable=True),
        sa.Column("applies_to_patients", sa.Text(), nullable=True),
        sa.Column("not_applies_to_patients", sa.Text(), nullable=True),
        sa.Column("implementation_barriers", sa.Text(), nullable=True),
        sa.Column("confidence_level", sa.String(32), nullable=True),
        sa.Column("guideline_relationship", sa.String(64), nullable=True),
        sa.Column("guideline_alignment_score", sa.Float(), nullable=True),
        sa.Column("guideline_comparison_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("article_id", name="uq_interpretation_article"),
        *_ts(),
    )

    # --- embeddings ------------------------------------------------------ #
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("point_id", sa.String(64), nullable=False),
        sa.Column("collection", sa.String(128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(32), server_default="article", nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("point_id", name="uq_embeddings_point_id"),
        *_ts(),
    )
    op.create_index("ix_embeddings_article_id", "embeddings", ["article_id"])

    # --- user_feedback --------------------------------------------------- #
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("topic", sa.String(128), nullable=True),
        sa.Column("read_seconds", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        *_ts(),
    )
    op.create_index("ix_user_feedback_article_id", "user_feedback", ["article_id"])
    op.create_index("ix_user_feedback_topic", "user_feedback", ["topic"])

    # --- audit_logs ------------------------------------------------------ #
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("level", sa.String(16), server_default="INFO", nullable=False),
        sa.Column("event", sa.String(128), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        *_ts(),
    )
    op.create_index("ix_audit_logs_run_id", "audit_logs", ["run_id"])
    op.create_index("ix_audit_logs_agent", "audit_logs", ["agent"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("user_feedback")
    op.drop_table("embeddings")
    op.drop_table("clinical_interpretations")
    op.drop_table("analyses")
    op.drop_table("article_authors")
    op.drop_table("articles")
    op.drop_table("topic_weights")
    op.drop_table("guidelines")
    op.drop_table("newsletters")
    op.drop_table("authors")
    op.drop_table("journals")
