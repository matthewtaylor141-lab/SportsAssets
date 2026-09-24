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

def test_2_completion_at_par_loses_only_the_FEE():
    """CORRECTED. I labelled this an above-par loss case. 0.45 + 0.55 =
    1.00 is EXACTLY par, so the -0.55 result is entirely the fee. It is
    kept as a FEE-INDUCED loss case -- which is a real and distinct
    thing -- and a genuinely above-par case follows it."""
    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.55, qty=100.0,
            decision_id="d2")
    m.on_print(at=1002.0, outcome_index=1, price=0.55, size=100.0,
               evidence_id="e2")
    assert m.state()["matched_qty"] == 100.0
    m.settle_at_observed_payout({0: 0.0, 1: 1.0}, 1100.0)
    # purchase cost 45.00 + 55.00 = 100.00 = par exactly. The 0.55 fee is
    # the whole loss.
    assert abs(m.pf.to_dict()["realized_pnl_usd"] + 0.55) < 1e-6
    assert abs(m.pf.to_dict()["fees_usd"] - 0.55) < 1e-6


def test_2a_a_GENUINELY_above_par_purchase_cost_is_still_selectable():
    """Basis 0.45 plus a 0.62 complement is 1.07 -- above par BEFORE any
    fee. It locks -0.07/contract, and it still beats selling into a 0.05
    bid which realises -0.40/contract."""
    ranked = MS.rank_priced_actions(
        100.0, 0.45, bid=0.05, bid_size=500, complement_ask=0.62,
        complement_ask_size=500, fee_fn=fee)
    assert ranked["selected"] == "TAKE_COMPLEMENT"
    best = ranked["priced_actions"][0]
    assert best["locks_a_loss"] is True
    # 100.00 - 62.00 - 0.62 - 45.00 = -7.62, purchase cost alone is 1.07
    assert abs(best["outcome_if_filled_usd"] + 7.62) < 1e-6
    assert "LOCKS A LOSS and is selected anyway" in ranked["selection_reason"]

    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.62, qty=100.0,
            decision_id="d2a")
    m.on_print(at=1002.0, outcome_index=1, price=0.62, size=100.0,
               evidence_id="e2a")
    m.settle_at_observed_payout({0: 0.0, 1: 1.0}, 1100.0)
    assert abs(m.pf.to_dict()["realized_pnl_usd"] + 7.62) < 1e-6


def test_2c_the_desk_blanket_gate_is_not_on_this_path_at_all():
    """bettor_desk.Policy carries `clears_below = 1.0 - avg - min_clear`
    and would refuse an above-par completion. This path never consults
    it: it uses bettor_desk.Order and Portfolio only, so the gate is
    STRUCTURALLY absent rather than overridden."""
    import inspect
    src = inspect.getsource(LC)
    assert "min_clear" not in src
    assert "clears_below" not in src
    assert ".policy" not in src
    # and Managed holds no Policy object
    m = managed()
    assert not hasattr(m, "policy")


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
    assert r2["placed"] is None and r2["refused"] == "WAIT_CANCEL_ACK"
    assert m.orders[r["placed"]].state == DK.CANCEL_PENDING
    m.acknowledge_cancels(1004.0)
    replacement = m.place("DIRECT_EXIT", at=1005.0, price=0.52, qty=100.0,
                          decision_id="d5-after-ack")
    assert replacement["qty"] == 70.0  # matched inventory is retained


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
    assert r["refused"] == "WAIT_CANCEL_ACK"
    m.acknowledge_cancels(1005.0)
    r = m.place("TAKE_COMPLEMENT", at=1006.0, price=0.49, qty=100.0,
                decision_id="d6-after-ack")
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
    assert len(m.open_orders()) == 1
    assert m.open_orders()[0].state == DK.CANCEL_PENDING
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


# ── gap 1: WHETHER to close is separate from HOW ─────────────────────

def test_hold_remains_the_operating_state_until_a_trigger_fires():
    """A PRICEABLE EXIT IS NOT A REASON TO TAKE IT. This is the gap: the
    method comparison selects an exit METHOD and says nothing about
    whether to exit at all."""
    m = managed()
    r = m.decide_and_act(at=1001.0, last_price=0.44, bid=0.44,
                         bid_size=500, complement_ask=0.49,
                         complement_ask_size=500, seconds_open=60)
    assert r["operating_state"] == "HOLD"
    assert r["acted"] is False
    assert m.open_orders() == []
    # CONTROL: a method WAS available, so the hold is about the trigger
    # and not about an empty action table.
    avail = MS.rank_priced_actions(100.0, 0.45, bid=0.44, bid_size=500,
                                   complement_ask=0.49,
                                   complement_ask_size=500, fee_fn=fee)
    assert avail["selected"] is not None


def test_an_adverse_move_fires_the_trigger_and_then_a_method_is_chosen():
    m = managed()
    r = m.decide_and_act(at=1002.0, last_price=0.35, bid=0.35,
                         bid_size=500, complement_ask=0.49,
                         complement_ask_size=500, seconds_open=60)
    assert r["operating_state"] == "CLOSING"
    assert r["trigger"]["fired"] is True
    fired = [c["name"] for c in r["trigger"]["conditions"] if c.get("fired")]
    assert fired == ["ADVERSE_MOVE"]
    assert r["acted"] is True and len(m.open_orders()) == 1


