#!/usr/bin/env python3
"""The whale bridge: a prior BETTOR grows out of, never an authority."""
import unittest

import whale_bridge as W


class MillionsOfFillsAreNotMillionsOfObservations(unittest.TestCase):

    def test_the_fill_count_is_refused_as_a_sample_size(self):
        e = W.effective_n(n_fills=4692866)
        self.assertEqual(e["POSITION_LEVEL_N"], W.NOT_IDENTIFIED)
        self.assertEqual(e["INDEPENDENT_EFFECTIVE_N"], W.NOT_IDENTIFIED)
        self.assertTrue(e["FILL_COUNT_IS_NOT_SAMPLE_SIZE"])

    def test_positions_give_a_position_level_n_not_an_independent_one(self):
        """CORRECTION 3. The count bounds independence; it does not measure it."""
        e = W.effective_n(n_fills=4692866, n_positions=259271)
        self.assertEqual(e["POSITION_LEVEL_N"], 259271)
        self.assertEqual(e["INDEPENDENT_EFFECTIVE_N"], W.NOT_IDENTIFIED)
        self.assertEqual(e["INDEPENDENT_N_UPPER_BOUND"], 259271)
        self.assertEqual(e["N_LEVEL_USED"], "POSITION")
        self.assertEqual(e["POSITION_LEVEL_INTERVAL_WIDTH"],
                         "LOWER_BOUND_ON_TRUE_CLUSTER_ROBUST_WIDTH")
        self.assertEqual(e["WEIGHTING_STATUS"], "HEURISTIC")
        self.assertTrue(e["ANTI_CONFIDENCE_CAPPED"])
        self.assertIn("correlated", e["WHY_INDEPENDENT_N_IS_NOT_IDENTIFIED"])

    def test_there_is_no_key_called_effective_n_any_more(self):
        """The old name claimed the very independence the data lacks."""
        for kw in ({"n_fills": 10}, {"n_positions": 10}, {"n_markets": 10},
                   {"n_events": 10}):
            self.assertNotIn("EFFECTIVE_N", W.effective_n(**kw))

    def test_events_are_the_one_level_that_can_be_independent(self):
        e = W.effective_n(n_fills=1000, n_positions=100, n_events=7)
        self.assertEqual(e["INDEPENDENT_EFFECTIVE_N"], 7)
        self.assertEqual(e["N_LEVEL_USED"], "EVENT")
        self.assertFalse(e["ANTI_CONFIDENCE_CAPPED"])

    def test_markets_are_not_events_either(self):
        e = W.effective_n(n_markets=500)
        self.assertEqual(e["INDEPENDENT_EFFECTIVE_N"], W.NOT_IDENTIFIED)
        self.assertEqual(e["INDEPENDENT_N_UPPER_BOUND"], 500)

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
        s = W.support_level(position_level_n=50000, supporting_accounts=1,
                            mechanism_agreement=True, in_distribution=True,
                            venue_equivalence="STRONG")
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.MODERATE)

    def test_two_agreeing_accounts_with_adequate_n_reach_strong(self):
        s = W.support_level(position_level_n=5000, supporting_accounts=2,
                            mechanism_agreement=True, in_distribution=True,
                            venue_equivalence="PARTIAL")
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.STRONG)

    def test_out_of_distribution_short_circuits(self):
        s = W.support_level(position_level_n=10**9, supporting_accounts=4,
                            mechanism_agreement=True, in_distribution=False)
        self.assertEqual(s["WHALE_SUPPORT_LEVEL"], W.OUT_OF_DISTRIBUTION)

    def test_no_venue_equivalence_caps_support_at_weak(self):
        s = W.support_level(position_level_n=10**6, supporting_accounts=4,
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


class SwisstonyIsAGhostPriorNotAPrimaryOne(unittest.TestCase):
    """CORRECTION 1. The exclusion was frozen before any prior was built and
    nothing in this workspace resolves it."""

    def test_the_canonical_prior_is_three_accounts(self):
        self.assertEqual(W.PRIMARY_WHALE_PRIOR_ACCOUNTS,
                         ("rn1", "ferrarichampions2026", "homerunhazard"))
        self.assertNotIn("swisstony", W.PRIMARY_WHALE_PRIOR_ACCOUNTS)

    def test_eligibility_is_not_established_not_denied_and_not_granted(self):
        self.assertEqual(W.SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY,
                         W.NOT_ESTABLISHED)
        self.assertEqual(W.SWISSTONY_DISPUTE_RESOLVED, W.NOT_ESTABLISHED)

    def test_the_disputed_field_is_named_from_retained_evidence(self):
        """The FIELD is reconstructible from BETA48_DATA_GATE; the RESOLUTION
        is not, and naming one is not the same as clearing the other."""
        self.assertIn("MERGE", W.SWISSTONY_DISPUTED_FIELD)
        self.assertIn("SIGN", W.SWISSTONY_DISPUTED_FIELD)
        self.assertIn("BETA48_DATA_GATE", W.SWISSTONY_DISPUTED_FIELD_SOURCE)

    def test_a_zero_reconciliation_residual_does_not_clear_the_exclusion(self):
        self.assertTrue(W.RECONCILIATION_RESIDUAL_ZERO_DOES_NOT_CLEAR_THE_EXCLUSION)
        self.assertIn("INDEPENDENT source",
                      W.WHY_A_ZERO_RESIDUAL_IS_NOT_A_RESOLUTION)

    def test_both_priors_exist_and_only_one_is_canonical(self):
        three = W.prior_account_set()
        four = W.prior_account_set(include_sensitivity=True)
        self.assertEqual(three["PRIOR"], "WHALE_PRIOR_3_ACCOUNT")
        self.assertEqual(three["STATUS"], "CANONICAL")
        self.assertEqual(four["PRIOR"], "WHALE_PRIOR_4_ACCOUNT_SENSITIVITY")
        self.assertFalse(four["MAY_BE_PROMOTED_TO_CANONICAL"])

    def test_the_ghost_account_buys_no_supporting_account_count(self):
        """Otherwise the exclusion would be cosmetic: swisstony would still be
        the account that turns MODERATE into STRONG."""
        self.assertEqual(W.prior_account_set(include_sensitivity=True)[
            "SUPPORTING_ACCOUNTS"], 3)

    def test_a_canonical_prior_containing_swisstony_is_refused(self):
        with self.assertRaises(W.PriorScopeError) as cm:
            W.assert_canonical(["rn1", "SwissTony"])
        self.assertIn("GHOST_PRIOR_SENSITIVITY_ONLY", str(cm.exception))

    def test_the_three_account_set_passes_its_own_guard(self):
        W.assert_canonical(W.PRIMARY_WHALE_PRIOR_ACCOUNTS)


class EveryCellIsConditionedOnWhaleSelectedEntries(unittest.TestCase):
    """CORRECTION 2. The sample is positions the whales chose to open."""

    def test_the_condition_is_declared(self):
        self.assertEqual(W.SELECTION_CONDITION,
                         "OBSERVED_WHALE_ENTERED_POSITIONS_ONLY")
        self.assertEqual(W.COUNTERFACTUAL_ENTRY_OUTCOME, W.NOT_IDENTIFIED)

    def test_whale_evidence_alone_cannot_create_a_trade(self):
        self.assertFalse(W.WHALE_EVIDENCE_ALONE_CAN_CREATE_A_TRADE)
        self.assertIn("ORIGINATE_A_TRADE", W.WHAT_THE_PRIOR_MAY_NOT_DO)
        self.assertIn("ESTABLISH_THAT_A_PRICE_LEVEL_IS_PROFITABLE",
                      W.WHAT_THE_PRIOR_MAY_NOT_DO)

    def test_the_only_legal_sentence_names_the_sample_first(self):
        s = W.band_statement("0.00-0.10", "MERGE", "POSITIVE")
        self.assertIn("among RETAINED WHALE-ENTERED POSITIONS",
                      s["STATEMENT"])
        self.assertEqual(s["IS_NOT_A_STATEMENT_ABOUT"],
                         "AN_ARBITRARY_ENTRY_AT_THIS_PRICE")
        self.assertFalse(s["CAN_CREATE_A_TRADE_BY_ITSELF"])

    def test_the_statement_carries_no_unconditional_phrasing(self):
        s = W.band_statement("0.00-0.10", "MERGE", "POSITIVE")
        self.assertEqual(
            W.scan_for_unconditional_phrasing(s["STATEMENT"]), [])

    def test_the_forbidden_phrasing_is_caught_by_name(self):
        hits = W.scan_for_unconditional_phrasing(
            "In short, PRICE BELOW 0.50 IS PROFITABLE.")
        self.assertIn("price below 0.50 is profitable", hits)

    def test_a_ghost_account_needs_the_sensitivity_flag_to_appear(self):
        with self.assertRaises(W.PriorScopeError):
            W.band_statement("0.00-0.10", "MERGE", "POSITIVE",
                             accounts=["swisstony"])
        s = W.band_statement("0.00-0.10", "MERGE", "POSITIVE",
                             accounts=["swisstony"], sensitivity=True)
        self.assertIn("SENSITIVITY RUN", s["STATEMENT"])
        self.assertEqual(s["PRIOR"], "WHALE_PRIOR_4_ACCOUNT_SENSITIVITY")


class TheJointPriceTimePriorIsNeverManufactured(unittest.TestCase):
    """CORRECTION 4. The source measured two marginals, not their product."""

    def test_the_joint_is_not_identified(self):
        self.assertEqual(W.PRICE_TIME_JOINT_PRIOR, W.NOT_IDENTIFIED)

    def test_each_marginal_on_its_own_is_measured(self):
        for kw in ({"account": "rn1", "price_band": "0.00-0.10"},
                   {"account": "rn1", "time_unpaired_interval": "4h"},
                   {"account": "rn1", "fill_size_bucket": "0-100"},
                   {"account": "rn1", "iso_week": "2025-W28"}):
            self.assertEqual(W.cell_support(**kw)["CELL_SUPPORT"], "MEASURED")

    def test_crossing_two_marginals_returns_the_refusal(self):
        c = W.cell_support(account="rn1", price_band="0.00-0.10",
                           time_unpaired_interval="4h")
        self.assertEqual(c["CELL_SUPPORT"], W.NOT_IDENTIFIED)
        self.assertEqual(c["PRICE_TIME_JOINT_PRIOR"], W.NOT_IDENTIFIED)
        self.assertFalse(c["INTERPOLATED"])
        self.assertIn("NOT x PRICE_BAND", c["WHY"])

    def test_all_four_construction_routes_are_named_as_forbidden(self):
        for route in ("MULTIPLICATION_OF_MARGINALS",
                      "INTERPOLATION_ACROSS_BANDS",
                      "CROSS_PRODUCT_TABLE_CONSTRUCTION",
                      "ADDITIVE_DECOMPOSITION"):
            self.assertIn(route, W.JOINT_PRIOR_CONSTRUCTION_FORBIDDEN)

    def test_the_explicit_join_function_raises_by_construction(self):
        for method in ("MULTIPLICATION_OF_MARGINALS", "INTERPOLATION", None):
            with self.assertRaises(W.JointPriorError):
                W.join_price_and_time("0.00-0.10", "4h", method=method)

    def test_an_unmeasured_axis_is_not_identified_not_interpolated(self):
        c = W.cell_support(account="rn1", sport="TENNIS")
        self.assertEqual(c["CELL_SUPPORT"], W.NOT_IDENTIFIED)
        self.assertIn("SPORT", c["UNMEASURED_AXES_REQUESTED"])

    def test_an_unrecognised_axis_is_refused_rather_than_allowed(self):
        c = W.cell_support(account="rn1", phase_of_moon="WAXING")
        self.assertEqual(c["CELL_SUPPORT"], W.NOT_IDENTIFIED)
        self.assertIn("absence of a rule is not permission", c["WHY"])


class WhaleCompletionHazardIsNotBettorsFillProbability(unittest.TestCase):
    """CORRECTION 5. Different event, different agent, different venue."""

    def test_the_constants_say_it_outright(self):
        self.assertEqual(W.WHALE_COMPLETION_AS_P_FILL, "FORBIDDEN")
        self.assertEqual(W.BETTOR_P_FILL_SOURCE,
                         "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED")
        self.assertEqual(W.BETTOR_P_FILL_STATUS, W.NOT_IDENTIFIED)

    def test_every_completion_field_name_is_refused_as_a_fill_source(self):
        for bad in W.FORBIDDEN_P_FILL_SOURCES:
            with self.assertRaises(W.FillSourceError):
                W.assert_p_fill_source(bad)

    def test_a_disguised_completion_source_is_still_caught(self):
        with self.assertRaises(W.FillSourceError):
            W.assert_p_fill_source("rn1_whale_completion_hazard_ceiling_1.00")

    def test_a_native_source_passes(self):
        r = W.assert_p_fill_source("BETTOR_NATIVE_ADMITTED_FILLS")
        self.assertEqual(r["WHALE_COMPLETION_AS_P_FILL"], "FORBIDDEN")

    def test_the_hazard_may_still_answer_a_completion_question(self):
        r = W.completion_prior(0.42, "P_COMPLETION_ONCE_WE_HOLD_A_LEG")
        self.assertEqual(r["MAY_NOT_INFORM"], "BETTOR_P_FILL")
        self.assertEqual(r["VENUE_OF_MEASUREMENT"],
                         "LEGACY_POLYMARKET_TWO_TOKEN")

    def test_the_hazard_may_not_be_served_as_a_fill_probability(self):
        for use in ("P_FILL", "P_FULL_FILL", "EXECUTION_PROBABILITY"):
            with self.assertRaises(W.FillSourceError):
                W.completion_prior(0.42, use)


class PairBasisAbroveParIsGrossNotNet(unittest.TestCase):
    """CORRECTION 8. Above par is a fact about prices paid, not a verdict."""

    def test_what_it_establishes_and_what_it_does_not(self):
        self.assertEqual(W.PAIR_BASIS_ABOVE_PAR_IS,
                         "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES")
        self.assertEqual(W.PAIR_BASIS_ABOVE_PAR_IS_NOT,
                         "ESTABLISHED_FINAL_NET_LOSS")
        self.assertEqual(W.NET_OF_INCENTIVES_PAIR_OUTCOME, W.NOT_IDENTIFIED)

    def test_incentives_are_not_separately_retained(self):
        self.assertEqual(W.INCENTIVES_IN_WHALE_ARTEFACTS,
                         "NOT_SEPARATELY_RETAINED")
        self.assertIn("MAKER_REBATE", W.INCENTIVES_THAT_COULD_OFFSET)

    def test_a_reading_above_par_carries_both_halves(self):
        r = W.pair_basis_reading("ferrarichampions2026", "0.90-1.01", 1.01177)
        self.assertEqual(r["GROSS_BASIS_ABOVE_PAR"], "YES")
        self.assertEqual(r["ESTABLISHES"],
                         "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES")
        self.assertEqual(r["DOES_NOT_ESTABLISH"], "ESTABLISHED_FINAL_NET_LOSS")

    def test_rn1_below_par_is_described_not_praised(self):
        r = W.pair_basis_reading("rn1", "0.90-1.01", 0.99825)
        self.assertEqual(r["GROSS_BASIS_ABOVE_PAR"], "NO")
        self.assertEqual(r["LANGUAGE"], "DESCRIPTIVE_NOT_EVALUATIVE")


class TheWeightingIsHeuristicAndCapped(unittest.TestCase):
    """CORRECTION 3, at the point where the count turns into a weight."""

    def test_a_position_level_weight_is_capped_in_both_directions(self):
        hi = W.blend_weights(259271, 10**9)
        lo = W.blend_weights(259271, 0.0000001)
        self.assertLessEqual(hi["NATIVE_WEIGHT"], W.ANTI_CONFIDENCE_WEIGHT_CAP)
        self.assertAlmostEqual(lo["NATIVE_WEIGHT"],
                               1.0 - W.ANTI_CONFIDENCE_WEIGHT_CAP, places=9)
        self.assertTrue(hi["ANTI_CONFIDENCE_CAPPED"])
        self.assertEqual(hi["WEIGHTING_STATUS"], "HEURISTIC")
        self.assertEqual(hi["INDEPENDENT_EFFECTIVE_N"], W.NOT_IDENTIFIED)

    def test_an_event_level_count_is_not_capped(self):
        b = W.blend_weights(1000, 10**9, n_level="EVENT")
        self.assertFalse(b["ANTI_CONFIDENCE_CAPPED"])
        self.assertGreater(b["NATIVE_WEIGHT"], W.ANTI_CONFIDENCE_WEIGHT_CAP)

    def test_only_a_declared_regime_shift_reaches_certainty(self):
        b = W.blend_weights(259271, 10, regime_shift=True)
        self.assertEqual(b["NATIVE_WEIGHT"], 1.0)
        self.assertTrue(b["REGIME_SHIFT_FLAG"])


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
