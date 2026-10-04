"""R30A RUNTIME: THE API POOL STARVATION, ITS ROOT CAUSE AND ITS FIX.

PRODUCTION EVIDENCE (2026-10-04, read-only):

  * render-ops `logs` (API, 15:47-18:47Z; runs 37225745383 / 37225748641 /
    37225751870): `pinnapi reactive audit failed; no unaudited evaluation
    started` x12 -- 10 with the TimeoutError inside asyncpg Pool._acquire, 2
    (16:00:53, 17:46:10) inside the INSERT after the connection was acquired
    --, `agent research tick failed: TimeoutError` x17 (no traceback logged),
    `pinnapi feed heartbeat failed` x8, clustered in the same minutes.
    The pool was ONE of two causes; the other -- the API event loop held 2-4 s
    at a time in exactly those minutes -- is pinned in
    test_r30a_loop_stalls.py.
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
    """run() -- and, for the decider, the one contention attempt run() makes
    (_hold_once: R30A review moved the lease there so a failed attempt can
    never fail out of run)."""
    tree = ast.parse(inspect.getsource(mod))
    found = [ast.unparse(node) for node in tree.body
             if isinstance(node, ast.AsyncFunctionDef)
             and node.name in ("run", "_hold_once")]
    if not found:
        raise AssertionError("%s has no run()" % mod.__name__)
    return "\n".join(found)


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
def test_production_shape_ten_slots_six_holders_and_a_burst():
    """THE PRODUCTION SHAPE, AT PRODUCTION SIZE (R30A review: the first
    reproduction used six slots for six holders, which makes the timeout
    certain). Ten slots (db.get_pool max_size=10), six held for life by the
    single-writer loops, four left; a burst of SIX transient jobs each
    holding its connection 1 s (an evaluation, a servicing pass, ...) and a
    seventh with the reactive audit's 2 s acquire budget behind them: on the
    old path two of the burst and the audit wait for one of the four slots
    and the audit's 2 s run out; on lease sessions the burst and the audit
    all get a slot at once."""
    import asyncpg

    async def burst(pool, n, hold_s):
        async def job():
            async with pool.acquire(timeout=10.0) as c:
                await c.execute("SELECT pg_sleep($1)", hold_s)
        return [asyncio.create_task(job()) for _ in range(n)]

    async def audit(pool):
        t0 = asyncio.get_running_loop().time()
        try:
            async with pool.acquire(timeout=2.0) as c:
                await c.fetchval("SELECT 1")
            return asyncio.get_running_loop().time() - t0
        except asyncio.TimeoutError:
            return None

    async def main():
        pool = await asyncpg.create_pool(DSN, min_size=10, max_size=10)
        saved_pool, saved_dsn = DB._pool, DB._dsn
        DB._pool, DB._dsn = pool, (lambda: DSN)
        keys = [990_000_000_200 + i for i in range(6)]
        try:
            # OLD PATH: six holders keep pooled connections for life
            held = [await pool.acquire() for _ in keys]
            for c, k in zip(held, keys):
                assert await c.fetchval("SELECT pg_try_advisory_lock($1)", k)
            jobs = await burst(pool, 6, 3.0)
            await asyncio.sleep(0.2)           # the burst takes the 4 slots
            old = await audit(pool)
            await asyncio.gather(*jobs)
            for c, k in zip(held, keys):
                await c.execute("SELECT pg_advisory_unlock($1)", k)
                await pool.release(c)
            # FIXED PATH: the same six holders on lease sessions
            cms = [DB.lease_session(pool, name="burst%d" % i)
                   for i in range(6)]
            conns = [await cm.__aenter__() for cm in cms]
            try:
                for c, k in zip(conns, keys):
                    assert await c.fetchval(
                        "SELECT pg_try_advisory_lock($1)", k)
                jobs = await burst(pool, 6, 3.0)
                await asyncio.sleep(0.2)
                new = await audit(pool)
                await asyncio.gather(*jobs)
            finally:
                for cm in cms:
                    await cm.__aexit__(None, None, None)
            return old, new
        finally:
            DB._pool, DB._dsn = saved_pool, saved_dsn
            await pool.close()

    old, new = asyncio.run(main())
    assert old is None, "the audit got a slot behind six holders + a burst"
    assert new is not None and new < 1.0, new


@pg
def test_lease_sessions_leave_the_shared_pool_free_for_a_two_second_audit():
    """THE MECHANISM, MINIMAL. A pool of six with six lock holders: on the
    old path (`pool.acquire()` held for life) the next 2 s acquire -- the
    reactive audit's budget -- times out; on lease sessions it succeeds,
    and the six locks are still held, each on its own named backend. (The
    production-sized case, ten slots and a burst, is the test above.)"""
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
            if "pg_locks" in sql and "pg_backend_pid()" in sql:
                # the PARENT's fence (db.advisory_held, its own session)
                return fence_answers.pop(0) if fence_answers else True
            if "pg_locks" in sql:
                # a CHILD's fence (db.advisory_held_by the writer's pid)
                return True
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


# ── R30A review: re-contention can never fail out of ext_pinnacle.run ───

def _ext_fakes(monkeypatch, L, *, fence_answers, opens):
    """A fake lease whose N-th open is decided by `opens` (a list of
    'ok' / exception instances), and a decider whose fence answers come
    from `fence_answers` then True."""
    from sportsassets import venue_cooldown_store as VCS
    conns, cycles_on = [], []

    class _Conn:
        def __init__(self, n):
            self.n = n

        async def execute(self, sql, *args):
            return "OK"

        async def fetchval(self, sql, *args):
            if "pg_try_advisory_lock" in sql:
                return True
            if "pg_locks" in sql and "pg_backend_pid()" in sql:
                return fence_answers.pop(0) if fence_answers else True
            if "pg_locks" in sql:
                return True
            if "pg_backend_pid" in sql:
                return 2000 + self.n
            return None

    class _LS:
        def __init__(self, pool, *, name):
            pass

        async def __aenter__(self):
            what = opens.pop(0) if opens else "ok"
            if isinstance(what, BaseException):
                raise what
            c = _Conn(len(conns))
            conns.append(c)
            return c

        async def __aexit__(self, *exc):
            return False

    class _PCtx:
        async def __aenter__(self):
            return _Conn(-1)

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self, *a, **kw):
            return _PCtx()

    async def _cycle(conn, *a, **kw):
        cycles_on.append(conn.n)
        return {"ran": False}

    async def _service(conn, *, now, review_interval_s=None,
                       run_learning=True):
        return {"ok": True}

    async def _resume(conn):
        return {"resumed": False, "why": "TEST"}

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(L, "lease_session", _LS)
    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "cycle", _cycle)
    monkeypatch.setattr(L, "_funded_service", _service)
    monkeypatch.setattr(L, "_xavier_daily_review", _noop)
    monkeypatch.setattr(VCS, "load_and_resume", _resume)
    monkeypatch.setattr(VCS, "pending", lambda: None)
    monkeypatch.setattr(L, "IDLE_POLL_S", 0.01)
    monkeypatch.setattr(L, "CONTEND_BACKOFF_MAX_S", 0.04)
    monkeypatch.setattr(L, "SERVICING_INTERVAL_S", 0.01)
    monkeypatch.setattr(L, "SERVICING_MIN_GAP_S", 0.0)
    return _Pool(), conns, cycles_on


def test_ext_pinnacle_contends_again_when_the_database_is_still_down(
        monkeypatch):
    """THE REVIEWERS' REPRODUCTION, FIXED: the lock is held, the fence then
    says NOT HELD, the next TWO lease opens raise (the database restarting)
    -- run() must stay alive, back off, and take the lock on the third open,
    and the cycles must resume on that session."""
    from sportsassets.workers import ext_pinnacle_loop as L
    pool, conns, cycles_on = _ext_fakes(
        monkeypatch, L, fence_answers=[True, False],
        opens=["ok", ConnectionRefusedError("database restarting"),
               OSError("still restarting"), "ok"])

    async def main():
        async def _get():
            return pool
        runner = asyncio.create_task(L.run(_get))
        try:
            async def _resumed():
                while len(conns) < 2 or not [n for n in cycles_on
                                             if n == 1]:
                    assert not runner.done(), runner.exception()
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(_resumed(), 10)
            return runner.done()
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner

    done = asyncio.run(main())
    assert done is False, "run() must never exit on a failed re-contention"
    assert len(conns) == 2           # two failed opens took no session
    assert cycles_on[0] == 0 and 1 in cycles_on
    assert L._SERVICING["task_active"] is False


def test_ext_pinnacle_survives_a_dead_pool_at_boot(monkeypatch):
    """get_pool itself failing (the database down at boot) is one failed
    attempt, not the end of the decider."""
    from sportsassets.workers import ext_pinnacle_loop as L
    pool, conns, cycles_on = _ext_fakes(monkeypatch, L, fence_answers=[],
                                        opens=["ok"])
    calls = {"n": 0}

    async def _get():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConnectionRefusedError("no database yet")
        return pool

    async def main():
        runner = asyncio.create_task(L.run(_get))
        try:
            async def _ran():
                while not cycles_on:
                    assert not runner.done(), runner.exception()
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(_ran(), 10)
        finally:
            runner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner

    asyncio.run(main())
    assert cycles_on and calls["n"] >= 3


def test_the_contention_backoff_doubles_and_is_capped():
    from sportsassets.workers import ext_pinnacle_loop as L
    assert L.contend_delay(0) == L.IDLE_POLL_S
    assert L.contend_delay(1) == L.IDLE_POLL_S
    assert L.contend_delay(2) == 2 * L.IDLE_POLL_S
    assert L.contend_delay(3) == 4 * L.IDLE_POLL_S
    assert L.contend_delay(50) == L.CONTEND_BACKOFF_MAX_S


# ── R30A review: the reactive fence is bounded, and so is a whole job ───

def test_a_stalled_reactive_fence_is_refused_not_waited_on(monkeypatch):
    """THE REVIEWER'S REPRODUCTION, FIXED: a fence that never answers
    blocked the one reactive worker for ever, uncounted. Now FENCE_S
    bounds it: the job is refused (FENCED_OUT, FENCE_UNANSWERED), nothing
    is audited or evaluated, and the worker serves the next change."""
    from sportsassets import pinnapi_reactive as R
    monkeypatch.setattr(R, "FENCE_S", 0.05)

    async def main():
        conns, records, calls, done = [], [], [], asyncio.Event()
        s, cache, tick = _scheduler(_Session(conns), records, calls, done)

        async def fence(conn):
            await asyncio.sleep(3600)
        s.fence = fence
        task = asyncio.create_task(s.run())
        try:
            tick(cache)
            for _ in range(300):
                if s.counts["FENCE_UNANSWERED"]:
                    break
                await asyncio.sleep(0.01)
            alive = not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        return records, calls, s, alive
    records, calls, s, alive = asyncio.run(main())
    assert alive
    assert s.counts["FENCE_UNANSWERED"] == 1 and s.counts["FENCED_OUT"] == 1
    assert records == [] and calls == []


def test_the_child_fence_of_the_decider_is_bounded():
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L
    src = inspect.getsource(L._hold_once)
    body = src[src.index("async def _child_fence"):]
    body = body[:body.index("await _LH.record")]
    assert "asyncio.timeout(FENCE_TIMEOUT_S)" in body
    loop_src = inspect.getsource(L._servicing_loop)
    assert "asyncio.timeout(FENCE_TIMEOUT_S)" in loop_src


def test_the_worst_case_job_bound_is_every_budget_spent():
    """The report's total bound, corrected (R30A review): session wait 2 +
    fence 2 + STARTED audit 2 + deadline 12 + completion audit 2 + its retry
    on a fresh session (2 + 2) = 24 s -- not 18."""
    from sportsassets import pinnapi_reactive as R
    assert R.worst_case_job_s() == 24.0
    assert R.worst_case_job_s(deadline=12.0) == (
        R.SESSION_WAIT_S + R.FENCE_S + R.AUDIT_S + 12.0 + R.AUDIT_S
        + R.SESSION_WAIT_S + R.AUDIT_S)
