"""
Language utilities — the audience is India (Hindi / Hinglish / Marwari / English).

Detection is intentionally lightweight: presence of Devanagari => Hindi/Marwari,
otherwise we assume Latin-script Hinglish/English. The agents are told the
detected language and instructed to mirror the user, so perfect detection is not
required — this only picks sensible defaults for the canned system messages.
"""

from __future__ import annotations

import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Language codes used across the app / DB (users.preferred_language).
HI = "hi"    # Hindi / Hinglish
EN = "en"    # English
MWR = "mwr"  # Marwari (falls back to Hindi phrasing)

LANG_LABELS = {
    HI: "Hindi/Hinglish",
    EN: "English",
    MWR: "Marwari/Hindi",
}


def detect_language(text: str | None) -> str:
    """Best-effort language guess for a single message."""
    if not text:
        return HI
    if DEVANAGARI.search(text):
        return HI
    return EN


def label(lang: str) -> str:
    """Human-readable language name to hand to the LLM prompts."""
    return LANG_LABELS.get(lang, "Hindi/Hinglish")


# ── Canned system messages (not LLM-generated) ──────────────────────────────
# Kept short and bilingual. The router uses these for deterministic moments
# (welcome, role choice, consent) so we don't spend an LLM call on them.

_STRINGS = {
    "welcome": {
        HI: "नमस्ते! मैं Kaamsetu हूँ — काम और कामगार को जोड़ने वाला पुल। 🙏",
        EN: "Hi! I'm Kaamsetu — the bridge between work and workers. 🙏",
    },
    "ask_role": {
        HI: "क्या आप काम ढूँढ रहे हैं, या लोगों को काम पर रखना चाहते हैं?",
        EN: "Are you looking for work, or looking to hire people?",
    },
    "role_candidate": {HI: "काम चाहिए", EN: "Find work"},
    "role_employer": {HI: "स्टाफ़ चाहिए", EN: "Hire people"},
    "profile_live": {
        HI: "बढ़िया! आपकी प्रोफ़ाइल तैयार है। ✅ अब मैं आपके लिए सही काम ढूँढना शुरू कर दूँगा।",
        EN: "Great! Your profile is ready. ✅ I'll now start finding the right work for you.",
    },
    "job_live": {
        HI: "बढ़िया! आपकी नौकरी पोस्ट हो गई है। ✅ मैं सही उम्मीदवार ढूँढना शुरू कर दूँगा।",
        EN: "Great! Your job is posted. ✅ I'll start finding the right candidates.",
    },
    "optin_yes": {HI: "हाँ, दिलचस्पी है", EN: "Yes, interested"},
    "optin_no": {HI: "नहीं", EN: "No thanks"},
    "profile_updated": {
        HI: "समझ गया, आपकी जानकारी अपडेट कर दी। ✅",
        EN: "Got it — I've updated your details. ✅",
    },
    "send_text_please": {
        HI: "कृपया text या voice message में जवाब दें। 🙏",
        EN: "Please reply with a text or voice message. 🙏",
    },
    "fallback": {
        HI: "माफ़ कीजिए, मैं समझ नहीं पाया। आप 'काम' या 'स्टाफ़' लिख सकते हैं।",
        EN: "Sorry, I didn't catch that. You can type 'work' or 'hire'.",
    },
}


def t(key: str, lang: str = HI) -> str:
    """Return a canned string for a key in the given language (falls back to HI)."""
    entry = _STRINGS.get(key, {})
    return entry.get(lang) or entry.get(HI) or ""
