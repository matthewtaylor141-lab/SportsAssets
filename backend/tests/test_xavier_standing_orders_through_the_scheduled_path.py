"""XAVIER'S STANDING PROTECTIVE ORDERS, PROVED THROUGH THE REAL SCHEDULED PATH.

WHAT RUNS. Every proof drives `ext_pinnacle_loop.cycle(conn)` (or the venue
event handlers, which run the SAME servicing pass) against a migrated
PostgreSQL database with the account written through the real writers --
exactly the XH harness of `test_xavier_manages_positions_through_the_
scheduled_path`. The standing-order step runs inside `pass_once`, under the
execution lock and each group's advisory lock; placements go through Xavier's
decision row, its dispatch claim, the leg reservation and
`bettor_funded_execution.submit_for_decision`.

WHAT IS SUBSTITUTED -- THE VENUE TRANSPORT AND NOTHING ABOVE IT: `pmus.
_get_client` (with `tests/standing_order_harness.RestingVenue`, which can
rest, fill, cancel and expire an order), the settlement probe and the book-
currency seam, as XH substitutes them. The submission switches are patched
True INSIDE these tests only. The standing-order policy is ENABLED by the
owner's write (`activate_version`) in each test; its code default is OFF.

EVERYTHING IS SYNTHETIC: the Red Sox / Yankees fixture, its books, its
settlement prose, the fills, the approved conditional model. NOTHING HERE IS
EVIDENCE ABOUT ANY MARKET.

THE SHAPE. 10 Red Sox moneyline held at 0.50 (HOLD on p = 0.55). The NYY
+1.5 (and +2.5) run lines are middles: in every ordinary settlement state at
least one leg pays $1 per pair, so a pair is protective when the hedge costs
less than $1 minus the primary's cost, the fees on both legs and the buffer.
At the displayed 0.70 the hedge loses to HOLD and nothing is taken; the
protective price is 0.45 (wire 0.55), where the pair's ordinary floor is
positive and -- valued on the approved distribution -- the pair beats HOLD.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_xavier as XV
from sportsassets import bettor_xavier_standing_orders as SPO
from tests import standing_order_harness as SH
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

pg = XH.pg
GID = "grp:" + XH.HELD_ID


async def _connect():
    return await XH._connect()


def _hedge_creates(venue):
    return [c for c in venue.creates_sent()
            if c["marketSlug"] in (XH.SIB, SH.SIB2)]


async def _assert_invariant(conn, venue=None):
    """hedge held + fill-capable <= confirmed primary, in OUR book; and at
    the VENUE, at most one live hedge order and never more hedge than
    primary."""
    gs = await SH.group(conn)
    assert gs["invariant"]["holds"], gs["invariant"]
    assert gs["live_or_potentially_live_orders"] <= 1, gs["orders"]
    if venue is not None:
        hedge_live = [o for o in venue.resting()
                      if o["marketSlug"] in (XH.SIB, SH.SIB2)]
        assert len(hedge_live) <= 1, hedge_live
        held = venue.holdings.get(XH.HELD, (0.0, 0.0))[0]
        hedged = -sum(venue.holdings.get(s, (0.0, 0.0))[0]
                      for s in (XH.SIB, SH.SIB2))
        assert hedged <= held + 1e-9, (hedged, held)
    return gs


async def _place(conn, monkeypatch, *, books=None, **start_kw):
    """Cycle 1: HOLD wins, one standing order rests at the protective
    price. Returns (venue, the venue order id, the cycle's output)."""
    await SH.start(conn, **start_kw)
    venue = SH.RestingVenue(books=books or SH.books(hedge2_bid=0.30),
                            holdings={XH.HELD: (10.0, 5.0)})
    XH.substitute(monkeypatch, venue)
    out = await XH.run_cycle(conn)
    so = SH.standing_step(out)
    assert XH.step_of(out)["decision"]["action"] == "HOLD", XH.step_of(out)
    assert so.get("placed") is True, SH.dumps(so)
    rest = venue.rest_creates()
    assert len(rest) == 1, venue.sent
    oid = [o for o in venue.orders if o.startswith("venue-rest-")][0]
    return venue, oid, out


# ════════════════════════════════════════════════════════════════════
# (a) A PARTIAL ENTRY FILL CREATES ONLY THE PERMITTED HEDGE CAPACITY
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_a_partial_entry_fill_creates_only_confirmed_capacity(
        monkeypatch):
    """The ENTRY was for 10 and 4 have filled; its remaining 6 still work at
    the venue. The standing order is sized on the CONFIRMED 4 -- never the
    intended 10 -- and the plan records the capacity it was sized on. When
    the entry fills 3 more, the unfilled standing order is resized through
    cancel -> confirmed terminal -> replacement (never a second live order),
    at the new confirmed 7."""
    conn = await _connect()
    try:
        await SH.clean(conn)
        await XH.authorize(conn)
        await SH.catalogue_two_hedges(conn)
        # THE ENTRY, 10 ORDERED, 4 FILLED, 6 STILL WORKING
        got = await FB.record_intent(
            conn, intent_id=XH.HELD_ID, account_id=XH.ACCT, venue=XH.VENUE,
            venue_class=XH.FA.VENUE_FUNDED, us_market_slug=XH.HELD,
            event_key=XH.EVENT, order_intent=XH.FX.LONG, limit_price=0.50,
            quantity=10, collateral_usd=XH.FX.collateral_for(
                0.50, 10, XH.FX.LONG),
            effective_digest="d-xc", payout_event=XH.PAYS_ON,
            held_is_long=True, portfolio_group_id=None, leg_role="PRIMARY",
            group_structure="INDIRECT_MIDDLE")
        assert got["ok"], got
        await FB.record_acknowledgement(conn, XH.HELD_ID,
                                        venue_order_id="venue-entry-xc",
                                        status="open")
        await FB.ingest_fills(conn, XH.HELD_ID, [
            {"qty": 4.0, "price": 0.50, "venue_fill_id": "vf-entry-1"}])
        from tests import approved_conditional_model as ACM
        await ACM.approve(conn)
        await XH.probability(conn, p=0.55)
        await SH.enable_policy(conn)
        venue = SH.RestingVenue(books=SH.books(hedge2_bid=0.30),
                                holdings={XH.HELD: (4.0, 2.0)})
        # the venue's own record of the working entry (4 of 10)
        venue.orders["venue-entry-xc"] = {"record": {
            "order": {"id": "venue-entry-xc", "marketSlug": XH.HELD,
                      "intent": XH.FX.LONG,
                      "price": {"value": "0.50", "currency": "USD"},
                      "quantity": 10, "cumQuantity": 4, "leavesQuantity": 6,
                      "state": "ORDER_STATE_PARTIALLY_FILLED"},
            "executions": [{"id": "vf-entry-1",
                            "type": "EXECUTION_TYPE_PARTIAL_FILL",
                            "lastPx": {"value": "0.50", "currency": "USD"},
                            "lastShares": "4"}]}}
        XH.substitute(monkeypatch, venue)
        out = await XH.run_cycle(conn)
        so = SH.standing_step(out)
        assert so.get("placed") is True, SH.dumps(XH.step_of(out))
        rest = venue.rest_creates()
        assert len(rest) == 1
        assert int(rest[0]["quantity"]) == 4          # confirmed, not intended
        (plan,) = await SH.plans(conn)
        cap = json.loads(plan["capacity"])
        assert cap["confirmed_primary_qty"] == 4.0
        assert cap["planned_qty"] == 4 and plan["quantity"] == 4
        gs = await _assert_invariant(conn, venue)
        assert gs["primary"]["quantity_ordered"] == 10.0
        assert gs["confirmed_primary_qty"] == 4.0
        assert gs["fill_capable_qty"] == 4.0
        assert gs["capacity_remaining_qty"] == 0.0

        # ── THE ENTRY FILLS 3 MORE: capacity 7, the rest is RESIZED ──
        ent = venue.orders["venue-entry-xc"]["record"]
        ent["order"].update(cumQuantity=7, leavesQuantity=3)
        ent["executions"].append({
            "id": "vf-entry-2", "type": "EXECUTION_TYPE_PARTIAL_FILL",
            "lastPx": {"value": "0.50", "currency": "USD"},
            "lastShares": "3"})
        venue.holdings[XH.HELD] = (7.0, 3.5)
        out2 = await XH.run_cycle(conn)
        assert "RESIZE_REQUESTED" in await SH.event_kinds(conn)
        assert len(venue.rest_creates()) == 1        # no second live order
        await _assert_invariant(conn, venue)
        # cycle 3: the cancel is confirmed terminal, capacity released, and
        # only then is the replacement placed at the confirmed 7
        out3 = await XH.run_cycle(conn)
        creates = venue.rest_creates()
        assert len(creates) == 2, venue.sent
        assert int(creates[1]["quantity"]) == 7
        kinds = await SH.event_kinds(conn)
        assert kinds.index("TERMINAL_CONFIRMED") < len(kinds) - 1
        ps = await SH.plans(conn)
        assert [p["quantity"] for p in ps] == [4, 7]
        await _assert_invariant(conn, venue)
        del out2, out3
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (b) THE FIRST PARTIAL HEDGE FILL SELECTS THE INSTRUMENT
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_b_the_first_partial_hedge_fill_selects_the_instrument(
        monkeypatch):
    """One standing order rests on the NYY +1.5; the NYY +2.5 is a MONITORED
    candidate that is valued and never submitted. A counterparty fills 3 of
    the 10. The next pass records, at once: the selected instrument (by its
    first fill), 3 covered, 7 uncovered, 7 still fillable on the live order,
    and the payout table of the ACTUAL combined inventory (10 held, 3
    hedged). The +2.5 is never sent; an acquisition on it is refused as a
    transition."""
    conn = await _connect()
    try:
        venue, oid, out = await _place(conn, monkeypatch)
        so = SH.standing_step(out)
        mon = {c["candidate_id"]: c for c in so["monitored_candidates"]}
        assert SH.HEDGE_CID in mon and SH.HEDGE2_CID in mon
        assert all(c["submitted"] is False for c in mon.values())
        (plan,) = await SH.plans(conn)
        placed_on = plan["candidate_id"]
        other = SH.HEDGE2_CID if placed_on == SH.HEDGE_CID else SH.HEDGE_CID
        venue.fill(oid, 3)
        out2 = await XH.run_cycle(conn)
        sel = await conn.fetchrow(
            "SELECT * FROM bettor_hedge_group_selection WHERE group_id=$1",
            GID)
        assert sel is not None and sel["candidate_id"] == placed_on
        assert sel["selected_by"] == "FIRST_FILL" and sel["first_fill_id"]
        gs = await _assert_invariant(conn, venue)
        assert gs["hedge_held_qty"] == 3.0
        assert gs["covered_qty"] == 3.0 and gs["uncovered_qty"] == 7.0
        assert gs["fill_capable_qty"] == 7.0
        assert gs["lifecycle_state"] == SPO.L_PARTIAL
        cov = [e for e in await SPO.events(conn, group_id=GID)
               if e["event_kind"] == "COVERAGE_RECORDED"]
        assert cov, await SH.event_kinds(conn)
        ev = cov[-1]["evidence"]
        assert ev["covered_qty"] == 3.0 and ev["uncovered_qty"] == 7.0
        assert ev["remaining_fillable_on_the_live_order"] == 7.0
        assert ev["selected_instrument"] == placed_on
        table = ev["payout_table_actual_inventory"]
        assert table["primary_qty"] == 10.0 and table["hedge_qty"] == 3.0
        assert table["regions"], table
        # the +2.5 was NEVER submitted, on any cycle
        assert all(c["marketSlug"] != SPO.HS.venue_slug_of(other)
                   for c in venue.creates_sent())
        assert len(_hedge_creates(venue)) == 1
        # and the hedge leg is now a real leg of the group
        h = await SH.hedge_intents(conn)
        assert len(h) == 1 and h[0]["residual"] == 3.0
        del out2
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (c) THE PROHIBITED RACE, REPRODUCED (policy bypassed ON PURPOSE)
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_c_two_live_hedge_orders_on_different_instruments_can_both_fill(
        monkeypatch):
    """THE HAZARD THE STRICT FALLBACK EXISTS FOR, constructed directly in the
    harness and bypassing the policy: two GOOD_TILL_DATE hedge orders of 10,
    one on the NYY +1.5 and one on the NYY +2.5, both resting against 10 held.
    The venue does not know they are alternatives, so BOTH fill: 20 hedge
    against 10 primary. The database's one-open-hedge-leg-per-group index
    refuses to book the second as an open leg -- and that changes nothing at
    the venue, where both orders had already matched. Which is why the policy
    never lets a second live-or-potentially-live hedge order exist, and why a
    claim of exchange-linked exclusivity (here a mock) is not accepted."""
    from sportsassets import pmus
    conn = await _connect()
    try:
        await SH.start(conn, enable=False)
        venue = SH.RestingVenue(books=SH.books(hedge2_bid=0.30),
                                holdings={XH.HELD: (10.0, 5.0)})
        XH.substitute(monkeypatch, venue)
        good_till = SPO._iso(time.time() + 900)
        a = pmus.submit_fok(XH.SIB, 0.55, 10, tif=SH.GTD,
                            intent="ORDER_INTENT_BUY_SHORT",
                            good_till=good_till)
        b = pmus.submit_fok(SH.SIB2, 0.55, 10, tif=SH.GTD,
                            intent="ORDER_INTENT_BUY_SHORT",
                            good_till=good_till)
        assert a["order_id"] and b["order_id"]
        assert len(venue.resting()) == 2          # two live hedge orders
        # OUR BOOK REFUSES THE SECOND AS AN OPEN HEDGE LEG ...
        ga = await FB.record_intent(
            conn, intent_id="fpi-race-a", account_id=XH.ACCT, venue=XH.VENUE,
            venue_class=XH.FA.VENUE_FUNDED, us_market_slug=XH.SIB,
            event_key=XH.EVENT, order_intent="ORDER_INTENT_BUY_SHORT",
            limit_price=0.55, quantity=10, collateral_usd=4.5,
            effective_digest="d", portfolio_group_id=GID, leg_role="HEDGE")
        gb = await FB.record_intent(
            conn, intent_id="fpi-race-b", account_id=XH.ACCT, venue=XH.VENUE,
            venue_class=XH.FA.VENUE_FUNDED, us_market_slug=SH.SIB2,
            event_key=XH.EVENT, order_intent="ORDER_INTENT_BUY_SHORT",
            limit_price=0.55, quantity=10, collateral_usd=4.5,
            effective_digest="d", portfolio_group_id=GID, leg_role="HEDGE")
        assert ga["ok"] is True
        assert gb["ok"] is False, gb             # one open hedge leg per group
        # ... AND BOTH VENUE ORDERS FILL ANYWAY
        venue.fill(a["order_id"], 10)
        venue.fill(b["order_id"], 10)
        held = venue.holdings[XH.HELD][0]
        hedged = -(venue.holdings[XH.SIB][0] + venue.holdings[SH.SIB2][0])
        assert held == 10.0 and hedged == 20.0
        assert hedged > held                      # THE HAZARD: 2x over-hedged
        # THE POLICY'S GATE REFUSES A SECOND LIVE ORDER, AND A MOCKED
        # EXCHANGE GUARANTEE IS NOT EVIDENCE
        g1 = SPO.second_live_hedge_permitted(live_or_potentially_live=1)
        assert g1["permitted"] is False
        assert g1["refusal"] == SPO.R_LIVE_ORDER_EXISTS
        g2 = SPO.second_live_hedge_permitted(
            live_or_potentially_live=1,
            claimed_exclusivity={"venue_oco": True, "source": "MOCK"})
        assert g2["permitted"] is False and g2["claim_is_evidence"] is False
        assert g2["refusal"] == SPO.R_NO_VERIFIED_EXCLUSIVITY
        assert SPO.EXCHANGE_LINKED_EXCLUSIVITY == "UNAVAILABLE_OR_UNVERIFIED"
        assert SPO.validate({"mode": "LINKED_EXCLUSIVE"})["refusal"] == \
            SPO.R_MODE_NOT_IMPLEMENTED
        assert SPO.validate({"split_quantities_across_instruments": True})[
            "refusal"] == SPO.R_SPLIT_QUANTITIES_NEED_A_SEPARATE_POLICY_DECISION
    finally:
        await conn.execute(
            "DELETE FROM bettor_funded_intents WHERE intent_id LIKE "
            "'fpi-race-%'")
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (d) NO SECOND HEDGE ORDER WHILE THE FIRST IS FILL-CAPABLE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_d_no_second_order_while_the_first_is_pending_cancel_or_ambiguous(
        monkeypatch):
    """The fee schedule changes, which invalidates the live order's floor
    classification: Xavier asks the venue to cancel it. The venue answers
    PENDING_CANCEL and holds it there for two passes: the order is still
    FILL-CAPABLE, its capacity stays reserved, the book still counts it as
    outstanding exposure, and NOTHING else is sent -- not a replacement, not
    an order on the other instrument. Only once the venue's read is TERMINAL
    (and reconciled) is the replacement placed. Then the replacement's
    answer is LOST: it is potentially live, and again nothing competes."""
    from sportsassets import calibration_fees as CF
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        venue.cancel_mode = "PENDING"
        real_theta = CF.taker_coefficient
        monkeypatch.setattr(CF, "taker_coefficient",
                            lambda sport=None, at=None: Decimal("0.0800"))
        await XH.run_cycle(conn)
        kinds = await SH.event_kinds(conn)
        assert "FLOOR_INVALIDATED" in kinds and "CANCEL_REQUESTED" in kinds
        assert venue.state(oid)["state"] == "ORDER_STATE_PENDING_CANCEL"
        for _ in range(2):
            await XH.run_cycle(conn)
            gs = await _assert_invariant(conn, venue)
            assert gs["lifecycle_state"] == SPO.L_CANCEL_REQUESTED
            assert gs["fill_capable_qty"] == 10.0
            assert len(gs["reserved"]) == 1              # still reserved
            exp = await FB.exposure(conn, account_id=XH.ACCT,
                                    venue=XH.VENUE)
            assert any(r["us_market_slug"] == XH.SIB
                       for r in exp["outstanding_orders"]), exp
            assert len(_hedge_creates(venue)) == 1       # nothing else sent
        # the database itself refuses to release while it is not terminal
        (res,) = [r for r in gs["reserved"]]
        rel = await SPO._release(conn, plan_id=res["plan_id"],
                                 reason="TERMINAL_CONFIRMED_BY_THE_VENUE")
        assert rel["released"] is False and "not confirmed terminal" in \
            rel["error"], rel
        # ── THE VENUE CONFIRMS THE CANCEL ────────────────────────────
        venue.finish_cancel(oid)
        venue.raise_on_create = TimeoutError("the answer never came back")
        await XH.run_cycle(conn)
        kinds = await SH.event_kinds(conn)
        assert "TERMINAL_CONFIRMED" in kinds
        creates = _hedge_creates(venue)
        assert len(creates) == 2, venue.sent       # the replacement, only now
        assert kinds.index("TERMINAL_CONFIRMED") < kinds.index(
            "SENT_OUTCOME_UNKNOWN")
        # ── ITS ANSWER WAS LOST: POTENTIALLY LIVE, NOTHING COMPETES ──
        venue.raise_on_create = None
        for _ in range(2):
            await XH.run_cycle(conn)
            gs = await _assert_invariant(conn, venue)
            assert gs["lifecycle_state"] == SPO.L_AMBIGUOUS
            assert gs["fill_capable_qty"] == 10.0
            assert len(_hedge_creates(venue)) == 2
        assert "AMBIGUOUS_NO_COMPETING_ORDER" in await SH.event_kinds(conn)
        del real_theta
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (e) A CANCEL ACKNOWLEDGED BEFORE A DELAYED FILL REPORT
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_e_a_cancel_ack_before_a_delayed_fill_report_over_allocates_nothing(
        monkeypatch):
    """3 of the resting 10 trade at the venue, but the venue's order read
    does not yet carry that execution (the fill REPORT is delayed). Xavier
    then cancels (fee change); the venue acknowledges and reports the order
    CANCELED with cumQuantity 3 -- while the book holds 0. That ending does
    not balance, so it is NOT applied: the order stays fill-capable for 10,
    its capacity stays reserved and no replacement is placed. Had the
    acknowledgement been treated as terminal, a replacement for 10 would have
    gone out against 3 already filled -- 13 hedge on 10 primary. The delayed
    execution then arrives on the private websocket; the event runs the same
    group authority, the ending balances, capacity is released and the group
    holds 3 hedge on 10 primary."""
    from sportsassets import calibration_fees as CF
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        venue.fill(oid, 3)
        venue.hide_executions_after[oid] = 0       # the fill is not reported
        monkeypatch.setattr(CF, "taker_coefficient",
                            lambda sport=None, at=None: Decimal("0.0800"))
        await XH.run_cycle(conn)
        assert "CANCEL_REQUESTED" in await SH.event_kinds(conn)
        st = venue.state(oid)
        assert st["state"] == "ORDER_STATE_CANCELED" and st["cumQuantity"] == 3
        for _ in range(2):
            await XH.run_cycle(conn)
            gs = await _assert_invariant(conn, venue)
            (o,) = gs["orders"]
            assert o["book_state"] == "ACKNOWLEDGED"     # NOT terminal
            assert o["fill_capable_qty"] == 10.0
            assert len(gs["reserved"]) == 1
            assert len(_hedge_creates(venue)) == 1       # no replacement
            assert await SH.ledger_hedge_filled(conn) == 0.0
        # ── THE DELAYED REPORT, ON THE PRIVATE WEBSOCKET ─────────────
        msg = venue.order_update(oid, execution_index=0)
        got = await L.on_private_order_message(conn, msg)
        assert got["ok"] and got["relevant"], got
        assert got["ingested"][0]["written"] == 1
        assert got["pass"]["ran"] is True
        gs = await _assert_invariant(conn, venue)
        (o,) = gs["orders"]
        assert o["book_state"] == "CANCELLED"            # now it balances
        assert gs["hedge_held_qty"] == 3.0 and gs["fill_capable_qty"] == 0.0
        assert not gs["reserved"]
        assert await SH.ledger_hedge_filled(conn) == 3.0
        assert len(_hedge_creates(venue)) == 1
        kinds = await SH.event_kinds(conn)
        assert "VENUE_ORDER_EVENT" in kinds and "TERMINAL_CONFIRMED" in kinds
        assert "REMAINDER_REPLACEMENT_REFUSED" in kinds
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (f) LOST ACKNOWLEDGEMENT, RESTART, DUPLICATE FILL REPORTS
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_f_a_lost_answer_and_a_restart_create_no_duplicate_exposure(
        monkeypatch):
    """The standing order REACHES the venue and the answer is lost. It is
    potentially live for its whole quantity: nothing is resent, nothing
    competes -- across a RESTART (connection closed, process caches dropped)
    and after the venue fills it in full. The venue then holds 10 hedge on 10
    primary; never more."""
    conn = await _connect()
    try:
        await SH.start(conn)
        venue = SH.RestingVenue(books=SH.books(hedge2_bid=0.30),
                                holdings={XH.HELD: (10.0, 5.0)})
        venue.raise_after_create = TimeoutError("the answer never came back")
        XH.substitute(monkeypatch, venue)
        await XH.run_cycle(conn)
        (h,) = await SH.hedge_intents(conn)
        assert h["state"] == "UNRESOLVED" and h["venue_order_id"] is None
        assert "SENT_OUTCOME_UNKNOWN" in await SH.event_kinds(conn)
        assert len(venue.resting()) == 1           # it IS live at the venue
        # ── THE RESTART ──────────────────────────────────────────────
        await conn.close()
        XH.reset_process_state()
        conn = await _connect()
        venue.raise_after_create = None
        for i in range(2):
            out = await XH.run_cycle(conn)
            gs = await _assert_invariant(conn, venue)
            assert gs["lifecycle_state"] == SPO.L_AMBIGUOUS
            assert gs["fill_capable_qty"] == 10.0
            assert len(_hedge_creates(venue)) == 1, venue.sent
            if i == 0:
                oid = venue.resting()[0]["id"]
                venue.fill(oid, 10)                # it fills, unseen by us
        assert out["funded_servicing"]["pair_cycle"][
            "resubmitted_anything"] is False
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_standing_order_plans "
            " WHERE account_id=$1", XH.ACCT) == 1
    finally:
        await SH.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_f_duplicate_fill_reports_book_one_fill(monkeypatch):
    """4 of the resting 10 fill. The same execution arrives on the private
    websocket twice, again inside an order snapshot, again on the servicing
    pass's order read -- and across a restart. The book holds ONE fill of 4
    (keyed by the venue's own execution id), and nothing else is sent."""
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        venue.fill(oid, 4)
        msg = venue.order_update(oid)
        a = await L.on_private_order_message(conn, msg, run_pass=False)
        b = await L.on_private_order_message(conn, msg, run_pass=False)
        assert a["ingested"][0]["written"] == 1
        assert b["ingested"][0]["written"] == 0
        assert b["ingested"][0]["already_held"] == 1
        c = await L.on_private_order_message(conn, venue.order_snapshot())
        assert c["ok"] and c["pass"]["ran"] is True
        await XH.run_cycle(conn)
        await conn.close()
        XH.reset_process_state()
        conn = await _connect()
        await XH.run_cycle(conn)
        assert await SH.ledger_hedge_filled(conn) == 4.0
        n = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills f JOIN "
            " bettor_funded_intents i ON i.intent_id=f.intent_id "
            " WHERE i.account_id=$1 AND i.leg_role='HEDGE'", XH.ACCT)
        assert n == 1
        fills = [e for e in await SPO.events(conn, group_id=GID)
                 if e["event_kind"] == "FILL_OBSERVED"]
        assert {float(e["filled_qty"]) for e in fills} == {4.0}
        gs = await _assert_invariant(conn, venue)
        assert gs["hedge_held_qty"] == 4.0 and gs["fill_capable_qty"] == 6.0
        assert len(_hedge_creates(venue)) == 1
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (g) A PRIMARY REDUCTION RACING A HEDGE FILL
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_g_a_primary_reduction_racing_a_hedge_fill_never_leaves_hedge_above_primary(
        monkeypatch):
    """10 held at 0.60 with a standing hedge resting for 10 and 3 filled.
    The forward probability falls to 0.30 and the held side bids 0.45 / 0.42:
    the group review selects REDUCE. The live hedge order can still fill 7,
    so the REDUCE is NOT sent: the order is cancelled first (the unresolved
    exposure is reported) and the reduction waits for its confirmed terminal
    state. THE RACE: before the venue completes the cancel, 4 more hedge
    contracts fill. The next pass reconciles the final fills (7 hedge) and
    the same REDUCE of 7 would now leave 3 primary under 7 hedge -- it is
    refused, and the residual it would have left (4 unprotected hedge
    contracts, their cost basis and worst case) is valued and reported. At
    no point does hedge held + fill-capable exceed the primary, and no sell
    is sent."""
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch, price=0.60)
        venue.fill(oid, 3)
        await XH.probability(conn, p=0.30)
        venue.books[XH.HELD] = {"bids": [(0.45, 4), (0.42, 3), (0.10, 400)],
                                "offers": [(0.99, 5)]}
        venue.cancel_mode = "PENDING"
        out = await XH.run_cycle(conn)
        step = XH.step_of(out)
        assert step["decision"]["action"] == "REDUCE", step["decision"]
        assert step["refusal"] == SPO.R_EXIT_WAITS_FOR_HEDGE_TERMINAL
        assert venue.state(oid)["state"] == "ORDER_STATE_PENDING_CANCEL"
        wait = [e for e in await SPO.events(conn, group_id=GID)
                if e["event_kind"] == "EXIT_WAITS_FOR_HEDGE_TERMINAL"]
        assert wait and wait[-1]["evidence"]["unresolved_exposure"][
            "hedge_fill_capable_qty"] == 7.0
        await _assert_invariant(conn, venue)
        # ── THE RACE: 4 MORE FILL BEFORE THE CANCEL COMPLETES ────────
        venue.fill(oid, 4)
        venue.finish_cancel(oid)
        out2 = await XH.run_cycle(conn)
        s2 = XH.step_of(out2)
        gs = await _assert_invariant(conn, venue)
        assert gs["hedge_held_qty"] == 7.0 and gs["fill_capable_qty"] == 0.0
        assert gs["confirmed_primary_qty"] == 10.0
        assert s2["decision"]["action"] == "REDUCE", s2["decision"]
        assert s2["refusal"] == SPO.R_EXIT_WOULD_LEAVE_HEDGE_ABOVE_PRIMARY
        res = s2["standing_order"]["residual"]
        assert res["hedge_excess_qty_after_the_exit"] == 4.0
        assert res["primary_after_qty"] == 3.0
        assert res["excess_cost_basis_usd"] > 0
        assert res["excess_worst_case_usd"] == -res["excess_cost_basis_usd"]
        # NO SELL WAS SENT, AND THE BOOK HOLDS 10 PRIMARY / 7 HEDGE
        assert not [c for c in venue.creates_sent()
                    if c.get("intent") == "ORDER_INTENT_SELL_LONG"]
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            " AND kind='EXIT'", XH.ACCT) == 0
        assert venue.holdings[XH.HELD][0] == 10.0
        assert -venue.holdings[XH.SIB][0] == 7.0
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (h) FULL COMPLETION LEAVES NO COMPETING ORDER
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_h_full_completion_leaves_no_competing_order(monkeypatch):
    """The standing order fills in two parts (6, then 4). The venue reads it
    FILLED; the book holds 10 hedge on 10 primary; the capacity is released
    on the confirmed terminal state; no order rests anywhere; and the passes
    that follow send nothing -- the monitored +2.5 was never sent and is not
    sent now."""
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        venue.fill(oid, 6)
        await XH.run_cycle(conn)
        venue.fill(oid, 4)
        for _ in range(3):
            await XH.run_cycle(conn)
        gs = await _assert_invariant(conn, venue)
        (o,) = gs["orders"]
        assert o["book_state"] == "FILLED"
        assert gs["hedge_held_qty"] == 10.0 and gs["covered_qty"] == 10.0
        assert gs["uncovered_qty"] == 0.0 and gs["fill_capable_qty"] == 0.0
        assert gs["live_or_potentially_live_orders"] == 0
        assert not gs["reserved"] and gs["lifecycle_state"] == \
            "%s:FILLED" % SPO.L_TERMINAL
        assert venue.resting() == []
        assert len(_hedge_creates(venue)) == 1
        rel = [r for r in gs["reservations"] if r["state"] == "RELEASED"]
        assert rel and rel[0]["release_reason"] == \
            "TERMINAL_CONFIRMED_BY_THE_VENUE"
        sel = gs["selection"]
        assert sel["candidate_id"] in (SH.HEDGE_CID, SH.HEDGE2_CID)
        kinds = await SH.event_kinds(conn)
        assert kinds.count("INSTRUMENT_SELECTED") == 1
        assert "TERMINAL_CONFIRMED" in kinds
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (i) A FEE CHANGE AND AN UNSUPPORTED SETTLEMENT TREATMENT INVALIDATE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_i_a_fee_change_invalidates_the_floor_classification(
        monkeypatch):
    """The plan was classified under the dated schedule's taker coefficient
    (0.0695). The coefficient changes: the classification is INVALIDATED by
    name, with both schedule identities on the record, and the order is
    cancelled. A classification is a statement about one fee schedule, not
    about the order."""
    from sportsassets import calibration_fees as CF
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        (plan,) = await SH.plans(conn)
        assert plan["floor_class"] == SPO.F_ORDINARY
        monkeypatch.setattr(CF, "taker_coefficient",
                            lambda sport=None, at=None: Decimal("0.1000"))
        await XH.run_cycle(conn)
        inv = [e for e in await SPO.events(conn, group_id=GID)
               if e["event_kind"] == "FLOOR_INVALIDATED"]
        assert inv, await SH.event_kinds(conn)
        ev = inv[0]["evidence"]
        assert ev["because"] == "THE_FEE_SCHEDULE_CHANGED"
        assert ev["classified_under"] == plan["fee_schedule_identity"]
        assert ev["now"]["theta_taker"] == "0.1000"
        assert oid in venue.cancels
        assert venue.state(oid)["state"] == "ORDER_STATE_CANCELED"
    finally:
        await SH.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_i_an_unsupported_settlement_treatment_invalidates_the_floor(
        monkeypatch):
    """The venue's settlement prose changes: a cancelled game now resolves
    'at the discretion of the exchange' -- a treatment this system does not
    support. That is NEVER a refund and never 50 cents: the cancellation cell
    is unknown, the structure is no longer established, and the standing
    order's floor classification is invalidated (the settlement identity it
    was classified under no longer holds) and the order cancelled. The floor
    classifier, given that leg, can at best call the pair conditional on
    ordinary settlement."""
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        venue, oid, out1 = await _place(conn, monkeypatch)
        monkeypatch.setattr(
            XH, "PROSE", "Resolves on the final score and includes any extra "
            "innings played. If the game is cancelled the exchange will "
            "determine the resolution at its discretion.")
        L.rules_cache_reset()
        await XH.run_cycle(conn)
        inv = [e for e in await SPO.events(conn, group_id=GID)
               if e["event_kind"] == "FLOOR_INVALIDATED"]
        assert inv, await SH.event_kinds(conn)
        because = {e["evidence"]["because"] for e in inv}
        assert because & {"THE_SETTLEMENT_RULES_AS_READ_CHANGED",
                          "THE_INSTRUMENT_IS_NO_LONGER_AN_ESTABLISHED_"
                          "STRUCTURE_WITH_THE_HELD_LEG"}, because
        assert oid in venue.cancels
        del out1
    finally:
        await SH.clean(conn)
        await conn.close()


