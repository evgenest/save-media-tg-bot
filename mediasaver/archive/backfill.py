from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional

from pyrogram.errors import FloodWait

from mediasaver.archive.group import Archives, ChatArchive, archive_for
from mediasaver.archive.speech import needs_transcript

# History backfill: bots can't page through chat history (no getHistory), but
# they can fetch messages by id, and supergroup ids are sequential. On start
# the bot scans ids from where the previous scan stopped (chat.json
# "scanned_until") up to the newest message, so the archive also covers
# messages sent before the bot joined or while it was down.

logger = logging.getLogger("archive.backfill")

CHUNK = 200  # max ids per messages.getMessages call
EMPTY_CHUNKS_TO_STOP = 2  # whole chunks with no message = past the newest one
PAUSE_SECONDS = 3.0  # between chunks, on top of Telegram's own flood waits


async def fetch_chunk(client, chat_id: int, ids: List[int], sleep=asyncio.sleep) -> list:
    while True:
        try:
            messages = await client.get_messages(chat_id, ids, replies=0)
        except FloodWait as exc:
            wait = int(getattr(exc, "value", 30) or 30)
            logger.info("Flood wait %ss on chat %s", wait, chat_id)
            await sleep(wait + 1)
            continue
        if not isinstance(messages, list):
            messages = [messages]
        return [m for m in messages if m is not None and not getattr(m, "empty", False)]


async def backfill_chat(
    client,
    chat_id: int,
    archives: Archives,
    *,
    sleep: Callable = asyncio.sleep,
    pause: float = PAUSE_SECONDS,
) -> int:
    """Scan one chat's history into its archives. Returns messages added."""
    chat_archives = [archive for (cid, _), archive in archives.items() if cid == chat_id]
    start = min(archive.store.read_meta().get("scanned_until", 0) for archive in chat_archives) + 1
    added = 0
    empty_chunks = 0
    current = start
    logger.info("Backfill of chat %s from id %s", chat_id, start)
    while empty_chunks < EMPTY_CHUNKS_TO_STOP:
        ids = list(range(current, current + CHUNK))
        messages = await fetch_chunk(client, chat_id, ids, sleep)
        current += CHUNK
        if not messages:
            empty_chunks += 1
            continue
        empty_chunks = 0

        touched: Dict[int, ChatArchive] = {}
        for message in sorted(messages, key=lambda m: m.id):
            archive = archive_for(archives, message)
            if archive is None:
                continue  # another topic
            async with archive.lock:
                if message.id in archive.known:
                    continue
                try:
                    await archive.ingest(client, message, render=False)
                except Exception:
                    logger.exception("Backfill failed on message %s in %s", message.id, chat_id)
                    continue
            touched[id(archive)] = archive
            added += 1

        last_id = max(message.id for message in messages)
        for archive in chat_archives:
            async with archive.lock:
                archive.store.update_meta(scanned_until=last_id)
        for archive in touched.values():
            async with archive.lock:
                await archive.render()
        logger.info("Backfill of chat %s: up to id %s, added %s", chat_id, last_id, added)
        await sleep(pause)

    for archive in chat_archives:
        for record in archive.store.load():
            if needs_transcript(record, archive.config.transcribe_max_seconds):
                await archive.transcribe(record)
    logger.info("Backfill of chat %s done, added %s", chat_id, added)
    return added


async def backfill_all(client, archives: Archives) -> None:
    for chat_id in sorted({cid for cid, _ in archives}):
        try:
            await backfill_chat(client, chat_id, archives)
        except Exception:
            logger.exception("Backfill of chat %s failed", chat_id)


def start_backfill(client, archives: Archives) -> Optional[asyncio.Task]:
    if not archives:
        return None
    return asyncio.create_task(backfill_all(client, archives))
