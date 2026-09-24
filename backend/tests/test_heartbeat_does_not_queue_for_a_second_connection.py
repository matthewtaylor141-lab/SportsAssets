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


def test_pool_stats_can_still_diagnose_saturation():
    """The one read that distinguishes a saturated pool from a slow
    database must keep working -- it is how this was identified."""
    assert callable(DB.pool_stats)
    doc = DB.pool_stats.__doc__ or ""
    assert "saturated pool" in doc
