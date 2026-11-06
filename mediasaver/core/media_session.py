from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pyrogram.file_id import FileId

from mediasaver.core.downloader import extract_media_info

logger = logging.getLogger(__name__)

_STOP_TIMEOUT = 10.0


def media_dc_id(message) -> Optional[int]:
    info = extract_media_info(message)
    if info is None:
        return None
    media = getattr(message, info.media_type, None)
    try:
        return FileId.decode(media.file_id).dc_id
    except Exception:
        return None


async def reset_media_session(client, message) -> None:
    """Drop the cached Pyrogram media session for the DC holding this
    message's file, so the next download opens a fresh connection.

    Pyrogram keeps media sessions in `client.media_sessions` forever; after a
    network hiccup one can stay half-dead and time out every request while
    the main session (and so the bot's replies) keeps working. Unknown DC ->
    reset all media sessions."""
    sessions = getattr(client, "media_sessions", None)
    if not sessions:
        return
    dc_id = media_dc_id(message)
    dc_ids = [dc_id] if dc_id in sessions else list(sessions)
    for dc in dc_ids:
        session = sessions.pop(dc, None)
        if session is None:
            continue
        logger.warning("Resetting media session for DC%s", dc)
        try:
            await asyncio.wait_for(session.stop(), _STOP_TIMEOUT)
        except Exception:
            logger.exception("Failed to stop media session for DC%s", dc)
