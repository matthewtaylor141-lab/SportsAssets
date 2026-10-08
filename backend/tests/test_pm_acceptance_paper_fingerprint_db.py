"""THE HISTORICAL PAPER FINGERPRINT ON A REAL DATABASE.

.github/pm-acceptance/paper_history_fingerprint.sql is what pm-acceptance
runs, read only, against production (PM review of RC4, 2026-10-08:
historical_paper_immutable was typed True). Here it runs against the
migrated scratch database, in a READ ONLY transaction, over PAPER history
written by the production ledger and simulator (synthetic books, a
fictional test account):

  * appending after the fixed cutoff (new orders, fills, ledger rows, a new
    account) leaves every immutable row set identical, and the moving
    projections reconcile;
  * a rewrite of a row below the cutoff changes its table's digest;
  * a row stamped below the cutoff by the application but recorded after
    it is counted as a backdated insert;
  * a fresh cutoff is never a settled watermark.
"""
from __future__ import annotations

import asyncio
import json
import math
import pathlib
import time
from datetime import datetime, timezone

import asyncpg
import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.pm_bind import acceptance as PA

from tests import paper_harness as H

try:
    from tests import pm_acceptance_fixture as F
except ImportError:                                             # pragma: no cover
    import pm_acceptance_fixture as F  # type: ignore

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
SQL = (pathlib.Path(__file__).resolve().parents[2] / ".github" /
       "pm-acceptance" / "paper_history_fingerprint.sql")
#: the session the workflow's PGOPTIONS set
SETTINGS = {"TimeZone": "UTC", "DateStyle": "ISO, MDY",
            "IntervalStyle": "postgres", "extra_float_digits": "1"}


async def fingerprint(cutoff: str) -> dict:
    """The SQL as psql -v cutoff=... runs it, in a READ ONLY transaction."""
    sql = SQL.read_text().replace(":'cutoff'", "'%s'" % cutoff)
    conn = await asyncpg.connect(H.DSN, server_settings=SETTINGS)
    try:
        async with conn.transaction(readonly=True):
            raw = await conn.fetchval(sql)
    finally:
        await conn.close()
    return json.loads(raw)


async def _fill(conn, acct, *, key, at, qty=10, limit=0.5):
    slug = "%s:fp-%s" % (acct["account_id"], key)
    o = H.order(acct, key=key, qty=qty, limit=limit, slug=slug, at=at)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3.0, offers=[(limit, qty)])
    sim = await SIM.simulate_order(conn, got["order"]["order_id"],
                                   now=at + 4.0, fee_fn=H.zero_fee)
    return got["order"]["order_id"], sim


async def _cutoff_after_now(conn) -> str:
    """The next whole second on the DATABASE clock, waited past."""
    now = (await conn.fetchval("SELECT extract(epoch FROM clock_timestamp())"))
    c = math.floor(float(now)) + 1
    while float(await conn.fetchval(
            "SELECT extract(epoch FROM clock_timestamp())")) <= c + 0.05:
        await asyncio.sleep(0.1)
    return datetime.fromtimestamp(c, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _verify(pre, post, monkeypatch):
    """paper_history over two REAL receipts; the watermark bound is lifted
    here only because a test cannot wait ten minutes behind its cutoff (the
    unsettled verdict itself is asserted separately)."""
    monkeypatch.setattr(PA, "WATERMARK_SETTLE_S", 0.0)
    t_pre = PA._ts(pre["captured_at"])
    return PA.paper_history(pre, post, baseline=F.baseline_meta(pre),
                            deploy_started_at=t_pre + 1.0)


@pg
async def test_appending_after_the_cutoff_leaves_history_identical(
        monkeypatch):
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "fp")
        await _fill(conn, acct, key="before", at=H.T0)
        cutoff = await _cutoff_after_now(conn)
        pre = await fingerprint(cutoff)
        # history continues AFTER the cutoff: a new account, its funding,
        # an order, a fill and their ledger rows, all stamped now
        now = time.time()
        later = await H.new_account(conn, "fp2", now=now)
        await _fill(conn, later, key="after", at=now)
        post = await fingerprint(cutoff)
    finally:
        await conn.close()
    assert pre["version"] == PA.PAPER_FP_VERSION
    assert pre["cutoff"] == post["cutoff"] == cutoff
    assert pre["settings"]["TimeZone"] == "UTC"
    assert pre["tables"] == post["tables"]
    # the history below the cutoff is not empty: the account, its funding
    # and the order / fill / events written before it
    for t in ("paper_ledger", "paper_fills", "paper_order_events",
              "paper_accounts", "paper_orders"):
        assert pre["tables"][t]["rows"] >= 1, t
    assert pre["columns"] == post["columns"]
    assert set(pre["columns"]) == set(PA.IMMUTABLE_TABLES)
    # the moving parts moved, and are not history
    assert post["projections"]["paper_accounts"]["rows_since_cutoff"] == \
        pre["projections"]["paper_accounts"]["rows_since_cutoff"] + 1
    assert post["projections"]["paper_orders"]["rows_since_cutoff"] == \
        pre["projections"]["paper_orders"]["rows_since_cutoff"] + 1
    r = _verify(pre, post, monkeypatch)
    assert r["status"] == PA.PROVEN, r["reasons"]
    assert r["projections"]["paper_accounts"]["status"] == "RECONCILED"


