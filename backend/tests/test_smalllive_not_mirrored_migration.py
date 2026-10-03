"""MIGRATION 198 (small-live NOT_MIRRORED) AND ITS ROLLBACK, on Postgres.

Everything runs inside one transaction that is rolled back, so the test
database is left exactly as found:

  * after 198 the status check admits NOT_MIRRORED and still refuses an
    unknown status;
  * the rollback restores the three-value check WITHOUT rewriting a row
    already recorded as NOT_MIRRORED (NOT VALID), and refuses a new one;
  * 198 is idempotent.
"""
from __future__ import annotations

import pathlib

import asyncpg
import pytest

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "198_smalllive_not_mirrored.sql").read_text()
DOWN = (MIG / "rollback" / "198_smalllive_not_mirrored.down.sql").read_text()
INS = ("INSERT INTO smalllive_reconciliations (group_id, venue, status, discrepancies, chain)"
       " VALUES ($1, 'POLYMARKET', $2, '[]'::jsonb, '{}'::jsonb)")


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
@pytest.mark.asyncio
async def test_198_admits_not_mirrored_and_its_rollback_keeps_recorded_rows():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                         # idempotent
        await conn.execute(INS, "mig198-a", "NOT_MIRRORED")
        await _expect(conn, asyncpg.CheckViolationError, INS, "mig198-b", "SOMETHING_ELSE")
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT status FROM smalllive_reconciliations WHERE group_id = 'mig198-a'"
        ) == "NOT_MIRRORED"
        await _expect(conn, asyncpg.CheckViolationError, INS, "mig198-c", "NOT_MIRRORED")
        await conn.execute(INS, "mig198-d", "MATCHED")
    finally:
        await tx.rollback()
        await conn.close()
