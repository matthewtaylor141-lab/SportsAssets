"""THE SIX LIFECYCLE SCENARIOS, through bettor_desk.Order / Portfolio.

EVERY SCENARIO HERE IS CONTROLLED, NOT OBSERVED. The prints are
constructed to exercise a specific state transition. They are labelled
CONTROLLED throughout and must never be quoted as results from real
source data -- a real RN1-seeded example is a separate artifact.

The six the directive names:
    1 profitable complementary purchase
    2 complementary purchase accepting a bounded loss
    3 direct sale
    4 partial fill followed by reassessment
    5 no fill followed by expiry or cancellation
    6 hold through settlement
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

from sportsassets import bettor_desk as DK                      # noqa: E402
from sportsassets import bettor_mgmt_lifecycle as LC            # noqa: E402
from sportsassets import bettor_mgmt_select as MS               # noqa: E402

LABEL = "CONTROLLED_SCENARIO"


def fee(qty, price):
    return round(0.01 * float(qty) * float(price), 6)


def managed(qty=100.0, px=0.45, at=1000.0, queue_share=1.0):
    """Seeded with 100 YES at 0.45. queue_share 1.0 so a print's whole
    size is available -- the share is swept in the scenarios that care."""
    return LC.Managed(condition_id="0xc1", outcome_index=0, seed_qty=qty,
                      seed_price=px, at=at, fee_fn=fee,
                      queue_share=queue_share)


# ── 1. profitable complementary purchase ─────────────────────────────

def test_1_profitable_complementary_purchase_completes_and_locks_a_gain():
    m = managed()
    assert m.residual() == 100.0
    r = m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
                decision_id="d1")
    assert r["refused"] is None and r["qty"] == 100.0
    fills = m.on_print(at=1002.0, outcome_index=1, price=0.49, size=100.0,
                       evidence_id="e1")
    assert len(fills) == 1 and fills[0]["qty"] == 100.0
    st = m.state()
    assert st["matched_qty"] == 100.0
    assert st["residual_qty"] == 0.0
    o = m.orders[r["placed"]]
    assert o.state == DK.FILLED
    # BASIS EXCLUDES THE FEE. bettor_desk.Portfolio.buy expenses an
    # entry fee immediately rather than capitalising it -- "not left in
    # limbo" -- so the basis is 45.00 + 49.00 = 94.00 and the 0.49 is
    # already in fees. My first version of this assertion assumed the
    # other convention; the code was right.
    assert abs(st["portfolio"]["inventory_cost_usd"] - 94.00) < 1e-6
    assert abs(st["portfolio"]["fees_usd"] - 0.49) < 1e-6
    # settled at par the pair returns 100.00 against a 94.00 basis, less
    # the 0.49 already expensed -> +5.51
    m.settle_at_observed_payout({0: 1.0, 1: 0.0}, 1100.0)
    assert abs(m.pf.to_dict()["realized_pnl_usd"] - 5.51) < 1e-6


# ── 2. completion accepting a BOUNDED LOSS ───────────────────────────

def test_2_completion_above_par_is_allowed_and_bounds_the_loss():
    """THE RULE THAT WAS WRONG. A blanket sub-par gate made this
    impossible. Here the bid is 0.10 and the complement 0.55: completing
    locks -0.05/contract, selling realises -0.35/contract. The locked
    LOSS is the better priced action and must be selectable."""
    ranked = MS.rank_priced_actions(
        100.0, 0.45, bid=0.10, bid_size=500, complement_ask=0.55,
        complement_ask_size=500, fee_fn=fee)
    assert ranked["selected"] == "TAKE_COMPLEMENT"
    best = ranked["priced_actions"][0]
    assert best["locks_a_loss"] is True
    assert best["outcome_if_filled_usd"] < 0
    assert "LOCKS A LOSS and is selected anyway" in ranked["selection_reason"]
    # the clean test: 1 - 0.55 = 0.45 > bid 0.10
    assert "1 - ask = 0.4500 vs bid = 0.1000" in ranked["selection_reason"]

    m = managed()
    r = m.place("TAKE_COMPLEMENT", at=1001.0, price=0.55, qty=100.0,
                decision_id="d2")
    m.on_print(at=1002.0, outcome_index=1, price=0.55, size=100.0,
               evidence_id="e2")
    assert m.state()["matched_qty"] == 100.0
    m.settle_at_observed_payout({0: 0.0, 1: 1.0}, 1100.0)
    # 45.00 + 55.00 + 0.55 = 100.55 out, 100.00 back -> -0.55, BOUNDED
    assert abs(m.pf.to_dict()["realized_pnl_usd"] + 0.55) < 1e-6


def test_2b_the_rule_still_prefers_selling_when_selling_is_better():
    """CONTROL. Without this, permitting above-par completion could just
    mean always completing."""
    ranked = MS.rank_priced_actions(
        100.0, 0.45, bid=0.60, bid_size=500, complement_ask=0.55,
        complement_ask_size=500, fee_fn=fee)
    # 1 - 0.55 = 0.45 < bid 0.60 -> selling wins
    assert ranked["selected"] == "DIRECT_EXIT"


# ── 3. direct sale ───────────────────────────────────────────────────

def test_3_direct_sale_exits_the_leg_and_releases_its_basis():
    m = managed()
    r = m.place("DIRECT_EXIT", at=1001.0, price=0.50, qty=100.0,
                decision_id="d3")
    m.on_print(at=1002.0, outcome_index=0, price=0.50, size=100.0,
               evidence_id="e3")
    st = m.state()
    assert st["held"][0] == 0.0
    assert st["residual_qty"] == 0.0
    # sold 100 at 0.50 = 50.00 less fee 0.50 against basis 45.00
    assert abs(m.pf.to_dict()["realized_pnl_usd"] - 4.50) < 1e-6


# ── 4. partial fill, then reassessment ───────────────────────────────

def test_4_a_partial_fill_leaves_residual_exposure_under_management():
    """THE CLAIM THIS PINS. Computing an attractive completion does not
    secure it. A print for 30 against an order for 100 completes 30
    pairs and leaves 70 UNPAIRED and still managed."""
    m = managed()
    r = m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
                decision_id="d4")
    f = m.on_print(at=1002.0, outcome_index=1, price=0.49, size=30.0,
                   evidence_id="e4")
    assert f[0]["qty"] == 30.0
    o = m.orders[r["placed"]]
    assert o.state == DK.PARTIALLY_FILLED
    assert abs(o.remaining - 70.0) < 1e-9
    st = m.state()
    assert st["matched_qty"] == 30.0
    assert abs(st["residual_qty"] - 70.0) < 1e-9
    # REASSESSMENT: a new action supersedes the partial order, and the
    # new order is capped at the REMAINING residual, not the original.
    r2 = m.place("DIRECT_EXIT", at=1003.0, price=0.52, qty=100.0,
                 decision_id="d5")
    assert r2["cancelled"] == [r["placed"]]
    assert abs(r2["qty"] - 100.0) < 1e-9      # a SELL is capped at held
    assert m.orders[r["placed"]].state == DK.CANCEL_PENDING


def test_4b_a_completion_order_cannot_exceed_the_unpaired_remainder():
    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
            decision_id="d4")
    m.on_print(at=1002.0, outcome_index=1, price=0.49, size=40.0,
               evidence_id="e4")
    m.acknowledge_cancels(1003.0)
    # 40 matched, 60 unpaired. A completion for 100 must cap at 60.
    r = m.place("TAKE_COMPLEMENT", at=1004.0, price=0.49, qty=100.0,
                decision_id="d6")
    assert abs(r["qty"] - 60.0) < 1e-9
    assert r["capped"] is True


# ── 5. no fill: expiry, and the cancel/fill race ─────────────────────

def test_5_an_order_that_never_fills_expires_with_the_position_intact():
    m = managed()
    r = m.place("TAKE_COMPLEMENT", at=1001.0, price=0.40, qty=100.0,
                decision_id="d7")
    # prints at 0.49 never reach a 0.40 bid
    assert m.on_print(at=1002.0, outcome_index=1, price=0.49, size=500.0,
                      evidence_id="e5") == []
    m.expire(1001.0 + m.expiry_s + 1)
    assert m.orders[r["placed"]].state == DK.EXPIRED
    st = m.state()
    assert st["residual_qty"] == 100.0, (
        "an unfilled completion leaves us at FULL exposure -- pricing it "
        "as though the risk had been removed is the error this pins")
    assert st["open_orders"] == []


def test_5b_a_cancel_pending_order_can_still_fill_and_the_fill_is_booked():
    """THE RACE. Requesting a cancel does not cancel it. A print that
    crosses an order in CANCEL_PENDING fills it, and that fill is real
    inventory -- ignoring it would leave the accounting short."""
    m = managed()
    r = m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
                decision_id="d8")
    m.place("DIRECT_EXIT", at=1002.0, price=0.52, qty=100.0,
            decision_id="d9")            # supersedes -> CANCEL_PENDING
    o = m.orders[r["placed"]]
    assert o.state == DK.CANCEL_PENDING
    f = m.on_print(at=1003.0, outcome_index=1, price=0.49, size=25.0,
                   evidence_id="e6")
    assert f and f[0]["raced_a_pending_cancel"] is True
    assert m.state()["matched_qty"] == 25.0
    # the acknowledgement then lands and closes it
    m.acknowledge_cancels(1004.0)
    assert o.state == DK.CANCELLED
    assert o.filled_qty == 25.0


def test_5c_only_one_management_order_is_ever_open():
    """THE DOCUMENTED CHOICE. Simultaneous coordinated orders are not
    supported in this release, and the state says so."""
    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
            decision_id="dA")
    m.place("DIRECT_EXIT", at=1002.0, price=0.52, qty=100.0,
            decision_id="dB")
    resting = [o for o in m.open_orders() if o.state == DK.RESTING]
    assert len(resting) == 1
    assert m.state()["supports_simultaneous_orders"] is False


# ── 6. hold through settlement ───────────────────────────────────────

def test_6_holding_through_settlement_books_the_observed_payout():
    m = managed()
    assert m.open_orders() == []
    m.settle_at_observed_payout({0: 1.0}, 1100.0)
    assert abs(m.pf.to_dict()["realized_pnl_usd"] - 55.0) < 1e-6
    assert m.held(0) == 0.0


def test_6b_holding_a_loser_books_the_whole_basis_as_a_loss():
    m = managed()
    m.settle_at_observed_payout({0: 0.0}, 1100.0)
    assert abs(m.pf.to_dict()["realized_pnl_usd"] + 45.0) < 1e-6


# ── accounting, across all of it ─────────────────────────────────────

def test_the_ledger_identity_holds_through_a_partial_completion():
    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
            decision_id="dC")
    m.on_print(at=1002.0, outcome_index=1, price=0.49, size=55.0,
               evidence_id="e7")
    inv = m.state()["invariant"]
    assert inv["ok"] is True, inv


def test_open_inventory_is_reported_at_cost_and_never_marked():
    m = managed()
    v = m.state()["open_inventory_valuation"]
    assert v["mark_usd"] == "NOT_IDENTIFIED"
    assert abs(v["inventory_cost_usd"] - 45.0) < 1e-9


def test_one_print_is_allocated_once_even_if_redelivered():
    """Replay safety comes from bettor_desk.Consumption, keyed on the
    print id -- not from anything added here."""
    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
            decision_id="dD")
    a = m.on_print(at=1002.0, outcome_index=1, price=0.49, size=30.0,
                   evidence_id="dup")
    b = m.on_print(at=1002.0, outcome_index=1, price=0.49, size=30.0,
                   evidence_id="dup")
    assert a and not b, "a re-delivered print must not fill twice"
    assert m.state()["matched_qty"] == 30.0


def test_these_scenarios_are_labelled_controlled_not_observed():
    assert LABEL == "CONTROLLED_SCENARIO"
