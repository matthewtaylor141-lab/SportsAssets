"""CAPITAL-CRITICAL: THE PAPER ORDER LIFECYCLE, ITS QUANTITY ACCOUNTING AND
POSITION LINKAGE, AND WHAT EVERY READER CALLS PROTECTION (RC6 archer-
lifecycle).

Production research run 37875517525 (2026-10-09 02:38Z, release 69a8a07e):
11,628 PAPER orders in nine (role, type, state) cells -- ENTRY MARKETABLE
FILLED / CANCELED (IOC remainder, 49 with a fill) / EXPIRED, EXIT and REDUCE
the same, STANDING_PROTECTION RESTING -> FILLED 288, EXPIRED 7,761 (7 of them
after a partial fill), CANCELED 2,785 (MARKET_SETTLED, 4 after a partial
fill); no PAPER order is ever REJECTED (no writer) and the venue-confirmed
book holds 8 EXCLUDED execution-mirror rows and no venue fill. Every
accounting invariant held (fills = filled_qty, FILLED complete, no fill on a
RESTING / PENDING / REJECTED row, terminal stamps, FILL events = fills,
every sale linked to a held position).

Proven here, on a real ledger driven through every state with those shapes:

  * PENDING_SIMULATION -> FILLED (entry), RESTING -> PARTIALLY_FILLED ->
    EXPIRED with the partial fill kept (bd296caf: 2,695 of 2,702), RESTING ->
    PARTIALLY_FILLED -> CANCEL_PENDING -> CANCELED (5c9f045a: 94.01 of 109),
    an IOC remainder CANCELED, a marketable EXPIRED with nothing: the
    receipt's lifecycle census and every invariant (0 breaks);
  * at each state the protection every reader computes: only FILLED
    quantity is protection (order_state_truth / the Command position room /
    Xavier's protection continuity / Trader's packet validity); an EXPIRED
    or CANCELED order's unfilled remainder is named as contributing no
    protection -- also when it filled in part, which carried no note before;
    an EXIT or REDUCE sale is never protection; an EXCLUDED mirror row is
    REJECTED, never filled, never protection;
  * the receipt names a broken invariant (a FILLED order whose fills do not
    add up), and keeps the venue-confirmed book apart under its own label;
  * Xavier's review records the reviewed POSITION's filled protection, not
    the whole group's (a group with two positions).
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_reconciliation as REC
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import order_state_truth as OST
from sportsassets import position_rooms as PR
from sportsassets import trader_mode as T
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import test_paper_economic_duplicate_rule as DUP
from tests import test_xavier_review_probability_freshness as XRF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

T0 = REC.PRODUCER_FIX_EFFECTIVE_AT + 7200.0


def _room_order(row: dict) -> dict:
    """A paper order as the Command position room reads it (position_rooms
    `_paper`), normalised by the one mapping."""
    return PR.normalize_order({
        "order_ref": row["order_id"], "source": "paper_orders",
        "role": row["role"], "direction": row["direction"],
        "qty": float(row["qty"]), "filled_qty": float(row["filled_qty"]),
        "limit": float(row["limit_price"]), "raw_state": row["state"]})


def _summary(rows: list, held: float) -> dict:
    return OST.protection_summary(held_qty=held, orders=[
        _room_order(r) for r in rows])


async def _order(conn, oid: str) -> dict:
    return dict(await conn.fetchrow(
        "SELECT * FROM paper_orders WHERE order_id=$1", oid))


async def _lifecycle(conn, account_id: str, at: float) -> dict:
    rc = await REC.receipt(conn, account_id, now=at)
    assert rc["status"] == "OK", rc
    return rc


def _cell(rc, role, state) -> dict | None:
    return next((c for c in rc["sections"]["order_lifecycle"][
        "by_role_type_state"] if c["role"] == role and c["state"] == state),
        None)


# ═════════════════════════════════════════════════════════════════════
# THE PROTECTIVE SALE THROUGH ITS STATES
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_protective_sale_resting_partial_then_expired():
    """bd296caf's shape: a resting protective sale fills in part, then its
    GTD expires; the filled part is protection, the expired remainder is
    not, and every reader says so."""
    conn = await H.connect()
    try:
        a, slug, g, oid = await DUP._protected_long(conn, "rc6life",
                                                    qty=300, at=T0)
        o = await _order(conn, oid)
        assert (o["state"], float(o["filled_qty"])) == ("RESTING", 0.0)
        assert o["role"] == "STANDING_PROTECTION"
        # RESTING: standing, NOT protection
        s = _summary([o], held=300)
        assert (s["filled_protection_qty"], s["standing_order_qty"],
                s["unprotected_qty"]) == (0.0, 300.0, 300.0)
        assert PR._protective_note(_room_order(o)) == \
            "RESTING - NOT PROTECTION UNTIL FILLED"
        assert PMF.protection_state([o], 300, now=T0 + 7)["state"] == \
            PMF.PS_PROTECTED
        # PARTIALLY_FILLED: 120 of 300 crosses
        await H.observe(conn, slug, T0 + 10, bids=[(0.95, 120)])
        await SIM.simulate_order(conn, oid, now=T0 + 11, fee_fn=H.zero_fee)
        o = await _order(conn, oid)
        assert (o["state"], float(o["filled_qty"])) == (
            "PARTIALLY_FILLED", 120.0)
        s = _summary([o], held=180)
        assert (s["position_qty"], s["filled_protection_qty"],
                s["standing_order_qty"], s["unprotected_qty"]) == (
            300.0, 120.0, 180.0, 180.0)
        assert PR._protective_note(_room_order(o)).startswith(
            "PARTIAL - ONLY THE FILLED 120 COUNTS AS PROTECTION")
        # EXPIRED at its GTD, the partial fill kept
        await SIM.simulate_order(conn, oid, now=L._epoch(o["expires_at"]) + 1,
                                 fee_fn=H.zero_fee)
        o = await _order(conn, oid)
        assert (o["state"], float(o["filled_qty"]),
                o["terminal_reason"]) == ("EXPIRED", 120.0,
                                          SIM.R_GTD_EXPIRED)
        assert o["terminal_at"] is not None
        t = OST.order_state(o["state"], source="paper_orders", qty=o["qty"],
                            filled_qty=o["filled_qty"])
        assert (t["state"], t["filled_qty"], t["standing_qty"],
                t["can_still_fill"]) == (OST.EXPIRED, 120.0, 0.0, False)
        s = _summary([o], held=180)
        assert (s["filled_protection_qty"], s["standing_order_qty"],
                s["pending_order_qty"], s["unprotected_qty"],
                s["conditional_floor_if_filled_usd"]) == (
            120.0, 0.0, 0.0, 180.0, None)
        # the Command position room names the expired remainder
        assert PR._protective_note(_room_order(o)) == (
            "EXPIRED - ONLY THE FILLED 120 COUNTS AS PROTECTION; ITS "
            "UNFILLED 180 EXPIRED - CONTRIBUTES NO PROTECTION")
        # Xavier's continuity: an expired row is never PROTECTED
        assert PMF.protection_state([o], 180, now=T0 + 99999)["state"] == \
            PMF.PS_EXPIRED
        # Trader: the expired order is not among its standing orders, so a
        # review that recorded it cannot read as valid protection now
        review = {"selection": {"management_packet": {
            "gate": {"complete": True}, "protection": {"order_id": oid}}}}
        pk = T.packet_state(review, {}, now=T0 + 99999, orders=[],
                            position_qty=180)
        assert "NO_VALID_ACTIVE_PROTECTION" in pk["missing"]
        # Archer: lifecycle census and every invariant, 0 breaks; no live
        # protection on a closed position
        rc = await _lifecycle(conn, a["account_id"], T0 + 99999)
        assert rc["counts"]["order_lifecycle_breaks"] == 0
        assert rc["counts"]["live_protection_on_closed_positions"] == 0
        c = _cell(rc, "STANDING_PROTECTION", "EXPIRED")
        assert (c["orders"], c["partly_filled"], c["filled"]) == (1, 1, 120)
        e = _cell(rc, "ENTRY", "FILLED")
        assert (e["orders"], e["filled"], e["order_type"]) == (
            1, 300, "MARKETABLE")
        inv = rc["sections"]["order_lifecycle"]["invariants"]
        assert set(inv) == set(REC.LIFECYCLE_BREAKS)
        assert all(v == 0 for v in inv.values())
        assert rc["sections"]["order_lifecycle"][
            "execution_environment"] == "PAPER_SIMULATED"
    finally:
        await conn.close()


@pg
async def test_a_protective_sale_partial_then_cancel_pending_then_canceled():
    """5c9f045a's shape (CANCELED after a partial fill, 94.01 of 109): a
    requested cancel MAY STILL FILL (standing, not protection); once
    confirmed only the filled part counts."""
    conn = await H.connect()
    try:
        a, slug, g, oid = await DUP._protected_long(conn, "rc6cxl",
                                                    qty=109, at=T0)
        await H.observe(conn, slug, T0 + 10, bids=[(0.95, 94.01)])
        await SIM.simulate_order(conn, oid, now=T0 + 11, fee_fn=H.zero_fee)
        o = await _order(conn, oid)
        assert (o["state"], float(o["filled_qty"])) == (
            "PARTIALLY_FILLED", 94.01)
        got = await SIM.request_cancel(conn, oid, now=T0 + 12,
                                       reason="MARKET_SETTLED")
        assert got["ok"], got
        o = await _order(conn, oid)
        assert o["state"] == "CANCEL_PENDING"
        s = _summary([o], held=14.99)
        assert (s["filled_protection_qty"], s["standing_order_qty"]) == (
            94.01, 14.99)
        assert PR._protective_note(_room_order(o)).startswith(
            "CANCEL_PENDING - MAY STILL FILL; ITS 14.99 UNFILLED ARE NOT "
            "PROTECTION")
        assert PMF.protection_state([o], 14.99, now=T0 + 12)["state"] == \
            PMF.PS_CANCEL_PENDING
        await SIM.simulate_order(conn, oid, now=T0 + 13, fee_fn=H.zero_fee)
        o = await _order(conn, oid)
        assert (o["state"], float(o["filled_qty"])) == ("CANCELED", 94.01)
        s = _summary([o], held=14.99)
        assert (s["filled_protection_qty"], s["standing_order_qty"],
                s["unprotected_qty"]) == (94.01, 0.0, 14.99)
        assert PR._protective_note(_room_order(o)) == (
            "CANCELLED - ONLY THE FILLED 94.01 COUNTS AS PROTECTION; ITS "
            "UNFILLED 14.99 WAS CANCELLED - CONTRIBUTES NO PROTECTION")
        assert PMF.protection_state([o], 14.99, now=T0 + 13)["state"] == \
            PMF.PS_CANCELLED
        rc = await _lifecycle(conn, a["account_id"], T0 + 20)
        assert rc["counts"]["order_lifecycle_breaks"] == 0
        c = _cell(rc, "STANDING_PROTECTION", "CANCELED")
        assert (c["orders"], c["partly_filled"]) == (1, 1)
    finally:
        await conn.close()


@pg
async def test_marketable_states_and_sales_that_are_not_protection():
    """PENDING_SIMULATION -> FILLED (entry); an EXIT IOC whose remainder is
    CANCELED after a partial fill; a REDUCE that EXPIRES with nothing: none
    of the sales is protection, and the accounting holds."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "rc6mkt", now=T0)
        slug = "rc6-mkt-%s" % a["account_id"][-10:]
        g = "paper_g_%s_mkt" % a["account_id"][-10:]
        e = H.order(a, key="e", qty=500, limit=0.40, slug=slug, at=T0,
                    group_id=g)
        got = await L.submit_order(conn, e, fee_fn=H.zero_fee, now=T0)
        eid = got["order"]["order_id"]
        assert (await _order(conn, eid))["state"] == "PENDING_SIMULATION"
        assert OST.canonical_order_state("PENDING_SIMULATION",
                                         source="paper_orders") == \
            OST.SUBMITTED
        await H.observe(conn, slug, T0 + 3, offers=[(0.40, 500)])
        await SIM.simulate_order(conn, eid, now=T0 + 4, fee_fn=H.zero_fee)
        assert (await _order(conn, eid))["state"] == "FILLED"
        x = H.order(a, key="x", direction="SELL", role="EXIT", qty=200,
                    limit=0.30, slug=slug, at=T0 + 10, group_id=g)
        got = await L.submit_order(conn, x, fee_fn=H.zero_fee, now=T0 + 10)
        xid = got["order"]["order_id"]
        await H.observe(conn, slug, T0 + 13, bids=[(0.35, 80)])
        await SIM.simulate_order(conn, xid, now=T0 + 14, fee_fn=H.zero_fee)
        xo = await _order(conn, xid)
        assert (xo["state"], float(xo["filled_qty"]),
                xo["terminal_reason"]) == ("CANCELED", 80.0,
                                           SIM.R_IOC_REMAINDER)
        r = H.order(a, key="r", direction="SELL", role="REDUCE", qty=50,
                    limit=0.90, slug=slug, at=T0 + 20, group_id=g)
        got = await L.submit_order(conn, r, fee_fn=H.zero_fee, now=T0 + 20)
        rid = got["order"]["order_id"]
        await H.observe(conn, slug, T0 + 23, bids=[(0.35, 80)])
        await SIM.simulate_order(conn, rid, now=T0 + 24, fee_fn=H.zero_fee)
        ro = await _order(conn, rid)
        assert (ro["state"], float(ro["filled_qty"])) == ("EXPIRED", 0.0)
        # an EXIT or REDUCE sale is never protection, filled or not
        s = _summary([xo, ro], held=420)
        assert (s["filled_protection_qty"], s["standing_order_qty"],
                s["orders"]) == (0.0, 0.0, [])
        assert PR._protective_note(_room_order(xo)) is None
        assert PR._protective_note(_room_order(ro)) is None
        rc = await _lifecycle(conn, a["account_id"], T0 + 30)
        assert rc["counts"]["order_lifecycle_breaks"] == 0
        assert _cell(rc, "EXIT", "CANCELED")["partly_filled"] == 1
        assert _cell(rc, "REDUCE", "EXPIRED")["with_fill"] == 0
    finally:
        await conn.close()


