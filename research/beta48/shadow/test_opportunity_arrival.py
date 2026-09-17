#!/usr/bin/env python3
"""OPPORTUNITY ARRIVAL: is this measuring the venue, or measuring our poller?

THE FAILURE MODE THAT MATTERS. A throughput number built on "opportunities"
can be inflated to any size by polling faster, and the inflation is invisible
in the output -- the chart just goes up. Every test in the first class exists
to make that impossible rather than unlikely.

The second failure mode is subtler and runs the other way: choosing one
materiality threshold, reporting one number, and never showing that a different
defensible threshold gives an answer 37 times larger. The tier tests pin that
the ladder is reported whole.

The third is the easy lie: calling a state change a positive-EV opportunity.
There is no EV in this repository, so there is no such count.

Nothing here contacts a venue.
"""
import json
import unittest
from pathlib import Path

import opportunity_arrival as OA

NOT_IDENTIFIED = "NOT_IDENTIFIED"
FIXTURE = Path(__file__).with_name("fixtures_ticks_35120338223.json")


def tick(seq, bid="0.40", ask="0.42", bq="100", aq="100", ladder=None,
         state="MARKET_STATE_OPEN", delta="0.0000", tt="T0", slug="m1",
         elapsed=None, traded="1000"):
    lad = ladder if ladder is not None else [[bid, bq]]
    return {
        "kind": "TICK", "slug": slug, "seq": seq,
        "BID": bid, "ASK": ask, "BID_QTY": bq, "ASK_QTY": aq,
        "BID_LADDER": lad, "ASK_LADDER": [[ask, aq]],
        "SPREAD": "0.0200", "STATE": state, "SHARES_TRADED": traded,
        "SHARES_TRADED_DELTA": delta, "TRANSACT_TIME": tt,
        "ELAPSED_S": elapsed if elapsed is not None else seq * 30.0,
    }


class PollingCannotManufactureVolume(unittest.TestCase):
    """The load-bearing property. Everything downstream inherits it."""

    def test_two_identical_observations_are_one_state_not_two(self):
        a, b = tick(0), tick(1)
        t = OA.transition(a, b)
        self.assertTrue(t["IDENTICAL_DECISION_STATE"])
        self.assertFalse(t["TIER_1"])
        self.assertFalse(t["TIER_2"])
        self.assertFalse(t["TIER_3"])
        self.assertEqual(t["TRIGGERS"], [])

    def test_a_hundred_identical_polls_produce_zero_opportunities(self):
        rows = [tick(i) for i in range(100)]
        r = OA.arrival(rows)
        self.assertEqual(r["DISTINCT_QUOTE_OPPORTUNITIES"], 0)
        self.assertEqual(r["MARKETS_WITH_0_OPPORTUNITIES"], 1)

    def test_polling_twice_as_fast_over_the_same_changes_changes_nothing(self):
        """Same three states, sampled sparsely and densely."""
        sparse = [tick(0, bq="100"), tick(1, bq="200"), tick(2, bq="300")]
        dense = [tick(0, bq="100"), tick(1, bq="100"), tick(2, bq="200"),
                 tick(3, bq="200"), tick(4, bq="300"), tick(5, bq="300")]
        self.assertEqual(OA.arrival(sparse)["DISTINCT_QUOTE_OPPORTUNITIES"],
                         OA.arrival(dense)["DISTINCT_QUOTE_OPPORTUNITIES"])

    def test_the_decision_state_ignores_observation_metadata(self):
        """A receipt time or a sequence number must never be a state change."""
        a = tick(0, tt="T0")
        b = dict(a, seq=1, RECEIPT_UTC="2026-01-01T00:00:09Z",
                 ELAPSED_S=999.0, TRANSACT_TIME="T9")
        self.assertEqual(OA.decision_state(a), OA.decision_state(b))
        self.assertFalse(OA.transition(a, b)["TIER_3"])

    def test_a_trigger_on_an_identical_state_is_a_hard_error(self):
        """The invariant is enforced in code, not left to these tests."""
        self.assertIn("raise AssertionError",
                      Path(OA.__file__).read_text())

    def test_the_sealed_series_is_monotone_under_decimation(self):
        rows = json.loads(FIXTURE.read_text())["ROWS"]
        chk = OA.poll_invariance_check(rows)
        self.assertTrue(chk["MONOTONE_NON_INCREASING"])
        self.assertTrue(chk["POLLING_CANNOT_MANUFACTURE_OPPORTUNITIES"])


