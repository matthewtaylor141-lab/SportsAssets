"""THE SHADOW DESK, checked where it would be easiest to flatter itself.

Every test here targets a specific way a simulated desk lies:

    a quote touching our price counted as a fill
    one print filling several orders
    the same print re-delivered by a second lane spent twice
    a sell realising against the wrong basis
    inventory mark quoted again as an executable liquidation
    a limit checked after the order exists
    cash going negative
    a merge action that the venue does not offer

Fixtures are synthetic. The engine is pure, so these are the same code
paths the live loop and the replay run.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_desk as DK


def mk(**kw):
    p = DK.Policy(entry_lo=0.40, entry_hi=0.65, order_usd=100.0,
                  expiry_s=600.0, exit_edge=0.02)
    lim = DK.Limits(starting_cash=10000.0, max_order_usd=500.0,
                    max_position_usd=1000.0, max_condition_usd=2000.0,
                    max_committed_usd=5000.0, max_open_orders=10)
    for k, v in kw.items():
        if hasattr(p, k):
            setattr(p, k, v)
        elif hasattr(lim, k):
            setattr(lim, k, v)
    return DK.Desk(policy=p, limits=lim, queue_share=kw.get("qs", 1.0))


def ev(at, price, size=1000.0, cond="C1", oi=0, eid=None):
    return {"kind": "PRINT", "at": at, "condition_id": cond,
            "outcome_index": oi, "price": price, "size": size,
            "evidence_id": eid or ("p%s" % at)}


class TestAQuoteIsNotAFill:

    def test_a_resting_order_needs_a_PRINT_not_a_touch(self):
        """THE CENTRAL REFUSAL. A book event that merely quotes our
        price fills nothing; only an observed trade does."""
        d = mk()
        d.step(ev(1, 0.50))                      # entry -> order rests
        o = d.open_orders()[0]
        assert o.state == DK.RESTING and o.filled_qty == 0

        quote = {"kind": "BOOK", "at": 2, "condition_id": "C1",
                 "outcome_index": 0, "price": 0.50, "size": 9999.0,
                 "evidence_id": "q2"}
        d.step(quote)
        assert d.orders[o.order_id].filled_qty == 0, \
            "a quote at our price must not fill us"

        d.step(ev(3, 0.50, size=500.0, eid="t3"))
        assert d.orders[o.order_id].filled_qty > 0

    def test_a_print_above_our_bid_does_not_fill_a_buy(self):
        d = mk()
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.55, eid="t2"))
        assert d.orders[o.order_id].filled_qty == 0

    def test_price_improvement_goes_to_the_print_price(self):
        """We are filled at what actually traded, not at our limit."""
        d = mk()
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.45, size=1000.0, eid="t2"))
        assert abs(o.avg_fill_price - 0.45) < 1e-12


class TestOnePrintIsNotUnlimitedLiquidity:

    def test_one_print_cannot_fill_two_orders_beyond_its_size(self):
        """THE DEFECT THE CONSUMPTION LEDGER EXISTS FOR. Without it a
        busy tape fills an unbounded book."""
        d = mk(max_open_orders=10, max_condition_usd=100000.0,
               max_position_usd=100000.0)
        d.step(ev(1, 0.50, cond="A"))
        d.step(ev(1, 0.50, cond="B"))
        # Both rest. Now one print in A, smaller than A's order.
        a = [o for o in d.open_orders() if o.condition_id == "A"][0]
        want = a.qty
        d.step(ev(2, 0.50, size=want / 4.0, cond="A", eid="small"))
        assert abs(a.filled_qty - want / 4.0) < 1e-9
        assert d.cons.remaining("small") < 1e-9

    def test_the_same_print_redelivered_cannot_be_spent_twice(self):
        """A second ingestion lane re-delivering one venue trade must
        not double our fills. The ledger is keyed on the print id."""
        d = mk()
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.50, size=o.qty / 2.0, eid="SAME"))
        first = o.filled_qty
        d.step(ev(3, 0.50, size=o.qty / 2.0, eid="SAME"))   # same id
        assert abs(o.filled_qty - first) < 1e-9

    def test_queue_share_limits_what_one_print_can_give_us(self):
        """We were not in the queue. queue_share is the assumption and
        it must actually bind."""
        d = mk(qs=0.10)
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.50, size=1000.0, eid="t2"))
        assert abs(o.filled_qty - min(o.qty, 100.0)) < 1e-9

    def test_a_partial_fill_leaves_the_order_open_and_named(self):
        d = mk()
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.50, size=o.qty / 3.0, eid="t2"))
        assert o.state == DK.PARTIALLY_FILLED
        assert o.remaining > 0
        assert o in d.open_orders()


class TestTheAccounting:

    def test_the_ledger_identity_holds_through_a_round_trip(self):
        d = mk()
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.50, size=1e6, eid="f"))
        assert o.state == DK.FILLED
        assert d.pf.invariant()["ok"], d.pf.invariant()
        d.settle("C1", 0, 1.0, 3)
        inv = d.pf.invariant()
        assert inv["ok"], inv

    def test_a_sell_realises_against_the_average_basis(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1000.0))
        d.pf.buy("C", 0, 100, 0.40, 0.0, 1)
        d.pf.buy("C", 0, 100, 0.60, 0.0, 2)      # avg 0.50
        pnl = d.pf.sell("C", 0, 100, 0.55, 0.0, 3)
        assert abs(pnl - 5.0) < 1e-9
        assert abs(d.pf.legs[("C", 0)]["cost"] - 50.0) < 1e-9

    def test_selling_more_than_held_is_refused(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits())
        d.pf.buy("C", 0, 10, 0.5, 0.0, 1)
        with pytest.raises(ValueError):
            d.pf.sell("C", 0, 11, 0.5, 0.0, 2)

    def test_the_two_legs_are_never_netted(self):
        """YES and NO are separate positions. Netting them is the defect
        that cost real money on the live mirror."""
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits())
        d.pf.buy("C", 0, 100, 0.40, 0.0, 1)
        d.pf.buy("C", 1, 100, 0.55, 0.0, 2)
        assert ("C", 0) in d.pf.legs and ("C", 1) in d.pf.legs
        assert d.pf.legs[("C", 0)]["qty"] == 100
        assert d.pf.legs[("C", 1)]["qty"] == 100

    def test_settlement_pays_the_payout_and_closes_the_leg(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1000.0))
        d.pf.buy("C", 0, 100, 0.40, 0.0, 1)
        pnl = d.pf.settle("C", 0, 0.0, 2)        # it lost
        assert abs(pnl + 40.0) < 1e-9
        assert d.pf.legs[("C", 0)]["qty"] == 0
        assert d.pf.legs[("C", 0)]["settled"] is True

    def test_a_loss_is_kept_not_dropped(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1000.0))
        d.pf.buy("C", 0, 100, 0.90, 0.0, 1)
        d.pf.settle("C", 0, 0.0, 2)
        assert d.pf.realized < 0
        assert d.pf.invariant()["ok"]

    def test_fees_are_charged_and_reduce_cash(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1000.0),
                    fee_fn=lambda q, p, m: 1.23)
        before = d.pf.cash
        d.pf.buy("C", 0, 10, 0.5, 1.23, 1)
        assert abs(before - d.pf.cash - (5.0 + 1.23)) < 1e-9
        assert abs(d.pf.fees - 1.23) < 1e-9


class TestMarkIsNotLiquidation:

    def test_an_unknown_depth_gives_NOT_IDENTIFIED_not_the_mark_again(self):
        """Quoting the mark a second time under another name is the
        exact overstatement this separation exists to stop."""
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits())
        d.pf.buy("C", 0, 100, 0.50, 0.0, 1)
        m = d.pf.mark({("C", 0): {"price": 0.60}})     # no depth
        assert m["inventory_mark_usd"] == 60.0
        assert m["executable_liquidation_usd"] == DK.NOT_IDENTIFIED
        assert m["legs_without_depth"] == 1

    def test_with_depth_the_liquidation_uses_the_bid_and_the_size(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits())
        d.pf.buy("C", 0, 100, 0.50, 0.0, 1)
        m = d.pf.mark({("C", 0): {"price": 0.60, "bid": 0.58,
                                  "executable_qty": 40}})
        assert m["inventory_mark_usd"] == 60.0
        assert abs(m["executable_liquidation_usd"] - 40 * 0.58) < 1e-9


class TestRiskIsInThePathNotAfterIt:

    def test_an_order_over_the_size_limit_is_never_created(self):
        d = mk(max_order_usd=10.0, order_usd=100.0)
        got = d.step(ev(1, 0.50))
        assert d.open_orders() == []
        assert got[-1]["action"] == DK.NO_TRADE
        assert "order_usd" in got[-1]["risk"]["failed"]

    def test_the_refusal_is_a_recorded_decision_with_its_numbers(self):
        d = mk(max_order_usd=10.0, order_usd=100.0)
        got = d.step(ev(1, 0.50))
        chk = got[-1]["risk"]["checks"]["order_usd"]
        assert chk["ok"] is False
        assert chk["limit"] == 10.0 and chk["value"] > 10.0

    def test_cash_cannot_go_negative_on_a_fill(self):
        d = mk(starting_cash=5.0, max_order_usd=1e9, order_usd=100.0)
        d.step(ev(1, 0.50))
        if d.open_orders():
            d.step(ev(2, 0.50, size=1e6, eid="f"))
        assert d.pf.cash >= -1e-9

    def test_the_loss_limit_blocks_new_entries(self):
        d = mk(loss_limit_usd=10.0)
        d.pf.realized = -50.0
        got = d.step(ev(1, 0.50))
        assert got[-1]["action"] == DK.NO_TRADE
        assert "loss_limit" in got[-1]["risk"]["failed"]

    def test_outside_the_band_is_a_recorded_NO_TRADE_not_silence(self):
        """A legitimate refusal is evidence of a decision. It must be
        written down, not merely produce no order."""
        d = mk()
        got = d.step(ev(1, 0.95))
        assert got[-1]["action"] == DK.NO_TRADE
        assert got[-1]["ev"]["reason_code"] == "OUTSIDE_ENTRY_BAND"
        assert d.open_orders() == []


class TestPairingAndTheMergeThatDoesNotExist:

    def test_there_is_no_merge_action_anywhere(self):
        """RETAIL_NATIVE_MERGE_AVAILABLE = NO. An unsupported action
        must stay visibly unsupported."""
        src = open(DK.__file__).read()
        assert '"MERGE"' not in src
        assert "MERGE = " not in src

    def test_completion_is_named_an_ordinary_buy_of_the_complement(self):
        d = mk(order_usd=100.0)
        d.pf.buy("C1", 0, 100, 0.40, 0.0, 1)
        d.last_price[("C1", 1)] = 0.50           # 0.40 + 0.50 < 1.00
        got = d.step(ev(2, 0.50, cond="C1", oi=1))
        acts = [g["action"] for g in got]
        assert DK.COMPLETE_PAIR in acts
        d2 = [g for g in got if g["action"] == DK.COMPLETE_PAIR][0]
        assert "NOT_AVAILABLE_ON_PMUS_RETAIL" in d2["ev"]["merge"]
        o = [o for o in d.open_orders() if o.intent == DK.COMPLETE_PAIR][0]
        assert o.side == "BUY" and o.outcome_index == 1

    def test_completion_is_refused_when_the_pair_would_lock_a_loss(self):
        """Ferrari locked one on 37% of its completed pairs. Pairing
        frequently is not pairing profitably."""
        d = mk()
        d.pf.buy("C1", 0, 100, 0.60, 0.0, 1)
        d.last_price[("C1", 1)] = 0.55           # 0.60 + 0.55 > 1.00
        got = d.step(ev(2, 0.55, cond="C1", oi=1))
        assert DK.COMPLETE_PAIR not in [g["action"] for g in got]


class TestTheResidualRuleIsTheLearnedOne:

    def test_without_a_curve_the_basis_is_named_as_the_price_itself(self):
        d = mk()
        d.pf.buy("C1", 0, 100, 0.50, 0.0, 1)
        got = d.step(ev(2, 0.50, cond="C1", oi=0))
        r = [g for g in got if g["action"] in (DK.HOLD, DK.EXIT)][0]
        assert r["ev"]["ev_hold_basis"] == "IDENTITY_PRICE_IS_PROBABILITY"

    def test_with_a_curve_the_exit_follows_the_learned_value(self):
        class Curve:
            def predict(self, p):
                return 0.10                      # says the leg is poor
        d = mk()
        d.policy.curve = Curve()
        d.policy.curve_sha = "abc123"
        d.pf.buy("C1", 0, 100, 0.50, 0.0, 1)
        got = d.step(ev(2, 0.50, cond="C1", oi=0))
        r = [g for g in got if g["action"] in (DK.HOLD, DK.EXIT)][0]
        assert r["action"] == DK.EXIT
        assert r["ev"]["ev_hold_basis"] == "LEARNED_ISOTONIC"
        assert r["learned_artifact_sha"] == "abc123"

    def test_p_fill_is_never_filled_in(self):
        """bettor_p_fill holds this NOT_IDENTIFIED and the desk does not
        quietly identify it."""
        d = mk()
        d.pf.buy("C1", 0, 100, 0.50, 0.0, 1)
        got = d.step(ev(2, 0.50, cond="C1", oi=0))
        r = [g for g in got if g["action"] in (DK.HOLD, DK.EXIT)][0]
        assert r["ev"]["p_fill"] == DK.NOT_IDENTIFIED
        assert d.snapshot()["p_fill"] == DK.NOT_IDENTIFIED

    def test_every_decision_carries_the_assumption_it_was_made_under(self):
        d = mk(qs=0.33)
        got = d.step(ev(1, 0.50))
        assert got[-1]["queue_share_ASSUMED"] == 0.33
        assert got[-1]["fill_model"] == DK.FILL_MODEL


class TestLifecycleAndRecovery:

    def test_an_order_expires_rather_than_resting_for_ever(self):
        d = mk(expiry_s=10.0)
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(100, 0.95, cond="OTHER", oi=0, eid="x"))
        assert o.state == DK.EXPIRED
        assert "expiry reached" in o.state_reason

    def test_the_full_transition_history_is_kept(self):
        d = mk()
        d.step(ev(1, 0.50))
        o = d.open_orders()[0]
        d.step(ev(2, 0.50, size=o.qty / 2, eid="a"))
        d.step(ev(3, 0.50, size=1e6, eid="b"))
        states = [e["to"] for e in o.events]
        assert states == [DK.RESTING, DK.PARTIALLY_FILLED, DK.FILLED]

    def test_a_decision_links_to_the_order_it_produced(self):
        d = mk()
        got = d.step(ev(1, 0.50))
        did = got[-1]["desk_decision_id"]
        assert d.open_orders()[0].decision_id == did

    def test_the_snapshot_reconciles_and_says_so(self):
        d = mk()
        d.step(ev(1, 0.50))
        d.step(ev(2, 0.50, size=1e6, eid="f"))
        s = d.snapshot()
        assert s["invariant"]["ok"] is True
        assert s["orders"]["by_state"][DK.FILLED] == 1
        assert s["policy"]["maturity"]["residual_exit"] == "LEARNED"
        assert s["policy"]["maturity"]["fill_probability"] == "ASSUMED"

    def test_replaying_the_same_events_gives_the_same_result(self):
        """Determinism. Two runs over one tape must reconcile
        identically, or nothing downstream can be trusted."""
        evts = [ev(1, 0.50), ev(2, 0.50, size=50.0, eid="a"),
                ev(3, 0.48, size=50.0, eid="b"), ev(4, 0.62, eid="c")]
        outs = []
        for _ in range(2):
            d = mk()
            for e in evts:
                d.step(dict(e))
            outs.append(d.snapshot()["portfolio"])
        assert outs[0] == outs[1]


class TestTheInvariantCatchesFeeLeakage:
    """THE DEFECT THE FIRST REAL REPLAY FOUND. An entry fee left cash
    without entering the cost basis or realised P&L, so it escaped the
    identity -- and since the PMUS maker theta is a REBATE, the leak ran
    in our favour. Drift was +301.72 on a $100k book."""

    def test_the_identity_holds_with_a_taker_fee_on_entry(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1000.0))
        d.pf.buy("C", 0, 100, 0.50, 2.50, 1)
        inv = d.pf.invariant()
        assert inv["ok"], inv
        assert abs(d.pf.realized + 2.50) < 1e-9

    def test_the_identity_holds_with_a_maker_REBATE_on_entry(self):
        """The sign that actually bit. A rebate is income, and income
        that is not booked shows up as drift."""
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=1000.0))
        d.pf.buy("C", 0, 100, 0.50, -1.75, 1)
        inv = d.pf.invariant()
        assert inv["ok"], inv
        assert abs(d.pf.realized - 1.75) < 1e-9

    def test_the_identity_holds_through_buy_sell_and_settle_with_fees(self):
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=5000.0))
        d.pf.buy("C", 0, 200, 0.45, -0.90, 1)
        d.pf.buy("C", 1, 200, 0.52, -1.10, 2)
        d.pf.sell("C", 0, 50, 0.48, 0.30, 3)
        d.pf.settle("C", 1, 1.0, 4)
        inv = d.pf.invariant()
        assert inv["ok"], inv

    def test_a_nonzero_drift_is_reported_as_not_ok(self):
        """A control: the invariant must be able to FAIL, or its
        passing means nothing."""
        d = DK.Desk(policy=DK.Policy(), limits=DK.Limits(starting_cash=100.0))
        d.pf.cash += 7.0                      # money from nowhere
        inv = d.pf.invariant()
        assert inv["ok"] is False
        assert abs(inv["drift"] - 7.0) < 1e-9
