"""Meta WhatsApp Cloud API integration.

Supports: free-form text, media upload (PDF), document send (by uploaded media
id or by public link), and pre-approved template messages.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings
from app.integrations.base import HttpClient
from app.utils.logger import get_logger
from app.utils.metrics import NEWSLETTERS_SENT

log = get_logger("whatsapp")

GRAPH = "https://graph.facebook.com"
TEXT_LIMIT = 4096


class WhatsAppError(RuntimeError):
    pass


class WhatsAppClient:
    def __init__(self) -> None:
        self.version = settings.whatsapp_api_version
        self.phone_id = settings.whatsapp_phone_number_id
        self.token = settings.whatsapp_access_token
        self.recipient = settings.whatsapp_recipient
        self._http = HttpClient(
            "whatsapp",
            # Trailing slash + relative paths so httpx preserves the /vXX.X segment
            # when joining (RFC 3986 base-url merging).
            base_url=f"{GRAPH}/{self.version}/",
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WhatsAppClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _ensure_configured(self) -> None:
        if not settings.whatsapp_enabled:
            raise WhatsAppError(
                "WhatsApp is not configured (set WHATSAPP_ACCESS_TOKEN / "
                "WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_RECIPIENT)."
            )

    def send_text(self, body: str, to: str | None = None) -> dict[str, Any]:
        self._ensure_configured()
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to or self.recipient,
            "type": "text",
            "text": {"preview_url": True, "body": body[:TEXT_LIMIT]},
        }
        resp = self._http.post(f"{self.phone_id}/messages", json=payload)
        NEWSLETTERS_SENT.labels(channel="whatsapp_text", status="sent").inc()
        log.info("whatsapp_text_sent", to=payload["to"], chars=len(body))
        return resp.json()

    def upload_media(self, file_path: str | Path, mime_type: str = "application/pdf") -> str:
        self._ensure_configured()
        path = Path(file_path)
        if not path.exists():
            raise WhatsAppError(f"media file not found: {path}")
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, mime_type)}
            data = {"messaging_product": "whatsapp", "type": mime_type}
            resp = self._http.post(f"{self.phone_id}/media", data=data, files=files)
        media_id = resp.json().get("id")
        if not media_id:
            raise WhatsAppError(f"media upload returned no id: {resp.text}")
        log.info("whatsapp_media_uploaded", media_id=media_id, file=path.name)
        return media_id

    def send_document(
        self,
        *,
        media_id: str | None = None,
        link: str | None = None,
        filename: str = "newsletter.pdf",
        caption: str | None = None,
        to: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_configured()
        if not media_id and not link:
            raise WhatsAppError("send_document requires either media_id or link")
        document: dict[str, Any] = {"filename": filename}
        if media_id:
            document["id"] = media_id
        else:
            document["link"] = link
        if caption:
            document["caption"] = caption[:1024]
        payload = {
            "messaging_product": "whatsapp",
            "to": to or self.recipient,
            "type": "document",
            "document": document,
        }
        resp = self._http.post(f"{self.phone_id}/messages", json=payload)
        NEWSLETTERS_SENT.labels(channel="whatsapp_document", status="sent").inc()
        log.info("whatsapp_document_sent", to=payload["to"], filename=filename)
        return resp.json()

    def send_template(self, name: str, language: str = "pt_BR", components: list[dict] | None = None) -> dict[str, Any]:
        self._ensure_configured()
        payload = {
            "messaging_product": "whatsapp",
            "to": self.recipient,
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": language},
                "components": components or [],
            },
        }
        resp = self._http.post(f"{self.phone_id}/messages", json=payload)
        log.info("whatsapp_template_sent", template=name)
        return resp.json()
