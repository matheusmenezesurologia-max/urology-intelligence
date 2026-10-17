"""European Association of Urology (EAU) guideline source."""
from __future__ import annotations

from app.integrations.guidelines_base import GuidelineRecord, fetch_listing

SOCIETY = "EAU"
INDEX_URLS = [
    "https://uroweb.org/guidelines",
    "https://uroweb.org/guidelines/sexual-and-reproductive-health",
]


def fetch_guidelines() -> list[GuidelineRecord]:
    return fetch_listing("eau", SOCIETY, INDEX_URLS)
