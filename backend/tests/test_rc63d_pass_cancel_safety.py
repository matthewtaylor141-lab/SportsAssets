"""CAPITAL-CRITICAL: THE PAPER PASS IS SAFE WHEN A CANCELLED STATEMENT'S
CANCEL IS SLOW (RC6.3d pass-cancel-safety).

An independent race review of the RC6.3c candidate (ada9270c) found two
PRE-EXISTING production hazards, by fault injection of asyncpg's cancel
timing. asyncpg (0.31.0) cancels a cut statement with a CancelRequest on a
SECOND connection, and every later statement on the connection first awaits
the server's acknowledgement of the cancel. When that acknowledgement -- or
the sending of the request -- is slower than a bound, two things went wrong:

  H1  paper_runtime._end_open_transaction wrapped the post-cut ROLLBACK in
      asyncio.timeout(CONNECTION_RESET_TIMEOUT_S). An acknowledgement slower
      than the bound had the bound cancel asyncpg's acknowledgement future
      itself, so from then until the server answered EVERY statement on the
      connection raised CancelledError (not an asyncpg error): S.record_pass,
      the finally ROLLBACK / pg_advisory_unlock and the pool release all
      raised and run_once PROPAGATED CancelledError -- no paper_session_health
      row, no heartbeat, the scheduled task ended cancelled, silently.

  H2  paper_derek._terminate called Connection.terminate() without waiting
      for the cancelled statement's CancelRequest to have been SENT.
      terminate() cancels the task that opens the cancel connection and
      writes the request; sent too late, the request was lost and the backend
      ran the cut statement to its end -- holding the pass's session-level
      advisory lock (client_connection_check_interval 0: a backend writing
      nothing never notices its client is gone). The record failed, no
      heartbeat, and every following pass refused ADVISORY_LOCK_HELD until the
      statement ended (up to an hour for a hung read).

Proved here on a real Postgres through run_once, with asyncpg's cancel delayed
by monkeypatching connect_utils._cancel:

  (a) H1  cancel acknowledgement delayed past a scaled CONNECTION_RESET_
          TIMEOUT_S. BASE: run_once raises CancelledError, health passes 0,
          no heartbeat. HEAD: run_once RETURNS; the connection is terminated
          deliberately (PAPER_PASS_CONNECTION_RESET_TIMED_OUT), its backend is
          ENDED from a fresh connection, and the pass is recorded there
          (PAPER_PASS_RECORDED_ON_A_FRESH_CONNECTION; passes 1, heartbeat
          written, the lock free, the next pass runs: passes 2).
  (b) H2  an owed-ENTER sequence hung in a transaction, the CancelRequest
          delayed past the scaled terminate wait. BASE: the statement is still
          active after run_once returns, the orphan holds the advisory lock,
          and the next pass refuses ADVISORY_LOCK_HELD. HEAD: paper_derek
          records the request was lost; run_once ENDS the orphan backend from
          a fresh connection, the lock is free, the pass records, the owed
          ENTER is abandoned exactly as today, and the next pass runs.
  (c)     the NORMAL path (no delay) is unchanged: a cut records on the same
          connection, no reset timeout, same codes, the lock free.
  (d)     the caller cancelling run_once (the scheduler shutting down) still
          propagates CancelledError -- a real cancel is never swallowed.

SMALL_LIVE stays SHADOW; SYNTHETIC steps and statements on a scratch test
database; no venue, no order, no production write.
"""
from __future__ import annotations

import asyncio
import json
import time

import asyncpg
import pytest
from asyncpg import connect_utils as CU

from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PRT

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: a guard so a candidate that cannot bound a pass FAILS instead of hanging
GUARD_S = 60.0

RESET_TIMED_OUT = "PAPER_PASS_CONNECTION_RESET_TIMED_OUT"
SKIPPED_CLOSED = "PAPER_STEP_SKIPPED_PASS_CONNECTION_CLOSED"
RECORDED_FRESH = "PAPER_PASS_RECORDED_ON_A_FRESH_CONNECTION"


async def _ok(conn, ctx):
    return {"n": 1}


async def _pool_and_getter():
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)

    async def get_pool():
        return pool
    return pool, get_pool


async def _heartbeat(conn) -> dict | None:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            PRT.HEARTBEAT_KEY)
    if v is None:
        return None
    return json.loads(v) if isinstance(v, str) else dict(v)


