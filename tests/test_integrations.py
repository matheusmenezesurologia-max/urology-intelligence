"""Tests for external API integrations (HTTP mocked with respx)."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.config import settings
from app.integrations import crossref, email as email_mod, openalex, pubmed, semantic_scholar, unpaywall
from app.integrations.base import RawArticle, RawAuthor

EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
 <PubmedArticle>
  <MedlineCitation>
   <PMID>40000001</PMID>
   <Article>
    <Journal><Title>Journal of Sexual Medicine</Title>
      <JournalIssue><PubDate><Year>2026</Year><Month>Jun</Month><Day>5</Day></PubDate></JournalIssue>
    </Journal>
    <ArticleTitle>Testosterone therapy in functional hypogonadism</ArticleTitle>
    <Abstract><AbstractText Label="BACKGROUND">A randomized trial of TRT.</AbstractText></Abstract>
    <AuthorList>
      <Author><LastName>Doe</LastName><ForeName>Jane</ForeName>
        <AffiliationInfo><Affiliation>Univ X</Affiliation></AffiliationInfo>
        <Identifier Source="ORCID">0000-0002-1825-0097</Identifier></Author>
    </AuthorList>
    <ELocationID EIdType="doi">10.1000/test.123</ELocationID>
   </Article>
  </MedlineCitation>
  <PubmedData><ArticleIdList>
    <ArticleId IdType="doi">10.1000/test.123</ArticleId>
    <ArticleId IdType="pmc">PMC1234567</ArticleId>
  </ArticleIdList></PubmedData>
 </PubmedArticle>
</PubmedArticleSet>"""


def test_rawarticle_dedup_key():
    a = RawArticle(title="T", source="s", doi="10.1/AbC")
    assert a.dedup_key() == "doi:10.1/abc"
    b = RawArticle(title="T", source="s", pmid="123")
    assert b.dedup_key() == "pmid:123"
    c = RawArticle(title="Some Title", source="s")
    assert c.dedup_key().startswith("title:some title")


@respx.mock
def test_pubmed_search_recent():
    respx.get(url__regex=r".*/esearch\.fcgi").mock(
        return_value=httpx.Response(200, json={"esearchresult": {"idlist": ["40000001"]}})
    )
    respx.get(url__regex=r".*/efetch\.fcgi").mock(
        return_value=httpx.Response(200, text=EFETCH_XML)
    )
    articles = pubmed.search_recent(["Journal of Sexual Medicine"], ["hypogonadism"], lookback_days=7)
    assert len(articles) == 1
    art = articles[0]
    assert art.pmid == "40000001"
    assert art.doi == "10.1000/test.123"
    assert art.pmcid == "PMC1234567"
    assert art.journal == "Journal of Sexual Medicine"
    assert art.authors and art.authors[0].name == "Jane Doe"
    assert art.publication_date.year == 2026


@respx.mock
def test_crossref_search_recent():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.5555/abc",
                    "title": ["Varicocele repair and fertility"],
                    "container-title": ["Fertility and Sterility"],
                    "abstract": "<jats:p>Background text.</jats:p>",
                    "author": [{"given": "John", "family": "Smith", "ORCID": "http://orcid.org/0000-0001"}],
                    "issued": {"date-parts": [[2026, 6, 1]]},
                    "URL": "https://doi.org/10.5555/abc",
                }
            ]
        }
    }
    respx.get(url__regex=r"https://api\.crossref\.org/works.*").mock(return_value=httpx.Response(200, json=payload))
    articles = crossref.search_recent(["varicocele"], lookback_days=7)
    assert len(articles) == 1
    assert articles[0].doi == "10.5555/abc"
    assert articles[0].abstract == "Background text."
    assert articles[0].journal == "Fertility and Sterility"


