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
        for k in ("BID_OBSERVED_RUN_SPAN_S_P50",
                  "TIME_AT_PRICE_OBSERVED_S_P50",
                  "BOOK_UPDATE_RATE_OBSERVED",
                  "MARKET_MOVED_THROUGH_QUOTE_OBSERVED",
                  "MID_MOVE_FREQUENCY_OBSERVED",
                  "ONE_TICK_SNAPSHOT_SHARE",
                  "SPREAD_PERSISTENCE_OBSERVED_S_P50"):
            self.assertIn(k, m, k)

    def test_the_unsuffixed_names_are_gone(self):
        m = BM.book_metrics([tick(i) for i in range(5)])
        for gone in ("BOOK_MOVE_FREQUENCY", "MID_MOVE_FREQUENCY",
                     "ONE_TICK_UPTIME", "ONE_TICK_UPTIME_OBSERVED",
                     "DEPTH_CHANGE_RATE", "QUOTE_LIFETIME",
                     "QUOTE_LIFETIME_OBSERVED_BID_S_P50",
                     "MARKET_MOVED_THROUGH_QUOTE_FREQUENCY"):
            self.assertNotIn(gone, m, gone)

    def test_what_a_poll_cannot_see_is_listed(self):
        c = BM.revisit_cadence([tick(i) for i in range(3)])
        for miss in ("QUOTE_MOVED_AWAY_AND_RETURNED", "TRADED",
                     "DEPTH_CHANGED_AND_CHANGED_BACK",
                     "DISAPPEARED_AND_REAPPEARED"):
            self.assertIn(miss, c["UNOBSERVED_BETWEEN_POLLS"])

    def test_the_over_broad_lower_bound_claim_is_withdrawn(self):
        m = BM.book_metrics([tick(i) for i in range(3)])
        self.assertNotIn("OBSERVED_RATES_ARE_A_LOWER_BOUND_ON_ACTIVITY", m)
        self.assertEqual(m["WITHDRAWN_TOO_BROAD"],
                         "EVERY_OBSERVED_RATE_IS_A_LOWER_BOUND_ON_ACTIVITY")


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
        self.assertEqual(m["WEIGHTING"], "MARKET_WEIGHTED_ALL_MARKETS")
        self.assertEqual(m["EVENTS_IN_WEIGHTING"], 2)
        self.assertTrue(m["ONE_EVENT_ONE_VOTE"])

    def test_one_event_family_cannot_dominate_the_event_weighted_figure(self):
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2"})
        # Market-weighted: 4 of 6 transitions moved. Event-weighted: E1 is 1.0
        # and E2 is 0.0, so the average is 0.5 -- the two-market event counts
        # once, not twice.
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], D(4) / D(6))
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED_RESOLVED_ONLY"],
                         D("0.5"))

    def test_without_an_event_map_the_event_weighting_is_not_identified(self):
        m = BM.book_metrics(self._rows())
        for k in BM.WEIGHTED_RATES:
            self.assertEqual(m[k + "_EVENT_WEIGHTED_RESOLVED_ONLY"], NI, k)


class TheSpread(unittest.TestCase):

    def test_percentiles_come_from_observed_books_only(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.56"), tick(2, ask="0.60")]
        m = BM.book_metrics(rows)
        self.assertEqual(m["SPREAD_MIN"], D("0.01"))
        self.assertEqual(m["SPREAD_MAX"], D("0.06"))
        self.assertTrue(m["SPREAD_IS_A_SAMPLED_STATE_DISTRIBUTION"])

    def test_one_tick_uptime_is_the_share_of_one_tick_books(self):
        rows = [tick(0, ask="0.55"), tick(1, ask="0.55"), tick(2, ask="0.60")]
        self.assertEqual(BM.book_metrics(rows)["ONE_TICK_SNAPSHOT_SHARE"],
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
        self.assertEqual(m["ONE_TICK_SNAPSHOT_SHARE"], NI)
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], NI)
        self.assertEqual(m["REVISIT_CADENCE"]["MEDIAN_REVISIT_INTERVAL_S"], NI)

    def test_a_float_quantity_is_refused(self):
        bad = tick(0)
        bad["BID_QTY"] = 100.0
        with self.assertRaises(TypeError):
            BM.book_metrics([bad])


