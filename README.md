# Kaamsetu — WhatsApp AI Matchmaker

> *"The bridge to work"* — a two-sided talent marketplace that lives entirely inside WhatsApp.

## Architecture

- **FastAPI** backend handles all webhook events and background tasks
- **Firebase / Firestore** is the single source of truth for all data
- **OpenAI** powers extraction, scoring, and conversational agents
- **APScheduler** runs periodic matchmaking sweeps inside the backend
- **WhatsApp Cloud API** is the only user interface

## Quick Start

```bash
# 1. Clone and enter the project
cd "Calling Kaamsetu Ai"

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env with your API keys and Firebase service account path

# 5. Run the server
uvicorn app.main:app --reload --port 8000
```

## Project Structure

```
app/
├── main.py                  # FastAPI entry point
├── config.py                # Environment + Firestore config
├── scheduler.py             # APScheduler (matchmaking sweep, synthetic refresh)
├── webhooks/
│   └── whatsapp.py          # Inbound webhook handler
├── orchestrator/
│   ├── router.py            # The Brain — routes every message
│   └── state_machine.py     # Flow transitions
├── agents/
│   ├── base.py              # Agent ABC
│   ├── candidate_intake.py  # Job-seeker onboarding
│   ├── employer_intake.py   # Employer job posting
│   ├── extractor.py         # Unstructured → JSON
│   ├── state_evaluator.py   # Missing-field engine (NO LLM)
│   ├── matchmaker.py        # Autonomous AI scoring
│   ├── messenger.py         # Outbound + double opt-in
│   └── synthetic_memory.py  # Soft-skill inference
├── memory/
│   ├── short_term.py        # Session ring buffer
│   ├── long_term.py         # Candidate/job read-write
│   └── synthetic.py         # Synthetic memory read-write
├── firebase/
│   ├── client.py            # Admin SDK init
│   ├── schemas.py           # Pydantic models
│   └── repositories.py      # Typed CRUD
├── prompts/                 # Jinja2 prompt templates
├── integrations/
│   ├── whatsapp_api.py      # Outbound WhatsApp API
│   ├── openai_client.py     # OpenAI wrapper
│   └── storage.py           # Media → Firebase Storage
└── utils/
    ├── idempotency.py       # Message dedup
    └── i18n.py              # Hindi / Marwari / English
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification token |
| `WHATSAPP_API_TOKEN` | WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp business phone number ID |
| `OPENAI_API_KEY` | OpenAI API key |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Path to Firebase service account JSON |

## Key Design Principles

1. **Firebase is truth** — agents never trust their own memory over Firestore
2. **Anti-hallucination** — State Evaluator is deterministic (no LLM); agents cannot ask for known fields
3. **Autonomous Matchmaker** — fires on live events + APScheduler sweep, runs the entire opt-in pipeline itself
4. **Structured everything** — all LLM data outputs use JSON mode against fixed schemas
5. **Idempotent & resumable** — all state lives in Firestore, never in memory
