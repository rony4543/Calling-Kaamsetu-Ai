"""
Employer Intake agent — walks an employer through posting one job, one field at
a time. Symmetric to candidate intake but the prompt returns JSON so it can
suggest WhatsApp quick-reply buttons for structured fields (job_type, urgency…).

Per turn:
  1. Extract structured job fields from the latest input (Extractor).
  2. Recompute missing fields deterministically (State Evaluator).
  3. If nothing is missing -> mark the job LIVE and signal completion.
  4. Otherwise -> ask ONE question for the next field, with optional buttons.

Button ids are prefixed 'emp_opt:' so the router can feed the chosen value back
into the flow as if the user had typed it.
"""

from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.agents.extractor import Extractor, apply_to_job, known_job
from app.agents.state_evaluator import evaluate_job
from app.config import get_config
from app.memory import short_term
from app.memory.long_term import save_job
from app.supabase.schemas import Job, JobStatus, Session

logger = logging.getLogger(__name__)


class EmployerIntakeAgent(BaseAgent):
    name = "employer_intake"

    def __init__(self) -> None:
        self.extractor = Extractor()

    def handle(self, session: Session, job: Job, user_text: str, lang: str) -> dict:
        """
        Advance the job posting by one turn.

        Returns a dict:
          {"done": True,  "job": Job}
          {"done": False, "text": str, "buttons": list|None, "next_field": str, "job": Job}
        """
        # 1. Extract + merge.
        if user_text:
            extraction = self.extractor.extract_job(user_text, known_job(job))
            apply_to_job(job, extraction)

        # 2. Missing-field evaluation.
        ev = evaluate_job(job)
        job.missing_fields = ev["missing_fields"]
        job.completeness_pct = ev["completeness_pct"]

        # 3. Complete?
        if not ev["missing_fields"]:
            job.status = JobStatus.LIVE
            save_job(job)
            return {"done": True, "job": job}

        job.status = JobStatus.DRAFT
        save_job(job)

        # 4. Ask next field (JSON prompt -> optional buttons).
        prompt = self.render(
            "employer_intake.j2",
            known_fields=json.dumps(known_job(job), ensure_ascii=False),
            missing_fields=json.dumps(ev["missing_fields"], ensure_ascii=False),
            next_field=ev["next_field"],
            short_term=short_term.as_text(session),
        )
        data = self.llm.chat_json(prompt, model=get_config().openai.chat_model)
        question = data.get("question") or "Could you tell me a bit more about the role?"
        buttons = None
        if data.get("use_buttons") and isinstance(data.get("buttons"), list):
            buttons = [
                {"id": f"emp_opt:{b.get('id', '')}", "title": str(b.get("title", ""))[:20]}
                for b in data["buttons"][:3]
                if b.get("id")
            ]
            buttons = buttons or None
        return {
            "done": False,
            "text": question,
            "buttons": buttons,
            "next_field": ev["next_field"],
            "job": job,
        }
