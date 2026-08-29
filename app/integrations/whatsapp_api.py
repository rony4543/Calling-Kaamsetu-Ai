"""
WhatsApp Cloud API client — all outbound messaging + media download.

Thin synchronous wrapper over the Graph API (httpx). Returns the provider
message id on success, None on failure (we log and move on — a failed send
should never crash a webhook).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import httpx

from app.config import get_config

logger = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self) -> None:
        self._cfg = get_config().whatsapp
        self._headers = {
            "Authorization": f"Bearer {self._cfg.api_token}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict) -> Optional[str]:
        try:
            resp = httpx.post(self._cfg.api_url, json=payload, headers=self._headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("messages", [{}])[0].get("id")
        except Exception as exc:  # noqa: BLE001
            logger.error("WhatsApp send failed: %s", exc)
            return None

    # ── Sending ───────────────────────────────────────────────────────────────
    def send_text(self, to: str, body: str) -> Optional[str]:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": body[:4096]},
            }
        )

    def send_buttons(self, to: str, body: str, buttons: list[dict]) -> Optional[str]:
        """Reply buttons (max 3). Each button: {"id": "...", "title": "<=20 chars"}."""
        rows = [
            {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
            for b in buttons[:3]
        ]
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:1024]},
                    "action": {"buttons": rows},
                },
            }
        )

    def send_list(
        self, to: str, body: str, button_text: str, sections: list[dict]
    ) -> Optional[str]:
        """List message. sections: [{"title", "rows": [{"id","title","description"?}]}]."""
        return self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": body[:1024]},
                    "action": {"button": button_text[:20], "sections": sections},
                },
            }
        )

    def mark_read(self, message_id: str) -> None:
        try:
            httpx.post(
                self._cfg.api_url,
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
                headers=self._headers,
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("mark_read failed (non-fatal): %s", exc)

    # ── Media download (voice notes, documents, images) ───────────────────────
    def download_media(self, media_id: str) -> tuple[Optional[bytes], Optional[str]]:
        """
        Two-step download: resolve the media id to a URL, then fetch the bytes.
        Returns (content, mime_type) or (None, None) on failure.
        """
        try:
            meta = httpx.get(
                f"{self._cfg.media_url}/{media_id}",
                headers={"Authorization": f"Bearer {self._cfg.api_token}"},
                timeout=30,
            )
            meta.raise_for_status()
            info = meta.json()
            url = info.get("url")
            mime = info.get("mime_type")
            if not url:
                return None, None
            blob = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self._cfg.api_token}"},
                timeout=60,
            )
            blob.raise_for_status()
            return blob.content, mime
        except Exception as exc:  # noqa: BLE001
            logger.error("Media download failed for %s: %s", media_id, exc)
            return None, None


@lru_cache()
def get_whatsapp_client() -> WhatsAppClient:
    """Cached singleton WhatsApp client."""
    return WhatsAppClient()
