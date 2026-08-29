"""
Pytest bootstrap for the deterministic unit suite.

These tests exercise pure, LLM-free, DB-free logic (role detection, the
state-evaluator, i18n, and WhatsApp payload parsing). No real credentials or
network are needed — but we set harmless dummy env vars so that importing any
module which *might* lazily build a client never hard-fails on a missing key.
`load_dotenv()` (called inside app.config) uses override=False, so it won't
clobber these.
"""

import os
import sys
from pathlib import Path

# Make `import app...` work no matter where pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "test")
_DUMMY = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-service-role-key",
    "OPENROUTER_API_KEY": "test-openrouter-key",
}
for _k, _v in _DUMMY.items():
    if not os.getenv(_k):  # treats "" (blank in .env) as unset
        os.environ[_k] = _v
