#!/usr/bin/env python3
"""The whale bridge: a prior BETTOR grows out of, never an authority."""
import unittest

import whale_bridge as W


class MillionsOfFillsAreNotMillionsOfObservations(unittest.TestCase):

    def test_the_fill_count_is_refused_as_a_sample_size(self):
        e = W.effective_n(n_fills=4692866)
        self.assertEqual(e["EFFECTIVE_N"], W.NOT_IDENTIFIED)
        self.assertTrue(e["FILL_COUNT_IS_NOT_SAMPLE_SIZE"])

    def test_positions_are_used_when_events_are_absent(self):
        e = W.effective_n(n_fills=4692866, n_positions=259271)
        self.assertEqual(e["EFFECTIVE_N"], 259271)
        self.assertEqual(e["EFFECTIVE_N_LEVEL"], "POSITION")
        self.assertIn("correlated", e["EFFECTIVE_N_OVERSTATES_PRECISION_BECAUSE"])

    def test_events_win_when_available(self):
        e = W.effective_n(n_fills=1000, n_positions=100, n_events=7)
        self.assertEqual(e["EFFECTIVE_N"], 7)
        self.assertEqual(e["EFFECTIVE_N_LEVEL"], "EVENT")

    def test_the_hierarchy_is_declared_in_order(self):
        self.assertEqual(W.HIERARCHY, ("EVENT", "MARKET", "POSITION", "FILL"))

    def test_the_artefacts_carry_no_event_ids_and_say_so(self):
        self.assertEqual(W.EVENT_IDS_IN_WHALE_ARTEFACTS, "NOT_PRESENT")
        self.assertIn("LOWER BOUND", W.WHY_METHOD_IS_A_FLOOR)


class FillsDoNotIdentifyPolicy(unittest.TestCase):

    def test_the_fill_distribution_is_identified_and_the_policy_is_not(self):
        self.assertEqual(W.WHALE_ORDER_POLICY, W.NOT_IDENTIFIED)
        self.assertEqual(W.WHALE_OPPORTUNITY_SELECTION_POLICY,
                         W.NOT_IDENTIFIED)
        self.assertNotEqual(W.WHALE_FILL_STATE_DISTRIBUTION, W.NOT_IDENTIFIED)

    def test_what_the_archive_cannot_see_is_named(self):
        for f in ("RESTING_QUOTES_THAT_NEVER_FILLED", "CANCELS",
                  "MISSED_FILLS", "DECLINED_OPPORTUNITIES",
                  "CONTEMPORANEOUS_QUEUE"):
            self.assertIn(f, W.UNOBSERVED_IN_THE_ARCHIVE)


class TheWhalesAreNotOneStrategy(unittest.TestCase):

    def test_each_account_has_its_own_mechanism_family(self):
        self.assertIn(W.M_TWO_SIDED_PASSIVE_MM, W.mechanisms_of("rn1"))
        self.assertIn(W.M_DIRECTIONAL_PRICING,
                      W.mechanisms_of("homerunhazard"))
        self.assertIn(W.M_LIVE_DIRECTIONAL_PRICING,
                      W.mechanisms_of("swisstony"))
        self.assertNotEqual(set(W.mechanisms_of("rn1")),
                            set(W.mechanisms_of("homerunhazard")))

    def test_completion_and_directional_evidence_may_not_be_pooled(self):
        ok, why = W.poolable(W.M_COMPLETION, W.M_DIRECTIONAL_PRICING)
        self.assertFalse(ok)
        self.assertIn("DIFFERENT_ESTIMANDS", why)

    def test_the_same_mechanism_pools_with_itself(self):
        ok, _ = W.poolable(W.M_COMPLETION, W.M_COMPLETION)
        self.assertTrue(ok)

    def test_an_unstated_pair_is_not_assumed_poolable(self):
        ok, why = W.poolable(W.M_CAPITAL_RECYCLING, W.M_SETTLEMENT_HOLD)
        self.assertFalse(ok)
        self.assertIn("NOT_ESTABLISHED", why)


class SupportLevelCannotBeBoughtWithFillCount(unittest.TestCase):

    def test_one_account_alone_cannot_reach_strong(self):
        s = W.support_level(effective_n_value=50000, supporting_accounts=1,
                            mechanism_agreement=True, in_distribution=True,
                            venue_equivalence="STRONG")
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.MODERATE)

    def test_two_agreeing_accounts_with_adequate_n_reach_strong(self):
        s = W.support_level(effective_n_value=5000, supporting_accounts=2,
                            mechanism_agreement=True, in_distribution=True,
                            venue_equivalence="PARTIAL")
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.STRONG)

    def test_out_of_distribution_short_circuits(self):
        s = W.support_level(effective_n_value=10**9, supporting_accounts=4,
                            mechanism_agreement=True, in_distribution=False)
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.OUT_OF_DISTRIBUTION)

    def test_no_venue_equivalence_caps_support_at_weak(self):
        s = W.support_level(effective_n_value=10**6, supporting_accounts=4,
                            mechanism_agreement=True, in_distribution=True,
                            venue_equivalence="NONE")
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.WEAK)

    def test_unknown_support_is_not_identified_not_zero(self):
        s = W.support_level()
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.NOT_IDENTIFIED)


