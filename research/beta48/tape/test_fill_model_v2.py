#!/usr/bin/env python3
"""The counterfactual fill model, pinned against the ways it could overclaim.

Every test here guards one specific route from "a trade happened" to "we would
have been filled", which is the inference the whole model exists to refuse to
make casually.
"""
from __future__ import annotations

import ast
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fill_model_v2 as M  # noqa: E402


def T(t, px, qty, symbol="s"):
    return {"time": t, "symbol": symbol, "price": px, "qty": qty}


class TheModelNeverAssertsAnActualFill(unittest.TestCase):

    def test_the_word_is_not_in_the_file(self):
        """Structural, not stylistic. A field called BETTOR_FILLED would be
        read as a fill by the next person whatever the docstring says, so the
        name must not exist to be read."""
        src = (HERE / "fill_model_v2.py").read_text()
        tree = ast.parse(src)
        names = {n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for banned in ("BETTOR_FILLED", "ACTUAL_FILL", "FILLED"):
            self.assertNotIn(banned, names, banned)

    def test_every_row_carries_the_four_unmeasurables(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 0)
        row = M.evaluate(q, [T(1, "0.30", 100)])
        for f in ("ACTUAL_BETTOR_FILL", "ACTUAL_BETTOR_FILL_PROBABILITY",
                  "TRADE_AGGRESSOR", "ACTUAL_BETTOR_QUEUE_POSITION"):
            self.assertEqual(row[f], "NOT_IDENTIFIED", f)

    def test_volume_exceeding_the_queue_is_support_not_a_fill(self):
        """The exact conversion the instruction forbids: TRADED_VOLUME >=
        QUEUE_AHEAD must not silently become a fill."""
        q = M.Quote(0, "s", "BID", "0.30", 10, 50)
        row = M.evaluate(q, [T(1, "0.30", 1000)])
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "YES")
        self.assertEqual(row["ACTUAL_BETTOR_FILL"], "NOT_IDENTIFIED")


class TheHierarchyIsMonotone(unittest.TestCase):
    """F0 <= F1 <= F2 <= F3 has to hold for every input, or the pessimistic
    model can report support its own upper bound denies."""

    def _flags(self, row):
        return [row["COUNTERFACTUAL_FILL_SUPPORTED_F0"],
                row["COUNTERFACTUAL_FILL_SUPPORTED_F1"],
                row["COUNTERFACTUAL_FILL_SUPPORTED_F2"],
                row["F3_TOUCH_ONLY"]]

    def test_across_a_sweep_of_volumes_and_queues(self):
        for ahead in (0, 10, 100, 1000):
            for vol in (0, 1, 10, 50, 100, 500, 5000):
                for removed in (None, 0, 25, 900):
                    q = M.Quote(0, "s", "BID", "0.30", 10, ahead)
                    row = M.evaluate(q, [T(1, "0.30", vol)] if vol else [],
                                     depth_removed_ahead=removed)
                    f = [x == "YES" for x in self._flags(row)]
                    self.assertTrue(f[3] >= f[2] >= f[1] >= f[0],
                                    (ahead, vol, removed, f))

    def test_f0_demands_the_whole_quote_traded_through(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 50)
        near = M.evaluate(q, [T(1, "0.30", 55)])     # past the queue, not us
        self.assertEqual(near["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "YES")
        self.assertEqual(near["COUNTERFACTUAL_FILL_SUPPORTED_F0"], "NO")
        full = M.evaluate(q, [T(1, "0.30", 60)])     # queue + our whole size
        self.assertEqual(full["COUNTERFACTUAL_FILL_SUPPORTED_F0"], "YES")

    def test_f3_is_an_upper_bound_and_is_not_called_a_fill(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 10 ** 9)
        row = M.evaluate(q, [T(1, "0.30", 1)])
        self.assertEqual(row["F3_TOUCH_ONLY"], "YES")
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "NO")
        self.assertNotIn("COUNTERFACTUAL_FILL_SUPPORTED_F3", row)


class CancellationsAreNotExecutions(unittest.TestCase):

    def test_f0_and_f1_credit_a_cancellation_at_zero(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 100)
        row = M.evaluate(q, [T(1, "0.30", 5)], depth_removed_ahead=200)
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F0"], "NO")
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "NO")

    def test_only_f2_credits_observed_disappearance(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 100)
        row = M.evaluate(q, [T(1, "0.30", 5)], depth_removed_ahead=200)
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F2"], "YES")

    def test_an_unobserved_interval_is_not_evidence_of_disappearance(self):
        """`None` means we did not look. It must not behave like a zero OR
        like a credit -- so F2 collapses to F1 rather than inventing one."""
        q = M.Quote(0, "s", "BID", "0.30", 10, 100)
        row = M.evaluate(q, [T(1, "0.30", 5)], depth_removed_ahead=None)
        self.assertEqual(row["VISIBLE_DEPTH_REMOVED_AHEAD"], None)
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F2"],
                         row["COUNTERFACTUAL_FILL_SUPPORTED_F1"])


