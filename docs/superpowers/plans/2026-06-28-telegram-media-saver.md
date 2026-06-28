# Telegram Media Saver Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot that lets a whitelisted owner forward photo/video/audio/document messages, batches them by time/button boundary, and saves them to disk with an incremental JSON manifest for later SFTP/rsync pickup.

**Architecture:** Single Python process using Kurigram (Pyrogram-compatible MTProto client, `pip install kurigram`, imported as `pyrogram`). Five flat modules at repo root — `config.py`, `storage.py`, `downloader.py`, `batch_manager.py`, `bot.py` — each independently unit-testable. `batch_manager.py` owns batch lifecycle/timing through injected async callbacks so it never touches Kurigram directly; `bot.py` is the thin wiring layer that implements those callbacks with real Telegram/filesystem calls. Ships as a single Docker container with one volume for `STORAGE_DIR`.

**Tech Stack:** Python 3.12, Kurigram (`kurigram` PyPI package, `import pyrogram`), TgCrypto, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Docker + docker-compose.

## Global Constraints

- File size up to 2 GB per file (native MTProto via Kurigram — no separate local Bot API server).
- Library: Kurigram (PyPI package name `kurigram`, Python import name `pyrogram`).
- Delivery is direct filesystem access (SFTP/SMB/rsync) — no web UI, no ZIP-to-chat, no cloud storage, no multi-user quotas (all explicitly out of scope/YAGNI).
- Files organized per batch: one folder per forwarded batch.
- Batch boundary is hybrid: closes after `BATCH_TIMEOUT` seconds (default 30) with no new file, OR when the "✅ Завершить пакет" inline button is pressed — whichever comes first.
- Access control: only Telegram user IDs in `ALLOWED_USER_IDS` may use the bot; everyone else is silently ignored.
- Runs as a single Python process in Docker on the user's VPS.
- `batch_manager.py` logic must be testable with mocks, fully isolated from Kurigram (interfaces/callbacks only).
- On restart, in-flight batch state in memory is lost by design — the next file simply starts a new batch. Files and `manifest.json` already written to disk are never lost.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `.gitignore`
- Create: `.env.example`

**Interfaces:**
- Produces: a pytest configuration (`asyncio_mode = "auto"`, `pythonpath = ["."]`) that every later task's tests rely on to import flat root-level modules (`config`, `storage`, `downloader`, `batch_manager`, `bot`) and to run `async def test_...` functions without per-test decorators.

- [ ] **Step 1: Create `requirements.txt`**

```
kurigram>=2.2.6
TgCrypto>=1.2.5
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 3: Create `pyproject.toml`**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

- [ ] **Step 4: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.env
storage/
*.session
*.session-journal
```

- [ ] **Step 5: Create `.env.example`**

```
API_ID=
API_HASH=
BOT_TOKEN=
ALLOWED_USER_IDS=
STORAGE_DIR=/storage
BATCH_TIMEOUT=30
```

- [ ] **Step 6: Install dependencies**

Run: `pip install -r requirements-dev.txt`
Expected: Kurigram, TgCrypto, pytest, and pytest-asyncio install without errors.

- [ ] **Step 7: Verify pytest picks up the config with no test files yet**

Run: `pytest --collect-only`
Expected: `no tests ran` (exit code 5), with **no** errors about unknown ini options or missing plugins. This confirms `asyncio_mode` and `pythonpath` are recognized (pytest-asyncio is installed) before any real test exists.

- [ ] **Step 8: Verify Kurigram installs under its `pyrogram` import name**

Run: `python -c "import pyrogram; print(pyrogram.__version__)"`
Expected: prints a version string, no `ModuleNotFoundError`.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml requirements.txt requirements-dev.txt .gitignore .env.example
git commit -m "chore: project scaffolding for media saver bot"
```

---

## Task 2: Configuration loading (`config.py`)

**Files:**
- Create: `config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces: `class ConfigError(ValueError)`; `@dataclass(frozen=True) class Config` with fields `api_id: int`, `api_hash: str`, `bot_token: str`, `allowed_user_ids: frozenset[int]`, `storage_dir: Path`, `batch_timeout: float`; function `load_config(env: Mapping[str, str]) -> Config`. Every later task that needs configuration imports `Config`/`load_config`/`ConfigError` from `config`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from config import Config, ConfigError, load_config

VALID_ENV = {
    "API_ID": "12345",
    "API_HASH": "abc123hash",
    "BOT_TOKEN": "111:AAAA",
    "ALLOWED_USER_IDS": "111, 222",
    "STORAGE_DIR": "/data/storage",
}


def test_load_config_success():
    config = load_config(VALID_ENV)

    assert config == Config(
        api_id=12345,
        api_hash="abc123hash",
        bot_token="111:AAAA",
        allowed_user_ids=frozenset({111, 222}),
        storage_dir=Path("/data/storage"),
        batch_timeout=30.0,
    )


@pytest.mark.parametrize(
    "missing_key", ["API_ID", "API_HASH", "BOT_TOKEN", "ALLOWED_USER_IDS", "STORAGE_DIR"]
)
def test_load_config_missing_required_var_raises(missing_key):
    env = dict(VALID_ENV)
    del env[missing_key]

    with pytest.raises(ConfigError, match=missing_key):
        load_config(env)


