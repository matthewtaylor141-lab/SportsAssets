"""MIGRATION 196 (Kalshi small-live) AND ITS ROLLBACK, on Postgres.

Everything runs inside one transaction that is rolled back, so the test
database is left exactly as found whether or not 196 was already applied:

  * the migration is idempotent and the control row starts DISABLED;
  * the control cannot be enabled without a reconciliation, nor with one
    recorded for a DIFFERENT key fingerprint;
  * a reconciliation cannot be UNREADABLE-and-complete, or accept a
    baseline unless NOT_EMPTY; live fills come only from the venue fills
    endpoint;
  * the rollback refuses while any reconciliation / intent / fill exists,
    and drops cleanly when none does.
"""
from __future__ import annotations

import json
import pathlib
import re

import asyncpg
import pytest

from sportsassets import kalshi_account as KA

from tests import kalshi_fixtures as F
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "196_kalshi_smalllive.sql").read_text()
DOWN = (MIG / "rollback" / "196_kalshi_smalllive.down.sql").read_text()


async def _expect(conn, exc, sql, *args):
    """Run `sql` in a savepoint and require it to fail with `exc`."""
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


async def _recon(conn, fp, verdict="EMPTY", complete=True, accepted=False):
    return await conn.fetchval(
        """INSERT INTO kalshi_account_reconciliations (key_fingerprint, kalshi_env,
             verdict, complete, baseline_accepted) VALUES ($1,'prod',$2,$3,$4)
           RETURNING reconciliation_id""", fp, verdict, complete, accepted)


def test_the_files_exist_and_name_only_kalshi_tables():
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", UP)
    assert created and all(t.startswith("kalshi_") for t in created)
    assert not re.search(r"REFERENCES\s+paper", UP, re.I)
    assert not re.search(r"(INSERT INTO|UPDATE|ALTER TABLE)\s+paper", UP, re.I)
    for t in created:
        assert "DROP TABLE IF EXISTS %s;" % t in DOWN


@pg
async def test_migration_196_constraints_and_rollback():
    conn = await asyncpg.connect(H.DSN)
    outer = conn.transaction()
    await outer.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                                  # idempotent
        ctl = await conn.fetchrow("SELECT * FROM kalshi_smalllive_control WHERE id = 1")
        assert ctl["enabled"] is False and ctl["stopped"] is False
        assert ctl["scale"] == 1000 and ctl["max_order_usd"] == 25
        await conn.execute("DELETE FROM kalshi_live_fills")
        await conn.execute("DELETE FROM kalshi_live_intents")
        await conn.execute("UPDATE kalshi_smalllive_control SET enabled = false,"
                           " reconciliation_id = NULL, key_fingerprint = NULL")
        await conn.execute("DELETE FROM kalshi_account_reconciliations")

        # enabling needs a reconciliation of the SAME key
        await _expect(conn, asyncpg.CheckViolationError,
                      "UPDATE kalshi_smalllive_control SET enabled = true,"
                      " key_fingerprint = 'fpA', kalshi_env = 'prod'")
        rid_b = await _recon(conn, "fpB")
        await _expect(conn, asyncpg.ForeignKeyViolationError,
                      "UPDATE kalshi_smalllive_control SET enabled = true,"
                      " key_fingerprint = 'fpA', kalshi_env = 'prod',"
                      " reconciliation_id = $1", rid_b)
        snap = KA.snapshot(F.empty_account(), key_fingerprint="fpA", kalshi_env="prod")
        rec = KA.reconciliation_record(snap)
        rid_a = await conn.fetchval(
            """INSERT INTO kalshi_account_reconciliations (key_fingerprint, kalshi_env,
                 verdict, complete, positions, resting_orders, fills_recent,
                 settlements_recent, errors) VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,
                 $7,$8,$9::jsonb) RETURNING reconciliation_id""",
            rec["key_fingerprint"], rec["kalshi_env"], rec["verdict"], rec["complete"],
            json.dumps(rec["positions"]), json.dumps(rec["resting_orders"]),
            rec["fills_recent"], rec["settlements_recent"], json.dumps(rec["errors"]))
        await conn.execute("UPDATE kalshi_smalllive_control SET enabled = true,"
                           " key_fingerprint = 'fpA', kalshi_env = 'prod',"
                           " reconciliation_id = $1", rid_a)

        # reconciliation shape rules
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO kalshi_account_reconciliations (key_fingerprint,
                           kalshi_env, verdict, complete) VALUES ('x','prod',
                           'UNREADABLE', true)""")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO kalshi_account_reconciliations (key_fingerprint,
                           kalshi_env, verdict, complete, baseline_accepted)
                         VALUES ('x','prod','EMPTY', true, true)""")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO kalshi_account_reconciliations (key_fingerprint,
                           kalshi_env, verdict, complete, read_only)
                         VALUES ('x','prod','EMPTY', true, false)""")
        # intents: excluded <=> exclusion; price on the tick range
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO kalshi_live_intents (link_id, mirror_id,
                           paper_order_id, intent, state) VALUES ('kl:a','km:a',
                           'paperord:a','BUY_LONG','EXCLUDED')""")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO kalshi_live_intents (link_id, mirror_id,
                           paper_order_id, intent, state, kalshi_price) VALUES
                           ('kl:a','km:a','paperord:a','BUY_LONG','PLANNED', 1.00)""")
        await _expect(conn, asyncpg.CheckViolationError,
                      """INSERT INTO kalshi_live_fills (trade_id, venue_order_id,
                           ticker, side, action, count, price, source) VALUES
                           ('t','o','K','yes','buy',1,0.5,'ORDER_ACK')""")

        # the rollback refuses over records ...
        sp = conn.transaction()
        await sp.start()
        try:
            with pytest.raises(asyncpg.RaiseError) as e:
                await conn.execute(DOWN)
            assert "not rolled back" in str(e.value)
        finally:
            await sp.rollback()
        # ... and drops cleanly without them
        await conn.execute("UPDATE kalshi_smalllive_control SET enabled = false,"
                           " reconciliation_id = NULL")
        await conn.execute("DELETE FROM kalshi_account_reconciliations")
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('kalshi_smalllive_control')") is None
    finally:
        await outer.rollback()
        await conn.close()
