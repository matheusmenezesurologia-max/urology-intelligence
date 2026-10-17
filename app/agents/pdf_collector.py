"""Agent 3 — PDF Collector.

Resolves and downloads an open-access PDF for each selected article, trying
(in order): a known OA link from the fetch sources, Unpaywall by DOI, and
PubMed Central. Saved under storage/pdfs/<article_id>.pdf.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from app.agents.common import audit
from app.config import PDF_DIR, settings
from app.database.models import Article
from app.database.session import session_scope
from app.integrations import unpaywall
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import PDFS_DOWNLOADED, track_agent
from app.workflows.state import PipelineState

log = get_logger("pdf_collector")
AGENT = "pdf_collector"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 urology-intelligence/3.0"
)


def _download(url: str) -> bytes | None:  # pragma: no cover - network
    """Download a URL and return its bytes if it is a real PDF, else None."""
    try:
        with httpx.Client(follow_redirects=True, timeout=settings.http_timeout_seconds,
                          headers={"User-Agent": _BROWSER_UA, "Accept": "application/pdf,*/*"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as exc:
        log.debug("pdf_download_failed", url=url, error=str(exc))
        return None
    ctype = resp.headers.get("content-type", "").lower()
    if content[:5] == b"%PDF-" or "application/pdf" in ctype:
        return content
    log.debug("pdf_not_pdf", url=url, ctype=ctype)
    return None


def _candidate_urls(doi: str | None, pmcid: str | None, oa_url: str | None):
    """Lazily yield ``(source, url)`` candidates in priority order.

    Being a generator, the (network-bound) Unpaywall lookup only runs if the
    earlier, cheaper candidates failed to produce a usable PDF.
    """
    if oa_url:
        yield ("oa_source", oa_url)
    if doi:
        resolved = unpaywall.resolve_pdf_url(doi)
        if resolved:
            yield ("unpaywall", resolved)
    if pmcid:
        pmc = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        yield ("pmc", f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/pdf/")
        yield ("europepmc", f"https://www.ebi.ac.uk/europepmc/webservices/rest/PMC/{pmc}/fullTextPDF")


def _collect_one(item: dict) -> dict:
    oa_url = (item.get("raw_metadata") or {}).get("open_access_pdf")
    for source, url in _candidate_urls(item.get("doi"), item.get("pmcid"), oa_url):
        data = _download(url)
        if data:
            path = PDF_DIR / f"{item['id']}.pdf"
            path.write_bytes(data)
            log.info("pdf_saved", article_id=item["id"], source=source, bytes=len(data))
            return {"id": item["id"], "path": str(path), "source": source}
    log.info("pdf_unavailable", article_id=item["id"])
    return {"id": item["id"], "path": None, "source": None}


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    selected_ids = state.get("selected_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            articles = session.scalars(select(Article).where(Article.id.in_(selected_ids))).all() if selected_ids else []
            items = [
                {"id": a.id, "doi": a.doi, "pmcid": a.pmcid, "raw_metadata": a.raw_metadata}
                for a in articles
            ]

        results: list[dict] = []
        if items:
            with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
                results = list(pool.map(_collect_one, items))

        collected = 0
        with session_scope() as session:
            for res in results:
                article = session.get(Article, res["id"])
                if not article:
                    continue
                if res["path"]:
                    article.pdf_path = res["path"]
                    article.pdf_source = res["source"]
                    article.has_full_text = True
                    collected += 1
                    PDFS_DOWNLOADED.labels(source=res["source"]).inc()
            audit(session, run_id, AGENT, "pdf_collect_complete",
                  message=f"{collected}/{len(items)} PDFs collected")

    log.info("pdf_collector_done", collected=collected, attempted=len(items))
    return {"stats": {"pdfs_collected": collected, "pdfs_attempted": len(items)}}
