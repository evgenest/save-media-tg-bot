import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from batch_manager import BatchManager, BatchPhase
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
    on_batch_sealed: AsyncMock
    on_download_file: AsyncMock
    on_file_downloaded: AsyncMock
    on_batch_closed: AsyncMock
    timer_factory: FakeTimerFactory


def make_harness() -> Harness:
    on_batch_created = AsyncMock()
    on_batch_sealed = AsyncMock()
    on_download_file = AsyncMock(return_value=make_outcome())
    on_file_downloaded = AsyncMock()
    on_batch_closed = AsyncMock()
    timer_factory = FakeTimerFactory()
    manager = BatchManager(
        batch_timeout=30,
        on_batch_created=on_batch_created,
        on_batch_sealed=on_batch_sealed,
        on_download_file=on_download_file,
        on_file_downloaded=on_file_downloaded,
        on_batch_closed=on_batch_closed,
        timer_factory=timer_factory,
        clock=lambda: datetime(2026, 6, 28, 12, 0, 0),
    )
    return Harness(
        manager,
        on_batch_created,
        on_batch_sealed,
        on_download_file,
        on_file_downloaded,
        on_batch_closed,
        timer_factory,
    )


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


async def drain() -> None:
    """Let scheduled tasks (worker, timer callbacks) run up to their next await."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_enqueue_creates_batch_once_and_reuses_it():
    h = make_harness()

    batch1 = await h.manager.enqueue(1, "msg-1")
    batch2 = await h.manager.enqueue(1, "msg-2")

    assert batch1 is batch2
    assert batch1.queued_count == 2
    assert batch1.phase == BatchPhase.PENDING
    h.on_batch_created.assert_awaited_once_with(batch1)


async def test_enqueue_resets_timer_on_each_call():
    h = make_harness()

    await h.manager.enqueue(1, "msg-1")
    first_handle = h.timer_factory.latest
    await h.manager.enqueue(1, "msg-2")
    second_handle = h.timer_factory.latest

    assert first_handle.cancelled is True
    assert second_handle.cancelled is False
    assert second_handle.delay == 30


async def test_seal_batch_with_no_active_batch_returns_none_and_does_not_notify():
    h = make_harness()

    result = await h.manager.seal_batch(1)

    assert result is None
    h.on_batch_sealed.assert_not_awaited()


async def test_seal_batch_cancels_pending_timer_and_downloads_queued_files():
    h = make_harness()
    await h.manager.enqueue(1, "msg-1")
    await h.manager.enqueue(1, "msg-2")

    sealed = await h.manager.seal_batch(1)
    await drain()

    assert sealed.phase == BatchPhase.CLOSED
    assert h.timer_factory.latest.cancelled is True
    assert h.manager.get_active_batch(1) is None
    h.on_batch_sealed.assert_awaited_once()
    assert h.on_download_file.await_count == 2
    assert h.on_file_downloaded.await_count == 2
    h.on_batch_closed.assert_awaited_once_with(sealed)
    assert sealed.file_count == 2
    assert sealed.total_bytes == 200


async def test_timer_firing_seals_and_downloads_the_batch():
    h = make_harness()
    await h.manager.enqueue(1, "msg-1")

    await h.timer_factory.latest.callback()
    await drain()

    assert h.manager.get_active_batch(1) is None
    h.on_batch_sealed.assert_awaited_once()
    h.on_batch_closed.assert_awaited_once()


async def test_failed_download_counts_as_error_but_does_not_stop_batch():
    h = make_harness()
    h.on_download_file.side_effect = [make_outcome(success=False), make_outcome(success=True)]
    await h.manager.enqueue(1, "msg-1")
    await h.manager.enqueue(1, "msg-2")

    sealed = await h.manager.seal_batch(1)
    await drain()

    assert sealed.error_count == 1
    assert sealed.file_count == 2


async def test_enqueue_after_seal_starts_a_brand_new_batch():
    h = make_harness()
    await h.manager.enqueue(1, "msg-1")
    first = await h.manager.seal_batch(1)

    second = await h.manager.enqueue(1, "msg-2")
    await drain()

    assert second.batch_id != first.batch_id
    assert second.queued_count == 1
    assert h.on_batch_created.await_count == 2


async def test_downloads_for_the_same_user_run_strictly_one_at_a_time():
    h = make_harness()
    gate = asyncio.Event()
    started_batch_ids: "list[int]" = []

    async def on_download_file(batch, message):
        started_batch_ids.append(batch.batch_id)
        if batch.batch_id == 1:
            await gate.wait()
        return make_outcome()

    h.on_download_file.side_effect = on_download_file

    first = await h.manager.enqueue(1, "a")
    await h.manager.seal_batch(1)
    await drain()

    second = await h.manager.enqueue(1, "b")
    await h.manager.seal_batch(1)
    await drain()

    # batch 2's download must not have started while batch 1 is still in flight
    assert started_batch_ids == [first.batch_id]
    h.on_batch_closed.assert_not_awaited()

    gate.set()
    await drain()

    assert started_batch_ids == [first.batch_id, second.batch_id]
    assert h.on_batch_closed.await_count == 2


async def test_two_users_have_independent_batches_and_timers():
    h = make_harness()

    await h.manager.enqueue(1, "msg-1")
    await h.manager.enqueue(2, "msg-2")

    await h.manager.seal_batch(1)
    await drain()

    assert h.manager.get_active_batch(1) is None
    assert h.manager.get_active_batch(2) is not None