if __name__ == "__main__":
    unittest.main()


class TheBiasStatementIsPerClassNotGlobal(unittest.TestCase):

    def setUp(self):
        self.m = BM.book_metrics([tick(i) for i in range(6)])

    def test_class_a_counts_are_an_undercount_with_a_stated_bound(self):
        self.assertEqual(self.m["CLASS_A_BOUND"],
                         "OBSERVED_TRANSITION_COUNT_LE_TRUE_TRANSITION_COUNT")
        self.assertEqual(self.m["CLASS_A_SAMPLING_BIAS_DIRECTION"],
                         "UNDERCOUNT")
        self.assertEqual(self.m["CLASS_A_BOUND_SUBJECT_TO"],
                         "THE_SNAPSHOTS_THEMSELVES_BEING_VALID")

    def test_every_class_a_count_is_actually_emitted(self):
        for k in BM.CLASS_A_TRANSITION_COUNTS:
            self.assertIn(k, self.m, k)
            self.assertIsInstance(self.m[k], int)

    def test_class_b_bias_direction_is_not_identified(self):
        self.assertEqual(self.m["CLASS_B_SAMPLING_BIAS_DIRECTION"], NI)
        self.assertIn("interval-censored", self.m["CLASS_B_WHY"])

    def test_the_two_classes_do_not_overlap(self):
        self.assertFalse(set(BM.CLASS_A_TRANSITION_COUNTS)
                         & set(BM.CLASS_B_STATE_OCCUPANCY))

    def test_a_frequency_is_a_share_not_a_continuous_time_rate(self):
        self.assertTrue(self.m["FREQUENCIES_ARE_PER_OBSERVED_TRANSITION"])
        self.assertEqual(self.m["FREQUENCIES_AS_CONTINUOUS_TIME_RATES"], NI)

    def test_one_tick_uptime_is_a_snapshot_share_not_time_weighted(self):
        self.assertIn("ONE_TICK_SNAPSHOT_SHARE", self.m)
        self.assertEqual(self.m["TRUE_TIME_WEIGHTED_ONE_TICK_UPTIME"], NI)


