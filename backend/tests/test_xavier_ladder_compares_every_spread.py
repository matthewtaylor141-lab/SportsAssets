"""XAVIER'S SPREAD LADDER, THROUGH THE REAL SCHEDULED PATH (proofs 4-8).

WHAT RUNS. `ext_pinnacle_loop.cycle(conn)` -- the scheduled servicing pass:
the production pair-input supplier (`candidate_legs_for`, the sibling
search), `discover`, `rank_admitted`, `decision_options`,
`decide_and_record` (the approved EXPECTED-NET-VALUE policy), Xavier's
pre-dispatch record and the bound-plan dispatch -- against a migrated
database, with ONLY the venue transport substituted (the harness of
`test_xavier_manages_positions_through_the_scheduled_path`, imported, not
copied). Candidates, rankings, decisions and valuations are never injected;
every assertion reads the PERSISTED record (`bettor_xavier_decisions`).

THE FIXTURE (SYNTHETIC). The harness's orientation: the held leg is the Red
Sox moneyline (extra innings included, so it cannot end level) and the
ladder is the opponent's -- the Yankees' -- run lines +1.5, +2.5, +3.5 and
+4.5 in the same game and period, each with its own book. (The owner's
example -- held Yankees, Red Sox +1.5..+4.5 -- with the teams swapped: the
same structures.) Prose: a cancelled game refunds every stake, so the VOID
payout is ESTABLISHED as a refund of each leg's cost. NOTHING HERE IS
EVIDENCE ABOUT ANY MARKET.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_decision as FD
from sportsassets.agents import xavier_ladder as XL
from tests import test_xavier_manages_positions_through_the_scheduled_path as H

pg = H.pg
ACQ = FD.ACTION_ACQUIRE_INDIRECT_HEDGE
BASE = "asc-mlb-bos-nyy-2026-10-06-neg-%spt5"
#: line -> the run line's slug (BOS -line / NYY +line); +1.5 is the harness's.
LINES = {1: BASE % 1, 2: BASE % 2, 3: BASE % 3, 4: BASE % 4}
assert LINES[1] == H.SIB


def nyy(line: int) -> str:
    """The Yankees +line side's candidate identity (the SHORT side)."""
    return LINES[line] + "#ORDER_INTENT_BUY_SHORT"


async def ladder_catalogue(conn, lines=(1, 2, 3, 4)):
    """The harness's catalogue (moneyline + the +1.5 run line) and the
    further run lines, both sides each, in the same game and period."""
    await H.catalogue(conn)
    for ln in lines:
        if ln == 1:
            continue
        slug = LINES[ln]
        for ident, side, abbr, signed, intent in (
                (slug + ":bos", "yes", "bos", "-%d.5" % ln,
                 "ORDER_INTENT_BUY_LONG"),
                (slug + ":nyy", "no", "nyy", "+%d.5" % ln,
                 "ORDER_INTENT_BUY_SHORT")):
            await conn.execute(
                "INSERT INTO us_premap (identifier, event_slug, event_title,"
                " market_slug, question, kind, line, side_norm, intent, signed,"
                " team_abbr, team_name, sports_type, game_start)"
                " VALUES ($1,$2,$3,$4,$5,'side',$6,$7,$8,$9,$10,$11,$12,"
                "         now() + interval '3 hours')",
                ident, H.EVENT, "Boston Red Sox vs. New York Yankees", slug,
                "Who will win?", "%d.5" % ln, side, intent, signed, abbr, abbr,
                "baseball_team_full_game_spread")


def ladder_books(*, held_bids, hedge_bids: dict, other_side_offer=0.95):
    """`hedge_bids`: line -> (bid, depth). The Yankees +line side costs
    1 - bid (a SHORT buy consumes the bids)."""
    out = {H.HELD: {"bids": list(held_bids), "offers": [(0.99, 5)]}}
    for ln, (bid, depth) in hedge_bids.items():
        out[LINES[ln]] = {"bids": [(bid, depth)],
                          "offers": [(other_side_offer, 500)]}
    return out