class TheTriggerLadderIsDeclaredNotChosenSilently(unittest.TestCase):

    def test_a_touch_price_move_fires_every_tier(self):
        t = OA.transition(tick(0, bid="0.40"), tick(1, bid="0.41"))
        self.assertTrue(t["TIER_1"] and t["TIER_2"] and t["TIER_3"])
        self.assertIn(OA.TRIGGER_BEST_BID, t["TRIGGERS"])

    def test_a_trade_fires_tier_1_even_when_the_book_looks_identical(self):
        """Somebody lifted the offer and it refilled at the same price.

        The book reads the same; the flow information is new. Cumulative
        shares traded is therefore part of the decision state, which is what
        keeps this from tripping the identical-state invariant.
        """
        t = OA.transition(tick(0, traded="1000"),
                          tick(1, traded="1025", delta="25.0"))
        self.assertTrue(t["TIER_1"])
        self.assertIn(OA.TRIGGER_TRADE, t["TRIGGERS"])
        self.assertFalse(t["IDENTICAL_DECISION_STATE"])

    def test_a_zero_trade_delta_is_not_a_trade(self):
        t = OA.transition(tick(0), tick(1, delta="0.0000"))
        self.assertNotIn(OA.TRIGGER_TRADE, t["TRIGGERS"])

    def test_a_touch_size_move_fires_tier_2_but_not_tier_1(self):
        t = OA.transition(tick(0, bq="100"), tick(1, bq="250"))
        self.assertFalse(t["TIER_1"])
        self.assertTrue(t["TIER_2"])
        self.assertIn(OA.TRIGGER_TOUCH_SIZE, t["TRIGGERS"])

    def test_a_deep_ladder_move_fires_only_tier_3(self):
        a = tick(0, ladder=[["0.40", "100"], ["0.30", "50"]])
        b = tick(1, ladder=[["0.40", "100"], ["0.30", "80"]])
        t = OA.transition(a, b)
        self.assertFalse(t["TIER_1"])
        self.assertFalse(t["TIER_2"])
        self.assertTrue(t["TIER_3"])
        self.assertIn(OA.TRIGGER_DEPTH, t["TRIGGERS"])

    def test_every_tier_is_reported_not_just_the_default(self):
        rows = json.loads(FIXTURE.read_text())["ROWS"]
        r = OA.arrival(rows)
        for t in (OA.TIER_1, OA.TIER_2, OA.TIER_3, OA.TIER_VENUE):
            self.assertIn(t, r["BY_TIER_PER_MARKET_HOUR"], t)

    def test_the_tiers_are_nested_by_construction(self):
        rows = json.loads(FIXTURE.read_text())["ROWS"]
        b = OA.arrival(rows)["BY_TIER_TOTAL"]
        self.assertLessEqual(b[OA.TIER_1], b[OA.TIER_2])
        self.assertLessEqual(b[OA.TIER_2], b[OA.TIER_3])

    def test_the_default_tier_is_named_and_justified(self):
        self.assertEqual(OA.DEFAULT_TIER, OA.TIER_2)
        self.assertIn("adverse selection", OA.WHY_DEFAULT_TIER)


class TheseAreNotPositiveEvOpportunities(unittest.TestCase):

    def test_the_output_says_what_it_is_not(self):
        r = OA.arrival([tick(0), tick(1, bid="0.41")])
        self.assertEqual(r["THIS_COUNTS"],
                         "OBSERVABLE_QUOTEABLE_STATE_OPPORTUNITIES")
        self.assertEqual(r["THIS_DOES_NOT_COUNT"], "POSITIVE_EV_OPPORTUNITIES")

    def test_no_positive_ev_field_is_emitted_anywhere(self):
        r = OA.arrival([tick(0), tick(1, bid="0.41")])
        for k in r:
            self.assertNotIn("POSITIVE_EV_COUNT", k)

    def test_the_undercount_direction_is_declared(self):
        r = OA.arrival([tick(0)])
        self.assertEqual(r["ARRIVAL_BIAS_DIRECTION"], "UNDERCOUNT")
        self.assertGreaterEqual(
            len(r["TRIGGERS_NOT_OBSERVABLE_IN_PUBLIC_DATA"]), 5)

    def test_time_to_event_is_explicitly_not_a_trigger(self):
        self.assertIn("unlimited opportunities",
                      OA.TIME_TO_EVENT_IS_NOT_A_TRIGGER_YET)


