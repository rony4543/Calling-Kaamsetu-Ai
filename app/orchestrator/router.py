"""
The Router — the Brain of Kaamsetu.

Every inbound WhatsApp message funnels through `Router.handle(InboundMessage)`:

    parse → idempotency (caller) → transcribe voice → log inbound → load/create
    session → route by active_flow → send reply → update short-term memory

Routing by `active_flow`:
    welcome / unknown role → welcome + role buttons  (role_candidate / role_employer)
    candidate_intake       → CandidateIntakeAgent
    employer_intake        → EmployerIntakeAgent
    optin                  → (button) Messenger.handle_optin_reply
    idle                   → absorb profile updates / start a new job posting

Opt-in button taps (optin_yes:/optin_no:) are handled globally, before flow
routing, because they can arrive in any state. Employer quick-reply buttons
(emp_opt:<value>) are unwrapped into plain text and fed to the active flow.

Heavy follow-on work (synthetic-memory refresh + matchmaking) runs inline here;
the webhook invokes the router as a FastAPI background task, so WhatsApp still
gets its 200 immediately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple

from app.agents.candidate_intake import CandidateIntakeAgent
from app.agents.employer_intake import EmployerIntakeAgent
from app.agents.matchmaker import MatchmakerAgent
from app.agents.messenger import get_messenger
from app.agents.synthetic_memory import SyntheticMemoryAgent
from app.integrations.openai_client import get_openai_client
from app.integrations.whatsapp_api import get_whatsapp_client
from app.memory import short_term
from app.orchestrator.state_machine import detect_role_from_text
from app.supabase.repositories import (
    CandidateRepo,
    ConversationRepo,
    EmployerRepo,
    JobRepo,
)
from app.supabase.schemas import (
    ActiveFlow,
    Candidate,
    ConversationMessage,
    Employer,
    Job,
    UserRole,
)
from app.utils import i18n

logger = logging.getLogger(__name__)


@dataclass
class InboundMessage:
    """Normalized inbound message (the webhook builds this from WhatsApp JSON)."""

    wa_id: str
    message_id: str
    type: str = "text"  # text | interactive | audio | voice | image | document
    text: str = ""
    interactive_id: Optional[str] = None
    media_id: Optional[str] = None
    media_mime: Optional[str] = None


class Router:
    def __init__(self) -> None:
        self.candidate_intake = CandidateIntakeAgent()
        self.employer_intake = EmployerIntakeAgent()
        self.matchmaker = MatchmakerAgent()
        self.synthetic = SyntheticMemoryAgent()
        self.messenger = get_messenger()
        self.wa = get_whatsapp_client()
        self.openai = get_openai_client()

    # ── Entry point ───────────────────────────────────────────────────────────
    def handle(self, msg: InboundMessage) -> None:
        try:
            self._handle(msg)
        except Exception as exc:  # noqa: BLE001 — a webhook must never crash the app
            logger.exception("Router failed for wa_id=%s: %s", msg.wa_id, exc)
            try:
                self.messenger.send_text(msg.wa_id, i18n.t("fallback", i18n.HI), agent="router")
            except Exception:  # noqa: BLE001
                pass

    def _handle(self, msg: InboundMessage) -> None:
        wa_id = msg.wa_id
        text, interactive_id = self._resolve_content(msg)
        lang = i18n.detect_language(text) if text else i18n.HI

        # Log the inbound turn (also creates the users row on first contact).
        self._log_inbound(wa_id, msg, text)

        session = short_term.get_or_create(wa_id)
        if text:
            short_term.record(wa_id, "user", text)

        # 1. Opt-in button taps — valid in any state, handled first.
        if interactive_id and (
            interactive_id.startswith("optin_yes:") or interactive_id.startswith("optin_no:")
        ):
            ack = self.messenger.handle_optin_reply(wa_id, interactive_id)
            if ack:
                self.messenger.send_text(wa_id, ack, agent="messenger")
            return

        # 2. Role-selection buttons from the welcome screen.
        if interactive_id == "role_candidate":
            return self._enter_role(wa_id, UserRole.CANDIDATE, seed_text="", lang=lang)
        if interactive_id == "role_employer":
            return self._enter_role(wa_id, UserRole.EMPLOYER, seed_text="", lang=lang)

        # 3. Employer quick-reply buttons -> unwrap to plain text for the flow.
        if interactive_id and interactive_id.startswith("emp_opt:"):
            text = interactive_id.split(":", 1)[1]
            interactive_id = None

        # 4. Nothing usable (e.g. an image with no caption).
        if not text and not interactive_id:
            return self._handle_empty(wa_id, session, lang)

        # 5. Route by current flow.
        flow = session.active_flow
        role = session.role

        if flow == ActiveFlow.CANDIDATE_INTAKE:
            return self._continue_candidate(wa_id, text, lang)
        if flow == ActiveFlow.EMPLOYER_INTAKE:
            return self._continue_employer(wa_id, text, lang)
        if flow == ActiveFlow.WELCOME:
            return self._entry(wa_id, text, lang)
        if flow == ActiveFlow.OPTIN:
            # A free-text message during an opt-in wait — treat by role, softly.
            return self._route_idle(wa_id, role, text, lang)
        if flow == ActiveFlow.IDLE:
            return self._route_idle(wa_id, role, text, lang)

        return self._entry(wa_id, text, lang)

    # ── Welcome / role selection ───────────────────────────────────────────────
    def _entry(self, wa_id: str, text: str, lang: str) -> None:
        role = detect_role_from_text(text)
        if role is UserRole.CANDIDATE:
            return self._enter_role(wa_id, UserRole.CANDIDATE, seed_text=text, lang=lang)
        if role is UserRole.EMPLOYER:
            return self._enter_role(wa_id, UserRole.EMPLOYER, seed_text=text, lang=lang)
        return self._send_welcome(wa_id, lang)

    def _send_welcome(self, wa_id: str, lang: str) -> None:
        body = f"{i18n.t('welcome', lang)}\n\n{i18n.t('ask_role', lang)}"
        buttons = [
            {"id": "role_candidate", "title": i18n.t("role_candidate", lang)},
            {"id": "role_employer", "title": i18n.t("role_employer", lang)},
        ]
        self.messenger.send_buttons(wa_id, body, buttons, agent="router")
        short_term.set_flow(wa_id, ActiveFlow.WELCOME, "router")

    def _enter_role(self, wa_id: str, role: UserRole, seed_text: str, lang: str) -> None:
        if role is UserRole.CANDIDATE:
            return self._start_candidate(wa_id, seed_text, lang)
        return self._start_employer(wa_id, seed_text, lang)

    # ── Candidate flow ─────────────────────────────────────────────────────────
    def _start_candidate(self, wa_id: str, seed_text: str, lang: str) -> None:
        candidate = CandidateRepo.get_by_wa_id(wa_id)
        if not candidate:
            CandidateRepo.create(Candidate(wa_id=wa_id))
            candidate = CandidateRepo.get_by_wa_id(wa_id)
        short_term.set_role(wa_id, UserRole.CANDIDATE)
        short_term.set_flow(wa_id, ActiveFlow.CANDIDATE_INTAKE, "candidate_intake")
        session = short_term.get_or_create(wa_id)
        result = self.candidate_intake.handle(session, candidate, seed_text, lang)
        self._finish_candidate_turn(wa_id, result, lang)

    def _continue_candidate(self, wa_id: str, text: str, lang: str) -> None:
        candidate = CandidateRepo.get_by_wa_id(wa_id)
        if not candidate:
            return self._start_candidate(wa_id, text, lang)
        session = short_term.get_or_create(wa_id)
        result = self.candidate_intake.handle(session, candidate, text, lang)
        self._finish_candidate_turn(wa_id, result, lang)

    def _finish_candidate_turn(
        self, wa_id: str, result: dict, lang: str, completion_msg: Optional[str] = None
    ) -> None:
        if result.get("done"):
            self.messenger.send_text(
                wa_id, completion_msg or i18n.t("profile_live", lang), agent="candidate_intake"
            )
            short_term.set_flow(wa_id, ActiveFlow.IDLE, "candidate_intake")
            short_term.set_expected_field(wa_id, None)
            self._post_live_candidate(wa_id, result.get("candidate"))
            return
        short_term.set_expected_field(wa_id, result.get("next_field"))
        question = result.get("text", "")
        self.messenger.send_text(wa_id, question, agent="candidate_intake")
        short_term.record(wa_id, "assistant", question)

    def _post_live_candidate(self, wa_id: str, candidate: Optional[Candidate]) -> None:
        try:
            self.synthetic.refresh_for(wa_id, "candidate")
        except Exception as exc:  # noqa: BLE001
            logger.warning("synthetic refresh (candidate) failed: %s", exc)
        try:
            if candidate and candidate.id:
                self.matchmaker.run_for_candidate(candidate.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("matchmaker run_for_candidate failed: %s", exc)

    # ── Employer flow ──────────────────────────────────────────────────────────
    def _start_employer(self, wa_id: str, seed_text: str, lang: str) -> None:
        employer = EmployerRepo.get_by_wa_id(wa_id)
        if not employer:
            EmployerRepo.create(Employer(wa_id=wa_id))
            employer = EmployerRepo.get_by_wa_id(wa_id)
        job = JobRepo.get_latest_draft_by_employer(employer.id)
        if not job:
            job_id = JobRepo.create(Job(employer_id=employer.id))
            job = JobRepo.get(job_id)
        short_term.set_role(wa_id, UserRole.EMPLOYER)
        short_term.set_flow(wa_id, ActiveFlow.EMPLOYER_INTAKE, "employer_intake")
        session = short_term.get_or_create(wa_id)
        result = self.employer_intake.handle(session, job, seed_text, lang)
        self._finish_employer_turn(wa_id, result, lang)

    def _continue_employer(self, wa_id: str, text: str, lang: str) -> None:
        employer = EmployerRepo.get_by_wa_id(wa_id)
        if not employer:
            return self._start_employer(wa_id, text, lang)
        job = JobRepo.get_latest_draft_by_employer(employer.id)
        if not job:
            return self._start_employer(wa_id, text, lang)
        session = short_term.get_or_create(wa_id)
        result = self.employer_intake.handle(session, job, text, lang)
        self._finish_employer_turn(wa_id, result, lang)

    def _finish_employer_turn(
        self, wa_id: str, result: dict, lang: str, completion_msg: Optional[str] = None
    ) -> None:
        if result.get("done"):
            self.messenger.send_text(
                wa_id, completion_msg or i18n.t("job_live", lang), agent="employer_intake"
            )
            short_term.set_flow(wa_id, ActiveFlow.IDLE, "employer_intake")
            short_term.set_expected_field(wa_id, None)
            self._post_live_job(wa_id, result.get("job"))
            return
        short_term.set_expected_field(wa_id, result.get("next_field"))
        question = result.get("text", "")
        buttons = result.get("buttons")
        if buttons:
            self.messenger.send_buttons(wa_id, question, buttons, agent="employer_intake")
        else:
            self.messenger.send_text(wa_id, question, agent="employer_intake")
        short_term.record(wa_id, "assistant", question)

    def _post_live_job(self, wa_id: str, job: Optional[Job]) -> None:
        try:
            self.synthetic.refresh_for(wa_id, "employer")
        except Exception as exc:  # noqa: BLE001
            logger.warning("synthetic refresh (employer) failed: %s", exc)
        try:
            if job and job.id:
                self.matchmaker.run_for_job(job.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("matchmaker run_for_job failed: %s", exc)

    # ── Idle ───────────────────────────────────────────────────────────────────
    def _route_idle(self, wa_id: str, role: UserRole, text: str, lang: str) -> None:
        """
        A message when there's no active intake. Candidates get profile updates
        absorbed; employers start/continue a job posting; unknown role -> welcome.
        """
        if role is UserRole.CANDIDATE:
            candidate = CandidateRepo.get_by_wa_id(wa_id)
            if not candidate:
                return self._entry(wa_id, text, lang)
            session = short_term.get_or_create(wa_id)
            result = self.candidate_intake.handle(session, candidate, text, lang)
            if not result.get("done"):
                short_term.set_flow(wa_id, ActiveFlow.CANDIDATE_INTAKE, "candidate_intake")
            self._finish_candidate_turn(
                wa_id, result, lang, completion_msg=i18n.t("profile_updated", lang)
            )
            return
        if role is UserRole.EMPLOYER:
            return self._start_employer(wa_id, text, lang)
        return self._entry(wa_id, text, lang)

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _resolve_content(self, msg: InboundMessage) -> Tuple[str, Optional[str]]:
        """Return (text, interactive_id). Transcribes voice notes via Whisper."""
        if msg.type in ("audio", "voice") and msg.media_id:
            try:
                content, _mime = self.wa.download_media(msg.media_id)
                if content:
                    transcript = self.openai.transcribe(content, filename="voice.ogg")
                    return (transcript or ""), None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Voice transcription failed: %s", exc)
            return "", None
        if msg.type == "interactive":
            return (msg.text or ""), msg.interactive_id
        return (msg.text or ""), msg.interactive_id

    def _handle_empty(self, wa_id: str, session, lang: str) -> None:
        if session.active_flow == ActiveFlow.WELCOME:
            return self._send_welcome(wa_id, lang)
        self.messenger.send_text(wa_id, i18n.t("send_text_please", lang), agent="router")

    def _log_inbound(self, wa_id: str, msg: InboundMessage, text: str) -> None:
        try:
            ConversationRepo.log_message(
                wa_id,
                ConversationMessage(
                    direction="inbound",
                    message_type=msg.type,
                    content=text or "",
                    wa_message_id=msg.message_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to log inbound message: %s", exc)


@lru_cache()
def get_router() -> Router:
    """Cached singleton Router."""
    return Router()
