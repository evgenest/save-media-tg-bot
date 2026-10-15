from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

# Archive records -> markdown: one file per day (days/YYYY-MM-DD.md, times in
# the configured time zone) plus index.md. Every message gets an anchor
# (#m<id>) so replies link to the message they answer, across days.

DAYS_DIR = "days"

_KIND_LABELS = {
    "photo": "фото",
    "video": "видео",
    "voice": "голосовое",
    "audio": "аудио",
    "video_note": "видеокружок",
    "animation": "GIF",
    "document": "файл",
    "sticker": "стикер",
}
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def local_dt(value: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(value).astimezone(tz)


def day_of(record: dict, tz: ZoneInfo) -> str:
    return local_dt(record["date"], tz).strftime("%Y-%m-%d")


def _duration(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _link_target(path: str) -> str:
    return f"<{path}>" if any(c in path for c in " ()<>") else path


def _quote_block(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _message_link(message_id: int, days_by_id: Dict[int, str], current_day: str) -> str:
    day = days_by_id.get(message_id)
    if day is None:
        return f"#{message_id}"
    target = f"#m{message_id}" if day == current_day else f"{day}.md#m{message_id}"
    return f"[#{message_id}]({target})"


def render_media(item: dict) -> str:
    kind = item.get("kind", "document")
    label = _KIND_LABELS.get(kind, kind)
    path = item.get("path")
    details = []
    if item.get("file_name"):
        details.append(item["file_name"])
    if item.get("duration"):
        details.append(_duration(item["duration"]))
    title = f"{label}" + (f": {', '.join(details)}" if details else "")
    if not path:
        return f"*[{title} — не сохранено{': ' + item['error'] if item.get('error') else ''}]*"
    target = _link_target(f"../{path}")
    if kind == "photo" or (kind == "document" and path.lower().endswith(_IMAGE_SUFFIXES)):
        return f"![{title}]({target})"
    return f"[{title}]({target})"


def render_record(
    record: dict,
    tz: ZoneInfo,
    days_by_id: Dict[int, str],
    records_by_id: Dict[int, dict],
) -> str:
    current_day = day_of(record, tz)
    time = local_dt(record["date"], tz).strftime("%H:%M")
    sender = (record.get("from") or {}).get("name") or "?"

    if record.get("kind") == "service":
        return f'<a id="m{record["id"]}"></a>\n*{time} — {sender}: {record.get("extra") or "служебное"}*'

    header = f'### {time} — {sender} · #{record["id"]}'
    if record.get("edit_date"):
        header += f' · изменено {local_dt(record["edit_date"], tz).strftime("%d.%m %H:%M")}'
    parts: List[str] = [f'<a id="m{record["id"]}"></a>\n{header}']

    reply = record.get("reply_to")
    if reply:
        replied = records_by_id.get(reply.get("id"))
        who = reply.get("from") or ((replied or {}).get("from") or {}).get("name") or "?"
        what = reply.get("snippet") or _record_snippet(replied)
        link = _message_link(reply["id"], days_by_id, current_day) if reply.get("id") else "?"
        line = f"> ↳ ответ на {link} ({who})" + (f": «{what}»" if what else "")
        if reply.get("external"):
            line += " — сообщение из другого чата"
        if record.get("quote"):
            line += "\n>\n" + _quote_block(f"Цитата: «{record['quote']}»")
        parts.append(line)

    if record.get("forward_from"):
        parts.append(f"*Переслано от: {record['forward_from']}*")
    if record.get("text"):
        parts.append(record["text"])
    if record.get("extra"):
        parts.append(f"*[{record['extra']}]*")
    for item in record.get("media") or []:
        parts.append(render_media(item))
    if record.get("transcript"):
        parts.append(_quote_block(f"**Расшифровка:** {record['transcript']}"))
    elif record.get("transcript_error"):
        parts.append(f"*[расшифровка не удалась: {record['transcript_error']}]*")
    return "\n\n".join(parts)


def _record_snippet(record: Optional[dict]) -> str:
    if not record:
        return ""
    text = " ".join((record.get("text") or "").split())
    if not text:
        return f"[{_KIND_LABELS.get(record.get('kind'), record.get('kind') or '')}]"
    return text if len(text) <= 120 else text[:119] + "…"


def group_by_day(records: List[dict], tz: ZoneInfo) -> "OrderedDict[str, List[dict]]":
    days: "OrderedDict[str, List[dict]]" = OrderedDict()
    for record in sorted(records, key=lambda r: (r["date"], r["id"])):
        days.setdefault(day_of(record, tz), []).append(record)
    return days


def render_archive(root: Path, records: List[dict], tz: ZoneInfo, title: str) -> None:
    """(Re)write days/*.md and index.md under `root`."""
    records = [record for record in records if record.get("date")]
    days = group_by_day(records, tz)
    days_by_id = {record["id"]: day for day, items in days.items() for record in items}
    records_by_id = {record["id"]: record for record in records}

    days_dir = root / DAYS_DIR
    days_dir.mkdir(parents=True, exist_ok=True)
    for day, items in days.items():
        body = "\n\n---\n\n".join(
            render_record(record, tz, days_by_id, records_by_id) for record in items
        )
        _write(days_dir / f"{day}.md", f"# {title} — {day}\n\n{body}\n")

    lines = [
        f"# {title}",
        "",
        f"Архив чата: {len(records)} сообщений, время — {tz.key}.",
        "Сырые данные — `messages.jsonl`, файлы — `media/`.",
        "",
        "| День | Сообщений | Файлов | Голосовых/аудио |",
        "|---|---|---|---|",
    ]
    for day, items in reversed(days.items()):
        files = sum(len(record.get("media") or []) for record in items)
        speech = sum(1 for record in items if record.get("kind") in ("voice", "audio", "video_note"))
        lines.append(f"| [{day}]({DAYS_DIR}/{day}.md) | {len(items)} | {files} | {speech} |")
    _write(root / "index.md", "\n".join(lines) + "\n")


def _write(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
