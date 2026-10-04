"""R30A RUNTIME: THE API POOL STARVATION, ITS ROOT CAUSE AND ITS FIX.

PRODUCTION EVIDENCE (2026-10-04, read-only):

  * render-ops `logs` (API, 15:46-18:47Z): `pinnapi reactive audit failed;
    no unaudited evaluation started` x14, `agent research tick failed:
    TimeoutError` x17, `pinnapi feed heartbeat failed` x9 -- every traceback
    ends in asyncpg Pool._acquire -> TimeoutError, and they cluster at the same
    seconds (16:00:53, 17:40:58, 17:46:10, 18:21:28).
  * research-sql run 37226381750 (pg_stat_activity + pg_locks): the API host
    held TEN pooled backends (the pool's max_size) and SIX of them were the
    session advisory locks of the single-writer loops -- execmirror 0x45584d31,
    desk ...031, rn1x_shadow ...032, rn1x_learn ...033, ext_pinnacle ...034,
    rn1x_model ...035 -- each connected since boot.
  * research-sql run 37226551461: 19 reactive attempts left STARTED for ever in
    24 h (13 on HELD positions): the completion audit could not get a
    connection, so the evaluation's valuation ids were never recorded.

THE FIX, PINNED HERE:
  1 · every single-writer loop holds its lock on a session of its own
      (`db.lease_session`), outside the shared pool;
  2 · a loop that loses its lock (or whose session dies) stops and contends
      again (`db.advisory_held` fencing) instead of looping on a dead session;
  3 · the reactive scheduler takes ONE connection per job for both audits and
      the evaluation, and refuses a job it cannot get a connection for
      (SESSION_UNAVAILABLE) before anything starts.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import os

import pytest

from sportsassets import db as DB

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


def _lock_holders():
    from sportsassets import bettor_desk_loop, execmirror
    from sportsassets.workers import (ext_pinnacle_loop, rn1x_learn_loop,
                                      rn1x_model_loop, rn1x_shadow)
    return (execmirror, bettor_desk_loop, rn1x_shadow, rn1x_learn_loop,
            ext_pinnacle_loop, rn1x_model_loop)


def _run_src(mod) -> str:
    tree = ast.parse(inspect.getsource(mod))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run":
            return ast.unparse(node)
    raise AssertionError("%s has no run()" % mod.__name__)


# ── 1 · the six lock holders take their session outside the shared pool ──

def test_the_six_production_lock_holders_are_exactly_these_keys():
    """The six granted advisory keys read in production are these loops'."""
    keys = {m.__name__.rsplit(".", 1)[-1]: m.LOCK_KEY for m in _lock_holders()}
    assert keys == {
        "execmirror": 0x45584D31,
        "bettor_desk_loop": 7723901544120031,
        "rn1x_shadow": 7723901544120032,
        "rn1x_learn_loop": 7723901544120033,
        "ext_pinnacle_loop": 7723901544120034,
        "rn1x_model_loop": 7723901544120035,
    }


def test_every_lock_holder_takes_its_lock_session_from_lease_session():
    for m in _lock_holders():
        src = _run_src(m)
        if "pg_try_advisory_lock" not in src:
            # the desk loop asks through its own helper
            assert "_acquire(conn)" in src, m.__name__
            assert "pg_try_advisory_lock" in inspect.getsource(m._acquire)
        assert src.count("lease_session(") == 1, (
            "%s must hold its session advisory lock on a session of its own "
            "(db.lease_session), not on a slot of the shared pool" % m.__name__)
        assert "pool.acquire()" not in src, m.__name__


def test_a_test_double_pool_keeps_its_own_acquire():
    """Only the process's own pool is bypassed: an injected pool keeps
    working unchanged (every fake-pool test of these loops relies on it)."""
    seen = []

    class _Conn:
        pass

    class _Ctx:
        async def __aenter__(self):
            seen.append("acquired")
            return _Conn()

        async def __aexit__(self, *exc):
            seen.append("released")
            return False

    class _Pool:
        def acquire(self, *a, **kw):
            return _Ctx()

    async def main():
        async with DB.lease_session(_Pool(), name="t") as conn:
            assert isinstance(conn, _Conn)
    asyncio.run(main())
    assert seen == ["acquired", "released"]