class QuoteDurationIsIntervalCensored(unittest.TestCase):

    def test_a_single_unbroken_run_is_censored_at_both_ends(self):
        r = BM.persistence_runs([tick(i) for i in range(5)], key="BID")
        self.assertEqual(r["PERSISTENCE_RUNS"], 1)
        self.assertEqual(r["LEFT_CENSORED_RUNS"], 1)
        self.assertEqual(r["RIGHT_CENSORED_RUNS"], 1)
        self.assertEqual(r["UNCENSORED_RUNS"], 0)

    def test_a_middle_run_is_uncensored(self):
        rows = [tick(0, bid="0.54"), tick(1, bid="0.55"), tick(2, bid="0.55"),
                tick(3, bid="0.56")]
        r = BM.persistence_runs(rows, key="BID")
        self.assertEqual(r["PERSISTENCE_RUNS"], 3)
        self.assertEqual(r["UNCENSORED_RUNS"], 1)
        self.assertEqual(r["LEFT_CENSORED_RUNS"], 1)
        self.assertEqual(r["RIGHT_CENSORED_RUNS"], 1)

    def test_the_span_is_a_minimum_not_a_lifetime(self):
        rows = [tick(0, bid="0.54"), tick(1, bid="0.54"), tick(2, bid="0.55")]
        r = BM.persistence_runs(rows, key="BID")
        self.assertEqual(r["OBSERVED_RUN_SPAN_S_MAX"], D("3"))
        self.assertEqual(r["PROVEN_CONTINUOUS_PERSISTENCE_S"], NI)
        self.assertEqual(r["TRUE_CONTINUOUS_QUOTE_LIFETIME"], NI)
        self.assertTrue(
            r["OBSERVED_RUN_SPAN_IS_NOT_A_MINIMUM_CONTINUOUS_LIFETIME"])
        self.assertEqual(r["INTRAINTERVAL_STATE_CHANGES"], "NOT_OBSERVED")

    def test_it_is_class_b_and_its_bias_direction_is_unknown(self):
        r = BM.persistence_runs([tick(i) for i in range(3)])
        self.assertEqual(r["METRIC_CLASS"], "B_STATE_OCCUPANCY")
        self.assertEqual(r["SAMPLING_BIAS_DIRECTION"], NI)

    def test_both_sides_are_reported_in_the_metrics(self):
        m = BM.book_metrics([tick(i) for i in range(4)])
        for side in ("BID", "ASK"):
            self.assertIn("%s_OBSERVED_RUN_SPAN_S_P50" % side, m)
            self.assertIn("%s_LEFT_CENSORED_RUNS" % side, m)
            self.assertEqual(m["%s_PROVEN_CONTINUOUS_PERSISTENCE_S" % side], NI)

    def test_runs_are_per_market_not_across_markets(self):
        rows = [tick(0, slug="a", bid="0.54"), tick(0, slug="b", bid="0.20"),
                tick(1, slug="a", bid="0.54"), tick(1, slug="b", bid="0.20")]
        r = BM.persistence_runs(rows, key="BID")
        self.assertEqual(r["PERSISTENCE_RUNS"], 2)


class EventWeightingAggregatesInternallyFirst(unittest.TestCase):

    def _rows(self):
        """E1 holds two markets of very different sizes; E2 holds one."""
        rows = []
        # E1 market 'a': 20 observations, every transition a book change.
        for i in range(21):
            rows.append(tick(i, slug="a", bid_changed=(i > 0)))
        # E1 market 'b': 3 observations, no changes at all.
        for i in range(3):
            rows.append(tick(i, slug="b"))
        # E2 market 'c': 3 observations, no changes.
        for i in range(3):
            rows.append(tick(i, slug="c"))
        return rows

    def test_the_event_pools_its_own_counts_before_it_votes(self):
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2"})
        # E1 pooled: 20 changes over 22 transitions. E2: 0 over 2.
        # One vote each -> (20/22 + 0) / 2.
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED_RESOLVED_ONLY"],
                         (D(20) / D(22)) / D(2))
        self.assertTrue(m["EVENT_AGGREGATED_INTERNALLY_FIRST"])
        self.assertFalse(m["MARKET_ROWS_REWEIGHTED_AFTER_POOLING"])

    def test_that_is_not_the_mean_of_per_market_rates(self):
        """The rejected alternative: average a's 1.0 with b's 0.0 inside E1,
        which would let a 3-observation market cancel a 21-observation one."""
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2"})
        mean_of_market_rates = (D(1) + D(0)) / D(2)      # E1 the wrong way
        wrong = (mean_of_market_rates + D(0)) / D(2)
        self.assertNotEqual(m["BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED_RESOLVED_ONLY"],
                            wrong)

    def test_a_market_with_no_event_gets_no_vote(self):
        m = BM.book_metrics(self._rows(), event_of={"a": "E1", "b": "E1"})
        self.assertEqual(m["EVENTS_IN_WEIGHTING"], 1)
        self.assertTrue(m["MARKETS_WITHOUT_AN_EVENT_GET_NO_VOTE"])

    def test_market_weighting_is_unchanged_and_named(self):
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2"})
        self.assertEqual(m["WEIGHTING"], "MARKET_WEIGHTED_ALL_MARKETS")
        self.assertEqual(m["BOOK_UPDATE_RATE_OBSERVED"], D(20) / D(24))