def test_load_config_invalid_api_id_raises():
    env = dict(VALID_ENV, API_ID="not-a-number")

    with pytest.raises(ConfigError, match="API_ID"):
        load_config(env)


def test_load_config_invalid_allowed_user_ids_raises():
    env = dict(VALID_ENV, ALLOWED_USER_IDS="abc")

    with pytest.raises(ConfigError, match="ALLOWED_USER_IDS"):
        load_config(env)


def test_load_config_default_batch_timeout():
    env = dict(VALID_ENV)
    env.pop("BATCH_TIMEOUT", None)

    config = load_config(env)

    assert config.batch_timeout == 30.0


def test_load_config_custom_batch_timeout():
    env = dict(VALID_ENV, BATCH_TIMEOUT="45")

    config = load_config(env)

    assert config.batch_timeout == 45.0


def test_load_config_non_positive_batch_timeout_raises():
    env = dict(VALID_ENV, BATCH_TIMEOUT="0")

    with pytest.raises(ConfigError, match="BATCH_TIMEOUT"):
        load_config(env)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 3: Implement `config.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_REQUIRED_VARS = ("API_ID", "API_HASH", "BOT_TOKEN", "ALLOWED_USER_IDS", "STORAGE_DIR")
_DEFAULT_BATCH_TIMEOUT = "30"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    allowed_user_ids: "frozenset[int]"
    storage_dir: Path
    batch_timeout: float


def load_config(env: Mapping[str, str]) -> Config:
    missing = [name for name in _REQUIRED_VARS if not env.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    try:
        api_id = int(env["API_ID"])
    except ValueError as exc:
        raise ConfigError("API_ID must be an integer") from exc

    try:
        allowed_user_ids = frozenset(
            int(part.strip())
            for part in env["ALLOWED_USER_IDS"].split(",")
            if part.strip()
        )
    except ValueError as exc:
        raise ConfigError(
            "ALLOWED_USER_IDS must be a comma-separated list of integers"
        ) from exc

    if not allowed_user_ids:
        raise ConfigError("ALLOWED_USER_IDS must contain at least one user ID")

    try:
        batch_timeout = float(env.get("BATCH_TIMEOUT", _DEFAULT_BATCH_TIMEOUT))
    except ValueError as exc:
        raise ConfigError("BATCH_TIMEOUT must be a number") from exc

    if batch_timeout <= 0:
        raise ConfigError("BATCH_TIMEOUT must be greater than zero")

    return Config(
        api_id=api_id,
        api_hash=env["API_HASH"],
        bot_token=env["BOT_TOKEN"],
        allowed_user_ids=allowed_user_ids,
        storage_dir=Path(env["STORAGE_DIR"]),
        batch_timeout=batch_timeout,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS — all 9 tests (1 success + 5 parametrized missing-var + 3 others... actual count: `test_load_config_success`, 5x `test_load_config_missing_required_var_raises`, `test_load_config_invalid_api_id_raises`, `test_load_config_invalid_allowed_user_ids_raises`, `test_load_config_default_batch_timeout`, `test_load_config_custom_batch_timeout`, `test_load_config_non_positive_batch_timeout_raises` = 11 passed).

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: add environment-based config loader"
```

---

## Task 3: Storage — batch directories and manifest (`storage.py`)

**Files:**
- Create: `storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing beyond stdlib (`pathlib`, `json`, `datetime`).
- Produces: `@dataclass(frozen=True) class ManifestEntry` with fields `message_id: int`, `original_name: Optional[str]`, `stored_name: str`, `media_type: str`, `size_bytes: int`, `message_date: str`, `caption: Optional[str]`, `error: Optional[str] = None`; function `create_batch_dir(storage_dir: Path, started_at: datetime) -> Path`; `class Manifest(batch_dir: Path, started_at: datetime)` with `.path: Path`, `.entries: list[ManifestEntry]` property, and `.add_entry(entry: ManifestEntry) -> None`. Task 6 (`bot.py`) imports all of these directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage.py`:

