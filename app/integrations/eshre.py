"""European Society of Human Reproduction and Embryology (ESHRE) guideline source."""
from __future__ import annotations

from app.integrations.guidelines_base import GuidelineRecord, fetch_listing

SOCIETY = "ESHRE"
INDEX_URLS = [
    "https://www.eshre.eu/Guidelines-and-Legal/Guidelines",
    "https://www.eshre.eu/Guidelines-and-Legal",
]


def fetch_guidelines() -> list[GuidelineRecord]:
    return fetch_listing("eshre", SOCIETY, INDEX_URLS)
