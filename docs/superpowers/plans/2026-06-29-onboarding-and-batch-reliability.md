# Onboarding Help Menu and Batch Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/start`/`/help` onboarding with an inline help menu, and fix the batch file-counter reliability bug by serializing per-user downloads, decoupling status-message UI updates from per-file events, and showing a live "currently downloading" progress line.

**Architecture:** All changes land in the existing three modules — `downloader.py` gains an optional `progress` passthrough parameter; `batch_manager.py` gets a one-line bugfix (timer reset reordered); `bot.py` gets new pure helper functions (`build_help_text`, `build_help_keyboard`, `make_progress_callback`, `refresh_status_message`, `build_bot_commands`), a `LiveProgress` dataclass, a per-user `asyncio.Lock` on `BotState`, and a periodic background "ticker" task per active batch that replaces the old per-file `edit_text` call. `main()` switches from `app.run()` to an explicit `start()` / `set_bot_commands()` / `idle()` / `stop()` sequence so commands can be registered at startup. No new files, no new dependencies.

**Tech Stack:** Python 3.12, Kurigram (`pyrogram` import, confirmed via Context7 docs at docs.kurigram.icu), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

## Global Constraints

- No network calls in unit tests — same principle as the existing test suite (`config`, `storage`, `downloader`, `batch_manager` tests use fakes/stubs; `bot.py` Pyrogram-glue code is only smoke-tested via `create_app` construction).
- Help text covers only in-Telegram usage (forwarding, status, finish button, auto-close timeout) — no server paths, no SFTP/storage internals.
- Files within one batch download strictly one at a time per user (`asyncio.Lock` keyed by `user_id`) — no parallel downloads.
- Status message is refreshed by a periodic ticker at most once every `STATUS_REFRESH_INTERVAL = 5.0` seconds — never edited directly from the per-file completion handler.
- `/start`, `/help`, and the "❓ Справка" callback are all gated by `is_allowed()` (same `ALLOWED_USER_IDS` whitelist as every other handler) — unauthorized users get silent no-ops, same as today.
- No "file N of M" total counter — there is no way to know the total batch size in advance; only show the current file's own progress.
- Deleting forwarded messages after download is explicitly out of scope for this plan.

---

## Task 1: Fix batch auto-close timer reset ordering (bugfix)

**Files:**
- Modify: `batch_manager.py:67-79` (the `record_file` method)
- Test: `tests/test_batch_manager.py`

**Interfaces:**
- Consumes: nothing new — internal reordering of existing `BatchManager.record_file` logic.
- Produces: no API change. `BatchManager.record_file(user_id: int, outcome: DownloadResult) -> Batch` keeps its exact signature; behavior change only (timer is now reset before the `on_file_added` callback runs, not after).

This is the root-cause fix for the bug where the final batch summary undercounts files: today, if `on_file_added` raises (e.g. a Telegram `FloodWait` while editing the status message), `self._reset_timer(user_id)` is never reached, so the auto-close timer can fire while more files are still downloading. Files that finish downloading after the batch is closed get written to disk but `record_file` raises `RuntimeError` for them, so they never reach `manifest.json` or the counters.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_batch_manager.py` (after `test_record_file_resets_timer_on_each_call`):

```python
async def test_record_file_resets_timer_even_if_on_file_added_raises():
    h = make_harness()
    await h.manager.ensure_batch(user_id=1)
    h.on_file_added.side_effect = RuntimeError("flood wait")

    with pytest.raises(RuntimeError, match="flood wait"):
        await h.manager.record_file(user_id=1, outcome=make_outcome())

    assert h.timer_factory.latest.cancelled is False
    assert h.timer_factory.latest.delay == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch_manager.py::test_record_file_resets_timer_even_if_on_file_added_raises -v`
Expected: FAIL with `IndexError: list index out of range` (raised from `h.timer_factory.latest`, because `_reset_timer` is never called when `on_file_added` raises before reaching it).

- [ ] **Step 3: Reorder `record_file` so the timer resets before the callback runs**

In `batch_manager.py`, replace:

```python
        batch.file_count += 1
        batch.total_bytes += outcome.size_bytes
        if not outcome.success:
            batch.error_count += 1

        await self._on_file_added(batch, outcome)
        self._reset_timer(user_id)
        return batch
