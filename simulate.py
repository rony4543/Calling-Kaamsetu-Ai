#!/usr/bin/env python3
"""
Kaamsetu local simulator — chat with the bot in your terminal, no WhatsApp needed.

It drives the REAL Router in-process: real OpenRouter LLM calls, real Supabase
reads/writes, the real deterministic state machine and matchmaker. Only the
WhatsApp client is stubbed, so the bot's replies print here instead of being
sent to Meta.

Prerequisites (both come from .env):
  • SUPABASE_KEY   — run `python3 scripts/supabase_bootstrap.py` first to fill it
  • OPENROUTER_API_KEY

Usage:
    python3 simulate.py
    SIM_WA_ID=919812345678 python3 simulate.py     # pick the simulated number

REPL commands:
    /tap <id>    simulate a button tap (e.g. `/tap role_candidate`,
                 `/tap optin_yes:<match_id>`)
    /whoami      show the simulated WhatsApp id
    /quit        exit
Anything else is sent as a plain text message.

Tip: each SIM_WA_ID is a separate "person" with its own persisted session and
profile in Supabase. Use a fresh number to start a clean conversation.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing app.config runs load_dotenv(), so .env is loaded here.
from app.config import get_config  # noqa: E402

_cfg = get_config()
if not _cfg.supabase.key:
    sys.exit(
        "✗ SUPABASE_KEY is empty in .env.\n"
        "  Run `python3 scripts/supabase_bootstrap.py` first so the simulator "
        "can read/write the database."
    )
if not _cfg.openai.api_key:
    sys.exit("✗ No NVIDIA_API_KEY / OPENAI_API_KEY in .env — the agents need it to think.")

# ── Stub the WhatsApp client BEFORE the router/messenger grab the singleton ───
from app.integrations.whatsapp_api import get_whatsapp_client  # noqa: E402

OUTBOX: list[tuple] = []
_wa = get_whatsapp_client()


def _cap_text(_to, body):
    OUTBOX.append(("text", body, None))
    return f"sim-out-{len(OUTBOX)}"


def _cap_buttons(_to, body, buttons):
    OUTBOX.append(("buttons", body, buttons))
    return f"sim-out-{len(OUTBOX)}"


def _cap_list(_to, body, _button_text, sections):
    OUTBOX.append(("list", body, sections))
    return f"sim-out-{len(OUTBOX)}"


def _no_media(_media_id):
    return (None, None)


_wa.send_text = _cap_text          # type: ignore[assignment]
_wa.send_buttons = _cap_buttons    # type: ignore[assignment]
_wa.send_list = _cap_list          # type: ignore[assignment]
_wa.download_media = _no_media     # type: ignore[assignment]

# Import the router only after stubbing (Router/Messenger cache the WA singleton).
from app.orchestrator.router import InboundMessage, get_router  # noqa: E402

SIM_WA_ID = os.getenv("SIM_WA_ID", "910000000001")


def _print_outbox() -> None:
    if not OUTBOX:
        print("bot> (no reply produced — check the server logs)")
        return
    for kind, body, extra in OUTBOX:
        print(f"bot> {body}")
        if kind == "buttons" and extra:
            titles = [b.get("title") or b.get("id") for b in extra]
            print("     " + "  ".join(f"[{t}]" for t in titles if t))
        elif kind == "list" and extra:
            titles = [r.get("title") for s in extra for r in s.get("rows", [])]
            print("     " + "  ".join(f"[{t}]" for t in titles if t))


def main() -> None:
    router = get_router()
    print(f"Kaamsetu simulator — you are +{SIM_WA_ID}")
    print("Type a message, or /tap <button_id>, /whoami, /quit.\n")

    n = 0
    while True:
        try:
            raw = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        if raw == "/quit":
            break
        if raw == "/whoami":
            print(f"     +{SIM_WA_ID}")
            continue

        n += 1
        mid = f"sim-in-{n}-{uuid.uuid4().hex[:8]}"
        if raw.startswith("/tap "):
            btn = raw[len("/tap "):].strip()
            msg = InboundMessage(
                wa_id=SIM_WA_ID, message_id=mid, type="interactive",
                text=btn, interactive_id=btn,
            )
        else:
            msg = InboundMessage(wa_id=SIM_WA_ID, message_id=mid, type="text", text=raw)

        OUTBOX.clear()
        router.handle(msg)  # never raises — the router swallows its own errors
        _print_outbox()


if __name__ == "__main__":
    main()