def test_advisory_key_parts_match_pg_locks_spelling():
    # pg_locks: classid = high 32 bits, objid = low 32 bits
    assert DB.advisory_key_parts(7723901544120034) == (0x1B70D8, 0xF7D13EE2)
    assert DB.advisory_key_parts(0x45584D31) == (0, 0x45584D31)


@pg
def test_lease_sessions_leave_the_shared_pool_free_for_a_two_second_audit():
    """THE PRODUCTION SHAPE, REPRODUCED. A pool of six (scaled from ten)
    with six lock holders: on the old path (`pool.acquire()` held for life)
    the next 2 s acquire -- the reactive audit's budget -- times out exactly
    as the production tracebacks did; on lease sessions it succeeds, and the
    six locks are still held, each on its own named backend."""
    import asyncpg

    async def main():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=6)
        saved_pool, saved_dsn = DB._pool, DB._dsn
        DB._pool, DB._dsn = pool, (lambda: DSN)
        keys = [990_000_000_000 + i for i in range(6)]
        try:
            # OLD PATH: six holders each keep a pooled connection
            held = [await pool.acquire() for _ in keys]
            for c, k in zip(held, keys):
                assert await c.fetchval("SELECT pg_try_advisory_lock($1)", k)
            with pytest.raises(asyncio.TimeoutError):
                async with pool.acquire(timeout=2.0):
                    pass
            for c, k in zip(held, keys):
                await c.execute("SELECT pg_advisory_unlock($1)", k)
                await pool.release(c)

            # FIXED PATH: the same six holders on lease sessions
            cms = [DB.lease_session(pool, name="holder%d" % i)
                   for i in range(6)]
            conns = [await cm.__aenter__() for cm in cms]
            try:
                assert all(cm.dedicated for cm in cms)
                for c, k in zip(conns, keys):
                    assert await c.fetchval(
                        "SELECT pg_try_advisory_lock($1)", k)
                    assert await DB.advisory_held(c, k) is True
                async with pool.acquire(timeout=2.0) as c:
                    assert await c.fetchval("SELECT 1") == 1
                apps = await pool.fetch(
                    "SELECT application_name FROM pg_stat_activity "
                    " WHERE application_name LIKE 'sportsassets-lease:holder%'")
                assert len(apps) == 6
            finally:
                for cm in cms:
                    await cm.__aexit__(None, None, None)
            # closing the sessions released every lock
            async with pool.acquire() as c:
                for k in keys:
                    assert await c.fetchval(
                        "SELECT pg_try_advisory_lock($1)", k)
                    await c.execute("SELECT pg_advisory_unlock($1)", k)
        finally:
            DB._pool, DB._dsn = saved_pool, saved_dsn
            await pool.close()

    asyncio.run(main())


@pg
def test_advisory_held_is_false_when_released_and_raises_on_a_dead_session():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        k = 990_000_000_100
        try:
            assert await DB.advisory_held(c, k) is False
            assert await c.fetchval("SELECT pg_try_advisory_lock($1)", k)
            assert await DB.advisory_held(c, k) is True
            await c.execute("SELECT pg_advisory_unlock($1)", k)
            assert await DB.advisory_held(c, k) is False
        finally:
            await c.close()
        with pytest.raises(Exception):
            await DB.advisory_held(c, k)

    asyncio.run(main())


# ── 2 · fencing: a lost lock ends the hold and the loop contends again ──

