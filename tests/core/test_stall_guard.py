import asyncio

import pytest

from mediasaver.core.stall_guard import DownloadStalled, run_with_stall_guard


async def test_returns_result_when_progress_keeps_coming():
    seen = []

    async def forward(current, total):
        seen.append(current)

    async def start(progress):
        # Total runtime exceeds the timeout, but no single gap does.
        for i in range(5):
            await asyncio.sleep(0.03)
            await progress(i, 5)
        return "ok"

    assert await run_with_stall_guard(start, 0.1, forward) == "ok"
    assert seen == [0, 1, 2, 3, 4]


async def test_raises_and_cancels_when_no_progress():
    cancelled = asyncio.Event()

    async def start(progress):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(DownloadStalled):
        await run_with_stall_guard(start, 0.05)
    assert cancelled.is_set()


async def test_propagates_download_error():
    async def start(progress):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await run_with_stall_guard(start, 1.0)
