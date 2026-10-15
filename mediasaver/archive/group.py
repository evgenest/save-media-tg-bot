from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Awaitable, Callable, Dict, Optional

from pyrogram import Client, filters
from pyrogram.types import Message

from mediasaver.archive.config import ArchiveConfig, ArchiveKey
from mediasaver.archive.record import (
    MEDIA_KINDS,
    build_record,
    markdown_body,
    media_meta,
    message_kind,
    topic_of,
)
from mediasaver.archive.render import render_archive
from mediasaver.archive.speech import claude_passes, needs_transcript, transcribe_record
from mediasaver.archive.store import ArchiveStore
from mediasaver.core.downloader import resolve_filename_collision, sanitize_filename
from mediasaver.media.rich_media import download_rich_media

# Group archive mode: the bot sits in the configured group chats and silently
# records every message (text, formatting, media, voice transcripts, replies,
# edits) into a markdown archive per chat. It never writes to the chat.
#
# Bots only see all group messages with privacy mode off (BotFather
# /setprivacy -> Disable, then re-add the bot) or as a group admin. Messages
# sent before the bot joined (or while it was down) are read on start by
# backfill.py - bots can fetch messages by id.

logger = logging.getLogger("archive")

_DEFAULT_EXTENSIONS = {"photo": ".jpg", "voice": ".ogg", "video_note": ".mp4", "video": ".mp4"}
DOWNLOAD_RETRIES = 2


def media_file_name(message_id: int, meta: dict) -> str:
    original = meta.get("file_name")
    if original:
        return sanitize_filename(f"{message_id}_{original}")
    mime = meta.get("mime_type")
    extension = (mimetypes.guess_extension(mime) if mime else None) or _DEFAULT_EXTENSIONS.get(
        meta["kind"], ""
    )
    return f"{message_id}_{meta['kind']}{extension}"


class ChatArchive:
    def __init__(self, store: ArchiveStore, config: ArchiveConfig) -> None:
        self.store = store
        self.config = config
        self.lock = asyncio.Lock()
        # One sweep of pending transcriptions at a time (backfill, reload).
        self.transcribe_lock = asyncio.Lock()
        # Ids already recorded - the history backfill skips them.
        self.known: "set[int]" = store.known_ids()

    async def render(self) -> None:
        meta = self.store.read_meta()
        title = meta.get("topic_title") or meta.get("title") or "Telegram"
        records = await asyncio.to_thread(self.store.load)
        await asyncio.to_thread(render_archive, self.store.root, records, self.config.timezone, title)

    async def download(self, client: Client, message: Message, kind: str) -> dict:
        meta = media_meta(message, kind)
        name = resolve_filename_collision(
            self.store.media_dir, media_file_name(message.id, meta)
        )
        dest = self.store.media_dir / name
        error: Optional[str] = None
        for _ in range(DOWNLOAD_RETRIES + 1):
            try:
                await client.download_media(message, file_name=str(dest))
                error = None
                break
            except Exception as exc:  # retried; a failure is recorded, never fatal
                error = str(exc)
        if error is None and dest.exists():
            meta["path"] = f"{self.store.media_dir.name}/{name}"
        else:
            meta["error"] = error or "файл не скачан"
        return meta

    async def ingest(self, client: Client, message: Message, *, render: bool = True) -> dict:
        """Record a message and download its media. Caller holds the lock."""
        self.store.ensure_dirs()
        title = getattr(message.chat, "title", None)
        if title and self.store.read_meta().get("title") != title:
            self.store.update_meta(id=message.chat.id, title=title)

        topic = getattr(message, "forum_topic_created", None)
        if topic is not None and getattr(topic, "title", None):
            self.store.update_meta(topic_title=topic.title)

        kind = message_kind(message)
        attachments = None
        rich_files: list = []
        if kind == "rich":
            media = await download_rich_media(
                client, message.rich_message, self.store.media_dir, stem=f"{message.id}_rich"
            )
            # Day files live in days/, media next to it.
            attachments = {
                key: (f"../{self.store.media_dir.name}/{name}" if name else None)
                for key, name in media.attachments.items()
            }
            rich_files = media.stored_names

        record = build_record(message, attachments=attachments)
        if rich_files:
            record["rich_files"] = rich_files
        self.store.append(record)
        self.known.add(message.id)
        if render:
            await self.render()

        if kind in MEDIA_KINDS and kind != "sticker":
            item = await self.download(client, message, kind)
            record["media"] = [item]
            self.store.append({"id": message.id, "media": [item]})
            if render:
                await self.render()
        return record

    async def transcribe(self, record: dict, *, retry_errors: bool = False) -> None:
        if not self.config.elevenlabs_api_key or not needs_transcript(
            record, self.config.transcribe_max_seconds, retry_errors=retry_errors
        ):
            return
        await asyncio.to_thread(
            transcribe_record,
            self.store,
            record,
            self.config.elevenlabs_api_key,
            model=self.config.stt_model,
            **claude_passes(self.config.speaker_model, self.config.claude_bin),
        )
        async with self.lock:
            await self.render()

    async def transcribe_pending(self, *, retry_errors: bool = False) -> None:
        """Transcribe every record still waiting for it (e.g. received before
        the ElevenLabs key was set)."""
        async with self.transcribe_lock:
            for record in await asyncio.to_thread(self.store.load):
                await self.transcribe(record, retry_errors=retry_errors)

    async def on_message(self, client: Client, message: Message) -> None:
        async with self.lock:
            if message.id in self.known:  # already read by the backfill
                return
            record = await self.ingest(client, message)
        # Outside the lock: a long voice message shouldn't hold up the chat.
        await self.transcribe(record)

    async def on_edit(self, message: Message) -> None:
        if getattr(message, "edit_date", None) is None:
            return  # reactions and other non-edit updates
        async with self.lock:
            text = markdown_body(message)
            current = next((r for r in self.store.load() if r["id"] == message.id), None)
            if current is not None and current.get("text") == text:
                return
            update = {"id": message.id, "text": text, "edit_date": build_record(message)["edit_date"]}
            if current is None:  # edited before the bot saw it - store it whole
                update = build_record(message)
            self.store.append(update)
            self.known.add(message.id)
            await self.render()


