"""Email delivery via SMTP (e.g. Gmail with an app password).

Uses the Python standard library only (``smtplib`` + ``email.message``), so no
extra dependency is required. Sends a multipart message with an HTML body and an
optional PDF attachment — a natural fit for the newsletter, which is already
rendered as HTML + PDF.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from app.config import settings
from app.utils.logger import get_logger
from app.utils.metrics import NEWSLETTERS_SENT

log = get_logger("email")


class EmailError(RuntimeError):
    pass


def _recipients(to: str | Iterable[str] | None) -> list[str]:
    if to is None:
        to = settings.email_to or settings.smtp_user
    if isinstance(to, str):
        parts = [p.strip() for p in to.replace(";", ",").split(",")]
    else:
        parts = [str(p).strip() for p in to]
    recipients = [p for p in parts if p]
    if not recipients:
        raise EmailError("no email recipients configured (set EMAIL_TO or SMTP_USER)")
    return recipients


def send_newsletter(
    *,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    pdf_path: str | Path | None = None,
    to: str | Iterable[str] | None = None,
) -> dict:
    """Send the weekly newsletter by email. Raises ``EmailError`` if misconfigured."""
    if not settings.email_enabled:
        raise EmailError(
            "email is not configured (set SMTP_HOST / SMTP_USER / SMTP_PASSWORD)."
        )

    recipients = _recipients(to)
    sender = settings.email_from or settings.smtp_user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    # Plain-text fallback first, then the HTML alternative.
    msg.set_content(text_body or "Sua newsletter Urology Intelligence Weekly está no corpo HTML (e em anexo, se houver).")
    msg.add_alternative(html_body, subtype="html")

    if pdf_path:
        path = Path(pdf_path)
        if path.exists():
            msg.add_attachment(
                path.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=path.name,
            )
        else:
            log.warning("email_pdf_missing", path=str(path))

    context = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:  # implicit TLS
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                  context=context, timeout=settings.http_timeout_seconds) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
        else:  # STARTTLS (e.g. Gmail on 587)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                              timeout=settings.http_timeout_seconds) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        NEWSLETTERS_SENT.labels(channel="email", status="error").inc()
        raise EmailError(f"SMTP send failed: {exc}") from exc

    NEWSLETTERS_SENT.labels(channel="email", status="sent").inc()
    log.info("email_sent", to=recipients, subject=subject, has_pdf=bool(pdf_path))
    return {"recipients": recipients}
