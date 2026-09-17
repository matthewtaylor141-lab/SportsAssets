#!/usr/bin/env python3
"""WHALE PAIRS: a BUY-only stream still contains the whole pair mechanism.

THE CLAIM THIS FILE EXISTS TO KILL. The EV_CORE_V1 report said pair completion
and residual economics were untestable because the corpus has no SELLs. That
was wrong. On a two-token binary venue an account closes exposure by BUYING the
complement, so every pair is visible in BUY flow alone.

THE SECOND CLAIM IT KILLS. An opposite-side buy is not automatically a completed
pair. It may partially pair, exactly pair, or overshoot into residual on the
other side, and conflating those misstates the economics.

THE THIRD. A pair's result is locked at acquisition and a residual's is not.
Adding them hides the exact failure the whale cohort work identified.

Nothing here contacts a venue.
"""
import unittest
from decimal import Decimal as D
from pathlib import Path

import whale_pairs as WP

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def buy(ts, idx, qty, px, cid="C1"):
    return {"ts": ts, "outcome_index": idx, "size": str(qty),
            "price": str(px), "side": "BUY", "condition_id": cid,
            "outcome": "YES" if idx == 0 else "NO"}


class ABuyOnlyStreamContainsPairs(unittest.TestCase):

    def test_buying_the_complement_creates_matched_inventory(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:10:00Z", 1, 100, "0.55")])
        self.assertEqual(D(r["MATCHED_QTY"]), D(100))
        self.assertTrue(r["EVER_PAIRED"])

    def test_the_correction_is_recorded_in_the_module(self):
        rep = WP.reconstruct_all([])
        self.assertIn("BUY_ONLY_UNTESTABLE", rep["CORRECTION_OF"])
        self.assertIn("complement is acquired by buying", rep["CORRECTION"])

    def test_one_leg_alone_is_never_matched(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40")])
        self.assertEqual(D(r["MATCHED_QTY"]), D(0))
        self.assertFalse(r["EVER_PAIRED"])

    def test_time_from_first_leg_to_complement_is_measured(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:05:00Z", 1, 100, "0.55")])
        self.assertEqual(r["TIME_FIRST_LEG_TO_COMPLEMENT"], 300.0)


class AnOppositeBuyIsNotAutomaticallyAFullPair(unittest.TestCase):

    def test_a_smaller_complement_only_partially_pairs(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 40, "0.55")])
        self.assertEqual(D(r["MATCHED_QTY"]), D(40))
        self.assertEqual(D(r["YES_RESIDUAL_QTY"]), D(60))
        self.assertEqual(D(r["NO_RESIDUAL_QTY"]), D(0))
        self.assertIn("PARTIAL_PAIR", r["INCREMENT_CLASSES"])

    def test_an_equal_complement_pairs_exactly(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 100, "0.55")])
        self.assertIn("EXACT_PAIR", r["INCREMENT_CLASSES"])
        self.assertEqual(D(r["YES_RESIDUAL_QTY"]), D(0))

    def test_a_larger_complement_overshoots_into_the_other_residual(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 160, "0.55")])
        self.assertEqual(D(r["MATCHED_QTY"]), D(100))
        self.assertEqual(D(r["NO_RESIDUAL_QTY"]), D(60))
        self.assertIn("PAIR_AND_OVERSHOOT", r["INCREMENT_CLASSES"])

    def test_adding_to_the_same_leg_pairs_nothing(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 0, 50, "0.42")])
        self.assertEqual(D(r["MATCHED_QTY"]), D(0))
        self.assertIn("ADDS_TO_EXISTING_LEG", r["INCREMENT_CLASSES"])

    def test_all_four_increment_classes_exist(self):
        for c in ("OPENS_NEW_LEG", "PARTIAL_PAIR", "EXACT_PAIR",
                  "PAIR_AND_OVERSHOOT", "ADDS_TO_EXISTING_LEG"):
            self.assertIn(c, WP.INCREMENT_CLASSES, c)


