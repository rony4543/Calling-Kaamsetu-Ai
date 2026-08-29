"""
Supabase client initialization.

Singleton — call get_supabase_client() anywhere. Reads SUPABASE_URL and
SUPABASE_KEY from the environment (.env).

IMPORTANT: use the SERVICE ROLE key, not the anon key. The schema enables
Row-Level Security with no public policies, so the anon key is blocked from
every table. The service role key bypasses RLS, which is correct for a trusted
server-side backend like this one. Never ship the service role key to a client.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from supabase import Client, create_client

logger = logging.getLogger(__name__)


@lru_cache()
def get_supabase_client() -> Client:
    """Return the cached Supabase client, creating it on first use."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_KEY are not set. Add them to .env "
            "(use the service_role key from Project Settings -> API, not anon)."
        )
    client = create_client(url, key)
    logger.info("Supabase client initialized (%s)", url)
    return client
