"""The stall watchdog records the code that blocked the event loop."""
import asyncio
import json
import time

import pytest

from sportsassets import loop_watchdog as W
from tests import paper_harness as H


def _block_the_loop_synchronously(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:      # pure-Python busy work on the loop
        sum(range(1000))


@pytest.mark.asyncio
async def test_a_blocked_loop_is_recorded_with_the_blocking_frame(monkeypatch):
    monkeypatch.setattr(W, "STALL_S", 0.6)
    W._ring.clear()
    tasks = W.start(lambda: None)
    try:
        await asyncio.sleep(0.4)            # ticks running, no stall
        assert W.snapshot() == []
        _block_the_loop_synchronously(1.5)
        await asyncio.sleep(0.6)            # let the end of the stall be seen
        rows = W.snapshot()
        assert len(rows) == 1, rows
        r = rows[0]
        assert r["loop_lag_s"] >= 0.6
        assert r["ended_lag_s"] >= 1.4
        loop_stack = next(v for k, v in r["stacks"].items()
                          if k.startswith("LOOP"))
        assert any("_block_the_loop_synchronously" in ln
                   for ln in loop_stack), loop_stack
        # one record per stall, not one per watchdog tick
        await asyncio.sleep(0.5)
        assert len(W.snapshot()) == 1
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_no_record_while_the_loop_keeps_ticking(monkeypatch):
    monkeypatch.setattr(W, "STALL_S", 0.6)
    W._ring.clear()
    tasks = W.start(lambda: None)
    try:
        for _ in range(8):
            await asyncio.sleep(0.15)
        assert W.snapshot() == []
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_the_ring_is_persisted_to_ingestion_state(monkeypatch):
    import asyncpg
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)

    async def get_pool():
        return pool

    try:
        W._ring.clear()
        W._record({"at": 1.0, "loop_lag_s": 3.2, "watchdog_overrun_s": 0.1,
                   "stacks": {"LOOP MainThread": ["x.py:1 f"]}})
        sleeps = []
        real_sleep = asyncio.sleep

        async def fast_sleep(s):
            sleeps.append(s)
            if len(sleeps) > 2:
                raise asyncio.CancelledError
            await real_sleep(0)

        monkeypatch.setattr(W.asyncio, "sleep", fast_sleep)
        with pytest.raises(asyncio.CancelledError):
            await W._persist(get_pool)
        async with pool.acquire() as c:
            v = await c.fetchval("SELECT value FROM ingestion_state "
                                 "WHERE key = $1", W.STATE_KEY)
        v = json.loads(v) if isinstance(v, str) else v
        assert v["stalls"][-1]["loop_lag_s"] == 3.2
        assert v["stall_s"] == W.STALL_S
    finally:
        await pool.close()
