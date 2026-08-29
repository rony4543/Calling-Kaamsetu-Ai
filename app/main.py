"""
Kaamsetu — WhatsApp AI matchmaker (FastAPI application entry point).

Startup:  load dynamic config from the Supabase `app_config` table, then start
          the APScheduler background jobs (matchmaker + synthetic refresh).
Shutdown: stop the scheduler cleanly.

Run locally:
    uvicorn app.main:app --reload --port 8000

The only user-facing surface is the WhatsApp webhook at /whatsapp/webhook.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_config
from app.scheduler import get_scheduler
from app.supabase.repositories import ConfigRepo
from app.webhooks.whatsapp import router as whatsapp_router

logging.basicConfig(
    level=getattr(logging, get_config().log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("kaamsetu")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    try:
        remote = ConfigRepo.get_all()
        if remote:
            get_config().apply_remote_config(remote)
            logger.info("Applied remote config (%d keys)", len(remote))
    except Exception as exc:  # noqa: BLE001 — remote config is best-effort
        logger.warning("Could not load remote config (using defaults): %s", exc)

    scheduler = get_scheduler()
    try:
        scheduler.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scheduler failed to start: %s", exc)

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    try:
        scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Kaamsetu", version="1.0.0", lifespan=lifespan)
app.include_router(whatsapp_router)


@app.get("/")
async def root() -> dict:
    return {"service": "Kaamsetu — WhatsApp AI matchmaker", "status": "running"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
