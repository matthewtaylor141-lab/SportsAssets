#!/usr/bin/env python3
"""Book-state measurements from a SAMPLED capture. None of them is a fill rate."""
import unittest
from decimal import Decimal as D

import book_metrics as BM

NI = BM.NOT_IDENTIFIED


def tick(seq, bid="0.54", ask="0.56", bid_qty="100", ask_qty="80", slug="m",
         bid_changed=False, ask_changed=False, depth_changed=False,
         life=0.0, kind="TICK", elapsed=None):
    return {
        "kind": kind, "slug": slug, "seq": seq,
        "ELAPSED_S": float(seq * 3) if elapsed is None else elapsed,
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

    def test_the_report_says_why_no_rate_is_present(self):
        m = BM.report([tick(i) for i in range(3)])
        self.assertTrue(m["FILL_RATE_NOT_PRESENT"])
        self.assertIn("no order existed", m["WHY"])


class SamplingIsNotContinuousObservation(unittest.TestCase):

    def test_the_revisit_cadence_is_reported_before_anything_derived(self):
        c = BM.revisit_cadence([tick(i) for i in range(5)])
        self.assertEqual(c["MEDIAN_REVISIT_INTERVAL_S"], D("3"))
        self.assertEqual(c["P10_REVISIT_INTERVAL_S"], D("3"))
        self.assertEqual(c["P90_REVISIT_INTERVAL_S"], D("3"))
        self.assertEqual(c["MAX_REVISIT_INTERVAL_S"], D("3"))
        self.assertTrue(c["SAMPLING_IS_NOT_CONTINUOUS_OBSERVATION"])

    def test_a_long_hole_shows_up_in_the_max(self):
        rows = [tick(0, elapsed=0.0), tick(1, elapsed=3.0),
                tick(2, elapsed=303.0)]
        c = BM.revisit_cadence(rows)
        self.assertEqual(c["MAX_REVISIT_INTERVAL_S"], D("300"))
        self.assertEqual(c["MEDIAN_REVISIT_INTERVAL_S"], D("3"))

    def test_cadence_is_reported_per_market_too(self):
        rows = [tick(0, slug="a"), tick(1, slug="a"),
                tick(0, slug="b", elapsed=0.0), tick(1, slug="b", elapsed=60.0)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["PER_SLUG"]["a"]["MEDIAN_REVISIT_INTERVAL_S"],
                         D("3"))
        self.assertEqual(m["PER_SLUG"]["b"]["MEDIAN_REVISIT_INTERVAL_S"],
                         D("60"))

    def test_the_continuous_quantities_are_not_identified(self):
        m = BM.book_metrics([tick(i) for i in range(5)])
        self.assertEqual(m["TRUE_CONTINUOUS_QUOTE_LIFETIME"], NI)
        self.assertEqual(m["TRUE_CONTINUOUS_BOOK_UPDATE_RATE"], NI)
        self.assertEqual(m["FEED_IS_EVENT_COMPLETE"], NI)
        self.assertFalse(m["EVERY_INTRAINTERVAL_TRANSITION_OBSERVED"])

    def test_the_sampled_rates_carry_the_observed_suffix(self):
        m = BM.book_metrics([tick(i) for i in range(5)])
        for k in ("QUOTE_LIFETIME_OBSERVED_BID_S_P50",
                  "TIME_AT_PRICE_OBSERVED_S_P50",
                  "BOOK_UPDATE_RATE_OBSERVED",
                  "MARKET_MOVED_THROUGH_QUOTE_OBSERVED",
                  "MID_MOVE_FREQUENCY_OBSERVED",
                  "ONE_TICK_UPTIME_OBSERVED",
                  "SPREAD_PERSISTENCE_OBSERVED_S_P50"):
            self.assertIn(k, m, k)

    def test_the_unsuffixed_names_are_gone(self):
        m = BM.book_metrics([tick(i) for i in range(5)])
        for gone in ("BOOK_MOVE_FREQUENCY", "MID_MOVE_FREQUENCY",
                     "ONE_TICK_UPTIME", "DEPTH_CHANGE_RATE",
                     "MARKET_MOVED_THROUGH_QUOTE_FREQUENCY"):
            self.assertNotIn(gone, m, gone)

    def test_what_a_poll_cannot_see_is_listed(self):
        c = BM.revisit_cadence([tick(i) for i in range(3)])
        for miss in ("QUOTE_MOVED_AWAY_AND_RETURNED", "TRADED",
                     "DEPTH_CHANGED_AND_CHANGED_BACK",
                     "DISAPPEARED_AND_REAPPEARED"):
            self.assertIn(miss, c["UNOBSERVED_BETWEEN_POLLS"])

    def test_the_bias_direction_is_stated(self):
        m = BM.book_metrics([tick(i) for i in range(3)])
        self.assertTrue(m["OBSERVED_RATES_ARE_A_LOWER_BOUND_ON_ACTIVITY"])


class MoveThroughIsAPricePath(unittest.TestCase):

    def test_it_is_counted_and_labelled_as_what_it_is_not(self):
        rows = [tick(0, bid="0.54"), tick(1, bid="0.52", ask="0.54")]
        m = BM.book_metrics(rows)
        self.assertEqual(m["MARKET_MOVED_THROUGH_QUOTE_OBSERVED"], D(1))
        self.assertTrue(m["MOVE_THROUGH_IS_NOT_TRADE"])
        self.assertTrue(m["MOVE_THROUGH_IS_NOT_A_COUNTERFACTUAL_FILL"])

    def test_converting_it_to_a_fill_probability_raises(self):
        with self.assertRaises(BM.MoveThroughIsNotAFill):
            BM.move_through_fill_probability(D("0.5"))


class EventClusteringIsNotIndependence(unittest.TestCase):

    def _rows(self):
        # Three markets: a and b are the SAME event, c is its own.
        rows = []
        for slug, moved in (("a", True), ("b", True), ("c", False)):
            for i in range(3):
                rows.append(tick(i, slug=slug,
                                 bid_changed=(moved and i > 0)))
        return rows

    def test_both_weightings_are_reported(self):
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2"})
        self.assertEqual(m["WEIGHTING"], "MARKET_WEIGHTED")
        self.assertEqual(m["EVENTS_IN_WEIGHTING"], 2)
        self.assertTrue(m["ONE_EVENT_ONE_VOTE"])

    def test_one_event_family_cannot_dominate_the_event_weighted_figure(self):
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2"})
        # Market-weighted: 4 of 6 transitions moved. Event-weighted: E1 is 1.0
        # and E2 is 0.0, so the average is 0.5 -- the two-market event counts
        # once, not twice.
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], D(4) / D(6))
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED"],
                         D("0.5"))

    def test_without_an_event_map_the_event_weighting_is_not_identified(self):
        m = BM.book_metrics(self._rows())
        for k in BM.WEIGHTED_RATES:
            self.assertEqual(m[k + "_EVENT_WEIGHTED"], NI, k)