async def _lock_is_free() -> bool:
    other = await asyncpg.connect(H.DSN)
    try:
        got = await other.fetchval("SELECT pg_try_advisory_lock($1)",
                                   PRT.ADVISORY_LOCK_KEY)
        if got:
            await other.execute("SELECT pg_advisory_unlock($1)",
                                PRT.ADVISORY_LOCK_KEY)
        return bool(got)
    finally:
        await other.close()


async def _active_in_this_db(conn, tag: str) -> int:
    """How many backends in THIS database are still running the tagged
    statement (the orphan probe, scoped -- a statement in another database is
    never this pass's)."""
    return await conn.fetchval(
        "SELECT count(*) FROM pg_stat_activity WHERE state='active' "
        "AND query LIKE '%' || $1 || '%' AND pid <> pg_backend_pid() "
        "AND datname = current_database()", tag)


async def _kill_tag(conn, tag: str) -> None:
    """Teardown: end any backend of this test still running the tagged
    statement (so a base run -- where the orphan is the bug -- cannot poison
    the shared database for other tests). On the head there is nothing to
    end."""
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
            "query LIKE '%' || $1 || '%' AND pid <> pg_backend_pid() "
            "AND datname = current_database()", tag)
    except Exception:                                          # noqa: BLE001
        pass


def _delay_cancel(monkeypatch, delay_s: float) -> None:
    """Fault injection: asyncpg sends a cancelled statement's CancelRequest
    on a second connection via connect_utils._cancel; delay that by
    `delay_s` seconds, so the request (and the server's acknowledgement of
    the cancel) arrive late -- exactly the race the review found."""
    orig = CU._cancel

    async def slow_cancel(*a, **kw):
        await asyncio.sleep(delay_s)
        return await orig(*a, **kw)
    monkeypatch.setattr(CU, "_cancel", slow_cancel)


async def _run_once(get_pool, a, steps, *, trigger="TEST_RC63D"):
    return await asyncio.wait_for(
        PRT.run_once(get_pool, trigger=trigger, force=True,
                     account_id=a["account_id"], config=a["config"],
                     fee_fn=H.zero_fee, steps=steps), GUARD_S)


