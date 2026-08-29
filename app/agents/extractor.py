"""
Extractor — turns unstructured user input (chat text, voice transcript, pasted
JD) into structured fields, using JSON mode against a fixed schema.

Two responsibilities:
  1. extract_candidate / extract_job — call the LLM, return an ExtractionResult.
  2. apply_to_candidate / apply_to_job — merge that result into the entity model,
     honouring confidence: high-confidence values fill/replace fields, while
     low-confidence values are parked in `pending_confirmation` for the user to
     confirm rather than silently trusted.

Anti-hallucination: the extractor is told what's already KNOWN and to not
overwrite it with lower-confidence guesses.
"""

from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.config import get_config
from app.supabase.schemas import Candidate, ExtractionResult, Job, PendingField

logger = logging.getLogger(__name__)

# Confidence at/above which we replace an already-known value.
_OVERWRITE_AT = 0.85


class Extractor(BaseAgent):
    name = "extractor"

    def extract_candidate(self, raw_input: str, known: dict) -> ExtractionResult:
        prompt = self.render(
            "extractor_candidate.j2",
            raw_input=raw_input,
            known_fields=json.dumps(known, ensure_ascii=False),
        )
        data = self.llm.chat_json(prompt, model=get_config().openai.extraction_model)
        return self._to_result(data)

    def extract_job(self, raw_input: str, known: dict) -> ExtractionResult:
        prompt = self.render(
            "extractor_job.j2",
            raw_input=raw_input,
            known_fields=json.dumps(known, ensure_ascii=False),
        )
        data = self.llm.chat_json(prompt, model=get_config().openai.extraction_model)
        return self._to_result(data)

    @staticmethod
    def _to_result(data: dict) -> ExtractionResult:
        return ExtractionResult(
            extracted=data.get("extracted", {}) or {},
            confidence=data.get("confidence", {}) or {},
            unmapped_notes=data.get("unmapped_notes", "") or "",
        )


# ── Merge helpers ──────────────────────────────────────────────────────────


def _should_set(currently_present: bool, conf: float, threshold: float) -> str:
    """
    Decide what to do with an extracted value.
      "set"     — fill it (either the field is empty, or confidence is high)
      "pending" — low confidence, park for confirmation
      "skip"    — already known and not confident enough to overwrite
    """
    if conf < threshold:
        return "pending"
    if not currently_present:
        return "set"
    return "set" if conf >= _OVERWRITE_AT else "skip"


def apply_to_candidate(
    candidate: Candidate, result: ExtractionResult, threshold: float | None = None
) -> Candidate:
    """Merge an ExtractionResult into a Candidate (mutates and returns it)."""
    threshold = threshold if threshold is not None else get_config().extraction_confidence_min
    ex, conf = result.extracted, result.confidence

    def handle(field: str, present: bool, setter, raw_value):
        c = conf.get(field, 1.0)
        c = 1.0 if c is None else float(c)
        action = _should_set(present, c, threshold)
        if action == "set":
            setter(raw_value)
        elif action == "pending":
            candidate.pending_confirmation[field] = PendingField(value=raw_value, confidence=c)

    if "name" in ex:
        handle("name", bool(candidate.name), lambda v: setattr(candidate, "name", v), ex["name"])
    if "skills" in ex and isinstance(ex["skills"], list):
        handle("skills", bool(candidate.skills), lambda v: setattr(candidate, "skills", v), ex["skills"])
    if "experience_years" in ex:
        handle(
            "experience_years",
            candidate.experience_years is not None,
            lambda v: setattr(candidate, "experience_years", _as_int(v)),
            ex["experience_years"],
        )
    if "education" in ex:
        handle("education", bool(candidate.education), lambda v: setattr(candidate, "education", v), ex["education"])
    if "availability" in ex:
        handle("availability", bool(candidate.availability), lambda v: setattr(candidate, "availability", v), ex["availability"])
    if "languages" in ex and isinstance(ex["languages"], list):
        handle("languages", bool(candidate.languages), lambda v: setattr(candidate, "languages", v), ex["languages"])
    if "job_type_pref" in ex and isinstance(ex["job_type_pref"], list):
        handle("job_type_pref", bool(candidate.job_type_pref), lambda v: setattr(candidate, "job_type_pref", v), ex["job_type_pref"])
    if "location" in ex and isinstance(ex["location"], dict):
        loc = ex["location"]
        present = bool(candidate.location.district or candidate.location.city)

        def set_loc(v):
            candidate.location.district = v.get("district") or candidate.location.district
            candidate.location.city = v.get("city") or candidate.location.city
            candidate.location.state = v.get("state") or candidate.location.state
            if "willing_to_relocate" in v:
                candidate.location.willing_to_relocate = bool(v["willing_to_relocate"])
            if v.get("max_commute_km") is not None:
                candidate.location.max_commute_km = _as_int(v["max_commute_km"])

        handle("location", present, set_loc, loc)
    if "expected_salary" in ex and isinstance(ex["expected_salary"], dict):
        sal = ex["expected_salary"]
        present = candidate.expected_salary.min is not None or candidate.expected_salary.max is not None

        def set_sal(v):
            if v.get("min") is not None:
                candidate.expected_salary.min = _as_int(v["min"])
            if v.get("max") is not None:
                candidate.expected_salary.max = _as_int(v["max"])

        handle("expected_salary", present, set_sal, sal)

    return candidate