class TheRunSpanClaimsNoContinuity(unittest.TestCase):

    def test_it_is_named_for_what_it_measures(self):
        r = BM.persistence_runs([tick(i) for i in range(4)])
        self.assertEqual(r["OBSERVED_RUN_SPAN_MEANS"],
                         "ELAPSED_TIME_SPANNING_CONSECUTIVE_SAMPLED_"
                         "OBSERVATIONS_THAT_MATCH")
        self.assertTrue(
            r["OBSERVED_RUN_SPAN_IS_NOT_A_MINIMUM_CONTINUOUS_LIFETIME"])

    def test_a_proven_floor_would_be_a_separate_quantity_and_we_have_none(self):
        r = BM.persistence_runs([tick(i) for i in range(4)])
        self.assertEqual(r["PROVEN_CONTINUOUS_PERSISTENCE_S"], NI)

    def test_two_matching_endpoints_do_not_prove_continuity(self):
        """X at t1 and X at t2 is consistent with X leaving and returning."""
        r = BM.persistence_runs([tick(0, bid="0.54"), tick(1, bid="0.54")])
        self.assertEqual(r["OBSERVED_RUN_SPAN_S_MAX"], D("3"))
        self.assertEqual(r["INTRAINTERVAL_STATE_CHANGES"], "NOT_OBSERVED")
        self.assertEqual(r["PROVEN_CONTINUOUS_PERSISTENCE_S"], NI)

    def test_the_minimum_wording_is_gone_from_every_key(self):
        m = BM.book_metrics([tick(i) for i in range(4)])
        for k in m:
            self.assertNotIn("MIN_OBSERVED_PERSISTENCE", k)


class CensoringIsScopedToTheSampledRun(unittest.TestCase):

    def test_it_applies_to_the_run_and_not_to_the_lifetime(self):
        r = BM.persistence_runs([tick(i) for i in range(4)])
        self.assertEqual(r["CENSORING_APPLIES_TO_SAMPLED_RUN"], "YES")
        self.assertEqual(
            r["CENSORING_APPLIES_TO_TRUE_CONTINUOUS_QUOTE_LIFETIME"], NI)

    def test_the_reason_names_the_disappear_and_return_case(self):
        r = BM.persistence_runs([tick(i) for i in range(4)])
        self.assertIn("disappear-and-return", r["WHY"])

    def test_the_flags_themselves_still_work(self):
        rows = [tick(0, bid="0.54"), tick(1, bid="0.55"), tick(2, bid="0.55"),
                tick(3, bid="0.56")]
        r = BM.persistence_runs(rows, key="BID")
        self.assertEqual(r["LEFT_CENSORED_RUNS"], 1)
        self.assertEqual(r["RIGHT_CENSORED_RUNS"], 1)
        self.assertEqual(r["UNCENSORED_RUNS"], 1)

    def test_both_sides_carry_the_scoping(self):
        m = BM.book_metrics([tick(i) for i in range(4)])
        for side in ("BID", "ASK"):
            self.assertEqual(m["%s_CENSORING_APPLIES_TO_SAMPLED_RUN" % side],
                             "YES")
            self.assertEqual(
                m["%s_CENSORING_APPLIES_TO_TRUE_CONTINUOUS_QUOTE_LIFETIME"
                  % side], NI)


