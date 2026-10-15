from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from mediasaver.archive.config import ArchiveConfig, ArchiveKey, load_archive_config
from mediasaver.archive.group import Archives, ChatArchive
from mediasaver.archive.store import ArchiveStore
from mediasaver.core.config import ConfigError

# Hot reload of the archive settings: the bot re-reads its env file
# (CONFIG_FILE) when it changes - on every group message and every
# WATCH_INTERVAL seconds - and applies ARCHIVE_CHATS, ARCHIVE_TZ,
# ELEVENLABS_API_KEY and TRANSCRIBE_MAX_SECONDS without a restart.
# Bot credentials and ARCHIVE_SESSION_DIR still need a restart.

logger = logging.getLogger("archive.live")

WATCH_INTERVAL = 15.0


def parse_env_file(text: str) -> Dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments skipped, optional
    `export ` prefix and surrounding quotes stripped."""
    env: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            env[key] = value
    return env


@dataclass
class Changes:
    added: List[ArchiveKey] = field(default_factory=list)
    removed: List[ArchiveKey] = field(default_factory=list)
    transcription: bool = False  # key appeared/changed or limit raised
    timezone: bool = False


def diff_configs(old: Optional[ArchiveConfig], new: Optional[ArchiveConfig]) -> Changes:
    old_chats = old.chats if old else {}
    new_chats = new.chats if new else {}
    changes = Changes(
        added=[key for key, root in new_chats.items() if old_chats.get(key) != root],
        removed=[key for key, root in old_chats.items() if new_chats.get(key) != root],
    )
    if new is not None and new.elevenlabs_api_key:
        changes.transcription = (
            old is None
            or old.elevenlabs_api_key != new.elevenlabs_api_key
            or new.transcribe_max_seconds > old.transcribe_max_seconds
        )
    if old is not None and new is not None:
        changes.timezone = old.timezone != new.timezone
    return changes


class LiveArchives:
    """Owns the archives dict shared with the handlers and the backfill, and
    keeps it in line with the env file."""

    def __init__(
        self,
        config: Optional[ArchiveConfig],
        *,
        env_file: Optional[Path] = None,
        environ: Optional[Mapping[str, str]] = None,
        session_dir: Path = Path("sessions"),
    ) -> None:
        self.config = config
        self.env_file = env_file
        # Fixed for the process: the MTProto session lives there.
        self.session_dir = config.session_dir if config else session_dir
        self.archives: Archives = {}
        self._apply(config)
        self._mtime = self._stat()
        environ = dict(os.environ if environ is None else environ)
        # Keys that came from the env file at start: dropping one from the
        # file must unset it, not fall back to the process environment.
        file_keys = set(self._read_file()) if env_file else set()
        self._base_env = {k: v for k, v in environ.items() if k not in file_keys}
        self._tasks: "set[asyncio.Task]" = set()

    def _stat(self) -> Optional[float]:
        try:
            return self.env_file.stat().st_mtime if self.env_file else None
        except OSError:
            return None

    def _read_file(self) -> Dict[str, str]:
        try:
            return parse_env_file(self.env_file.read_text(encoding="utf-8"))
        except OSError:
            return {}

    def _apply(self, config: Optional[ArchiveConfig]) -> None:
        wanted = config.chats if config else {}
        for key in list(self.archives):
            if wanted.get(key) != self.archives[key].store.root:
                del self.archives[key]
        for key, root in wanted.items():
            if key not in self.archives:
                self.archives[key] = ChatArchive(ArchiveStore(root), config)
        for archive in self.archives.values():
            archive.config = config

    def reload(self) -> Optional[Changes]:
        """Re-read the env file if it changed. None = nothing to do."""
        mtime = self._stat()
        if mtime is None or mtime == self._mtime:
            return None
        self._mtime = mtime
        env = {**self._base_env, **self._read_file()}
        try:
            new = load_archive_config(env)
        except ConfigError as exc:
            logger.error("Config reload failed, keeping the previous settings: %s", exc)
            return None
        changes = diff_configs(self.config, new)
        self.config = new
        self._apply(new)
        logger.info(
            "Config reloaded: +%s -%s transcription=%s timezone=%s",
            changes.added,
            changes.removed,
            changes.transcription,
            changes.timezone,
        )
        return changes

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def refresh(self, client) -> None:
        changes = self.reload()
        if changes is None:
            return
        # Imported here: backfill imports group, which this module also uses.
        from mediasaver.archive.backfill import backfill_chat

        for chat_id in sorted({chat_id for chat_id, _ in changes.added}):
            self._spawn(backfill_chat(client, chat_id, self.archives))
        if changes.transcription:
            for archive in list(self.archives.values()):
                self._spawn(archive.transcribe_pending())
        if changes.timezone:
            for archive in list(self.archives.values()):
                async with archive.lock:
                    await archive.render()

    async def watch(self, client, interval: float = WATCH_INTERVAL) -> None:
        if self.env_file is None:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await self.refresh(client)
            except Exception:
                logger.exception("Config refresh failed")
