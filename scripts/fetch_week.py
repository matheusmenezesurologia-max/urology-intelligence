#!/usr/bin/env python3
"""Lightweight weekly article fetcher for the Urology Intelligence cloud routine.

Standard-library only (no pip install needed) so it runs reliably in an
unattended cloud agent. Queries PubMed + CrossRef for the last N days, scores
each article by the clinician's priority areas, deduplicates, and prints a
ranked shortlist in Markdown for Claude to triage and analyze.

Usage:
    python scripts/fetch_week.py                # last 7 days, top 30
    python scripts/fetch_week.py --days 7 --top 30 --out shortlist.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from xml.etree import ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF = "https://api.crossref.org/works"
MAILTO = "matheusmenezesurologia@gmail.com"
UA = f"urology-intelligence-routine/1.0 (mailto:{MAILTO})"

TARGET_JOURNALS = [
    "Journal of Sexual Medicine", "Sexual Medicine", "Sexual Medicine Reviews",
    "Andrology", "Fertility and Sterility", "Human Reproduction",
    "Translational Andrology and Urology", "European Urology",
    "European Urology Open Science", "The Journal of Urology", "BJU International",
    "Urology", "World Journal of Urology", "Nature Reviews Urology",
]
TERMS = [
    "andrology", "male infertility", "male reproduction", "spermatogenesis",
    "azoospermia", "varicocele", "hypogonadism", "testosterone replacement therapy",
    "erectile dysfunction", "Peyronie disease", "premature ejaculation",
    "benign prostatic hyperplasia", "prostate cancer",
]
# Priority weights (andrology-first) applied when scoring relevance.
WEIGHTS = {
    "androl": 1.0, "sexual": 1.0, "infertil": 1.0, "reproduc": 1.0, "sperm": 0.95,
    "azoosperm": 0.95, "varicocele": 0.9, "testis": 0.8, "testicular": 0.8,
    "hypogonad": 0.85, "testosterone": 0.85, "erectile": 0.85, "peyronie": 0.85,
    "ejaculat": 0.85, "semen": 0.8, "fertilit": 0.9,
    "benign prostatic": 0.6, "hyperplasia": 0.55, "luts": 0.55, "prostate cancer": 0.6,
}
STRONG_DESIGN = ("randomized", "randomised", "meta-analysis", "meta analysis",
                 "systematic review", "cohort", "multicenter", "multicentre")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _get(url: str, params: dict, timeout: int = 45) -> bytes:
    qs = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", _TAG.sub("", text)).strip()


def fetch_pubmed(days: int, retmax: int = 200) -> list[dict]:
    journals = "(" + " OR ".join(f'"{j}"[Journal]' for j in TARGET_JOURNALS) + ")"
    terms = "(" + " OR ".join(f'"{t}"[Title/Abstract]' for t in TERMS) + ")"
    pmids: list[str] = []
    for term in (journals, terms):
        try:
            raw = _get(f"{EUTILS}/esearch.fcgi", {
                "db": "pubmed", "term": term, "retmax": retmax, "retmode": "json",
                "reldate": days, "datetype": "edat", "sort": "most+recent",
                "tool": "urology-intelligence", "email": MAILTO,
            })
            pmids += json.loads(raw).get("esearchresult", {}).get("idlist", [])
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] pubmed esearch failed: {exc}", file=sys.stderr)
    pmids = list(dict.fromkeys(pmids))
    out: list[dict] = []
    for i in range(0, len(pmids), 100):
        batch = pmids[i:i + 100]
        try:
            xml = _get(f"{EUTILS}/efetch.fcgi", {
                "db": "pubmed", "id": ",".join(batch), "retmode": "xml",
                "tool": "urology-intelligence", "email": MAILTO,
            })
            root = ET.fromstring(xml)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] pubmed efetch failed: {exc}", file=sys.stderr)
            continue
        for node in root.findall(".//PubmedArticle"):
            art = node.find("MedlineCitation/Article")
            if art is None:
                continue
            title = _clean("".join(art.find("ArticleTitle").itertext())) if art.find("ArticleTitle") is not None else ""
            abstract = " ".join(_clean("".join(a.itertext())) for a in art.findall(".//Abstract/AbstractText"))
            journal = _clean(art.findtext(".//Journal/Title"))
            doi = None
            for aid in node.findall(".//ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = (aid.text or "").strip().lower() or None
            pub = art.find(".//Journal/JournalIssue/PubDate")
            year = pub.findtext("Year") if pub is not None else None
            out.append({"title": title, "abstract": abstract, "journal": journal,
                        "doi": doi, "date": year or "", "source": "pubmed"})
    return out


def fetch_crossref(days: int, rows: int = 80) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        raw = _get(CROSSREF, {
            "query": " ".join(TERMS),
            "filter": f"from-pub-date:{since},type:journal-article",
            "rows": rows, "sort": "published", "order": "desc",
            "select": "DOI,title,container-title,abstract,issued", "mailto": MAILTO,
        })
        items = json.loads(raw).get("message", {}).get("items", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] crossref failed: {exc}", file=sys.stderr)
        return []
    out = []
    for it in items:
        titles = it.get("title") or []
        if not titles:
            continue
        dp = (it.get("issued", {}).get("date-parts") or [[]])[0]
        out.append({
            "title": _clean(titles[0]),
            "abstract": _clean(it.get("abstract")),
            "journal": (it.get("container-title") or [""])[0],
            "doi": (it.get("DOI") or "").lower() or None,
            "date": "-".join(str(p) for p in dp) if dp else "",
            "source": "crossref",
        })
    return out


def score(art: dict) -> float:
    blob = f"{art['title']} {art['abstract']}".lower()
    rel = max((w for kw, w in WEIGHTS.items() if kw in blob), default=0.3)
    kw_hits = sum(1 for kw in WEIGHTS if kw in blob)
    design = 0.15 if any(d in blob for d in STRONG_DESIGN) else 0.0
    journal = 0.2 if art.get("journal") in TARGET_JOURNALS else 0.0
    has_abstract = 0.1 if art.get("abstract") else 0.0
    return round(rel + 0.05 * kw_hits + design + journal + has_abstract, 3)


def dedup(arts: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for a in arts:
        key = f"doi:{a['doi']}" if a.get("doi") else f"t:{a['title'].lower()[:120]}"
        if key not in seen:
            seen[key] = a
        elif not seen[key].get("abstract") and a.get("abstract"):
            seen[key] = a
    return list(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    arts = dedup(fetch_pubmed(args.days) + fetch_crossref(args.days))
    for a in arts:
        a["score"] = score(a)
    arts.sort(key=lambda a: a["score"], reverse=True)
    top = arts[: args.top]

    lines = [f"# Candidatos da semana (últimos {args.days} dias) — {len(arts)} únicos, top {len(top)}", ""]
    for i, a in enumerate(top, 1):
        doi = f" · DOI: {a['doi']}" if a.get("doi") else ""
        lines.append(f"## {i}. {a['title']}")
        lines.append(f"*{a['journal'] or 'n/a'}* · {a['date'] or 's/data'} · score {a['score']} · {a['source']}{doi}")
        lines.append(a["abstract"] or "_(sem resumo disponível)_")
        lines.append("")
    md = "\n".join(lines)

    sys.stdout.buffer.write(md.encode("utf-8"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"\n[ok] shortlist escrita em {args.out} ({len(top)} artigos)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