class TradingThroughIsNotTradingAtOurPrice(unittest.TestCase):
    """A resting BID is passed over by trades BELOW it. Counting those as
    volume at our level would manufacture fills out of a falling market."""

    def test_a_bid_is_not_filled_by_prints_below_it(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 5)
        row = M.evaluate(q, [T(1, "0.25", 10_000)])
        self.assertEqual(row["TRADED_VOLUME_AT_PRICE"], D("0"))
        self.assertEqual(row["TRADED_VOLUME_THROUGH_PRICE"], D("10000"))
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "NO")
        self.assertEqual(row["F3_TOUCH_ONLY"], "YES")

    def test_the_ask_side_mirrors_it(self):
        q = M.Quote(0, "s", "ASK", "0.30", 10, 5)
        row = M.evaluate(q, [T(1, "0.35", 10_000)])
        self.assertEqual(row["TRADED_VOLUME_THROUGH_PRICE"], D("10000"))
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "NO")

    def test_prints_before_the_hypothetical_insert_do_not_count(self):
        """The quote did not exist yet. Counting earlier volume would give a
        quote credit for a queue it never joined."""
        q = M.Quote(100, "s", "BID", "0.30", 10, 5)
        row = M.evaluate(q, [T(1, "0.30", 10_000), T(101, "0.30", 1)])
        self.assertEqual(row["TRADED_VOLUME_AT_PRICE"], D("1"))

    def test_another_symbols_prints_are_dropped_not_tolerated(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 5)
        row = M.evaluate(q, [T(1, "0.30", 10_000, symbol="other")])
        self.assertEqual(row["TRADED_VOLUME_AT_PRICE"], D("0"))


class TheTapeHasNoSide(unittest.TestCase):

    def test_the_absence_is_recorded_on_every_row(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 0)
        row = M.evaluate(q, [T(1, "0.30", 100)])
        self.assertEqual(row["DEPTH_CONSUMPTION_SIDE"], "NOT_IDENTIFIED")

    def test_the_field_inventory_matches_the_captured_page(self):
        self.assertEqual(M.TRADE_SIDE, "NO")
        self.assertEqual(M.AGGRESSOR, "NO")
        self.assertEqual(M.PARTICIPANT_IDENTITY, "NO")
        for f in ("EXECUTION_TIMESTAMP", "EXECUTION_PRICE",
                  "EXECUTION_QUANTITY", "SYMBOL"):
            self.assertEqual(getattr(M, f), "YES", f)
        self.assertEqual(M.AUTH_REQUIRED, "NO")


class MarkoutSignsAndGaps(unittest.TestCase):

    def test_a_buyer_gains_when_the_price_rises(self):
        out = M.markout("0.30", "BID", [T(5, "0.34", 1)], "s", 0)
        self.assertEqual(out["MARKOUT_5S"], D("0.04"))

    def test_a_seller_gains_when_the_price_falls(self):
        """Getting this sign backwards converts adverse selection into edge."""
        out = M.markout("0.30", "ASK", [T(5, "0.26", 1)], "s", 0)
        self.assertEqual(out["MARKOUT_5S"], D("0.04"))
        bad = M.markout("0.30", "ASK", [T(5, "0.34", 1)], "s", 0)
        self.assertEqual(bad["MARKOUT_5S"], D("-0.04"))

    def test_no_print_in_the_horizon_is_not_identified_not_zero(self):
        """At ~1.7% of 30-second intervals carrying any trade at all, "nobody
        traded" is most of the data. Recording it as 0.0 would report a market
        that never moves."""
        out = M.markout("0.30", "BID", [T(600, "0.90", 1)], "s", 0)
        self.assertEqual(out["MARKOUT_5S"], "NOT_IDENTIFIED")
        self.assertEqual(out["MARKOUT_60S"], "NOT_IDENTIFIED")
        self.assertNotEqual(out["MARKOUT_5S"], 0)

    def test_the_one_second_horizon_is_conditional_on_unknown_precision(self):
        """The captured page documents a Transaction Time and does not state
        its resolution, so whether MARKOUT_1S is measurable is a question about
        the data."""
        self.assertEqual(M.TAPE_TIMESTAMP_PRECISION, "NOT_IDENTIFIED")
        self.assertEqual(M.MARKOUT_1S_FEASIBLE, "NOT_IDENTIFIED")

    def test_the_two_populations_are_never_pooled(self):
        s = M.split_markouts([1, 2], [3])
        self.assertTrue(s["THESE_ARE_NEVER_POOLED"])
        self.assertNotEqual(s["MARKOUT_AFTER_ANY_TRADE"],
                            s["MARKOUT_AFTER_COUNTERFACTUAL_MAKER_FILL"])


