"""
Typed CRUD repositories for all Supabase tables.

Every read/write to Supabase goes through these repositories.
Agents and the orchestrator never touch Supabase directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Any

from app.supabase.client import get_supabase_client
from app.supabase.schemas import (
    Candidate,
    CandidateStatus,
    ConversationMessage,
    Employer,
    Event,
    Job,
    JobStatus,
    Match,
    MatchStatus,
    Session,
    CandidateLocation,
    EmployerLocation,
    JobLocation,
    GeoPoint,
    SalaryRange,
    RawIntake,
    PendingField,
    SyntheticMemory,
    DimensionScores,
    OptIn,
    OptInStatus,
    UserRole,
    ActiveFlow,
    ShortTermMessage
)

logger = logging.getLogger(__name__)

# --- Helpers ---

def _candidate_to_db(c: Candidate) -> dict:
    return {
        "status": c.status.value,
        "name": c.name,
        "loc_district": c.location.district,
        "loc_city": c.location.city,
        "loc_state": c.location.state,
        "loc_lat": c.location.geo.lat,
        "loc_lng": c.location.geo.lng,
        "willing_to_relocate": c.location.willing_to_relocate,
        "max_commute_km": c.location.max_commute_km,
        "skills": c.skills,
        "experience_years": c.experience_years,
        "education": c.education,
        "expected_salary_min": c.expected_salary.min,
        "expected_salary_max": c.expected_salary.max,
        "salary_currency": c.expected_salary.currency,
        "salary_period": c.expected_salary.period,
        "job_type_pref": c.job_type_pref,
        "availability": c.availability,
        "languages": c.languages,
        "resume_url": c.resume_url,
        "raw_intake": c.raw_intake.model_dump(mode="json"),
        "pending_confirmation": {k: v.model_dump(mode="json") for k,v in c.pending_confirmation.items()},
        "missing_fields": c.missing_fields,
        "completeness_pct": c.completeness_pct,
        "source": c.source,
    }

def _db_to_candidate(user_row: dict, profile_row: dict) -> Candidate:
    loc = CandidateLocation(
        district=profile_row.get("loc_district"),
        city=profile_row.get("loc_city"),
        state=profile_row.get("loc_state"),
        geo=GeoPoint(lat=profile_row.get("loc_lat"), lng=profile_row.get("loc_lng")),
        willing_to_relocate=profile_row.get("willing_to_relocate", False),
        max_commute_km=profile_row.get("max_commute_km", 25)
    )
    sal = SalaryRange(
        min=profile_row.get("expected_salary_min"),
        max=profile_row.get("expected_salary_max"),
        currency=profile_row.get("salary_currency", "INR"),
        period=profile_row.get("salary_period", "month")
    )
    c = Candidate(
        id=user_row.get("id"),
        wa_id=user_row.get("wa_id"),
        status=CandidateStatus(profile_row.get("status", "draft")),
        name=profile_row.get("name"),
        location=loc,
        skills=profile_row.get("skills", []),
        experience_years=profile_row.get("experience_years"),
        education=profile_row.get("education"),
        expected_salary=sal,
        job_type_pref=profile_row.get("job_type_pref", []),
        availability=profile_row.get("availability"),
        languages=profile_row.get("languages", []),
        resume_url=profile_row.get("resume_url"),
        raw_intake=RawIntake(**profile_row.get("raw_intake", {})),
        pending_confirmation={k: PendingField(**v) for k, v in profile_row.get("pending_confirmation", {}).items()},
        synthetic=SyntheticMemory(), 
        missing_fields=profile_row.get("missing_fields", []),
        completeness_pct=profile_row.get("completeness_pct", 0),
        source=profile_row.get("source", "whatsapp"),
    )
    return c

def _employer_to_db(e: Employer) -> dict:
    return {
        "company_name": e.company_name,
        "contact_name": e.contact_name,
        "verified": e.verified,
        "industry": e.industry,
        "loc_district": e.location.district,
        "loc_city": e.location.city,
        "loc_state": e.location.state,
    }

def _db_to_employer(user_row: dict, profile_row: dict) -> Employer:
    loc = EmployerLocation(
        district=profile_row.get("loc_district"),
        city=profile_row.get("loc_city"),
        state=profile_row.get("loc_state"),
    )
    return Employer(
        id=user_row.get("id"),
        wa_id=user_row.get("wa_id"),
        company_name=profile_row.get("company_name"),
        contact_name=profile_row.get("contact_name"),
        verified=profile_row.get("verified", False),
        industry=profile_row.get("industry"),
        location=loc,
    )

def _job_to_db(j: Job) -> dict:
    return {
        "employer_id": j.employer_id,
        "status": j.status.value,
        "title": j.title,
        "description_raw": j.description_raw,
        "skills_required": j.skills_required,
        "nice_to_have": j.nice_to_have,
        "experience_min": j.experience_min,
        "loc_district": j.location.district,
        "loc_city": j.location.city,
        "loc_state": j.location.state,
        "loc_lat": j.location.geo.lat,
        "loc_lng": j.location.geo.lng,
        "remote_ok": j.location.remote_ok,
        "job_type": j.job_type,
        "salary_min": j.salary.min,
        "salary_max": j.salary.max,
        "salary_currency": j.salary.currency,
        "salary_period": j.salary.period,
        "openings": j.openings,
        "urgency": j.urgency,
        "missing_fields": j.missing_fields,
        "completeness_pct": j.completeness_pct,
    }

def _db_to_job(row: dict) -> Job:
    loc = JobLocation(
        district=row.get("loc_district"),
        city=row.get("loc_city"),
        state=row.get("loc_state"),
        geo=GeoPoint(lat=row.get("loc_lat"), lng=row.get("loc_lng")),
        remote_ok=row.get("remote_ok", False),
    )
    sal = SalaryRange(
        min=row.get("salary_min"),
        max=row.get("salary_max"),
        currency=row.get("salary_currency", "INR"),
        period=row.get("salary_period", "month")
    )
    return Job(
        id=row.get("id"),
        employer_id=row.get("employer_id"),
        status=JobStatus(row.get("status", "draft")),
        title=row.get("title"),
        description_raw=row.get("description_raw"),
        skills_required=row.get("skills_required", []),
        nice_to_have=row.get("nice_to_have", []),
        experience_min=row.get("experience_min"),
        location=loc,
        job_type=row.get("job_type"),
        salary=sal,
        openings=row.get("openings"),
        urgency=row.get("urgency"),
        missing_fields=row.get("missing_fields", []),
        completeness_pct=row.get("completeness_pct", 0)
    )

def _match_to_db(m: Match) -> dict:
    return {
        "job_id": m.job_id,
        "candidate_id": m.candidate_id,
        "overall_score": m.overall_score,
        "score_skills": m.dimension_scores.skills,
        "score_experience": m.dimension_scores.experience,
        "score_location": m.dimension_scores.location,
        "score_salary": m.dimension_scores.salary,
        "score_availability": m.dimension_scores.availability,
        "score_soft": m.dimension_scores.soft,
        "rationale": m.rationale,
        "red_flags": m.red_flags,
        "pitch_for_employer": m.pitch_for_employer,
        "pitch_for_candidate": m.pitch_for_candidate,
        "confidence": m.confidence,
        "status": m.status.value,
        "candidate_optin": m.candidate_optin.status.value,
        "employer_optin": m.employer_optin.status.value,
    }

def _db_to_match(row: dict) -> Match:
    ds = DimensionScores(
        skills=row.get("score_skills", 0),
        experience=row.get("score_experience", 0),
        location=row.get("score_location", 0),
        salary=row.get("score_salary", 0),
        availability=row.get("score_availability", 0),
        soft=row.get("score_soft", 0),
    )
    c_optin = OptIn(status=OptInStatus(row.get("candidate_optin", "pending")))
    e_optin = OptIn(status=OptInStatus(row.get("employer_optin", "pending")))
    return Match(
        id=row.get("id"),
        job_id=row.get("job_id"),
        candidate_id=row.get("candidate_id"),
        overall_score=row.get("overall_score", 0),
        dimension_scores=ds,
        rationale=row.get("rationale", ""),
        red_flags=row.get("red_flags", []),
        pitch_for_employer=row.get("pitch_for_employer", ""),
        pitch_for_candidate=row.get("pitch_for_candidate", ""),
        confidence=row.get("confidence", 0.0),
        status=MatchStatus(row.get("status", "proposed")),
        candidate_optin=c_optin,
        employer_optin=e_optin,
    )


def _ensure_user(wa_id: str, is_candidate: bool = False, is_employer: bool = False) -> dict:
    db = get_supabase_client()
    resp = db.table("users").select("*").eq("wa_id", wa_id).execute()
    if resp.data:
        user = resp.data[0]
        updates = {}
        if is_candidate and not user.get("is_candidate"):
            updates["is_candidate"] = True
            updates["primary_role"] = "candidate"
        if is_employer and not user.get("is_employer"):
            updates["is_employer"] = True
            updates["primary_role"] = "employer"
        if updates:
            up_resp = db.table("users").update(updates).eq("id", user["id"]).execute()
            return up_resp.data[0]
        return user
    else:
        new_user = {
            "wa_id": wa_id,
            "primary_role": "candidate" if is_candidate else "employer" if is_employer else "unknown",
            "is_candidate": is_candidate,
            "is_employer": is_employer
        }
        resp = db.table("users").insert(new_user).execute()
        return resp.data[0]

# --- Repositories ---

class CandidateRepo:

    @staticmethod
    def create(candidate: Candidate) -> str:
        db = get_supabase_client()
        user = _ensure_user(candidate.wa_id, is_candidate=True)
        data = _candidate_to_db(candidate)
        data["user_id"] = user["id"]
        resp = db.table("candidate_profiles").insert(data).execute()
        doc_id = user["id"]
        logger.info("Created candidate %s for wa_id=%s", doc_id, candidate.wa_id)
        return doc_id

    @staticmethod
    def get(candidate_id: str) -> Optional[Candidate]:
        db = get_supabase_client()
        resp = db.table("candidate_profiles").select("*, users!inner(*)").eq("user_id", candidate_id).execute()
        if not resp.data:
            return None
        prof = resp.data[0]
        user = prof.pop("users")
        return _db_to_candidate(user, prof)

    @staticmethod
    def get_by_wa_id(wa_id: str) -> Optional[Candidate]:
        db = get_supabase_client()
        resp = db.table("users").select("*, candidate_profiles(*)").eq("wa_id", wa_id).execute()
        if not resp.data:
            return None
        user = resp.data[0]
        if not user.get("candidate_profiles"):
            return None
        prof = user["candidate_profiles"][0] if isinstance(user["candidate_profiles"], list) else user["candidate_profiles"]
        return _db_to_candidate(user, prof)

    @staticmethod
    def update(candidate_id: str, updates: dict) -> None:
        db = get_supabase_client()
        db.table("candidate_profiles").update(updates).eq("user_id", candidate_id).execute()
        logger.debug("Updated candidate %s: %s", candidate_id, list(updates.keys()))

    @staticmethod
    def get_live_candidates(district: Optional[str] = None, job_types: Optional[list[str]] = None) -> list[Candidate]:
        db = get_supabase_client()
        query = db.table("candidate_profiles").select("*, users!inner(*)").eq("status", "live")
        if district:
            query = query.eq("loc_district", district)
        resp = query.execute()
        candidates = []
        for prof in resp.data:
            user = prof.pop("users")
            candidate = _db_to_candidate(user, prof)
            if job_types and candidate.job_type_pref:
                if not set(candidate.job_type_pref) & set(job_types):
                    continue
            candidates.append(candidate)
        return candidates

    @staticmethod
    def get_all_live() -> list[Candidate]:
        db = get_supabase_client()
        resp = db.table("candidate_profiles").select("*, users!inner(*)").eq("status", "live").execute()
        return [_db_to_candidate(p.pop("users"), p) for p in resp.data]


class EmployerRepo:

    @staticmethod
    def create(employer: Employer) -> str:
        db = get_supabase_client()
        user = _ensure_user(employer.wa_id, is_employer=True)
        data = _employer_to_db(employer)
        data["user_id"] = user["id"]
        db.table("employer_profiles").insert(data).execute()
        logger.info("Created employer %s for wa_id=%s", user["id"], employer.wa_id)
        return user["id"]

    @staticmethod
    def get(employer_id: str) -> Optional[Employer]:
        db = get_supabase_client()
        resp = db.table("employer_profiles").select("*, users!inner(*)").eq("user_id", employer_id).execute()
        if not resp.data:
            return None
        prof = resp.data[0]
        user = prof.pop("users")
        return _db_to_employer(user, prof)

    @staticmethod
    def get_by_wa_id(wa_id: str) -> Optional[Employer]:
        db = get_supabase_client()
        resp = db.table("users").select("*, employer_profiles(*)").eq("wa_id", wa_id).execute()
        if not resp.data:
            return None
        user = resp.data[0]
        if not user.get("employer_profiles"):
            return None
        prof = user["employer_profiles"][0] if isinstance(user["employer_profiles"], list) else user["employer_profiles"]
        return _db_to_employer(user, prof)

    @staticmethod
    def update(employer_id: str, updates: dict) -> None:
        db = get_supabase_client()
        db.table("employer_profiles").update(updates).eq("user_id", employer_id).execute()


class JobRepo:

    @staticmethod
    def create(job: Job) -> str:
        db = get_supabase_client()
        data = _job_to_db(job)
        resp = db.table("job_posts").insert(data).execute()
        doc_id = resp.data[0]["id"]
        logger.info("Created job %s for employer=%s", doc_id, job.employer_id)
        return doc_id

    @staticmethod
    def get(job_id: str) -> Optional[Job]:
        db = get_supabase_client()
        resp = db.table("job_posts").select("*").eq("id", job_id).execute()
        return _db_to_job(resp.data[0]) if resp.data else None

    @staticmethod
    def get_by_employer(employer_id: str) -> list[Job]:
        db = get_supabase_client()
        resp = db.table("job_posts").select("*").eq("employer_id", employer_id).execute()
        return [_db_to_job(r) for r in resp.data]

    @staticmethod
    def get_latest_draft_by_employer(employer_id: str) -> Optional[Job]:
        db = get_supabase_client()
        resp = db.table("job_posts").select("*").eq("employer_id", employer_id).eq("status", "draft").order("created_at", desc=True).limit(1).execute()
        return _db_to_job(resp.data[0]) if resp.data else None

    @staticmethod
    def update(job_id: str, updates: dict) -> None:
        db = get_supabase_client()
        db.table("job_posts").update(updates).eq("id", job_id).execute()

    @staticmethod
    def get_all_live() -> list[Job]:
        db = get_supabase_client()
        resp = db.table("job_posts").select("*").eq("status", "live").execute()
        return [_db_to_job(r) for r in resp.data]


class MatchRepo:

    @staticmethod
    def create(match: Match) -> str:
        db = get_supabase_client()
        data = _match_to_db(match)
        resp = db.table("matches").insert(data).execute()
        doc_id = resp.data[0]["id"]
        logger.info("Created match %s", doc_id)
        return doc_id

    @staticmethod
    def get(match_id: str) -> Optional[Match]:
        db = get_supabase_client()
        resp = db.table("matches").select("*").eq("id", match_id).execute()
        return _db_to_match(resp.data[0]) if resp.data else None

    @staticmethod
    def update(match_id: str, updates: dict) -> None:
        db = get_supabase_client()
        db.table("matches").update(updates).eq("id", match_id).execute()

    @staticmethod
    def exists_for_pair(candidate_id: str, job_id: str) -> bool:
        db = get_supabase_client()
        resp = db.table("matches").select("id").eq("candidate_id", candidate_id).eq("job_id", job_id).execute()
        return len(resp.data) > 0

    @staticmethod
    def get_pending_for_candidate(candidate_id: str) -> list[Match]:
        db = get_supabase_client()
        resp = db.table("matches").select("*").eq("candidate_id", candidate_id).eq("status", "proposed").execute()
        return [_db_to_match(r) for r in resp.data]

    @staticmethod
    def get_by_status(status: MatchStatus) -> list[Match]:
        db = get_supabase_client()
        resp = db.table("matches").select("*").eq("status", status.value).execute()
        return [_db_to_match(r) for r in resp.data]

    @staticmethod
    def get_pending_employer_review() -> list[Match]:
        db = get_supabase_client()
        resp = db.table("matches").select("*").eq("status", "candidate_accepted").execute()
        return [_db_to_match(r) for r in resp.data]


class SessionRepo:

    @staticmethod
    def get(wa_id: str) -> Optional[Session]:
        db = get_supabase_client()
        resp = db.table("sessions").select("*").eq("wa_id", wa_id).execute()
        if not resp.data:
            return None
        row = resp.data[0]
        return Session(
            wa_id=row["wa_id"],
            role=UserRole(row.get("role", "unknown")),
            user_id=row.get("user_id"),
            active_flow=ActiveFlow(row.get("active_flow", "welcome")),
            expected_field=row.get("expected_field"),
            short_term=[ShortTermMessage(**m) for m in row.get("short_term", [])],
            retry_counts=row.get("retry_counts", {}),
            last_agent=row.get("last_agent"),
            last_message_id=row.get("last_message_id"),
        )

    @staticmethod
    def create(session: Session) -> None:
        db = get_supabase_client()
        user = _ensure_user(session.wa_id)
        data = {
            "user_id": user["id"],
            "wa_id": session.wa_id,
            "role": session.role.value,
            "active_flow": session.active_flow.value,
            "short_term": [m.model_dump(mode="json") for m in session.short_term],
            "retry_counts": session.retry_counts,
        }
        db.table("sessions").insert(data).execute()
        logger.info("Created session for wa_id=%s", session.wa_id)

    @staticmethod
    def update(wa_id: str, updates: dict) -> None:
        db = get_supabase_client()
        db.table("sessions").update(updates).eq("wa_id", wa_id).execute()

    @staticmethod
    def set_flow(wa_id: str, flow: str, agent: Optional[str] = None) -> None:
        updates = {"active_flow": flow}
        if agent:
            updates["last_agent"] = agent
        SessionRepo.update(wa_id, updates)


class EventRepo:
    @staticmethod
    def log(event_type: str, wa_id: str, data: Optional[dict] = None) -> str:
        db = get_supabase_client()
        user_resp = db.table("users").select("id").eq("wa_id", wa_id).execute()
        user_id = user_resp.data[0]["id"] if user_resp.data else None
        event = {
            "user_id": user_id,
            "wa_id": wa_id,
            "type": event_type,
            "data": data or {}
        }
        resp = db.table("events").insert(event).execute()
        return resp.data[0]["id"]


class ConversationRepo:

    @staticmethod
    def log_message(wa_id: str, message: ConversationMessage) -> str:
        db = get_supabase_client()
        user = _ensure_user(wa_id)
        data = message.model_dump(exclude={"id"}, mode="json")
        data["user_id"] = user["id"]
        resp = db.table("conversation_messages").insert(data).execute()
        return resp.data[0]["id"]

    @staticmethod
    def get_transcript(wa_id: str, limit: int = 100) -> list[ConversationMessage]:
        db = get_supabase_client()
        user_resp = db.table("users").select("id").eq("wa_id", wa_id).execute()
        if not user_resp.data:
            return []
        user_id = user_resp.data[0]["id"]
        
        resp = db.table("conversation_messages").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
        result = []
        for row in reversed(resp.data):
            result.append(ConversationMessage(**row))
        return result


class ConfigRepo:

    @staticmethod
    def get_scoring_weights() -> Optional[dict]:
        db = get_supabase_client()
        resp = db.table("app_config").select("value").eq("key", "scoring_weights").execute()
        return resp.data[0]["value"] if resp.data else None

    @staticmethod
    def get_all() -> dict:
        db = get_supabase_client()
        resp = db.table("app_config").select("*").execute()
        merged = {}
        for row in resp.data:
            if isinstance(row["value"], dict):
                merged.update(row["value"])
            else:
                merged[row["key"]] = row["value"]
        return merged
