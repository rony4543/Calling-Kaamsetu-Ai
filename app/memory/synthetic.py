"""
Tier 3 — Synthetic memory store (synthetic_profiles).

The agent's INFERRED read of a person: soft skills, tone, reliability, urgency.
Versioned — we never overwrite, we mark the old row is_current=false and insert a
new current row (version+1) — so we can watch how our read of someone drifts.

There is no SyntheticRepo in repositories.py, so this module talks to the table
directly (it's the only writer/reader of synthetic_profiles).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.supabase.client import get_supabase_client

logger = logging.getLogger(__name__)


def get_current(user_id: str) -> Optional[dict]:
    """Return the current synthetic profile row for a user, or None."""
    if not user_id:
        return None
    db = get_supabase_client()
    try:
        resp = (
            db.table("synthetic_profiles")
            .select("*")
            .eq("user_id", user_id)
            .eq("is_current", True)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_current synthetic failed: %s", exc)
        return None


def save(user_id: str, data: dict, model: Optional[str] = None) -> None:
    """
    Save a new current synthetic profile, versioning the previous one out.

    `data` keys: soft_skills[list], tone, reliability_signal, urgency, summary,
    personality[dict].
    """
    if not user_id:
        return
    db = get_supabase_client()
    try:
        current = get_current(user_id)
        version = (current.get("version", 0) + 1) if current else 1
        # Retire the old current row first (unique index allows one current/user).
        db.table("synthetic_profiles").update({"is_current": False}).eq(
            "user_id", user_id
        ).eq("is_current", True).execute()
        db.table("synthetic_profiles").insert(
            {
                "user_id": user_id,
                "soft_skills": data.get("soft_skills", []) or [],
                "tone": data.get("tone"),
                "reliability_signal": data.get("reliability_signal"),
                "urgency": data.get("urgency"),
                "summary": data.get("summary"),
                "personality": data.get("personality", {}) or {},
                "model": model,
                "version": version,
                "is_current": True,
            }
        ).execute()
        logger.info("Saved synthetic profile v%s for user %s", version, user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("save synthetic failed: %s", exc)