def apply_to_job(job: Job, result: ExtractionResult, threshold: float | None = None) -> Job:
    """Merge an ExtractionResult into a Job (mutates and returns it)."""
    threshold = threshold if threshold is not None else get_config().extraction_confidence_min
    ex, conf = result.extracted, result.confidence

    def confident(field: str) -> bool:
        c = conf.get(field, 1.0)
        return (1.0 if c is None else float(c)) >= threshold

    if "title" in ex and confident("title") and not job.title:
        job.title = ex["title"]
    if "description_raw" in ex and ex["description_raw"]:
        job.description_raw = ex["description_raw"]
    if "skills_required" in ex and isinstance(ex["skills_required"], list) and confident("skills_required"):
        if not job.skills_required:
            job.skills_required = ex["skills_required"]
    if "nice_to_have" in ex and isinstance(ex["nice_to_have"], list):
        job.nice_to_have = ex["nice_to_have"] or job.nice_to_have
    if "experience_min" in ex and confident("experience_min") and job.experience_min is None:
        job.experience_min = _as_int(ex["experience_min"])
    if "job_type" in ex and confident("job_type") and not job.job_type:
        job.job_type = ex["job_type"]
    if "openings" in ex and confident("openings") and job.openings is None:
        job.openings = _as_int(ex["openings"])
    if "urgency" in ex and ex["urgency"]:
        job.urgency = ex["urgency"]
    if "location" in ex and isinstance(ex["location"], dict) and confident("location"):
        loc = ex["location"]
        job.location.district = loc.get("district") or job.location.district
        job.location.city = loc.get("city") or job.location.city
        job.location.state = loc.get("state") or job.location.state
        if "remote_ok" in loc:
            job.location.remote_ok = bool(loc["remote_ok"])
    if "salary" in ex and isinstance(ex["salary"], dict) and confident("salary"):
        sal = ex["salary"]
        if sal.get("min") is not None:
            job.salary.min = _as_int(sal["min"])
        if sal.get("max") is not None:
            job.salary.max = _as_int(sal["max"])

    return job


def known_candidate(candidate: Candidate) -> dict:
    """Compact dict of already-known candidate fields (for the KNOWN prompt block)."""
    known: dict = {}
    if candidate.name:
        known["name"] = candidate.name
    if candidate.skills:
        known["skills"] = candidate.skills
    if candidate.experience_years is not None:
        known["experience_years"] = candidate.experience_years
    if candidate.location.district or candidate.location.city:
        known["location"] = {"district": candidate.location.district, "city": candidate.location.city}
    if candidate.expected_salary.min is not None or candidate.expected_salary.max is not None:
        known["expected_salary"] = {"min": candidate.expected_salary.min, "max": candidate.expected_salary.max}
    if candidate.job_type_pref:
        known["job_type_pref"] = candidate.job_type_pref
    if candidate.availability:
        known["availability"] = candidate.availability
    if candidate.education:
        known["education"] = candidate.education
    if candidate.languages:
        known["languages"] = candidate.languages
    return known


def known_job(job: Job) -> dict:
    """Compact dict of already-known job fields (for the KNOWN prompt block)."""
    known: dict = {}
    if job.title:
        known["title"] = job.title
    if job.skills_required:
        known["skills_required"] = job.skills_required
    if job.experience_min is not None:
        known["experience_min"] = job.experience_min
    if job.location.district or job.location.city:
        known["location"] = {"district": job.location.district, "city": job.location.city}
    if job.job_type:
        known["job_type"] = job.job_type
    if job.salary.min is not None or job.salary.max is not None:
        known["salary"] = {"min": job.salary.min, "max": job.salary.max}
    if job.openings is not None:
        known["openings"] = job.openings
    return known


def _as_int(value):
    """Coerce a value to int, tolerating '5 years', '15000', floats, etc."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None
