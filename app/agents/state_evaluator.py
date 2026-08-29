"""
State Evaluator — the deterministic missing-field engine.

CRITICAL: this contains NO LLM calls. It is the anti-hallucination backbone.
Given a candidate or job, it computes — by fixed rules — which required fields
are still missing, the overall completeness %, and which single field to ask
for next. The intake agents are then forbidden from asking about anything not
returned here, so the bot can never re-ask for something it already knows.

Required-field lists come from app/config.py (overridable via the app_config
table), so what counts as "complete" is tunable without touching this code.
"""

from __future__ import annotations

from typing import Optional

from app.config import get_config
from app.supabase.schemas import Candidate, Job


def _present(value) -> bool:
    """A field counts as known if it is non-empty / not None."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) > 0
    return True


# Field-name -> predicate(entity) returning True when that field is satisfied.
_CANDIDATE_CHECKS = {
    "name": lambda c: _present(c.name),
    "location": lambda c: _present(c.location.district) or _present(c.location.city),
    "skills": lambda c: _present(c.skills),
    "experience_years": lambda c: c.experience_years is not None,
    "expected_salary": lambda c: c.expected_salary.min is not None
    or c.expected_salary.max is not None,
    "job_type_pref": lambda c: _present(c.job_type_pref),
    "availability": lambda c: _present(c.availability),
    "education": lambda c: _present(c.education),
    "languages": lambda c: _present(c.languages),
}

_JOB_CHECKS = {
    "title": lambda j: _present(j.title),
    "skills_required": lambda j: _present(j.skills_required),
    "experience_min": lambda j: j.experience_min is not None,
    "location": lambda j: _present(j.location.district) or _present(j.location.city),
    "job_type": lambda j: _present(j.job_type),
    "salary": lambda j: j.salary.min is not None or j.salary.max is not None,
    "openings": lambda j: j.openings is not None,
}


def _evaluate(entity, required: list[str], checks: dict) -> dict:
    missing = [f for f in required if f in checks and not checks[f](entity)]
    total = len([f for f in required if f in checks])
    known = total - len(missing)
    completeness = round((known / total) * 100) if total else 100
    next_field: Optional[str] = missing[0] if missing else None
    return {
        "missing_fields": missing,
        "completeness_pct": completeness,
        "next_field": next_field,
    }


def evaluate_candidate(candidate: Candidate) -> dict:
    """Return {missing_fields, completeness_pct, next_field} for a candidate."""
    required = get_config().required_fields_candidate
    return _evaluate(candidate, required, _CANDIDATE_CHECKS)


def evaluate_job(job: Job) -> dict:
    """Return {missing_fields, completeness_pct, next_field} for a job."""
    required = get_config().required_fields_job
    return _evaluate(job, required, _JOB_CHECKS)