def test_rejected_and_excluded_are_never_filled_never_protection():
    """No PAPER writer produces REJECTED (production: 0); the execution
    mirror's EXCLUDED rows (production: 8, 2026-10-03, never sent) map to
    REJECTED / EXCLUDED_BEFORE_SUBMISSION."""
    for raw, src, sub in (("REJECTED", "paper_orders", None),
                          ("EXCLUDED", "execmirror_orders",
                           OST.SUB_EXCLUDED)):
        t = OST.order_state(raw, source=src, qty=3, filled_qty=0)
        assert (t["state"], t["sub_state"], t["filled_qty"],
                t["standing_qty"], t["can_still_fill"]) == (
            OST.REJECTED, sub, 0.0, 0.0, False)
        o = PR.normalize_order({"order_ref": "x", "source": src,
                                "role": "STANDING_PROTECTION",
                                "direction": "SELL", "qty": 3.0,
                                "filled_qty": 0.0, "raw_state": raw})
        assert PR._protective_note(o) == \
            "REJECTED UNFILLED - CONTRIBUTES NO PROTECTION"
        s = OST.protection_summary(held_qty=3, orders=[o])
        assert (s["filled_protection_qty"], s["standing_order_qty"],
                s["pending_order_qty"], s["unprotected_qty"]) == (
            0.0, 0.0, 0.0, 3.0)


