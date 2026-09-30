"""AN ACTION IS VALUED, SIZED AND RANKED ONLY ON DEPTH ITS OWN LIMIT CAN REACH.

THE DEFECT (XC, reproduced). A hedge displayed at a COST of 0.985 -- a SHORT
against a 0.015 bid on a market whose own `orderPriceMinTickSize` is 0.005 --
was valued, sized and ranked on that bid's depth. The order built for it went
out with a 0.02 wire limit: `pmus._amount` formats every price "%.2f", and
`safe_cent` rounds a short's wire UP so the order can never pay more than
0.98. That rounding is right about cost and silent about reach -- a SELL at
0.02 does not trade a bid at 0.015 -- so the action won on liquidity the order
actually sent could never touch. On the long side an offer at 0.985 was
floored to a 0.98 buy limit, which cannot lift it. The leg was even valued at
98 cents (`build_leg` rounds the cost to a whole cent), i.e. at a price nobody
offered.

THE REPAIR, AS PROVED HERE. Every ladder the funded lane values (the entry's
and the hedge candidate's acquisition ladders, the held position's exit
ladder) keeps only levels whose wire price is a multiple of BOTH the market's
own tick and the adapter's whole cent; the rest is excluded before valuation
and sizing and reported by name with its prices and quantities. The plan's
limit is the exact wire price of the worst counted level, the adapter payload
carries exactly it, and every counted level is reachable by it. A market
whose tick was not read is refused by name. Nothing is rounded upward to
recover depth.

WHAT RUNS. `ext_pinnacle_loop.cycle()` through the scheduled harness of
`test_xavier_manages_positions_through_the_scheduled_path` (the real writers,
rankings, valuations, Xavier's record and the bound-plan dispatch); ONLY the
venue transport is substituted, and the submission switches are patched True
inside these tests only, with the fake adapter recording what it was asked to
send. The fake venue publishes each market's tick on its listing row, as the
real one does.

ALL MARKET DATA HERE IS SYNTHETIC -- books, ticks, prose, probabilities and the
approved conditional model's training observations. NOTHING HERE IS EVIDENCE
ABOUT ANY MARKET.
"""
from __future__ import annotations

import datetime as _dt
import os
from decimal import Decimal

import pytest

from sportsassets import bettor_book_snapshot as BS
from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_decision as FD
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from sportsassets import bettor_xavier as XV
from tests import test_xavier_manages_positions_through_the_scheduled_path as XC

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

LABEL = "SYNTHETIC -- ENGINEERING DEMONSTRATION, NOT EVIDENCE ABOUT ANY MARKET"

HELD, SIB, ACCT, HELD_ID = XC.HELD, XC.SIB, XC.ACCT, XC.HELD_ID
SHORT, LONG = "ORDER_INTENT_BUY_SHORT", "ORDER_INTENT_BUY_LONG"
#: The NYY +1.5 side of the run line. In the SHORT variant it is the
#: instrument's SHORT side (cost 1 - bid, consuming the bids); in the LONG
#: variant the catalogue names it the LONG side (cost = offer, consuming the
#: offers). Same leg, same payout, opposite side of one book.
HEDGE_SHORT = SIB + "#" + SHORT
HEDGE_LONG = SIB + "#" + LONG

#: HOLD's forward probability for the Red Sox moneyline (SYNTHETIC), at the
#: floor of the source's declared support. The NYY +1.5 then pays unless the
#: Red Sox win by two or more: 0.98 + 0.02 x P(hedge | Red Sox win), where the
#: approved (SYNTHETIC) conditional model supplies the second factor. On a
#: held basis of 0.10 that makes a 0.98-cost hedge worth a few cents over
#: HOLD -- the regime in which a 98.5-cent-class hedge can win at all.
P_HOLD = 0.02
HELD_PRICE = 0.10
#: The held moneyline's bids: one representable level worth less than
#: holding, so a DIRECT_EXIT is on the record and loses on its number.
HELD_BIDS = [(0.01, 400)]