class TheEconomicReconstruction(unittest.TestCase):

    def _full(self, **over):
        base = dict(GROSS_FAIR_VALUE_EDGE="0", SPREAD_CAPTURE="0.01",
                    MAKER_REBATE="0.001", LIQUIDITY_INCENTIVE="0",
                    FILL_INCENTIVE="0", OTHER_VERIFIED_INCENTIVE="0",
                    ADVERSE_SELECTION="0", RESIDUAL_INVENTORY_COST="0",
                    EXIT_COST="0")
        base.update(over)
        return base

    def test_one_unmeasured_term_refuses_the_whole_sum(self):
        """A sum with a NOT_IDENTIFIED term is not a smaller number, it is not
        a number. Substituting zero is how an unmeasured cost disappears."""
        row = M.reconstruct(**self._full(ADVERSE_SELECTION="NOT_IDENTIFIED"))
        self.assertEqual(row["TOTAL_NET_INCL_INCENTIVES"], "NOT_IDENTIFIED")
        self.assertEqual(row["TRADING_NET_EX_INCENTIVES"], "NOT_IDENTIFIED")
        self.assertEqual(row["UNMEASURED_TERMS"], ["ADVERSE_SELECTION"])

    def test_the_two_nets_are_reported_separately(self):
        row = M.reconstruct(**self._full(LIQUIDITY_INCENTIVE="0.05"))
        self.assertNotEqual(row["TRADING_NET_EX_INCENTIVES"],
                            row["TOTAL_NET_INCL_INCENTIVES"])

    def test_negative_ex_incentives_and_positive_with_is_labelled(self):
        row = M.reconstruct(**self._full(ADVERSE_SELECTION="0.05",
                                         LIQUIDITY_INCENTIVE="0.20"))
        self.assertLess(row["TRADING_NET_EX_INCENTIVES"], 0)
        self.assertGreater(row["TOTAL_NET_INCL_INCENTIVES"], 0)
        self.assertEqual(row["EDGE_CLASSIFICATION"], "INCENTIVE_DEPENDENT")

    def test_incentive_dependent_is_never_called_a_structural_edge(self):
        row = M.reconstruct(**self._full(ADVERSE_SELECTION="0.05",
                                         LIQUIDITY_INCENTIVE="0.20"))
        self.assertNotEqual(row["EDGE_CLASSIFICATION"],
                            "STRUCTURAL_TRADING_EDGE")

    def test_a_real_trading_edge_is_named_as_one(self):
        row = M.reconstruct(**self._full(GROSS_FAIR_VALUE_EDGE="0.02"))
        self.assertEqual(row["EDGE_CLASSIFICATION"], "STRUCTURAL_TRADING_EDGE")

    def test_the_basis_travels_with_the_number(self):
        row = M.reconstruct(**self._full())
        self.assertEqual(row["BASIS"],
                         "COUNTERFACTUAL_MAKER_FILL_NOT_ACTUAL_FILL")


class TheMatchingRuleCarriesItsException(unittest.TestCase):

    def test_price_time_is_recorded_as_current(self):
        self.assertEqual(M.CURRENT_MATCHING_PRIORITY, "PRICE_TIME")
        self.assertEqual(M.BETTER_PRICE_PRIORITY, "YES")
        self.assertEqual(M.SAME_PRICE_TIME_PRIORITY, "YES")

    def test_the_rulebook_exception_is_not_dropped(self):
        """The Rulebook permits a different algorithm per contract after
        notice, so price-time is the current rule and not a law of nature."""
        self.assertEqual(M.MATCHING_ALGORITHM_EXCEPTION_POSSIBLE, "YES")
        self.assertEqual(M.CONTRACT_SPECIFIC_ALGORITHM_OVERRIDE_POSSIBLE,
                         "YES")
        self.assertEqual(M.CHECK_PRODUCT_SPECIFIC_MATCHING_NOTICE, "REQUIRED")
        self.assertEqual(M.PRODUCT_NOTICE_CHECK_REQUIRED_BEFORE_LIVE, "YES")


class ThisFileContactsNothing(unittest.TestCase):

    def test_it_imports_no_network_library(self):
        tree = ast.parse((HERE / "fill_model_v2.py").read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(x.name.split(".")[0] for x in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertLessEqual(mods, {"decimal", "__future__"}, mods)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class QueueDepletionIsNotAFill(unittest.TestCase):
    """The defect the monotonicity sweep caught in the first version of this
    file: F2 credited a fill where NOTHING traded, because the queue ahead had
    been cancelled away. Reaching the front of an untouched queue leaves a
    maker holding exactly zero contracts."""

    def test_a_queue_cleared_by_cancellation_alone_is_not_support(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 100)
        row = M.evaluate(q, [], depth_removed_ahead=900)
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F2"], "NO")
        self.assertEqual(row["F3_TOUCH_ONLY"], "NO")

    def test_cancellation_plus_a_trade_at_our_price_is_support(self):
        q = M.Quote(0, "s", "BID", "0.30", 10, 100)
        row = M.evaluate(q, [T(1, "0.30", 5)], depth_removed_ahead=900)
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F1"], "NO")
        self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F2"], "YES")

    def test_f2_can_never_exceed_its_own_touch_upper_bound(self):
        for ahead in (0, 100):
            for removed in (None, 0, 10 ** 6):
                q = M.Quote(0, "s", "BID", "0.30", 10, ahead)
                row = M.evaluate(q, [], depth_removed_ahead=removed)
                self.assertEqual(row["F3_TOUCH_ONLY"], "NO")
                self.assertEqual(row["COUNTERFACTUAL_FILL_SUPPORTED_F2"],
                                 "NO", (ahead, removed))
