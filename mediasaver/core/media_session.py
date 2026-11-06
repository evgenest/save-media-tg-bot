from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from pyrogram.file_id import FileId

from mediasaver.core.downloader import extract_media_info

logger = logging.getLogger(__name__)

_STOP_TIMEOUT = 10.0


def target_file_id(target) -> Optional[str]:
    """file_id of a media object (Photo, Document...) or of a message's media."""
    file_id = getattr(target, "file_id", None)
    if isinstance(file_id, str):
        return file_id
    info = extract_media_info(target)
    if info is None:
        return None
    return getattr(getattr(target, info.media_type, None), "file_id", None)


def file_dc_id(file_id: Optional[str]) -> Optional[int]:
    if not file_id:
        return None
    try:
        return FileId.decode(file_id).dc_id
    except Exception:
        return None


async def reset_media_session(client, target) -> None:
    """Drop the cached Pyrogram media session for the DC holding the file of
    `target` (a message or a media object), so the next download opens a
    fresh connection.

    Pyrogram keeps media sessions in `client.media_sessions` forever; after a
    network hiccup one can stay half-dead and time out every request while
    the main session (and so the bot's replies) keeps working. Unknown DC ->
    reset all media sessions."""
    sessions = getattr(client, "media_sessions", None)
    if not sessions:
        return
    dc_id = file_dc_id(target_file_id(target))
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


def reset_hook(client, target) -> Callable[[], Awaitable[None]]:
    return lambda: reset_media_session(client, target)