def _lvl(p, q):
    """A level as the venue publishes it -- to the tick, NOT to the cent.
    The harness's own formatter prints "%.2f", which would turn the 0.015 bid
    this file is about into a cent before the lane ever saw it."""
    return {"px": {"value": str(Decimal(str(p))), "currency": "USD"},
            "qty": str(q)}


async def _long_catalogue(conn):
    """The LONG variant: the same two run-line rows with their intents
    swapped, so the NYY +1.5 is acquired by BUYING THE OFFERS."""
    await conn.execute(
        "UPDATE us_premap SET intent = CASE WHEN intent=$2 THEN $3 ELSE $2 END"
        " WHERE market_slug=$1", SIB, SHORT, LONG)


async def _run(conn, monkeypatch, *, p=P_HOLD, held_bids=HELD_BIDS,
               sib_bids=(), sib_offers=(), ticks=None, side=SHORT,
               approve_model=True, with_sib=True, price=HELD_PRICE):
    await XC.start(conn, p=p, approve_model=approve_model, price=price)
    if side == LONG:
        await _long_catalogue(conn)
    books = {HELD: {"bids": list(held_bids), "offers": [(0.99, 5)]}}
    if with_sib:
        books[SIB] = {"bids": list(sib_bids), "offers": list(sib_offers)}
    venue = XC.Venue(books=books, holdings={HELD: (10.0, 10.0 * price)})
    venue.ticks.update(ticks or {})
    monkeypatch.setattr(XC, "_lvl", _lvl)
    XC.substitute(monkeypatch, venue)
    out = await XC.run_cycle(conn)
    rec = (await XC.xavier_records(conn))[0]
    return out, venue, rec


def _fee(qty, price):
    """The fee the lane charges an acquisition: the dated schedule in force
    today, at the sport's own taker coefficient (baseball)."""
    from sportsassets import bettor_fee_schedule as FS
    from sportsassets import calibration_fees as CF
    when = _dt.datetime.now(_dt.timezone.utc)
    sched = FS.for_date(when.date().isoformat())
    theta = CF.taker_coefficient("baseball", at=when.isoformat())
    return float(sched.exact(theta, int(qty), float(price)))


def _grid_of(rec, cid):
    return rec["evidence"]["executable_grid"]["hedge_candidates"][cid]


def _excluded(view):
    return sorted((round(float(x["api_price"]), 6), float(x["qty"]))
                  for x in view.get("levels_excluded_unrepresentable") or [])


def _search_refusals(rec):
    return next((a for a in rec["alternatives"]
                 if a.get("blocker") == "HEDGE_SEARCH_REFUSALS"), {})


# ════════════════════════════════════════════════════════════════════
# (a) THE 98.5-CENT HEDGE, BOTH SIDES: SIZED ONLY ON THE 0.98 DEPTH
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("side,tick", [(SHORT, "0.005"), (LONG, "0.005"),
                                       (SHORT, "0.001"), (LONG, "0.001")])
