"""Tests for the 12 agents (LLM + network mocked, real SQLite persistence)."""
from __future__ import annotations

from sqlalchemy import select

from app.agents import (
    clinical_practice_translator,
    evidence_appraiser,
    full_text_analyzer,
    guideline_comparator,
    guideline_tracker,
    journal_scout,
    newsletter_dispatcher,
    newsletter_editor,
    pdf_collector,
    personalized_learning,
    research_opportunity_scout,
    scientific_triage,
)
from app.agents.common import persist_articles
from app.config import settings
from app.database.models import (
    Analysis,
    Article,
    ClinicalInterpretation,
    Guideline,
    Newsletter,
    TopicWeight,
    UserFeedback,
)
from app.database.session import session_scope
from app.integrations.base import RawArticle, RawAuthor
from app.integrations.guidelines_base import GuidelineRecord


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_article(session, **overrides) -> int:
    art = Article(
        title=overrides.get("title", "Micro-TESE in non-obstructive azoospermia"),
        abstract=overrides.get("abstract", "A prospective cohort of micro-TESE outcomes."),
        doi=overrides.get("doi", "10.1/seed"),
        is_selected=overrides.get("is_selected", True),
        study_design=overrides.get("study_design", "prospective-cohort"),
        relevance_score=overrides.get("relevance_score", 80.0),
    )
    session.add(art)
    session.commit()
    return art.id


def _add_analysis(session, article_id, **scores):
    an = Analysis(
        article_id=article_id,
        summary="Summary of findings.",
        structured={"conclusions": "micro-TESE improves retrieval", "sample_size": 120},
        clinical_impact_score=scores.get("clinical_impact_score"),
    )
    session.add(an)
    session.commit()


# --------------------------------------------------------------------------- #
# common.persist_articles
# --------------------------------------------------------------------------- #
def test_persist_articles_dedup(session):
    raw = [
        RawArticle(title="A", source="pubmed", doi="10.1/x",
                   authors=[RawAuthor(name="X Y")], journal="Andrology"),
        RawArticle(title="A dup", source="crossref", doi="10.1/x"),
    ]
    new_ids = persist_articles(session, raw, run_id="r1")
    session.commit()
    assert len(new_ids) == 1
    # Second persistence of the same DOI yields nothing new.
    again = persist_articles(session, [RawArticle(title="A", source="x", doi="10.1/x")], run_id="r2")
    assert again == []


# --------------------------------------------------------------------------- #
# Agent 1 — journal_scout
# --------------------------------------------------------------------------- #
def test_journal_scout_merge_fills_missing():
    arts = [
        RawArticle(title="T", source="pubmed", doi="10.1/m"),
        RawArticle(title="T", source="crossref", doi="10.1/m", abstract="filled", journal="Urology"),
    ]
    merged = journal_scout._merge(arts)
    assert len(merged) == 1
    assert merged[0].abstract == "filled"
    assert merged[0].journal == "Urology"


def test_journal_scout_node(monkeypatch):
    sample = [RawArticle(title="TRT RCT", source="pubmed", doi="10.1/js", journal="J Sex Med",
                         abstract="abstract", authors=[RawAuthor(name="A B")])]
    monkeypatch.setattr(journal_scout.pubmed, "search_recent", lambda *a, **k: sample)
    monkeypatch.setattr(journal_scout.crossref, "search_recent", lambda *a, **k: [])
    monkeypatch.setattr(journal_scout.semantic_scholar, "search_recent", lambda *a, **k: [])
    monkeypatch.setattr(journal_scout.openalex, "search_recent", lambda *a, **k: [])

    out = journal_scout.node({"run_id": "r", "lookback_days": 7})
    assert len(out["article_ids"]) == 1
    with session_scope() as s:
        assert s.scalar(select(Article).where(Article.doi == "10.1/js")) is not None


