"""PubMed / NCBI E-utilities integration (esearch + efetch)."""
from __future__ import annotations

import time
from datetime import date
from typing import Iterable
from xml.etree import ElementTree as ET

from app.config import settings
from app.integrations.base import HttpClient, RawArticle, RawAuthor
from app.utils.logger import get_logger
from app.utils.metrics import ARTICLES_FETCHED

log = get_logger("pubmed")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _common_params() -> dict[str, str]:
    params = {"tool": settings.ncbi_tool, "email": settings.ncbi_email}
    if settings.ncbi_api_key:
        params["api_key"] = settings.ncbi_api_key
    return params


def _journal_filter(journals: Iterable[str]) -> str:
    return "(" + " OR ".join(f'"{j}"[Journal]' for j in journals) + ")"


def _term_filter(terms: Iterable[str]) -> str:
    return "(" + " OR ".join(f'"{t}"[Title/Abstract]' for t in terms) + ")"


def _parse_date(article_el: ET.Element) -> date | None:
    pub = article_el.find(".//Journal/JournalIssue/PubDate")
    if pub is None:
        return None
    year_el = pub.find("Year")
    if year_el is not None and year_el.text:
        year = int(year_el.text)
        month_el = pub.find("Month")
        month = 1
        if month_el is not None and month_el.text:
            mt = month_el.text.strip().lower()
            month = _MONTHS.get(mt[:3], int(mt) if mt.isdigit() else 1)
        day_el = pub.find("Day")
        day = int(day_el.text) if (day_el is not None and day_el.text and day_el.text.isdigit()) else 1
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 1)
    medline = pub.find("MedlineDate")
    if medline is not None and medline.text:
        token = medline.text.strip().split(" ")[0][:4]
        if token.isdigit():
            return date(int(token), 1, 1)
    return None


def _parse_article(node: ET.Element) -> RawArticle | None:
    medline = node.find("MedlineCitation")
    if medline is None:
        return None
    article_el = medline.find("Article")
    if article_el is None:
        return None

    pmid_el = medline.find("PMID")
    pmid = pmid_el.text if pmid_el is not None else None

    title_el = article_el.find("ArticleTitle")
    title = "".join(title_el.itertext()).strip() if title_el is not None else "(untitled)"

    # Abstract: concatenate labeled sections.
    abstract_parts: list[str] = []
    for ab in article_el.findall(".//Abstract/AbstractText"):
        label = ab.get("Label")
        text = "".join(ab.itertext()).strip()
        abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(p for p in abstract_parts if p) or None

    journal_el = article_el.find(".//Journal/Title")
    journal = journal_el.text if journal_el is not None else None

    # Identifiers (DOI, PMC).
    doi = None
    pmcid = None
    for aid in node.findall(".//ArticleIdList/ArticleId"):
        id_type = aid.get("IdType")
        if id_type == "doi":
            doi = (aid.text or "").strip() or None
        elif id_type == "pmc":
            pmcid = (aid.text or "").strip() or None
    if doi is None:
        eloc = article_el.find('.//ELocationID[@EIdType="doi"]')
        if eloc is not None and eloc.text:
            doi = eloc.text.strip()

    authors: list[RawAuthor] = []
    for au in article_el.findall(".//AuthorList/Author"):
        last = au.findtext("LastName")
        fore = au.findtext("ForeName")
        collective = au.findtext("CollectiveName")
        if last:
            name = f"{fore} {last}".strip() if fore else last
        elif collective:
            name = collective
        else:
            continue
        aff = au.findtext(".//AffiliationInfo/Affiliation")
        orcid = None
        ident = au.find('.//Identifier[@Source="ORCID"]')
        if ident is not None and ident.text:
            orcid = ident.text.strip().rsplit("/", 1)[-1]
        authors.append(RawAuthor(name=name, affiliation=aff, orcid=orcid))

    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (f"https://doi.org/{doi}" if doi else None)

    return RawArticle(
        title=title,
        source="pubmed",
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        abstract=abstract,
        journal=journal,
        url=url,
        publication_date=_parse_date(article_el),
        authors=authors,
    )


def esearch(client: HttpClient, term: str, retmax: int, reldate: int) -> list[str]:
    params = {
        **_common_params(),
        "db": "pubmed",
        "term": term,
        "retmax": str(retmax),
        "retmode": "json",
        "reldate": str(reldate),
        "datetype": "edat",
        "sort": "most+recent",
    }
    data = client.get_json(f"{EUTILS}/esearch.fcgi", params=params)
    return data.get("esearchresult", {}).get("idlist", [])


def efetch(client: HttpClient, pmids: list[str]) -> list[RawArticle]:
    articles: list[RawArticle] = []
    # Batch to respect URL length / API courtesy.
    for i in range(0, len(pmids), 100):
        batch = pmids[i : i + 100]
        params = {**_common_params(), "db": "pubmed", "id": ",".join(batch), "retmode": "xml"}
        resp = client.get(f"{EUTILS}/efetch.fcgi", params=params)
        root = ET.fromstring(resp.text)
        for node in root.findall(".//PubmedArticle"):
            parsed = _parse_article(node)
            if parsed:
                articles.append(parsed)
        time.sleep(0.34 if not settings.ncbi_api_key else 0.11)  # rate-limit courtesy
    return articles


def search_recent(
    journals: list[str],
    terms: list[str],
    lookback_days: int,
    retmax: int = 200,
) -> list[RawArticle]:
    """Fetch articles from the last ``lookback_days`` days.

    Two complementary queries are merged:
      1. Everything published in the target journals.
      2. Priority-term hits across all of PubMed (catches relevant work elsewhere).
    """
    with HttpClient("pubmed", headers={"User-Agent": settings.ncbi_tool}) as client:
        pmids: set[str] = set()
        q_journals = _journal_filter(journals)
        pmids.update(esearch(client, q_journals, retmax, lookback_days))

        q_terms = _term_filter(terms)
        pmids.update(esearch(client, q_terms, retmax, lookback_days))

        log.info("pubmed_esearch", count=len(pmids), lookback_days=lookback_days)
        if not pmids:
            return []
        articles = efetch(client, list(pmids))

    ARTICLES_FETCHED.labels(source="pubmed").inc(len(articles))
    log.info("pubmed_efetch", fetched=len(articles))
    return articles