async def test_a_the_hedge_is_valued_and_sized_only_on_its_representable_depth(
        monkeypatch, side, tick):
    """The run line shows a hedge COST of 0.98 for 4 contracts and 0.985 for
    20 (SHORT: bids 0.02 / 0.015; LONG: offers 0.98 / 0.985) on a market whose
    tick is `tick`. 10 are wanted. The 0.985 depth is excluded by name before
    valuation; the hedge is valued, fee'd and sized on the 4 at 0.98 alone,
    with 6 left uncovered; it wins, and the adapter receives exactly the
    valued limit and quantity -- which trade every counted level."""
    cid = HEDGE_SHORT if side == SHORT else HEDGE_LONG
    if side == SHORT:
        book = {"sib_bids": [(0.02, 4), (0.015, 20)],
                "sib_offers": [(0.90, 500)]}
        wire, off_grid = 0.02, 0.015
    else:
        book = {"sib_offers": [(0.98, 4), (0.985, 20)],
                "sib_bids": [(0.10, 500)]}
        wire, off_grid = 0.98, 0.985
    conn = await XC._connect()
    try:
        out, venue, rec = await _run(conn, monkeypatch, side=side,
                                     ticks={SIB: tick}, **book)
        step = XC.step_of(out)
        assert rec["chosen_action"] == "ACQUIRE_HEDGE", (rec["alternatives"],
                                                         step)
        assert rec["execution_eligibility"] == XV.E_DISPATCHED

        # ── THE EXCLUSION, PERSISTED BY NAME BEFORE VALUATION ────────
        view = _grid_of(rec, cid)
        assert view["executable_grid"]["ok"] is True
        assert view["executable_grid"]["tick"] == tick
        assert view["executable_grid"]["step"] == "0.01"
        assert _excluded(view) == [(off_grid, 20.0)]
        assert view["excluded_unrepresentable_qty"] == pytest.approx(20.0)
        assert [(round(float(x["api_price"]), 6), float(x["qty"]))
                for x in view["counted_levels"]] == [(wire, 4.0)]
        assert view["price"] == pytest.approx(0.98)
        assert view["depth_qty"] == pytest.approx(4.0)

        # ── VALUED AND FEE'D ON THE COUNTED 4 AT 0.98, NOTHING ELSE ──
        acq = XC.alternative(rec, FD.ACTION_ACQUIRE_INDIRECT_HEDGE, cid)
        assert acq["rankable"] is True
        assert acq["qty"] == 4
        assert acq["unpaired_residual_qty"] == pytest.approx(6.0)
        assert acq["search_screen"]["floor_survives"]["executable_depth"][
            "depth_qty"] == pytest.approx(4.0)
        assert acq["fees_usd"] == pytest.approx(_fee(4, 0.98), abs=1e-6)
        # not the fee on the wanted 10, nor on any quantity at 0.985
        for q, px in ((10, 0.98), (4, 0.985), (10, 0.985), (24, 0.985)):
            assert abs(acq["fees_usd"] - _fee(q, px)) > 1e-6, (q, px)
        assert acq["capital_required_usd"] == pytest.approx(
            4 * 0.98 + acq["fees_usd"], abs=1e-6)
        hold = XC.alternative(rec, "HOLD")
        assert acq["expected_net_usd"] > hold["expected_net_usd"]

        # ── THE ADAPTER RECEIVES EXACTLY THE VALUED LIMIT AND SIZE ───
        plan, c = XC.assert_sent_the_persisted_plan(venue, rec)
        assert plan["limit_price"] == wire
        assert int(plan["quantity"]) == 4
        assert c["marketSlug"] == SIB and c["intent"] == side
        assert c["price"]["value"] == "%.2f" % wire
        assert Decimal(c["price"]["value"]) == Decimal(str(wire))
        assert int(c["quantity"]) == 4
        # EVERY COUNTED LEVEL IS REACHABLE BY IT (buy: level cost <= limit
        # cost; a short buy sells the contract, so in wire space bid >= limit)
        reach = BS.limit_reaches(
            wire=float(c["price"]["value"]),
            levels=view["counted_levels"],
            side_consumed=BS.SIDE_BID if side == SHORT else BS.SIDE_ASK,
            grid=view["executable_grid"])
        assert reach["ok"] is True, reach
        # AND THE EXCLUDED LEVEL IS NOT: it could never have filled
        miss = BS.limit_reaches(
            wire=float(c["price"]["value"]),
            levels=[{"api_price": off_grid, "qty": 20}],
            side_consumed=BS.SIDE_BID if side == SHORT else BS.SIDE_ASK)
        assert miss["refusal"] == BS.R_COUNTED_LEVEL_UNREACHABLE
        # THE VENUE FILLED ALL 4 AT THE COUNTED LEVEL
        h = await conn.fetchrow(
            "SELECT state, quantity::float8 AS q, limit_price::float8 AS lp "
            "  FROM bettor_funded_intents WHERE account_id=$1 "
            "   AND leg_role='HEDGE'", ACCT)
        assert h["state"] == "FILLED" and h["q"] == 4.0
        assert h["lp"] == pytest.approx(wire)
    finally:
        await XC.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("side", [SHORT, LONG])