# ═════════════════════════════════════════════════════════════════════
# (a) H1 · A SLOW CANCEL ACKNOWLEDGEMENT NEVER ENDS THE PASS UNRECORDED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_h1_a_slow_cancel_ack_still_records_the_pass_on_a_fresh_connection(
        monkeypatch):
    """FAILS ON THE BASE: the post-cut ROLLBACK's bound cancels asyncpg's
    cancel-acknowledgement future; run_once propagates CancelledError and the
    pass ends unrecorded (no paper_session_health row, no heartbeat)."""
    # steps get ~4 s of a 20 s pass; the reset bound (1 s) finishes well
    # before the hard timeout, so the reset-timeout path has room to run
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 20.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 16.0, raising=False)
    monkeypatch.setattr(PRT, "CONNECTION_RESET_TIMEOUT_S", 1.0, raising=False)
    _delay_cancel(monkeypatch, 3.0)                 # > the 1 s reset bound
    tag = "rc63d-h1-%d" % int(time.time() * 1000)
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "pc_h1", now=H.T0)

        async def cut_in_tx(c, ctx):
            await c.execute("BEGIN")
            await c.execute("SELECT pg_sleep(3600) /* %s */" % tag)

        raised = None
        res = None
        try:
            res = await _run_once(get_pool, a,
                                  [("first", _ok), ("cut", cut_in_tx),
                                   ("later", _ok)])
        except asyncio.CancelledError:
            raised = "CancelledError"

        # HEAD: run_once returned, named what happened, recorded on a fresh
        # connection. BASE: it propagated CancelledError (raised set).
        assert raised is None, (
            "run_once propagated a CancelledError that the caller did not "
            "request: the pass ended unrecorded (H1 on the base)")
        assert res["ran"] is True, res
        assert res.get("refusal") is None
        assert res["exceeded_step"] == "cut"
        assert RESET_TIMED_OUT in (res["errors"].get("CONNECTION_RESET") or "")
        assert res["skipped_steps"].get("later") == SKIPPED_CLOSED
        assert RECORDED_FRESH in (res["errors"].get("PASS_CONNECTION") or "")
        pc = res["pass_connection"]
        assert pc["closed"] is True
        assert RESET_TIMED_OUT in pc["closed_by"]
        assert pc["reap"]["outcome"] in ("BACKEND_TERMINATED",
                                         "BACKEND_ALREADY_GONE")
        assert pc["recorded"] is True and pc["heartbeat"] is True

        # paper_session_health + heartbeat written, on the fresh connection
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1
        hb = await _heartbeat(conn)
        assert hb is not None and hb["ran"] is True

        # no orphan, the lock free, the next pass runs
        assert await _active_in_this_db(conn, tag) == 0
        assert await _lock_is_free()
        res2 = await _run_once(get_pool, a, [("later", _ok)],
                               trigger="TEST_RC63D_NEXT")
        assert res2["ran"] is True and res2.get("refusal") is None
        assert (await S.health(conn, a["session_id"]))["passes"] == 2
    finally:
        await _kill_tag(conn, tag)
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# (b) H2 · A LOST CANCEL REQUEST NEVER LEAVES AN ORPHAN HOLDING THE LOCK
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_h2_a_terminated_pass_connection_backend_is_ended_so_the_lock_frees(
        monkeypatch):
    """FAILS ON THE BASE: paper_derek terminates the pass connection for an
    owed sequence that outlived its bounds without waiting for the cancel to
    be sent; the request is lost, the backend keeps the cut statement (and
    the advisory lock) and the next pass refuses ADVISORY_LOCK_HELD."""
    # a 14 s pass, 2 s kept for the record; Derek's owed-ENTER bounds scaled
    # so owed_enter_overrun_bound_s() = 6 + 3x0.5 + 2x0.25 = 8 s -> the
    # ENTER-owing step is cut at 12 - 8 = 4 s
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 14.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 2.0, raising=False)
    monkeypatch.setattr(PRT, "CONNECTION_RESET_TIMEOUT_S", 2.0, raising=False)
    monkeypatch.setattr(PD, "ENTER_ORDER_GRACE_S", 6.0)
    monkeypatch.setattr(PD, "ENTER_ABANDON_WAIT_S", 0.5)
    monkeypatch.setattr(PD, "ENTER_TERMINATED_WAIT_S", 0.25)
    assert PD.owed_enter_overrun_bound_s() == 8.0
    _delay_cancel(monkeypatch, 3.0)             # > the 0.25 s terminate wait
    tag = "rc63d-h2-%d" % int(time.time() * 1000)
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "pc_h2", now=H.T0)
        pass_conn = [None]

        async def hang(progress):
            progress["stage"] = "HUNG_IN_A_TRANSACTION"
            async with pass_conn[0].transaction():
                await pass_conn[0].execute(
                    "SELECT pg_sleep(3600) /* %s */" % tag)

        async def derek(c, ctx):
            pass_conn[0] = c
            return await PD.owed_order(c, ctx, decision_id="rc63d-h2-d1",
                                       strategy="PC_TEST", sequence=hang)

        before = dict(PD.OWED_ORDER_COUNTS)
        res = await _run_once(get_pool, a, [("first", _ok), ("derek", derek),
                                            ("later", _ok)])
        assert res["ran"] is True, res
        assert res["exceeded_step"] == "derek"

        # THE BEHAVIOURAL GUARANTEE (fails on the base): the orphan backend is
        # gone, the pass's advisory lock is free, the pass recorded, and the
        # NEXT pass runs. On the base the orphan holds the lock and the next
        # pass refuses ANOTHER_PAPER_PASS_IS_RUNNING / ADVISORY_LOCK_HELD.
        assert await _active_in_this_db(conn, tag) == 0, (
            "the cut statement is still running on an orphan backend (H2 on "
            "the base: the cancel request was lost at the terminate)")
        assert await _lock_is_free(), (
            "the pass advisory lock is still held by the orphan backend (H2)")
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1
        hb = await _heartbeat(conn)
        assert hb is not None and hb["ran"] is True
        res2 = await _run_once(get_pool, a, [("later", _ok)],
                               trigger="TEST_RC63D_NEXT")
        assert res2["ran"] is True, res2
        assert res2.get("refusal") is None
        assert (await S.health(conn, a["session_id"]))["passes"] == 2

        # and the record names what happened: paper_derek recorded that the
        # cancel request was lost, run_once ended the backend from a fresh
        # connection, and the owed ENTER was abandoned exactly as today
        d = {k: PD.OWED_ORDER_COUNTS[k] - before.get(k, 0)
             for k in PD.OWED_ORDER_COUNTS}
        assert d["connections_terminated"] == 1
        assert d.get("cancel_requests_lost_at_terminate") == 1
        assert d["abandoned"] == 1
        pc = res["pass_connection"]
        assert pc["closed"] is True and "paper_derek" in pc["closed_by"]
        assert pc["reap"]["outcome"] in ("BACKEND_TERMINATED",
                                         "BACKEND_ALREADY_GONE")
    finally:
        await _kill_tag(conn, tag)
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# (c) THE NORMAL PATH (NO DELAY) IS UNCHANGED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_c_the_normal_cut_path_with_a_fast_ack_is_unchanged(monkeypatch):
    """No injected delay: a cut step's statement is cancelled and the server
    acknowledges at once, so the ROLLBACK returns on the SAME connection --
    no reset timeout, no terminate, no fresh connection -- exactly as before
    this lane. Passes on the base and the head."""
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 20.0)
    monkeypatch.setattr(PRT, "PASS_RECORD_RESERVE_S", 16.0, raising=False)
    monkeypatch.setattr(PRT, "CONNECTION_RESET_TIMEOUT_S", 5.0, raising=False)
    tag = "rc63d-c-%d" % int(time.time() * 1000)
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "pc_c", now=H.T0)

        async def cut_in_tx(c, ctx):
            await c.execute("BEGIN")
            await c.execute("SELECT pg_sleep(3600) /* %s */" % tag)

        t0 = time.time()
        res = await _run_once(get_pool, a, [("first", _ok),
                                            ("cut", cut_in_tx),
                                            ("later", _ok)])
        took = time.time() - t0
        assert res["ran"] is True and res.get("refusal") is None
        assert res["exceeded_step"] == "cut"
        assert res["errors"]["cut"].startswith(PRT.R_STEP_EXCEEDED)
        # the fast ack: the connection was NOT terminated, nothing recorded
        # on a fresh one, and the later step ran on the same connection
        assert "CONNECTION_RESET" not in res["errors"], res["errors"]
        assert "PASS_CONNECTION" not in res["errors"], res["errors"]
        assert res.get("pass_connection") is None
        # the later step is skipped because the hung cut step spent the pass
        # time (as before this lane) -- NOT because the connection was closed:
        # the fast ack left the connection usable, so no terminate happened
        assert res["skipped_steps"].get("later") == PRT.R_STEP_SKIPPED
        assert SKIPPED_CLOSED not in res["skipped_steps"].values()
        assert "HEALTH" not in res["errors"], res["errors"]
        # timing within tolerance of the base: cut at the ~4 s steps bound,
        # the record on the same connection, well inside the hard timeout
        assert took < 12.0, took
        assert await _active_in_this_db(conn, tag) == 0
        h = await S.health(conn, a["session_id"])
        assert h is not None and h["passes"] == 1
        assert await _lock_is_free()
    finally:
        await _kill_tag(conn, tag)
        await pool.close()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# (d) A REAL CALLER CANCELLATION IS NEVER SWALLOWED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_d_the_caller_cancelling_run_once_still_propagates(monkeypatch):
    """The scheduler shutting down cancels run_once: CancelledError must
    propagate (the fix names and swallows only a CancelledError the caller
    did NOT request -- asyncpg's cancel-acknowledgement wait). Passes on the
    base and the head; it pins that the fix did not start swallowing real
    cancels."""
    monkeypatch.setattr(PRT, "HARD_TIMEOUT_S", 60.0)
    tag = "rc63d-d-%d" % int(time.time() * 1000)
    conn = await H.connect()
    pool, get_pool = await _pool_and_getter()
    try:
        a = await H.new_account(conn, "pc_d", now=H.T0)

        async def long_step(c, ctx):
            await c.execute("SELECT pg_sleep(3600) /* %s */" % tag)

        task = asyncio.ensure_future(PRT.run_once(
            get_pool, trigger="TEST_RC63D_CANCEL", force=True,
            account_id=a["account_id"], config=a["config"],
            fee_fn=H.zero_fee, steps=[("first", _ok), ("long", long_step)]))
        await asyncio.sleep(1.0)                      # the step is in flight
        task.cancel()                                 # the caller's shutdown
        with pytest.raises(asyncio.CancelledError):
            await task
        # the lock is released and no orphan is left (the pass connection's
        # cleanup runs on the caller's cancellation)
        await asyncio.sleep(0.5)
        assert await _active_in_this_db(conn, tag) == 0
        assert await _lock_is_free()
    finally:
        await _kill_tag(conn, tag)
        await pool.close()
        await conn.close()
