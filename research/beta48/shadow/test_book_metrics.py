#!/usr/bin/env python3
"""Book-only measurements. Valuable, and none of them is a fill rate."""
import unittest
from decimal import Decimal as D

import book_metrics as BM

NI = BM.NOT_IDENTIFIED


def tick(seq, bid="0.54", ask="0.56", bid_qty="100", ask_qty="80", slug="m",
         bid_changed=False, ask_changed=False, depth_changed=False,
         life=0.0, kind="TICK"):
    return {
        "kind": kind, "slug": slug, "seq": seq, "ELAPSED_S": float(seq * 3),
        "BID": bid, "ASK": ask, "BID_QTY": bid_qty, "ASK_QTY": ask_qty,
        "SPREAD": str(D(ask) - D(bid)),
        "BID_LADDER": [[bid, bid_qty]], "ASK_LADDER": [[ask, ask_qty]],
        "BID_CHANGED": bid_changed, "ASK_CHANGED": ask_changed,
        "DEPTH_CHANGED": depth_changed,
        "TIME_AT_BID_S": life, "TIME_AT_ASK_S": life,
        "QUOTE_LIFETIME_S": life,
    }


class NothingHereIsAnExecutionOutcome(unittest.TestCase):

    def test_no_measured_key_is_named_like_a_fill(self):
        m = BM.report([tick(i) for i in range(5)])
        for k, v in m.items():
            if isinstance(v, bool) or not isinstance(v, (int, D)):
                continue
            for word in ("FILL", "EXECUTION", "TRADE"):
                self.assertNotIn(word, k, "%s is named like an outcome" % k)

    def test_the_forbidden_names_are_listed_and_absent(self):
        m = BM.report([tick(i) for i in range(3)])
        for name in BM.FORBIDDEN_RENAMES:
            self.assertNotIn(name, m)

    def test_the_through_metric_says_what_it_is_not(self):
        m = BM.book_metrics([tick(i) for i in range(3)])
        self.assertTrue(m["MARKET_MOVED_THROUGH_QUOTE_IS_NOT_AN_EXECUTION"])

    def test_the_report_says_why_no_rate_is_present(self):
        m = BM.report([tick(i) for i in range(3)])
        self.assertTrue(m["FILL_RATE_NOT_PRESENT"])
        self.assertIn("no order existed", m["WHY"])