async def test_a_with_only_the_985_depth_the_hedge_is_not_rankable_and_hold_wins(
        monkeypatch, side):
    """The run line shows ONLY the 0.985 cost (SHORT: a 0.015 bid; LONG: a
    0.985 offer), 20 deep, on a 0.005-tick market. No order we can send
    reaches it, so the hedge is not rankable -- named -- and HOLD wins;
    nothing is sent."""
    cid = HEDGE_SHORT if side == SHORT else HEDGE_LONG
    book = ({"sib_bids": [(0.015, 20)], "sib_offers": [(0.90, 500)]}
            if side == SHORT else
            {"sib_offers": [(0.985, 20)], "sib_bids": [(0.10, 500)]})
    conn = await XC._connect()
    try:
        out, venue, rec = await _run(conn, monkeypatch, side=side,
                                     ticks={SIB: "0.005"}, **book)
        assert rec["chosen_action"] == "HOLD", rec["alternatives"]
        assert rec["execution_eligibility"] == XV.E_HOLD
        # THE HEDGE IS ON THE RECORD, NOT RANKABLE, WITH THE GRID'S NAME
        assert not [a for a in rec["alternatives"]
                    if a.get("candidate_id") == cid and a.get("rankable")]
        srch = _search_refusals(rec)
        assert srch.get("rankable") is False
        assert srch["refusals_by_name"].get(BS.R_NO_REPRESENTABLE_DEPTH) == 1
        view = _grid_of(rec, cid)
        assert view["refusal"] == BS.R_NO_REPRESENTABLE_DEPTH
        assert _excluded(view) == [(0.015 if side == SHORT else 0.985, 20.0)]
        # NOTHING WAS SENT, PREVIEWED OR RESERVED
        assert venue.creates_sent() == []
        assert [k for k, _ in venue.sent if k == "preview"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1"
            " AND intent_id <> $2", ACCT, HELD_ID) == 0
    finally:
        await XC.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (b) BEFORE THE REPAIR THE UNREACHABLE DEPTH WON; NOW IT DOES NOT
# ════════════════════════════════════════════════════════════════════

def _pre_repair_acquisition_plan_for(*, winner, ranked_row, account_id, venue,
                                     group_id, held_position, fee_usd=None,
                                     inputs_expire_at=None):
    """`acquisition_plan_for` AS IT WAS AT 093168f, reduced to what differs:
    the valued wire went through `safe_cent` and whatever came back was the
    limit -- so a 0.985 cost became a 0.02 wire on a SHORT and a 0.98 limit
    on a LONG. Used ONLY to show the pre-repair ranking in (b)."""
    w, row = dict(winner or {}), dict(ranked_row or {})
    cid = str(w.get("condition_id"))
    _slug, side = PC.HS.split_identity(cid)
    qty, price = row.get("covered_qty"), row.get("price")
    wire = (float(Decimal("1") - Decimal(str(price))) if side == SHORT
            else float(price))
    wire = FX.safe_cent(wire, side)
    return PC.AcquisitionPlan(
        quantity_from="pre-repair", price_from="pre-repair",
        account_id=account_id, venue=venue, group_id=group_id,
        candidate_id=cid, action=PC.ACTION_ACQUIRE, quantity=qty,
        limit_price=wire, collateral_usd=FX.collateral_for(wire, qty, side),
        inputs_expire_at=inputs_expire_at,
        assessed_at=row.get("evidence_age_s"), source="pre-repair")


