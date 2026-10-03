"""MIGRATION 199 (execution intents) AND ITS ROLLBACK, on Postgres.

Inside one transaction that is rolled back:
  * 199 is idempotent; one intent per decision (decision_id UNIQUE);
  * at most one actual submission per intent (the partial UNIQUE index);
  * a refused / paper-only intent must name its refusal, a dispatched one
    must not;
  * the rollback refuses while any intent exists (the audit chain is never
    dropped as cleanup) and drops cleanly when none does.
"""
from __future__ import annotations

import pathlib

import asyncpg
import pytest

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "199_execution_intents.sql").read_text()
DOWN = (MIG / "rollback" / "199_execution_intents.down.sql").read_text()

INS = ("INSERT INTO execution_intents (intent_id, decision_id, strategy, us_market_slug,"
       " order_intent, group_id, order_type, time_in_force, paper_target_qty, wire_price,"
       " decided_at, live_eligible, actual_state, actual_refusal)"
       " VALUES ($1,$2,'S','slug','ORDER_INTENT_BUY_LONG','g','MARKETABLE','IOC',100,0.5,"
       " now(),$3,$4,$5)")


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


async def _clear(conn):
    """Inside the rolled-back transaction only: the intent-lane rows and what
    references them (fills, events), so the constraints are tested alone."""
    ids = "SELECT mirror_id FROM execmirror_orders WHERE execution_intent_id IS NOT NULL"
    await conn.execute("DELETE FROM execmirror_fills WHERE mirror_id IN (%s)" % ids)
    await conn.execute("DELETE FROM execmirror_events WHERE mirror_id IN (%s)" % ids)
    await conn.execute("DELETE FROM execmirror_orders WHERE execution_intent_id IS NOT NULL")
    await conn.execute("DELETE FROM execution_intents")


@pg
@pytest.mark.asyncio
async def test_199_one_intent_per_decision_one_submission_per_intent_and_a_guarded_rollback():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                                   # idempotent
        await _clear(conn)
        await conn.execute(INS, "i1", "d1", True, "DISPATCHED", None)
        await _expect(conn, asyncpg.UniqueViolationError, INS, "i2", "d1", True, "DISPATCHED", None)
        await _expect(conn, asyncpg.CheckViolationError, INS, "i3", "d3", False, "PAPER_ONLY", None)
        await _expect(conn, asyncpg.CheckViolationError, INS, "i4", "d4", True, "DISPATCHED", "X")
        mo = ("INSERT INTO execmirror_orders (mirror_id, execution_intent_id, role,"
              " us_market_slug, intent, order_type, tif, state)"
              " VALUES ($1,'i1','ENTRY','slug','ORDER_INTENT_BUY_LONG','MARKETABLE','IOC','SUBMITTING')")
        await conn.execute(mo, "ei:i1")
        await _expect(conn, asyncpg.UniqueViolationError, mo, "ei:i1-again")
        await _expect(conn, asyncpg.RaiseError, DOWN)            # intents exist
        await _clear(conn)
        await conn.execute(DOWN)
        assert await conn.fetchval("SELECT to_regclass('execution_intents')") is None
    finally:
        await tx.rollback()
        await conn.close()
