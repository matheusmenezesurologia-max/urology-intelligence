"""American Society for Reproductive Medicine (ASRM) guideline source."""
from __future__ import annotations

from app.integrations.guidelines_base import GuidelineRecord, fetch_listing

SOCIETY = "ASRM"
INDEX_URLS = [
    "https://www.asrm.org/practice-guidance/practice-committee-documents/",
    "https://www.asrm.org/practice-guidance/clinical-guidance/",
]


def fetch_guidelines() -> list[GuidelineRecord]:
    return fetch_listing("asrm", SOCIETY, INDEX_URLS)