class PairEconomicsUseCostBasisNotQuantity(unittest.TestCase):

    def test_a_cheap_pair_locks_a_profit(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 100, "0.55")])
        self.assertEqual(D(r["MATCHED_PAIR_BASIS"]), D("0.95"))
        self.assertEqual(D(r["MATCHED_LOCKED_PNL"]), D("5.00"))
        self.assertFalse(r["PAIR_BASIS_ABOVE_ONE"])

    def test_an_expensive_pair_locks_a_loss_and_says_so(self):
        """The Ferrari lesson: a completed pair can be a guaranteed loss."""
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.60"),
                                   buy("2026-09-01T10:01:00Z", 1, 100, "0.55")])
        self.assertEqual(D(r["MATCHED_PAIR_BASIS"]), D("1.15"))
        self.assertTrue(r["PAIR_BASIS_ABOVE_ONE"])
        self.assertLess(D(r["MATCHED_LOCKED_PNL"]), 0)

    def test_matched_pnl_does_not_depend_on_the_outcome(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 100, "0.55")])
        a = WP.settle_market(r, {"YES": 1.0, "NO": 0.0})
        b = WP.settle_market(r, {"YES": 0.0, "NO": 1.0})
        self.assertEqual(a["SETTLED_MATCHED_PNL"], b["SETTLED_MATCHED_PNL"])
        self.assertTrue(a["MATCHED_PNL_IS_OUTCOME_INDEPENDENT"])

    def test_residual_pnl_does_depend_on_the_outcome(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 40, "0.55")])
        # The leg key is the venue's 0/1 token index, so the payout map needs
        # a mapper. Without one the residual is NOT_IDENTIFIED -- which is the
        # correct refusal, and is asserted separately below.
        leg = lambda i: "YES" if i == 0 else "NO"
        win = WP.settle_market(r, {"YES": 1.0, "NO": 0.0}, outcome_of_leg=leg)
        lose = WP.settle_market(r, {"YES": 0.0, "NO": 1.0}, outcome_of_leg=leg)
        self.assertNotEqual(win["SETTLED_RESIDUAL_PNL"],
                            lose["SETTLED_RESIDUAL_PNL"])

    def test_an_unmappable_leg_refuses_the_residual_rather_than_guessing(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.40"),
                                   buy("2026-09-01T10:01:00Z", 1, 40, "0.55")])
        s = WP.settle_market(r, {"YES": 1.0, "NO": 0.0})   # no mapper supplied
        self.assertEqual(s["SETTLED_RESIDUAL_PNL"], NOT_IDENTIFIED)
        self.assertFalse(s["RESIDUAL_PNL_COMPLETE"])

    def test_matched_and_residual_are_reported_separately(self):
        r = WP.reconstruct_market([buy("2026-09-01T10:00:00Z", 0, 100, "0.60"),
                                   buy("2026-09-01T10:01:00Z", 1, 40, "0.55")])
        s = WP.settle_market(r, {"YES": 0.0, "NO": 1.0},
                             outcome_of_leg=lambda i: "YES" if i == 0 else "NO")
        self.assertIn("SETTLED_MATCHED_PNL", s)
        self.assertIn("SETTLED_RESIDUAL_PNL", s)
        self.assertIn("hides a pair-positive account",
                      r["MATCHED_AND_RESIDUAL_ARE_SEPARATE"])


class OnlySomeComponentsActuallyNeedSells(unittest.TestCase):

    def test_the_mechanism_as_a_whole_is_testable(self):
        rep = WP.reconstruct_all([])
        self.assertIn("pair completion timing and hazard",
                      rep["WHAT_IS_TESTABLE_WITHOUT_SELLS"])

    def test_only_named_components_require_sells(self):
        rep = WP.reconstruct_all([])
        for k in ("VOLUNTARY_EXIT_AT_A_PRICE", "REDUCTION_NOT_VIA_COMPLEMENT",
                  "REALISED_EXIT_MARKOUT"):
            self.assertIn(k, rep["REQUIRES_SELLS"], k)

    def test_a_sell_row_is_skipped_not_treated_as_a_buy(self):
        rows = [buy("2026-09-01T10:00:00Z", 0, 100, "0.40")]
        rows.append(dict(rows[0], side="SELL", ts="2026-09-01T10:01:00Z"))
        r = WP.reconstruct_market(rows)
        self.assertEqual(r["FILLS_SKIPPED"], 1)
        self.assertEqual(D(r["CUM_YES_QTY"]), D(100))

    def test_it_places_no_order(self):
        src = Path(WP.__file__).read_text()
        code = src.split('"""', 2)[2].lower()
        for bad in ("httpx", "requests.", "submit(", "https://"):
            self.assertNotIn(bad, code, bad)


if __name__ == "__main__":
    unittest.main()


