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

    def test_the_weighting_is_the_interval_risk_set_not_account_size(self):
        """A conditional hazard is weighted by who was STILL AT RISK, not by
        who opened the most positions. The superseded weighting is named so
        the revision is visible rather than silent."""
        c = P["CROSS_WHALE_CONSENSUS_PRIOR"]
        self.assertEqual(c["CONSENSUS_WEIGHTING"], "POOLED_INTERVAL_RISK_SET")
        self.assertEqual(c["CONSENSUS_WEIGHTING_SUPERSEDED"],
                         "FIRST_SIDE_ACQUISITIONS")
        self.assertIsInstance(c["ACQUISITION_WEIGHTS_RETAINED"], dict)

    def test_the_weighting_does_not_claim_to_be_optimal(self):
        c = P["CROSS_WHALE_CONSENSUS_PRIOR"]
        self.assertEqual(c["CONSENSUS_STATISTICAL_OPTIMALITY"],
                         "NOT_ESTABLISHED")

    def test_the_four_way_cell_the_ideal_weight_needs_does_not_exist(self):
        """The completion grid is crossed with BASIS_CEILING but not with
        PRICE_BAND. Saying so is the point: an unavailable cross must not be
        implied by publishing a weight as though it were measured there."""
        c = P["CROSS_WHALE_CONSENSUS_PRIOR"]
        self.assertEqual(
            c["FOUR_WAY_CELL_ACCOUNT_x_BAND_x_CEILING_x_INTERVAL"], NI)

    def test_the_risk_set_share_moves_away_from_the_largest_account(self):
        """The whole reason for the change: RN1 holds 62.3% of acquisitions
        but only 57.7% of the positions still unpaired at 1800s, because its
        own legs completed. The first number would over-weight it there."""
        rows = P["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]
        first = rows[0]["RISK_SET_WEIGHT_SHARE"]["rn1"]
        late = rows[8]["RISK_SET_WEIGHT_SHARE"]["rn1"]
        self.assertAlmostEqual(first, 0.62329, places=4)
        self.assertAlmostEqual(late, 0.57724, places=4)
        self.assertLess(late, first)

    def test_the_pooled_lambda_reproduces_its_definition_from_the_counts(self):
        for r in P["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]:
            lam = r["CONSENSUS_CONTINUOUS_HAZARD_LAMBDA"]
            if lam == NI:
                continue
            m = [x["INTERVAL_MINUTES"] for x in
                 P["ACCOUNT_PRIORS"]["rn1"]["HAZARD_BY_BASIS_CEILING"]
                 ["any_basis"]["ROWS"] if x["INTERVAL"] == r["INTERVAL"]][0]
            want = -math.log(1.0 - r["POOLED_N_COMPLETED"]
                             / r["POOLED_N_AT_RISK"]) / m
            self.assertAlmostEqual(lam, want, places=12)

    def test_the_pooled_risk_set_is_the_sum_of_its_members(self):
        for r in P["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]:
            self.assertEqual(r["POOLED_N_AT_RISK"],
                             sum(r["RISK_SET_N_BY_MEMBER"].values()))

    def test_the_superseded_estimator_is_retained_for_comparison(self):
        """Both numbers are published. The revision moved the tail lambda by
        about 1.4%; hiding the old one would make that unverifiable."""
        rows = P["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]
        for r in rows:
            self.assertIn("ACQUISITION_WEIGHTED_CONTINUOUS_HAZARD_LAMBDA", r)
        late = rows[8]
        self.assertLess(late["CONSENSUS_CONTINUOUS_HAZARD_LAMBDA"],
                        late["ACQUISITION_WEIGHTED_CONTINUOUS_HAZARD_LAMBDA"])

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


class TheUncertaintyTravelsWithTheHazard(unittest.TestCase):
    """Two cells with the same lambda and very different support must not
    receive the same decision weight. That requires the counts."""

    def _rows(self, acct="rn1", key="any_basis"):
        return P["ACCOUNT_PRIORS"][acct]["HAZARD_BY_BASIS_CEILING"][key]["ROWS"]

    def test_every_interval_carries_its_risk_set_and_its_completions(self):
        for r in self._rows():
            self.assertIsInstance(r["N_AT_RISK_AT_START"], int)
            self.assertIsInstance(r["N_COMPLETED_IN_INTERVAL"], int)
            self.assertGreater(r["N_AT_RISK_AT_START"], 0)
            self.assertGreaterEqual(r["N_COMPLETED_IN_INTERVAL"], 0)

    def test_the_risk_set_is_opens_minus_everyone_already_completed(self):
        rows = self._rows()
        opens = P["ACCOUNT_PRIORS"]["rn1"]["FIRST_SIDE_ACQUISITIONS"]
        self.assertEqual(rows[0]["N_AT_RISK_AT_START"], opens)
        done = 0
        for r in rows:
            self.assertEqual(r["N_AT_RISK_AT_START"], opens - done)
            done += r["N_COMPLETED_IN_INTERVAL"]

    def test_the_risk_set_only_ever_shrinks(self):
        rows = self._rows()
        for a, b in zip(rows, rows[1:]):
            self.assertLessEqual(b["N_AT_RISK_AT_START"],
                                 a["N_AT_RISK_AT_START"])

    def test_the_se_comes_from_the_counts_and_reproduces_its_formula(self):
        for r in self._rows():
            se = r["HAZARD_LAMBDA_SE"]
            if se == NI:
                continue
            n, d, m = (r["N_AT_RISK_AT_START"], r["N_COMPLETED_IN_INTERVAL"],
                       r["INTERVAL_MINUTES"])
            h = d / n
            self.assertAlmostEqual(
                se, math.sqrt(h / ((1 - h) * n)) / m, places=12)

    def test_the_settlement_tail_has_counts_but_no_lambda(self):
        """It has a real risk set and real completions; what it does not have
        is an elapsed duration, so no rate can be formed from it."""
        tail = self._rows()[-1]
        self.assertEqual(tail["INTERVAL"], "3600s-settlement")
        self.assertGreater(tail["N_AT_RISK_AT_START"], 0)
        self.assertGreater(tail["N_COMPLETED_IN_INTERVAL"], 0)
        self.assertEqual(tail["CONTINUOUS_HAZARD_LAMBDA"], NI)
        self.assertEqual(tail["HAZARD_LAMBDA_SE"], NI)
        self.assertEqual(tail["HAZARD_LAMBDA_CI95"], NI)

    def test_a_ceiling_curve_says_which_risk_set_convention_it_uses(self):
        """A basis-ceiling curve treats an above-ceiling completion as still
        at risk. That is a sub-distribution quantity, not a cause-specific
        hazard, and the row says so rather than letting a reader assume."""
        any_r = self._rows(key="any_basis")[3]
        ceil_r = self._rows(key="ceiling_0.90")[3]
        self.assertEqual(any_r["RISK_SET_CONVENTION"],
                         "EXACT_OPENS_MINUS_COMPLETED")
        self.assertEqual(ceil_r["RISK_SET_CONVENTION"],
                         "SUB_DISTRIBUTION_ABOVE_CEILING_TREATED_AS_AT_RISK")
        self.assertEqual(ceil_r["CAUSE_SPECIFIC_HAZARD"], "NOT_COMPUTED")
        # and the gap between the two conventions is visible, not hidden
        self.assertLess(ceil_r["N_AT_RISK_ANY_BASIS_DEPARTURES"],
                        ceil_r["N_AT_RISK_AT_START"])

    def test_the_archive_rate_and_the_recovered_counts_agree(self):
        """The whole risk-set derivation rests on the denominator being
        opens_total. If that ever stopped holding, every N_AT_RISK here would
        be wrong, so it is checked rather than believed."""
        for acct in P["ACCOUNT_PRIORS"]:
            rows = (P["ACCOUNT_PRIORS"][acct]["HAZARD_BY_BASIS_CEILING"]
                    ["any_basis"]["ROWS"])
            opens = P["ACCOUNT_PRIORS"][acct]["FIRST_SIDE_ACQUISITIONS"]
            done = 0
            for r in rows:
                done += r["N_COMPLETED_IN_INTERVAL"]
                self.assertAlmostEqual(r["CUMULATIVE_COMPLETION_F"],
                                       done / opens, places=4, msg=acct)


class TheExclusionTravelsWithTheArtifact(unittest.TestCase):
    """An account never disappears from the consensus silently."""

    def setUp(self):
        self.e = P["EXCLUSION_PROVENANCE"]["swisstony"]

    def test_the_account_specific_prior_still_exists(self):
        self.assertEqual(self.e["SWISSTONY_INCLUDED_IN_ACCOUNT_SPECIFIC_PRIOR"],
                         "YES")
        self.assertIn("swisstony", P["ACCOUNT_PRIORS"])
        self.assertEqual(self.e["SWISSTONY_INCLUDED_IN_CONSENSUS"], "NO")

    def test_the_reason_is_recorded_and_is_not_a_reconciliation_failure(self):
        self.assertIn("two-source", self.e["EXCLUSION_REASON"])
        self.assertIn("RECONCILES",
                      self.e["EXCLUSION_IS_NOT_A_RECONCILIATION_FAILURE"])

    def test_the_part_of_the_reason_we_do_not_have_is_marked_unknown(self):
        """The specific two-source comparison behind the flag is not retained
        in this workspace. Naming fields we cannot point to would be inventing
        the reason, so the gap is declared instead."""
        self.assertEqual(self.e["WHICH_FIELDS_PREVENT_INCLUSION"],
                         "NOT_IDENTIFIED_IN_THIS_WORKSPACE")

    def test_the_exclusion_was_frozen_before_any_hazard_was_cut(self):
        self.assertEqual(self.e["WHETHER_EXCLUSION_WAS_FROZEN_BEFORE_RESULT"],
                         "YES")
        self.assertIn("ee7329c", self.e["FROZEN_AT"])

    def test_swisstony_still_supports_the_descriptive_findings(self):
        """It is excluded from the ESTIMATE, not from the description. Its
        own lambda series is present and decays like the others."""
        rows = (P["ACCOUNT_PRIORS"]["swisstony"]["HAZARD_BY_BASIS_CEILING"]
                ["any_basis"]["ROWS"])
        lams = [r["CONTINUOUS_HAZARD_LAMBDA"] for r in rows
                if r["CONTINUOUS_HAZARD_LAMBDA"] != NI]
        self.assertTrue(all(lams[i] >= lams[i + 1]
                            for i in range(len(lams) - 1)))
        self.assertGreater(lams[0] / lams[-1], 50)