```

with:

```python
        batch.file_count += 1
        batch.total_bytes += outcome.size_bytes
        if not outcome.success:
            batch.error_count += 1

        self._reset_timer(user_id)
        await self._on_file_added(batch, outcome)
        return batch
```

- [ ] **Step 4: Run the full batch_manager test suite to verify it passes with no regressions**

Run: `pytest tests/test_batch_manager.py -v`
Expected: all tests PASS (including the new one).

- [ ] **Step 5: Commit**

```bash
git add batch_manager.py tests/test_batch_manager.py
git commit -m "fix: reset batch auto-close timer before on_file_added callback

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Forward a download progress callback through `downloader.py`

**Files:**
- Modify: `downloader.py:1-9` (imports), `downloader.py:61-62` (`MediaDownloader` Protocol), `downloader.py:79-101` (`download_media_message`)
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `download_media_message(client, message, dest_dir, *, date_str, max_retries=2, progress=None)` — `progress`, if given, is `Callable[[int, int], Awaitable[None]]` and gets forwarded verbatim to `client.download_media(message, file_name=str(dest_path), progress=progress)`. Task 3/10 will build the actual callback; this task only wires the passthrough.

- [ ] **Step 1: Write the failing test (and extend the test double to record progress)**

In `tests/test_downloader.py`, replace the `StubClient` class:

```python
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
```

with:

```python
class StubClient:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: "list[str]" = []
        self.progress_calls: "list[object]" = []

    async def download_media(self, message, file_name: str, progress=None) -> str:
        self.calls.append(file_name)
        self.progress_calls.append(progress)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("network error")
        Path(file_name).write_bytes(b"x" * 10)
        return file_name
```

Then add a new test at the end of the file:

```python
async def test_download_media_message_forwards_progress_callback(tmp_path):
    message = FakeMessage(id=12, document=FakeMedia(file_name="report.pdf"))
    client = StubClient()

    async def progress(current, total):
        pass

    await download_media_message(client, message, tmp_path, date_str="d", progress=progress)

    assert client.progress_calls == [progress]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_downloader.py::test_download_media_message_forwards_progress_callback -v`
Expected: FAIL with `TypeError: download_media_message() got an unexpected keyword argument 'progress'`

- [ ] **Step 3: Add the `progress` parameter and forward it**

In `downloader.py`, change the imports line:

```python
from typing import Optional, Protocol
```

to:

```python
from typing import Awaitable, Callable, Optional, Protocol
```

Change the `MediaDownloader` Protocol:

```python
class MediaDownloader(Protocol):
    async def download_media(self, message, file_name: str) -> str: ...
```

to:

```python
class MediaDownloader(Protocol):
    async def download_media(
        self,
        message,
        file_name: str,
        progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> str: ...
```

Change the `download_media_message` signature:

```python
async def download_media_message(
    client: MediaDownloader,
    message,
    dest_dir: Path,
    *,
    date_str: str,
    max_retries: int = 2,
) -> DownloadResult:
```

to:

```python
async def download_media_message(
    client: MediaDownloader,
    message,
    dest_dir: Path,
    *,
    date_str: str,
    max_retries: int = 2,
    progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
) -> DownloadResult:
```

Change the download call:

```python
            await client.download_media(message, file_name=str(dest_path))
```

to:

```python
            await client.download_media(message, file_name=str(dest_path), progress=progress)
```

- [ ] **Step 4: Run the full downloader test suite to verify it passes with no regressions**