Archives = Dict[ArchiveKey, ChatArchive]


def archive_for(archives: Archives, message: Message) -> Optional[ChatArchive]:
    """The archive of the message's topic, else of its whole chat."""
    chat_id = message.chat.id
    return archives.get((chat_id, topic_of(message))) or archives.get((chat_id, None))


def register_archive_handlers(
    app: Client,
    archives: Archives,
    *,
    refresh: Optional[Callable[[Client], Awaitable[None]]] = None,
    group: int = 1,
) -> None:
    """Attach the archive handlers to the bot's client. A separate handler
    group, so they run independently of the private-chat media saver.
    `archives` may change at runtime (config reload); `refresh` runs before
    each message to pick such changes up."""
    warned: "set[int]" = set()

    async def find(client: Client, message: Message) -> Optional[ChatArchive]:
        if refresh is not None:
            try:
                await refresh(client)
            except Exception:
                logger.exception("Config refresh failed")
        archive = archive_for(archives, message)
        chat_id = message.chat.id
        # Other topics of an archived chat are skipped silently.
        archived_chats = {cid for cid, _ in archives}
        if archive is None and chat_id not in archived_chats and chat_id not in warned:
            warned.add(chat_id)
            logger.warning(
                "Chat not in ARCHIVE_CHATS, ignored: id=%s title=%r",
                chat_id,
                getattr(message.chat, "title", None),
            )
        return archive

    @app.on_message(filters.group, group=group)
    async def handle_message(client: Client, message: Message) -> None:
        archive = await find(client, message)
        if archive is None:
            return
        try:
            await archive.on_message(client, message)
        except Exception:
            logger.exception("Failed to archive message %s in %s", message.id, message.chat.id)

    @app.on_edited_message(filters.group, group=group)
    async def handle_edit(client: Client, message: Message) -> None:
        archive = await find(client, message)
        if archive is None:
            return
        try:
            await archive.on_edit(message)
        except Exception:
            logger.exception("Failed to archive edit %s in %s", message.id, message.chat.id)
