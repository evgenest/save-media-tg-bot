from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MEDIA_ATTRS = ("photo", "video", "audio", "document", "voice", "video_note", "animation")


def sanitize_filename(name: str) -> str:
    name = name.strip().strip(".")
    name = _UNSAFE_CHARS.sub("_", name).strip()
    return name or "file"


def resolve_filename_collision(directory: Path, filename: str) -> str:
    if not (directory / filename).exists():
        return filename

    path = Path(filename)
    stem, suffix = path.stem, path.suffix
    counter = 2
    while True:
        candidate = f"{stem} ({counter}){suffix}"
        if not (directory / candidate).exists():
            return candidate
        counter += 1


def build_default_filename(
    media_type: str, message_id: int, date_str: str, mime_type: Optional[str]
) -> str:
    extension = mimetypes.guess_extension(mime_type) if mime_type else None
    return f"{media_type}_{date_str}_{message_id}{extension or ''}"


@dataclass(frozen=True)
class MediaInfo:
    media_type: str
    file_name: Optional[str]
    file_size: int
    mime_type: Optional[str]


def extract_media_info(message) -> Optional[MediaInfo]:
    for attr in _MEDIA_ATTRS:
        media = getattr(message, attr, None)
        if media is not None:
            return MediaInfo(
                media_type=attr,
                file_name=getattr(media, "file_name", None),
                file_size=getattr(media, "file_size", 0) or 0,
                mime_type=getattr(media, "mime_type", None),
            )
    return None


class MediaDownloader(Protocol):
    async def download_media(self, message, file_name: str) -> str: ...


@dataclass(frozen=True)
class DownloadResult:
    success: bool
    message_id: int
    media_type: str
    original_name: Optional[str]
    stored_name: str
    size_bytes: int
    file_path: Path
    message_date: str
    caption: Optional[str]
    error: Optional[str] = None


async def download_media_message(
    client: MediaDownloader,
    message,
    dest_dir: Path,
    *,
    date_str: str,
    max_retries: int = 2,
) -> DownloadResult:
    media_info = extract_media_info(message)
    if media_info is None:
        raise ValueError("Message does not contain supported media")

    caption = getattr(message, "caption", None)
    base_name = media_info.file_name or build_default_filename(
        media_info.media_type, message.id, date_str, media_info.mime_type
    )
    final_name = resolve_filename_collision(dest_dir, sanitize_filename(base_name))
    dest_path = dest_dir / final_name

    last_error: Optional[str] = None
    for _ in range(max_retries + 1):
        try:
            await client.download_media(message, file_name=str(dest_path))
        except Exception as exc:  # any download failure is retried, never crashes the batch
            last_error = str(exc)
            continue

        size_bytes = dest_path.stat().st_size if dest_path.exists() else media_info.file_size
        return DownloadResult(
            success=True,
            message_id=message.id,
            media_type=media_info.media_type,
            original_name=media_info.file_name,
            stored_name=final_name,
            size_bytes=size_bytes,
            file_path=dest_path,
            message_date=date_str,
            caption=caption,
        )

    return DownloadResult(
        success=False,
        message_id=message.id,
        media_type=media_info.media_type,
        original_name=media_info.file_name,
        stored_name=final_name,
        size_bytes=0,
        file_path=dest_path,
        message_date=date_str,
        caption=caption,
        error=last_error,
    )