@pg
def test_execmirror_leaves_a_dead_session_and_contends_again(monkeypatch):
    """Before R30A the mirror's inner loop only ever left on cancellation: a
    session that died failed every tick for ever. Now the per-tick fence sees
    the dead session, the hold ends, a fresh session takes the lock, and the
    ticks continue there."""
    import asyncpg

    from sportsassets import execmirror as EXM
    from sportsassets import execution_intent as EI

    ticks = []

    async def _tick(self, conn):
        ticks.append(await conn.fetchval("SELECT pg_backend_pid()"))
        return {"state": "TEST"}

    monkeypatch.setattr(EXM.Mirror, "tick", _tick)
    monkeypatch.setattr(EI, "start", lambda get_pool, mirror: None)
    monkeypatch.setattr(EXM, "TICK_S", 0.02)
    monkeypatch.setattr(EXM, "CONTEND_RETRY_S", 0.05)

    async def main():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
        saved_pool, saved_dsn = DB._pool, DB._dsn
        DB._pool, DB._dsn = pool, (lambda: DSN)

        async def _get():
            return pool
        runner = asyncio.create_task(EXM.run(_get))
        try:
            async def _ticks(n):
                while len(ticks) < n:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(_ticks(3), 10)
            first = ticks[-1]
            # the database drops the holder's session
            await pool.execute("SELECT pg_terminate_backend($1)", first)

            async def _moved():
                while not ticks or ticks[-1] == first:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(_moved(), 10)
            second = ticks[-1]
            assert second != first
            hi, lo = DB.advisory_key_parts(EXM.LOCK_KEY)
            holder = await pool.fetchval(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' "
                "   AND granted AND classid = $1::bigint::oid "
                "   AND objid = $2::bigint::oid", hi, lo)
            assert holder == second
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner
            DB._pool, DB._dsn = saved_pool, saved_dsn
            await pool.close()

    asyncio.run(main())


