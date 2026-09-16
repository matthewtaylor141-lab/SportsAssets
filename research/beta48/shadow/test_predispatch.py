#!/usr/bin/env python3
"""The pre-dispatch gate. Every check has a way to say NO."""
import unittest

import predispatch as PD

NI = PD.NOT_IDENTIFIED

GOOD = {
    "SELECTED_REQUEST_RATE": "0.25",
    "RATE_SELECTION_EVIDENCE": "pilot run 35129103916, rungs 0.25-2.0",
    "RATE_CONFIDENCE": "SUSTAINED",
    "TARGET_MARKETS": 24,
    "TARGET_EVENTS": 8,
    "SPORT_STRATA": {"nfl": 6, "cfb": 6, "mlb": 6, "ufc": 6},
    "MAX_MARKETS_PER_EVENT": 3,
    "FUTURES_IN_PRIMARY_SAMPLE": 0,
    "POLL_ORDER_FAIRNESS_TEST": "PASS",
    "EVENT_IDENTITY_COVERAGE": "1.0",
}


class AGoodDesignIsAuthorised(unittest.TestCase):

    def test_it_passes(self):
        r = PD.check(GOOD)
        self.assertEqual(r["CAPTURE_DISPATCH_AUTHORIZED"], "YES")
        self.assertEqual(r["FAILED_CHECKS"], [])

    def test_the_block_ends_on_the_authorisation_line(self):
        text = PD.render(PD.check(GOOD))
        self.assertTrue(text.strip().endswith("CAPTURE_DISPATCH_AUTHORIZED  = YES"))
        self.assertIn("SELECTED_REQUEST_RATE", text)

    def test_it_contacts_nothing(self):
        self.assertTrue(PD.check(GOOD)["THIS_MODULE_CONTACTS_NOTHING"])


class EveryCheckCanFail(unittest.TestCase):

    def _no(self, **over):
        d = dict(GOOD, **over)
        r = PD.check(d)
        self.assertEqual(r["CAPTURE_DISPATCH_AUTHORIZED"], "NO")
        return r

    def test_a_candidate_rate_is_not_enough(self):
        r = self._no(RATE_CONFIDENCE="LIMITED")
        self.assertIn("RATE_IS_CONFIRMED_NOT_A_CANDIDATE", r["FAILED_CHECKS"])

    def test_a_missing_rate_blocks_it(self):
        r = self._no(SELECTED_REQUEST_RATE=NI)
        self.assertIn("SELECTED_REQUEST_RATE", r["MISSING_FIELDS"])

    def test_unnamed_rate_evidence_blocks_it(self):
        r = self._no(RATE_SELECTION_EVIDENCE="")
        self.assertIn("RATE_SELECTION_EVIDENCE_NAMED", r["FAILED_CHECKS"])

    def test_one_future_in_the_primary_sample_blocks_it(self):
        r = self._no(FUTURES_IN_PRIMARY_SAMPLE=1)
        self.assertIn("NO_FUTURES_IN_PRIMARY_SAMPLE", r["FAILED_CHECKS"])

    def test_a_failing_fairness_test_blocks_it(self):
        r = self._no(POLL_ORDER_FAIRNESS_TEST="FAIL")
        self.assertIn("POLL_ORDER_FAIRNESS_PASSES", r["FAILED_CHECKS"])

    def test_partial_event_identity_blocks_it(self):
        """'we will map it afterwards' is what lost the last harvest."""
        r = self._no(EVENT_IDENTITY_COVERAGE="0.75")
        self.assertIn("EVENT_IDENTITY_COMPLETE", r["FAILED_CHECKS"])

    def test_a_single_event_sample_blocks_it(self):
        r = self._no(TARGET_EVENTS=1)
        self.assertIn("TARGET_EVENTS_IS_MORE_THAN_ONE", r["FAILED_CHECKS"])

    def test_an_unbounded_markets_per_event_blocks_it(self):
        r = self._no(MAX_MARKETS_PER_EVENT=NI)
        self.assertIn("MAX_MARKETS_PER_EVENT_IS_BOUNDED", r["FAILED_CHECKS"])

    def test_an_empty_design_fails_everything_it_can(self):
        r = PD.check({})
        self.assertEqual(r["CAPTURE_DISPATCH_AUTHORIZED"], "NO")
        self.assertEqual(len(r["MISSING_FIELDS"]), len(PD.REQUIRED_FIELDS))

    def test_the_failed_checks_are_printed_in_the_block(self):
        text = PD.render(self._no(RATE_CONFIDENCE="LIMITED"))
        self.assertIn("FAILED_CHECK", text)
        self.assertTrue(text.strip().endswith("= NO"))


class TheGateMatchesTheRunThatFailed(unittest.TestCase):
    """Replay the 35120338223 design: the gate should have stopped it."""

    def test_the_previous_capture_would_have_been_refused(self):
        r = PD.check({
            "SELECTED_REQUEST_RATE": "2.0",
            "RATE_SELECTION_EVIDENCE": NI,        # never measured
            "RATE_CONFIDENCE": NI,
            "TARGET_MARKETS": 24,
            "TARGET_EVENTS": NI,                  # no event map
            "SPORT_STRATA": {"nfl": 6, "cfb": 6, "mlb": 6, "ufc": 6},
            "MAX_MARKETS_PER_EVENT": NI,
            "FUTURES_IN_PRIMARY_SAMPLE": 3,       # MVP, DPOY, OPOY
            "POLL_ORDER_FAIRNESS_TEST": NI,       # fixed order, never tested
            "EVENT_IDENTITY_COVERAGE": NI,
        })
        self.assertEqual(r["CAPTURE_DISPATCH_AUTHORIZED"], "NO")
        for c in ("RATE_IS_CONFIRMED_NOT_A_CANDIDATE",
                  "RATE_SELECTION_EVIDENCE_NAMED",
                  "NO_FUTURES_IN_PRIMARY_SAMPLE",
                  "POLL_ORDER_FAIRNESS_PASSES",
                  "EVENT_IDENTITY_COMPLETE"):
            self.assertIn(c, r["FAILED_CHECKS"], c)


if __name__ == "__main__":
    unittest.main()