# --------------------------------------------------------------------------- #
# Agent 2 — scientific_triage
# --------------------------------------------------------------------------- #
def test_scientific_triage_node(monkeypatch, session):
    ids = [_make_article(session, doi=f"10.1/t{i}", is_selected=False) for i in range(3)]
    monkeypatch.setattr(
        scientific_triage, "complete_json",
        lambda *a, **k: {"relevance_score": 90, "study_design": "rct",
                         "priority_area": "andrology", "rationale": "strong"},
    )
    out = scientific_triage.node({"run_id": "r", "article_ids": ids, "top_n": 2})
    assert len(out["selected_ids"]) == 2
    with session_scope() as s:
        selected = s.scalars(select(Article).where(Article.is_selected.is_(True))).all()
        assert len(selected) == 2
        assert all(a.relevance_score and a.relevance_score > 0 for a in selected)


# --------------------------------------------------------------------------- #
# Agent 3 — pdf_collector
# --------------------------------------------------------------------------- #
def test_pdf_collector_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/pdf")
    with session_scope() as s:
        s.get(Article, aid).raw_metadata = {"open_access_pdf": "http://oa/p.pdf"}
    monkeypatch.setattr(pdf_collector, "_download", lambda url: b"%PDF-1.4 minimal")
    out = pdf_collector.node({"run_id": "r", "selected_ids": [aid]})
    assert out["stats"]["pdfs_collected"] == 1
    with session_scope() as s:
        art = s.get(Article, aid)
        assert art.has_full_text is True
        assert art.pdf_path


# --------------------------------------------------------------------------- #
# Agent 4 — full_text_analyzer
# --------------------------------------------------------------------------- #
def test_full_text_analyzer_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/fta")
    monkeypatch.setattr(full_text_analyzer.retriever, "index_text", lambda *a, **k: 0)
    monkeypatch.setattr(
        full_text_analyzer, "complete_json",
        lambda *a, **k: {"objective": "obj", "sample_size": 120, "summary": "sum",
                         "conclusions": "good"},
    )
    out = full_text_analyzer.node({"run_id": "r", "selected_ids": [aid]})
    assert aid in out["analyzed_ids"]
    with session_scope() as s:
        an = s.scalar(select(Analysis).where(Analysis.article_id == aid))
        assert an and an.sample_size == 120 and an.summary == "sum"


# --------------------------------------------------------------------------- #
# Agent 5 — evidence_appraiser
# --------------------------------------------------------------------------- #
def test_evidence_appraiser_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/ea")
    _add_analysis(session, aid)
    monkeypatch.setattr(
        evidence_appraiser, "complete_json",
        lambda *a, **k: {"clinical_impact_score": 8, "evidence_quality_score": 7,
                         "practice_change_probability": 6, "andrology_relevance_score": 9,
                         "risk_of_bias": "low", "appraisal_notes": "solid"},
    )
    out = evidence_appraiser.node({"run_id": "r", "analyzed_ids": [aid]})
    assert aid in out["appraised_ids"]
    with session_scope() as s:
        an = s.scalar(select(Analysis).where(Analysis.article_id == aid))
        assert an.evidence_quality_score == 7 and an.risk_of_bias == "low"


# --------------------------------------------------------------------------- #
# Agent 6 — clinical_practice_translator
# --------------------------------------------------------------------------- #
def test_clinical_translator_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/ct")
    _add_analysis(session, aid)
    monkeypatch.setattr(
        clinical_practice_translator, "complete_json",
        lambda *a, **k: {"changes_practice": True, "what_to_do_differently": "do X",
                         "applies_to_patients": "NOA", "not_applies_to_patients": "OA",
                         "implementation_barriers": "cost", "confidence_level": "moderate"},
    )
    out = clinical_practice_translator.node({"run_id": "r", "appraised_ids": [aid]})
    assert aid in out["translated_ids"]
    with session_scope() as s:
        interp = s.scalar(select(ClinicalInterpretation).where(ClinicalInterpretation.article_id == aid))
        assert interp.changes_practice is True and interp.confidence_level == "moderate"