```python
import json
from datetime import datetime

from storage import Manifest, ManifestEntry, create_batch_dir


def test_create_batch_dir_creates_directory_with_timestamp_name(tmp_path):
    started_at = datetime(2026, 6, 28, 14, 30, 5)

    batch_dir = create_batch_dir(tmp_path, started_at)

    assert batch_dir == tmp_path / "2026-06-28_14-30-05"
    assert batch_dir.is_dir()


def test_create_batch_dir_resolves_collision_with_numeric_suffix(tmp_path):
    started_at = datetime(2026, 6, 28, 14, 30, 5)
    (tmp_path / "2026-06-28_14-30-05").mkdir()

    batch_dir = create_batch_dir(tmp_path, started_at)

    assert batch_dir == tmp_path / "2026-06-28_14-30-05_2"
    assert batch_dir.is_dir()


def test_manifest_add_entry_writes_json_file(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    manifest = Manifest(batch_dir, datetime(2026, 6, 28, 14, 30, 5))
    entry = ManifestEntry(
        message_id=1,
        original_name="video.mp4",
        stored_name="video.mp4",
        media_type="video",
        size_bytes=1024,
        message_date="20260628-143005",
        caption="hello",
    )

    manifest.add_entry(entry)

    data = json.loads(manifest.path.read_text(encoding="utf-8"))
    assert data["files"] == [
        {
            "message_id": 1,
            "original_name": "video.mp4",
            "stored_name": "video.mp4",
            "media_type": "video",
            "size_bytes": 1024,
            "message_date": "20260628-143005",
            "caption": "hello",
            "error": None,
        }
    ]


def test_manifest_add_entry_appends_multiple_entries(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    manifest = Manifest(batch_dir, datetime(2026, 6, 28, 14, 30, 5))

    manifest.add_entry(ManifestEntry(1, "a.jpg", "a.jpg", "photo", 10, "d1", None))
    manifest.add_entry(ManifestEntry(2, "b.jpg", "b.jpg", "photo", 20, "d2", None))

    data = json.loads(manifest.path.read_text(encoding="utf-8"))
    assert len(data["files"]) == 2
    assert manifest.entries[1].message_id == 2


def test_manifest_write_leaves_no_temp_file(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    manifest = Manifest(batch_dir, datetime(2026, 6, 28, 14, 30, 5))

    manifest.add_entry(ManifestEntry(1, "a.jpg", "a.jpg", "photo", 10, "d1", None))

    assert not manifest.path.with_suffix(".json.tmp").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'storage'`.

- [ ] **Step 3: Implement `storage.py`**

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ManifestEntry:
    message_id: int
    original_name: Optional[str]
    stored_name: str
    media_type: str
    size_bytes: int
    message_date: str
    caption: Optional[str]
    error: Optional[str] = None


def create_batch_dir(storage_dir: Path, started_at: datetime) -> Path:
    base_name = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    candidate = storage_dir / base_name
    suffix = 2
    while candidate.exists():
        candidate = storage_dir / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


