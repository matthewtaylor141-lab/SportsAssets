"""PAPER_SIM_V1: THE FILL SIMULATOR'S RULES (simulated, not verified execution).

  * a marketable order walks the observed depth of the side its intent
    consumes, best level first, and never beyond its price limit;
  * delay: it fills only on a book observed at or after decision + delay; a
    book observed before that is not used; none before expiry -> EXPIRED,
    no fill, reservation released;
  * FOK fills all or nothing; IOC releases its remainder;
  * two paper orders cannot consume the same observed liquidity;
  * a resting order never fills on a touch, fills only on a strict cross
    after its queue ahead, and a missing update (no new book, an unreadable
    book) is never a fill;
  * every acknowledgement and fill is labelled SIMULATOR with the version;
  * the optimistic sensitivity is separate and writes nothing.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def SL(a, n):
    return "%s:%s" % (a["account_id"], n)


# ── pure ────────────────────────────────────────────────────────────
def test_the_walk_respects_the_limit_and_the_side():
    md = H.md(bids=[(0.47, 100), (0.45, 100)],
              offers=[(0.50, 100), (0.52, 100), (0.55, 100)])
    buy = SIM.levels_for(md, direction="BUY", holding_side="LONG")
    assert [x["price"] for x in buy["levels"]] == [0.50, 0.52, 0.55]
    got = SIM.walk(buy["levels"], consumed={}, limit=0.52, qty=500,
                   direction="BUY", allow_partial=True)
    assert got["filled"] == 200.0 and not got["complete"]
    assert [t["price"] for t in got["takes"]] == [0.50, 0.52]
    fok = SIM.walk(buy["levels"], consumed={}, limit=0.52, qty=500,
                   direction="BUY", allow_partial=False)
    assert fok["filled"] == 0.0 and fok["refusal"] == SIM.R_FOK_SHORT
    # BUY_SHORT hits the bids at cost 1 - bid
    short = SIM.levels_for(md, direction="BUY", holding_side="SHORT")
    assert [x["price"] for x in short["levels"]] == [0.53, 0.55]
    assert short["side"] == "bids"
    # SELL of a long hits the bids, best (highest) first
    sell = SIM.levels_for(md, direction="SELL", holding_side="LONG")
    assert [x["price"] for x in sell["levels"]] == [0.47, 0.45]
    got = SIM.walk(sell["levels"], consumed={}, limit=0.46, qty=500,
                   direction="SELL", allow_partial=True)
    assert got["filled"] == 100.0
    # consumed quantity at a level is not available again
    got = SIM.walk(buy["levels"], consumed={SIM._wk(0.50): 100.0},
                   limit=0.50, qty=50, direction="BUY", allow_partial=True)
    assert got["filled"] == 0.0
    # levels off the adapter's cent grid are excluded, never rounded
    half = SIM.levels_for({"offers": [{"px": "0.505", "qty": "100"}],
                           "bids": []}, direction="BUY", holding_side="LONG")
    assert half["levels"] == [] and half["excluded_off_cent_grid"] == 1


def test_a_touch_is_not_a_fill_and_the_queue_goes_first():
    lv = SIM.levels_for(H.md(offers=[(0.40, 500)]), direction="BUY",
                        holding_side="LONG")["levels"]
    touch = SIM.resting_cross(lv, consumed={}, limit=0.40, direction="BUY",
                              queue_ahead=0.0, remaining=100)
    assert touch["filled"] == 0.0 and touch["refusal"] == SIM.R_TOUCH_ONLY
    lv = SIM.levels_for(H.md(offers=[(0.39, 150)]), direction="BUY",
                        holding_side="LONG")["levels"]
    q = SIM.resting_cross(lv, consumed={}, limit=0.40, direction="BUY",
                          queue_ahead=200.0, remaining=100)
    assert q["filled"] == 0.0 and q["refusal"] == SIM.R_QUEUE_AHEAD
    assert q["queue_ahead_after"] == 50.0
    q2 = SIM.resting_cross(lv, consumed={}, limit=0.40, direction="BUY",
                           queue_ahead=50.0, remaining=100)
    assert q2["filled"] == 100.0 and q2["queue_ahead_after"] == 0.0


def test_the_optimistic_sensitivity_is_a_bound_not_a_fill():
    got = SIM.optimistic_fill(H.md(offers=[(0.40, 100), (0.41, 100)]),
                              direction="BUY", holding_side="LONG",
                              qty=150, limit=0.41)
    assert got["basis"] == "OPTIMISTIC_SENSITIVITY_NOT_A_FILL"
    assert got["filled_qty"] == 150.0


# ── against the database ────────────────────────────────────────────
@pg
async def test_delay_semantics_a_book_before_the_delay_is_not_used_and_none_expires():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "delay")
        s = SL(a, "m")
        o = H.order(a, key="d1", qty=100, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=30.0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        await H.observe(conn, s, H.T0 + 1.0, offers=[(0.50, 100)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 1.5,
                                     fee_fn=H.zero_fee)
        assert r["pending"] and r["refusal"] == SIM.R_NO_BOOK_YET
        # a missing update is not a fill: still nothing at T0 + 20
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 20,
                                     fee_fn=H.zero_fee)
        assert r["pending"]
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 31,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "EXPIRED" and r["refusal"] == \
            SIM.R_NO_BOOK_IN_WINDOW
        b = await L.balances(conn, a["account_id"], now=H.T0 + 32)
        assert b["cash_usd"] == 500000.0 and b["reserved_usd"] == 0.0
        # next book at/after the delay is the one used
        o2 = H.order(a, key="d2", qty=100, limit=0.50, slug=s, at=H.T0 + 40)
        g2 = await L.submit_order(conn, o2, fee_fn=H.zero_fee, now=H.T0 + 40)
        await H.observe(conn, s, H.T0 + 41.0, offers=[(0.45, 100)])
        await H.observe(conn, s, H.T0 + 42.5, offers=[(0.50, 100)])
        await H.observe(conn, s, H.T0 + 43.0, offers=[(0.30, 100)])
        r = await SIM.simulate_order(conn, g2["order"]["order_id"],
                                     now=H.T0 + 44, fee_fn=H.zero_fee)
        assert r["state"] == "FILLED"
        f = await conn.fetchrow("SELECT * FROM paper_fills WHERE order_id=$1",
                                g2["order"]["order_id"])
        assert float(f["price"]) == 0.50
        assert L._epoch(f["book_observed_at"]) == pytest.approx(H.T0 + 42.5)
        assert f["event_source"] == "SIMULATOR"
        assert f["simulator_version"] == SIM.VERSION
        ev = await conn.fetch("SELECT kind, event_source, simulator_version "
                              "FROM paper_order_events WHERE order_id=$1",
                              g2["order"]["order_id"])
        assert {e["event_source"] for e in ev} == {"SIMULATOR"}
        assert {e["simulator_version"] for e in ev} == {SIM.VERSION}
    finally:
        await conn.close()


@pg
async def test_two_orders_cannot_consume_the_same_observed_liquidity():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "consume")
        s = SL(a, "m")
        ids = []
        for k in ("c1", "c2"):
            o = H.order(a, key=k, qty=300, limit=0.50, slug=s, at=H.T0)
            ids.append((await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                             now=H.T0))["order"]["order_id"])
        await H.observe(conn, s, H.T0 + 3, offers=[(0.49, 200), (0.50, 200)])
        r1 = await SIM.simulate_order(conn, ids[0], now=H.T0 + 4,
                                      fee_fn=H.zero_fee)
        r2 = await SIM.simulate_order(conn, ids[1], now=H.T0 + 4,
                                      fee_fn=H.zero_fee)
        assert r1["filled_qty"] == 300.0
        assert r2["filled_qty"] == 100.0          # only what was left
        tot = await conn.fetchval(
            "SELECT sum(qty) FROM paper_fills WHERE order_id = ANY($1)", ids)
        assert float(tot) == 400.0                # the displayed depth
        cons = await conn.fetch(
            "SELECT wire_price, displayed_qty, consumed_qty FROM "
            " paper_liquidity_consumed WHERE us_market_slug=$1 ORDER BY 1", s)
        assert [(float(c["wire_price"]), float(c["consumed_qty"]))
                for c in cons] == [(0.49, 200.0), (0.50, 200.0)]
    finally:
        await conn.close()


@pg
async def test_a_resting_order_needs_a_new_crossing_book_and_an_unreadable_one_is_nothing():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "rest")
        s = SL(a, "m")
        obs0 = await H.observe(conn, s, H.T0, bids=[(0.40, 300)],
                               offers=[(0.45, 300)])
        q = await SIM.queue_ahead_at_placement(
            conn, slug=s, direction="BUY", holding_side="LONG", limit=0.40,
            market_data=H.md(bids=[(0.40, 300)], offers=[(0.45, 300)]),
            account_id=a["account_id"])
        assert q["queue_ahead_qty"] == 300.0
        o = H.order(a, key="r1", qty=100, limit=0.40, slug=s, at=H.T0,
                    order_type="RESTING", tif="GTD", delay=0.0, ttl=3600,
                    queue_ahead=q["queue_ahead_qty"],
                    queue_basis=dict(q, placement_obs_id=obs0))
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 5,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "RESTING" and r["books_examined"] == 0
        # an unreadable book is evidence of nothing
        await SIM.record_book(conn, slug=s, read={"marketData": None,
                                                  "error": "Timeout",
                                                  "observed_at": H.T0 + 6},
                              source="TEST", read_basis="TEST")
        # a touch: an offer AT our bid
        await H.observe(conn, s, H.T0 + 7, bids=[(0.40, 300)],
                        offers=[(0.40, 1000)])
        # a cross smaller than the queue ahead
        await H.observe(conn, s, H.T0 + 8, offers=[(0.39, 250)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 9,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "RESTING" and not r["fills"]
        reasons = [x["reason"] for x in r["no_fill_reasons"]]
        assert reasons == [SIM.R_BOOK_UNREADABLE, SIM.R_TOUCH_ONLY,
                           SIM.R_QUEUE_AHEAD]
        assert r["queue_ahead_remaining"] == 50.0
        # a cross beyond the rest of the queue fills us, at our limit
        await H.observe(conn, s, H.T0 + 10, offers=[(0.38, 1000)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 11,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "FILLED", r
        f = await conn.fetchrow("SELECT * FROM paper_fills WHERE order_id=$1",
                                oid)
        assert float(f["price"]) == 0.40 and f["basis"] == \
            SIM.BASIS_CROSS
    finally:
        await conn.close()


@pg
async def test_cancel_pending_becomes_terminal_only_when_the_simulator_confirms():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "cancel")
        s = SL(a, "m")
        o = H.order(a, key="k1", qty=100, limit=0.40, slug=s, at=H.T0,
                    order_type="RESTING", tif="GTD", delay=0.0, ttl=3600,
                    queue_ahead=0.0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        b = await L.balances(conn, a["account_id"], now=H.T0)
        assert b["reserved_usd"] == 40.0
        c = await SIM.request_cancel(conn, oid, now=H.T0 + 1, reason="T")
        assert c["state"] == "CANCEL_PENDING"
        b = await L.balances(conn, a["account_id"], now=H.T0 + 1)
        assert b["reserved_usd"] == 40.0        # not released yet
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 2,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "CANCELED"
        b = await L.balances(conn, a["account_id"], now=H.T0 + 2)
        assert b["reserved_usd"] == 0.0 and b["cash_usd"] == 500000.0
    finally:
        await conn.close()
