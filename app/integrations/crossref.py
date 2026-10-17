"""CrossRef REST API integration."""
from __future__ import annotations

import re
from datetime import date, timedelta

from app.config import settings
from app.integrations.base import HttpClient, RawArticle, RawAuthor
from app.utils.logger import get_logger
from app.utils.metrics import ARTICLES_FETCHED

log = get_logger("crossref")

CROSSREF_API = "https://api.crossref.org/works"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_jats(text: str | None) -> str | None:
    if not text:
        return None
    return _TAG_RE.sub("", text).strip() or None


def _parse_date(issued: dict) -> date | None:
    parts = (issued or {}).get("date-parts", [[]])
    if not parts or not parts[0]:
        return None
    dp = parts[0]
    year = dp[0]
    month = dp[1] if len(dp) > 1 else 1
    day = dp[2] if len(dp) > 2 else 1
    try:
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def _to_article(item: dict) -> RawArticle | None:
    titles = item.get("title") or []
    if not titles:
        return None
    containers = item.get("container-title") or []
    authors = [
        RawAuthor(
            name=" ".join(p for p in [a.get("given"), a.get("family")] if p) or a.get("name", ""),
            affiliation=(a.get("affiliation") or [{}])[0].get("name") if a.get("affiliation") else None,
            orcid=(a.get("ORCID") or "").rsplit("/", 1)[-1] or None,
        )
        for a in item.get("author", [])
        if a.get("family") or a.get("name")
    ]
    doi = item.get("DOI")
    return RawArticle(
        title=titles[0].strip(),
        source="crossref",
        doi=doi.lower() if doi else None,
        abstract=_strip_jats(item.get("abstract")),
        journal=containers[0] if containers else None,
        url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        publication_date=_parse_date(item.get("issued", {})),
        authors=authors,
        raw={"subjects": item.get("subject", [])},
    )


def search_recent(terms: list[str], lookback_days: int, rows: int = 100) -> list[RawArticle]:
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    params = {
        "query": " ".join(terms),
        "filter": f"from-pub-date:{since},type:journal-article",
        "rows": str(rows),
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,container-title,abstract,author,issued,URL,subject",
    }
    if settings.crossref_mailto:
        params["mailto"] = settings.crossref_mailto

    headers = {"User-Agent": f"urology-intelligence/3.0 (mailto:{settings.crossref_mailto})"}
    with HttpClient("crossref", headers=headers) as client:
        data = client.get_json(CROSSREF_API, params=params)

    items = data.get("message", {}).get("items", [])
    articles = [a for a in (_to_article(i) for i in items) if a]
    ARTICLES_FETCHED.labels(source="crossref").inc(len(articles))
    log.info("crossref_fetched", count=len(articles), since=since)
    return articles
