"""
Application configuration — loads from .env and the Supabase `app_config` table.

Static settings (API keys, URLs) come from environment variables. Dynamic
settings (scoring weights, thresholds, required fields) come from the
`app_config` table and can be tuned without a code deploy.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


# ── Static config (from .env) ──────────────────────────────────────────────


class WhatsAppConfig(BaseModel):
    """WhatsApp Cloud API credentials."""

    verify_token: str = Field(default_factory=lambda: os.getenv("WHATSAPP_VERIFY_TOKEN", ""))
    api_token: str = Field(default_factory=lambda: os.getenv("WHATSAPP_API_TOKEN", ""))
    phone_number_id: str = Field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""))
    api_version: str = Field(default_factory=lambda: os.getenv("WHATSAPP_API_VERSION", "v21.0"))

    @property
    def api_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    @property
    def media_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"


def _pick_model(env_var: str, default_model: str) -> str:
    """Resolve a chat model id. Explicit env override wins; otherwise return the default model."""
    return os.getenv(env_var) or default_model


class OpenAIConfig(BaseModel):
    """
    LLM provider config.

    Chat / JSON completions go through OpenRouter when OPENROUTER_API_KEY is set
    (OpenAI-compatible; base_url = https://openrouter.ai/api/v1), otherwise
    straight to OpenAI. Whisper transcription and embeddings are NOT offered by
    OpenRouter, so they use OPENAI_API_KEY directly; if that key is unset those
    two features degrade gracefully (voice asks for text, semantic recall is
    skipped) while chat + matchmaking keep working.

    Per-model overrides: CHAT_MODEL, EXTRACTION_MODEL, SCORING_MODEL, INTENT_MODEL.
    """

    # Chat provider — NVIDIA API
    api_key: str = Field(
        default_factory=lambda: os.getenv("NVIDIA_API_KEY", "")
    )
    base_url: Optional[str] = Field(
        default_factory=lambda: "https://integrate.api.nvidia.com/v1"
    )
    # Aux provider (OpenAI only) for Whisper + embeddings.
    aux_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    extraction_model: str = Field(default_factory=lambda: _pick_model("EXTRACTION_MODEL", "meta/muse-glimmer-30b"))
    chat_model: str = Field(default_factory=lambda: _pick_model("CHAT_MODEL", "meta/muse-glimmer-30b"))
    scoring_model: str = Field(default_factory=lambda: _pick_model("SCORING_MODEL", "meta/muse-glimmer-30b"))
    intent_model: str = Field(default_factory=lambda: _pick_model("INTENT_MODEL", "meta/muse-glimmer-30b"))
    whisper_model: str = "whisper-1"  # OpenAI direct (via aux_api_key)
    embedding_model: str = "text-embedding-3-small"  # 1536 dims -> memory_chunks.embedding


class SupabaseConfig(BaseModel):
    """Supabase connection. Use the SERVICE ROLE key (RLS is on; anon is blocked)."""

    url: str = Field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    key: str = Field(default_factory=lambda: os.getenv("SUPABASE_KEY", ""))


# ── Dynamic config (defaults here, overridable from the app_config table) ──


class ScoringWeights(BaseModel):
    """Matchmaker scoring rubric — weights must sum to 100."""

    skills: int = 35
    experience: int = 20
    location: int = 15
    salary: int = 15
    availability: int = 10
    soft: int = 5


class AppConfig(BaseModel):
    """Master config combining static env vars and dynamic app_config rows."""

    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    supabase: SupabaseConfig = Field(default_factory=SupabaseConfig)

    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)
    match_threshold: int = 85
    extraction_confidence_min: float = 0.7
    max_field_retries: int = 3
    matchmaker_interval_minutes: int = 15
    synthetic_refresh_interval_minutes: int = 60
    optin_timeout_hours: int = 48
    max_pending_optins_per_candidate: int = 2

    required_fields_candidate: list[str] = Field(
        default_factory=lambda: [
            "name",
            "location",
            "skills",
            "experience_years",
            "expected_salary",
            "job_type_pref",
            "availability",
        ]
    )
    required_fields_job: list[str] = Field(
        default_factory=lambda: [
            "title",
            "skills_required",
            "experience_min",
            "location",
            "job_type",
            "salary",
            "openings",
        ]
    )

    env: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def apply_remote_config(self, config_doc: dict) -> None:
        """Merge dynamic config (rows from the Supabase `app_config` table)."""
        if "scoring_weights" in config_doc:
            self.scoring_weights = ScoringWeights(**config_doc["scoring_weights"])
        for key in [
            "match_threshold",
            "extraction_confidence_min",
            "max_field_retries",
            "required_fields_candidate",
            "required_fields_job",
            "matchmaker_interval_minutes",
            "optin_timeout_hours",
            "max_pending_optins_per_candidate",
        ]:
            if key in config_doc:
                setattr(self, key, config_doc[key])


# Singleton — reset with get_config.cache_clear() if needed
@lru_cache()
def get_config() -> AppConfig:
    """Return the cached application config singleton."""
    return AppConfig()
