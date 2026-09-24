"""A LOOP HOLDING A CONNECTION MUST NOT QUEUE FOR ANOTHER ONE.

THE FAILURE THIS EXISTS FOR (acceptance run 27, 2026-09-24). The run failed
at "The proxy does not serve HTML for an API path", which was a symptom.
The cause, from the service's own log:

    rn1x_shadow.py:783 in run -> await heartbeat(SERVICE, ...)
    db.py:191 in heartbeat -> async with pool.acquire(timeout=...)
    TimeoutError

The four rn1x loops each hold ONE session-scoped connection for their whole
life -- they must, because the advisory lock that makes them the single
writer dies with the session -- out of a pool with max_size=10. That leaves
six for request traffic AND for every `heartbeat()` call. Under a desk
sweep the pool saturates; DB-backed routes took 14 979 ms and the gateway
turned them into 502.

TWO THINGS MAKE THIS WORSE THAN A MISSING HEARTBEAT:

  * `flow` -- the fetched/processed/deferred/refused/failed/written
    reconciliation -- travels inside that heartbeat record. A starved pool
    erases the accounting, and the lane then reads as though it examined
    nothing. Run 26's `flow {}` had TWO independent causes and I had
    credited only one of them.
  * It is chronic, not new: seven TimeoutErrors in three hours, six of them
    BEFORE the release that exposed it (11:42:29, 11:53:35, 12:06:45,
    12:10:04, 12:16:30, 12:33:15, then 13:12:35). Run 26 passed while one
    was firing, so whether an acceptance run trips over it is luck.

`ext_pinnacle_loop` and `rn1x_model_loop` already wrote their heartbeats on
the connection they held. `rn1x_shadow` was the one reaching back into the
pool. It no longer does.
"""
import inspect

import pytest

from sportsassets import db as DB
from sportsassets.workers import rn1x_shadow as SH


def _code(fn) -> str:
    src = inspect.getsource(fn)
    for line in (fn.__doc__ or "").splitlines():
        src = src.replace(line, "")
    return "\n".join(l.split("#", 1)[0] for l in src.splitlines())


class _Con:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args, **kw):
        self.executed.append((sql, args))
        return "INSERT 0 1"


class _ExplodingPool:
    """A saturated pool: any acquire is a TimeoutError.

    This is the production condition, reproduced. A heartbeat that reaches
    for this pool fails; one that uses the connection it already holds does
    not.
    """

    def acquire(self, *a, **kw):
        raise AssertionError(
            "heartbeat reached for a pooled connection while the caller "
            "already held one -- this is the run-27 starvation path")


def test_a_heartbeat_with_a_connection_never_touches_the_pool(monkeypatch):
    import asyncio

    con = _Con()

    async def _no_pool():
        raise AssertionError("get_pool must not be called when con is given")

    monkeypatch.setattr(DB, "get_pool", _no_pool)
    asyncio.run(DB.heartbeat("svc", "ok", {"flow": {"written": 3}}, con=con))
    assert len(con.executed) == 1
    sql, args = con.executed[0]
    assert "service_heartbeats" in sql
    assert args[0] == "svc"
    # the detail -- which is where `flow` lives -- actually went in
    assert "written" in args[2]


def test_the_pool_path_still_exists_for_callers_without_a_connection(monkeypatch):
    """The fix must not remove the ordinary route; most services have no
    long-lived connection to lend."""
    src = _code(DB.heartbeat)
    assert "pool = await get_pool()" in src
    assert "HEARTBEAT_TIMEOUT_S" in src


def test_the_shadow_loop_beats_on_its_held_connection():
    src = _code(SH.run)
    beats = [l for l in src.splitlines() if "heartbeat(" in l]
    assert beats, "the loop must still heartbeat"
    # every heartbeat call in run() passes the held connection
    joined = _code(SH.run)
    assert joined.count("con=conn") >= 3, (
        "each heartbeat in run() -- standby, cycle and error -- must use "
        "the connection the loop already holds")


