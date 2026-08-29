"""
OpenAI wrapper — the single place the app talks to OpenAI.

Provides the primitives the agents need:
  - chat()        : a completion (optionally JSON mode) -> str
  - chat_json()   : a completion parsed into a dict
  - transcribe()  : Whisper speech-to-text for voice notes
  - embed()       : text embedding for the pgvector semantic index

Model selection comes from app/config.py (OpenAIConfig).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Optional

from openai import OpenAI

from app.config import get_config

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(self) -> None:
        cfg = get_config().openai
        self._cfg = cfg
        if not cfg.api_key:
            logger.warning(
                "No chat API key set (NVIDIA_API_KEY / OPENAI_API_KEY) — chat calls will fail."
            )
        default_headers = None
        if cfg.base_url and "openrouter" in cfg.base_url:
            # Optional attribution headers for the OpenRouter dashboard.
            default_headers = {"HTTP-Referer": "https://kaamsetu.app", "X-Title": "Kaamsetu"}
        self._client = OpenAI(
            api_key=cfg.api_key, base_url=cfg.base_url, default_headers=default_headers
        )
        # Aux client for Whisper + embeddings (OpenAI only — OpenRouter has neither).
        # None when OPENAI_API_KEY is unset; the methods below then degrade gracefully.
        self._aux = OpenAI(api_key=cfg.aux_api_key) if cfg.aux_api_key else None

    # ── Text ──────────────────────────────────────────────────────────────────
    def chat(
        self,
        system: str,
        user: str = "Proceed.",
        *,
        model: Optional[str] = None,
        json_mode: bool = False,
        temperature: float = 0.4,
    ) -> str:
        """Run a chat completion and return the assistant text."""
        kwargs: dict = {
            "model": model or self._cfg.chat_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def chat_json(
        self,
        system: str,
        user: str = "Return the JSON now.",
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> dict:
        """Run a JSON-mode completion and parse it. Returns {} on parse failure."""
        raw = self.chat(system, user, model=model, json_mode=True, temperature=temperature)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Failed to parse JSON from model: %s\nRaw: %s", exc, raw[:500])
            return {}

    # ── Voice ─────────────────────────────────────────────────────────────────
    def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
        """Whisper transcription for WhatsApp voice notes (returns '' on failure).

        Requires OPENAI_API_KEY (OpenRouter offers no transcription). Returns ''
        when that key is unset, so the router falls back to asking for text.
        """
        if not self._aux:
            logger.warning("Transcription unavailable — set OPENAI_API_KEY to enable Whisper.")
            return ""
        try:
            resp = self._aux.audio.transcriptions.create(
                model=self._cfg.whisper_model,
                file=(filename, audio_bytes),
            )
            return (resp.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("Transcription failed: %s", exc)
            return ""

    # ── Embeddings ──────────────────────────────────────────────────────────────
    def embed(self, text: str) -> list[float]:
        """Embed text for the memory_chunks semantic index (returns [] on failure).

        Requires OPENAI_API_KEY (OpenRouter offers no embeddings). Returns []
        when unset, so semantic indexing/recall is skipped without breaking chat.
        """
        if not self._aux:
            return []
        try:
            resp = self._aux.embeddings.create(
                model=self._cfg.embedding_model,
                input=text,
            )
            return resp.data[0].embedding
        except Exception as exc:  # noqa: BLE001
            logger.error("Embedding failed: %s", exc)
            return []


@lru_cache()
def get_openai_client() -> OpenAIClient:
    """Cached singleton OpenAI client."""
    return OpenAIClient()
