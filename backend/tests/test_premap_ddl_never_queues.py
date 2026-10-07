"""CAPITAL-CRITICAL: THE PREMAP SWEEP'S DDL NEVER QUEUES AHEAD OF READERS.

Production 2026-10-07 10:56Z-11:53Z: the sweep's `ALTER TABLE us_premap ADD
COLUMN IF NOT EXISTS intent` (ACCESS EXCLUSIVE even when the column exists)
waited on a long transaction's ACCESS SHARE; every later us_premap reader
queued behind it -- the paper pass timed out for 57 minutes, Xavier reviewed
nothing, the hourly protections expired unreplaced.

  * on a complete schema `_ensure_table` issues NO DDL (catalogue read only);
  * when DDL is needed and a reader holds the table, it gives up within the
    lock timeout (a logged skip) -- it never waits indefinitely, so readers
    are never queued behind it."""
from __future__ import annotations

import asyncio
import time

import asyncpg
import pytest

from sportsassets.workers import premap

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


class _Spy:
    def __init__(self, conn):
        self.conn, self.ddl = conn, []

    def __getattr__(self, k):
        return getattr(self.conn, k)

    async def execute(self, q, *a):
        self.ddl.append(q)
        return await self.conn.execute(q, *a)


@pg
async def test_a_complete_schema_takes_no_ddl_lock():
    conn = await asyncpg.connect(H.DSN)
    try:
        assert await premap._premap_schema_complete(conn) is True
        spy = _Spy(conn)
        await premap._ensure_table(spy)
        assert spy.ddl == []
    finally:
        await conn.close()


@pg
async def test_needed_ddl_behind_a_reader_is_skipped_within_the_timeout(
        monkeypatch):
    holder = await asyncpg.connect(H.DSN)
    conn = await asyncpg.connect(H.DSN)
    reader = await asyncpg.connect(H.DSN)
    tx = holder.transaction()
    await tx.start()
    try:
        await holder.execute("LOCK TABLE us_premap IN ACCESS SHARE MODE")

        async def incomplete(_c):
            return False
        monkeypatch.setattr(premap, "_premap_schema_complete", incomplete)
        t0 = time.monotonic()
        await asyncio.wait_for(premap._ensure_table(conn), 10)
        took = time.monotonic() - t0
        assert took < premap.PREMAP_DDL_LOCK_TIMEOUT_MS / 1000.0 + 3.0
        # nothing is left queued: a reader proceeds at once
        await asyncio.wait_for(reader.fetchval(
            "SELECT count(*) FROM us_premap"), 3)
    finally:
        await tx.rollback()
        for c in (holder, conn, reader):
            await c.close()


def test_this_proof_is_capital_critical():
    from pathlib import Path
    listed = (Path(__file__).resolve().parents[1] / "tools" /
              "capital_critical_tests.txt").read_text().splitlines()
    assert "tests/test_premap_ddl_never_queues.py" in listed
