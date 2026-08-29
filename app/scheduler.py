"""
APScheduler background jobs — the autonomous heartbeat of Kaamsetu.

Two periodic jobs (intervals come from config / the app_config table):
  - matchmaker sweep      : score every live candidate against every live job
                            and fire double opt-ins for matches at/above threshold.
  - synthetic refresh     : rebuild the inferred (soft) memory for live candidates.

Both jobs are guarded (max_instances=1, coalesce=True) so a slow run never
stacks up, and every unit of work is wrapped so one failure can't kill the loop.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from apscheduler.schedulers.background import BackgroundScheduler

from app.agents.matchmaker import MatchmakerAgent
from app.agents.synthetic_memory import SyntheticMemoryAgent
from app.config import get_config
from app.supabase.repositories import CandidateRepo

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self._sched = BackgroundScheduler(timezone="UTC")
        self.matchmaker = MatchmakerAgent()
        self.synthetic = SyntheticMemoryAgent()

    def start(self) -> None:
        cfg = get_config()
        self._sched.add_job(
            self._run_matchmaker,
            "interval",
            minutes=max(1, cfg.matchmaker_interval_minutes),
            id="matchmaker_sweep",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._sched.add_job(
            self._run_synthetic_refresh,
            "interval",
            minutes=max(1, cfg.synthetic_refresh_interval_minutes),
            id="synthetic_refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._sched.start()
        logger.info(
            "Scheduler started (matchmaker=%dm, synthetic=%dm)",
            cfg.matchmaker_interval_minutes,
            cfg.synthetic_refresh_interval_minutes,
        )

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ── Jobs ────────────────────────────────────────────────────────────────
    def _run_matchmaker(self) -> None:
        try:
            self.matchmaker.sweep()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Matchmaker sweep failed: %s", exc)

    def _run_synthetic_refresh(self) -> None:
        try:
            candidates = CandidateRepo.get_all_live()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Synthetic refresh: could not list candidates: %s", exc)
            return
        logger.info("Synthetic refresh over %d live candidates", len(candidates))
        for c in candidates:
            try:
                self.synthetic.refresh_for(c.wa_id, "candidate")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Synthetic refresh failed for %s: %s", c.wa_id, exc)


@lru_cache()
def get_scheduler() -> Scheduler:
    """Cached singleton Scheduler."""
    return Scheduler()