class VenueEquivalenceIsNeverAssumedFromPayoff(unittest.TestCase):

    def test_completion_is_partial_not_strong(self):
        v = W.venue_equivalence(W.M_COMPLETION)
        self.assertEqual(v["VENUE_MECHANISM_EQUIVALENCE"], "PARTIAL")
        self.assertIn("NOT the same implementation", v["NOTE"])

    def test_the_hedge_mechanism_has_no_established_equivalent(self):
        v = W.venue_equivalence(W.M_HEDGE_INVENTORY_CLOSE)
        self.assertEqual(v["VENUE_MECHANISM_EQUIVALENCE"], W.NOT_IDENTIFIED)

    def test_same_payoff_does_not_imply_same_execution(self):
        self.assertTrue(W.venue_equivalence(W.M_COMPLETION)[
            "SAME_PAYOFF_DOES_NOT_IMPLY_SAME_EXECUTION"])


class ThePriorDecaysAsBettorLearns(unittest.TestCase):

    def test_with_no_native_evidence_the_prior_carries_everything(self):
        b = W.blend_weights(259271, None)
        self.assertEqual(b["PRIOR_WEIGHT"], 1.0)
        self.assertEqual(b["NATIVE_WEIGHT"], 0.0)

    def test_native_weight_rises_with_native_evidence(self):
        a = W.blend_weights(259271, 50)
        b = W.blend_weights(259271, 5000)
        self.assertLess(a["NATIVE_WEIGHT"], b["NATIVE_WEIGHT"])
        self.assertGreater(b["NATIVE_WEIGHT"], 0.9)

    def test_the_prior_never_outweighs_by_its_own_size(self):
        """A huge whale n must not swamp BETTOR forever: the weight depends on
        NATIVE n against a credibility constant, not on the prior's n."""
        small = W.blend_weights(100, 200)
        huge = W.blend_weights(10**9, 200)
        self.assertEqual(small["NATIVE_WEIGHT"], huge["NATIVE_WEIGHT"])

    def test_a_regime_shift_zeroes_the_prior_rather_than_decaying_it(self):
        b = W.blend_weights(259271, 10, regime_shift=True)
        self.assertEqual(b["PRIOR_WEIGHT"], 0.0)
        self.assertTrue(b["REGIME_SHIFT_FLAG"])
        self.assertIn("different world", b["WHY"])

    def test_there_is_no_single_blended_score(self):
        self.assertTrue(W.NO_SINGLE_BLENDED_SCORE)
        for c in ("P_COMPLETION", "COMPLETION_HAZARD", "RESIDUAL_OUTCOME"):
            self.assertIn(c, W.PRIOR_COMPONENTS)


class Day1ModeIsDesignedAndNotActivated(unittest.TestCase):

    def test_the_mode_is_off(self):
        self.assertFalse(W.WHALE_ANCHORED_MODE_ACTIVE)
        self.assertEqual(W.WHALE_ANCHORED_MODE_IS,
                         "A_LAUNCH_SAFEGUARD_NOT_A_PERMANENT_RESTRICTION")

    def test_route_a_needs_both_whale_support_and_positive_bettor_ev(self):
        yes = W.day1_admission(whale_support=W.STRONG, bettor_ev_positive=True,
                               mechanism=W.M_COMPLETION)
        self.assertEqual(yes["DECISION"], "ADMIT_CAPITAL_CANDIDATE")
        no = W.day1_admission(whale_support=W.STRONG, bettor_ev_positive=False)
        self.assertEqual(no["DECISION"], "SHADOW")

    def test_out_of_distribution_starts_in_shadow(self):
        d = W.day1_admission(whale_support=W.OUT_OF_DISTRIBUTION,
                             bettor_ev_positive=True)
        self.assertEqual(d["DECISION"], "SHADOW")

    def test_route_b_does_not_require_whale_support(self):
        d = W.day1_admission(whale_support=W.OUT_OF_DISTRIBUTION,
                             structural_validated=True)
        self.assertEqual(d["ROUTE"], W.ROUTE_B)
        self.assertEqual(d["DECISION"], "ADMIT_CAPITAL_CANDIDATE")

    def test_no_admission_result_is_ever_an_instruction_to_trade(self):
        for d in (W.day1_admission(whale_support=W.STRONG,
                                   bettor_ev_positive=True),
                  W.day1_admission(structural_validated=True)):
            self.assertFalse(d["ACTIVATED"])


class TheModuleTouchesNothing(unittest.TestCase):

    def test_read_only(self):
        self.assertTrue(W.THIS_MODULE_CONTACTS_NOTHING)
        self.assertEqual(W.ORDERS, 0)
        self.assertEqual(W.CAPITAL, 0)
        self.assertFalse(W.mirror_live)

    def test_the_evidence_hierarchy_is_declared_in_order(self):
        self.assertEqual(W.EVIDENCE_HIERARCHY[0], "PRIMARY_WHALE_OBSERVATION")
        self.assertEqual(W.EVIDENCE_HIERARCHY[-1], "TERTIARY_BETTOR_NATIVE")


if __name__ == "__main__":
    unittest.main()
