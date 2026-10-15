from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mediasaver.core.config import ConfigError

# Group archive mode (group_archive.py): the same bot, added to a group chat,
# silently records every message into a markdown archive. Optional - enabled
# when ARCHIVE_CHATS is set; the bot credentials come from config.py.

_DEFAULT_TZ = "Europe/Berlin"
_DEFAULT_TRANSCRIBE_MAX_SECONDS = "1800"


@dataclass(frozen=True)
class ArchiveConfig:
    # chat_id -> archive directory of that chat.
    chats: "dict[int, Path]"
    timezone: ZoneInfo
    # Directory for the MTProto session file. A persistent session lets the
    # bot catch up on messages sent while it was offline.
    session_dir: Path
    elevenlabs_api_key: Optional[str]
    transcribe_max_seconds: int


def parse_chats(raw: str) -> "dict[int, Path]":
    """`<chat_id>=<dir>` pairs separated by `;`."""
    chats: "dict[int, Path]" = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        chat_id, sep, directory = part.partition("=")
        if not sep or not directory.strip():
            raise ConfigError(f"ARCHIVE_CHATS entry must be <chat_id>=<dir>: {part!r}")
        try:
            chats[int(chat_id.strip())] = Path(directory.strip()).expanduser()
        except ValueError as exc:
            raise ConfigError(f"ARCHIVE_CHATS chat id must be an integer: {chat_id!r}") from exc
    if not chats:
        raise ConfigError("ARCHIVE_CHATS must contain at least one chat")
    return chats


def load_archive_config(env: Mapping[str, str]) -> Optional[ArchiveConfig]:
    """None when the archive mode is off (no ARCHIVE_CHATS)."""
    if not (env.get("ARCHIVE_CHATS") or "").strip():
        return None

    try:
        timezone = ZoneInfo(env.get("ARCHIVE_TZ") or _DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown ARCHIVE_TZ: {env.get('ARCHIVE_TZ')!r}") from exc

    try:
        max_seconds = int(env.get("TRANSCRIBE_MAX_SECONDS") or _DEFAULT_TRANSCRIBE_MAX_SECONDS)
    except ValueError as exc:
        raise ConfigError("TRANSCRIBE_MAX_SECONDS must be an integer") from exc

    return ArchiveConfig(
        chats=parse_chats(env["ARCHIVE_CHATS"]),
        timezone=timezone,
        session_dir=Path(env.get("ARCHIVE_SESSION_DIR") or "sessions").expanduser(),
        elevenlabs_api_key=env.get("ELEVENLABS_API_KEY") or None,
        transcribe_max_seconds=max_seconds,
    )
