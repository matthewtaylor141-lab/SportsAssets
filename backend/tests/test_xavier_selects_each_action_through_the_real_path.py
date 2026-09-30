"""PROOF 9: SEPARATE VALID CASES SELECT HOLD, EXIT, REDUCE AND AN INDIRECT PAIR
-- THROUGH THE REAL DECISION PATH, NEVER BY INJECTING THE WINNER.

Each case drives `ext_pinnacle_loop.cycle(conn)` with only the venue
transport substituted (the harness of `test_xavier_manages_positions_
through_the_scheduled_path`). What differs between the cases is the MARKET
the synthetic venue shows and the probability row -- never a candidate, a
ranking or a decision. Each asserts on the PERSISTED Xavier record: the
chosen action, its ladder class, that the chosen alternative is the ranked
winner, and that the management policy's record says the choice is the
approved expected-value policy's own (default parameters, no silent change).

SYNTHETIC evidence throughout; nothing here is evidence about any market.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_funded_decision as FD
from sportsassets.agents import xavier_ladder as XL
from tests import test_xavier_manages_positions_through_the_scheduled_path as H

pg = H.pg


def _policy_is_the_approved_one(rec):
    pol = rec["reasoning"]["xavier_ladder"]["policy"]
    assert pol["policy_key"] == "XAVIER_MANAGEMENT_POLICY"
    assert pol["identical_to_approved_ev_policy"] is True
    assert pol["applied_to_dispatch"] is True
    assert pol["capital_preservation"]["enabled"] is False
    return pol


def _chosen(rec, action, **kw):
    a = H.alternative(rec, action, **kw)
    assert a["rankable"] is True
    for k in XL.LADDER_FIELDS:
        assert k in a, k
    return a


async def _one(conn, monkeypatch, *, p, price, books, holdings,
               approve_model=True):
    await H.start(conn, p=p, price=price, approve_model=approve_model)
    venue = H.Venue(books=books, holdings=holdings)
    H.substitute(monkeypatch, venue)
    out = await H.run_cycle(conn)
    recs = await H.xavier_records(conn)
    assert len(recs) == 1, out.get("funded_servicing")
    return out, recs[0], venue


@pg
@pytest.mark.asyncio
async def test_proof9_hold_is_selected_when_holding_is_worth_most(monkeypatch):
    """10 at 0.50, p = 0.55; the book bids 0.52 (below HOLD's 0.55 per
    contract) and the Yankees +1.5 costs 0.80: HOLD wins on its number."""
    conn = await H._connect()
    try:
        out, rec, venue = await _one(
            conn, monkeypatch, p=0.55, price=0.50,
            books=H.books(held_bids=[(0.52, 400)], hedge_bid=0.20),
            holdings={H.HELD: (10.0, 5.0)})
        assert rec["chosen_action"] == "HOLD"
        hold = _chosen(rec, "HOLD")
        assert hold["alternative_class"] == XL.ALT_HOLD
        pol = _policy_is_the_approved_one(rec)
        assert pol["approved_ev_selected"] == "HOLD"
        others = [a for a in rec["alternatives"] if a.get("rankable")
                  and a["action"] != "HOLD"]
        assert others and all(a["expected_net_usd"] < hold["expected_net_usd"]
                              for a in others)
        assert venue.creates_sent() == []
    finally:
        await H.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_proof9_a_full_exit_is_selected_when_the_forward_value_collapsed(
        monkeypatch):
    """10 at 0.50 and the forward probability is now 0.10 (HOLD -4.00); the
    book bids 0.45 for 400, so the WHOLE position can be sold at a small
    realised loss -- worth far more than holding. No hedge book exists. The
    full DIRECT_EXIT is selected (the ledger's EXIT) and classed FULL_EXIT:
    forward value decides, the sunk basis does not."""
    conn = await H._connect()
    try:
        out, rec, venue = await _one(
            conn, monkeypatch, p=0.10, price=0.50,
            books=H.books(held_bids=[(0.45, 400)]),
            holdings={H.HELD: (10.0, 5.0)}, approve_model=False)
        step = H.step_of(out)
        assert step["decision"]["action"] == "EXIT", step["decision"]
        assert rec["chosen_action"] == "EXIT"
        ex = _chosen(rec, "DIRECT_EXIT")
        assert ex["plan_digest"] == rec["chosen_plan_digest"]
        assert ex["alternative_class"] == XL.ALT_FULL_EXIT
        assert ex["exposure_after"]["primary_qty"] == 0
        assert ex["capital_duration"]["capital_duration_h"] == 0.0
        # the table is flat: every state realises the same net
        nets = {r["net_pnl_usd"] for r in ex["payout_table"]["rows"]
                if r.get("established")}
        assert len(nets) == 1 and nets.pop() < 0          # a realised loss
        hold = H.alternative(rec, "HOLD")
        assert ex["expected_net_usd"] > hold["expected_net_usd"]
        assert "history_is_not_a_reason" in rec["reasoning"]
        _policy_is_the_approved_one(rec)
    finally:
        await H.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_proof9_a_partial_reduction_is_selected_when_only_part_pays(
        monkeypatch):
    """10 at 0.60, p = 0.30; the book pays more than holding for 7 (4 at
    0.45, 3 at 0.42) and not for the rest (0.10): REDUCE 7 is selected and
    classed PARTIAL_REDUCTION, 3 kept in every figure."""
    conn = await H._connect()
    try:
        out, rec, venue = await _one(
            conn, monkeypatch, p=0.30, price=0.60,
            books=H.books(held_bids=[(0.45, 4), (0.42, 3), (0.10, 400)]),
            holdings={H.HELD: (10.0, 6.0)}, approve_model=False)
        assert rec["chosen_action"] == "REDUCE"
        rd = _chosen(rec, "REDUCE")
        assert rd["plan_digest"] == rec["chosen_plan_digest"]
        assert rd["alternative_class"] == XL.ALT_PARTIAL
        assert rd["exposure_after"]["primary_qty"] == 3
        assert rd["payout_table"]["quantities"]["kept"] == 3
        _policy_is_the_approved_one(rec)
    finally:
        await H.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_proof9_an_indirect_pair_is_selected_when_it_is_worth_most(
        monkeypatch):
    """10 at 0.50, p = 0.55; a profitable exit and a reduction exist, and
    the Yankees +1.5 at 0.45 is worth more than either on whole-position
    expected value: the indirect pair is selected, with its ladder class,
    its payout table and P(both legs win) on the record, and dispatched as
    its bound plan."""
    conn = await H._connect()
    try:
        out, rec, venue = await _one(
            conn, monkeypatch, p=0.55, price=0.50,
            books=H.books(held_bids=H.PROFIT_LADDER, hedge_bid=0.55),
            holdings={H.HELD: (10.0, 5.0)})
        assert rec["chosen_action"] == "ACQUIRE_HEDGE"
        a = _chosen(rec, FD.ACTION_ACQUIRE_INDIRECT_HEDGE,
                    candidate_id=H.HEDGE_CID)
        assert a["plan_digest"] == rec["chosen_plan_digest"]
        assert a["alternative_class"] in (XL.ALT_PAIR_PROFIT,
                                          XL.ALT_PAIR_LOSS)
        assert a["p_both_legs_win"] is not None
        assert a["payout_table"]["kind"] == "ACQUISITION"
        for act in ("HOLD", "DIRECT_EXIT", "REDUCE"):
            assert H.alternative(rec, act)["expected_net_usd"] < \
                a["expected_net_usd"]
        pol = _policy_is_the_approved_one(rec)
        assert pol["approved_ev_selected_candidate"][1] == H.HEDGE_CID
        H.assert_sent_the_persisted_plan(venue, rec)
    finally:
        await H.clean(conn)
        await conn.close()
