#!/usr/bin/env python3
"""SHARES_TRADED: runtime behaviour and venue meaning, and never one for the other."""
import unittest
from decimal import Decimal as D

import tick_semantics as TS

NI = TS.NOT_IDENTIFIED

SEM_OK = {"CUMULATIVE_OR_INTERVAL": "CUMULATIVE",
          "MARKET_WIDE_OR_SIDE_SPECIFIC": "MARKET_WIDE",
          "RESET_BOUNDARY": "KNOWN_AND_AVOIDABLE_IN_WINDOW"}
RUN_OK = {"MONOTONIC_WITHIN_MARKET": True, "RESET_EVENTS_OBSERVED": 0,
          "NEGATIVE_DELTAS_OBSERVED": 0}


def row(seq, traded, slug="m", bid_changed=False, depth_changed=False,
        kind="TICK"):
    return {"kind": kind, "slug": slug, "seq": seq, "ELAPSED_S": float(seq * 3),
            "SHARES_TRADED": traded, "BID_CHANGED": bid_changed,
            "ASK_CHANGED": False, "DEPTH_CHANGED": depth_changed}


class TheVenueMeaningIsNotKnown(unittest.TestCase):

    def test_every_semantic_field_is_not_identified_today(self):
        for k in TS.VENUE_SEMANTICS_FIELDS:
            self.assertEqual(TS.VENUE_SEMANTICS[k], NI, k)

    def test_the_field_status_is_diagnostic_only(self):
        self.assertEqual(TS.SHARES_TRADED_DELTA_STATUS,
                         TS.CONSERVATIVE_DIAGNOSTIC_ONLY)

    def test_the_gate_is_shut(self):
        self.assertFalse(TS.semantics_established())

    def test_proven_not_filled_is_refused_by_name(self):
        self.assertEqual(TS.PROVEN_NOT_FILLED,
                         "REFUSED_NOT_AN_OUTPUT_OF_THIS_PROGRAMME")


class RuntimeBehaviourIsNotVenueSemantics(unittest.TestCase):

    def test_a_perfectly_monotone_capture_does_not_open_the_gate(self):
        d = TS.diagnose([row(i, str(1000 + i * 5)) for i in range(20)])
        self.assertIs(d["MONOTONIC_WITHIN_MARKET"], True)
        self.assertEqual(d["NEGATIVE_DELTAS_OBSERVED"], 0)
        self.assertFalse(d["VENUE_SEMANTICS_ESTABLISHED"])
        self.assertFalse(d["HARD_REFUTATION_UNLOCKED"])
        self.assertFalse(TS.semantics_established(runtime=d))

    def test_the_two_facts_are_recorded_separately(self):
        d = TS.diagnose([row(i, str(1000 + i)) for i in range(5)])
        self.assertIn("MONOTONIC_WITHIN_MARKET", d)
        self.assertIn("VENUE_SEMANTICS", d)
        self.assertTrue(d["RUNTIME_BEHAVIOUR_IS_NOT_VENUE_SEMANTICS"])

    def test_both_halves_together_do_open_it(self):
        d = TS.diagnose([row(i, str(1000 + i * 5)) for i in range(20)])
        self.assertTrue(TS.semantics_established(SEM_OK, d))

    def test_an_interval_field_never_opens_it(self):
        d = TS.diagnose([row(i, str(1000 + i * 5)) for i in range(20)])
        bad = dict(SEM_OK, CUMULATIVE_OR_INTERVAL="INTERVAL")
        self.assertFalse(TS.semantics_established(bad, d))

    def test_a_side_specific_field_never_opens_it(self):
        d = TS.diagnose([row(i, str(1000 + i * 5)) for i in range(20)])
        bad = dict(SEM_OK, MARKET_WIDE_OR_SIDE_SPECIFIC="SIDE_SPECIFIC")
        self.assertFalse(TS.semantics_established(bad, d))


