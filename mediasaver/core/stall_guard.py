from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

Progress = Callable[[int, int], Awaitable[None]]


class DownloadStalled(Exception):
    """No progress reported for longer than the allowed stall timeout."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"загрузка зависла: нет данных {seconds:.0f} с")
        self.seconds = seconds


async def run_with_stall_guard(
    start: Callable[[Progress], Awaitable[T]],
    stall_timeout: float,
    progress: Optional[Progress] = None,
) -> T:
    """Run `start(progress)` and cancel it once no progress arrives for
    `stall_timeout` seconds. A slow but moving download is never cut off:
    the deadline moves forward with every progress report."""
    last_progress = time.monotonic()

    async def tracked(current: int, total: int) -> None:
        nonlocal last_progress
        last_progress = time.monotonic()
        if progress is not None:
            await progress(current, total)

    task = asyncio.ensure_future(start(tracked))
    try:
        while True:
            remaining = last_progress + stall_timeout - time.monotonic()
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise DownloadStalled(stall_timeout)
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