class Manifest:
    def __init__(self, batch_dir: Path, started_at: datetime) -> None:
        self.path = batch_dir / "manifest.json"
        self._batch_dir = batch_dir
        self._started_at = started_at
        self._entries: "list[ManifestEntry]" = []

    @property
    def entries(self) -> "list[ManifestEntry]":
        return list(self._entries)

    def add_entry(self, entry: ManifestEntry) -> None:
        self._entries.append(entry)
        self._write()

    def _write(self) -> None:
        data = {
            "batch_dir": str(self._batch_dir),
            "started_at": self._started_at.isoformat(),
            "files": [asdict(entry) for entry in self._entries],
        }
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add storage.py tests/test_storage.py
git commit -m "feat: add batch directory creation and incremental manifest writer"
```

---

## Task 4: Media download — sanitization, collisions, retries (`downloader.py`)

**Files:**
- Create: `downloader.py`
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: nothing beyond stdlib for the pure functions; `download_media_message` consumes any object satisfying `async def download_media(self, message, file_name: str) -> str` (duck-typed against Kurigram's `Client.download_media`, never imports `pyrogram` directly — this keeps the module mockable without a real Telegram connection) and a `message`-like object exposing `.id`, `.caption`, and one of `.photo/.video/.audio/.document/.voice/.video_note/.animation`.
- Produces: `sanitize_filename(name: str) -> str`; `resolve_filename_collision(directory: Path, filename: str) -> str`; `build_default_filename(media_type: str, message_id: int, date_str: str, mime_type: Optional[str]) -> str`; `@dataclass(frozen=True) class MediaInfo` (`media_type`, `file_name`, `file_size`, `mime_type`); `extract_media_info(message) -> Optional[MediaInfo]`; `@dataclass(frozen=True) class DownloadResult` (`success: bool`, `message_id: int`, `media_type: str`, `original_name: Optional[str]`, `stored_name: str`, `size_bytes: int`, `file_path: Path`, `message_date: str`, `caption: Optional[str]`, `error: Optional[str] = None`); `async def download_media_message(client, message, dest_dir: Path, *, date_str: str, max_retries: int = 2) -> DownloadResult`. Task 5 (`batch_manager.py`) imports `DownloadResult` for typing; Task 6 (`bot.py`) imports `DownloadResult` and `download_media_message`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_downloader.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from downloader import (
    DownloadResult,
    MediaInfo,
    build_default_filename,
    download_media_message,
    extract_media_info,
    resolve_filename_collision,
    sanitize_filename,
)


@dataclass
class FakeMedia:
    file_name: Optional[str] = None
    file_size: int = 0
    mime_type: Optional[str] = None


@dataclass
class FakeMessage:
    id: int
    caption: Optional[str] = None
    photo: Optional[FakeMedia] = None
    video: Optional[FakeMedia] = None
    audio: Optional[FakeMedia] = None
    document: Optional[FakeMedia] = None
    voice: Optional[FakeMedia] = None
    video_note: Optional[FakeMedia] = None
    animation: Optional[FakeMedia] = None


class StubClient:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: "list[str]" = []

    async def download_media(self, message, file_name: str) -> str:
        self.calls.append(file_name)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("network error")
        Path(file_name).write_bytes(b"x" * 10)
        return file_name


def test_sanitize_filename_replaces_unsafe_chars():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_filename_empty_after_sanitization_returns_fallback():
    assert sanitize_filename("...") == "file"


def test_resolve_filename_collision_no_collision_returns_original(tmp_path):
    assert resolve_filename_collision(tmp_path, "video.mp4") == "video.mp4"


def test_resolve_filename_collision_appends_numbered_suffix(tmp_path):
    (tmp_path / "video.mp4").write_bytes(b"")

    assert resolve_filename_collision(tmp_path, "video.mp4") == "video (2).mp4"


def test_resolve_filename_collision_increments_until_free(tmp_path):
    (tmp_path / "video.mp4").write_bytes(b"")
    (tmp_path / "video (2).mp4").write_bytes(b"")

    assert resolve_filename_collision(tmp_path, "video.mp4") == "video (3).mp4"


def test_build_default_filename_with_known_mime_type():
    name = build_default_filename("photo", 42, "20260628-143005", "image/jpeg")
    assert name == "photo_20260628-143005_42.jpg"


def test_build_default_filename_without_mime_type():
    name = build_default_filename("document", 42, "20260628-143005", None)
    assert name == "document_20260628-143005_42"


def test_extract_media_info_returns_none_for_text_message():
    assert extract_media_info(FakeMessage(id=1)) is None


def test_extract_media_info_returns_photo_info():
    message = FakeMessage(id=1, photo=FakeMedia(file_size=2048))

    info = extract_media_info(message)

    assert info == MediaInfo(media_type="photo", file_name=None, file_size=2048, mime_type=None)


async def test_download_media_message_success_first_attempt(tmp_path):
    message = FakeMessage(
        id=7, caption="hi", document=FakeMedia(file_name="report.pdf", mime_type="application/pdf")
    )
    client = StubClient()

    result = await download_media_message(client, message, tmp_path, date_str="20260628-143005")

    assert result == DownloadResult(
        success=True,
        message_id=7,
        media_type="document",
        original_name="report.pdf",
        stored_name="report.pdf",
        size_bytes=10,
        file_path=tmp_path / "report.pdf",
        message_date="20260628-143005",
        caption="hi",
    )
    assert client.calls == [str(tmp_path / "report.pdf")]


async def test_download_media_message_retries_then_succeeds(tmp_path):
    message = FakeMessage(id=8, document=FakeMedia(file_name="report.pdf"))
    client = StubClient(fail_times=1)

    result = await download_media_message(client, message, tmp_path, date_str="d", max_retries=2)

    assert result.success is True
    assert len(client.calls) == 2


async def test_download_media_message_fails_after_max_retries(tmp_path):
    message = FakeMessage(id=9, document=FakeMedia(file_name="report.pdf"))
    client = StubClient(fail_times=5)

    result = await download_media_message(client, message, tmp_path, date_str="d", max_retries=2)

    assert result.success is False
    assert result.error == "network error"
    assert len(client.calls) == 3


async def test_download_media_message_raises_for_non_media_message(tmp_path):
    message = FakeMessage(id=10)
    client = StubClient()

    with pytest.raises(ValueError, match="supported media"):
        await download_media_message(client, message, tmp_path, date_str="d")


async def test_download_media_message_uses_default_filename_when_missing(tmp_path):
    message = FakeMessage(id=11, photo=FakeMedia(mime_type="image/jpeg"))
    client = StubClient()

    result = await download_media_message(client, message, tmp_path, date_str="20260628-143005")

    assert result.stored_name == "photo_20260628-143005_11.jpg"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_downloader.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'downloader'`.

- [ ] **Step 3: Implement `downloader.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_downloader.py -v`
Expected: PASS — 13 passed.

- [ ] **Step 5: Commit**

```bash
git add downloader.py tests/test_downloader.py
git commit -m "feat: add media download with sanitization, collision resolution, and retries"
```

---

## Task 5: Batch lifecycle and hybrid timeout/button boundary (`batch_manager.py`)

**Files:**
- Create: `batch_manager.py`
- Test: `tests/test_batch_manager.py`

**Interfaces:**
- Consumes: `DownloadResult` from `downloader` (Task 4) for typing the `record_file` outcome parameter.
- Produces: `@dataclass class Batch` (`user_id: int`, `started_at: datetime`, `file_count: int = 0`, `total_bytes: int = 0`, `error_count: int = 0`); `def asyncio_timer_factory(delay: float, callback) -> TimerHandle` (production default); `class BatchManager(batch_timeout, on_batch_created, on_file_added, on_batch_closed, timer_factory=asyncio_timer_factory, clock=datetime.now)` with `async def ensure_batch(user_id: int) -> Batch`, `async def record_file(user_id: int, outcome: DownloadResult) -> Batch`, `async def close_batch(user_id: int) -> Optional[Batch]`, `def get_active_batch(user_id: int) -> Optional[Batch]`. Callback signatures: `on_batch_created(batch: Batch) -> Awaitable[None]`, `on_file_added(batch: Batch, outcome: DownloadResult) -> Awaitable[None]`, `on_batch_closed(batch: Batch) -> Awaitable[None]`. Task 6 (`bot.py`) imports `Batch` and `BatchManager` and supplies the three callbacks.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_manager.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from batch_manager import BatchManager
from downloader import DownloadResult


