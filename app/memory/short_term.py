"""
Tier 0 — Working / short-term memory.

A thin ring buffer of the last few conversation turns, stored on the user's
`sessions` row (short_term jsonb). Gives agents just enough context for
conversational continuity without re-reading the full transcript every turn.
Also the single place flow state (active_flow, expected_field, role) is nudged.
"""

from __future__ import annotations

import logging

from app.supabase.repositories import SessionRepo
from app.supabase.schemas import ActiveFlow, Session, ShortTermMessage, UserRole

logger = logging.getLogger(__name__)

MAX_TURNS = 6  # keep the buffer tiny — long-term truth lives elsewhere


def get_or_create(wa_id: str, role: UserRole = UserRole.UNKNOWN) -> Session:
    session = SessionRepo.get(wa_id)
    if session:
        return session
    SessionRepo.create(Session(wa_id=wa_id, role=role))
    return SessionRepo.get(wa_id) or Session(wa_id=wa_id, role=role)


def record(wa_id: str, role: str, text: str) -> None:
    """Append one turn (role='user'|'assistant') and trim to the last MAX_TURNS."""
    if not text:
        return
    session = get_or_create(wa_id)
    session.short_term.append(ShortTermMessage(role=role, text=text[:1000]))
    session.short_term = session.short_term[-MAX_TURNS:]
    SessionRepo.update(wa_id, {"short_term": [m.model_dump(mode="json") for m in session.short_term]})


def as_text(session: Session | None) -> str:
    """Render the ring buffer as 'role: text' lines for a prompt."""
    if not session or not session.short_term:
        return "(no prior messages)"
    return "\n".join(f"{m.role}: {m.text}" for m in session.short_term)


def set_expected_field(wa_id: str, field: str | None) -> None:
    SessionRepo.update(wa_id, {"expected_field": field})


def set_flow(wa_id: str, flow: str | ActiveFlow, agent: str | None = None) -> None:
    flow_value = flow.value if isinstance(flow, ActiveFlow) else flow
    SessionRepo.set_flow(wa_id, flow_value, agent)


def set_role(wa_id: str, role: str | UserRole) -> None:
    role_value = role.value if isinstance(role, UserRole) else role
    SessionRepo.update(wa_id, {"role": role_value})
