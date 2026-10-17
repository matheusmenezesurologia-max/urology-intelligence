"""Agent 11 — Newsletter Editor.

Assembles the week's curated evidence into a structured digest, generates the
"Urology Intelligence Weekly" newsletter (markdown) via LLM, renders HTML and
PDF, persists the issue, and indexes it into the RAG store.
"""
from __future__ import annotations

import json
from datetime import date

import markdown as md
from sqlalchemy import select

from app.agents.common import audit, load_prompt
from app.config import NEWSLETTER_DIR, settings
from app.database.models import Article, Newsletter
from app.database.session import session_scope
from app.rag import retriever
from app.utils.llm import LLMError, complete
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import track_agent
from app.workflows.state import PipelineState

log = get_logger("newsletter_editor")
AGENT = "newsletter_editor"

EXEC_SUMMARY_SYSTEM = (
    "Resuma a newsletter abaixo em até 6 linhas (pt-BR), formato executivo para "
    "envio por WhatsApp: os 2-3 destaques práticos da semana em andrologia/medicina "
    "sexual/reprodução, com tom direto. Sem markdown pesado, use no máximo emojis "
    "pontuais e bullets simples."
)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #1a2238;
         max-width: 820px; margin: 40px auto; padding: 0 24px; line-height: 1.55; }}
  h1 {{ color: #0b3d91; border-bottom: 3px solid #0b3d91; padding-bottom: 8px; }}
  h2 {{ color: #0b3d91; margin-top: 32px; border-left: 4px solid #2a9d8f; padding-left: 10px; }}
  h3 {{ color: #264653; }}
  a {{ color: #2a6f97; }}
  code, pre {{ background: #f4f6fb; padding: 2px 5px; border-radius: 4px; }}
  blockquote {{ border-left: 4px solid #e9c46a; margin: 12px 0; padding: 6px 14px; background: #fffdf5; }}
  .footer {{ margin-top: 48px; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; padding-top: 12px; }}
</style></head>
<body>
{body}
<div class="footer">Urology Intelligence — gerado automaticamente em {date}. Uso clínico-científico pessoal.</div>
</body></html>"""


def _article_digest(article: Article) -> dict:
    an = article.analysis
    interp = article.interpretation
    return {
        "title": article.title,
        "journal": article.journal.name if article.journal else None,
        "doi": article.doi,
        "url": article.url,
        "study_design": article.study_design,
        "relevance_score": article.relevance_score,
        "summary": (an.summary if an else None) or (article.abstract or "")[:600],
        "scores": {
            "clinical_impact": an.clinical_impact_score if an else None,
            "evidence_quality": an.evidence_quality_score if an else None,
            "practice_change_probability": an.practice_change_probability if an else None,
            "andrology_relevance": an.andrology_relevance_score if an else None,
            "risk_of_bias": an.risk_of_bias if an else None,
        } if an else {},
        "clinical": {
            "changes_practice": interp.changes_practice if interp else None,
            "what_to_do_differently": interp.what_to_do_differently if interp else None,
            "applies_to": interp.applies_to_patients if interp else None,
            "confidence": interp.confidence_level if interp else None,
            "guideline_relationship": interp.guideline_relationship if interp else None,
            "guideline_alignment_score": interp.guideline_alignment_score if interp else None,
        } if interp else {},
    }


def _render_and_save(run_id: str, markdown_text: str, issue_date: date) -> dict[str, str]:
    paths: dict[str, str] = {}
    base = NEWSLETTER_DIR / f"newsletter_{run_id}"

    md_path = base.with_suffix(".md")
    md_path.write_text(markdown_text, encoding="utf-8")
    paths["markdown"] = str(md_path)

    body_html = md.markdown(markdown_text, extensions=["extra", "sane_lists", "tables"])
    full_html = HTML_TEMPLATE.format(title="Urology Intelligence Weekly", body=body_html, date=issue_date.isoformat())
    html_path = base.with_suffix(".html")
    html_path.write_text(full_html, encoding="utf-8")
    paths["html"] = str(html_path)

    try:  # PDF rendering depends on native libs; degrade gracefully if unavailable.
        from weasyprint import HTML

        pdf_path = base.with_suffix(".pdf")
        HTML(string=full_html).write_pdf(str(pdf_path))
        paths["pdf"] = str(pdf_path)
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("pdf_render_failed", error=str(exc))

    return paths


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    prompt = load_prompt("newsletter")
    issue_date = date.today()
    ids = state.get("analyzed_ids", []) or state.get("selected_ids", [])

    with track_agent(AGENT):
        with session_scope() as session:
            articles = session.scalars(
                select(Article).where(Article.id.in_(ids))
            ).all() if ids else []
            # Order by relevance for the digest.
            articles.sort(key=lambda a: a.relevance_score or 0, reverse=True)
            digest = {
                "issue_date": issue_date.isoformat(),
                "articles": [_article_digest(a) for a in articles],
                "guideline_updates": state.get("guideline_updates", []),
                "research_opportunities": state.get("research_opportunities", []),
            }
            article_ids = [a.id for a in articles]

        # Generate the newsletter markdown.
        try:
            markdown_text = complete(
                prompt,
                "Digest JSON desta semana:\n" + json.dumps(digest, ensure_ascii=False, indent=2),
                model=settings.newsletter_model,
                temperature=0.4,
                max_tokens=4000,
            )
        except LLMError as exc:
            log.error("newsletter_generation_failed", error=str(exc))
            markdown_text = f"# Urology Intelligence Weekly\n\n_Falha ao gerar a newsletter: {exc}_"

        # Executive summary for WhatsApp.
        try:
            executive_summary = complete(
                EXEC_SUMMARY_SYSTEM, markdown_text, model=settings.newsletter_model,
                temperature=0.3, max_tokens=400,
            )
        except LLMError:
            executive_summary = markdown_text.split("\n\n", 1)[0][:600]

        paths = _render_and_save(run_id, markdown_text, issue_date)

        with session_scope() as session:
            newsletter = Newsletter(
                run_id=run_id,
                issue_date=issue_date,
                title=f"Urology Intelligence Weekly — {issue_date.isoformat()}",
                markdown_path=paths.get("markdown"),
                html_path=paths.get("html"),
                pdf_path=paths.get("pdf"),
                executive_summary=executive_summary,
                content_markdown=markdown_text,
            )
            session.add(newsletter)
            session.flush()
            newsletter_id = newsletter.id

            for aid in article_ids:
                article = session.get(Article, aid)
                if article:
                    article.newsletter_id = newsletter_id

            try:
                retriever.index_text(
                    session,
                    source_type="newsletter",
                    source_id=newsletter_id,
                    title=newsletter.title,
                    text=markdown_text,
                )
            except Exception as exc:
                log.warning("newsletter_index_failed", error=str(exc))

            audit(session, run_id, AGENT, "newsletter_complete",
                  message=f"issue {newsletter_id} with {len(article_ids)} articles",
                  payload={"paths": paths})

    log.info("newsletter_editor_done", newsletter_id=newsletter_id, paths=list(paths))
    return {
        "newsletter_id": newsletter_id,
        "newsletter_paths": paths,
        "executive_summary": executive_summary,
        "stats": {"newsletter_id": newsletter_id, "newsletter_articles": len(article_ids)},
    }
