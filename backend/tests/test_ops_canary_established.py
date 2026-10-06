"""THE PRODUCTION CANARY IS ESTABLISHED FROM REAL EVIDENCE (closeout).

Production (release 55eb7c82): no_order and cursors_never_regressed both read
NOT_ESTABLISHED. Causes, read from production:

  * live_parity_effective_cutover holds 0 rows: live_parity_cutover may be
    recorded only by a named human (lpc_named_human_ck) and never was, so
    "since the cutover" was undefined even though SMALL LIVE read SHADOW and
    the workers' venue-write lock read LOCKED;
  * the cursor check compared only the decision-only live lane's journal,
    and that lane is STOPPED by its owner control (bettor_live_observation =
    false) with one boot on record (2026-09-22): no restart to compare.

Now: with no human cutover the no-order window is the CURRENT RELEASE'S boot
(named, narrower, never fabricated); with the decision-only lane stopped by
its control the RUNNING lanes' durable cursors are compared across the
restart (catalogue receipts advance and never regress; the market-plane
registry does not shrink at the restart). PASS only on evidence; a live order
in the window or a regressed cursor FAILS; missing evidence stays
NOT_ESTABLISHED.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from sportsassets import ops_canary as OC

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
NOW = datetime.now(timezone.utc)
BOOT = NOW - timedelta(minutes=30)


async def _tx():
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    # this transaction's canary inputs, exactly
    await c.execute("DELETE FROM ingestion_state WHERE key IN "
                    "('workers_boot', 'bettor_live_observation')")
    await c.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ('workers_boot', $1)",
        json.dumps({"commit": "abc1234", "commit_sha": "a" * 40,
                    "at": BOOT.isoformat(), "venue_writes": "LOCKED"}))
    await c.execute(
        "INSERT INTO ingestion_state (key, value) VALUES "
        "('bettor_live_observation', 'false'::jsonb)")
    await c.execute("SET LOCAL session_replication_role = replica")
    await c.execute("DELETE FROM live_parity_cutover")
    await c.execute("DELETE FROM venue_catalogue_receipts")
    await c.execute("DELETE FROM market_plane_events")
    # the shared test database may carry other tests' live controls and
    # order rows: this transaction starts from inert controls and no orders
    for t in ("execmirror_control", "kalshi_smalllive_control"):
        if await c.fetchval("SELECT to_regclass($1)", t):
            await c.execute("UPDATE " + t + " SET enabled = false, "
                            "stopped = true")
    await c.execute("UPDATE small_live_control SET mode = 'SHADOW', "
                    "halted = false")
    for _n, t, _ts, _p in OC.LIVE_ORDER_SOURCES:
        if await c.fetchval("SELECT to_regclass($1)", t):
            await c.execute("DELETE FROM " + t)
    await c.execute("SET LOCAL session_replication_role = origin")
    return c, tr


async def _receipt(c, lane, started, finished):
    await c.execute(
        "INSERT INTO venue_catalogue_receipts (lane, started_at, finished_at,"
        " outcome, pages_read, requests, events_seen, events_kept, "
        " events_dropped, markets_seen, markets_kept, markets_dropped, "
        " sides_written, truncated, version, receipt) VALUES ($1, $2, $3, "
        " 'COMPLETE', 1, 1, 1, 1, 0, 1, 1, 0, 2, false, 'v', '{}'::jsonb)",
        lane, started, finished)


async def _snapshot(c, at, n):
    await c.execute(
        "INSERT INTO market_plane_events (event_key, kind, payload, at) "
        "VALUES ($1, 'SNAPSHOT', $2::jsonb, $3)",
        "snap:%s" % at.timestamp(), json.dumps({"universe": {"represented":
                                                             n}}), at)


def _run(fn):
    async def go():
        c, tr = await _tx()
        try:
            return await fn(c)
        finally:
            await tr.rollback()
            await c.close()
    return asyncio.run(go())


@pg
def test_no_order_passes_on_the_release_boot_window_with_no_human_cutover():
    async def fn(c):
        wb = await OC.workers_boot(c)
        out = await OC.no_order(c, wb)
        assert out["window_basis"].startswith("CURRENT_RELEASE_BOOT")
        assert out["state"] in (OC.PASS, OC.FAIL)
        assert not any("window is undefined" in u
                       for u in out["not_established"])
        return out
    out = _run(fn)
    # a clean test database: nothing placed in the window
    assert out["state"] == OC.PASS, out


@pg
def test_a_live_order_inside_the_release_window_fails():
    async def fn(c):
        # a venue order record observed after the release boot (inserted
        # with triggers off: the canary reads the table, whatever wrote it)
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute(
            "INSERT INTO small_live_order_events (execution_id, "
            " venue_order_id, state, source, venue_record, observed_at) "
            "VALUES ('x', 'v1', 'RESTING', 'VENUE_ORDER_RECORD', '{}'::jsonb,"
            " $1)", BOOT + timedelta(minutes=1))
        await c.execute("SET LOCAL session_replication_role = origin")
        wb = await OC.workers_boot(c)
        return await OC.no_order(c, wb)
    out = _run(fn)
    assert out["state"] == OC.FAIL, out
    assert out["live_orders"]["small_live_order_events"]["since_cutover"] == 1


@pg
def test_an_order_before_the_release_boot_is_outside_the_window():
    async def fn(c):
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute(
            "INSERT INTO small_live_order_events (execution_id, "
            " venue_order_id, state, source, venue_record, observed_at) "
            "VALUES ('x', 'v1', 'FILLED', 'VENUE_ORDER_RECORD', '{}'::jsonb,"
            " $1)", BOOT - timedelta(days=3))
        await c.execute("SET LOCAL session_replication_role = origin")
        wb = await OC.workers_boot(c)
        return await OC.no_order(c, wb)
    out = _run(fn)
    assert out["live_orders"]["small_live_order_events"]["all_time"] == 1
    assert out["live_orders"]["small_live_order_events"]["since_cutover"] == 0
    assert out["state"] == OC.PASS, out


@pg
def test_running_lane_cursors_pass_when_the_catalogue_advanced_after_boot():
    async def fn(c):
        await _receipt(c, "full", BOOT - timedelta(minutes=20),
                       BOOT - timedelta(minutes=18))
        await _receipt(c, "full", BOOT + timedelta(minutes=5),
                       BOOT + timedelta(minutes=8))
        wb = await OC.workers_boot(c)
        jb = await OC.journal_boots(c)
        return await OC.cursors(c, {"newest_first": []}, wboot=wb)
    out = _run(fn)
    assert out["state"] == OC.PASS, out
    assert out["lane_checked"] == "RUNNING_LANES"
    assert out["catalogue"]["refreshes_after_boot"] == 1
    assert out["registry"]["state"] == "NOT_APPLICABLE"


@pg
def test_a_catalogue_cursor_that_went_backwards_fails():
    async def fn(c):
        await _receipt(c, "full", BOOT + timedelta(minutes=5),
                       BOOT + timedelta(minutes=9))
        await _receipt(c, "full", BOOT + timedelta(minutes=6),
                       BOOT + timedelta(minutes=7))      # finished earlier
        wb = await OC.workers_boot(c)
        return await OC.cursors(c, {"newest_first": []}, wboot=wb)
    out = _run(fn)
    assert out["state"] == OC.FAIL and out["catalogue"]["regressions"] == 1


@pg
def test_no_refresh_since_the_boot_is_not_established():
    async def fn(c):
        await _receipt(c, "full", BOOT - timedelta(minutes=20),
                       BOOT - timedelta(minutes=18))
        wb = await OC.workers_boot(c)
        return await OC.cursors(c, {"newest_first": []}, wboot=wb)
    out = _run(fn)
    assert out["state"] == OC.UNKNOWN


@pg
def test_a_registry_that_shrank_at_the_restart_fails():
    async def fn(c):
        await _receipt(c, "full", BOOT + timedelta(minutes=5),
                       BOOT + timedelta(minutes=8))
        await _snapshot(c, BOOT - timedelta(minutes=2), 70000)
        await _snapshot(c, BOOT + timedelta(minutes=2), 30000)
        wb = await OC.workers_boot(c)
        return await OC.cursors(c, {"newest_first": []}, wboot=wb)
    out = _run(fn)
    assert out["state"] == OC.FAIL
    assert out["registry"]["active_before_boot"] == 70000


@pg
def test_a_registry_carried_through_the_restart_passes():
    async def fn(c):
        await _receipt(c, "full", BOOT + timedelta(minutes=5),
                       BOOT + timedelta(minutes=8))
        await _snapshot(c, BOOT - timedelta(minutes=2), 70000)
        await _snapshot(c, BOOT + timedelta(minutes=2), 69500)
        wb = await OC.workers_boot(c)
        return await OC.cursors(c, {"newest_first": []}, wboot=wb)
    out = _run(fn)
    assert out["state"] == OC.PASS and out["registry"]["state"] == OC.PASS


@pg
def test_an_observing_lane_with_one_boot_stays_not_established():
    async def fn(c):
        await c.execute("UPDATE ingestion_state SET value = 'true'::jsonb "
                        " WHERE key = 'bettor_live_observation'")
        wb = await OC.workers_boot(c)
        return await OC.cursors(c, {"newest_first": [{"boot_id": "x"}]},
                                wboot=wb)
    out = _run(fn)
    assert out["state"] == OC.UNKNOWN
