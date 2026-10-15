from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.types import Message

from archive_config import ArchiveConfig
from archive_record import MEDIA_KINDS, build_record, markdown_body, media_meta, message_kind
from archive_render import render_archive
from archive_speech import needs_transcript, transcribe_record
from archive_store import ArchiveStore
from downloader import resolve_filename_collision, sanitize_filename
from rich_media import download_rich_media

# Group archive mode: the bot sits in the configured group chats and silently
# records every message (text, formatting, media, voice transcripts, replies,
# edits) into a markdown archive per chat. It never writes to the chat.
#
# Bots only see all group messages with privacy mode off (BotFather
# /setprivacy -> Disable, then re-add the bot) or as a group admin. Messages
# sent before the bot joined are out of reach - import them from a Telegram
# Desktop export with tg_export_import.py.

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

    async def render(self) -> None:
        title = self.store.read_meta().get("title") or "Telegram"
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

    async def on_message(self, client: Client, message: Message) -> None:
        async with self.lock:
            self.store.ensure_dirs()
            title = getattr(message.chat, "title", None)
            if title and self.store.read_meta().get("title") != title:
                self.store.write_meta({"id": message.chat.id, "title": title})

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
            await self.render()

            if kind in MEDIA_KINDS and kind != "sticker":
                item = await self.download(client, message, kind)
                record["media"] = [item]
                self.store.append({"id": message.id, "media": [item]})
                await self.render()

        # Outside the lock: a long voice message shouldn't hold up the chat.
        if self.config.elevenlabs_api_key and needs_transcript(
            record, self.config.transcribe_max_seconds
        ):
            await asyncio.to_thread(
                transcribe_record, self.store, record, self.config.elevenlabs_api_key
            )
            async with self.lock:
                await self.render()

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
            await self.render()


def register_archive_handlers(app: Client, config: ArchiveConfig, *, group: int = 1) -> None:
    """Attach the archive handlers to the bot's client. A separate handler
    group, so they run independently of the private-chat media saver."""
    archives: Dict[int, ChatArchive] = {
        chat_id: ChatArchive(ArchiveStore(root), config) for chat_id, root in config.chats.items()
    }
    warned: "set[int]" = set()

    def archive_for(message: Message) -> Optional[ChatArchive]:
        archive = archives.get(message.chat.id)
        if archive is None and message.chat.id not in warned:
            warned.add(message.chat.id)
            logger.warning(
                "Chat not in ARCHIVE_CHATS, ignored: id=%s title=%r",
                message.chat.id,
                getattr(message.chat, "title", None),
            )
        return archive

    @app.on_message(filters.group, group=group)
    async def handle_message(client: Client, message: Message) -> None:
        archive = archive_for(message)
        if archive is None:
            return
        try:
            await archive.on_message(client, message)
        except Exception:
            logger.exception("Failed to archive message %s in %s", message.id, message.chat.id)

    @app.on_edited_message(filters.group, group=group)
    async def handle_edit(client: Client, message: Message) -> None:
        archive = archive_for(message)
        if archive is None:
            return
        try:
            await archive.on_edit(message)
        except Exception:
            logger.exception("Failed to archive edit %s in %s", message.id, message.chat.id)
