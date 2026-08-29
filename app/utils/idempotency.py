"""
Idempotency — WhatsApp re-delivers webhooks, so the same inbound message can
arrive several times. We record every processed wa_message_id in the
`idempotency_keys` table (key = wa_message_id) and skip anything we've seen.

State lives in Postgres (not memory) so this holds across restarts and multiple
workers — one of the app's core design principles.
"""

from __future__ import annotations

import logging

from app.supabase.client import get_supabase_client

logger = logging.getLogger(__name__)


def is_duplicate(message_id: str | None) -> bool:
    """
    Return True if this message was already processed (and should be skipped).

    On first sight, records the key and returns False. Fails OPEN (returns
    False) if the check itself errors, so a transient DB hiccup never silently
    drops a real user message.
    """
    if not message_id:
        return False
    db = get_supabase_client()
    try:
        existing = db.table("idempotency_keys").select("key").eq("key", message_id).execute()
        if existing.data:
            logger.info("Duplicate message skipped: %s", message_id)
            return True
        db.table("idempotency_keys").insert({"key": message_id}).execute()
        return False
    except Exception as exc:  # noqa: BLE001 — never let dedupe crash the webhook
        # A unique-violation here means a concurrent worker inserted it first:
        # treat as duplicate. Any other error: fail open.
        msg = str(exc).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            return True
        logger.warning("Idempotency check failed open for %s: %s", message_id, exc)
        return False
