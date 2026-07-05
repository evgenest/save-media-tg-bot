from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from itertools import count
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Protocol

from downloader import DownloadResult


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], Awaitable[None]]], TimerHandle]


def asyncio_timer_factory(delay: float, callback: Callable[[], Awaitable[None]]) -> TimerHandle:
    loop = asyncio.get_running_loop()
    return loop.call_later(delay, lambda: asyncio.create_task(callback()))


class BatchPhase(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    CLOSED = "closed"


@dataclass
class Batch:
    batch_id: int
    user_id: int
    started_at: datetime
    last_activity_at: datetime
    phase: BatchPhase = BatchPhase.PENDING
    queued_messages: List[Any] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    error_count: int = 0

    @property
    def queued_count(self) -> int:
        return len(self.queued_messages)


OnBatchCreated = Callable[[Batch], Awaitable[None]]
OnBatchSealed = Callable[[Batch], Awaitable[None]]
OnDownloadFile = Callable[[Batch, Any], Awaitable[DownloadResult]]
OnFileDownloaded = Callable[[Batch, DownloadResult], Awaitable[None]]
OnBatchClosed = Callable[[Batch], Awaitable[None]]


class BatchManager:
    """Two-phase batch state machine.

    A batch collects incoming messages (PENDING) without downloading anything.
    Once sealed - by inactivity timeout or a manual trigger - it moves to a
    per-user download queue and is processed strictly one file at a time,
    independently of whatever new PENDING batch starts collecting next.
    """

    def __init__(
        self,
        batch_timeout: float,
        on_batch_created: OnBatchCreated,
        on_batch_sealed: OnBatchSealed,
        on_download_file: OnDownloadFile,
        on_file_downloaded: OnFileDownloaded,
        on_batch_closed: OnBatchClosed,
        timer_factory: TimerFactory = asyncio_timer_factory,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._batch_timeout = batch_timeout
        self._on_batch_created = on_batch_created
        self._on_batch_sealed = on_batch_sealed
        self._on_download_file = on_download_file
        self._on_file_downloaded = on_file_downloaded
        self._on_batch_closed = on_batch_closed
        self._timer_factory = timer_factory
        self._clock = clock
        self._active: Dict[int, Batch] = {}
        self._timers: Dict[int, TimerHandle] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._queues: Dict[int, Deque[Batch]] = {}
        self._workers: Dict[int, asyncio.Task] = {}
        self._next_batch_id = count(1)

    def get_active_batch(self, user_id: int) -> Optional[Batch]:
        return self._active.get(user_id)

    async def enqueue(self, user_id: int, message: Any) -> Batch:
        async with self._get_lock(user_id):
            batch = self._active.get(user_id)
            if batch is None:
                now = self._clock()
                batch = Batch(
                    batch_id=next(self._next_batch_id),
                    user_id=user_id,
                    started_at=now,
                    last_activity_at=now,
                )
                self._active[user_id] = batch
                await self._on_batch_created(batch)

            batch.queued_messages.append(message)
            batch.last_activity_at = self._clock()
            self._reset_timer(user_id)
            return batch

    async def seal_batch(self, user_id: int) -> Optional[Batch]:
        async with self._get_lock(user_id):
            batch = self._active.pop(user_id, None)
            if batch is None:
                return None
            self._cancel_timer(user_id)
            batch.phase = BatchPhase.DOWNLOADING
            await self._on_batch_sealed(batch)

        self._queues.setdefault(user_id, deque()).append(batch)
        worker = self._workers.get(user_id)
        if worker is None or worker.done():
            self._workers[user_id] = asyncio.create_task(self._run_worker(user_id))
        return batch

    async def _run_worker(self, user_id: int) -> None:
        queue = self._queues.get(user_id)
        if queue is None:
            return

        while queue:
            batch = queue.popleft()
            for message in batch.queued_messages:
                outcome = await self._on_download_file(batch, message)
                batch.file_count += 1
                batch.total_bytes += outcome.size_bytes
                if not outcome.success:
                    batch.error_count += 1
                await self._on_file_downloaded(batch, outcome)

            batch.phase = BatchPhase.CLOSED
            await self._on_batch_closed(batch)

        self._queues.pop(user_id, None)
        self._workers.pop(user_id, None)

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    def _reset_timer(self, user_id: int) -> None:
        self._cancel_timer(user_id)
        self._timers[user_id] = self._timer_factory(
            self._batch_timeout, lambda: self.seal_batch(user_id)
        )

    def _cancel_timer(self, user_id: int) -> None:
        timer = self._timers.pop(user_id, None)
        if timer is not None:
            timer.cancel()