# --------------------------------------------------------------------------- #
# Agent 8 — guideline_comparator
# --------------------------------------------------------------------------- #
def test_guideline_comparator_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/gc")
    _add_analysis(session, aid)
    with session_scope() as s:
        s.add(ClinicalInterpretation(article_id=aid))
    monkeypatch.setattr(guideline_comparator, "embed_query", lambda q: [0.1, 0.2])
    monkeypatch.setattr(guideline_comparator.vector_store, "search", lambda *a, **k: [])
    monkeypatch.setattr(
        guideline_comparator, "complete_json",
        lambda *a, **k: {"relationship": "extends", "alignment_score": 7,
                         "affected_guidelines": ["EAU: infertility"], "notes": "n"},
    )
    out = guideline_comparator.node({"run_id": "r", "translated_ids": [aid]})
    assert out["stats"]["guideline_comparisons"] == 1
    with session_scope() as s:
        interp = s.scalar(select(ClinicalInterpretation).where(ClinicalInterpretation.article_id == aid))
        assert interp.guideline_relationship == "extends" and interp.guideline_alignment_score == 7


# --------------------------------------------------------------------------- #
# Agent 7 — guideline_tracker
# --------------------------------------------------------------------------- #
def test_guideline_tracker_node(monkeypatch):
    rec = GuidelineRecord(society="EAU", title="Sexual and Reproductive Health 2026",
                          url="https://uroweb.org/g/x", topic="reproduc")
    monkeypatch.setattr(guideline_tracker, "_gather", lambda: [rec])
    monkeypatch.setattr(guideline_tracker, "_fetch_text", lambda url: "guideline body text")
    monkeypatch.setattr(guideline_tracker, "complete", lambda *a, **k: "Resumo clínico.")
    monkeypatch.setattr(guideline_tracker.retriever, "index_text", lambda *a, **k: 0)

    out = guideline_tracker.node({"run_id": "r"})
    assert len(out["guideline_updates"]) == 1
    with session_scope() as s:
        g = s.scalar(select(Guideline).where(Guideline.society == "EAU"))
        assert g is not None and g.summary == "Resumo clínico."


# --------------------------------------------------------------------------- #
# Agent 9 — research_opportunity_scout
# --------------------------------------------------------------------------- #
def test_research_opportunity_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/ro")
    _add_analysis(session, aid)
    monkeypatch.setattr(
        research_opportunity_scout, "complete_json",
        lambda *a, **k: {"knowledge_gaps": ["gap"], "conflicting_results": [],
                         "topics_without_rcts": ["x"], "opportunities": {"meta_analysis": ["m"]}},
    )
    out = research_opportunity_scout.node({"run_id": "r", "analyzed_ids": [aid]})
    assert out["research_opportunities"] and out["research_opportunities"][0]["knowledge_gaps"] == ["gap"]


# --------------------------------------------------------------------------- #
# Agent 10 — personalized_learning
# --------------------------------------------------------------------------- #
def test_personalized_learning_node(session):
    with session_scope() as s:
        s.add_all([
            UserFeedback(event_type="liked", topic="andrology"),
            UserFeedback(event_type="ignored", topic="bph"),
            UserFeedback(event_type="opened", topic="andrology", read_seconds=200),
        ])
    out = personalized_learning.node({"run_id": "r"})
    assert out["stats"]["feedback_events"] == 3
    with session_scope() as s:
        andro = s.scalar(select(TopicWeight).where(TopicWeight.topic == "andrology"))
        bph = s.scalar(select(TopicWeight).where(TopicWeight.topic == "bph"))
        assert andro.weight > 1.0   # positive signal raises weight
        assert bph.weight < 1.0     # negative signal lowers weight


