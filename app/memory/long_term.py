"""
Tier 1 — Long-term memory (the source of truth) + the semantic index.

Two jobs:
  1. Convenience save/load of the typed entities (candidate, job) on top of the
     repositories, so agents don't import the private row-mapping helpers.
  2. The "know everything about the user" layer:
       - record_fact(): append a temporal fact to memory_facts, superseding any
         current value for the same (user, sector, key).
       - index_text(): embed arbitrary text and drop it into memory_chunks for
         pgvector recall.
       - recall(): semantic top-k recall via the match_memory() RPC.

Everything here is defensive: a failed embedding or fact write logs and returns,
it never breaks the conversation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.integrations.openai_client import get_openai_client
from app.supabase.client import get_supabase_client
from app.supabase.repositories import (
    CandidateRepo,
    JobRepo,
    _candidate_to_db,
    _job_to_db,
)
from app.supabase.schemas import Candidate, Job

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Typed entities ───────────────────────────────────────────────────────────


def get_candidate(wa_id: str) -> Optional[Candidate]:
    return CandidateRepo.get_by_wa_id(wa_id)


def save_candidate(candidate: Candidate) -> None:
    """Persist the full candidate model back to candidate_profiles."""
    if not candidate.id:
        logger.warning("save_candidate called with no id; skipping")
        return
    CandidateRepo.update(candidate.id, _candidate_to_db(candidate))


def get_job(job_id: str) -> Optional[Job]:
    return JobRepo.get(job_id)


def save_job(job: Job) -> None:
    if not job.id:
        logger.warning("save_job called with no id; skipping")
        return
    JobRepo.update(job.id, _job_to_db(job))


# ── Flexible temporal fact store (memory_facts) ──────────────────────────────


def record_fact(
    user_id: str,
    key: str,
    value,
    sector: str = "other",
    source: str = "extracted",
    confidence: float = 0.7,
) -> None:
    """
    Write a fact, superseding any current value for (user, sector, key).
    `value` is stored as jsonb, so pass JSON-serialisable data.
    """
    if not user_id:
        return
    db = get_supabase_client()
    try:
        # Close out the currently-valid value, if any.
        db.table("memory_facts").update({"valid_to": _now()}).eq("user_id", user_id).eq(
            "sector", sector
        ).eq("key", key).is_("valid_to", "null").execute()
        # Insert the new current value.
        db.table("memory_facts").insert(
            {
                "user_id": user_id,
                "sector": sector,
                "key": key,
                "value": value,
                "confidence": confidence,
                "source": source,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_fact failed (%s=%s): %s", key, value, exc)


# ── Semantic index (memory_chunks + pgvector) ────────────────────────────────


def index_text(
    user_id: str,
    content: str,
    source_type: str,
    source_id: Optional[str] = None,
    sector: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Embed `content` and store it in memory_chunks for semantic recall."""
    if not user_id or not content:
        return
    embedding = get_openai_client().embed(content)
    if not embedding:
        return
    db = get_supabase_client()
    try:
        db.table("memory_chunks").insert(
            {
                "user_id": user_id,
                "source_type": source_type,
                "source_id": source_id,
                "sector": sector,
                "content": content,
                "embedding": embedding,
                "metadata": metadata or {},
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("index_text failed: %s", exc)


def recall(user_id: str, query: str, k: int = 8, min_similarity: float = 0.0) -> list[dict]:
    """Semantic top-k recall of this user's memories relevant to `query`."""
    if not user_id or not query:
        return []
    embedding = get_openai_client().embed(query)
    if not embedding:
        return []
    db = get_supabase_client()
    try:
        resp = db.rpc(
            "match_memory",
            {
                "p_user_id": user_id,
                "p_query_embedding": embedding,
                "p_match_count": k,
                "p_min_similarity": min_similarity,
            },
        ).execute()
        return resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall failed: %s", exc)
        return []