class FakeTimerHandle:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeTimerFactory:
    def __init__(self):
        self.handles: "list[FakeTimerHandle]" = []

    def __call__(self, delay, callback):
        handle = FakeTimerHandle(delay, callback)
        self.handles.append(handle)
        return handle

    @property
    def latest(self) -> FakeTimerHandle:
        return self.handles[-1]


@dataclass
class Harness:
    manager: BatchManager
    on_batch_created: AsyncMock
    on_file_added: AsyncMock
    on_batch_closed: AsyncMock
    timer_factory: FakeTimerFactory


def make_harness() -> Harness:
    on_batch_created = AsyncMock()
    on_file_added = AsyncMock()
    on_batch_closed = AsyncMock()
    timer_factory = FakeTimerFactory()
    manager = BatchManager(
        batch_timeout=30,
        on_batch_created=on_batch_created,
        on_file_added=on_file_added,
        on_batch_closed=on_batch_closed,
        timer_factory=timer_factory,
        clock=lambda: datetime(2026, 6, 28, 12, 0, 0),
    )
    return Harness(manager, on_batch_created, on_file_added, on_batch_closed, timer_factory)


def make_outcome(success=True, size_bytes=100) -> DownloadResult:
    return DownloadResult(
        success=success,
        message_id=1,
        media_type="photo",
        original_name="a.jpg",
        stored_name="a.jpg",
        size_bytes=size_bytes,
        file_path=Path("a.jpg"),
        message_date="d",
        caption=None,
        error=None if success else "boom",
    )


async def test_ensure_batch_creates_new_batch_once():
    h = make_harness()

    batch1 = await h.manager.ensure_batch(user_id=1)
    batch2 = await h.manager.ensure_batch(user_id=1)

    assert batch1 is batch2
    h.on_batch_created.assert_awaited_once_with(batch1)


async def test_record_file_without_active_batch_raises():
    h = make_harness()

    with pytest.raises(RuntimeError, match="No active batch"):
        await h.manager.record_file(user_id=1, outcome=make_outcome())


async def test_record_file_increments_counters_and_notifies():
    h = make_harness()
    await h.manager.ensure_batch(user_id=1)

    batch = await h.manager.record_file(user_id=1, outcome=make_outcome(size_bytes=512))

    assert batch.file_count == 1
    assert batch.total_bytes == 512
    assert batch.error_count == 0
    h.on_file_added.assert_awaited_once()


async def test_record_file_counts_failed_outcome_as_error():
    h = make_harness()
    await h.manager.ensure_batch(user_id=1)

    batch = await h.manager.record_file(user_id=1, outcome=make_outcome(success=False))

    assert batch.error_count == 1


async def test_record_file_resets_timer_on_each_call():
    h = make_harness()
    await h.manager.ensure_batch(user_id=1)

    await h.manager.record_file(user_id=1, outcome=make_outcome())
    first_handle = h.timer_factory.latest
    await h.manager.record_file(user_id=1, outcome=make_outcome())
    second_handle = h.timer_factory.latest

    assert first_handle.cancelled is True
    assert second_handle.cancelled is False
    assert second_handle.delay == 30


async def test_timer_firing_closes_the_batch():
    h = make_harness()
    await h.manager.ensure_batch(user_id=1)
    await h.manager.record_file(user_id=1, outcome=make_outcome())

    await h.timer_factory.latest.callback()

    assert h.manager.get_active_batch(1) is None
    h.on_batch_closed.assert_awaited_once()


async def test_close_batch_via_button_cancels_pending_timer():
    h = make_harness()
    await h.manager.ensure_batch(user_id=1)
    await h.manager.record_file(user_id=1, outcome=make_outcome())

    closed = await h.manager.close_batch(1)

    assert closed is not None
    assert h.timer_factory.latest.cancelled is True
    h.on_batch_closed.assert_awaited_once_with(closed)


async def test_close_batch_with_no_active_batch_returns_none_and_does_not_notify():
    h = make_harness()

    result = await h.manager.close_batch(1)

    assert result is None
    h.on_batch_closed.assert_not_awaited()


