from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mediasaver.archive.speakers import DEFAULT_SPEAKER_MODEL
from mediasaver.archive.transcribe import MODEL_ID
from mediasaver.core.config import ConfigError

# Group archive mode (group_archive.py): the same bot, added to a group chat,
# silently records every message into a markdown archive. Optional - enabled
# when ARCHIVE_CHATS is set; the bot credentials come from config.py.

_DEFAULT_TZ = "Europe/Berlin"

ArchiveKey = Tuple[int, Optional[int]]
_DEFAULT_TRANSCRIBE_MAX_SECONDS = "1800"


@dataclass(frozen=True)
class ArchiveConfig:
    # (chat_id, topic_id) -> archive directory; topic_id None = whole chat,
    # 1 = the General topic of a forum.
    chats: "dict[ArchiveKey, Path]"
    timezone: ZoneInfo
    # Directory for the MTProto session file. A persistent session lets the
    # bot catch up on messages sent while it was offline.
    session_dir: Path
    elevenlabs_api_key: Optional[str]
    # ElevenLabs Scribe model for transcripts (STT_MODEL).
    stt_model: str
    transcribe_max_seconds: int
    # Claude model that names voices in multi-speaker audio (SPEAKER_MODEL);
    # None ("off") = only «Спикер N».
    speaker_model: Optional[str] = DEFAULT_SPEAKER_MODEL
    # Claude Code CLI binary (CLAUDE_BIN); None = PATH, then ~/.local/bin/claude.
    claude_bin: Optional[str] = None


def parse_chats(raw: str) -> "dict[ArchiveKey, Path]":
    """`<chat_id>[/<topic_id>]=<dir>` pairs separated by `;`."""
    chats: "dict[ArchiveKey, Path]" = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        key, sep, directory = part.partition("=")
        if not sep or not directory.strip():
            raise ConfigError(f"ARCHIVE_CHATS entry must be <chat_id>[/<topic_id>]=<dir>: {part!r}")
        chat_id, _, topic_id = key.strip().partition("/")
        try:
            parsed: ArchiveKey = (int(chat_id), int(topic_id) if topic_id else None)
        except ValueError as exc:
            raise ConfigError(f"ARCHIVE_CHATS ids must be integers: {key!r}") from exc
        chats[parsed] = Path(directory.strip()).expanduser()
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
        stt_model=(env.get("STT_MODEL") or "").strip() or MODEL_ID,
        transcribe_max_seconds=max_seconds,
        speaker_model=_speaker_model(env.get("SPEAKER_MODEL")),
        claude_bin=(env.get("CLAUDE_BIN") or "").strip() or None,
    )


def _speaker_model(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if value.lower() == "off":
        return None
    return value or DEFAULT_SPEAKER_MODEL