class TheSpread(unittest.TestCase):

    def test_percentiles_come_from_observed_books_only(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.56"), tick(2, ask="0.60")]
        m = BM.book_metrics(rows)
        self.assertEqual(m["SPREAD_MIN"], D("0.01"))
        self.assertEqual(m["SPREAD_MAX"], D("0.06"))
        self.assertTrue(m["SPREAD_IS_A_SAMPLED_STATE_DISTRIBUTION"])

    def test_one_tick_uptime_is_the_share_of_one_tick_books(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.55"), tick(2, ask="0.60")]
        self.assertEqual(BM.book_metrics(rows)["ONE_TICK_UPTIME_OBSERVED"],
                         D(2) / D(3))

    def test_spread_persistence_measures_a_sampled_run(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.55"), tick(2, ask="0.60")]
        m = BM.book_metrics(rows)
        self.assertGreaterEqual(m["SPREAD_PERSISTENCE_OBSERVED_S_N"], 1)


class TheMovementRates(unittest.TestCase):

    def test_book_moves_are_counted_over_transitions_not_rows(self):
        rows = [tick(0), tick(1, bid_changed=True), tick(2)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["OBSERVED_TRANSITIONS"], 2)
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], D(1) / D(2))

    def test_price_improvement_is_a_better_bid_or_a_better_offer(self):
        rows = [tick(0), tick(1, bid="0.55", bid_changed=True),
                tick(2, bid="0.55", ask="0.555", ask_changed=True)]
        self.assertEqual(
            BM.book_metrics(rows)["PRICE_IMPROVEMENT_FREQUENCY_OBSERVED"],
            D(2) / D(2))

    def test_a_widening_book_is_not_an_improvement(self):
        rows = [tick(0), tick(1, bid="0.50", bid_changed=True)]
        self.assertEqual(
            BM.book_metrics(rows)["PRICE_IMPROVEMENT_FREQUENCY_OBSERVED"],
            D(0) / D(1))

    def test_the_mid_can_hold_while_the_touch_moves(self):
        rows = [tick(0, bid="0.54", ask="0.56"),
                tick(1, bid="0.53", ask="0.57", bid_changed=True,
                     ask_changed=True)]
        m = BM.book_metrics(rows)
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], D(1))
        self.assertEqual(m["MID_MOVE_FREQUENCY_OBSERVED"], D(0))


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
        self.assertEqual(m["ONE_TICK_UPTIME_OBSERVED"], NI)
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], NI)
        self.assertEqual(m["REVISIT_CADENCE"]["MEDIAN_REVISIT_INTERVAL_S"], NI)

    def test_a_float_quantity_is_refused(self):
        bad = tick(0)
        bad["BID_QTY"] = 100.0
        with self.assertRaises(TypeError):
            BM.book_metrics([bad])


if __name__ == "__main__":
    unittest.main()
