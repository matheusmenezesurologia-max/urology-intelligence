"""Pytest fixtures.

A throwaway SQLite database backs the tests (models use a portable JSON type, so
the same schema works on SQLite and PostgreSQL). Environment is configured BEFORE
importing the application so the engine/settings singletons bind to the test DB.
"""
from __future__ import annotations

import os
import pathlib
import tempfile

# --- Configure environment before any app import -------------------------- #
_TMP = tempfile.mkdtemp(prefix="ui_test_")
_DB_PATH = pathlib.Path(_TMP) / "test.db"
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH.as_posix()
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "")
os.environ.setdefault("WHATSAPP_RECIPIENT", "")
# Force-isolate delivery config so tests NEVER read the real .env credentials
# or send a real email. Tests that exercise email set these via monkeypatch.
os.environ["DELIVERY_CHANNEL"] = "email"
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["EMAIL_FROM"] = ""
os.environ["EMAIL_TO"] = ""

import pytest  # noqa: E402

from app.database.base import Base  # noqa: E402
from app.database.session import SessionLocal, engine  # noqa: E402
from app.database import models  # noqa: E402,F401  (register tables)


@pytest.fixture(autouse=True)
def fresh_db():
    """Recreate all tables before each test for isolation."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def sample_raw_article():
    from app.integrations.base import RawArticle, RawAuthor

    return RawArticle(
        title="Testosterone therapy in functional hypogonadism: an RCT",
        source="pubmed",
        doi="10.1000/test.123",
        pmid="40000001",
        abstract="A randomized controlled trial of TRT in functional hypogonadism.",
        journal="Journal of Sexual Medicine",
        url="https://doi.org/10.1000/test.123",
        authors=[RawAuthor(name="Jane Doe", affiliation="Univ X", orcid="0000-0002-1825-0097")],
    )