# ═════════════════════════════════════════════════════════════════════
# THE RECEIPT NAMES A BROKEN INVARIANT; THE TWO BOOKS STAY APART
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_receipt_names_a_broken_lifecycle_invariant():
    conn = await H.connect()
    try:
        a, _slug, _g, _oid = await DUP._protected_long(conn, "rc6brk",
                                                       qty=300, at=T0)
        rc = await _lifecycle(conn, a["account_id"], T0 + 20)
        assert rc["counts"]["order_lifecycle_breaks"] == 0
        eid = await conn.fetchval(
            "SELECT order_id FROM paper_orders WHERE account_id=$1 AND "
            " role='ENTRY'", a["account_id"])
        # a FILLED entry whose recorded filled_qty no longer equals its fills
        await conn.execute("UPDATE paper_orders SET filled_qty = filled_qty"
                           " - 1 WHERE order_id=$1", eid)
        rc = await _lifecycle(conn, a["account_id"], T0 + 21)
        inv = rc["sections"]["order_lifecycle"]["invariants"]
        assert inv["fills_sum_ne_filled_qty"] == 1
        assert inv["filled_state_not_complete"] == 1
        assert rc["counts"]["order_lifecycle_breaks"] == 2
    finally:
        await conn.close()


@pg
async def test_the_venue_confirmed_book_is_read_apart_from_paper():
    conn = await H.connect()
    try:
        a, _slug, _g, _oid = await DUP._protected_long(conn, "rc6venue",
                                                       qty=300, at=T0)
        rc = await _lifecycle(conn, a["account_id"], T0 + 20)
        v = rc["sections"]["venue_confirmed_book"]
        assert v["label"].startswith("ACTUAL (venue-confirmed)")
        assert {r["source"] for r in v["rows"]} >= {"execmirror_fills"}
        # PAPER counts never include a venue row: the census is the paper
        # ledger's own (one ENTRY, one protection)
        assert sum(c["orders"] for c in rc["sections"]["order_lifecycle"][
            "by_role_type_state"]) == 2
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# XAVIER'S REVIEW: THE REVIEWED POSITION'S FILLED PROTECTION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_xavier_review_records_the_positions_own_filled_protection():
    """A group holding two positions (two markets): the first position's
    protection fills 120; the review of the SECOND position records 0
    filled protection (it read the whole group's 120 before)."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "rc6two", now=T0)
        g = "paper_g_%s_two" % a["account_id"][-10:]
        slugs = ["rc6-two-a-%s" % a["account_id"][-8:],
                 "rc6-two-b-%s" % a["account_id"][-8:]]
        # both entries submitted before either fills (the entry rail
        # judges the open positions a new entry would join)
        oids = []
        for k, slug in enumerate(slugs):
            e = H.order(a, key="e%d" % k, qty=300, limit=0.40, slug=slug,
                        at=T0, group_id=g)
            got = await L.submit_order(conn, e, fee_fn=H.zero_fee, now=T0)
            assert got["ok"], got
            oids.append(got["order"]["order_id"])
        for slug, eid in zip(slugs, oids):
            await H.observe(conn, slug, T0 + 3, offers=[(0.40, 300)])
            await SIM.simulate_order(conn, eid, now=T0 + 4,
                                     fee_fn=H.zero_fee)
        await PX.step_handoff(conn, XRF._ctx(a, T0 + 5))
        # the first position's standing sale (one live standing order per
        # group: paper_orders_one_live_standing_idx)
        from sportsassets import bettor_xavier_standing_orders as SPO
        (pos,) = [p for p in await L.positions(conn, a["account_id"])
                  if p["group_id"] == g and p["us_market_slug"] == slugs[0]]
        at = T0 + 6
        put = await PX._maintain_standing(
            conn, dict(XRF._ctx(a, at), now=at, clock=lambda: at), pos=pos,
            standing=[], prot=PX.protective_price(
                qty=pos["open_qty"], cost_basis=pos["cost_basis_usd"],
                fee_fn=H.zero_fee, at=at), md=None, at=at, SPO=SPO)
        assert put["taken"] == "PLACE_STANDING" and put["ok"], put
        pa = put["order_id"]
        await SIM.simulate_order(conn, pa, now=at + 0.5, fee_fn=H.zero_fee)
        await H.observe(conn, slugs[0], T0 + 10, bids=[(0.95, 120)])
        await SIM.simulate_order(conn, pa, now=T0 + 11, fee_fn=H.zero_fee)
        assert float((await _order(conn, pa))["filled_qty"]) == 120.0
        await PX.review_group(conn, XRF._ctx(a, T0 + 30), g,
                              trigger=PX.T_BACKSTOP)
        got = {}
        for r in await conn.fetch(
                "SELECT exposure, confirmed_protection FROM "
                " paper_xavier_reviews WHERE group_id=$1", g):
            ex = H.j(r["exposure"])
            cp = H.j(r["confirmed_protection"])
            got[float(ex["open_qty"])] = cp
        assert set(got) == {180.0, 300.0}
        assert got[180.0]["filled_protection_qty"] == 120.0
        assert got[300.0]["filled_protection_qty"] == 0.0
        assert got[300.0]["scope"] == "POSITION"
        assert got[300.0]["execution_environment"] == "PAPER_SIMULATED"
    finally:
        await conn.close()