class EventWeightedCoverageTravelsWithTheMetric(unittest.TestCase):

    def _rows(self):
        rows = []
        for slug in ("a", "b", "c", "d"):
            for i in range(4):
                rows.append(tick(i, slug=slug, bid_changed=(slug == "a"
                                                            and i > 0)))
        return rows

    def test_the_coverage_is_reported_beside_the_figure(self):
        m = BM.book_metrics(self._rows(), event_of={"a": "E1", "b": "E1"})
        self.assertEqual(m["CAPTURE_MARKETS_TOTAL"], 4)
        self.assertEqual(m["CAPTURE_MARKETS_EVENT_RESOLVED"], 2)
        self.assertEqual(m["CAPTURE_MARKETS_EVENT_UNRESOLVED"], 2)
        self.assertEqual(m["EVENT_WEIGHTING_MARKET_COVERAGE_PCT"], D(50))
        self.assertEqual(m["EVENT_WEIGHTED_POPULATION"],
                         "EVENT_IDENTITY_RESOLVED_SUBSET")
        self.assertFalse(m["EVENT_WEIGHTED_COVERS_ALL_CAPTURED_MARKETS"])

    def test_full_coverage_is_stated_as_such(self):
        m = BM.book_metrics(self._rows(),
                            event_of={"a": "E1", "b": "E1", "c": "E2",
                                      "d": "E3"})
        self.assertEqual(m["EVENT_WEIGHTING_MARKET_COVERAGE_PCT"], D(100))
        self.assertTrue(m["EVENT_WEIGHTED_COVERS_ALL_CAPTURED_MARKETS"])

    def test_with_no_map_the_coverage_is_zero_and_says_so(self):
        m = BM.book_metrics(self._rows())
        self.assertEqual(m["CAPTURE_MARKETS_EVENT_RESOLVED"], 0)
        self.assertEqual(m["CAPTURE_MARKETS_EVENT_UNRESOLVED"], 4)
        self.assertEqual(m["EVENT_WEIGHTED_POPULATION"], NI)


class TheThreeWayWeightingComparison(unittest.TestCase):

    def _rows(self):
        """'a' moves every tick; 'b', 'c', 'd' never move. Only a and b are
        event-resolved, so dropping the unresolved markets changes the number
        by itself -- which is exactly what the third figure isolates."""
        rows = []
        for slug in ("a", "b", "c", "d"):
            for i in range(4):
                rows.append(tick(i, slug=slug,
                                 bid_changed=(slug == "a" and i > 0)))
        return rows

    def setUp(self):
        self.m = BM.book_metrics(self._rows(),
                                 event_of={"a": "E1", "b": "E2"})

    def test_all_three_figures_are_present(self):
        self.assertIn("BOOK_UPDATE_RATE_OBSERVED", self.m)
        self.assertIn("BOOK_UPDATE_RATE_OBSERVED_MARKET_WEIGHTED_RESOLVED_ONLY",
                      self.m)
        self.assertIn("BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED_RESOLVED_ONLY",
                      self.m)

    def test_the_population_drop_is_visible_on_its_own(self):
        # All markets: 3 of 12 transitions moved. Resolved only (a, b): 3 of 6.
        self.assertEqual(self.m["BOOK_UPDATE_RATE_OBSERVED"], D(3) / D(12))
        self.assertEqual(
            self.m["BOOK_UPDATE_RATE_OBSERVED_MARKET_WEIGHTED_RESOLVED_ONLY"],
            D(3) / D(6))

    def test_only_the_last_two_isolate_the_weighting(self):
        # E1 = 1.0, E2 = 0.0, one vote each -> 0.5, against 0.5 market-weighted
        # on the same subset. Same population, so the difference here is the
        # weighting alone -- and on this fixture it happens to be zero.
        self.assertEqual(
            self.m["BOOK_UPDATE_RATE_OBSERVED_EVENT_WEIGHTED_RESOLVED_ONLY"],
            D("0.5"))
        self.assertEqual(
            self.m["BOOK_UPDATE_RATE_OBSERVED_MARKET_WEIGHTED_RESOLVED_ONLY"],
            D("0.5"))

    def test_the_comparison_is_explained_on_the_row(self):
        self.assertIn("isolates the population drop", self.m["THREE_WAY_COMPARISON"])
        self.assertEqual(self.m["MARKET_WEIGHTED_POPULATION"],
                         "ALL_CAPTURED_MARKETS")
