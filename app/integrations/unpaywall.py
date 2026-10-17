"""Unpaywall integration — resolves an open-access PDF URL from a DOI."""
from __future__ import annotations

import httpx

from app.config import settings
from app.integrations.base import HttpClient
from app.utils.logger import get_logger

log = get_logger("unpaywall")

UNPAYWALL_API = "https://api.unpaywall.org/v2"


def resolve_pdf_url(doi: str) -> str | None:
    """Return a direct PDF URL for ``doi`` if an open-access copy exists."""
    if not doi or not settings.unpaywall_email:
        return None
    try:
        with HttpClient("unpaywall") as client:
            data = client.get_json(
                f"{UNPAYWALL_API}/{doi}", params={"email": settings.unpaywall_email}
            )
    except httpx.HTTPError as exc:
        log.warning("unpaywall_lookup_failed", doi=doi, error=str(exc))
        return None

    best = data.get("best_oa_location") or {}
    url = best.get("url_for_pdf") or best.get("url")
    if not url:
        for loc in data.get("oa_locations", []) or []:
            if loc.get("url_for_pdf"):
                url = loc["url_for_pdf"]
                break
    if url:
        log.info("unpaywall_resolved", doi=doi)
    return url
