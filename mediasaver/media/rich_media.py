from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from mediasaver.core.downloader import (
    download_with_retries,
    resolve_filename_collision,
    sanitize_filename,
)
from mediasaver.core.media_session import reset_hook
from mediasaver.core.stall_guard import DEFAULT_STALL_TIMEOUT
from mediasaver.media.rich_markdown import iter_media_blocks, media_of

ProgressFactory = Callable[[str], Callable[[int, int], Awaitable[None]]]

_DEFAULT_EXTENSIONS = {"фото": ".jpg", "видео": ".mp4", "анимация": ".mp4", "голосовое": ".ogg"}


@dataclass
class RichMediaResult:
    # id(block) -> stored file name, or None if that download failed.
    attachments: Dict[int, Optional[str]] = field(default_factory=dict)
    stored_names: List[str] = field(default_factory=list)
    total_bytes: int = 0
    errors: List[str] = field(default_factory=list)


def attachment_extension(label: str, media) -> str:
    file_name = getattr(media, "file_name", None)
    if file_name and Path(file_name).suffix:
        return Path(file_name).suffix
    mime_type = getattr(media, "mime_type", None)
    extension = mimetypes.guess_extension(mime_type) if mime_type else None
    return extension or _DEFAULT_EXTENSIONS.get(label, "")


async def download_rich_media(
    client,
    rich_message,
    dest_dir: Path,
    *,
    stem: str,
    max_retries: int = 2,
    progress_for: Optional[ProgressFactory] = None,
) -> RichMediaResult:
    """Download photos/videos/audio embedded in a rich message next to its
    .md file as `<stem>_<n><ext>`, so the markdown can link them."""
    result = RichMediaResult()
    for index, block in enumerate(iter_media_blocks(rich_message.blocks), start=1):
        label, media = media_of(block)
        if media is None:  # Telegram didn't include the file itself
            result.attachments[id(block)] = None
            result.errors.append(f"{label} #{index}: файл недоступен")
            continue

        file_name = resolve_filename_collision(
            dest_dir, sanitize_filename(f"{stem}_{index}{attachment_extension(label, media)}")
        )
        dest_path = dest_dir / file_name
        progress = progress_for(file_name) if progress_for else None

        last_error = await download_with_retries(
            client,
            media,
            dest_path,
            max_retries=max_retries,
            progress=progress,
            stall_timeout=DEFAULT_STALL_TIMEOUT,
            on_retry=reset_hook(client, media),
        )

        if last_error is not None:
            result.attachments[id(block)] = None
            result.errors.append(f"{label} #{index}: {last_error}")
            continue

        result.attachments[id(block)] = file_name
        result.stored_names.append(file_name)
        result.total_bytes += dest_path.stat().st_size if dest_path.exists() else 0
    return result