def test_i_the_floor_classes_on_their_own_numbers():
    """PURE CHECKS of the classifier on the XH legs (SYNTHETIC): a 60c hedge
    on a 50c primary costs more than $1 a pair and can never be a positive
    floor; an unknown cancellation treatment is never a refund; a quantity
    that is not a whole contract, or an unpriced fee, is not a floor."""
    from sportsassets import bettor_indirect_structures as IS
    held, hedge = _legs(IS, void_prose=(
        "If the game is cancelled all stakes are refunded."))
    kw = dict(held_leg=held, hedge_leg=hedge, primary_qty=10,
              primary_cost_usd=5.0, entry_fees_usd=0.17, hedge_qty=10,
              sport_permits_tie=False)
    sixty = SPO.classify_floor(hedge_cost_price=0.60, hedge_fee_usd_=0.17,
                               **kw)
    assert sixty["floor_class"] == SPO.F_CONTROLLED
    assert sixty["costs"]["total_cost_usd"] > 10.0          # > $1 a pair
    ok = SPO.classify_floor(hedge_cost_price=0.40, hedge_fee_usd_=0.17, **kw)
    assert ok["floor_class"] == SPO.F_ORDINARY             # VOID nets -fees
    assert ok["ordinary_margin_usd"] > 0
    assert any(v < 0 for v in ok["extraordinary_margins"].values())
    # a fixture that cannot void: every established state is positive
    allst = SPO.classify_floor(hedge_cost_price=0.40, hedge_fee_usd_=0.17,
                               fixture_can_void=False, **kw)
    assert allst["floor_class"] == SPO.F_ALL
    # an UNKNOWN cancellation treatment: never a refund, never 50c
    h2, g2 = _legs(IS, void_prose=(
        "If the game is cancelled the exchange decides."))
    unk = SPO.classify_floor(hedge_cost_price=0.40, hedge_fee_usd_=0.17,
                             **dict(kw, held_leg=h2, hedge_leg=g2))
    assert unk["floor_class"] == SPO.F_ORDINARY
    assert unk["extraordinary_unknown"], unk
    void_row = next(r for r in unk["regions"] if r["state"] == "VOID")
    assert void_row["payout_usd"] is None
    assert void_row["worst_payout_usd"] == 0.0              # valued at 0
    # not a whole contract / fee not priced / hedge above primary
    assert SPO.classify_floor(hedge_cost_price=0.40, hedge_fee_usd_=0.17,
                              **dict(kw, hedge_qty=2.5))["floor_class"] \
        == SPO.F_NONE
    assert SPO.classify_floor(hedge_cost_price=0.40, hedge_fee_usd_=None,
                              **kw)["floor_class"] == SPO.F_NONE
    assert SPO.classify_floor(hedge_cost_price=0.40, hedge_fee_usd_=0.17,
                              **dict(kw, hedge_qty=11))["floor_class"] \
        == SPO.F_NONE