# --------------------------------------------------------------------------- #
# Agent 11 — newsletter_editor
# --------------------------------------------------------------------------- #
def test_newsletter_editor_node(monkeypatch, session):
    aid = _make_article(session, doi="10.1/nl")
    _add_analysis(session, aid)
    with session_scope() as s:
        s.add(ClinicalInterpretation(article_id=aid, changes_practice=True, what_to_do_differently="x"))
    monkeypatch.setattr(newsletter_editor, "complete", lambda *a, **k: "# Urology Intelligence Weekly\n\nConteúdo.")
    monkeypatch.setattr(newsletter_editor.retriever, "index_text", lambda *a, **k: 0)

    out = newsletter_editor.node({"run_id": "rnl", "analyzed_ids": [aid],
                                  "guideline_updates": [], "research_opportunities": []})
    assert out["newsletter_id"]
    assert "markdown" in out["newsletter_paths"]
    with session_scope() as s:
        nl = s.get(Newsletter, out["newsletter_id"])
        assert nl and nl.content_markdown.startswith("# Urology Intelligence Weekly")


# --------------------------------------------------------------------------- #
# Agent 12 — newsletter_dispatcher (channel-aware)
# --------------------------------------------------------------------------- #
def _make_newsletter() -> int:
    with session_scope() as s:
        nl = Newsletter(run_id="r", issue_date=__import__("datetime").date.today(),
                        title="Urology Intelligence Weekly — teste")
        s.add(nl)
        s.flush()
        return nl.id


def test_newsletter_dispatcher_none_configured(session):
    # Default channel is email, but no SMTP password configured -> no-op.
    nid = _make_newsletter()
    out = newsletter_dispatcher.node({"run_id": "r", "newsletter_id": nid, "newsletter_paths": {}})
    assert out["dispatched"] is False


def test_newsletter_dispatcher_email(monkeypatch, session):
    monkeypatch.setattr(settings, "delivery_channel", "email", raising=False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_user", "me@example.com", raising=False)
    monkeypatch.setattr(settings, "smtp_password", "app-pass", raising=False)

    sent = {}

    def fake_send(**kwargs):
        sent.update(kwargs)
        return {"recipients": ["me@example.com"]}

    monkeypatch.setattr(newsletter_dispatcher.email_integration, "send_newsletter", fake_send)
    nid = _make_newsletter()
    out = newsletter_dispatcher.node({"run_id": "r", "newsletter_id": nid,
                                      "newsletter_paths": {}, "executive_summary": "resumo exec"})
    assert out["dispatched"] is True
    assert out["stats"]["channels"] == {"email": True}
    assert "Urology Intelligence Weekly" in sent["subject"]
    assert sent["text_body"] == "resumo exec"
    with session_scope() as s:
        assert s.get(Newsletter, nid).dispatched is True


def test_newsletter_dispatcher_whatsapp(monkeypatch, session):
    monkeypatch.setattr(settings, "delivery_channel", "whatsapp", raising=False)
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "P", raising=False)
    monkeypatch.setattr(settings, "whatsapp_access_token", "T", raising=False)
    monkeypatch.setattr(settings, "whatsapp_recipient", "5511", raising=False)

    sent = {}

    class FakeWA:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_text(self, body, to=None):
            sent["text"] = body
            return {"ok": True}

        def upload_media(self, *a, **k):
            return "media1"

        def send_document(self, *a, **k):
            sent["doc"] = True
            return {"ok": True}

    monkeypatch.setattr(newsletter_dispatcher, "WhatsAppClient", FakeWA)
    nid = _make_newsletter()
    out = newsletter_dispatcher.node({"run_id": "r", "newsletter_id": nid,
                                      "newsletter_paths": {}, "executive_summary": "resumo"})
    assert out["dispatched"] is True
    assert "resumo" in sent["text"]
    with session_scope() as s:
        assert s.get(Newsletter, nid).dispatched is True
