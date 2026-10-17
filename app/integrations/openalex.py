"""OpenAlex API integration."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import settings
from app.integrations.base import HttpClient, RawArticle, RawAuthor
from app.utils.logger import get_logger
from app.utils.metrics import ARTICLES_FETCHED

log = get_logger("openalex")

OPENALEX_API = "https://api.openalex.org/works"


def _reconstruct_abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positions)


def _clean_id(value: str | None, prefix: str) -> str | None:
    if not value:
        return None
    return value.replace(prefix, "").strip() or None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_article(work: dict) -> RawArticle | None:
    title = work.get("display_name") or work.get("title")
    if not title:
        return None
    ids = work.get("ids") or {}
    source = (work.get("primary_location") or {}).get("source") or {}
    oa = work.get("open_access") or {}
    best_oa = work.get("best_oa_location") or {}
    authors = [
        RawAuthor(
            name=(a.get("author") or {}).get("display_name", ""),
            affiliation=(a.get("raw_affiliation_strings") or [None])[0],
            orcid=_clean_id((a.get("author") or {}).get("orcid"), "https://orcid.org/"),
        )
        for a in work.get("authorships", [])
        if (a.get("author") or {}).get("display_name")
    ]
    return RawArticle(
        title=title.strip(),
        source="openalex",
        doi=_clean_id(work.get("doi"), "https://doi.org/"),
        pmid=_clean_id(ids.get("pmid"), "https://pubmed.ncbi.nlm.nih.gov/"),
        pmcid=_clean_id(ids.get("pmcid"), "https://www.ncbi.nlm.nih.gov/pmc/articles/"),
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        journal=source.get("display_name"),
        url=work.get("doi") or ids.get("openalex"),
        publication_date=_parse_date(work.get("publication_date")),
        authors=authors,
        raw={"open_access_pdf": oa.get("oa_url") or best_oa.get("pdf_url")},
    )


def search_recent(terms: list[str], lookback_days: int, per_page: int = 100) -> list[RawArticle]:
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    params = {
        "search": " ".join(terms),
        "filter": f"from_publication_date:{since},type:article",
        "per-page": str(per_page),
        "sort": "publication_date:desc",
    }
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto

    headers = {"User-Agent": f"urology-intelligence/3.0 (mailto:{settings.openalex_mailto})"}
    with HttpClient("openalex", headers=headers) as client:
        data = client.get_json(OPENALEX_API, params=params)

    works = data.get("results", [])
    articles = [a for a in (_to_article(w) for w in works) if a]
    ARTICLES_FETCHED.labels(source="openalex").inc(len(articles))
    log.info("openalex_fetched", count=len(articles), since=since)
    return articles
