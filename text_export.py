from __future__ import annotations

from pathlib import Path
from typing import Optional

from downloader import DownloadResult, resolve_filename_collision, sanitize_filename
from rich_markdown import Attachments, render_rich_message
from rich_media import ProgressFactory, RichMediaResult, download_rich_media

TEXT_MEDIA_TYPE = "text"


def build_default_filename(message_id: int, date_str: str) -> str:
    return f"text_{date_str}_{message_id}.md"


def is_text_message(message) -> bool:
    """Plain text or a rich message (structured blocks with empty `text`)."""
    return message.text is not None or getattr(message, "rich_message", None) is not None


def render_message_markdown(message, attachments: Attachments = {}) -> str:
    """Render a Telegram text message as markdown, preserving entity formatting.

    Pyrogram's `message.text` is a `Str` subclass carrying the message's
    entities; its `.markdown` property does the entity -> markdown conversion
    (bold/italic/links/etc). Plain strings (e.g. in tests) have no such
    property and are returned as-is. Rich messages carry no `text` at all and
    are rendered from their blocks instead; `attachments` maps their embedded
    media to files saved next to the .md.
    """
    text = message.text
    if text is None:
        rich_message = getattr(message, "rich_message", None)
        if rich_message is None:
            return ""
        return render_rich_message(rich_message, attachments)
    return getattr(text, "markdown", text)


async def save_text_message(
    message,
    dest_dir: Path,
    *,
    date_str: str,
    client=None,
    progress_for: Optional[ProgressFactory] = None,
) -> DownloadResult:
    base_name = build_default_filename(message.id, date_str)
    final_name = resolve_filename_collision(dest_dir, sanitize_filename(base_name))
    dest_path = dest_dir / final_name

    media = RichMediaResult()
    rich_message = getattr(message, "rich_message", None)
    if message.text is None and rich_message is not None and client is not None:
        media = await download_rich_media(
            client, rich_message, dest_dir, stem=dest_path.stem, progress_for=progress_for
        )
    content = render_message_markdown(message, media.attachments)

    try:
        dest_path.write_text(content, encoding="utf-8")
    except Exception as exc:  # mirrors download_media_message: never crash the batch
        return DownloadResult(
            success=False,
            message_id=message.id,
            media_type=TEXT_MEDIA_TYPE,
            original_name=None,
            stored_name=final_name,
            size_bytes=media.total_bytes,
            file_path=dest_path,
            message_date=date_str,
            caption=None,
            error=str(exc),
            attachments=tuple(media.stored_names),
        )

    # The .md is saved either way; a failed embedded download still counts as
    # an error so it shows up in the batch summary and manifest.
    return DownloadResult(
        success=not media.errors,
        message_id=message.id,
        media_type=TEXT_MEDIA_TYPE,
        original_name=None,
        stored_name=final_name,
        size_bytes=dest_path.stat().st_size + media.total_bytes,
        file_path=dest_path,
        message_date=date_str,
        caption=None,
        error="; ".join(media.errors) or None,
        attachments=tuple(media.stored_names),
    )