async def run(conn, monkeypatch, *, p, hedge_bids, held_bids=((0.52, 400),),
              qty=10, price=0.50, lines=(1, 2, 3, 4)):
    await H.start(conn, p=p, qty=qty, price=price)
    await ladder_catalogue(conn, lines)
    venue = H.Venue(books=ladder_books(held_bids=held_bids,
                                       hedge_bids=hedge_bids),
                    holdings={H.HELD: (float(qty), qty * price)})
    H.substitute(monkeypatch, venue)
    out = await H.run_cycle(conn)
    recs = await H.xavier_records(conn)
    assert recs, out.get("funded_servicing")
    return out, recs[0], venue


def acq(rec, cid):
    return H.alternative(rec, ACQ, cid)


def rows_by_legs(table, per_leg):
    return [r for r in table["rows"]
            if r.get("per_leg_cents_per_unit") == list(per_leg)
            and r.get("state") == "REGULAR"]


def void_row(table):
    got = [r for r in table["rows"] if r.get("state") == "VOID"]
    assert len(got) == 1, table["rows"]
    return got[0]


# ════════════════════════════════════════════════════════════════════
# PROOF 4: EVERY AVAILABLE SPREAD REACHES XAVIER'S COMPARISON
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_proof4_every_spread_on_the_ladder_reaches_the_comparison(
        monkeypatch):
    """+1.5, +2.5, +3.5 and +4.5 are all quoted, admitted, ranked, bound to
    plans, priced and compared -- asserted on the PERSISTED record's
    alternatives, each with its own ladder fields -- and nothing is dropped
    after the first admitted contract."""
    conn = await H._connect()
    try:
        out, rec, _ = await run(
            conn, monkeypatch, p=0.55,
            hedge_bids={1: (0.55, 500), 2: (0.45, 500), 3: (0.35, 500),
                        4: (0.25, 500)})
        step = H.step_of(out)
        want = [nyy(ln) for ln in (1, 2, 3, 4)]
        # the supplier built every one, the ranking carried every one
        assert set(want) <= set(step["search_order"]), step["search_order"]
        assert set(want) <= set(step["hedge_decision_inputs"][
            "candidate_ids"]), step["hedge_decision_inputs"]
        # ── ON THE PERSISTED RECORD ──────────────────────────────────
        for cid in want:
            a = acq(rec, cid)
            assert a["rankable"] is True, a
            H.assert_whole_position_economics(a)
            for k in XL.LADDER_FIELDS:
                assert k in a, (k, cid)
            t = a["payout_table"]
            assert t["kind"] == "ACQUISITION" and t["rows"], t
            assert t["minimum_rule"] == XL.MINIMUM_RULE
            assert a["worst_case_established_usd"] == pytest.approx(
                t["position_minimum_usd"])
            assert a["p_net_profit"] is not None
            assert a["p_both_legs_win"] is not None
            assert a["settlement_compatibility"]["status"] == \
                "COMPATIBLE_SAME_GRADING_KEY"
            assert a["alternative_class"] in (XL.ALT_PAIR_PROFIT,
                                              XL.ALT_PAIR_LOSS)
        # THE LADDER, sorted by line, and each line is the one admitted
        xl = rec["reasoning"]["xavier_ladder"]
        lad = [r for r in xl["ladder"] if r["candidate_id"] in want]
        assert [r["candidate_id"] for r in lad] == want
        assert [abs(r["line"]) for r in lad] == [1.5, 2.5, 3.5, 4.5]
        # THE SEARCH'S COMPLETENESS: every admitted contract is in the
        # comparison (or refused by name) -- none silently dropped
        sc = xl["search_completeness"]
        assert sc["every_admitted_reached_the_comparison"] is True, sc
        assert sc["admitted_missing_from_the_comparison"] == []
        assert sc["acquisitions_in_the_comparison"] >= 4
        assert sc["budget_statement"].startswith("NOT_REPORTED_BY_THE_"
                                                 "SUPPLIER") or \
            sc["supplier_truncated_at_limit"] is False
        # THE LEDGER'S ONE COMPARISON RANKED ALL FOUR
        led = await H.ledger_row(conn, rec["decision_id"])
        ranked = {c.get("candidate_id") for c in led["ranked"]
                  if c.get("action") == ACQ}
        assert set(want) <= ranked, ranked
    finally:
        await H.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# PROOF 5: WIDER IS NOT AUTOMATICALLY BETTER
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_proof5_a_wider_expensive_spread_loses_to_a_narrower_cheaper_one(
        monkeypatch):
    """Yankees +4.5 at $0.75 overlaps the held moneyline on Red Sox wins by
    1-4 runs -- a broader both-win region and a higher P(both legs win) than
    +1.5 at $0.45 (Red Sox by exactly 1) -- and still loses to it on
    expected net value under the approved policy. Broader is not better by
    itself: the extra overlap is paid for."""
    conn = await H._connect()
    try:
        out, rec, _ = await run(conn, monkeypatch, p=0.55,
                                hedge_bids={1: (0.55, 500), 4: (0.25, 500)},
                                lines=(1, 4))
        narrow, wide = acq(rec, nyy(1)), acq(rec, nyy(4))
        sn = narrow["settlement_compatibility"]
        sw = wide["settlement_compatibility"]
        # the overlap: broader for +4.5
        assert len(sw["both_win_regions"]) > len(sn["both_win_regions"]), (
            sw, sn)
        # each states its own P(both legs win) from the approved (SYNTHETIC)
        # distribution; the comparison below is on expected value, not on it
        assert wide["p_both_legs_win"] is not None
        assert narrow["p_both_legs_win"] is not None
        both_n = [r for r in narrow["payout_table"]["rows"]
                  if r.get("per_leg_cents_per_unit") == [100, 100]]
        both_w = [r for r in wide["payout_table"]["rows"]
                  if r.get("per_leg_cents_per_unit") == [100, 100]]
        assert len(both_w) >= 1 and len(both_n) >= 1
        # ... and it costs more: capital, and expected value
        assert wide["capital_required_usd"] > narrow["capital_required_usd"]
        assert narrow["expected_net_usd"] > wide["expected_net_usd"]
        # UNDER THE APPROVED POLICY the narrower one ranks ahead
        led = await H.ledger_row(conn, rec["decision_id"])
        order = [c.get("candidate_id") for c in led["ranked"]
                 if c.get("action") == ACQ]
        assert order.index(nyy(1)) < order.index(nyy(4)), order
        pol = rec["reasoning"]["xavier_ladder"]["policy"]
        assert pol["identical_to_approved_ev_policy"] is True
        assert pol["params"]["max_ev_sacrifice_for_downside_usd"] == 0.0
        if rec["chosen_action"] == "ACQUIRE_HEDGE":
            assert acq(rec, nyy(1))["plan_digest"] == rec[
                "chosen_plan_digest"]
    finally:
        await H.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# PROOF 6: THE HONEST $1.10 PAIR
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_proof6_a_one_ten_pair_shows_its_losing_single_winner_outcome(
        monkeypatch):
    """Primary $0.50 + hedge $0.60 = $1.10 before fees. The payout table
    shows the single-winner outcomes losing $0.10 per pair (plus the fee),
    the double win gaining $0.90 per pair (less the fee), the void refunding
    both stakes (established by the prose -- not assumed); the worst case is
    the single-winner loss; the break-even P(both) is the amount over $1
    plus the fee per pair."""
    conn = await H._connect()
    try:
        out, rec, _ = await run(conn, monkeypatch, p=0.55,
                                hedge_bids={1: (0.40, 500)}, lines=(1,))
        a = acq(rec, nyy(1))
        fee = a["fees_usd"]
        assert fee > 0
        t = a["payout_table"]
        assert t["quantities"]["held_qty"] == 10
        assert t["quantities"]["hedge_qty"] == 10
        assert t["quantities"]["cost_usd"] == pytest.approx(5.0 + 6.0)
        held_only = rows_by_legs(t, (100, 0))       # Red Sox by 2+
        hedge_only = rows_by_legs(t, (0, 100))      # Yankees win
        both = rows_by_legs(t, (100, 100))          # Red Sox by exactly 1
        assert held_only and hedge_only and both, t["rows"]
        for r in held_only + hedge_only:
            assert r["net_pnl_usd"] == pytest.approx(10.0 - 11.0 - fee)
        for r in both:
            assert r["net_pnl_usd"] == pytest.approx(20.0 - 11.0 - fee)
        v = void_row(t)
        assert v["established"] is True
        assert v["combined_payout_usd"] == pytest.approx(11.0)
        assert v["net_pnl_usd"] == pytest.approx(-fee)
        # THE WORST CASE IS THE SINGLE-WINNER LOSS, on the combined payout
        assert a["worst_case_established_usd"] == pytest.approx(-1.0 - fee)
        assert a["worst_case_net_usd"] == pytest.approx(-1.0 - fee)
        assert a["alternative_class"] == XL.ALT_PAIR_LOSS
        be = a["double_win_break_even"]
        assert be["applies"] is True
        assert be["combined_cost_per_pair_usd"] == pytest.approx(1.10)
        assert be["single_winner_net_per_pair_usd"] == pytest.approx(
            -0.10 - fee / 10)
        assert be["double_win_net_per_pair_usd"] == pytest.approx(
            0.90 - fee / 10)
        assert be["break_even_p_both_given_normal"] == pytest.approx(
            0.10 + fee / 10)
        # a search preference of <= $1.00 is not met -- and admits/rejects
        # nothing: the pair is still valued and compared on its numbers
        sp = a["search_preference"]
        assert sp["is_a_permission"] is False
        assert sp["within_combined_cost_preference"] is False
        assert sp["within_hedge_price_preference"] is True      # 0.60
        assert a["rankable"] is True
    finally:
        await H.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# PROOF 7: FEES MAKE AN UNDER-$1 PAIR LOSS-CAPABLE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_proof7_fees_turn_an_under_one_dollar_pair_into_a_loss_capable_one(
        monkeypatch):
    """Primary $0.50 + hedge $0.49 = $0.99: nominally under $1, so every
    single-winner state grosses +$0.10 on 10 pairs. The deployed fee on the
    10-contract acquisition exceeds that, so the single-winner states LOSE
    and the worst case is negative. The screen's `matched_cost_under_one_
    dollar` is true and its fee check fails; the search preference is met
    and changes nothing."""
    conn = await H._connect()
    try:
        out, rec, _ = await run(conn, monkeypatch, p=0.55,
                                hedge_bids={1: (0.51, 500)}, lines=(1,))
        a = acq(rec, nyy(1))
        fee = a["fees_usd"]
        # THE REAL FEE FUNCTIONS: the deployed book's fee_for and the
        # schedule the pair supplier priced -- both above the $0.10 margin
        book_fee, basis = FB.fee_for(10, 0.49, at=time.time())
        assert book_fee > 0.10, (book_fee, basis)
        assert fee > 0.10, a["fees_and_execution"]
        t = a["payout_table"]
        assert t["quantities"]["cost_usd"] == pytest.approx(5.0 + 4.9)
        for r in rows_by_legs(t, (100, 0)) + rows_by_legs(t, (0, 100)):
            assert r["combined_payout_usd"] == pytest.approx(10.0)
            assert r["net_pnl_usd"] == pytest.approx(0.10 - fee)
            assert r["net_pnl_usd"] < 0
        assert a["worst_case_established_usd"] < 0
        assert a["worst_case_established_usd"] == pytest.approx(
            min(0.10 - fee, -fee))
        scr = a["search_screen"]
        assert scr["matched_cost_under_one_dollar"] is True
        assert scr["floor_survives"]["fees"]["survives"] is False
        assert scr["locks_a_floor"] is False
        assert a["search_preference"]["within_combined_cost_preference"] \
            is True
        assert a["alternative_class"] == XL.ALT_PAIR_LOSS
        be = a["double_win_break_even"]
        assert be["single_winner_net_per_pair_usd"] < 0
    finally:
        await H.clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# PROOF 8: UNEQUAL QUANTITIES KEEP THE UNPAIRED REMAINDER EVERYWHERE
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_proof8_unequal_quantities_keep_the_unpaired_remainder_in_every_figure(
        monkeypatch):
    """10 held, the +1.5 book shows 6: the acquisition is valued with the 4
    unpaired primary in every row of its payout table, its expectation, its
    worst case, its P(net profit) and its exposure -- recomputed here from
    the persisted classes at 10 and 6, and checked against the ranking's own
    whole-position expectation. After the fill, the GROUP's HOLD table is
    one joint table over 10 primary and 6 hedge."""
    conn = await H._connect()
    try:
        out, rec, venue = await run(conn, monkeypatch, p=0.55,
                                    held_bids=H.PROFIT_LADDER,
                                    hedge_bids={1: (0.55, 6)}, lines=(1,))
        a = acq(rec, nyy(1))
        fee = a["fees_usd"]
        t = a["payout_table"]
        q = t["quantities"]
        assert (q["held_qty"], q["hedge_qty"], q["uncovered_qty"]) == (
            10, 6, 4)
        cost = 10 * 0.50 + 6 * 0.45
        assert q["cost_usd"] == pytest.approx(cost)
        # EVERY ROW is the whole position: 10 held + 6 hedge
        for r in t["rows"]:
            plc = r.get("per_leg_cents_per_unit")
            if not r.get("established") or plc is None:
                continue
            assert r["combined_payout_usd"] == pytest.approx(
                (10 * plc[0] + 6 * plc[1]) / 100.0)
            assert r["net_pnl_usd"] == pytest.approx(
                (10 * plc[0] + 6 * plc[1]) / 100.0 - cost - fee)
        # the worst case is NOT the matched slice's: the 4 unpaired can lose
        hedge_only = rows_by_legs(t, (0, 100))
        assert hedge_only and hedge_only[0]["net_pnl_usd"] == pytest.approx(
            6.0 - cost - fee)
        assert a["worst_case_established_usd"] == pytest.approx(
            min(r["net_pnl_usd"] for r in t["rows"]
                if r.get("established") and r.get("net_pnl_usd")
                is not None))
        assert a["worst_case_established_usd"] == pytest.approx(
            a["worst_case_net_usd"])
        # EXPECTATION AND P(PROFIT) from the classes, at 10 and 6
        cls = t["probability_classes"]
        assert cls and abs(sum(c["probability"] for c in cls) - 1) < 1e-6
        for c in cls:
            plc = c["per_leg_cents_per_unit"]
            assert c["net_pnl_usd"] == pytest.approx(
                (10 * plc[0] + 6 * plc[1]) / 100.0 - cost - fee)
        e = sum(c["probability"] * c["net_pnl_usd"] for c in cls)
        assert a["expected_net_pnl_same_measure_usd"] == pytest.approx(e)
        pp = sum(c["probability"] for c in cls if c["net_pnl_usd"] > 1e-9)
        assert a["p_net_profit"] == pytest.approx(pp)
        led = await H.ledger_row(conn, rec["decision_id"])
        lr = next(c for c in led["ranked"] if c.get("candidate_id") == nyy(1))
        assert e == pytest.approx(lr["whole_position_expected_net_usd"],
                                  abs=1e-5)
        # EXPOSURE
        ex = a["exposure_after"]
        assert (ex["primary_qty"], ex["hedge_qty"], ex["matched_qty"],
                ex["unpaired_qty"], ex["unpaired_role"]) == (
            10, 6, 6, 4, "PRIMARY")
        assert a["unpaired_residual_qty"] == pytest.approx(4.0)
        assert a["unpaired_value_at_risk_usd"] == pytest.approx(4 * 0.50)
        # ── CYCLE 2 (if the 6 filled): ONE JOINT TABLE FOR THE GROUP ──
        if rec["chosen_action"] == "ACQUIRE_HEDGE" and venue.creates_sent():
            await H.run_cycle(conn)
            rec2 = (await H.xavier_records(conn))[0]
            holds = [x for x in rec2["alternatives"]
                     if x.get("action") == "HOLD" and x.get("rankable")]
            assert len(holds) == 1, rec2["alternatives"]
            hold = holds[0]
            gt = hold["payout_table"]
            assert gt["kind"] == "GROUP"
            assert gt["quantities"]["kept_primary"] == 10
            assert gt["quantities"]["kept_hedge"] == 6
            assert gt["probabilities"] == XL.NOT_ESTABLISHED
            basis = 10 * 0.50 + 6 * 0.45
            for r in gt["rows"]:
                plc = r.get("per_leg_cents_per_unit")
                if r.get("established") and plc:
                    assert r["net_pnl_usd"] == pytest.approx(
                        (10 * plc[0] + 6 * plc[1]) / 100.0 - basis)
            assert hold["exposure_after"]["unpaired_qty"] == 4
            assert hold["p_net_profit"] is None
            assert hold["ladder_field_reasons"]["p_net_profit"] == \
                XL.R_NO_JOINT_MEASURE
    finally:
        await H.clean(conn)
        await conn.close()
