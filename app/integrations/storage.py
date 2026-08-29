"""
Media storage — uploads voice notes, resumes and images to Supabase Storage
(the `media` bucket), replacing the old Firebase Storage integration.

Best-effort by design: if the bucket isn't configured or the upload fails, we
log and return None. Media is supplementary — a failed upload must never block
the conversation. The Whisper transcript of a voice note is what actually feeds
long-term memory, and that path does not depend on the upload succeeding.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from app.supabase.client import get_supabase_client

logger = logging.getLogger(__name__)

BUCKET = "media"


class Storage:
    def __init__(self) -> None:
        self._db = get_supabase_client()

    def upload(
        self, content: bytes, path: str, content_type: str = "application/octet-stream"
    ) -> Optional[str]:
        """
        Upload bytes to the media bucket at `path`. Returns the public URL, or
        None on failure. Assumes a bucket named 'media' exists (create it once
        in the Supabase dashboard; make it public if you want shareable URLs).
        """
        try:
            self._db.storage.from_(BUCKET).upload(
                path=path,
                file=content,
                file_options={"content-type": content_type, "upsert": "true"},
            )
            return self._db.storage.from_(BUCKET).get_public_url(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Storage upload failed for %s: %s", path, exc)
            return None

    def record_asset(
        self,
        user_id: Optional[str],
        kind: str,
        storage_path: str,
        mime_type: Optional[str] = None,
        transcript: Optional[str] = None,
    ) -> Optional[str]:
        """Insert a row in media_assets so the file is tracked. Returns its id."""
        try:
            resp = (
                self._db.table("media_assets")
                .insert(
                    {
                        "user_id": user_id,
                        "kind": kind,
                        "storage_path": storage_path,
                        "mime_type": mime_type,
                        "transcript": transcript,
                    }
                )
                .execute()
            )
            return resp.data[0]["id"] if resp.data else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record media asset: %s", exc)
            return None


@lru_cache()
def get_storage() -> Storage:
    """Cached singleton Storage helper."""
    return Storage()
