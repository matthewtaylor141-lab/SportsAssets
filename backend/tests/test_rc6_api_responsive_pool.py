"""RC6 api-responsive: a single-writer loop's heartbeat beats on the session
the loop holds, never on a second connection from the shared pool.

PRODUCTION (RC5, 2026-10-08): with the API's pool saturated, the rn1x learn
loop's heartbeat queued for a second connection and timed out inside
asyncpg's Pool._acquire (18:37:41Z, render-ops logs); the cycle it reported
had finished. The pool's half of the same afternoon is /healthz's
(test_rc6_api_responsive_healthz).
"""
from __future__ import annotations

import asyncio

import pytest

# ═════════════════════════════════════════════════════════════════════
# 5 · THE RN1X LEARN HEARTBEAT BEATS ON ITS OWN SESSION
# ═════════════════════════════════════════════════════════════════════

def test_the_learn_loop_beats_on_the_session_it_holds(monkeypatch):
    """Production 2026-10-08 18:37:41Z: this beat queued for a second
    connection from the saturated shared pool and timed out inside asyncpg's
    Pool._acquire; the finished cycle was logged as failed."""
    from sportsassets.workers import rn1x_learn_loop as RL

    class _Conn:
        async def fetchval(self, sql, *a):
            return True                   # the evaluator lock is ours

    held = _Conn()

    class _Lease:
        def __init__(self, pool, *, name):
            pass

        async def __aenter__(self):
            return held

        async def __aexit__(self, *a):
            return False

    beats = []

    async def heartbeat(service, status="ok", detail=None, con=None):
        beats.append((status, con))

    n = {"cycles": 0}

    async def cycle(conn, *, code_sha="unknown"):
        n["cycles"] += 1
        if n["cycles"] > 1:
            raise asyncio.CancelledError
        return {"state": "OK"}

    async def no_sleep(_s):
        return None

    async def pool():
        return object()

    monkeypatch.setattr(RL, "lease_session", _Lease)
    monkeypatch.setattr(RL, "heartbeat", heartbeat)
    monkeypatch.setattr(RL, "cycle", cycle)
    monkeypatch.setattr(RL.asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(RL.run(pool))
    assert beats and beats[0] == ("ok", held), beats