class CoverageGatesEveryPairClaim(unittest.TestCase):
    """The retained corpus is 22.3% of RN1's trades. Partial history is fatal."""

    def test_a_complete_condition_is_recognised(self):
        cov = WP.coverage_status("C1", [1, 2, 3], {"C1": {1, 2, 3}})
        self.assertEqual(cov["COVERAGE"], "COMPLETE_ALL_TRADES_PROBED")
        self.assertEqual(cov["MISSING_FILLS"], 0)

    def test_a_partial_condition_is_flagged_with_its_gap(self):
        cov = WP.coverage_status("C1", [1, 2], {"C1": {1, 2, 3, 4}})
        self.assertEqual(cov["COVERAGE"], "PARTIAL_PROBE_COVERAGE")
        self.assertEqual(cov["MISSING_FILLS"], 2)
        self.assertAlmostEqual(cov["FRACTION"], 0.5)

    def test_an_uncheckable_condition_is_not_assumed_complete(self):
        cov = WP.coverage_status("C9", [1], {})
        self.assertEqual(cov["COVERAGE"], "COVERAGE_NOT_CHECKABLE")

    def test_the_account_is_proven_from_the_spec_not_inferred(self):
        self.assertEqual(WP.CORPUS_IS_SINGLE_ACCOUNT, "YES")
        self.assertEqual(WP.CORPUS_ACCOUNT, "RN1")
        self.assertIn("trades.whale_id = RN1", WP.ACCOUNT_PROVENANCE)

    def test_the_bias_directions_are_declared_and_differ(self):
        """Matched qty is a lower bound; basis cannot be signed at all."""
        self.assertEqual(WP.MATCHED_QTY_BIAS_ON_PARTIAL, "LOWER_BOUND")
        self.assertEqual(WP.BASIS_BIAS_ON_PARTIAL, "UNSIGNED")

    def test_a_missing_cheap_fill_moves_the_basis_the_other_way(self):
        """Why the basis cannot be signed: it depends which fill is missing."""
        seen = [buy("1", 0, 100, "0.70"), buy("2", 1, 100, "0.45")]
        with_cheap = seen + [buy("3", 0, 100, "0.10")]
        a = WP.pair_basis_by_method(seen, WP.METHOD_WEIGHTED)
        b = WP.pair_basis_by_method(with_cheap, WP.METHOD_WEIGHTED)
        self.assertGreater(a["MATCHED_PAIR_BASIS"], b["MATCHED_PAIR_BASIS"])


class TheAccountingConventionMustNotCreateTheFinding(unittest.TestCase):

    def test_all_three_conventions_are_implemented(self):
        self.assertEqual(len(WP.METHODS), 3)
        f = [buy("1", 0, 100, "0.40"), buy("2", 1, 100, "0.45")]
        for m in WP.METHODS:
            self.assertIsNotNone(WP.pair_basis_by_method(f, m), m)

    def test_identical_prices_give_identical_answers(self):
        f = [buy("1", 0, 100, "0.40"), buy("2", 1, 100, "0.45")]
        vals = {str(WP.pair_basis_by_method(f, m)["MATCHED_PAIR_BASIS"])
                for m in WP.METHODS}
        self.assertEqual(len(vals), 1)

    def test_conventions_can_disagree_when_prices_differ(self):
        """FIFO matches the earliest lots; weighted averages them."""
        f = [buy("1", 0, 100, "0.40"), buy("2", 0, 100, "0.70"),
             buy("3", 1, 100, "0.45")]
        w = WP.pair_basis_by_method(f, WP.METHOD_WEIGHTED)
        fi = WP.pair_basis_by_method(f, WP.METHOD_FIFO)
        self.assertNotEqual(w["MATCHED_PAIR_BASIS"], fi["MATCHED_PAIR_BASIS"])

    def test_robustness_reports_whether_the_sign_survives(self):
        f = [buy("1", 0, 100, "0.40"), buy("2", 0, 100, "0.70"),
             buy("3", 1, 100, "0.45")]
        r = WP.robustness(f)
        self.assertIn("ALL_METHODS_AGREE_ON_SIGN", r)
        self.assertIn("convention must not be what produces the finding",
                      r["WHY_THREE_METHODS"])

    def test_a_market_with_one_leg_has_no_pair_under_any_convention(self):
        f = [buy("1", 0, 100, "0.40")]
        self.assertEqual(WP.robustness(f)["PAIR_STATUS"], "NO_PAIR")
