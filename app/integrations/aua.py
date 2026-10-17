"""American Urological Association (AUA) guideline source."""
from __future__ import annotations

from app.integrations.guidelines_base import GuidelineRecord, fetch_listing

SOCIETY = "AUA"
INDEX_URLS = [
    "https://www.auanet.org/guidelines-and-quality/guidelines",
    "https://www.auanet.org/guidelines-and-quality/guidelines/sexual-medicine-guidelines",
]


def fetch_guidelines() -> list[GuidelineRecord]:
    return fetch_listing("aua", SOCIETY, INDEX_URLS)