def test_the_error_heartbeat_falls_back_rather_than_going_silent():
    """If the held connection is itself the casualty, an error heartbeat
    must still be attempted through the pool."""
    src = _code(SH.run)
    # TWO error beats: the first on the held connection, the second
    # through the pool in case that connection is itself the casualty.
    assert src.count('heartbeat(SERVICE, "error"') == 2, src.count(
        'heartbeat(SERVICE, "error"')
    # and the fallback is the one WITHOUT con=
    first = src.find('heartbeat(SERVICE, "error"')
    second = src.find('heartbeat(SERVICE, "error"', first + 1)
    assert "con=conn" in src[first:second]
    assert "con=conn" not in src[second:second + 200]


def test_the_other_two_loops_already_avoided_the_pool():
    """Stated as a test so a future refactor cannot quietly reintroduce
    the acquire in the loops that never had it."""
    from sportsassets.workers import ext_pinnacle_loop as EXT
    from sportsassets.workers import rn1x_model_loop as ML

    for mod in (EXT, ML):
        src = _code(mod._heartbeat)
        assert "conn.execute" in src, mod.__name__
        assert "pool.acquire" not in src, mod.__name__


def test_a_failed_heartbeat_does_not_stop_position_management():
    """TELEMETRY IS NOT THE WORK.

    A heartbeat that times out leaves the tile STALE. It must not take the
    cycle down with it, because the cycle is what manages positions. Run
    with heartbeat raising TimeoutError on every call: `cycle` must still
    be entered repeatedly.

    This also fixes an overstatement of mine. I wrote that a starved pool
    "erases the accounting". It does not. The cycle's work lands in its own
    tables; what is lost is the heartbeat RECORD that reports it, so `flow`
    reads stale or absent. Stale telemetry and destroyed data are different
    failures and only the first one happened.
    """
    import asyncio

    calls = {"cycle": 0, "beats": 0}

    async def _cycle(conn, *, lane="HISTORICAL"):
        calls["cycle"] += 1
        return {"state": "IDLE_NO_CANDIDATES", "results": []}

    async def _beat(*a, **kw):
        calls["beats"] += 1
        raise TimeoutError("saturated pool")

    class _Conn:
        async def fetchval(self, *a, **kw):
            return True                      # the lock is granted

    class _Pool:
        def acquire(self, *a, **kw):
            class _Ctx:
                async def __aenter__(_s):
                    return _Conn()

                async def __aexit__(_s, *e):
                    return False
            return _Ctx()

    async def _get():
        return _Pool()

    async def main():
        import sportsassets.workers.rn1x_shadow as M

        orig_cycle, orig_beat = M.cycle, M.heartbeat
        orig_tick, orig_idle = M.TICK_S, M.IDLE_S
        M.cycle, M.heartbeat = _cycle, _beat
        M.TICK_S, M.IDLE_S = 0.01, 0.01
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(M.run(_get), timeout=1.0)
        finally:
            M.cycle, M.heartbeat = orig_cycle, orig_beat
            M.TICK_S, M.IDLE_S = orig_tick, orig_idle

    asyncio.run(main())
    assert calls["beats"] >= 1, "the heartbeat was never attempted"
    assert calls["cycle"] >= 4, (
        "a failing heartbeat stopped the cycle: position management would "
        "halt silently while the loop still looked armed (cycles=%d)"
        % calls["cycle"])


def test_every_write_goes_through_the_lock_holding_connection():
    """A PROCESS THAT LOST ITS LOCK MUST NOT STILL BE WRITING.

    The lock is session-scoped on the connection `run()` holds, so if that
    connection dies the lock is released -- and every write the cycle makes
    must travel on that SAME connection, or a process could keep writing
    from the pool after ownership was gone. `cycle(conn, ...)` takes the
    connection as its first argument and the existing
    test_the_lock_is_released_when_the_holder_disconnects covers the
    release itself; this pins that the cycle is not handed a pool.
    """
    sig = inspect.signature(SH.cycle)
    assert list(sig.parameters)[0] == "conn", (
        "the cycle must write on the connection that holds the lock")
    src = _code(SH.cycle)
    assert "get_pool" not in src, (
        "the cycle reached for the pool instead of its own connection")
    assert "pool.acquire" not in src


def test_pool_stats_can_still_diagnose_saturation():
    """The one read that distinguishes a saturated pool from a slow
    database must keep working -- it is how this was identified."""
    assert callable(DB.pool_stats)
    doc = DB.pool_stats.__doc__ or ""
    assert "saturated pool" in doc
