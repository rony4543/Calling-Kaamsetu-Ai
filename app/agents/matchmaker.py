"""
Matchmaker — the autonomous scoring + proposal engine.

It scores ONE candidate against ONE job (0-100) using the configurable weighted
rubric and JSON mode, then — for any match at/above the threshold — creates a
`matches` row and kicks off the double opt-in via the Messenger.

Entry points:
  - run_for_candidate(candidate_id): score a (newly-live) candidate vs all live jobs
  - run_for_job(job_id):             score a (newly-live) job vs all live candidates
  - sweep():                         the periodic APScheduler pass over everything

Runs fully autonomously: nothing here waits on a human except the opt-in replies,
which come back through the router -> Messenger.handle_optin_reply.
"""

from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.agents.messenger import get_messenger
from app.config import get_config
from app.memory import synthetic
from app.supabase.repositories import CandidateRepo, JobRepo, MatchRepo
from app.supabase.schemas import (
    Candidate,
    DimensionScores,
    Job,
    Match,
    MatchScoreResult,
)

logger = logging.getLogger(__name__)


class MatchmakerAgent(BaseAgent):
    name = "matchmaker"

    # ── Scoring ───────────────────────────────────────────────────────────────
    def score(self, job: Job, candidate: Candidate) -> MatchScoreResult:
        cfg = get_config()
        w = cfg.scoring_weights
        syn = synthetic.get_current(candidate.id) if candidate.id else None
        syn_view = {
            "soft_skills": (syn or {}).get("soft_skills", []),
            "tone": (syn or {}).get("tone"),
            "reliability_signal": (syn or {}).get("reliability_signal"),
            "summary": (syn or {}).get("summary"),
        }
        prompt = self.render(
            "matchmaker.j2",
            scoring_weights=json.dumps(w.model_dump()),
            job_json=json.dumps(self._job_json(job), ensure_ascii=False),
            candidate_json=json.dumps(self._candidate_json(candidate), ensure_ascii=False),
            candidate_synthetic=json.dumps(syn_view, ensure_ascii=False),
            skills_weight=w.skills,
            experience_weight=w.experience,
            location_weight=w.location,
            salary_weight=w.salary,
            availability_weight=w.availability,
            soft_weight=w.soft,
        )
        data = self.llm.chat_json(prompt, model=cfg.openai.scoring_model)
        return self._to_score(data)

    # ── Orchestration ───────────────────────────────────────────────────────
    def run_for_candidate(self, candidate_id: str) -> None:
        cfg = get_config()
        candidate = CandidateRepo.get(candidate_id)
        if not candidate or candidate.status.value != "live":
            return
        slots = cfg.max_pending_optins_per_candidate - len(
            MatchRepo.get_pending_for_candidate(candidate_id)
        )
        if slots <= 0:
            return
        for job in JobRepo.get_all_live():
            if slots <= 0:
                break
            if MatchRepo.exists_for_pair(candidate_id, job.id):
                continue
            if self._score_and_maybe_propose(job, candidate):
                slots -= 1

    def run_for_job(self, job_id: str) -> None:
        cfg = get_config()
        job = JobRepo.get(job_id)
        if not job or job.status.value != "live":
            return
        for candidate in CandidateRepo.get_all_live():
            if MatchRepo.exists_for_pair(candidate.id, job.id):
                continue
            if len(MatchRepo.get_pending_for_candidate(candidate.id)) >= cfg.max_pending_optins_per_candidate:
                continue
            self._score_and_maybe_propose(job, candidate)

    def sweep(self) -> None:
        """Periodic full pass — score every live candidate against every live job."""
        candidates = CandidateRepo.get_all_live()
        logger.info("Matchmaker sweep over %d live candidates", len(candidates))
        for candidate in candidates:
            try:
                self.run_for_candidate(candidate.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sweep failed for candidate %s: %s", candidate.id, exc)

    # ── Internals ─────────────────────────────────────────────────────────────
    def _score_and_maybe_propose(self, job: Job, candidate: Candidate) -> bool:
        """Score one pair; if it clears the threshold, create + propose. Returns True if proposed."""
        cfg = get_config()
        try:
            result = self.score(job, candidate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scoring failed (job=%s cand=%s): %s", job.id, candidate.id, exc)
            return False
        if result.overall_score < cfg.match_threshold:
            return False
        match = Match(
            job_id=job.id,
            candidate_id=candidate.id,
            overall_score=result.overall_score,
            dimension_scores=result.dimension_scores,
            rationale=result.rationale,
            red_flags=result.red_flags,
            pitch_for_employer=result.pitch_for_employer,
            pitch_for_candidate=result.pitch_for_candidate,
            confidence=result.confidence,
        )
        try:
            match_id = MatchRepo.create(match)
        except Exception as exc:  # noqa: BLE001
            # Most likely the unique(job_id, candidate_id) constraint — already matched.
            logger.debug("Match create skipped (job=%s cand=%s): %s", job.id, candidate.id, exc)
            return False
        get_messenger().propose_to_candidate(match_id)
        logger.info(
            "Proposed match %s (score=%d) job=%s cand=%s",
            match_id,
            result.overall_score,
            job.id,
            candidate.id,
        )
        return True

    @staticmethod
    def _to_score(data: dict) -> MatchScoreResult:
        ds = data.get("dimension_scores", {}) or {}
        return MatchScoreResult(
            overall_score=int(data.get("overall_score", 0) or 0),
            dimension_scores=DimensionScores(
                skills=int(ds.get("skills", 0) or 0),
                experience=int(ds.get("experience", 0) or 0),
                location=int(ds.get("location", 0) or 0),
                salary=int(ds.get("salary", 0) or 0),
                availability=int(ds.get("availability", 0) or 0),
                soft=int(ds.get("soft", 0) or 0),
            ),
            rationale=data.get("rationale", "") or "",
            red_flags=data.get("red_flags", []) or [],
            pitch_for_employer=data.get("pitch_for_employer", "") or "",
            pitch_for_candidate=data.get("pitch_for_candidate", "") or "",
            confidence=float(data.get("confidence", 0.0) or 0.0),
        )

    @staticmethod
    def _candidate_json(c: Candidate) -> dict:
        return {
            "skills": c.skills,
            "experience_years": c.experience_years,
            "education": c.education,
            "location": {
                "district": c.location.district,
                "city": c.location.city,
                "willing_to_relocate": c.location.willing_to_relocate,
                "max_commute_km": c.location.max_commute_km,
            },
            "expected_salary": {"min": c.expected_salary.min, "max": c.expected_salary.max},
            "job_type_pref": c.job_type_pref,
            "availability": c.availability,
            "languages": c.languages,
        }

    @staticmethod
    def _job_json(j: Job) -> dict:
        return {
            "title": j.title,
            "skills_required": j.skills_required,
            "nice_to_have": j.nice_to_have,
            "experience_min": j.experience_min,
            "location": {
                "district": j.location.district,
                "city": j.location.city,
                "remote_ok": j.location.remote_ok,
            },
            "job_type": j.job_type,
            "salary": {"min": j.salary.min, "max": j.salary.max},
            "urgency": j.urgency,
        }