class TheSpread(unittest.TestCase):

    def test_percentiles_come_from_observed_books_only(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.56"), tick(2, ask="0.60")]
        m = BM.book_metrics(rows)
        self.assertEqual(m["SPREAD_MIN"], D("0.01"))
        self.assertEqual(m["SPREAD_MAX"], D("0.06"))
        self.assertIn(m["SPREAD_P50"], (D("0.01"), D("0.02"), D("0.06")))

    def test_one_tick_uptime_is_the_share_of_one_tick_books(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.55"), tick(2, ask="0.60")]
        self.assertEqual(BM.book_metrics(rows)["ONE_TICK_UPTIME"],
                         D(2) / D(3))

    def test_spread_persistence_measures_how_long_a_spread_stands(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.55"), tick(2, ask="0.60")]
        m = BM.book_metrics(rows)
        self.assertGreaterEqual(m["SPREAD_PERSISTENCE_S_N"], 1)


class TheMovementRates(unittest.TestCase):

    def test_book_moves_are_counted_over_transitions_not_rows(self):
        rows = [tick(0), tick(1, bid_changed=True), tick(2)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["OBSERVED_TRANSITIONS"], 2)
        self.assertEqual(m["BOOK_MOVE_FREQUENCY"], D(1) / D(2))

    def test_price_improvement_is_a_better_bid_or_a_better_offer(self):
        rows = [tick(0), tick(1, bid="0.55", bid_changed=True),
                tick(2, bid="0.55", ask="0.555", ask_changed=True)]
        self.assertEqual(BM.book_metrics(rows)["PRICE_IMPROVEMENT_FREQUENCY"],
                         D(2) / D(2))

    def test_a_widening_book_is_not_an_improvement(self):
        rows = [tick(0), tick(1, bid="0.50", bid_changed=True)]
        self.assertEqual(BM.book_metrics(rows)["PRICE_IMPROVEMENT_FREQUENCY"],
                         D(0) / D(1))

    def test_the_mid_can_hold_while_the_touch_moves(self):
        rows = [tick(0, bid="0.54", ask="0.56"),
                tick(1, bid="0.53", ask="0.57", bid_changed=True,
                     ask_changed=True)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["BOOK_MOVE_FREQUENCY"], D(1))
        self.assertEqual(m["MID_MOVE_FREQUENCY"], D(0))

    def test_the_market_passing_our_level_is_counted_as_that_and_no_more(self):
        rows = [tick(0, bid="0.54"), tick(1, bid="0.52", ask="0.54")]
        m = BM.book_metrics(rows)
        self.assertEqual(m["MARKET_MOVED_THROUGH_QUOTE_FREQUENCY"], D(1))


class TheQueueAndTheDepth(unittest.TestCase):

    def test_queue_ahead_at_entry_is_the_displayed_touch_size(self):
        m = BM.book_metrics([tick(i, bid_qty="250") for i in range(3)])
        self.assertEqual(m["QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY_P50"], D("250"))

    def test_the_queue_change_is_measured_only_at_an_unmoved_price(self):
        rows = [tick(0, bid_qty="100"), tick(1, bid_qty="60"),
                tick(2, bid="0.53", bid_qty="900", bid_changed=True)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["DISPLAYED_QUEUE_CHANGE_N"], 1)
        self.assertEqual(m["DISPLAYED_QUEUE_CHANGE_MAX"], D("40"))

    def test_touch_size_is_reported_for_both_sides(self):
        m = BM.book_metrics([tick(i) for i in range(3)])
        self.assertEqual(m["TOUCH_SIZE_BID_P50"], D("100"))
        self.assertEqual(m["TOUCH_SIZE_ASK_P50"], D("80"))


class TheBookMarkout(unittest.TestCase):

    def test_it_measures_where_the_book_went_not_what_we_earned(self):
        rows = [tick(0, bid="0.54", ask="0.56"),
                tick(1, bid="0.60", ask="0.62")]
        m = BM.book_metrics(rows, horizons_s=(30,))
        self.assertEqual(m["POST_QUOTE_BOOK_MARKOUT_30S_MAX"], D("0.06"))

    def test_a_capture_with_no_later_row_gives_no_markout(self):
        m = BM.book_metrics([tick(0)], horizons_s=(30,))
        self.assertEqual(m["POST_QUOTE_BOOK_MARKOUT_30S_N"], 0)
        self.assertEqual(m["POST_QUOTE_BOOK_MARKOUT_30S_P50"], NI)


class TheCaptureItself(unittest.TestCase):

    def test_read_errors_are_counted_and_not_treated_as_books(self):
        rows = [tick(0), {"kind": "TICK_ERROR", "slug": "m", "seq": 1,
                          "ELAPSED_S": 3.0}, tick(2)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["TICK_ERRORS"], 1)
        self.assertEqual(m["OBSERVATIONS"], 2)

    def test_markets_are_kept_apart(self):
        rows = [tick(0, slug="a"), tick(0, slug="b"), tick(1, slug="a")]
        m = BM.book_metrics(rows)
        self.assertEqual(m["MARKETS"], 2)
        self.assertEqual(m["PER_SLUG"]["a"]["OBSERVED_TRANSITIONS"], 1)
        self.assertEqual(m["PER_SLUG"]["b"]["OBSERVED_TRANSITIONS"], 0)

    def test_an_empty_capture_is_not_identified_not_zero(self):
        m = BM.book_metrics([])
        self.assertEqual(m["ONE_TICK_UPTIME"], NI)
        self.assertEqual(m["BOOK_MOVE_FREQUENCY"], NI)

    def test_a_float_quantity_is_refused(self):
        bad = tick(0)
        bad["BID_QTY"] = 100.0
        with self.assertRaises(TypeError):
            BM.book_metrics([bad])


if __name__ == "__main__":
    unittest.main()
