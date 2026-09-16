#!/usr/bin/env python3
"""The exit priors are a derivation, not an assertion. These pin it."""
import json
import math
import unittest
from pathlib import Path

import build_exit_priors as B

P = json.loads((Path(__file__).parent / "evidence" / "whale_audit" /
                "whale_exit_priors_v1.json").read_text())
NI = "NOT_IDENTIFIED"


class TheThreeTimeMetricsAreKeptApart(unittest.TestCase):

    def _rows(self, acct="ferrarichampions2026", key="any_basis"):
        return P["ACCOUNT_PRIORS"][acct]["HAZARD_BY_BASIS_CEILING"][key]["ROWS"]

    def test_lambda_is_the_continuous_hazard_not_H_over_minutes(self):
        for r in self._rows():
            if r["CONTINUOUS_HAZARD_LAMBDA"] == NI:
                continue
            # they are DIFFERENT numbers, and lambda is the larger for H>0
            self.assertNotAlmostEqual(
                r["CONTINUOUS_HAZARD_LAMBDA"],
                r["AVERAGE_COMPLETION_PROBABILITY_PER_MINUTE"], places=6)

    def test_lambda_reproduces_its_definition(self):
        rows = self._rows()
        prev_s = 1.0
        for r in rows:
            if r["CONTINUOUS_HAZARD_LAMBDA"] == NI:
                continue
            s2 = r["SURVIVAL_S"]
            want = -math.log(s2 / prev_s) / r["INTERVAL_MINUTES"]
            self.assertAlmostEqual(r["CONTINUOUS_HAZARD_LAMBDA"], want, places=9)
            prev_s = s2

    def test_the_per_minute_figure_is_flagged_descriptive_only(self):
        for r in self._rows():
            self.assertTrue(r["PER_MINUTE_IS_DESCRIPTIVE_ONLY"])
            self.assertTrue(r["LAMBDA_IS_PREFERRED_COMPARISON"])

    def test_the_per_minute_figure_can_exceed_one_and_so_is_not_a_probability(self):
        # 0-5s: H=2.89% over 1/12 minute -> 34.7%/min. Over a 2-second
        # interval the same arithmetic would exceed 100%/min. It is a rate of
        # a probability, not a probability, and must never be read as one.
        first = self._rows()[0]
        self.assertGreater(first["AVERAGE_COMPLETION_PROBABILITY_PER_MINUTE"],
                           first["INTERVAL_COMPLETION_H"])

    def test_the_settlement_tail_has_no_lambda(self):
        last = self._rows()[-1]
        self.assertEqual(last["INTERVAL"], "3600s-settlement")
        self.assertEqual(last["CONTINUOUS_HAZARD_LAMBDA"], NI)
        self.assertEqual(last["INTERVAL_MINUTES"], NI)
        self.assertEqual(last["AVERAGE_COMPLETION_PROBABILITY_PER_MINUTE"], NI)

    def test_the_settlement_tail_is_excluded_from_monotonicity(self):
        for a in P["ACCOUNT_PRIORS"].values():
            h = a["HAZARD_BY_BASIS_CEILING"]["any_basis"]
            self.assertTrue(h["SETTLEMENT_TAIL_EXCLUDED_FROM_MONOTONICITY"])


class PairingIntensityDecays(unittest.TestCase):

    def test_lambda_is_non_increasing_in_every_account(self):
        for name, a in P["ACCOUNT_PRIORS"].items():
            h = a["HAZARD_BY_BASIS_CEILING"]["any_basis"]
            self.assertTrue(h["LAMBDA_MONOTONE_NON_INCREASING"],
                            "%s lambda not monotone" % name)

    def test_the_decay_is_large_not_marginal(self):
        for a in P["ACCOUNT_PRIORS"].values():
            lams = [r["CONTINUOUS_HAZARD_LAMBDA"]
                    for r in a["HAZARD_BY_BASIS_CEILING"]["any_basis"]["ROWS"]
                    if r["CONTINUOUS_HAZARD_LAMBDA"] != NI]
            self.assertGreater(lams[0] / lams[-1], 20)

    def test_survival_is_non_increasing(self):
        for a in P["ACCOUNT_PRIORS"].values():
            s = [r["SURVIVAL_S"]
                 for r in a["HAZARD_BY_BASIS_CEILING"]["any_basis"]["ROWS"]]
            self.assertEqual(s, sorted(s, reverse=True))


