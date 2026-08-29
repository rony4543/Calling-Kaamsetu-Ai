"""
Pydantic models — the contract between agents, repositories, and Supabase.

These mirror the tables in supabase/migrations/0001_init.sql. Repositories map
these nested models to/from the flat table columns (e.g. location.district ->
loc_district). Agents work with these models, never with raw rows.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────


class CandidateStatus(str, Enum):
    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"
    PLACED = "placed"


class JobStatus(str, Enum):
    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"
    FILLED = "filled"
    EXPIRED = "expired"


class MatchStatus(str, Enum):
    PROPOSED = "proposed"
    CANDIDATE_ACCEPTED = "candidate_accepted"
    CANDIDATE_DECLINED = "candidate_declined"
    EMPLOYER_ACCEPTED = "employer_accepted"
    EMPLOYER_DECLINED = "employer_declined"
    EXPIRED = "expired"
    PLACED = "placed"


class OptInStatus(str, Enum):
    PENDING = "pending"
    YES = "yes"
    NO = "no"


class UserRole(str, Enum):
    UNKNOWN = "unknown"
    CANDIDATE = "candidate"
    EMPLOYER = "employer"


class ActiveFlow(str, Enum):
    WELCOME = "welcome"
    CANDIDATE_INTAKE = "candidate_intake"
    EMPLOYER_INTAKE = "employer_intake"
    IDLE = "idle"
    OPTIN = "optin"


class JobType(str, Enum):
    FULL_TIME = "full_time"
    CONTRACT = "contract"
    PART_TIME = "part_time"
    SHIFT = "shift"


class Urgency(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


# ── Shared sub-models ──────────────────────────────────────────────────────


class GeoPoint(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None


class CandidateLocation(BaseModel):
    district: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    geo: GeoPoint = Field(default_factory=GeoPoint)
    willing_to_relocate: bool = False
    max_commute_km: Optional[int] = 25


class JobLocation(BaseModel):
    district: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    geo: GeoPoint = Field(default_factory=GeoPoint)
    remote_ok: bool = False


class EmployerLocation(BaseModel):
    district: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None


class SalaryRange(BaseModel):
    min: Optional[int] = None
    max: Optional[int] = None
    currency: str = "INR"
    period: str = "month"


class RawIntake(BaseModel):
    voice_urls: list[str] = Field(default_factory=list)
    pdf_url: Optional[str] = None
    chat_snippets: list[str] = Field(default_factory=list)


class PendingField(BaseModel):
    value: Any = None
    confidence: float = 0.0


class SyntheticMemory(BaseModel):
    soft_skills: list[str] = Field(default_factory=list)
    tone: Optional[str] = None
    reliability_signal: Optional[float] = None
    urgency: Optional[str] = None  # for employers
    summary: Optional[str] = None
    generated_at: Optional[datetime] = None


class OptIn(BaseModel):
    status: OptInStatus = OptInStatus.PENDING
    ts: Optional[datetime] = None


class DimensionScores(BaseModel):
    skills: int = 0
    experience: int = 0
    location: int = 0
    salary: int = 0
    availability: int = 0
    soft: int = 0


class ShortTermMessage(BaseModel):
    role: str  # "assistant" | "user"
    text: str


# ── Entity models ──────────────────────────────────────────────────────────


class Candidate(BaseModel):
    """A candidate = the `candidate_profiles` row joined with its `users` row."""

    id: Optional[str] = None  # users.id (the unified user id)
    wa_id: str
    status: CandidateStatus = CandidateStatus.DRAFT
    name: Optional[str] = None
    location: CandidateLocation = Field(default_factory=CandidateLocation)
    skills: list[str] = Field(default_factory=list)
    experience_years: Optional[int] = None
    education: Optional[str] = None
    expected_salary: SalaryRange = Field(default_factory=SalaryRange)
    job_type_pref: list[str] = Field(default_factory=list)
    availability: Optional[str] = None
    languages: list[str] = Field(default_factory=list)
    resume_url: Optional[str] = None
    raw_intake: RawIntake = Field(default_factory=RawIntake)
    pending_confirmation: dict[str, PendingField] = Field(default_factory=dict)
    synthetic: SyntheticMemory = Field(default_factory=SyntheticMemory)
    missing_fields: list[str] = Field(default_factory=list)
    completeness_pct: int = 0
    source: str = "whatsapp"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Employer(BaseModel):
    """An employer = the `employer_profiles` row joined with its `users` row."""

    id: Optional[str] = None  # users.id
    wa_id: str
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    verified: bool = False
    location: EmployerLocation = Field(default_factory=EmployerLocation)
    industry: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(BaseModel):
    """`job_posts` row."""

    id: Optional[str] = None
    employer_id: str  # users.id of the employer
    status: JobStatus = JobStatus.DRAFT
    title: Optional[str] = None
    description_raw: Optional[str] = None
    skills_required: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    experience_min: Optional[int] = None
    location: JobLocation = Field(default_factory=JobLocation)
    job_type: Optional[str] = None
    salary: SalaryRange = Field(default_factory=SalaryRange)
    openings: Optional[int] = None
    urgency: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    completeness_pct: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class Match(BaseModel):
    """`matches` row."""

    id: Optional[str] = None
    job_id: str
    candidate_id: str
    overall_score: int = 0
    dimension_scores: DimensionScores = Field(default_factory=DimensionScores)
    rationale: str = ""
    red_flags: list[str] = Field(default_factory=list)
    pitch_for_employer: str = ""
    pitch_for_candidate: str = ""
    confidence: float = 0.0
    status: MatchStatus = MatchStatus.PROPOSED
    candidate_optin: OptIn = Field(default_factory=OptIn)
    employer_optin: OptIn = Field(default_factory=OptIn)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Session(BaseModel):
    """`sessions` row — short-term / working memory + flow state."""

    wa_id: str
    user_id: Optional[str] = None  # users.id
    role: UserRole = UserRole.UNKNOWN
    linked_id: Optional[str] = None  # legacy alias for user_id
    active_flow: ActiveFlow = ActiveFlow.WELCOME
    expected_field: Optional[str] = None
    short_term: list[ShortTermMessage] = Field(default_factory=list)
    retry_counts: dict[str, int] = Field(default_factory=dict)
    last_agent: Optional[str] = None
    last_message_id: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class Event(BaseModel):
    """`events` row — append-only audit / analytics stream."""

    id: Optional[str] = None
    type: str  # e.g. "consent_given", "profile_live", "match_proposed"
    wa_id: str
    data: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ConversationMessage(BaseModel):
    """`conversation_messages` row — full message log."""

    id: Optional[str] = None
    direction: str  # "inbound" | "outbound"
    message_type: str  # "text" | "voice" | "document" | "interactive" | "template"
    content: str = ""
    media_url: Optional[str] = None
    wa_message_id: Optional[str] = None
    agent: Optional[str] = None  # which agent generated the outbound message
    created_at: Optional[datetime] = None  # set by the DB default; read-only here


# ── Structured-output schemas (OpenAI JSON mode) ───────────────────────────


class ExtractionResult(BaseModel):
    """Structured output from the Extractor sub-agent."""

    extracted: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    unmapped_notes: str = ""


class MatchScoreResult(BaseModel):
    """Structured output from the Matchmaker scoring call."""

    overall_score: int = 0
    dimension_scores: DimensionScores = Field(default_factory=DimensionScores)
    rationale: str = ""
    red_flags: list[str] = Field(default_factory=list)
    pitch_for_employer: str = ""
    pitch_for_candidate: str = ""
    confidence: float = 0.0