def test_time_open_fires_the_trigger_on_its_own():
    m = managed()
    r = m.decide_and_act(at=1003.0, last_price=0.45, bid=0.45,
                         bid_size=500, complement_ask=0.49,
                         complement_ask_size=500, seconds_open=90_000)
    assert r["trigger"]["fired"] is True
    assert "TIME_OPEN" in [c["name"] for c in r["trigger"]["conditions"]
                           if c.get("fired")]


def test_an_unobserved_price_is_NOT_IDENTIFIED_not_a_zero_move():
    t = MS.exposure_trigger(basis_per_contract=0.45, last_price=None,
                            seconds_open=60)
    move = [c for c in t["conditions"] if c["name"] == "ADVERSE_MOVE"][0]
    assert move["status"] == MS.NOT_IDENTIFIED
    assert "not zero" in move["why"]
    assert t["fired"] is False


def test_the_trigger_is_labelled_a_rule_and_refuses_future_inputs():
    d = MS.TRIGGER_DECLARATION
    assert d["kind"] == "RULE"
    assert "settlement forecast" in d["inputs_refused"]
    assert "the cohort's later actions" in d["inputs_refused"]
    assert "NOT_IDENTIFIED" in d["is_not"]


# ── gap 2: net executable quantities, and a pair is not cash ─────────

def test_a_held_pair_is_not_treated_as_available_cash():
    r = MS.rank_priced_actions(100.0, 0.45, bid=0.40, bid_size=500,
                               complement_ask=0.49,
                               complement_ask_size=500, fee_fn=fee)
    comp = [c for c in r["priced_actions"]
            if c["action"] == "TAKE_COMPLEMENT"][0]
    exit_ = [c for c in r["priced_actions"]
             if c["action"] == "DIRECT_EXIT"][0]
    # Completing SPENDS cash now and returns nothing until settlement.
    assert comp["cash_now_usd"] < 0
    assert comp["cash_at_settlement_usd"] > 0
    assert comp["collateral_released_now"] is False
    assert comp["collateral_release"] == MS.NOT_IDENTIFIED
    # Selling returns cash now.
    assert exit_["cash_now_usd"] > 0
    assert exit_["collateral_released_now"] is True


def test_the_simplified_test_is_labelled_as_pre_cost():
    r = MS.rank_priced_actions(100.0, 0.45, bid=0.40, bid_size=500,
                               complement_ask=0.49,
                               complement_ask_size=500, fee_fn=fee)
    assert "SIMPLIFIED pre-cost test" in r["selection_reason"]
    assert "NET ones after action-specific fees" in r["selection_reason"]


# ── gap 4: a reported fill is recorded in full, never clipped ────────

def test_a_fill_exceeding_inventory_is_recorded_and_the_surplus_exposed():
    """THE BUG THIS REPLACES. The previous version computed
    min(remaining, cap) and took only that much from the print --
    silently trimming a real execution to fit the inventory it expected.
    A book that records less than was executed is wrong in the direction
    that looks tidy."""
    m = managed()
    # Rest a completion for the whole 100, then complete 60 of it by a
    # SECOND route so the first order's remaining exceeds the residual.
    r = m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
                decision_id="g4")
    # a direct buy of the complement outside the order path
    m.pf.buy("0xc1", 1, 60.0, 0.49, 0.0, 1001.5)
    assert abs(m.residual() - 40.0) < 1e-9      # 60 now matched
    f = m.on_print(at=1002.0, outcome_index=1, price=0.49, size=100.0,
                   evidence_id="g4e")
    assert f, "the print must still fill the resting order"
    # THE WHOLE EXECUTION IS RECORDED, not clipped to 40.
    assert abs(f[0]["qty"] - 100.0) < 1e-9
    assert f[0]["inventory_surplus_qty"] > 0
    st = m.state()
    assert st["inventory_discrepancy_qty"] > 0
    d = st["inventory_discrepancies"][0]
    assert abs(d["filled_qty"] - 100.0) < 1e-9
    assert abs(d["inventory_cap"] - 40.0) < 1e-9
    assert "recorded IN FULL rather than clipped" in d["why"]
    # and the fill itself says it was not clipped
    o = m.orders[r["placed"]]
    assert o.fills[0]["was_clipped"] is False


def test_a_replacement_order_cannot_create_unaccounted_exposure():
    """A new action cancels the incumbent and is capped at the CURRENT
    residual, so the two together cannot exceed the position."""
    m = managed()
    m.place("TAKE_COMPLEMENT", at=1001.0, price=0.49, qty=100.0,
            decision_id="rA")
    m.on_print(at=1002.0, outcome_index=1, price=0.49, size=40.0,
               evidence_id="rE")
    m.acknowledge_cancels(1003.0)
    r2 = m.place("TAKE_COMPLEMENT", at=1004.0, price=0.49, qty=100.0,
                 decision_id="rB")
    total = 40.0 + r2["qty"]
    assert total <= 100.0 + 1e-9, (
        "the filled quantity plus the replacement order must not exceed "
        "the seeded position")