def _legs(IS, *, void_prose):
    """The XH moneyline and NYY +1.5 legs, built from prose (SYNTHETIC)."""
    from fractions import Fraction
    common = dict(fixture_id=XH.EVENT, period="FULL_GAME",
                  overtime=IS.OT_INCLUDED, settlement_text_captured=True,
                  tie_rule="A tie resolves 50-50.",
                  void_rule=void_prose)
    held = IS.Leg(condition_id=XH.HELD + "#ORDER_INTENT_BUY_LONG",
                  kind=IS.KIND_MONEYLINE, backs="A", quantity=10,
                  cost_cents_per_unit=50, **common)
    hedge = IS.Leg(condition_id=XH.SIB + "#ORDER_INTENT_BUY_SHORT",
                   kind=IS.KIND_SPREAD, backs="B", line=Fraction(-3, 2),
                   quantity=10, cost_cents_per_unit=40, **common)
    return held, hedge


# ════════════════════════════════════════════════════════════════════
# (j) THE WORKSPACE, ITS PAGE AND AUDREY'S AUDIT SHOW IT TRUTHFULLY
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_j_the_workspace_page_and_audrey_show_standing_protection(
        monkeypatch):
    """After a standing order rests and 3 of its 10 fill: Xavier's workspace
    (the JSON route, its standing-orders route and the rendered page) and
    Audrey's daily audit each show the SELECTED instrument, 3 COVERED / 7
    UNCOVERED, the RESTING order kept apart from the filled protection with
    its 7 FILL-CAPABLE, the LIFECYCLE state, the FLOOR CLASS and the venue
    CAPABILITY flag with the strict fallback's disclosure -- each read from
    the records, nothing recomputed."""
    from sportsassets.agents import audrey_audit as AA
    from sportsassets.agents import xavier_standing_view as XSV
    from sportsassets.api import agents_xavier as AX
    from tests import test_agent_workspaces_render_the_contract as RC
    from tests import test_xavier_workspace_reads_truthfully as WT
    t0 = time.time()
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        (plan,) = await SH.plans(conn)
        venue.fill(oid, 3)
        await XH.run_cycle(conn)
        ws = await AX.workspace(conn)
        pos = ws["sections"]["positions"]
        assert pos["status"] == AX.OK, pos
        (g,) = [x for x in pos["data"]["groups"] if x["group"] == GID]
        sp = g["standing_protection"]
        assert sp["selected_instrument"]["candidate_id"] == \
            plan["candidate_id"]
        assert sp["selected_instrument"]["selected_by"] == "FIRST_FILL"
        assert sp["filled_protection"]["covered_qty"] == 3.0
        assert sp["filled_protection"]["uncovered_qty"] == 7.0
        (r,) = sp["resting_orders"]
        assert r["fill_capable_qty"] == 7.0 and r["filled_qty"] == 3.0
        assert r["lifecycle_state"] == SPO.L_PARTIAL
        assert sp["resting_orders_are"].startswith("OBLIGATIONS")
        assert sp["filled_protection"]["is"] == \
            "HEDGE CONTRACTS THE BOOK HOLDS"
        assert sp["fill_capable_qty"] == 7.0
        assert sp["lifecycle_state"] == SPO.L_PARTIAL
        assert sp["floor_class"] == SPO.F_ORDINARY
        assert sp["plan"]["floor_condition"]
        assert sp["exchange_linked_exclusivity"] == \
            "UNAVAILABLE_OR_UNVERIFIED"
        assert sp["mode"] == "STRICT_FALLBACK"
        assert "queue position" in sp["latency_disclosure"]
        assert sp["invariant"]["holds"] is True
        assert sp["payout_table_actual_inventory"]["hedge_qty"] == 3.0
        ver = ws["sections"]["versions"]["data"]["standing_order_policy"]
        assert ver["source"] == "ACTIVE_POLICY"
        assert ver["params"]["enabled"] is True
        assert ver["exchange_linked_exclusivity"] == \
            "UNAVAILABLE_OR_UNVERIFIED"
        # ── THE ROUTE AND THE STANDING-ORDERS ROUTE ──────────────────
        WT._pool(monkeypatch)
        c = WT._client(monkeypatch)
        assert c.get("/api/command/agents/xavier/standing-orders"
                     ).status_code == 401
        got = c.get("/api/command/agents/xavier/standing-orders",
                    headers=WT.AUTH)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["read_only"] is True
        assert body["exchange_linked_exclusivity"] == \
            "UNAVAILABLE_OR_UNVERIFIED"
        (bg,) = body["groups"]
        assert bg["filled_protection"]["covered_qty"] == 3.0
        assert bg["resting_orders"][0]["fill_capable_qty"] == 7.0
        # ── THE PAGE RENDERS IT (the page's own JavaScript, under node) ─
        html = RC._render("xavier", json.loads(json.dumps(ws, default=str)))
        st, card = RC._card(html, "positions")
        assert st == "OK"
        for text in ("Standing protection", "UNAVAILABLE_OR_UNVERIFIED",
                     "STRICT_FALLBACK", plan["candidate_id"],
                     "Resting orders", "obligations, not protection",
                     SPO.L_PARTIAL, SPO.F_ORDINARY,
                     "Covered (filled protection)", "Fill-capable"):
            assert text in card, text
        # ── AUDREY'S AUDIT ───────────────────────────────────────────
        au = await AA.standing_orders_section(conn, start=t0 - 60,
                                              end=time.time() + 60)
        assert au["available"] is True and not au["findings"], au
        (ag,) = [x for x in au["groups"] if x["group_id"] == GID]
        assert ag["invariant_holds_now"] is True
        assert ag["at_most_one_live_order"] is True
        assert ag["releases_only_on_terminal"] is True
        assert ag["selected_instrument"]["candidate_id"] == \
            plan["candidate_id"]
        assert ag["covered_qty"] == 3.0 and ag["uncovered_qty"] == 7.0
        assert ag["resting_orders"][0]["fill_capable_qty"] == 7.0
        assert ag["fill_capable_qty"] == 7.0
        assert ag["lifecycle_state"] == SPO.L_PARTIAL
        assert ag["floor_class"] == SPO.F_ORDINARY
        assert ag["exchange_linked_exclusivity"] == \
            "UNAVAILABLE_OR_UNVERIFIED"
        tz_name, tz, note = AA.audit_timezone()
        rep = await AA.build_report(conn, day=AA.local_day(time.time(), tz),
                                    tz_name=tz_name, tz=tz, tz_note=note)
        assert any(x["group_id"] == GID
                   for x in rep["standing_orders"]["groups"]), \
            rep["standing_orders"]
        acct = await XSV.account_view(conn, account_id=XH.ACCT,
                                      venue=XH.VENUE)
        assert acct["groups"][0]["lifecycle_state"] == SPO.L_PARTIAL
    finally:
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# REQUIREMENT 8: EVENTS TRIGGER THE SAME GROUP AUTHORITY
# ════════════════════════════════════════════════════════════════════