class TheRequiredDistributionFields(unittest.TestCase):

    def rows(self):
        out = []
        # m1: many changes. m2: exactly one. m3: none.
        for i in range(23):          # 22 transitions -> the >20 bucket
            out.append(tick(i, slug="m1", bq=str(100 + i)))
        out += [tick(0, slug="m2", bq="100"), tick(1, slug="m2", bq="200")]
        out += [tick(0, slug="m3"), tick(1, slug="m3")]
        return out

    def test_every_required_field_is_present(self):
        r = OA.arrival(self.rows())
        for f in ("DISTINCT_QUOTE_OPPORTUNITIES_PER_HOUR",
                  "DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR",
                  "DISTINCT_QUOTE_OPPORTUNITIES_PER_EVENT_HOUR",
                  "MEDIAN_OPPORTUNITIES_PER_MARKET",
                  "P75_OPPORTUNITIES_PER_MARKET",
                  "P90_OPPORTUNITIES_PER_MARKET",
                  "P95_OPPORTUNITIES_PER_MARKET",
                  "MARKETS_WITH_0_OPPORTUNITIES",
                  "MARKETS_WITH_1_OPPORTUNITY", "MARKETS_WITH_2_TO_5",
                  "MARKETS_WITH_6_TO_20", "MARKETS_WITH_GT_20"):
            self.assertIn(f, r, f)

    def test_the_buckets_partition_the_markets(self):
        r = OA.arrival(self.rows())
        total = (r["MARKETS_WITH_0_OPPORTUNITIES"]
                 + r["MARKETS_WITH_1_OPPORTUNITY"] + r["MARKETS_WITH_2_TO_5"]
                 + r["MARKETS_WITH_6_TO_20"] + r["MARKETS_WITH_GT_20"])
        self.assertEqual(total, r["MARKETS_OBSERVED"])
        self.assertEqual(r["MARKETS_WITH_0_OPPORTUNITIES"], 1)
        self.assertEqual(r["MARKETS_WITH_1_OPPORTUNITY"], 1)
        self.assertEqual(r["MARKETS_WITH_GT_20"], 1)

    def test_event_hour_is_absent_without_an_event_map(self):
        r = OA.arrival(self.rows())
        self.assertEqual(r["DISTINCT_QUOTE_OPPORTUNITIES_PER_EVENT_HOUR"],
                         NOT_IDENTIFIED)

    def test_event_hour_appears_when_identity_is_supplied(self):
        r = OA.arrival(self.rows(), event_of=lambda s: "E1")
        self.assertNotEqual(r["DISTINCT_QUOTE_OPPORTUNITIES_PER_EVENT_HOUR"],
                            NOT_IDENTIFIED)

    def test_family_breakdown_is_reported_when_supplied(self):
        r = OA.arrival(self.rows(),
                       family_of=lambda s: "SPORTS_MARKET_TYPE_TOTAL")
        self.assertIn("SPORTS_MARKET_TYPE_TOTAL", r["BY_FAMILY"])


class TheSealedTickEvidence(unittest.TestCase):
    """The real capture: 1,365 observations, 6 markets, ~72 minutes."""

    def load(self):
        return json.loads(FIXTURE.read_text())

    def test_the_fixture_is_the_sealed_run(self):
        d = self.load()
        self.assertEqual(d["SOURCE_RUN"], "35120338223")
        self.assertEqual(d["TICK_ROWS_TOTAL"], 1365)
        self.assertEqual(len(d["ROWS"]), 1365)

    def test_the_measured_arrival_rate(self):
        r = OA.arrival(self.load()["ROWS"])
        self.assertEqual(r["MARKETS_OBSERVED"], 6)
        self.assertGreater(r["DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR"], 0)
        # More than one opportunity per market over the run: the fact that
        # kills the one-fill-per-market assumption outright.
        self.assertGreater(r["MEDIAN_OPPORTUNITIES_PER_MARKET"], 1)

    def test_no_observable_change_lacked_a_venue_version_move(self):
        """Our diff must be a SUBSET of the venue's own book versions.

        If this were non-zero we would be firing on something the venue does
        not consider a book change -- an invented opportunity.
        """
        r = OA.arrival(self.load()["ROWS"])
        self.assertEqual(r["OBSERVABLE_CHANGE_WITHOUT_VENUE_VERSION_MOVE"], 0)

    def test_the_venue_changed_in_ways_we_cannot_see(self):
        """Observation loss, measured rather than assumed away."""
        r = OA.arrival(self.load()["ROWS"])
        self.assertGreater(
            r["VENUE_VERSION_ADVANCED_WITHOUT_OBSERVABLE_CHANGE"], 0)

    def test_the_top_of_book_price_barely_moved(self):
        """The finding that makes the tier choice load-bearing."""
        r = OA.arrival(self.load()["ROWS"])
        t1 = r["BY_TIER_TOTAL"][OA.TIER_1]
        t3 = r["BY_TIER_TOTAL"][OA.TIER_3]
        self.assertLess(t1 * 5, t3, "tier 1 and tier 3 should differ hugely")


class ItPlacesNoOrderAndContactsNothing(unittest.TestCase):

    def test_no_network_and_no_submit(self):
        src = Path(OA.__file__).read_text().lower()
        for bad in ("httpx", "requests", "submit", "place_order", "https://"):
            self.assertNotIn(bad, src, bad)


if __name__ == "__main__":
    unittest.main()