async def test_two_users_have_independent_batches_and_timers():
    h = make_harness()

    await h.manager.ensure_batch(user_id=1)
    await h.manager.record_file(user_id=1, outcome=make_outcome())
    await h.manager.ensure_batch(user_id=2)
    await h.manager.record_file(user_id=2, outcome=make_outcome())

    await h.manager.close_batch(1)

    assert h.manager.get_active_batch(1) is None
    assert h.manager.get_active_batch(2) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_batch_manager.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'batch_manager'`.

- [ ] **Step 3: Implement `batch_manager.py`**

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Dict, Optional, Protocol

from downloader import DownloadResult


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], Awaitable[None]]], TimerHandle]


def asyncio_timer_factory(delay: float, callback: Callable[[], Awaitable[None]]) -> TimerHandle:
    loop = asyncio.get_event_loop()
    return loop.call_later(delay, lambda: asyncio.create_task(callback()))


@dataclass
class Batch:
    user_id: int
    started_at: datetime
    file_count: int = 0
    total_bytes: int = 0
    error_count: int = 0


OnBatchCreated = Callable[[Batch], Awaitable[None]]
OnFileAdded = Callable[[Batch, DownloadResult], Awaitable[None]]
OnBatchClosed = Callable[[Batch], Awaitable[None]]


class BatchManager:
    def __init__(
        self,
        batch_timeout: float,
        on_batch_created: OnBatchCreated,
        on_file_added: OnFileAdded,
        on_batch_closed: OnBatchClosed,
        timer_factory: TimerFactory = asyncio_timer_factory,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._batch_timeout = batch_timeout
        self._on_batch_created = on_batch_created
        self._on_file_added = on_file_added
        self._on_batch_closed = on_batch_closed
        self._timer_factory = timer_factory
        self._clock = clock
        self._active: Dict[int, Batch] = {}
        self._timers: Dict[int, TimerHandle] = {}

    def get_active_batch(self, user_id: int) -> Optional[Batch]:
        return self._active.get(user_id)

    async def ensure_batch(self, user_id: int) -> Batch:
        batch = self._active.get(user_id)
        if batch is None:
            batch = Batch(user_id=user_id, started_at=self._clock())
            self._active[user_id] = batch
            await self._on_batch_created(batch)
        return batch

    async def record_file(self, user_id: int, outcome: DownloadResult) -> Batch:
        batch = self._active.get(user_id)
        if batch is None:
            raise RuntimeError(f"No active batch for user {user_id}; call ensure_batch first")

        batch.file_count += 1
        batch.total_bytes += outcome.size_bytes
        if not outcome.success:
            batch.error_count += 1

        await self._on_file_added(batch, outcome)
        self._reset_timer(user_id)
        return batch

    async def close_batch(self, user_id: int) -> Optional[Batch]:
        batch = self._active.pop(user_id, None)
        if batch is None:
            return None
        self._cancel_timer(user_id)
        await self._on_batch_closed(batch)
        return batch

    def _reset_timer(self, user_id: int) -> None:
        self._cancel_timer(user_id)
        self._timers[user_id] = self._timer_factory(
            self._batch_timeout, lambda: self.close_batch(user_id)
        )

    def _cancel_timer(self, user_id: int) -> None:
        timer = self._timers.pop(user_id, None)
        if timer is not None:
            timer.cancel()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_batch_manager.py -v`
Expected: PASS — 9 passed.

- [ ] **Step 5: Commit**

```bash
git add batch_manager.py tests/test_batch_manager.py
git commit -m "feat: add batch manager with hybrid timeout/button boundary"
```

---

## Task 6: Telegram wiring (`bot.py`)

**Files:**
- Create: `bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `Config`/`load_config` (Task 2), `create_batch_dir`/`Manifest`/`ManifestEntry` (Task 3), `DownloadResult`/`download_media_message` (Task 4), `Batch`/`BatchManager` (Task 5), plus `pyrogram.Client`/`filters`/`InlineKeyboardButton`/`InlineKeyboardMarkup`/`CallbackQuery`/`Message`.
- Produces: `class BotState` (in-memory per-user dicts for batch dir / manifest / status message); `def create_app(config: Config, state: BotState) -> Client`; `def main() -> None` (entry point, runs the bot). Nothing downstream depends on `bot.py` — it is the top of the dependency graph.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot.py`:

```python
from pathlib import Path

from bot import BotState, create_app
from config import Config


def make_test_config(tmp_path: Path) -> Config:
    return Config(
        api_id=12345,
        api_hash="hash",
        bot_token="111:AAAA",
        allowed_user_ids=frozenset({111}),
        storage_dir=tmp_path,
        batch_timeout=30.0,
    )


def test_create_app_builds_without_network_access(tmp_path):
    config = make_test_config(tmp_path)

    app = create_app(config, BotState())

    assert app.name == "mediasaver_bot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py -v`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'bot'`.

- [ ] **Step 3: Implement `bot.py`**

```python
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from batch_manager import Batch, BatchManager
from config import Config, load_config
from downloader import DownloadResult, download_media_message
from storage import Manifest, ManifestEntry, create_batch_dir

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mediasaver")

FINISH_BATCH_DATA = "finish_batch"

MEDIA_FILTER = (
    filters.photo
    | filters.video
    | filters.audio
    | filters.document
    | filters.voice
    | filters.video_note
    | filters.animation
)


class BotState:
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}


def is_allowed(user_id: Optional[int], config: Config) -> bool:
    return user_id is not None and user_id in config.allowed_user_ids


def build_status_text(batch: Batch) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = f"Сохранено: {batch.file_count} файлов, {size_mb:.1f} МБ"
    if batch.error_count:
        text += f"\nОшибок: {batch.error_count}"
    return text