@pg
async def test_a_fresh_cutoff_is_never_a_settled_watermark():
    conn = await H.connect()
    try:
        cutoff = await _cutoff_after_now(conn)
    finally:
        await conn.close()
    pre = await fingerprint(cutoff)
    post = await fingerprint(cutoff)
    assert pre["watermark"]["cutoff_lag_s"] < PA.WATERMARK_SETTLE_S
    r = PA.paper_history(pre, post, baseline=F.baseline_meta(pre),
                         deploy_started_at=PA._ts(pre["captured_at"]) + 1)
    assert r["status"] == PA.UNPROVEN
    assert PA.R_PAPER_BASELINE_NOT_SETTLED in r["reasons"]


@pg
async def test_a_rewritten_row_below_the_cutoff_changes_the_digest(
        monkeypatch):
    """The append-only triggers are bypassed HERE ONLY (a superuser
    session on the scratch database, the test's own fictional account) to
    prove the fingerprint sees what the guards are meant to prevent."""
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "fprw")
        oid, _ = await _fill(conn, acct, key="rw", at=H.T0)
        cutoff = await _cutoff_after_now(conn)
        pre = await fingerprint(cutoff)
        if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE "
                                   "rolname = current_user"):
            pytest.fail("the scratch database role must be able to bypass "
                        "the guards for this proof")
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            n = await conn.execute(
                "UPDATE paper_fills SET fee_usd = fee_usd + 0.01 "
                " WHERE order_id = $1", oid)
        assert n == "UPDATE 1"
        post = await fingerprint(cutoff)
    finally:
        await conn.close()
    assert pre["tables"]["paper_fills"]["digest"] != \
        post["tables"]["paper_fills"]["digest"]
    assert pre["tables"]["paper_fills"]["fees_usd"] != \
        post["tables"]["paper_fills"]["fees_usd"]
    r = _verify(pre, post, monkeypatch)
    assert r["status"] == PA.CHANGED and r["immutable"] is False
    assert "%s:paper_fills:digest" % PA.R_PAPER_HISTORY_CHANGED in \
        r["reasons"]


@pg
async def test_a_backdated_fill_is_counted_not_hidden(monkeypatch):
    """A fill whose filled_at (the simulation clock, the application's
    stamp) is below the cutoff but which the database recorded after it is
    outside the closed set AND counted as late: history added after the
    fact is CHANGED, never silently absorbed."""
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "fpbd")
        cutoff = await _cutoff_after_now(conn)
        pre = await fingerprint(cutoff)
        await _fill(conn, acct, key="backdated", at=H.T0)
        post = await fingerprint(cutoff)
    finally:
        await conn.close()
    assert post["tables"]["paper_fills"]["rows"] == \
        pre["tables"]["paper_fills"]["rows"]
    assert post["tables"]["paper_fills"]["late_recorded_rows"] == \
        pre["tables"]["paper_fills"]["late_recorded_rows"] + 1
    r = _verify(pre, post, monkeypatch)
    assert r["status"] == PA.CHANGED
    assert "%s:paper_fills" % PA.R_PAPER_BACKDATED_ROWS in r["reasons"]


@pg
async def test_the_fingerprint_cannot_write():
    """Under default_transaction_read_only the SQL runs; nothing in it is a
    write (the workflow also refuses the file on a mutating keyword)."""
    sql = SQL.read_text().replace(":'cutoff'", "'2026-10-08T02:00:00Z'")
    conn = await asyncpg.connect(H.DSN, server_settings=dict(
        SETTINGS, default_transaction_read_only="on"))
    try:
        out = json.loads(await conn.fetchval(sql))
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute("CREATE TABLE fp_probe (x int)")
    finally:
        await conn.close()
    assert out["cutoff"] == "2026-10-08T02:00:00Z"