Run: `pytest tests/test_downloader.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add downloader.py tests/test_downloader.py
git commit -m "feat: forward optional progress callback through download_media_message

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add `LiveProgress` dataclass and `make_progress_callback` in `bot.py`

**Files:**
- Modify: `bot.py:1-22` (imports), `bot.py:37-41` (`BotState`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `LiveProgress` dataclass with fields `current_name: Optional[str] = None`, `current_bytes: int = 0`, `current_total: int = 0`. `make_progress_callback(live: LiveProgress, file_name: str) -> Callable[[int, int], Awaitable[None]]` — returns an async function matching the `progress` contract from Task 2; calling it mutates `live` in place. `BotState` gains `self.live_progress: Dict[int, LiveProgress] = {}`. Later tasks (5, 6, 10, 11) read/write `live_progress`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bot.py`:

```python
from bot import LiveProgress, make_progress_callback


async def test_make_progress_callback_updates_live_state():
    live = LiveProgress()
    callback = make_progress_callback(live, "video.mp4")

    await callback(50, 200)

    assert live.current_name == "video.mp4"
    assert live.current_bytes == 50
    assert live.current_total == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py::test_make_progress_callback_updates_live_state -v`
Expected: FAIL with `ImportError: cannot import name 'LiveProgress' from 'bot'`

- [ ] **Step 3: Add the dataclass, the factory function, and the new `BotState` field**

In `bot.py`, change the imports block:

```python
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional
```

to:

```python
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional
```

Add this new code right before `class BotState:`:

```python
@dataclass
class LiveProgress:
    current_name: Optional[str] = None
    current_bytes: int = 0
    current_total: int = 0


def make_progress_callback(
    live: LiveProgress, file_name: str
) -> Callable[[int, int], Awaitable[None]]:
    async def progress(current: int, total: int) -> None:
        live.current_name = file_name
        live.current_bytes = current
        live.current_total = total

    return progress
```

Change `BotState.__init__`:

```python
class BotState:
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}
```

to:

```python
class BotState:
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}
        self.live_progress: Dict[int, LiveProgress] = {}
```

- [ ] **Step 4: Run the full bot test suite to verify it passes with no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add LiveProgress state and progress callback factory

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add `build_help_text()` in `bot.py`

