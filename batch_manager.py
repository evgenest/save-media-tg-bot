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
