from __future__ import annotations

from pathlib import Path
from typing import Optional

from downloader import DownloadResult, resolve_filename_collision, sanitize_filename

TEXT_MEDIA_TYPE = "text"


def build_default_filename(message_id: int, date_str: str) -> str:
    return f"text_{date_str}_{message_id}.md"


def render_message_markdown(message) -> str:
    """Render a Telegram text message as markdown, preserving entity formatting.

    Pyrogram's `message.text` is a `Str` subclass carrying the message's
    entities; its `.markdown` property does the entity -> markdown conversion
    (bold/italic/links/etc). Plain strings (e.g. in tests) have no such
    property and are returned as-is.
    """
    text = message.text
    if text is None:
        return ""
    return getattr(text, "markdown", text)


async def save_text_message(message, dest_dir: Path, *, date_str: str) -> DownloadResult:
    content = render_message_markdown(message)
    base_name = build_default_filename(message.id, date_str)
    final_name = resolve_filename_collision(dest_dir, sanitize_filename(base_name))
    dest_path = dest_dir / final_name

    try:
        dest_path.write_text(content, encoding="utf-8")
    except Exception as exc:  # mirrors download_media_message: never crash the batch
        return DownloadResult(
            success=False,
            message_id=message.id,
            media_type=TEXT_MEDIA_TYPE,
            original_name=None,
            stored_name=final_name,
            size_bytes=0,
            file_path=dest_path,
            message_date=date_str,
            caption=None,
            error=str(exc),
        )

    return DownloadResult(
        success=True,
        message_id=message.id,
        media_type=TEXT_MEDIA_TYPE,
        original_name=None,
        stored_name=final_name,
        size_bytes=dest_path.stat().st_size,
        file_path=dest_path,
        message_date=date_str,
        caption=None,
    )
