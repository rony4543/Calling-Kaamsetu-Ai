"""
WhatsApp Cloud API webhook — the single entry point for all inbound messages.

  GET  /whatsapp/webhook  → verification handshake (hub.challenge echo).
  POST /whatsapp/webhook  → receive messages; normalize each into an
                            InboundMessage and hand it to the Router as a
                            background task, then return 200 immediately.

WhatsApp requires a fast 2xx or it retries (and eventually disables) the
webhook, so all real work is deferred to a FastAPI BackgroundTask. Duplicate
deliveries are dropped via the idempotency store keyed on wa_message_id.
"""

from __future__ import annotations

import logging
from typing import Iterator

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse

from app.config import get_config
from app.orchestrator.router import InboundMessage, get_router
from app.utils.idempotency import is_duplicate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


# ── Verification (GET) ──────────────────────────────────────────────────────
@router.get("/webhook")
async def verify(request: Request) -> PlainTextResponse:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token and token == get_config().whatsapp.verify_token:
        logger.info("WhatsApp webhook verified")
        return PlainTextResponse(challenge or "")
    logger.warning("WhatsApp webhook verification failed (mode=%s)", mode)
    return PlainTextResponse("Forbidden", status_code=403)


# ── Receive (POST) ──────────────────────────────────────────────────────────
@router.post("/webhook")
async def receive(request: Request, background_tasks: BackgroundTasks) -> PlainTextResponse:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Malformed webhook body: %s", exc)
        return PlainTextResponse("EVENT_RECEIVED")

    try:
        for inbound in _extract_messages(body):
            if is_duplicate(inbound.message_id):
                continue
            background_tasks.add_task(get_router().handle, inbound)
    except Exception as exc:  # noqa: BLE001 — always ack, never 5xx to Meta
        logger.exception("Failed to dispatch webhook: %s", exc)

    return PlainTextResponse("EVENT_RECEIVED")


# ── Parsing ─────────────────────────────────────────────────────────────────
def _extract_messages(body: dict) -> Iterator[InboundMessage]:
    """Walk the WhatsApp payload and yield one InboundMessage per user message."""
    for entry in body.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            # Ignore delivery/read status callbacks — only real messages route.
            for msg in value.get("messages", []) or []:
                parsed = _parse_message(msg)
                if parsed:
                    yield parsed


def _parse_message(msg: dict) -> InboundMessage | None:
    wa_id = msg.get("from")
    mid = msg.get("id")
    if not wa_id or not mid:
        return None
    mtype = msg.get("type", "text")

    text = ""
    interactive_id = None
    media_id = None
    media_mime = None

    if mtype == "text":
        text = (msg.get("text") or {}).get("body", "")

    elif mtype == "interactive":
        inter = msg.get("interactive") or {}
        itype = inter.get("type")  # "button_reply" | "list_reply"
        payload = inter.get(itype, {}) if itype else {}
        interactive_id = payload.get("id")
        text = payload.get("title", "")

    elif mtype == "button":  # template quick-reply button
        text = (msg.get("button") or {}).get("text", "")

    elif mtype in ("audio", "voice"):
        media = msg.get(mtype) or {}
        media_id = media.get("id")
        media_mime = media.get("mime_type")
        mtype = "voice"

    elif mtype == "image":
        media = msg.get("image") or {}
        media_id = media.get("id")
        media_mime = media.get("mime_type")
        text = media.get("caption", "")

    elif mtype == "document":
        media = msg.get("document") or {}
        media_id = media.get("id")
        media_mime = media.get("mime_type")
        text = media.get("caption", "")

    return InboundMessage(
        wa_id=wa_id,
        message_id=mid,
        type=mtype,
        text=text,
        interactive_id=interactive_id,
        media_id=media_id,
        media_mime=media_mime,
    )