class TheDiagnosticsNameWhatWentWrong(unittest.TestCase):

    def test_a_counter_going_backwards_is_a_negative_delta(self):
        d = TS.diagnose([row(0, "1000"), row(1, "900")])
        self.assertIs(d["MONOTONIC_WITHIN_MARKET"], False)
        self.assertEqual(d["NEGATIVE_DELTAS_OBSERVED"], 1)

    def test_a_collapse_to_near_zero_is_counted_as_a_reset(self):
        d = TS.diagnose([row(0, "5000"), row(1, "12")])
        self.assertEqual(d["RESET_EVENTS_OBSERVED"], 1)

    def test_a_still_field_while_the_book_moves_is_counted(self):
        rows = [row(0, "1000"), row(1, "1000", bid_changed=True),
                row(2, "1000", depth_changed=True)]
        d = TS.diagnose(rows)
        self.assertEqual(d["SAME_VALUE_DESPITE_BOOK_CHANGES"], 2)

    def test_a_read_error_breaks_the_chain_rather_than_spanning_it(self):
        rows = [row(0, "1000"), row(1, None, kind="TICK_ERROR"),
                row(2, "9000")]
        d = TS.diagnose(rows)
        self.assertEqual(d["MISSING_TRANSITIONS"], 1)
        # The 8,000 jump across the gap is NOT counted as an observed
        # transition, because we did not observe the interval.
        self.assertEqual(d["OBSERVED_TRANSITIONS"], 0)

    def test_a_missing_value_also_breaks_the_chain(self):
        d = TS.diagnose([row(0, "1000"), row(1, NI), row(2, "2000")])
        self.assertEqual(d["MISSING_TRANSITIONS"], 1)
        self.assertEqual(d["OBSERVED_TRANSITIONS"], 0)

    def test_a_jump_far_above_this_market_s_own_step_is_flagged(self):
        rows = [row(0, "1000"), row(1, "1010"), row(2, "1020"),
                row(3, "1030"), row(4, "1040"), row(5, "9000")]
        d = TS.diagnose(rows)
        self.assertEqual(d["LARGE_DISCONTINUITIES"], 1)

    def test_update_frequency_is_a_share_of_observed_transitions(self):
        rows = [row(0, "1000"), row(1, "1000"), row(2, "1010"),
                row(3, "1010")]
        d = TS.diagnose(rows)
        self.assertEqual(d["FIELD_UPDATE_FREQUENCY"], D(1) / D(3))

    def test_markets_are_walked_separately(self):
        rows = [row(0, "1000", slug="a"), row(0, "50", slug="b"),
                row(1, "1010", slug="a"), row(1, "60", slug="b")]
        d = TS.diagnose(rows)
        self.assertEqual(d["MARKETS"], 2)
        self.assertIs(d["MONOTONIC_WITHIN_MARKET"], True)
        self.assertEqual(d["PER_SLUG"]["a"]["OBSERVED_TRANSITIONS"], 1)

    def test_an_empty_capture_is_not_identified_not_monotone(self):
        d = TS.diagnose([])
        self.assertEqual(d["MONOTONIC_WITHIN_MARKET"], NI)


class TheRefutationStatus(unittest.TestCase):

    def test_a_failing_bound_is_not_identified_while_the_gate_is_shut(self):
        r = TS.refutation_status(True)
        self.assertEqual(r["FILL_REFUTATION_STATUS"], NI)
        self.assertEqual(r["WHY"],
                         TS.AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT)
        self.assertTrue(r["SEMANTICS_MISSING"])

    def test_it_is_never_equivalent_to_execution_evidence(self):
        d = TS.diagnose([row(i, str(1000 + i * 5)) for i in range(20)])
        for r in (TS.refutation_status(True),
                  TS.refutation_status(True, SEM_OK, d)):
            self.assertFalse(r["EQUIVALENT_TO_EXECUTION_EVIDENCE"])

    def test_with_both_halves_it_becomes_a_verdict(self):
        d = TS.diagnose([row(i, str(1000 + i * 5)) for i in range(20)])
        self.assertEqual(
            TS.refutation_status(True, SEM_OK, d)["FILL_REFUTATION_STATUS"],
            "REFUTED")
        self.assertEqual(
            TS.refutation_status(False, SEM_OK, d)["FILL_REFUTATION_STATUS"],
            "NOT_REFUTED")

    def test_the_arithmetic_is_always_carried_either_way(self):
        self.assertTrue(
            TS.refutation_status(True)["AGGREGATE_VOLUME_BOUND_ARITHMETIC"])


if __name__ == "__main__":
    unittest.main()
