"""International Society for Sexual Medicine (ISSM) guideline source.

ISSM (with ISSWSH/ESSM) co-authors sexual-medicine standards committee reports.
"""
from __future__ import annotations

from app.integrations.guidelines_base import GuidelineRecord, fetch_listing

SOCIETY = "ISSM"
INDEX_URLS = [
    "https://www.issm.info/news",
    "https://www.issm.info/sexual-health-headlines",
]


def fetch_guidelines() -> list[GuidelineRecord]:
    return fetch_listing("issm", SOCIETY, INDEX_URLS)