class NothingIsInvented(unittest.TestCase):

    def test_the_archive_can_never_price_an_exit(self):
        self.assertEqual(P["EV_EXIT_HISTORICAL"], NI)

    def test_the_sell_policy_is_not_generalizable(self):
        self.assertEqual(P["WHALE_SELL_POLICY_GENERALIZABLE"], "NOT_ESTABLISHED")

    def test_per_position_evidence_is_declared_absent(self):
        self.assertEqual(P["PER_POSITION_ROWS"], "NOT_PRESENT")
        self.assertEqual(P["HISTORICAL_BOOK_STATE"], "NOT_PRESENT")

    def test_ratios_are_suppressed_on_a_low_merge_base(self):
        for a in P["ACCOUNT_PRIORS"].values():
            for b in a["CHANNEL_BY_PRICE_BAND"].values():
                if b["SUPPORT"] == "LOW_MERGE_BASE_UNSTABLE":
                    self.assertEqual(b["SETTLED_TO_MERGE_ABS_RATIO"], NI)
                    self.assertEqual(b["TOTAL_TO_MERGE_RATIO"], NI)

    def test_priors_are_labelled_initial_and_supersedable(self):
        self.assertTrue(P["PRIORS_ARE_INITIAL_ONLY"])
        self.assertIn("BETTOR_POSTERIOR", P["SUPERSEDED_BY"])


class TheConsensusIsWeightedAndRetainsDisagreement(unittest.TestCase):

    def test_the_excluded_account_is_not_in_the_consensus(self):
        c = P["CROSS_WHALE_CONSENSUS_PRIOR"]
        self.assertIn("swisstony", c["EXCLUDED_FROM_CONSENSUS"])
        self.assertNotIn("swisstony", c["CONSENSUS_MEMBERS"])

    def test_an_unweighted_average_is_explicitly_refused(self):
        self.assertTrue(P["CROSS_WHALE_CONSENSUS_PRIOR"]
                        ["UNWEIGHTED_AVERAGE_REFUSED"])

    def test_the_weighting_is_named_not_assumed(self):
        c = P["CROSS_WHALE_CONSENSUS_PRIOR"]
        self.assertEqual(c["CONSENSUS_WEIGHTING"], "FIRST_SIDE_ACQUISITIONS")
        self.assertIsInstance(c["CONSENSUS_WEIGHTS"], dict)

    def test_consensus_lambda_lies_within_its_members(self):
        for row in P["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]:
            lam = row["CONSENSUS_CONTINUOUS_HAZARD_LAMBDA"]
            if lam == NI:
                continue
            ms = [v for v in row["MEMBER_LAMBDAS"].values() if v != NI]
            self.assertGreaterEqual(lam, min(ms) - 1e-12)
            self.assertLessEqual(lam, max(ms) + 1e-12)

    def test_member_lambdas_are_retained_not_collapsed(self):
        # disagreement survives: the consensus never replaces its members
        for row in P["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]:
            self.assertEqual(len(row["MEMBER_LAMBDAS"]), 3)

    def test_cohort_support_levels_are_assigned(self):
        ok = {"HIGH_COHORT_SUPPORT", "MODERATE_COHORT_SUPPORT",
              "LOW_COHORT_SUPPORT", NI}
        for band in P["CROSS_WHALE_CONSENSUS_PRIOR"]["CHANNEL_SIGN_AGREEMENT"].values():
            for ch in band.values():
                self.assertIn(ch["AGREEMENT"], ok)

    def test_the_cheap_band_merge_sign_has_high_support(self):
        # 4/4 positive merge in 0.00-0.10 -- the regularity the audit reports
        c = P["CROSS_WHALE_CONSENSUS_PRIOR"]["CHANNEL_SIGN_AGREEMENT"]
        self.assertEqual(c["0.00-0.10"]["MERGE"]["AGREEMENT"],
                         "HIGH_COHORT_SUPPORT")


if __name__ == "__main__":
    unittest.main()
