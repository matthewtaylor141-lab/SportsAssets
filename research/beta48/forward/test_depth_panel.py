#!/usr/bin/env python3
"""Tests for the Target Size scoring-range rule.

The rule has four clauses and each one is a separate way to get it wrong:
sides scored independently, the walk goes outward from BEST, orders beyond the
qualifying range score ZERO, and RAW size sets the threshold. Every clause has
a test that fails if it is dropped.
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import depth_panel as P


def book(bids, offers):
    """A book in the venue's own shape: [(price, qty)] per side."""
    def side(rows):
        return [{"px": {"value": str(p), "currency": "USD"}, "qty": str(q)}
                for p, q in rows]
    return {"marketData": {"bids": side(bids), "offers": side(offers)}}


class TicksAreAPriceDistanceNotALadderIndex(unittest.TestCase):
    """THE SPARSE-LADDER TRAP. Observed books skip levels, so the Nth rung is
    not N ticks from best. Using the index would overstate every score."""

    def test_a_sparse_ladder_does_not_renumber_the_ticks(self):
        # Rungs at 0.145, 0.143, 0.142, 0.138 on a 0.001 tick: the FOURTH rung
        # is SEVEN ticks back, not three.
        for px, expect in ((D("0.145"), 0), (D("0.143"), 2),
                           (D("0.142"), 3), (D("0.138"), 7)):
            self.assertEqual(
                P.ticks_from_best(px, D("0.145"), D("0.001"), "bid"), expect)

    def test_the_same_prices_on_a_cent_tick_are_far_fewer_ticks(self):
        """A tick is not a cent. The board carries 0.001, 0.005 and 0.01."""
        self.assertEqual(
            P.ticks_from_best(D("0.14"), D("0.15"), D("0.01"), "bid"), 1)
        self.assertEqual(
            P.ticks_from_best(D("0.14"), D("0.15"), D("0.001"), "bid"), 10)

    def test_an_offer_is_worse_as_it_rises(self):
        self.assertEqual(
            P.ticks_from_best(D("0.152"), D("0.150"), D("0.001"), "ask"), 2)
        self.assertEqual(
            P.ticks_from_best(D("0.148"), D("0.150"), D("0.001"), "ask"), None)

    def test_a_price_better_than_best_is_not_a_tick_count(self):
        """A post-only maker quote cannot be inside the touch, so this is a
        None rather than a negative that would silently flatter a score."""
        self.assertIsNone(
            P.ticks_from_best(D("0.16"), D("0.15"), D("0.001"), "bid"))