**Files:**
- Modify: `bot.py` (add function near `build_summary_text`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_help_text(batch_timeout: float) -> str`. Used by Task 9 (`/start`, `/help` handlers and the help callback).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bot.py`:

```python
from bot import build_help_text


def test_build_help_text_includes_batch_timeout():
    text = build_help_text(45.0)
    assert "45 секунд" in text


def test_build_help_text_mentions_finish_button_and_help_command():
    text = build_help_text(30.0)
    assert "Завершить пакет" in text
    assert "/help" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py::test_build_help_text_includes_batch_timeout -v`
Expected: FAIL with `ImportError: cannot import name 'build_help_text' from 'bot'`

- [ ] **Step 3: Add `build_help_text`**

In `bot.py`, add after `build_summary_text`:

```python
def build_help_text(batch_timeout: float) -> str:
    timeout_seconds = int(batch_timeout)
    return (
        "👋 Привет! Этот бот сохраняет медиафайлы, которые вы ему пересылаете.\n\n"
        "Как это работает:\n"
        "1. Пришлите (перешлите) одно или несколько сообщений с фото, видео, "
        "аудио или документами.\n"
        "2. Бот скачивает их по одному и показывает статус — сколько файлов "
        "сохранено и что скачивается прямо сейчас.\n"
        "3. Нажмите «✅ Завершить пакет» под статусом, либо просто не "
        f"присылайте файлы {timeout_seconds} секунд — пакет закроется сам.\n"
        "4. В конце вы получите итог: количество файлов и общий размер.\n\n"
        "Эта справка доступна в любой момент — командой /help или кнопкой ниже."
    )
```

- [ ] **Step 4: Run the full bot test suite to verify it passes with no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add build_help_text for /start and /help

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Add `build_help_keyboard()` and live-progress support in `build_status_text()`

**Files:**
- Modify: `bot.py:24` (constants), `bot.py:48-53` (`build_status_text`), `bot.py:68-71` (near `build_finish_keyboard`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `LiveProgress` from Task 3.
- Produces: `SHOW_HELP_DATA = "show_help"` constant; `build_help_keyboard() -> InlineKeyboardMarkup` (one button, callback_data `"show_help"`); `build_status_text(batch: Batch, live: Optional[LiveProgress] = None) -> str` — backward compatible (existing single-argument call sites keep working unchanged until Task 11 rewires them).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_bot.py`:

```python
from datetime import datetime

from batch_manager import Batch
from bot import build_help_keyboard, build_status_text


def make_batch(**overrides):
    defaults = dict(user_id=1, started_at=datetime(2026, 6, 28, 12, 0, 0))
    defaults.update(overrides)
    return Batch(**defaults)


def test_build_status_text_without_live_progress_is_unchanged():
    batch = make_batch(file_count=3, total_bytes=1024 * 1024)
    assert build_status_text(batch) == "Сохранено: 3 файлов, 1.0 МБ"


def test_build_status_text_with_live_progress_shows_current_file():
    batch = make_batch(file_count=3, total_bytes=1024 * 1024)
    live = LiveProgress(current_name="a.jpg", current_bytes=50, current_total=200)

    text = build_status_text(batch, live)

    assert "Сейчас: a.jpg — 25%" in text


def test_build_status_text_omits_current_file_line_when_none():
    batch = make_batch(file_count=1, total_bytes=0)
    live = LiveProgress()

    text = build_status_text(batch, live)

    assert "Сейчас:" not in text


def test_build_help_keyboard_has_show_help_button():
    keyboard = build_help_keyboard()
    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == "show_help"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py::test_build_help_keyboard_has_show_help_button -v`
Expected: FAIL with `ImportError: cannot import name 'build_help_keyboard' from 'bot'`

- [ ] **Step 3: Add the constant, the keyboard builder, and live-progress support**

In `bot.py`, change:

```python
FINISH_BATCH_DATA = "finish_batch"
```

to:

```python
FINISH_BATCH_DATA = "finish_batch"
SHOW_HELP_DATA = "show_help"
```

Change `build_status_text`:

```python
def build_status_text(batch: Batch) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = f"Сохранено: {batch.file_count} файлов, {size_mb:.1f} МБ"
    if batch.error_count:
        text += f"\nОшибок: {batch.error_count}"
    return text
```

to:

```python
def build_status_text(batch: Batch, live: Optional[LiveProgress] = None) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = f"Сохранено: {batch.file_count} файлов, {size_mb:.1f} МБ"
    if live is not None and live.current_name is not None:
        percent = (live.current_bytes * 100 / live.current_total) if live.current_total else 0
        text += f"\nСейчас: {live.current_name} — {percent:.0f}%"
    if batch.error_count:
        text += f"\nОшибок: {batch.error_count}"
    return text
```

Add after `build_finish_keyboard`:

```python
def build_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❓ Справка", callback_data=SHOW_HELP_DATA)]]
    )
```

- [ ] **Step 4: Run the full bot test suite to verify it passes with no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add help keyboard and live-progress line in status text

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Add `refresh_status_message()` helper

**Files:**
- Modify: `bot.py` (add function after `build_help_keyboard`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: `build_status_text`, `build_finish_keyboard`, `LiveProgress` from earlier tasks.
- Produces: `async def refresh_status_message(batch: Batch, live: LiveProgress, status_message) -> object` — calls `status_message.edit_text(...)`, swallows and logs any exception (e.g. `FloodWait`) instead of propagating, returns the (possibly unchanged) message object. Used by Task 11's periodic ticker.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_bot.py`:

```python
from bot import refresh_status_message


class FakeStatusMessage:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.edits: "list[tuple]" = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))
        if self.fail:
            raise RuntimeError("flood wait")
        return self


async def test_refresh_status_message_edits_with_latest_text():
    batch = make_batch(file_count=2, total_bytes=2048)
    live = LiveProgress(current_name="a.jpg", current_bytes=10, current_total=20)
    message = FakeStatusMessage()

    result = await refresh_status_message(batch, live, message)

    assert result is message
    assert len(message.edits) == 1
    assert "a.jpg — 50%" in message.edits[0][0]


async def test_refresh_status_message_swallows_edit_errors():
    batch = make_batch(file_count=1, total_bytes=0)
    live = LiveProgress()
    message = FakeStatusMessage(fail=True)

    result = await refresh_status_message(batch, live, message)

    assert result is message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py::test_refresh_status_message_edits_with_latest_text -v`
Expected: FAIL with `ImportError: cannot import name 'refresh_status_message' from 'bot'`

- [ ] **Step 3: Add `refresh_status_message`**

In `bot.py`, add after `build_help_keyboard`:

```python
async def refresh_status_message(batch: Batch, live: LiveProgress, status_message):
    try:
        return await status_message.edit_text(
            build_status_text(batch, live), reply_markup=build_finish_keyboard()
        )
    except Exception:
        logger.exception("Failed to refresh status message for user %s", batch.user_id)
        return status_message
```

- [ ] **Step 4: Run the full bot test suite to verify it passes with no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add refresh_status_message helper that swallows edit errors

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Add per-user `asyncio.Lock` via `BotState.get_lock()`

**Files:**
- Modify: `bot.py:1-22` (imports), `bot.py` (`BotState`)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `BotState.get_lock(user_id: int) -> asyncio.Lock` — memoized, same lock instance returned for repeated calls with the same `user_id`. Used by Task 10 to serialize per-user downloads.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_bot.py`:

```python
def test_get_lock_returns_same_lock_for_same_user():
    state = BotState()

    lock1 = state.get_lock(1)
    lock2 = state.get_lock(1)

    assert lock1 is lock2


def test_get_lock_returns_different_locks_for_different_users():
    state = BotState()

    assert state.get_lock(1) is not state.get_lock(2)
```

(`BotState` is already imported in `tests/test_bot.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py::test_get_lock_returns_same_lock_for_same_user -v`
Expected: FAIL with `AttributeError: 'BotState' object has no attribute 'get_lock'`

- [ ] **Step 3: Add `import asyncio`, the `locks` field, and `get_lock`**

In `bot.py`, change:

```python
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional
```

to:

```python
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional
```

Change `BotState`:

```python
class BotState:
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}
        self.live_progress: Dict[int, LiveProgress] = {}