def _as_before_the_repair(monkeypatch):
    """The three checks this repair added, put back to what they were: no
    exclusion from the ladder, no grid assertion on the quoted price, and the
    plan's limit rounded by `safe_cent` rather than refused."""
    monkeypatch.setattr(BS, "restrict_to_executable", lambda lad, grid: dict(
        lad, executable_grid=grid, executable_grid_applied=True,
        levels_excluded_unrepresentable=[]))
    monkeypatch.setattr(BS, "on_executable_grid", lambda *a, **k: True)
    monkeypatch.setattr(PC, "acquisition_plan_for",
                        _pre_repair_acquisition_plan_for)


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("side", [SHORT, LONG])
async def test_b_the_985_depth_won_the_ranking_before_the_repair_and_now_it_does_not(
        monkeypatch, side):
    """One book: the run line shows ONLY the 0.985 cost, 20 deep, on a
    0.005-tick market. BEFORE (the repair's three checks put back): the hedge
    WINS on that depth, valued as though at 0.98, and the adapter is sent a
    limit (SHORT 0.02 / LONG 0.98) that trades none of it -- the fake venue
    fills nothing. AFTER: the depth is excluded by name, the hedge is not
    rankable, HOLD wins and nothing is sent. The ranking changes."""
    cid = HEDGE_SHORT if side == SHORT else HEDGE_LONG
    book = ({"sib_bids": [(0.015, 20)], "sib_offers": [(0.90, 500)]}
            if side == SHORT else
            {"sib_offers": [(0.985, 20)], "sib_bids": [(0.10, 500)]})
    sent_wire = 0.02 if side == SHORT else 0.98
    conn = await XC._connect()
    try:
        # ── BEFORE ───────────────────────────────────────────────────
        _as_before_the_repair(monkeypatch)
        _out, venue, rec = await _run(conn, monkeypatch, side=side,
                                      ticks={SIB: "0.005"}, **book)
        assert rec["chosen_action"] == "ACQUIRE_HEDGE", rec["alternatives"]
        acq = XC.alternative(rec, FD.ACTION_ACQUIRE_INDIRECT_HEDGE, cid)
        hold = XC.alternative(rec, "HOLD")
        assert acq["expected_net_usd"] > hold["expected_net_usd"]
        # it won on 10 contracts of depth that exists only at 0.985 ...
        assert acq["qty"] == 10
        cr = venue.creates_sent()
        assert len(cr) == 1
        assert cr[0]["intent"] == side and int(cr[0]["quantity"]) == 10
        # ... and was sent at a limit that reaches none of it
        assert float(cr[0]["price"]["value"]) == sent_wire
        assert BS.limit_reaches(
            wire=sent_wire,
            levels=[{"api_price": 0.015 if side == SHORT else 0.985}],
            side_consumed=BS.SIDE_BID if side == SHORT else BS.SIDE_ASK,
        )["refusal"] == BS.R_COUNTED_LEVEL_UNREACHABLE
        h = await conn.fetchrow(
            "SELECT intent_id, state FROM bettor_funded_intents "
            " WHERE account_id=$1 AND leg_role='HEDGE'", ACCT)
        assert h["state"] != "FILLED", dict(h)
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills WHERE intent_id=$1",
            h["intent_id"]) == 0
        before = rec["chosen_action"]
        await XC.clean(conn)
        monkeypatch.undo()

        # ── AFTER ────────────────────────────────────────────────────
        _out, venue, rec = await _run(conn, monkeypatch, side=side,
                                      ticks={SIB: "0.005"}, **book)
        assert (before, rec["chosen_action"]) == ("ACQUIRE_HEDGE", "HOLD")
        assert not [a for a in rec["alternatives"]
                    if a.get("candidate_id") == cid and a.get("rankable")]
        assert _search_refusals(rec)["refusals_by_name"].get(
            BS.R_NO_REPRESENTABLE_DEPTH) == 1
        assert venue.creates_sent() == []
    finally:
        await XC.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (c) EXITS: THE SALE LADDER KEEPS ONLY REACHABLE LEVELS
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_c_an_unrepresentable_best_bid_is_excluded_and_the_exit_is_sized_on_reachable_levels(
        monkeypatch):
    """Held 10 Red Sox at 0.50, HOLD on p = 0.55. The held moneyline, a
    0.005-tick market, bids 0.625 for 4, 0.62 for 3, then 0.40. No sell the
    adapter can carry hits 0.625 (0.63 misses it; 0.62 hits 0.62 and not
    better-priced depth it did not count), so that level is excluded by name.
    The exit is sized on the 3 at 0.62 alone; the order sent carries exactly
    0.62 for 3, and reaches every counted level. (Before the repair the exit
    was sized on the 4 at 0.625 and sent at 0.63, which reaches none of it.)"""
    conn = await XC._connect()
    try:
        out, venue, rec = await _run(
            conn, monkeypatch, p=0.55, price=0.50, approve_model=False,
            with_sib=False, ticks={HELD: "0.005"},
            held_bids=[(0.625, 4), (0.62, 3), (0.40, 400)])
        assert rec["chosen_action"] in ("EXIT", "REDUCE"), rec["alternatives"]
        assert rec["execution_eligibility"] == XV.E_DISPATCHED
        ex = rec["evidence"]["executable_grid"]["exit_ladder"]
        assert ex["executable_grid"]["tick"] == "0.005"
        assert _excluded(ex) == [(0.625, 4.0)]
        assert ex["excluded_unrepresentable_qty"] == pytest.approx(4.0)
        # EVERY EXIT ALTERNATIVE IS SIZED ON THE REACHABLE 3 AT 0.62
        for act in ("DIRECT_EXIT", "REDUCE"):
            a = XC.alternative(rec, act)
            if a.get("rankable"):
                assert a["qty"] == pytest.approx(3.0), a
                assert a["proceeds_per_contract"] == pytest.approx(0.62), a
        plan, c = XC.assert_sent_the_persisted_plan(venue, rec)
        assert plan["limit_price"] == 0.62
        assert c["price"]["value"] == "0.62" and int(c["quantity"]) == 3
        # THE SENT LIMIT REACHES EVERY COUNTED LEVEL (a sell: bid >= limit)
        assert BS.limit_reaches(
            wire=float(c["price"]["value"]),
            levels=[{"api_price": 0.62, "qty": 3}],
            side_consumed=BS.SIDE_BID)["ok"] is True
        # AND IT FILLED ALL 3 AT 0.62
        x = await conn.fetchrow(
            "SELECT state, quantity::float8 AS q FROM bettor_funded_intents "
            " WHERE account_id=$1 AND kind='EXIT'", ACCT)
        assert x["state"] == "FILLED" and x["q"] == 3.0
    finally:
        await XC.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_c_with_no_reachable_bid_every_exit_is_not_rankable_by_name_and_hold_is_decided(
        monkeypatch):
    """The held moneyline bids ONLY 0.625 (0.005 tick). HOLD, which needs no
    fill, is still decided; DIRECT_EXIT and REDUCE are on the record, not
    rankable, with the grid's own blocker; nothing is sent."""
    conn = await XC._connect()
    try:
        out, venue, rec = await _run(
            conn, monkeypatch, p=0.55, price=0.50, approve_model=False,
            with_sib=False, ticks={HELD: "0.005"},
            held_bids=[(0.625, 10)])
        assert rec["chosen_action"] == "HOLD", rec["alternatives"]
        for act in ("DIRECT_EXIT", "REDUCE"):
            a = XC.alternative(rec, act)
            assert a["rankable"] is False
            assert a["blocker"] == BS.R_NO_REPRESENTABLE_DEPTH, a
        ex = rec["evidence"]["executable_grid"]["exit_ladder"]
        assert ex["refusal"] == BS.R_NO_REPRESENTABLE_DEPTH
        assert _excluded(ex) == [(0.625, 10.0)]
        assert venue.creates_sent() == []
    finally:
        await XC.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# (d) AN UNREAD TICK IS REFUSED BY NAME; 0.01 MARKETS ARE UNCHANGED
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_d_a_market_whose_tick_was_not_read_is_refused_by_name(
        monkeypatch):
    """Neither listing row publishes orderPriceMinTickSize. Representability
    is not established, so it is not assumed: the hedge candidate is refused
    and both exits are not rankable, each with the tick's own name. HOLD is
    decided and nothing is sent -- even though the hedge book shows the same
    0.98 depth that wins in (a)."""
    conn = await XC._connect()
    try:
        out, venue, rec = await _run(
            conn, monkeypatch, ticks={SIB: None, HELD: None},
            sib_bids=[(0.02, 4), (0.015, 20)], sib_offers=[(0.90, 500)],
            held_bids=[(0.01, 400)])
        assert rec["chosen_action"] == "HOLD", rec["alternatives"]
        assert _search_refusals(rec)["refusals_by_name"].get(
            BS.R_TICK_NOT_ESTABLISHED) == 2          # both sides of the line
        assert _grid_of(rec, HEDGE_SHORT)["refusal"] == \
            BS.R_TICK_NOT_ESTABLISHED
        for act in ("DIRECT_EXIT", "REDUCE"):
            a = XC.alternative(rec, act)
            assert a["rankable"] is False
            assert a["blocker"] == BS.R_TICK_NOT_ESTABLISHED, a
        assert venue.creates_sent() == []
    finally:
        await XC.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_d_on_a_cent_tick_market_nothing_is_excluded_and_the_counted_level_is_sent(
        monkeypatch):
    """A 0.01-tick run line bidding 0.02 for 4 and 0.01 for 20: every level is
    representable, nothing is excluded, the hedge is valued at its best level
    (0.98 for 4) and sent at exactly 0.02 for 4."""
    conn = await XC._connect()
    try:
        out, venue, rec = await _run(
            conn, monkeypatch, ticks={SIB: "0.01"},
            sib_bids=[(0.02, 4), (0.01, 20)], sib_offers=[(0.90, 500)])
        assert rec["chosen_action"] == "ACQUIRE_HEDGE", rec["alternatives"]
        view = _grid_of(rec, HEDGE_SHORT)
        assert view["executable_grid"]["tick"] == "0.01"
        assert view["levels_excluded_unrepresentable"] == []
        plan, c = XC.assert_sent_the_persisted_plan(venue, rec)
        assert c["price"]["value"] == "0.02" and int(c["quantity"]) == 4
        assert plan["limit_price"] == 0.02
    finally:
        await XC.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# THE SAME INVARIANT, PURE: THE ENTRY PLAN AND THE SHORT-HELD EXIT