def test_ext_pinnacle_stops_its_children_and_contends_again_when_fenced_out(
        monkeypatch):
    """The decider's fence, on fakes: the lock is granted, two passes run,
    the fence then reads NOT HELD -- the servicing task (the funded
    management lane) must stop with the hold, a NEW session must be taken,
    and the passes must resume on it."""
    from sportsassets import venue_cooldown_store as VCS
    from sportsassets.workers import ext_pinnacle_loop as L

    fence_answers = [True, True, False]       # then True for ever
    conns, cycles_on, servicing_alive = [], [], []

    class _Conn:
        def __init__(self, n):
            self.n = n

        async def execute(self, sql, *args):
            return "OK"

        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql:
                return True
            if "pg_locks" in sql:
                return fence_answers.pop(0) if fence_answers else True
            if "pg_backend_pid" in sql:
                return 1000 + self.n
            return None

    class _Ctx:
        async def __aenter__(self):
            c = _Conn(len(conns))
            conns.append(c)
            return c

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self, *a, **kw):
            return _Ctx()

    async def _cycle(conn, *a, **kw):
        cycles_on.append(conn.n)
        servicing_alive.append(L._SERVICING.get("task_active"))
        return {"ran": False}

    async def _service(conn, *, now, review_interval_s=L.CYCLE_S,
                       run_learning=True):
        return {"ok": True}

    async def _resume(conn):
        return {"resumed": False, "why": "TEST"}

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "cycle", _cycle)
    monkeypatch.setattr(L, "_funded_service", _service)
    monkeypatch.setattr(L, "_xavier_daily_review", _noop)
    monkeypatch.setattr(VCS, "load_and_resume", _resume)
    monkeypatch.setattr(VCS, "pending", lambda: None)
    monkeypatch.setattr(L, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr(L, "SERVICING_INTERVAL_S", 0.01)
    monkeypatch.setattr(L, "SERVICING_MIN_GAP_S", 0.0)

    async def main():
        async def _get():
            return _Pool()
        runner = asyncio.create_task(L.run(_get))
        try:
            async def _resumed():
                while not [n for n in cycles_on if n >= 1][:2]:
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(_resumed(), 10)
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner

    asyncio.run(main())
    # two passes on session 0, none after the fence said NOT HELD, then the
    # passes resumed on a NEW session
    assert cycles_on[:2] == [0, 0]
    assert 0 not in cycles_on[2:]
    assert len(conns) >= 2 and any(n >= 1 for n in cycles_on)
    assert L._SERVICING["task_active"] is False


# ── 3 · the reactive scheduler: one connection per job ──────────────────

def _reactive_fixture():
    from tests.test_pinnapi_reactive import AT, R, seed, tick
    cache, event = seed()
    return R, AT, cache, event, tick


class _Conn:
    def __init__(self, n, fail_completion=False):
        self.n, self.fail_completion = n, fail_completion


class _Session:
    def __init__(self, conns, *, wait_s=0.0, fail_first_completion=False):
        self.conns, self.wait_s = conns, wait_s
        self.fail_first_completion = fail_first_completion

    def __call__(self):
        sess = self

        class _Ctx:
            async def __aenter__(self):
                if sess.wait_s:
                    await asyncio.sleep(sess.wait_s)
                c = _Conn(len(sess.conns),
                          fail_completion=(sess.fail_first_completion
                                           and not sess.conns))
                sess.conns.append(c)
                return c

            async def __aexit__(self, *exc):
                return False
        return _Ctx()


def _scheduler(session, records, calls, done):
    R, AT, cache, event, tick = _reactive_fixture()

    async def audit(record, conn):
        if record["state"] != "STARTED" and conn.fail_completion:
            raise ConnectionError("cancelled statement left it unusable")
        records.append((copy.deepcopy(record), conn.n))
        if record["state"] in ("COMPLETED", "TIMEOUT", "REFUSED", "ERROR"):
            done.set()

    async def evaluate(job, conn):
        calls.append(conn.n)
        return {"valuation_ids": []}

    s = R.Scheduler(cache, evaluate, audit, clock=lambda: AT,
                    session=session, session_wait=0.2)
    s.register(event, sport_key="baseball_mlb", family="baseball",
               received_at=AT - 4)
    cache.on_change = s.changed
    return s, cache, tick


def test_both_audits_and_the_evaluation_share_one_connection():
    async def main():
        conns, records, calls, done = [], [], [], asyncio.Event()
        s, cache, tick = _scheduler(_Session(conns), records, calls, done)
        task = asyncio.create_task(s.run())
        try:
            tick(cache)
            await asyncio.wait_for(done.wait(), 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return conns, records, calls, s
    conns, records, calls, s = asyncio.run(main())
    assert len(conns) == 1, "one acquire per job, not three"
    assert [r["state"] for r, _ in records] == ["STARTED", "COMPLETED"]
    assert {n for _, n in records} == {0} and calls == [0]
    assert "session_wait_s" in records[0][0]
    assert s.counts["COMPLETED"] == 1


def test_no_connection_in_time_refuses_the_job_before_anything_starts():
    async def main():
        conns, records, calls, done = [], [], [], asyncio.Event()
        s, cache, tick = _scheduler(_Session(conns, wait_s=1.0), records,
                                    calls, done)
        task = asyncio.create_task(s.run())
        try:
            tick(cache)
            for _ in range(200):
                if s.counts["SESSION_UNAVAILABLE"]:
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return records, calls, s
    records, calls, s = asyncio.run(main())
    assert s.counts["SESSION_UNAVAILABLE"] == 1
    assert records == [] and calls == [], (
        "no audit row and no evaluation without a connection: fail closed")


def test_a_completion_audit_the_job_connection_cannot_take_is_retried_fresh():
    """The orphaned-STARTED defect: the completion record must land, on a
    fresh connection when the job's own cannot take it."""
    async def main():
        conns, records, calls, done = [], [], [], asyncio.Event()
        s, cache, tick = _scheduler(
            _Session(conns, fail_first_completion=True), records, calls, done)
        task = asyncio.create_task(s.run())
        try:
            tick(cache)
            await asyncio.wait_for(done.wait(), 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return conns, records, s
    conns, records, s = asyncio.run(main())
    assert [r["state"] for r, _ in records] == ["STARTED", "COMPLETED"]
    assert records[0][1] == 0 and records[1][1] == 1
    assert s.counts["COMPLETION_AUDIT_RETRIED"] == 1
    assert s.counts["AUDIT_FAILED"] == 0