```

to:

```python
class BotState:
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}
        self.live_progress: Dict[int, LiveProgress] = {}
        self.locks: Dict[int, asyncio.Lock] = {}

    def get_lock(self, user_id: int) -> asyncio.Lock:
        lock = self.locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[user_id] = lock
        return lock
```

- [ ] **Step 4: Run the full bot test suite to verify it passes with no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add per-user lock on BotState for serialized downloads

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Add `build_bot_commands()`

**Files:**
- Modify: `bot.py` (imports, add function)
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_bot_commands() -> list[BotCommand]` — returns `[BotCommand("start", ...), BotCommand("help", ...)]`. Used by Task 12's `main()` restructure.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bot.py`:

```python
from bot import build_bot_commands


def test_build_bot_commands_includes_start_and_help():
    commands = build_bot_commands()

    names = [c.command for c in commands]

    assert names == ["start", "help"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot.py::test_build_bot_commands_includes_start_and_help -v`
Expected: FAIL with `ImportError: cannot import name 'build_bot_commands' from 'bot'`

- [ ] **Step 3: Import `BotCommand` and add `build_bot_commands`**

In `bot.py`, change:

```python
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
```

to:

```python
from pyrogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
```

Add after `build_help_keyboard` (or anywhere among the other builder functions):

```python
def build_bot_commands() -> "list[BotCommand]":
    return [
        BotCommand("start", "Запустить бота и показать справку"),
        BotCommand("help", "Показать справку по использованию"),
    ]
```

- [ ] **Step 4: Run the full bot test suite to verify it passes with no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add build_bot_commands for Telegram command menu

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Wire `/start`, `/help`, and the help callback into `create_app`

**Files:**
- Modify: `bot.py:1-7` (imports), `bot.py` (`create_app`, callback handler)
- Test: `tests/test_bot.py` (regression only — existing smoke test must still pass)