# ════════════════════════════════════════════════════════════════════

def _md(*, bids=(), offers=()):
    return {"bids": [_lvl(p, q) for p, q in bids],
            "offers": [_lvl(p, q) for p, q in offers]}


GRID_005 = BS.executable_grid("0.005", source=LABEL)


@pytest.mark.parametrize("intent", [LONG, SHORT])
def test_the_entry_is_sized_on_representable_depth_and_sent_at_its_worst_counted_level(
        intent):
    """An entry walk over a 0.005-tick book (SYNTHETIC): LONG offers 0.60 x
    10, 0.605 x 50, 0.61 x 10; SHORT bids 0.40 x 10, 0.395 x 50, 0.39 x 10
    (the same costs). The 0.605 / 0.395 level is excluded BEFORE the budget
    walk; the walk takes 0.60 then 0.61 (cost space); the plan's limit is the
    worst counted level's own wire price (LONG 0.61, SHORT 0.39) -- not the
    break-even floored or ceiled -- and it reaches both counted levels."""
    md = (_md(offers=[(0.60, 10), (0.605, 50), (0.61, 10)])
          if intent == LONG else
          _md(bids=[(0.40, 10), (0.395, 50), (0.39, 10)]))
    lad = BS.restrict_to_executable(
        BS.acquisition_ladder(md, intent=intent), GRID_005)
    assert lad["ok"] is True
    assert [round(x["acquisition_price"], 6) for x in lad["levels"]] == [
        0.60, 0.61]
    assert [(x["api_price"], x["qty"])
            for x in lad["levels_excluded_unrepresentable"]] == [
        (0.605 if intent == LONG else 0.395, 50.0)]
    est = EX.estimate(ladder=lad, fair_value=0.70,
                      fee_fn=lambda *, qty, price: 0.0,
                      intended_notional_usd=12.1)
    assert est["ok"] is True, est
    assert [round(x["price"], 6) for x in est["levels_taken"]] == [0.60, 0.61]
    assert est["size"] == pytest.approx(20.0)
    assert est["executable_limit_price"] == pytest.approx(0.61)
    rec = {"admissible": True, "refusals": [], "us_market_slug": "m-synth",
           "event_key": "ev-synth", "order_intent": intent,
           "execution_plan": {"execution": est}}
    plan = FX.plan_from_decision(rec)
    assert plan["ok"] is True, plan
    wire = 0.61 if intent == LONG else 0.39
    assert plan["limit_price"] == wire
    assert plan["quantity"] == 20
    assert plan["rounded"]["direction"] == "NONE"
    assert plan["counted_levels"]["reach"]["ok"] is True
    # the break-even (0.70) floored/ceiled would have been a different order
    assert FX.safe_cent(0.70 if intent == LONG else 0.30, intent) != wire


