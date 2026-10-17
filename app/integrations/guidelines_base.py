"""Shared helpers for guideline-society integrations.

Each society module configures one or more public index URLs and a set of
topic keywords. We fetch the index HTML, extract anchor links whose text matches
the user's clinical priorities, and return normalized :class:`GuidelineRecord`s.
The Guideline Tracker agent then fetches/hashes each entry to detect changes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.integrations.base import HttpClient
from app.utils.logger import get_logger

log = get_logger("guidelines")

# Anchor extractor: capture href and inner text (tags stripped).
_ANCHOR_RE = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Keywords aligned to the user's clinical priorities (andrology-first).
DEFAULT_KEYWORDS = [
    "sexual", "androl", "infertil", "fertilit", "reproduc", "testosterone",
    "hypogonad", "erectile", "peyronie", "ejaculat", "varicocele", "azoosperm",
    "sperm", "prostat", "hyperplasia", "bph", "oncolog", "luts", "men's health",
    "male", "endocrine",
]


@dataclass
class GuidelineRecord:
    society: str
    title: str
    url: str
    topic: str | None = None


def _clean_text(html_fragment: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", html_fragment)).strip()


def _match_topic(text: str, keywords: list[str]) -> str | None:
    low = text.lower()
    for kw in keywords:
        if kw in low:
            return kw
    return None


def fetch_listing(
    service: str,
    society: str,
    urls: list[str],
    keywords: list[str] | None = None,
    min_text_len: int = 8,
) -> list[GuidelineRecord]:  # pragma: no cover - network
    """Fetch each index URL and extract candidate guideline links."""
    keywords = keywords or DEFAULT_KEYWORDS
    records: dict[str, GuidelineRecord] = {}
    headers = {"User-Agent": "urology-intelligence/3.0 (+guideline-tracker)"}

    with HttpClient(service, headers=headers) as client:
        for index_url in urls:
            try:
                html = client.get(index_url).text
            except httpx.HTTPError as exc:
                log.warning("guideline_index_failed", society=society, url=index_url, error=str(exc))
                continue
            for href, inner in _ANCHOR_RE.findall(html):
                text = _clean_text(inner)
                if len(text) < min_text_len:
                    continue
                topic = _match_topic(text, keywords)
                if not topic:
                    continue
                absolute = urljoin(index_url, href)
                if absolute in records:
                    continue
                records[absolute] = GuidelineRecord(
                    society=society, title=text[:300], url=absolute, topic=topic
                )

    result = list(records.values())
    log.info("guideline_listing", society=society, found=len(result))
    return result