**Interfaces:**
- Consumes: `build_help_text`, `build_help_keyboard`, `SHOW_HELP_DATA`, `is_allowed` (all already present).
- Produces: two new behaviors inside `create_app` — a message handler for `/start` and `/help`, and an extended callback-query handler that also handles `SHOW_HELP_DATA`. No new importable symbols; this is integration wiring, verified by the existing network-free smoke test plus manual verification (Task 13's README checklist).

- [ ] **Step 1: Add `filters` usage for commands and register the help handler**

In `bot.py`, add this new handler inside `create_app`, right before the `handle_media` handler definition:

```python
    @app.on_message(filters.command(["start", "help"]) & filters.private)
    async def handle_help_commands(client: Client, message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        await message.reply_text(
            build_help_text(config.batch_timeout), reply_markup=build_help_keyboard()
        )
```

- [ ] **Step 2: Extend the callback-query handler to also handle the help button**

Replace:

```python
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
```

with:

```python
    @app.on_callback_query()
    async def handle_callback_query(client: Client, callback_query: CallbackQuery) -> None:
        if callback_query.data not in (FINISH_BATCH_DATA, SHOW_HELP_DATA):
            return

        user_id = callback_query.from_user.id
        if not is_allowed(user_id, config):
            await callback_query.answer()
            return

        if callback_query.data == SHOW_HELP_DATA:
            await callback_query.answer()
            await callback_query.message.reply_text(
                build_help_text(config.batch_timeout), reply_markup=build_help_keyboard()
            )
            return

        await batch_manager.close_batch(user_id)
        await callback_query.answer("Пакет завершён")
```

- [ ] **Step 3: Run the full bot test suite to verify no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS, including `test_create_app_builds_without_network_access` (confirms the two new handlers register without making network calls).

- [ ] **Step 4: Run the entire project test suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: wire /start, /help, and help button into create_app

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Serialize per-user downloads and wire the progress callback into `handle_media`

**Files:**
- Modify: `bot.py:16-19` (imports from `downloader`), `bot.py` (`handle_media`)
- Test: `tests/test_bot.py` (regression only)

**Interfaces:**
- Consumes: `BotState.get_lock` (Task 7), `LiveProgress`/`make_progress_callback` (Task 3), `extract_media_info` (already in `downloader.py`), `download_media_message(..., progress=...)` (Task 2).
- Produces: `handle_media` now processes at most one file at a time per user, and updates `state.live_progress[user_id]` while a download is in flight, clearing `current_name` once it completes. No new importable symbols.

- [ ] **Step 1: Import `extract_media_info`**

In `bot.py`, change:

```python
from downloader import DownloadResult, download_media_message
```

to:

```python
from downloader import DownloadResult, download_media_message, extract_media_info
```

- [ ] **Step 2: Wrap `handle_media`'s body in the per-user lock and wire the progress callback**

Replace:

```python
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
```

with:

```python
    @app.on_message(MEDIA_FILTER & filters.private)
    async def handle_media(client: Client, message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        user_id = message.from_user.id
        async with state.get_lock(user_id):
            await batch_manager.ensure_batch(user_id)
            batch_dir = state.batch_dirs[user_id]
            date_str = message.date.strftime("%Y%m%d-%H%M%S")

            media_info = extract_media_info(message)
            display_name = (media_info.file_name if media_info else None) or "файл"
            live = state.live_progress.setdefault(user_id, LiveProgress())
            progress = make_progress_callback(live, display_name)

            outcome = await download_media_message(
                client, message, batch_dir, date_str=date_str, progress=progress
            )
            live.current_name = None
            await batch_manager.record_file(user_id, outcome)
```

- [ ] **Step 3: Run the full bot test suite to verify no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Run the entire project test suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: serialize per-user downloads and track live progress

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Wire the periodic status ticker lifecycle

**Files:**
- Modify: `bot.py` (`BotState`, new `run_status_ticker` function, `on_batch_created`, `on_file_added`, `on_batch_closed`)
- Test: `tests/test_bot.py` (regression only)

**Interfaces:**
- Consumes: `refresh_status_message` (Task 6), `LiveProgress` (Task 3).
- Produces: `STATUS_REFRESH_INTERVAL: float` module constant; `run_status_ticker(user_id: int, batch_manager: BatchManager, state: BotState) -> None` (long-running coroutine, cancelled via `asyncio.Task.cancel()`); `BotState.ticker_tasks: Dict[int, asyncio.Task]`. After this task, the status message is refreshed by the ticker every `STATUS_REFRESH_INTERVAL` seconds instead of once per file.

- [ ] **Step 1: Add the `ticker_tasks` field to `BotState`**

In `bot.py`, change:

```python
        self.live_progress: Dict[int, LiveProgress] = {}
        self.locks: Dict[int, asyncio.Lock] = {}
```

to:

```python
        self.live_progress: Dict[int, LiveProgress] = {}
        self.locks: Dict[int, asyncio.Lock] = {}
        self.ticker_tasks: Dict[int, asyncio.Task] = {}
```

- [ ] **Step 2: Add the `STATUS_REFRESH_INTERVAL` constant and `run_status_ticker`**

In `bot.py`, add after the `MEDIA_FILTER` definition:

```python
STATUS_REFRESH_INTERVAL = 5.0


async def run_status_ticker(user_id: int, batch_manager: BatchManager, state: BotState) -> None:
    try:
        while True:
            await asyncio.sleep(STATUS_REFRESH_INTERVAL)
            batch = batch_manager.get_active_batch(user_id)
            if batch is None:
                return
            status_message = state.status_messages.get(user_id)
            if status_message is None:
                continue
            live = state.live_progress.get(user_id, LiveProgress())
            state.status_messages[user_id] = await refresh_status_message(
                batch, live, status_message
            )
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 3: Start the ticker in `on_batch_created`, stop dropping per-file edits in `on_file_added`, and cancel the ticker in `on_batch_closed`**

Replace:

```python
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
```

with:

```python
    async def on_batch_created(batch: Batch) -> None:
        batch_dir = create_batch_dir(config.storage_dir, batch.started_at)
        state.batch_dirs[batch.user_id] = batch_dir
        state.manifests[batch.user_id] = Manifest(batch_dir, batch.started_at)
        state.live_progress[batch.user_id] = LiveProgress()
        status_message = await app.send_message(
            batch.user_id, build_status_text(batch), reply_markup=build_finish_keyboard()
        )
        state.status_messages[batch.user_id] = status_message
        state.ticker_tasks[batch.user_id] = asyncio.create_task(
            run_status_ticker(batch.user_id, batch_manager, state)
        )

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

    async def on_batch_closed(batch: Batch) -> None:
        batch_dir = state.batch_dirs.pop(batch.user_id, None)
        state.manifests.pop(batch.user_id, None)
        state.live_progress.pop(batch.user_id, None)
        ticker_task = state.ticker_tasks.pop(batch.user_id, None)
        if ticker_task is not None:
            ticker_task.cancel()
        status_message = state.status_messages.pop(batch.user_id, None)
        if status_message is not None and batch_dir is not None:
            await status_message.edit_text(
                build_summary_text(batch, batch_dir), reply_markup=None
            )
```

- [ ] **Step 4: Run the full bot test suite to verify no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the entire project test suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: refresh batch status via a periodic ticker instead of per file

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Restructure `main()` to register bot commands at startup

**Files:**
- Modify: `bot.py:8` (imports), `bot.py:156-165` (`main`, `if __name__ == "__main__"`)
- Test: none new (Pyrogram network glue, same as the pre-existing `main()`) — regression via full suite.

**Interfaces:**
- Consumes: `build_bot_commands` (Task 8), `create_app` (unchanged signature).
- Produces: `run_bot(config: Config, state: BotState) -> None` (async); `main()` keeps its exact no-argument signature and stays the script entry point.

- [ ] **Step 1: Import `idle` from pyrogram and `asyncio` (already imported in Task 7) at module level**

In `bot.py`, change:

```python
from pyrogram import Client, filters
```

to:

```python
from pyrogram import Client, filters, idle
```

- [ ] **Step 2: Replace `main()` with an async startup sequence that registers bot commands**

Replace:

```python
def main() -> None:
    config = load_config(os.environ)
    config.storage_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(config, BotState())
    logger.info("Starting mediasaver bot")
    app.run()


if __name__ == "__main__":
    main()
```

with:

```python
async def run_bot(config: Config, state: BotState) -> None:
    app = create_app(config, state)
    await app.start()
    await app.set_bot_commands(build_bot_commands())
    logger.info("Starting mediasaver bot")
    await idle()
    await app.stop()


def main() -> None:
    config = load_config(os.environ)
    config.storage_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run_bot(config, BotState()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full bot test suite to verify no regressions**

Run: `pytest tests/test_bot.py -v`
Expected: all tests PASS (`create_app` itself is untouched, so `test_create_app_builds_without_network_access` still passes; `main()`/`run_bot()` are not exercised by tests, same as before this plan).

- [ ] **Step 4: Run the entire project test suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: register /start and /help in Telegram's command menu at startup

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Update README with onboarding and reliability behavior

**Files:**
- Modify: `README.md` (usage section step 6, manual verification checklist)

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: nothing importable — operator-facing documentation update.

- [ ] **Step 1: Document `/start`/`/help` in the usage steps**

In `README.md`, replace:

```markdown
6. Откройте чат с ботом в Telegram (нажмите Start) и перешлите ему файлы.
   Каждый пакет переслать одной кнопкой "✅ Завершить пакет" или дождитесь
   `BATCH_TIMEOUT` секунд без новых файлов — бот пришлёт итог с путём к папке.
```

with:

```markdown
6. Откройте чат с ботом в Telegram и нажмите Start — бот пришлёт приветствие
   с краткой инструкцией и кнопкой "❓ Справка". Эту же справку можно вызвать
   в любой момент командой `/help` или кнопкой ниже сообщения.
7. Перешлите боту файлы. Пока пакет активен, статусное сообщение раз в ~5
   секунд показывает, сколько файлов уже сохранено и какой файл скачивается
   прямо сейчас (с процентом). Файлы одного пакета скачиваются строго по
   одному, не параллельно.
8. Завершите пакет кнопкой "✅ Завершить пакет" или дождитесь `BATCH_TIMEOUT`
   секунд без новых файлов — бот пришлёт итог с путём к папке.
```

- [ ] **Step 2: Renumber the remaining step and extend the manual verification checklist**

In `README.md`, replace:

```markdown
7. Забирайте файлы с сервера из каталога `./storage/<дата>_<время>/` по
   SFTP/SMB/rsync. В каждой папке лежит `manifest.json` со списком файлов
   (исходное имя, итоговое имя, тип, размер, дата, подпись, ошибки).
```

with:

```markdown
9. Забирайте файлы с сервера из каталога `./storage/<дата>_<время>/` по
   SFTP/SMB/rsync. В каждой папке лежит `manifest.json` со списком файлов
   (исходное имя, итоговое имя, тип, размер, дата, подпись, ошибки).
```

Then replace the end of the manual verification checklist:

```markdown
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

with:

```markdown
Дополнительно (опционально) — ручная интеграционная проверка на тестовом
боте: поднять `docker compose up -d --build`, проверить:
- `/start` присылает приветствие с кнопкой "❓ Справка"; `/help` и сама кнопка
  показывают тот же текст в любой момент;
- при пересылке нескольких файлов подряд статусное сообщение обновляется
  раз в ~5 секунд и показывает текущий скачиваемый файл с процентом;
- файлы одного пакета скачиваются по одному, не параллельно;
- кнопка "✅ Завершить пакет" закрывает пакет немедленно;
- при отсутствии новых файлов пакет закрывается сам через `BATCH_TIMEOUT`
  секунд;
- при пересылке большого числа файлов подряд (20+) итоговый счётчик и
  `./storage/<пакет>/manifest.json` точно совпадают с реальным числом файлов
  на диске — это регрессионная проверка бывшего бага с заниженным счётом.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document onboarding help menu and live batch status

Co-Authored-By: Claude Sonnet 4.6 (1M context) <noreply@anthropic.com>"
```
