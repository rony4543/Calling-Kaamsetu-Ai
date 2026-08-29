"""
Synthetic Memory agent — infers a soft/psychological read of a person from their
conversation transcript (soft skills, tone, reliability, urgency).

Everything it produces is explicitly INFERRED and low-trust: the matchmaker uses
it only for the small 'soft' scoring dimension (<= 5%). Results are versioned
into synthetic_profiles via memory/synthetic.py (never overwritten).

Triggered when a profile goes live and periodically by the scheduler.
"""

from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.agents.extractor import known_candidate
from app.config import get_config
from app.memory import synthetic
from app.supabase.repositories import CandidateRepo, ConversationRepo, EmployerRepo

logger = logging.getLogger(__name__)


class SyntheticMemoryAgent(BaseAgent):
    name = "synthetic_memory"

    def infer(self, hard_profile: dict, transcript: str) -> dict:
        """Run the synthetic prompt and return the inferred read as a dict."""
        prompt = self.render(
            "synthetic.j2",
            hard_profile=json.dumps(hard_profile, ensure_ascii=False),
            transcript=transcript or "(no conversation yet)",
        )
        return self.llm.chat_json(prompt, model=get_config().openai.chat_model)

    def refresh_for(self, wa_id: str, role: str) -> None:
        """
        Rebuild the synthetic profile for a user from their transcript + hard
        profile, and save a new version. `role` is 'candidate' or 'employer'.
        """
        user_id = None
        hard: dict = {}

        if role == "candidate":
            c = CandidateRepo.get_by_wa_id(wa_id)
            if not c:
                return
            user_id = c.id
            hard = known_candidate(c)
        else:
            e = EmployerRepo.get_by_wa_id(wa_id)
            if not e:
                return
            user_id = e.id
            hard = {"company_name": e.company_name, "industry": e.industry, "role": "employer"}

        transcript = "\n".join(
            f"{m.direction}: {m.content}" for m in ConversationRepo.get_transcript(wa_id, 100)
        )
        try:
            result = self.infer(hard, transcript)
            if result:
                synthetic.save(user_id, result, model=get_config().openai.chat_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Synthetic refresh failed for %s: %s", wa_id, exc)