def test_an_entry_whose_counted_level_would_be_rounded_is_refused_by_name():
    """A decision whose walk counted a 0.605 level (a ladder that did NOT pass
    through the grid) cannot be sent without moving the limit: refused by
    name, nothing rounded."""
    est = {"size": 10, "vwap": 0.605, "limit_price": 0.70,
           "executable_limit_price": 0.605,
           "levels_taken": [{"price": 0.605, "qty": 10, "cost": 6.05}]}
    plan = FX.plan_from_decision(
        {"admissible": True, "refusals": [], "us_market_slug": "m-synth",
         "event_key": "ev-synth", "order_intent": LONG,
         "execution_plan": {"execution": est}})
    assert plan["ok"] is False
    assert plan["refusal"] == FX.R_LIMIT_WOULD_BE_ROUNDED


def test_a_short_held_exit_keeps_only_offers_a_buy_can_reach():
    """The mirror of (c) for a held SHORT: closing buys the long contract off
    the OFFERS, receiving 1 - offer. A 0.375 offer (proceeds 0.625) on a
    0.005 market is excluded; the exit is priced on the 0.38 offer (proceeds
    0.62), and the limit sent -- 0.38, exactly -- reaches it."""
    lad = BS.restrict_to_executable(
        BS.exit_ladder(_md(offers=[(0.375, 4), (0.38, 3)]),
                       held_intent=SHORT), GRID_005)
    assert lad["ok"] is True
    assert lad["best_exit_price"] == pytest.approx(0.62)
    assert lad["best_api_price"] == 0.38 and lad["size_at_best"] == 3.0
    assert [(x["api_price"], x["qty"])
            for x in lad["levels_excluded_unrepresentable"]] == [(0.375, 4.0)]
    ranked = {"marginal_sale": {}}
    terms = FM._exit_terms("DIRECT_EXIT", 3.0, lad, ranked, SHORT)
    assert terms["ok"] is True, terms
    assert terms["limit_price"] == 0.38
    assert BS.limit_reaches(wire=terms["limit_price"],
                            levels=[{"api_price": 0.38}],
                            side_consumed=lad["side_consumed"])["ok"] is True
    # A LEVEL THAT SLIPPED PAST THE GRID IS REFUSED, NOT ROUNDED
    raw = BS.exit_ladder(_md(offers=[(0.375, 4)]), held_intent=SHORT)
    bad = FM._exit_terms("DIRECT_EXIT", 4.0, raw, ranked, SHORT)
    assert bad["ok"] is False
    assert bad["refusal"] == FM.R_EXIT_LIMIT_WOULD_BE_ROUNDED