def _book_msg(slug, *, bid, ask, state=None):
    lv = lambda p: {"px": {"value": "%.2f" % p, "currency": "USD"},  # noqa
                    "qty": "10"}
    md = {"marketSlug": slug, "bids": [lv(bid)], "offers": [lv(ask)]}
    if state is not None:
        md["state"] = state
    return {"requestId": "ws-md", "subscriptionType":
            "SUBSCRIPTION_TYPE_MARKET_DATA", "marketData": md}


def _event_step(got):
    fs = ((got or {}).get("pass") or {}).get("funded_service") or {}
    return XH.step_of({"funded_servicing": fs}) or {}


@pg
@pytest.mark.asyncio
async def test_events_price_touch_is_not_a_fill_and_every_event_revalues(
        monkeypatch):
    """A MARKET UPDATE on the hedge's market that touches our limit is not a
    fill: it is recorded as such and nothing is ingested. It runs the SAME
    servicing pass (execution lock, group lock) -- not a second loop -- and
    that pass re-values the standing order against HOLD under the active
    Xavier policy, on the event. A market SUSPENSION on the next event makes
    the same pass cancel the order, and while the market is suspended the
    confirmed-terminal order is not replaced. The 60-second servicing pass
    remains the backstop: the same state is reached by `cycle()` alone."""
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        (plan,) = await SH.plans(conn)
        wire = float(plan["wire_limit_price"])
        slug = plan["venue_slug"]
        # 1 · a book that touches our limit: NOT a fill; the pass re-values
        got = await L.on_market_message(conn, _book_msg(
            slug, bid=wire, ask=0.90))
        assert got["ok"] and got["relevant"] and got["pass"]["ran"]
        assert got["pass"]["source"] == L.SOURCE_VENUE_EVENT
        kinds = await SH.event_kinds(conn)
        assert "PRICE_TOUCH_IS_NOT_A_FILL" in kinds
        assert await SH.ledger_hedge_filled(conn) == 0.0
        assert (await SH.group(conn))["fill_capable_qty"] == 10.0
        so = _event_step(got)["standing_order"]
        val = so["valuation"]
        assert val["ok"] is True and val["desirable"] is True
        assert val["increment_vs_hold_usd"] > 0
        assert val["policy_choice"]["selected"] == SPO.ACTION
        assert val["policy_choice"]["selection_rule"] == "EXPECTED_NET_VALUE"
        assert len(_hedge_creates(venue)) == 1
        # 2 · the market is SUSPENDED: the event's pass cancels the order
        got = await L.on_market_message(conn, _book_msg(
            slug, bid=0.31, ask=0.90, state="MARKET_STATE_SUSPENDED"),
            now=time.time() + 2)
        assert got["pass"]["ran"] is True
        sus = [e for e in await SPO.events(conn, group_id=GID)
               if e["event_kind"] == "MARKET_SUSPENDED_OR_CLOSED"]
        assert sus and sus[0]["source"] == "VENUE_MARKET_EVENT"
        assert oid in venue.cancels
        # 3 · the backstop pass confirms it terminal; nothing is re-placed
        #     while the market is suspended
        for _ in range(2):
            await XH.run_cycle(conn)
        gs = await _assert_invariant(conn, venue)
        assert gs["live_or_potentially_live_orders"] == 0
        assert "TERMINAL_CONFIRMED" in await SH.event_kinds(conn)
        assert len(_hedge_creates(venue)) == 1
    finally:
        await SH.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_events_revalue_under_the_capital_preservation_policy(
        monkeypatch):
    """THE SAME RE-EVALUATION UNDER CAPITAL_PRESERVATION_V1. The owner
    activates Xavier's capital-preservation policy (a $1.00 expected-value
    sacrifice); the next event re-values the standing order with THAT
    policy's own rule (`agents.xavier_policy.apply`) and records it."""
    from sportsassets.agents import xavier_policy as XP
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        got = await XP.activate_version(
            conn, version="CP-STANDING-TEST", params={
                "selection_rule": XP.SEL_CAPITAL_PRESERVATION,
                "max_ev_sacrifice_for_downside_usd": 1.0},
            approved_by=SH.APPROVER, created_by="standing-order-test")
        assert got["ok"], got
        (plan,) = await SH.plans(conn)
        await XH.probability(conn, p=0.80)
        # TEST ISOLATION: the previous test's last market-event pass on this
        # slug is stamped time.time() + 2 in the process-wide per-slug gap
        # map, so whether THIS event runs its pass depended on how fast the
        # two tests happened to run (MARKET_EVENT_MIN_GAP_S). The gap rule is
        # unchanged; only the other test's stamp is cleared.
        L._VENUE_EVENTS["last_market_pass"].pop(plan["venue_slug"], None)
        got = await L.on_market_message(conn, _book_msg(
            plan["venue_slug"], bid=0.31, ask=0.90))
        val = _event_step(got)["standing_order"]["valuation"]
        pc = val["policy_choice"]
        assert pc["selection_rule"] == XP.SEL_CAPITAL_PRESERVATION
        assert pc["capital_preservation"]["enabled"] is True
        assert pc["capital_preservation"]["value"] == 1.0
        assert val["worst_case_pair_usd"] > val["worst_case_hold_usd"]
        assert pc["selected"] == SPO.ACTION and val["desirable"] is True
        assert oid not in venue.cancels
        assert (await SH.group(conn))["lifecycle_state"] == SPO.L_RESTING
    finally:
        await conn.execute(
            "DELETE FROM agent_policy_versions WHERE agent_id='XAVIER' "
            "   AND policy_key=$1", XP.POLICY_KEY)
        await SH.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# REQUIREMENTS 4 AND 7: SWITCHING, EXPIRY, AUTHORIZATION, SETTLEMENT