@respx.mock
def test_openalex_search_recent():
    payload = {
        "results": [
            {
                "display_name": "Sperm DNA fragmentation review",
                "doi": "https://doi.org/10.7777/oa",
                "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/40000099", "openalex": "https://openalex.org/W1"},
                "abstract_inverted_index": {"Sperm": [0], "DNA": [1], "fragmentation": [2]},
                "authorships": [{"author": {"display_name": "A B", "orcid": "https://orcid.org/0000-0003"}, "raw_affiliation_strings": ["Dept Y"]}],
                "primary_location": {"source": {"display_name": "Andrology"}},
                "open_access": {"oa_url": "http://oa.example/p.pdf"},
                "publication_date": "2026-06-02",
            }
        ]
    }
    respx.get(url__regex=r"https://api\.openalex\.org/works.*").mock(return_value=httpx.Response(200, json=payload))
    articles = openalex.search_recent(["sperm DNA"], lookback_days=7)
    assert len(articles) == 1
    assert articles[0].abstract == "Sperm DNA fragmentation"
    assert articles[0].pmid == "40000099"
    assert articles[0].journal == "Andrology"
    assert articles[0].raw["open_access_pdf"] == "http://oa.example/p.pdf"


@respx.mock
def test_semantic_scholar_search_recent():
    payload = {
        "data": [
            {
                "title": "Clomiphene vs testosterone for hypogonadism",
                "abstract": "Comparison study.",
                "externalIds": {"DOI": "10.9999/s2", "PubMed": 40000200},
                "venue": "Sexual Medicine",
                "publicationDate": "2026-06-03",
                "authors": [{"name": "C D"}],
                "url": "https://www.semanticscholar.org/paper/x",
                "openAccessPdf": {"url": "http://s2.example/p.pdf"},
            }
        ],
        "token": None,
    }
    respx.get(url__regex=r".*/paper/search/bulk").mock(return_value=httpx.Response(200, json=payload))
    articles = semantic_scholar.search_recent(["clomiphene"], lookback_days=7, limit=10)
    assert len(articles) == 1
    assert articles[0].doi == "10.9999/s2"
    assert articles[0].pmid == "40000200"
    assert articles[0].raw["open_access_pdf"] == "http://s2.example/p.pdf"


@respx.mock
def test_unpaywall_resolve():
    respx.get(url__regex=r"https://api\.unpaywall\.org/v2/.*").mock(
        return_value=httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "http://oa/x.pdf"}})
    )
    assert unpaywall.resolve_pdf_url("10.1000/test.123") == "http://oa/x.pdf"


@respx.mock
def test_unpaywall_no_oa():
    respx.get(url__regex=r"https://api\.unpaywall\.org/v2/.*").mock(
        return_value=httpx.Response(200, json={"best_oa_location": None, "oa_locations": []})
    )
    assert unpaywall.resolve_pdf_url("10.1000/none") is None


@respx.mock
def test_whatsapp_send_text(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "PHONE", raising=False)
    monkeypatch.setattr(settings, "whatsapp_access_token", "TOKEN", raising=False)
    monkeypatch.setattr(settings, "whatsapp_recipient", "5511999999999", raising=False)
    from app.integrations.whatsapp import WhatsAppClient

    route = respx.post(url__regex=r"https://graph\.facebook\.com/.*/PHONE/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
    )
    with WhatsAppClient() as wa:
        resp = wa.send_text("hello")
    assert route.called
    assert resp["messages"][0]["id"] == "wamid.1"


def test_email_send_newsletter(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_port", 587, raising=False)
    monkeypatch.setattr(settings, "smtp_user", "me@example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "app-pass", raising=False)
    monkeypatch.setattr(settings, "email_to", "doc@example.com", raising=False)

    captured: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"], captured["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self, *a):
            pass

        def starttls(self, *a, **k):
            captured["starttls"] = True

        def login(self, user, pw):
            captured["login"] = (user, pw)

        def send_message(self, msg):
            captured["msg"] = msg

    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)

    pdf = tmp_path / "nl.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    res = email_mod.send_newsletter(subject="Urology Weekly", html_body="<h1>Hi</h1>",
                                    text_body="resumo", pdf_path=str(pdf))

    assert res["recipients"] == ["doc@example.com"]
    msg = captured["msg"]
    assert msg["Subject"] == "Urology Weekly"
    assert msg["To"] == "doc@example.com"
    assert msg["From"] == "me@example.com"
    assert captured["login"] == ("me@example.com", "app-pass")
    assert captured["starttls"] is True
    types = {part.get_content_type() for part in msg.walk()}
    assert "text/html" in types
    assert "application/pdf" in types


def test_email_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_user", "", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "", raising=False)
    with pytest.raises(email_mod.EmailError):
        email_mod.send_newsletter(subject="x", html_body="<p>x</p>")
