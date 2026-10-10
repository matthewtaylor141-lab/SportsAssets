"""rc6.3 capability (review fix): a tick timeout's lateness is measured when
its deadline fires, never after the step has unwound.

THE DEFECT (b3b71804). `_phase.__aexit__` took `elapsed_s` only after
`pool.acquire().__aexit__` had finished. When the deadline cancels a statement
in flight, asyncpg's PoolConnectionHolder.release awaits the server's answer to
the cancel (`_protocol._wait_for_cancellation()`) and then `reset()`: two more
round trips that take as long as a stalled database takes, WITHOUT holding the
event loop. That unwind was counted as lateness, so describe() said '(expired
X s late: the event loop was held past the deadline)' for a loop that was never
held. Real asyncpg against real Postgres through a proxy that delays the
server's bytes by awaiting (the reviewer's repro): 'CLAIM: TimeoutError at
STATEMENTS after 3.32s of a 0.3s budget (expired 3.02s late: the event loop
was held past the deadline)' with a largest event-loop lag of 0.004 s. The
production question it would have misdirected: the CLAIM / CONTROL / HEARTBEAT
timeouts during the ingestion_state read stalls (max 2,880 ms with FOR UPDATE,
2,972 ms without).

THE FIX. `_phase.bound()` arms a probe at the timeout's own deadline; it runs
in the same loop iteration as the timeout's callback, so its delay past the
deadline is only the time the loop was held. The unwind after the deadline is
reported apart (`unwind_s`, 'unwinding after the deadline (cancel and
release) took N s more').

The first three tests need no database (the pool is a stand-in whose release
awaits like asyncpg's). The last uses real asyncpg against the test Postgres
(RN1X_TEST_DSN) through an awaiting TCP proxy; it needs no schema.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit, urlunsplit

import pytest

from sportsassets.agents import capability_runtime as R
from sportsassets.agents import capability_work as W
from tests import paper_harness as H


class _LoopLag:
    """The largest gap between 10 ms sleeps: how long the loop was held."""

    def __init__(self):
        self.max = 0.0
        self._task = None

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            t = loop.time()
            await asyncio.sleep(0.01)
            self.max = max(self.max, loop.time() - t - 0.01)

    def start(self):
        self._task = asyncio.ensure_future(self._run())

    def stop(self):
        self._task.cancel()


class _AsyncpgLikeAcquire:
    """`pool.acquire()` whose release, after a statement was cancelled in
    flight, does what asyncpg's PoolConnectionHolder.release does: awaits the
    server's answer to the cancel (_wait_for_cancellation), then reset() --
    each one round trip to a database that answers in `round_trip_s`. It
    awaits; it never holds the event loop. Pool.release runs it under
    asyncio.shield, as asyncpg does."""

    def __init__(self, round_trip_s):
        self.round_trip_s = round_trip_s

    async def __aenter__(self):
        return MagicMock()

    async def _release_after_cancel(self):
        await asyncio.sleep(self.round_trip_s)   # _wait_for_cancellation()
        await asyncio.sleep(self.round_trip_s)   # reset()

    async def __aexit__(self, et, ev, tb):
        if ev is not None:
            await asyncio.shield(self._release_after_cancel())
        return False


def _pool(acquire):
    pool = MagicMock()
    pool.acquire = acquire
    return pool


def _shrink(monkeypatch, budget):
    real = asyncio.timeout
    monkeypatch.setattr(R.asyncio, "timeout", lambda s: real(min(s, budget)))


def _arm_tick(monkeypatch, claim):
    monkeypatch.setattr(W, "schema", AsyncMock(return_value=True))
    monkeypatch.setattr(W, "control",
                        AsyncMock(return_value={"enabled": True}))
    monkeypatch.setattr(R, "admit", AsyncMock(return_value=0))
    monkeypatch.setattr(W, "claim", claim)


async def _failed_tick(pool):
    lag = _LoopLag()
    lag.start()
    try:
        with pytest.raises(R.TickPhaseFailed) as got:
            await R.tick(pool)
    finally:
        lag.stop()
    return got.value, lag.max


def test_a_slow_release_after_the_deadline_is_not_called_a_held_loop(
        monkeypatch):
    """The statement is cancelled at its 0.05 s deadline; the release then
    waits 2 x 0.4 s on the database. The loop is never held, so the failure
    must not say it was: it fired on time and the unwind is named apart."""
    budget, round_trip = 0.05, 0.4

    async def stalled_claim(conn, now):
        await asyncio.sleep(10)
    _arm_tick(monkeypatch, stalled_claim)
    _shrink(monkeypatch, budget)
    err, lag = asyncio.run(_failed_tick(
        _pool(lambda *a, **kw: _AsyncpgLikeAcquire(round_trip))))
    text = err.describe()
    assert "the event loop was held" not in text, (text, lag)
    assert err.phase == "CLAIM" and err.step == R.STATEMENTS
    assert err.elapsed_s - err.budget_s < R.LATE_S, (text, lag)
    assert err.unwind_s >= 2 * round_trip - 0.05, text
    assert ("unwinding after the deadline (cancel and release) took "
            in text), text
    assert text.startswith("CLAIM: TimeoutError at STATEMENTS after ")


def test_a_held_loop_and_a_slow_release_are_each_reported_as_what_they_were(
        monkeypatch):
    """The loop IS held 0.8 s across the deadline, then the release waits
    2 x 1.0 s on the database. The lateness is the hold (about 0.75 s), not
    hold plus release (about 2.75 s); the release is the unwind."""
    budget, hold, round_trip = 0.05, 0.8, 1.0

    async def held_then_stalled_claim(conn, now):
        time.sleep(hold)                 # the event loop is held
        await asyncio.sleep(10)
    _arm_tick(monkeypatch, held_then_stalled_claim)
    _shrink(monkeypatch, budget)
    err, _ = asyncio.run(_failed_tick(
        _pool(lambda *a, **kw: _AsyncpgLikeAcquire(round_trip))))
    text = err.describe()
    late = err.elapsed_s - err.budget_s
    assert R.LATE_S <= late < hold + 0.3, text
    assert "the event loop was held past the deadline" in text
    assert err.unwind_s >= 2 * round_trip - 0.05, text
    assert "unwinding after the deadline" in text


def test_a_step_that_finishes_in_time_leaves_no_deadline_probe_armed():
    """The probe is disarmed when the step ends: a step that finished before
    its deadline never records the deadline as fired afterwards."""
    async def main():
        async with R._phase("X", 0.05) as ph:
            async with ph.bound():
                await asyncio.sleep(0)
        await asyncio.sleep(0.15)
        return ph
    ph = asyncio.run(main())
    assert ph.fired_at is None and ph.fired_step is None


# ── real asyncpg, real Postgres, a stalling (awaiting) proxy ──────────

def _proxied(dsn, port):
    u = urlsplit(dsn)
    userinfo = u.netloc.rsplit("@", 1)[0] + "@" if "@" in u.netloc else ""
    return urlunsplit((u.scheme, "%s127.0.0.1:%d" % (userinfo, port),
                       u.path, u.query, u.fragment))


@pytest.mark.skipif(not H.DSN, reason="requires RN1X_TEST_DSN")
def test_real_asyncpg_cancel_and_reset_under_a_database_stall_is_the_unwind(
        monkeypatch):
    """The reviewer's repro as a regression. Once the claim starts, a TCP
    proxy delays every server-to-client chunk by awaiting asyncio.sleep (a
    database stall that never holds the loop). The claim's statement is
    cancelled at its 0.3 s deadline; asyncpg's release then waits on the
    cancel and the reset through the stall. On b3b71804 this read 'after
    3.32s of a 0.3s budget (expired 3.02s late: the event loop was held past
    the deadline)'."""
    import asyncpg
    delay, budget = 0.6, 0.3
    target = urlsplit(H.DSN)
    host, port = target.hostname or "127.0.0.1", target.port or 5432
    stalled = {"on": False}

    async def pipe(reader, writer, delayed):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                if delayed and stalled["on"]:
                    await asyncio.sleep(delay)
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def handle(cr, cw):
        sr, sw = await asyncio.open_connection(host, port)
        asyncio.ensure_future(pipe(cr, sw, False))
        asyncio.ensure_future(pipe(sr, cw, True))

    async def stalled_claim(conn, now):
        stalled["on"] = True
        await conn.fetchval("SELECT pg_sleep(20)")
    _arm_tick(monkeypatch, stalled_claim)

    async def main():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        pool = await asyncpg.create_pool(
            _proxied(H.DSN, server.sockets[0].getsockname()[1]),
            min_size=1, max_size=2)
        try:
            real = asyncio.timeout
            monkeypatch.setattr(R.asyncio, "timeout",
                                lambda s: real(min(s, budget)))
            try:
                return await _failed_tick(pool)
            finally:
                monkeypatch.setattr(R.asyncio, "timeout", real)
                stalled["on"] = False
        finally:
            try:
                await asyncio.wait_for(pool.close(), 10)
            except Exception:
                pool.terminate()
            server.close()

    err, lag = asyncio.run(main())
    text = err.describe()
    assert "the event loop was held" not in text, (text, lag)
    assert err.phase == "CLAIM" and err.step == R.STATEMENTS, text
    assert err.elapsed_s - err.budget_s < R.LATE_S, (text, lag)
    assert err.unwind_s >= delay, text
    assert "unwinding after the deadline (cancel and release) took " in text
