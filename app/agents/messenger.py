"""
Messenger — all outbound conversation + the double opt-in pipeline.

Responsibilities:
  - send_text / send_buttons: send via WhatsApp AND log the outbound turn to
    conversation_messages (so the transcript stays complete for memory).
  - propose_to_candidate / propose_to_employer: the two halves of the double
    opt-in. Candidate is always asked first; only on their YES is the employer
    asked. On the employer's YES, contact details are exchanged and the match is
    marked placed.
  - handle_optin_reply: process a Yes/No button tap and advance the pipeline.

The matchmaker calls propose_to_candidate; the router calls handle_optin_reply.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from app.integrations.whatsapp_api import get_whatsapp_client
from app.memory import short_term
from app.supabase.repositories import (
    CandidateRepo,
    ConversationRepo,
    EmployerRepo,
    EventRepo,
    JobRepo,
    MatchRepo,
)
from app.supabase.schemas import ConversationMessage
from app.utils.i18n import HI, t

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Messenger:
    def __init__(self) -> None:
        self.wa = get_whatsapp_client()

    # ── Low-level send + log ─────────────────────────────────────────────────
    def send_text(self, wa_id: str, body: str, agent: Optional[str] = None) -> Optional[str]:
        mid = self.wa.send_text(wa_id, body)
        self._log(wa_id, "text", body, mid, agent)
        return mid

    def send_buttons(
        self, wa_id: str, body: str, buttons: list[dict], agent: Optional[str] = None
    ) -> Optional[str]:
        mid = self.wa.send_buttons(wa_id, body, buttons)
        self._log(wa_id, "interactive", body, mid, agent)
        return mid

    def _log(self, wa_id, mtype, body, mid, agent) -> None:
        try:
            ConversationRepo.log_message(
                wa_id,
                ConversationMessage(
                    direction="outbound",
                    message_type=mtype,
                    content=body,
                    wa_message_id=mid,
                    agent=agent,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to log outbound message: %s", exc)

    # ── Summaries ─────────────────────────────────────────────────────────────
    @staticmethod
    def _job_summary(job) -> str:
        place = job.location.city or job.location.district or ""
        sal = ""
        if job.salary.min or job.salary.max:
            lo = job.salary.min or job.salary.max
            hi = job.salary.max or job.salary.min
            sal = f" • ₹{lo}-{hi}/{job.salary.period}"
        return f"*{job.title or 'Job'}*{(' • ' + place) if place else ''}{sal}"

    @staticmethod
    def _candidate_summary(c) -> str:
        place = c.location.city or c.location.district or ""
        exp = f"{c.experience_years} yrs" if c.experience_years is not None else ""
        skills = ", ".join(c.skills[:4])
        parts = [p for p in [c.name or "Candidate", exp, skills, place] if p]
        return " • ".join(parts)

    # ── Double opt-in ─────────────────────────────────────────────────────────
    def propose_to_candidate(self, match_id: str) -> None:
        m = MatchRepo.get(match_id)
        if not m:
            return
        c = CandidateRepo.get(m.candidate_id)
        job = JobRepo.get(m.job_id)
        if not c or not job:
            return
        body = (
            f"नया काम मिला! 💼\n\n{self._job_summary(job)}\n\n{m.pitch_for_candidate}\n\n"
            "क्या आप इसमें interested हैं?"
        )
        buttons = [
            {"id": f"optin_yes:{match_id}", "title": t("optin_yes", HI)},
            {"id": f"optin_no:{match_id}", "title": t("optin_no", HI)},
        ]
        self.send_buttons(c.wa_id, body, buttons, agent="matchmaker")
        short_term.set_flow(c.wa_id, "optin")
        EventRepo.log("match_proposed", c.wa_id, {"match_id": match_id, "job_id": job.id})

    def propose_to_employer(self, match_id: str) -> None:
        m = MatchRepo.get(match_id)
        if not m:
            return
        c = CandidateRepo.get(m.candidate_id)
        job = JobRepo.get(m.job_id)
        emp = EmployerRepo.get(job.employer_id) if job else None
        if not c or not job or not emp:
            return
        body = (
            f"एक candidate आपके job '*{job.title}*' में interested है! 👤\n\n"
            f"{self._candidate_summary(c)}\n\n{m.pitch_for_employer}\n\n"
            "क्या आप आगे बढ़ना चाहते हैं?"
        )
        buttons = [
            {"id": f"optin_yes:{match_id}", "title": t("optin_yes", HI)},
            {"id": f"optin_no:{match_id}", "title": t("optin_no", HI)},
        ]
        self.send_buttons(emp.wa_id, body, buttons, agent="matchmaker")
        short_term.set_flow(emp.wa_id, "optin")
        EventRepo.log("employer_review", emp.wa_id, {"match_id": match_id, "job_id": job.id})

    def handle_optin_reply(self, wa_id: str, reply_id: str) -> Optional[str]:
        """Process an 'optin_yes:<id>' / 'optin_no:<id>' tap. Returns an ack to send back."""
        if ":" not in reply_id:
            return None
        action, mid = reply_id.split(":", 1)
        yes = action == "optin_yes"
        m = MatchRepo.get(mid)
        if not m:
            return None
        c = CandidateRepo.get(m.candidate_id)
        job = JobRepo.get(m.job_id)
        emp = EmployerRepo.get(job.employer_id) if job else None
        now = _now()

        # Candidate side (asked first)
        if c and wa_id == c.wa_id:
            short_term.set_flow(wa_id, "idle")
            if yes:
                MatchRepo.update(
                    mid,
                    {"candidate_optin": "yes", "candidate_optin_at": now, "status": "candidate_accepted"},
                )
                EventRepo.log("candidate_accepted", wa_id, {"match_id": mid})
                self.propose_to_employer(mid)
                return "बढ़िया! ✅ मैंने employer को बता दिया है। जल्द अपडेट मिलेगा।"
            MatchRepo.update(
                mid,
                {"candidate_optin": "no", "candidate_optin_at": now, "status": "candidate_declined"},
            )
            EventRepo.log("candidate_declined", wa_id, {"match_id": mid})
            return "ठीक है, कोई बात नहीं। मैं और काम ढूँढता रहूँगा।"

        # Employer side (asked only after candidate's yes)
        if emp and wa_id == emp.wa_id:
            short_term.set_flow(wa_id, "idle")
            if yes:
                MatchRepo.update(
                    mid,
                    {"employer_optin": "yes", "employer_optin_at": now, "status": "placed"},
                )
                self._exchange_contacts(job, c, emp)
                EventRepo.log("match_placed", wa_id, {"match_id": mid})
                return "शानदार! ✅ मैंने दोनों के contact share कर दिए हैं।"
            MatchRepo.update(
                mid,
                {"employer_optin": "no", "employer_optin_at": now, "status": "employer_declined"},
            )
            EventRepo.log("employer_declined", wa_id, {"match_id": mid})
            return "ठीक है। मैं और candidates ढूँढता रहूँगा।"

        return None

    def _exchange_contacts(self, job, c, emp) -> None:
        company = emp.company_name or "Employer"
        self.send_text(
            c.wa_id,
            f"🎉 '{job.title}' के लिए *{company}* आपसे संपर्क करना चाहते हैं!\nContact: +{emp.wa_id}",
            agent="messenger",
        )
        self.send_text(
            emp.wa_id,
            f"🎉 *{c.name or 'Candidate'}* तैयार हैं!\nContact: +{c.wa_id}",
            agent="messenger",
        )


@lru_cache()
def get_messenger() -> Messenger:
    """Cached singleton Messenger."""
    return Messenger()
