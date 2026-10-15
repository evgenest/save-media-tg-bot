from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from mediasaver.archive.render import render_archive
from mediasaver.archive.config import _speaker_model
from mediasaver.archive.speech import claude_passes, transcribe_pending
from mediasaver.archive.transcribe import MODEL_ID
from mediasaver.archive.store import ArchiveStore
from mediasaver.core.downloader import resolve_filename_collision, sanitize_filename
from mediasaver.archive.tg_markdown import export_pieces_to_markdown

# Imports a Telegram Desktop chat export (Export chat history -> JSON, with
# media) into a group archive, in the same format the bot writes. Bots can't
# read messages sent before they joined, so this covers the history.
# Messages the bot already recorded are kept as they are (same message ids).
#
#   uv run python tg_export_import.py <export dir> <archive dir> [--transcribe]

_MEDIA_TYPES = {
    "voice_message": "voice",
    "audio_file": "audio",
    "video_file": "video",
    "video_message": "video_note",
    "animation": "animation",
    "sticker": "sticker",
}


def _utc(unix: Optional[str]) -> Optional[str]:
    if not unix:
        return None
    return datetime.fromtimestamp(int(unix), tz=timezone.utc).isoformat()


def _sender(message: dict, name_key: str, id_key: str) -> dict:
    raw_id = str(message.get(id_key) or "")
    digits = "".join(ch for ch in raw_id if ch.isdigit())
    return {"id": int(digits) if digits else None, "name": message.get(name_key) or "?", "username": None}


def _service_text(message: dict) -> str:
    action = message.get("action") or "service"
    if message.get("members"):
        return f"{action}: {', '.join(str(member) for member in message['members'])}"
    if action == "pin_message" and message.get("message_id"):
        return f"закреплено сообщение #{message['message_id']}"
    if message.get("title"):
        return f"{action}: {message['title']}"
    return action


def _extra(message: dict) -> Optional[str]:
    if message.get("poll"):
        poll = message["poll"]
        answers = "; ".join(answer.get("text", "") for answer in poll.get("answers") or [])
        return f"Опрос: {poll.get('question', '')} — {answers}"
    if message.get("location_information"):
        location = message["location_information"]
        title = message.get("place_name")
        return f"Место: {title + ' — ' if title else ''}{location.get('latitude')}, {location.get('longitude')}"
    if message.get("contact_information"):
        contact = message["contact_information"]
        name = " ".join(part for part in (contact.get("first_name"), contact.get("last_name")) if part)
        return f"Контакт: {name} {contact.get('phone_number', '')}".strip()
    if message.get("media_type") == "sticker":
        return f"Стикер {message.get('sticker_emoji') or ''}".strip()
    return None


def _copy_media(export_dir: Path, store: ArchiveStore, message: dict, relative: str, kind: str) -> dict:
    item: dict = {"kind": kind}
    for source_key, key in (("file_name", "file_name"), ("mime_type", "mime_type"), ("duration_seconds", "duration")):
        if message.get(source_key):
            item[key] = message[source_key]
    source = export_dir / relative
    if relative.startswith("(") or not source.is_file():
        item["error"] = "файла нет в экспорте"
        return item
    name = resolve_filename_collision(
        store.media_dir, sanitize_filename(f"{message['id']}_{source.name}")
    )
    shutil.copy2(source, store.media_dir / name)
    item["path"] = f"{store.media_dir.name}/{name}"
    return item


def export_record(export_dir: Path, store: ArchiveStore, message: dict) -> dict:
    is_service = message.get("type") == "service"
    record = {
        "id": message["id"],
        "date": _utc(message.get("date_unixtime")),
        "edit_date": _utc(message.get("edited_unixtime")),
        "from": _sender(message, "actor" if is_service else "from", "actor_id" if is_service else "from_id"),
        "kind": "service" if is_service else "text",
        "text": "" if is_service else export_pieces_to_markdown(message.get("text_entities") or message.get("text")),
        "reply_to": {"id": message["reply_to_message_id"]} if message.get("reply_to_message_id") else None,
        "quote": None,
        "forward_from": message.get("forwarded_from"),
        "media_group_id": None,
        "extra": _service_text(message) if is_service else _extra(message),
        "source": "export",
    }
    if is_service:
        return record

    media = []
    if message.get("photo"):
        record["kind"] = "photo"
        media.append(_copy_media(export_dir, store, message, message["photo"], "photo"))
    elif message.get("file"):
        kind = _MEDIA_TYPES.get(message.get("media_type"), "document")
        record["kind"] = kind
        if kind != "sticker":
            media.append(_copy_media(export_dir, store, message, message["file"], kind))
    elif record["extra"]:
        record["kind"] = next(
            (k for k in ("poll", "location", "contact") if message.get(f"{k}_information") or message.get(k)),
            "other",
        )
    if media:
        record["media"] = media
    return record


def import_export(export_dir: Path, archive_dir: Path) -> "tuple[int, int, str]":
    data = json.loads((export_dir / "result.json").read_text(encoding="utf-8"))
    store = ArchiveStore(archive_dir)
    store.ensure_dirs()
    if not store.read_meta().get("title") and data.get("name"):
        store.write_meta({"title": data["name"]})

    existing = {record["id"]: record for record in store.load()}
    imported = skipped = 0
    for message in data.get("messages") or []:
        current = existing.get(message.get("id"))
        if current is not None and current.get("source") == "bot":
            skipped += 1  # the bot's live copy is richer (quotes, rich blocks)
            continue
        store.append(export_record(export_dir, store, message))
        imported += 1
    return imported, skipped, store.read_meta().get("title") or "Telegram"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path, help="folder with result.json from Telegram Desktop")
    parser.add_argument("archive_dir", type=Path, help="archive folder of the chat (ARCHIVE_CHATS)")
    parser.add_argument("--tz", default=os.environ.get("ARCHIVE_TZ") or "Europe/Berlin")
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="transcribe voice/audio/video via ElevenLabs (ELEVENLABS_API_KEY)",
    )
    args = parser.parse_args(argv)

    imported, skipped, title = import_export(args.export_dir, args.archive_dir)
    print(f"Импортировано: {imported}, уже было от бота: {skipped}")

    store = ArchiveStore(args.archive_dir)
    if args.transcribe:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            print("ELEVENLABS_API_KEY не задан — расшифровка пропущена", file=sys.stderr)
        else:
            max_seconds = int(os.environ.get("TRANSCRIBE_MAX_SECONDS") or 1800)
            speaker_model = _speaker_model(os.environ.get("SPEAKER_MODEL"))
            count = transcribe_pending(
                store,
                api_key,
                max_seconds,
                model=os.environ.get("STT_MODEL") or MODEL_ID,
                **claude_passes(speaker_model, os.environ.get("CLAUDE_BIN") or None),
            )
            print(f"Расшифровано: {count}")

    render_archive(store.root, store.load(), ZoneInfo(args.tz), title)
    print(f"Готово: {store.root / 'index.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
