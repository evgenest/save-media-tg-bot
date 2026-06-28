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
