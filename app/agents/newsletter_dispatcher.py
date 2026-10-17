"""Agent 12 — Newsletter Dispatcher.

Delivers the weekly issue through the configured channel(s): email (default),
WhatsApp, or both (``DELIVERY_CHANNEL``). Email sends the rendered HTML as the
body with the PDF attached; WhatsApp sends the executive summary plus the PDF as
media. Marks the newsletter dispatched when at least one channel succeeds, and
no-ops gracefully when a channel is not configured.
"""
from __future__ import annotations

from pathlib import Path

from app.agents.common import audit
from app.config import settings
from app.database.base import utcnow
from app.database.models import Newsletter
from app.database.session import session_scope
from app.integrations import email as email_integration
from app.integrations.email import EmailError
from app.integrations.whatsapp import WhatsAppClient, WhatsAppError
from app.utils.logger import bind_run, get_logger
from app.utils.metrics import NEWSLETTERS_SENT, track_agent
from app.workflows.state import PipelineState

log = get_logger("newsletter_dispatcher")
AGENT = "newsletter_dispatcher"


def _load_html_body(html_path: str | None, content_markdown: str | None) -> str:
    """Prefer the rendered HTML file; fall back to markdown→HTML or a stub."""
    if html_path and Path(html_path).exists():
        try:
            return Path(html_path).read_text(encoding="utf-8")
        except OSError:
            pass
    if content_markdown:
        try:
            import markdown as _md

            return _md.markdown(content_markdown, extensions=["extra", "sane_lists", "tables"])
        except Exception:  # pragma: no cover - markdown is a hard dep, defensive only
            return f"<pre>{content_markdown}</pre>"
    return "<p>Urology Intelligence Weekly.</p>"


def _dispatch_email(subject: str, html_body: str, pdf_path: str | None, executive_summary: str) -> tuple[bool, str]:
    if not settings.email_enabled:
        log.warning("email_disabled")
        return False, "email not configured"
    try:
        email_integration.send_newsletter(
            subject=subject, html_body=html_body, text_body=executive_summary, pdf_path=pdf_path
        )
        return True, "sent"
    except EmailError as exc:
        log.error("email_dispatch_failed", error=str(exc))
        return False, str(exc)


def _dispatch_whatsapp(pdf_path: str | None, executive_summary: str) -> tuple[bool, str]:
    if not settings.whatsapp_enabled:
        log.warning("whatsapp_disabled")
        return False, "whatsapp not configured"
    header = "📚 *Urology Intelligence Weekly*\n\n"
    try:
        with WhatsAppClient() as wa:
            wa.send_text(header + executive_summary)
            if pdf_path and Path(pdf_path).exists():
                media_id = wa.upload_media(pdf_path, "application/pdf")
                wa.send_document(media_id=media_id, filename=Path(pdf_path).name,
                                 caption="Newsletter completa (PDF) — Urology Intelligence Weekly")
        return True, "sent"
    except WhatsAppError as exc:
        log.error("whatsapp_dispatch_failed", error=str(exc))
        NEWSLETTERS_SENT.labels(channel="whatsapp", status="error").inc()
        return False, str(exc)


def node(state: PipelineState) -> dict:
    run_id = state["run_id"]
    bind_run(run_id, AGENT)
    newsletter_id = state.get("newsletter_id")
    paths = state.get("newsletter_paths", {})
    executive_summary = state.get("executive_summary") or "Sua newsletter Urology Intelligence Weekly está pronta."

    with track_agent(AGENT):
        # If the editor failed (e.g. timed out), there is no issue to send — skip
        # instead of dispatching an empty stub.
        if not newsletter_id:
            log.warning("dispatch_skipped_no_newsletter")
            with session_scope() as session:
                audit(session, run_id, AGENT, "dispatch_skipped",
                      message="no newsletter generated", level="WARNING")
            return {"dispatched": False, "stats": {"dispatched": False, "reason": "no_newsletter"}}

        # Snapshot the newsletter fields we need (attributes stay valid after the
        # session closes because expire_on_commit=False).
        with session_scope() as session:
            newsletter = session.get(Newsletter, newsletter_id) if newsletter_id else None
            subject = (newsletter.title if newsletter else None) or "Urology Intelligence Weekly"
            html_path = paths.get("html") or (newsletter.html_path if newsletter else None)
            pdf_path = paths.get("pdf") or (newsletter.pdf_path if newsletter else None)
            content_markdown = newsletter.content_markdown if newsletter else None

        html_body = _load_html_body(html_path, content_markdown)

        results: dict[str, tuple[bool, str]] = {}
        if settings.wants_email:
            results["email"] = _dispatch_email(subject, html_body, pdf_path, executive_summary)
        if settings.wants_whatsapp:
            results["whatsapp"] = _dispatch_whatsapp(pdf_path, executive_summary)
        if not results:  # misconfigured DELIVERY_CHANNEL -> default to email
            results["email"] = _dispatch_email(subject, html_body, pdf_path, executive_summary)

        dispatched = any(ok for ok, _ in results.values())

        with session_scope() as session:
            if newsletter_id and dispatched:
                nl = session.get(Newsletter, newsletter_id)
                if nl:
                    nl.dispatched = True
                    nl.dispatched_at = utcnow()
            audit(
                session, run_id, AGENT, "dispatch_complete",
                message="; ".join(f"{k}={'ok' if ok else 'fail'}" for k, (ok, _) in results.items()),
                level="INFO" if dispatched else "WARNING",
                payload={k: {"ok": ok, "detail": d} for k, (ok, d) in results.items()},
            )

    log.info("newsletter_dispatcher_done", dispatched=dispatched, channels=list(results))
    return {
        "dispatched": dispatched,
        "stats": {"dispatched": dispatched, "channels": {k: ok for k, (ok, _) in results.items()}},
    }