class TheQualifyingWalk(unittest.TestCase):

    def test_the_walk_uses_raw_size_not_discounted_size(self):
        """The threshold clause. Discounting here would let a far-away level
        qualify a book that raw size says does not reach Target."""
        levels = [(D("0.50"), D("100"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), D("100"), "bid")
        self.assertEqual(d["FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED"], 0)
        # 100 raw at the touch meets a 100 target. Any discount would not.

    def test_it_walks_outward_from_best_in_order(self):
        levels = [(D("0.50"), D("10")), (D("0.49"), D("10")),
                  (D("0.48"), D("10")), (D("0.47"), D("10"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), D("25"), "bid")
        self.assertEqual(d["FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED"], 2)
        self.assertEqual(d["CUMULATIVE_SIZE_AT_BEST"], D("10"))
        self.assertEqual(d["CUMULATIVE_SIZE_1_TICK"], D("20"))
        self.assertEqual(d["CUMULATIVE_SIZE_2_TICKS"], D("30"))

    def test_a_book_that_never_reaches_target_says_so(self):
        levels = [(D("0.50"), D("5"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), D("1000"), "bid")
        self.assertIsNone(d["FIRST_LEVEL_WHERE_TARGET_SIZE_REACHED"])
        self.assertFalse(d["TARGET_SIZE_REACHED_IN_VISIBLE_BOOK"])

    def test_an_unknown_target_is_never_guessed(self):
        levels = [(D("0.50"), D("5"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), None, "bid")
        self.assertEqual(d["TARGET_SIZE_REACHED_IN_VISIBLE_BOOK"],
                         "NOT_IDENTIFIED")


class WouldOurQuoteScore(unittest.TestCase):

    def test_target_met_at_best_means_one_tick_back_scores_nothing(self):
        """THE PRACTICAL QUESTION. However large our quote, if the qualifying
        range closed at the touch we are outside it and score zero."""
        levels = [(D("0.50"), D("5000"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), D("500"), "bid")
        self.assertEqual(P.would_quote_score(d, 0, D("500")), "YES")
        self.assertEqual(P.would_quote_score(d, 1, D("500")), "NO")
        self.assertEqual(P.would_quote_score(d, 3, D("500")), "NO")

    def test_a_thin_touch_leaves_room_further_back(self):
        levels = [(D("0.50"), D("10")), (D("0.49"), D("10")),
                  (D("0.48"), D("10"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), D("25"), "bid")
        self.assertEqual(P.would_quote_score(d, 2, D("25")), "YES")
        self.assertEqual(P.would_quote_score(d, 3, D("25")), "NO")

    def test_an_unreached_target_is_answered_on_the_visible_book_only(self):
        """Not upgraded to a general YES: hidden or later size may still
        close the range before us."""
        levels = [(D("0.50"), D("5"))]
        d = P.qualifying_depth(levels, D("0.50"), D("0.01"), D("1000"), "bid")
        self.assertEqual(P.would_quote_score(d, 2, D("1000")),
                         "YES_ON_VISIBLE_BOOK")

    def test_no_target_means_no_answer(self):
        d = P.qualifying_depth([(D("0.5"), D("5"))], D("0.5"), D("0.01"),
                               None, "bid")
        self.assertEqual(P.would_quote_score(d, 0, None), "NOT_IDENTIFIED")


class SidesAreScoredIndependently(unittest.TestCase):

    def test_a_crowded_ask_does_not_disqualify_a_thin_bid(self):
        b = book(bids=[("0.49", "5")], offers=[("0.51", "100000")])
        row = P.panel_row(b, D("0.01"), D("500"), slug="x")
        self.assertEqual(row["ASK_TARGET_ALREADY_REACHED_AT_BEST"], True)
        self.assertEqual(row["BID_TARGET_ALREADY_REACHED_AT_BEST"], False)
        self.assertEqual(row["ASK_QUOTE_1_TICKS_BACK_SCORE_ELIGIBILITY"], "NO")
        self.assertEqual(row["BID_QUOTE_1_TICKS_BACK_SCORE_ELIGIBILITY"],
                         "YES_ON_VISIBLE_BOOK")

    def test_an_empty_side_is_recorded_not_skipped(self):
        row = P.panel_row(book(bids=[], offers=[("0.51", "10")]),
                          D("0.01"), D("5"))
        self.assertTrue(row["BID"]["BOOK_SIDE_EMPTY"])
        self.assertEqual(row["BID_WOULD_BETTOR_QUOTE_SCORE"], "NOT_IDENTIFIED")

    def test_the_ladder_order_is_imposed_not_trusted(self):
        """A response returned out of order must not invert 'outward from
        best'."""
        b = book(bids=[("0.45", "10"), ("0.50", "10"), ("0.48", "10")],
                 offers=[("0.55", "1"), ("0.52", "1")])
        bids, offers = P.book_sides(b)
        self.assertEqual([p for p, _ in bids],
                         [D("0.50"), D("0.48"), D("0.45")])
        self.assertEqual([p for p, _ in offers], [D("0.52"), D("0.55")])


class RewardShareStaysUnknowable(unittest.TestCase):

    def test_the_panel_never_estimates_a_reward(self):
        """By construction. A share needs every participant's qualifying score
        through time; a book snapshot cannot contain it."""
        row = P.panel_row(book([("0.49", "10")], [("0.51", "10")]),
                          D("0.01"), D("5"))
        self.assertEqual(row["ESTIMATED_REWARD"], "NOT_IDENTIFIED")
        self.assertEqual(row["ACTUAL_REWARD"], "NOT_IDENTIFIED")
        self.assertEqual(row["REWARD_SHARE"], "NOT_IDENTIFIED")

    def test_size_ahead_is_a_lower_bound_not_a_queue_position(self):
        """Time priority within a level is not observable, so this is what is
        at or better than our level -- never our place in the queue."""
        d = P.qualifying_depth([(D("0.50"), D("900"))], D("0.50"),
                               D("0.01"), D("100"), "bid")
        self.assertEqual(P.size_ahead_of_quote(d, 0), D("900"))


class TheDiscountIsNotTheThreshold(unittest.TestCase):

    def test_the_discount_ladder_is_exact(self):
        """0.30^3 = 0.027. NOT 0.0405."""
        self.assertEqual(P.liquidity_score(1, 0, D("0.30")), D("1"))
        self.assertEqual(P.liquidity_score(1, 1, D("0.30")), D("0.30"))
        self.assertEqual(P.liquidity_score(1, 2, D("0.30")), D("0.0900"))
        self.assertEqual(P.liquidity_score(1, 3, D("0.30")), D("0.027000"))

    def test_qualifying_is_decided_without_the_discount_factor(self):
        """qualifying_depth takes no discount factor at all -- which is the
        strongest possible statement that the discount cannot affect it."""
        import inspect
        self.assertNotIn("discount",
                         inspect.signature(P.qualifying_depth).parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