def build_summary_text(batch: Batch, batch_dir: Path) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = (
        f"Пакет завершён: {batch_dir}\n"
        f"Файлов: {batch.file_count}\n"
        f"Размер: {size_mb:.1f} МБ"
    )
    if batch.error_count:
        text += f"\nОшибок: {batch.error_count}"
    return text


def build_finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Завершить пакет", callback_data=FINISH_BATCH_DATA)]]
    )


def create_app(config: Config, state: BotState) -> Client:
    app = Client(
        "mediasaver_bot",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        in_memory=True,
    )

    async def on_batch_created(batch: Batch) -> None:
        batch_dir = create_batch_dir(config.storage_dir, batch.started_at)
        state.batch_dirs[batch.user_id] = batch_dir
        state.manifests[batch.user_id] = Manifest(batch_dir, batch.started_at)
        status_message = await app.send_message(
            batch.user_id, build_status_text(batch), reply_markup=build_finish_keyboard()
        )
        state.status_messages[batch.user_id] = status_message

    async def on_file_added(batch: Batch, outcome: DownloadResult) -> None:
        manifest = state.manifests[batch.user_id]
        manifest.add_entry(
            ManifestEntry(
                message_id=outcome.message_id,
                original_name=outcome.original_name,
                stored_name=outcome.stored_name,
                media_type=outcome.media_type,
                size_bytes=outcome.size_bytes,
                message_date=outcome.message_date,
                caption=outcome.caption,
                error=outcome.error,
            )
        )
        status_message = state.status_messages.get(batch.user_id)
        if status_message is not None:
            state.status_messages[batch.user_id] = await status_message.edit_text(
                build_status_text(batch), reply_markup=build_finish_keyboard()
            )

    async def on_batch_closed(batch: Batch) -> None:
        batch_dir = state.batch_dirs.pop(batch.user_id, None)
        state.manifests.pop(batch.user_id, None)
        status_message = state.status_messages.pop(batch.user_id, None)
        if status_message is not None and batch_dir is not None:
            await status_message.edit_text(
                build_summary_text(batch, batch_dir), reply_markup=None
            )

    batch_manager = BatchManager(
        batch_timeout=config.batch_timeout,
        on_batch_created=on_batch_created,
        on_file_added=on_file_added,
        on_batch_closed=on_batch_closed,
    )

    @app.on_message(MEDIA_FILTER & filters.private)
    async def handle_media(client: Client, message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        user_id = message.from_user.id
        await batch_manager.ensure_batch(user_id)
        batch_dir = state.batch_dirs[user_id]
        date_str = message.date.strftime("%Y%m%d-%H%M%S")
        outcome = await download_media_message(client, message, batch_dir, date_str=date_str)
        await batch_manager.record_file(user_id, outcome)

    @app.on_callback_query()
    async def handle_callback_query(client: Client, callback_query: CallbackQuery) -> None:
        if callback_query.data != FINISH_BATCH_DATA:
            return

        user_id = callback_query.from_user.id
        if not is_allowed(user_id, config):
            await callback_query.answer()
            return

        await batch_manager.close_batch(user_id)
        await callback_query.answer("Пакет завершён")

    return app


def main() -> None:
    config = load_config(os.environ)
    config.storage_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(config, BotState())
    logger.info("Starting mediasaver bot")
    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot.py -v`
Expected: PASS — 1 passed. `Client(..., in_memory=True)` performs no disk or network I/O at construction time, so this stays a fast, offline test.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS — all tests from Tasks 2-6 pass together (33 passed).

- [ ] **Step 6: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: wire Kurigram client, whitelist, batching, and finish-batch button"
```

---

## Task 7: Containerization and deployment docs

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Modify: `.env.example` (already created in Task 1 — verify it matches `config.py`'s required vars; no changes expected)
- Create: `README.md`

**Interfaces:**
- Consumes: `bot.py` as the container's entry point; `requirements.txt` for the image's dependency layer.
- Produces: a buildable Docker image and a `docker compose up -d` deployment path. Nothing downstream depends on this task.

- [ ] **Step 1: Create `Dockerfile`**

`TgCrypto` (from `requirements.txt`) ships only a source distribution on PyPI — no prebuilt wheel — so it must be compiled at build time. `python:3.12-slim` has no compiler by default, so `gcc`/`libc6-dev` are installed, used, and purged in the same layer to keep the final image small.

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY config.py storage.py downloader.py batch_manager.py bot.py ./

CMD ["python", "bot.py"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  mediasaver:
    build: .
    container_name: mediasaver-bot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./storage:/storage
```

- [ ] **Step 3: Verify `.env.example` already covers every required variable**

Read `.env.example` from Task 1 and confirm it lists `API_ID`, `API_HASH`, `BOT_TOKEN`, `ALLOWED_USER_IDS`, `STORAGE_DIR=/storage`, `BATCH_TIMEOUT=30` — matching `_REQUIRED_VARS` in `config.py`. No edit needed if so.

- [ ] **Step 4: Create `README.md`**

```markdown
# Telegram Media Saver Bot

Бот, которому пересылаешь сообщения с медиа (фото, видео, музыка, документы).
Он скачивает их пакетами и раскладывает по папкам на сервере — забирать файлы
по SFTP/rsync вместо медленного скачивания по одному из клиента Telegram.

## Настройка

1. Получите `API_ID` и `API_HASH` на https://my.telegram.org (раздел "API
   development tools").
2. Создайте бота через [@BotFather](https://t.me/BotFather) и получите
   `BOT_TOKEN`.
3. Узнайте свой Telegram user ID (например, через
   [@userinfobot](https://t.me/userinfobot)) и впишите его в
   `ALLOWED_USER_IDS` (через запятую, если владельцев несколько).
4. Скопируйте `.env.example` в `.env` и заполните все значения:

   ```bash
   cp .env.example .env
   ```

5. Запустите бота:

   ```bash
   docker compose up -d --build
   ```

6. Откройте чат с ботом в Telegram (нажмите Start) и перешлите ему файлы.
   Каждый пакет переслать одной кнопкой "✅ Завершить пакет" или дождитесь
   `BATCH_TIMEOUT` секунд без новых файлов — бот пришлёт итог с путём к папке.

7. Забирайте файлы с сервера из каталога `./storage/<дата>_<время>/` по
   SFTP/SMB/rsync. В каждой папке лежит `manifest.json` со списком файлов
   (исходное имя, итоговое имя, тип, размер, дата, подпись, ошибки).

## Переменные окружения

| Переменная       | Назначение                                              |
|-------------------|---------------------------------------------------------|
| `API_ID`          | MTProto API ID с my.telegram.org                        |
| `API_HASH`        | MTProto API hash с my.telegram.org                      |
| `BOT_TOKEN`       | Токен бота от @BotFather                                |
| `ALLOWED_USER_IDS`| Белый список Telegram ID через запятую                  |
| `STORAGE_DIR`     | Каталог для файлов внутри контейнера (`/storage`)       |
| `BATCH_TIMEOUT`   | Сек. без новых файлов до авто-закрытия пакета (по умолч. 30) |

## Разработка и тесты

```bash
pip install -r requirements-dev.txt
pytest -v
```

Модули `config.py`, `storage.py`, `downloader.py` и `batch_manager.py` покрыты
юнит-тестами без обращения к реальному Telegram API. `bot.py` проверен лёгким
smoke-тестом, который собирает приложение без сетевых вызовов.

Дополнительно (опционально) — ручная интеграционная проверка на тестовом
боте: поднять `docker compose up -d --build`, переслать боту несколько файлов
разных типов (фото, видео, документ), убедиться, что:
- статусное сообщение обновляется после каждого файла;
- кнопка "✅ Завершить пакет" закрывает пакет немедленно;
- при отсутствии новых файлов пакет закрывается сам через `BATCH_TIMEOUT`
  секунд;
- в `./storage/<пакет>/manifest.json` присутствуют все файлы с корректными
  полями.
```

- [ ] **Step 5: Build the Docker image**

Run: `docker build -t mediasaver .`
Expected: image builds successfully (`Successfully tagged mediasaver:latest` or equivalent BuildKit success output), confirming `requirements.txt` installs cleanly in the `python:3.12-slim` base and all five `.py` files copy in without error.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml README.md
git commit -m "docs: add Dockerfile, docker-compose, and deployment README"
```

---

## Self-Review Notes

- **Spec coverage:** `config.py` (Task 2) ↔ spec's `config.py` section; `storage.py` (Task 3) ↔ spec's `storage.py` + folder-per-batch + manifest section; `downloader.py` (Task 4) ↔ spec's `downloader.py` (naming, sanitization, collisions, retry-without-crashing); `batch_manager.py` (Task 5) ↔ spec's hybrid timeout/button boundary, per-user active batch, mockable-from-Kurigram requirement; `bot.py` (Task 6) ↔ spec's entry point, whitelist, handlers, status message editing, finalization summary; Task 7 ↔ spec's Dockerfile/docker-compose/.env.example/README deployment section. Album handling (`media_group_id`) and "text without media is ignored" both fall out naturally from `MEDIA_FILTER` matching only media messages — no separate task needed. Restart safety is satisfied by `Manifest._write`'s atomic tmp-then-replace and `BatchManager` simply having no batches in memory after a restart (next file starts fresh) — no extra code needed, called out explicitly in Global Constraints.
- **Placeholder scan:** no TBD/TODO, every step has complete runnable code and exact commands with expected output.
- **Type consistency:** `DownloadResult` fields (`message_id`, `media_type`, `original_name`, `stored_name`, `size_bytes`, `file_path`, `message_date`, `caption`, `error`) are identical between Task 4's definition, Task 5's `make_outcome` test helper, and Task 6's `on_file_added`/`ManifestEntry` construction. `Batch` fields (`user_id`, `started_at`, `file_count`, `total_bytes`, `error_count`) match between Task 5's definition and Task 6's `build_status_text`/`build_summary_text`. `BatchManager` callback names (`on_batch_created`, `on_file_added`, `on_batch_closed`) and methods (`ensure_batch`, `record_file`, `close_batch`, `get_active_batch`) are used with the same names and signatures in both Task 5's tests and Task 6's wiring.
