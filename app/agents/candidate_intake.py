"""
Candidate Intake agent — onboards a job seeker, one field at a time.

Per turn:
  1. Extract structured fields from what the user just said (Extractor).
  2. Recompute missing fields deterministically (State Evaluator).
  3. If nothing is missing -> mark the candidate LIVE and signal completion.
  4. Otherwise -> ask ONE warm question for the next missing field only.

It NEVER asks for a field the State Evaluator says is already known — the
anti-hallucination guarantee. All persistence goes through memory/long_term.
"""

from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.agents.extractor import Extractor, apply_to_candidate, known_candidate
from app.agents.state_evaluator import evaluate_candidate
from app.config import get_config
from app.memory import short_term
from app.memory.long_term import save_candidate
from app.supabase.schemas import Candidate, CandidateStatus, Session
from app.utils import i18n

logger = logging.getLogger(__name__)


class CandidateIntakeAgent(BaseAgent):
    name = "candidate_intake"

    def __init__(self) -> None:
        self.extractor = Extractor()

    def handle(self, session: Session, candidate: Candidate, user_text: str, lang: str) -> dict:
        """
        Advance the candidate profile by one turn.

        Returns a dict:
          {"done": True,  "candidate": Candidate}                 -> profile complete/live
          {"done": False, "text": str, "next_field": str, "candidate": Candidate}
        """
        # 1. Extract from the latest input and merge.
        if user_text:
            extraction = self.extractor.extract_candidate(user_text, known_candidate(candidate))
            apply_to_candidate(candidate, extraction)

        # 2. Deterministic missing-field evaluation.
        ev = evaluate_candidate(candidate)
        candidate.missing_fields = ev["missing_fields"]
        candidate.completeness_pct = ev["completeness_pct"]

        # 3. Complete?
        if not ev["missing_fields"]:
            candidate.status = CandidateStatus.LIVE
            save_candidate(candidate)
            return {"done": True, "candidate": candidate}

        candidate.status = CandidateStatus.DRAFT
        save_candidate(candidate)

        # 4. Ask exactly one question for the next field.
        prompt = self.render(
            "candidate_intake.j2",
            known_fields=json.dumps(known_candidate(candidate), ensure_ascii=False),
            missing_fields=json.dumps(ev["missing_fields"], ensure_ascii=False),
            next_field=ev["next_field"],
            short_term=short_term.as_text(session),
            language=i18n.label(lang),
        )
        question = self.llm.chat(
            prompt, user="Generate the next question.", model=get_config().openai.chat_model, temperature=0.5
        )
        return {
            "done": False,
            "text": question,
            "next_field": ev["next_field"],
            "candidate": candidate,
        }