# ════════════════════════════════════════════════════════════════════

UNSUPPORTED_PROSE = ("Resolves on the final score and includes any extra "
                     "innings played. If the game is cancelled the exchange "
                     "will determine the resolution at its discretion.")


@pg
@pytest.mark.asyncio
async def test_lifecycle_a_switch_is_cancel_then_terminal_then_replacement(
        monkeypatch):
    """The instrument the standing order rests on stops being an
    established structure (ITS market's settlement prose changes). The
    switch to the other instrument follows the strict sequence: cancel ->
    the venue's TERMINAL read, reconciled -> capacity released -> only then
    the replacement on the other instrument, recorded as an evaluated
    transition with the latency / queue trade-off. At no instant do two
    hedge orders live."""
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        (plan,) = await SH.plans(conn)
        other_slug = SH.SIB2 if plan["venue_slug"] == XH.SIB else XH.SIB
        venue.prose_by_slug[plan["venue_slug"]] = UNSUPPORTED_PROSE
        L.rules_cache_reset()
        await XH.run_cycle(conn)
        assert oid in venue.cancels
        assert len(_hedge_creates(venue)) == 1      # nothing yet: not terminal
        await _assert_invariant(conn, venue)
        L.rules_cache_reset()
        await XH.run_cycle(conn)
        creates = _hedge_creates(venue)
        assert len(creates) == 2, venue.sent
        assert creates[1]["marketSlug"] == other_slug
        kinds = await SH.event_kinds(conn)
        assert kinds.index("TERMINAL_CONFIRMED") < kinds.index(
            "INSTRUMENT_TRANSITION_EVALUATED") < len(kinds)
        tr = [e for e in await SPO.events(conn, group_id=GID)
              if e["event_kind"] == "INSTRUMENT_TRANSITION_EVALUATED"][0]
        assert tr["evidence"]["from"] == plan["candidate_id"]
        assert tr["evidence"]["previous_order_terminal_and_reconciled"]
        assert "queue position" in tr["evidence"]["latency_disclosure"]
        await _assert_invariant(conn, venue)
    finally:
        await SH.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_lifecycle_a_venue_expired_gtd_order_is_replaced_after_its_terminal_read(
        monkeypatch):
    """Every standing order goes out GOOD_TILL_DATE with a goodTillTime (a
    rest a dead process cannot cancel expires at the venue). The venue
    expires it; the next pass reads EXPIRED (terminal, reconciled), releases
    the capacity and places a replacement with a new expiry."""
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        first = venue.rest_creates()[0]
        assert first["tif"] == SH.GTD and first["goodTillTime"]
        (plan,) = await SH.plans(conn)
        assert plan["tif"] == SH.GTD and plan["good_till_time"] is not None
        venue.expire(oid)
        await XH.run_cycle(conn)
        creates = venue.rest_creates()
        assert len(creates) == 2, venue.sent
        assert creates[1]["tif"] == SH.GTD and creates[1]["goodTillTime"]
        ps = await SH.plans(conn)
        assert len(ps) == 2
        rel = await conn.fetch(
            "SELECT release_reason FROM bettor_standing_capacity_reservations"
            " WHERE plan_id=$1", ps[0]["plan_id"])
        assert rel[0]["release_reason"] == "TERMINAL_CONFIRMED_BY_THE_VENUE"
        await _assert_invariant(conn, venue)
    finally:
        await SH.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_lifecycle_a_revoked_authorization_cancels_and_places_nothing(
        monkeypatch):
    """The system authorization the order was placed under is revoked: the
    next pass asks the venue to cancel the standing order, and once it is
    terminal nothing replaces it."""
    from sportsassets import bettor_funded_activation as FA
    conn = await _connect()
    try:
        venue, oid, _ = await _place(conn, monkeypatch)
        rec = FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY))
        rec["revoked"] = True
        await conn.execute(
            "UPDATE ingestion_state SET value=$2::jsonb WHERE key=$1",
            FA.AUTHORIZATION_KEY, json.dumps(rec))
        await XH.run_cycle(conn)
        assert "AUTHORIZATION_EXPIRED" in await SH.event_kinds(conn)
        assert oid in venue.cancels
        for _ in range(2):
            await XH.run_cycle(conn)
        gs = await _assert_invariant(conn, venue)
        assert gs["live_or_potentially_live_orders"] == 0
        assert len(_hedge_creates(venue)) == 1
    finally:
        await SH.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_lifecycle_a_settled_primary_leaves_no_standing_order(
        monkeypatch):
    """The primary SETTLES while the standing order rests. The primary is no
    longer an open position -- and the order is still Xavier's: the pass's
    sweep maintains its group under the group lock, cancels it
    (PRIMARY_CLOSED) and releases its capacity only on the terminal read."""
    conn = await _connect()
    try:
        await SH.start(conn)
        venue = SH.RestingVenue(books=SH.books(hedge2_bid=0.30),
                                holdings={XH.HELD: (10.0, 5.0)})
        table: dict = {}
        XH.substitute(monkeypatch, venue, settlements=table)
        await XH.run_cycle(conn)
        oid = [o for o in venue.orders if o.startswith("venue-rest-")][0]
        table[XH.HELD] = XH._settled(1.0)
        await XH.run_cycle(conn)
        assert await conn.fetchval(
            "SELECT closed_reason FROM bettor_funded_intents "
            " WHERE intent_id=$1", XH.HELD_ID) == "SETTLED_BY_THE_VENUE"
        assert "PRIMARY_CLOSED" in await SH.event_kinds(conn)
        assert oid in venue.cancels
        await XH.run_cycle(conn)
        gs = await SH.group(conn)
        assert gs["live_or_potentially_live_orders"] == 0
        assert not gs["reserved"]
        assert "TERMINAL_CONFIRMED" in await SH.event_kinds(conn)
        assert len(_hedge_creates(venue)) == 1
    finally:
        await SH.clean(conn)
        await conn.close()
