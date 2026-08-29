"""
Flow transitions + role detection.

The conversation is a small state machine over `active_flow`:

    welcome ──(role chosen)──> candidate_intake ─┐
        │                                         ├─(profile complete)─> idle
        └────(role chosen)──> employer_intake ───┘        │
                                                            ├─(match proposed)─> optin
    optin ──(reply handled)──> idle                        │
    idle  ──(new input)───────> intake / optin ────────────┘

The router owns the actual transitions; this module provides the shared
role-detection heuristics and a small, documented transition table.
"""

from __future__ import annotations

from typing import Optional

from app.supabase.schemas import ActiveFlow, UserRole

# Latin + Devanagari keyword hints. Buttons are the primary path; these only
# catch users who type their intent instead of tapping.
CANDIDATE_KEYWORDS = [
    "work", "job", "kaam", "naukri", "rozgar", "employment",
    "need job", "looking for work", "find work", "hire me",
    "काम", "नौकरी", "रोज़गार", "रोजगार",
]
EMPLOYER_KEYWORDS = [
    "hire", "hiring", "staff", "vacancy", "recruit", "post job", "post a job",
    "employee", "candidate chahiye", "worker chahiye",
    "भर्ती", "स्टाफ", "कर्मचारी", "वैकेंसी",
]

# Documentation of the intended transitions (the router enforces them).
TRANSITIONS = {
    ActiveFlow.WELCOME: [ActiveFlow.CANDIDATE_INTAKE, ActiveFlow.EMPLOYER_INTAKE, ActiveFlow.WELCOME],
    ActiveFlow.CANDIDATE_INTAKE: [ActiveFlow.CANDIDATE_INTAKE, ActiveFlow.IDLE],
    ActiveFlow.EMPLOYER_INTAKE: [ActiveFlow.EMPLOYER_INTAKE, ActiveFlow.IDLE],
    ActiveFlow.IDLE: [ActiveFlow.CANDIDATE_INTAKE, ActiveFlow.EMPLOYER_INTAKE, ActiveFlow.OPTIN, ActiveFlow.IDLE],
    ActiveFlow.OPTIN: [ActiveFlow.IDLE, ActiveFlow.OPTIN],
}


def detect_role_from_text(text: str | None) -> Optional[UserRole]:
    """Guess candidate vs employer from free text. Returns None if unclear."""
    if not text:
        return None
    tl = text.lower()
    # Employer signals win ties ("worker/staff chahiye" is an employer).
    if any(kw in tl for kw in EMPLOYER_KEYWORDS):
        return UserRole.EMPLOYER
    if any(kw in tl for kw in CANDIDATE_KEYWORDS):
        return UserRole.CANDIDATE
    return None