def test_the_grid_is_the_venue_tick_and_the_adapter_cent_together():
    assert BS.executable_grid("0.01")["step"] == "0.01"
    assert BS.executable_grid("0.005")["step"] == "0.01"
    assert BS.executable_grid("0.001")["step"] == "0.01"
    assert BS.executable_grid("0.02")["step"] == "0.02"
    for missing in (None, "", BS.NOT_IDENTIFIED):
        assert BS.executable_grid(missing)["refusal"] == \
            BS.R_TICK_NOT_ESTABLISHED
    for bad in ("0", "1", "-0.01", "abc"):
        assert BS.executable_grid(bad)["refusal"] == BS.R_TICK_UNUSABLE
    g = BS.executable_grid("0.005")
    assert BS.on_executable_grid(0.98, g) and BS.on_executable_grid("0.02", g)
    assert not BS.on_executable_grid(0.985, g)
    assert not BS.on_executable_grid(0.015, g)
    # AN UNREAD TICK REFUSES THE LADDER; IT IS NEVER A CENT BY DEFAULT
    lad = BS.restrict_to_executable(
        BS.acquisition_ladder(_md(offers=[(0.60, 10)]), intent=LONG),
        BS.executable_grid(None))
    assert lad["ok"] is False and lad["levels"] == []
    assert lad["refusal"] == BS.R_TICK_NOT_ESTABLISHED
