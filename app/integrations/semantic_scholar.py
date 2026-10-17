"""Semantic Scholar Graph API integration (bulk paper search)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import settings
from app.integrations.base import HttpClient, RawArticle, RawAuthor
from app.utils.logger import get_logger
from app.utils.metrics import ARTICLES_FETCHED

log = get_logger("semantic_scholar")

S2_BULK = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
FIELDS = "title,abstract,externalIds,venue,publicationDate,authors,url,openAccessPdf"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y").date()
        except ValueError:
            return None


def _to_article(item: dict) -> RawArticle | None:
    title = item.get("title")
    if not title:
        return None
    ext = item.get("externalIds") or {}
    oa = item.get("openAccessPdf") or {}
    doi = ext.get("DOI")
    return RawArticle(
        title=title.strip(),
        source="semantic_scholar",
        doi=doi.lower() if doi else None,
        pmid=str(ext["PubMed"]) if ext.get("PubMed") else None,
        pmcid=f"PMC{ext['PubMedCentral']}" if ext.get("PubMedCentral") else None,
        abstract=item.get("abstract"),
        journal=item.get("venue"),
        url=item.get("url"),
        publication_date=_parse_date(item.get("publicationDate")),
        authors=[RawAuthor(name=a["name"]) for a in item.get("authors", []) if a.get("name")],
        raw={"open_access_pdf": oa.get("url")},
    )


def search_recent(terms: list[str], lookback_days: int, limit: int = 200) -> list[RawArticle]:
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    today = date.today().isoformat()
    params = {
        "query": " | ".join(terms),  # S2 bulk supports boolean-ish OR via '|'
        "fields": FIELDS,
        "publicationDateOrYear": f"{since}:{today}",
        "sort": "publicationDate:desc",
    }
    headers = {"User-Agent": "urology-intelligence/3.0"}
    if settings.semantic_scholar_api_key:
        headers["x-api-key"] = settings.semantic_scholar_api_key

    articles: list[RawArticle] = []
    with HttpClient("semantic_scholar", headers=headers) as client:
        token: str | None = None
        while len(articles) < limit:
            q = dict(params)
            if token:
                q["token"] = token
            data = client.get_json(S2_BULK, params=q)
            for item in data.get("data", []) or []:
                parsed = _to_article(item)
                if parsed:
                    articles.append(parsed)
            token = data.get("token")
            if not token or not data.get("data"):
                break

    articles = articles[:limit]
    ARTICLES_FETCHED.labels(source="semantic_scholar").inc(len(articles))
    log.info("semantic_scholar_fetched", count=len(articles), since=since)
    return articles
