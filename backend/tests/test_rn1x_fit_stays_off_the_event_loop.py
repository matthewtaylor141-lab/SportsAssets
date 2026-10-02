"""The rn1x model fit runs in a worker thread: the event loop keeps serving.

Production evidence (loop watchdog, 2026-10-02 14:43:48Z): the fit held the
API's loop for 49 s, past /healthz's 5 s deadline."""
import asyncio
import time

import pytest

from sportsassets.workers import rn1x_model_loop as M


@pytest.mark.asyncio
async def test_the_loop_keeps_ticking_while_the_model_fits(monkeypatch):
    def slow_fit(rows):
        end = time.monotonic() + 1.2
        while time.monotonic() < end:            # pure-Python busy work
            sum(range(500))
        return {"ok": False, "refusal": "TEST", "n": 0}

    async def ok(*a, **k):
        return {}
    monkeypatch.setattr(M, "fit", slow_fit)
    monkeypatch.setattr(M, "_running", lambda conn: _aret((True, None)))
    monkeypatch.setattr(M, "_table_ready", lambda conn: _aret(True))
    monkeypatch.setattr(M, "prepare", lambda conn, now=None: _aret(
        {"closed": [], "open": [], "dataset_sha": "x"}))
    monkeypatch.setattr(M, "join_outcomes", ok)
    monkeypatch.setattr(M, "evaluate", ok)
    monkeypatch.setattr(M, "_heartbeat", ok)

    ticks, gaps = [], []

    async def ticker():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            ticks.append(now)

    t = asyncio.create_task(ticker())
    try:
        out = await M.cycle(object())
    finally:
        t.cancel()
    assert out["fit"]["refusal"] == "TEST"
    assert len(ticks) >= 8, len(ticks)            # served during the fit
    assert max(gaps) < 0.6, max(gaps)             # never blocked for long


async def _aret(v):
    return v
