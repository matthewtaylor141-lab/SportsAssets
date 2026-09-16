#!/usr/bin/env python3
"""Conditioning is a declared property, and the machine checks it."""
import inspect
import unittest
from decimal import Decimal as D

import ev_semantics as S
import position_state as P


class EveryTermDeclaresItsSemantics(unittest.TestCase):

    def test_every_term_has_all_six_declared_properties(self):
        for name, t in S.TERMS.items():
            for f in ("UNIT", "SIGN", "CONDITIONING", "TIME_HORIZON",
                      "SOURCE", "UNCERTAINTY"):
                self.assertIn(f, t, "%s missing %s" % (name, f))
            self.assertIn(t["CONDITIONING"], S.CONDITIONING_VALUES)
            self.assertIn(t["SIGN"], (S.CREDIT, S.DEBIT, S.SIGNED))

    def test_an_unregistered_term_is_refused(self):
        with self.assertRaises(KeyError):
            S.describe("SOME_TERM_NOBODY_REGISTERED")

    def test_the_name_says_the_conditioning(self):
        """A reader should not have to look it up."""
        for name, t in S.TERMS.items():
            if t["CONDITIONING"] == S.CONDITIONAL_ON_FILL:
                self.assertTrue(name.endswith("_CONDITIONAL_ON_FILL"), name)
            elif t["CONDITIONING"] == S.UNCONDITIONAL:
                self.assertTrue(name.endswith("_UNCONDITIONAL"), name)


class TheDoubleCountCheckCatchesTheRealShapes(unittest.TestCase):

    def test_the_same_quantity_inside_and_outside_is_refused(self):
        with self.assertRaises(S.DoubleCountError) as cm:
            S.check_no_double_count(
                ("ADVERSE_SELECTION_CONDITIONAL_ON_FILL",),
                ("EXPECTED_ADVERSE_SELECTION_UNCONDITIONAL",))
        self.assertIn("ADVERSE_SELECTION", str(cm.exception))

    def test_an_unconditional_term_inside_the_fill_branch_is_refused(self):
        """It already carries its own P_FILL; the branch would apply it twice."""
        with self.assertRaises(S.ConditioningError) as cm:
            S.check_no_double_count(
                ("EXPECTED_ADVERSE_SELECTION_UNCONDITIONAL",), ())
        self.assertIn("already contains it", str(cm.exception))

    def test_a_conditional_term_outside_the_fill_branch_is_refused(self):
        with self.assertRaises(S.ConditioningError) as cm:
            S.check_no_double_count(
                (), ("ADVERSE_SELECTION_CONDITIONAL_ON_FILL",))
        self.assertIn("does not fill", str(cm.exception))

    def test_a_clean_split_passes(self):
        rep = S.check_no_double_count(
            ("SPREAD_CAPTURE_CONDITIONAL_ON_FILL",
             "ADVERSE_SELECTION_CONDITIONAL_ON_FILL"),
            ("CAPITAL_RESERVATION_COST_UNCONDITIONAL",))
        self.assertEqual(rep["DOUBLE_COUNT_CHECK"], "PASS")

    def test_inventory_cost_is_caught_in_both_forms_too(self):
        with self.assertRaises(S.DoubleCountError):
            S.check_no_double_count(
                ("INVENTORY_COST_CONDITIONAL_ON_FILL",),
                ("EXPECTED_INVENTORY_COST_UNCONDITIONAL",))


class TheMakerQuoteEVHasTheRightBranchStructure(unittest.TestCase):

    CONDITIONAL = {"SPREAD_CAPTURE_CONDITIONAL_ON_FILL": D("0.02"),
                   "ADVERSE_SELECTION_CONDITIONAL_ON_FILL": D("-0.005")}

    @staticmethod
    def q(*a, **kw):
        """A NEW quote on a flat book: nothing is carried when it misses.

        The state is passed EXPLICITLY on every call, because correction 6
        removed the zero default -- see TheNoFillBranchIsExplicit below.
        """
        kw.setdefault("no_fill_state", S.NO_EXPOSURE_CARRIED)
        return S.ev_maker_quote(*a, **kw)

    def test_risk_terms_are_scaled_by_p_fill_exactly_once(self):
        v, missing = self.q(D("0.5"), self.CONDITIONAL)
        self.assertEqual(missing, ())
        self.assertEqual(v, D("0.5") * D("0.015"))

    def test_a_zero_fill_probability_gives_the_no_fill_value(self):
        """No fill means no position, no inventory, no markout."""
        v, _ = self.q(D("0"), self.CONDITIONAL)
        self.assertEqual(v, D("0"))

    def test_capital_reservation_is_charged_even_when_nothing_fills(self):
        v, _ = self.q(
            D("0"), self.CONDITIONAL,
            {"CAPITAL_RESERVATION_COST_UNCONDITIONAL": D("-0.001")})
        self.assertEqual(v, D("-0.001"))

    def test_partial_fill_is_its_own_outcome(self):
        v, _ = self.q(
            D("0.3"), self.CONDITIONAL, p_partial_fill=D("0.2"),
            partial_terms={"SPREAD_CAPTURE_CONDITIONAL_ON_FILL": D("0.01")})
        self.assertEqual(v, D("0.3") * D("0.015") + D("0.2") * D("0.01"))
        self.assertTrue(S.PARTIAL_FILL_IS_ITS_OWN_OUTCOME)

    def test_probabilities_that_exceed_one_are_refused(self):
        with self.assertRaises(ValueError):
            self.q(D("0.7"), self.CONDITIONAL,
                   p_partial_fill=D("0.5"),
                   partial_terms={
                       "SPREAD_CAPTURE_CONDITIONAL_ON_FILL": D("0")})

    def test_a_not_identified_term_propagates_and_names_itself(self):
        v, missing = self.q(
            D("0.5"), dict(self.CONDITIONAL,
                           **{"CLOSE_COST_CONDITIONAL_ON_FILL":
                              S.NOT_IDENTIFIED}))
        self.assertEqual(v, S.NOT_IDENTIFIED)
        self.assertIn("CLOSE_COST_CONDITIONAL_ON_FILL", missing)

    def test_an_unknown_fill_probability_propagates(self):
        v, missing = self.q(S.NOT_IDENTIFIED, self.CONDITIONAL)
        self.assertEqual(v, S.NOT_IDENTIFIED)
        self.assertIn("P_FULL_FILL", missing)

    def test_a_float_term_is_refused(self):
        with self.assertRaises(TypeError):
            self.q(D("0.5"),
                   {"SPREAD_CAPTURE_CONDITIONAL_ON_FILL": 0.02})

    def test_the_double_count_check_runs_before_any_arithmetic(self):
        with self.assertRaises(S.ConditioningError):
            self.q(D("0.5"),
                   {"EXPECTED_ADVERSE_SELECTION_UNCONDITIONAL": D("0")})


class TheNoFillBranchIsExplicit(unittest.TestCase):
    """CORRECTION 6. A passive EV must say what it leaves behind on a miss.

    The old signature defaulted VALUE_IF_NO_FILL to zero. That is right for a
    new quote on a flat book and WRONG for a passive EXIT, where the position
    survives the miss -- and the same function prices both.
    """

    CONDITIONAL = {"SPREAD_CAPTURE_CONDITIONAL_ON_FILL": D("0.02")}

    def test_an_undeclared_no_fill_branch_is_refused(self):
        with self.assertRaises(S.NoFillBranchError) as cm:
            S.ev_maker_quote(D("0.5"), self.CONDITIONAL)
        self.assertIn("no-fill branch is not declared", str(cm.exception))

    def test_a_bare_number_without_a_state_is_refused(self):
        """A number alone does not say whether we are still exposed."""
        with self.assertRaises(S.NoFillBranchError):
            S.ev_maker_quote(D("0.5"), self.CONDITIONAL,
                             value_if_no_fill=D("0"))

    def test_only_no_exposure_carried_may_default_to_zero(self):
        b = S.no_fill_branch(S.NO_EXPOSURE_CARRIED)
        self.assertEqual(b["EV_IF_NO_FILL"], D("0"))
        self.assertEqual(S.EV_IF_NO_FILL_MAY_DEFAULT_TO_ZERO_ONLY_WHEN,
                         S.NO_EXPOSURE_CARRIED)

    def test_a_state_that_carries_exposure_needs_a_horizon(self):
        with self.assertRaises(S.NoFillBranchError) as cm:
            S.no_fill_branch(S.EXPOSURE_CONTINUES)
        self.assertIn("NO_FILL_TIME_HORIZON", str(cm.exception))

    def test_an_unpriced_continuation_is_not_identified_never_zero(self):
        b = S.no_fill_branch(S.EXPOSURE_CONTINUES, horizon="TO_SETTLEMENT")
        self.assertEqual(b["EV_IF_NO_FILL"], S.NOT_IDENTIFIED)
        self.assertNotEqual(b["EV_IF_NO_FILL"], 0)

    def test_the_missed_exit_propagates_rather_than_pricing_as_free(self):
        v, missing = S.ev_maker_quote(
            D("0.5"), self.CONDITIONAL,
            no_fill_state=S.EXPOSURE_CONTINUES,
            no_fill_horizon="TO_SETTLEMENT")
        self.assertEqual(v, S.NOT_IDENTIFIED)
        self.assertIn("EV_IF_NO_FILL[EXPOSURE_CONTINUES]", missing)

    def test_a_declared_continuation_value_is_used(self):
        v, missing = S.ev_maker_quote(
            D("0.5"), self.CONDITIONAL,
            no_fill_state=S.EXPOSURE_CONTINUES,
            no_fill_horizon="TO_SETTLEMENT",
            value_if_no_fill=D("-0.04"))
        self.assertEqual(missing, ())
        self.assertEqual(v, D("0.5") * D("0.02") + D("0.5") * D("-0.04"))

    def test_a_retained_queue_position_is_its_own_state(self):
        b = S.no_fill_branch(S.QUEUE_POSITION_RETAINED,
                             horizon="NEXT_QUOTE_CYCLE",
                             continuation_value=D("0.001"))
        self.assertEqual(b["NO_FILL_STATE"], S.QUEUE_POSITION_RETAINED)
        self.assertEqual(b["EV_IF_NO_FILL"], D("0.001"))

    def test_an_unknown_state_is_refused(self):
        with self.assertRaises(S.NoFillBranchError):
            S.no_fill_branch("PROBABLY_FINE")

    def test_a_whale_completion_hazard_may_not_seed_p_fill(self):
        """CORRECTION 5, enforced at the EV call site as well."""
        import whale_bridge as W
        with self.assertRaises(W.FillSourceError):
            S.ev_maker_quote(D("0.5"), self.CONDITIONAL,
                             no_fill_state=S.NO_EXPOSURE_CARRIED,
                             p_fill_source="WHALE_COMPLETION_HAZARD")


class CertainExecutionIsGatedOnProvenDepth(unittest.TestCase):
    """CORRECTION 7. A best ask proves a price. It proves no size."""

    OK = dict(depth_source=S.DEPTH_SOURCE_REQUIRED, snapshot_age_s=1)

    def test_full_depth_from_a_fresh_snapshot_allows_certain(self):
        g = S.certain_execution_gate(500, executable_depth=500, **self.OK)
        self.assertEqual(g["CONDITIONING_ALLOWED"], S.CERTAIN)
        self.assertIs(S.assert_certain_allowed(g), g)

    def test_a_best_ask_alone_does_not_establish_certainty(self):
        g = S.certain_execution_gate(500, executable_depth=500,
                                     depth_source="BEST_ASK_PRESENT",
                                     snapshot_age_s=0)
        self.assertEqual(g["CONDITIONING_ALLOWED"], S.NOT_ESTABLISHED)
        for phrase in ("PRESENCE_OF_A_BEST_ASK", "A_QUOTED_PRICE_WITH_NO_SIZE"):
            self.assertIn(phrase, S.CERTAIN_IS_NOT_ESTABLISHED_BY)

    def test_a_one_lot_book_does_not_make_a_500_lot_order_certain(self):
        g = S.certain_execution_gate(500, executable_depth=1, **self.OK)
        self.assertEqual(g["CONDITIONING_ALLOWED"], S.NOT_ESTABLISHED)
        self.assertEqual(g["DEPTH_SHORTFALL"], "499")
        with self.assertRaises(S.ExecutabilityError):
            S.assert_certain_allowed(g)

    def test_an_untimed_snapshot_is_not_evidence_about_now(self):
        g = S.certain_execution_gate(10, executable_depth=1000,
                                     depth_source=S.DEPTH_SOURCE_REQUIRED)
        self.assertEqual(g["CONDITIONING_ALLOWED"], S.NOT_ESTABLISHED)
        self.assertIn("no age", g["WHY"])

    def test_a_stale_snapshot_is_refused(self):
        g = S.certain_execution_gate(10, executable_depth=1000,
                                     depth_source=S.DEPTH_SOURCE_REQUIRED,
                                     snapshot_age_s=60)
        self.assertEqual(g["CONDITIONING_ALLOWED"], S.NOT_ESTABLISHED)
        self.assertTrue(S.STALE_SNAPSHOT_IS_NOT_DEPTH)

    def test_unidentified_depth_is_refused(self):
        g = S.certain_execution_gate(10, executable_depth=S.NOT_IDENTIFIED,
                                     **self.OK)
        self.assertEqual(g["CONDITIONING_ALLOWED"], S.NOT_ESTABLISHED)

    def test_the_gate_is_named_in_the_guard_block(self):
        g = S.semantic_guards()
        self.assertEqual(g["AGGRESSIVE_CERTAIN_EXECUTION_GATE"],
                         "PROVEN_EXECUTABLE_DEPTH_FOR_FULL_SIZE")
        self.assertEqual(g["PASSIVE_NO_FILL_BRANCH"], "EXPLICIT")


class TheEarlierClaimAboutEvPairNowWasWrong(unittest.TestCase):
    """ev_pair_now prices an action that EXECUTES NOW, so unconditional terms
    are correct there. This pins the correction so it cannot be re-reversed."""

    def test_ev_pair_now_prices_the_aggressive_action(self):
        self.assertEqual(P.PROBES[P.A_AGGRESSIVE_COMPLEMENT_PAIR],
                         "COMPLEMENT_EXECUTABLE_NOW")
        self.assertIn("executable", P.ev_pair_now.__doc__)

    def test_its_terms_are_registered_as_certain_not_conditional(self):
        for t in ("LOCKED_PAIR_VALUE_CERTAIN", "EXECUTION_COSTS_CERTAIN"):
            self.assertEqual(S.conditioning_of(t), S.CERTAIN)

    def test_a_certain_term_may_not_be_put_in_a_fill_branch(self):
        with self.assertRaises(S.ConditioningError):
            S.check_no_double_count(("LOCKED_PAIR_VALUE_CERTAIN",), ())

    def test_the_passive_actions_are_the_ones_with_no_ev_function(self):
        """The real gap: a passive action's feasibility exists, its
        fill-conditional EV function did not."""
        self.assertEqual(P.PROBES[P.A_PASSIVE_COMPLEMENT_PAIR],
                         "COMPLEMENT_PASSIVE_PLACEABLE")
        names = [n for n, _ in inspect.getmembers(P, inspect.isfunction)]
        self.assertNotIn("ev_passive_quote", names)
        self.assertNotIn("ev_maker_quote", names)


class TheThreeSemanticGuards(unittest.TestCase):

    def test_p_times_one_minus_p_is_only_the_terminal_payout_variance(self):
        self.assertEqual(S.P_TIMES_ONE_MINUS_P_IS,
                         "TERMINAL_BERNOULLI_PAYOUT_VARIANCE")
        self.assertEqual(S.P_TIMES_ONE_MINUS_P_IS_NOT,
                         "THE_COMPLETE_MEASURE_OF_INVENTORY_RISK")
        for f in ("POSITION_SIZE", "TIME_TO_SETTLEMENT", "LIQUIDITY",
                  "CORRELATION", "CAPITAL_OCCUPANCY", "EXIT_ALTERNATIVES"):
            self.assertIn(f, S.INVENTORY_RISK_ALSO_DEPENDS_ON)

    def test_settlement_is_not_called_a_guaranteed_free_exit(self):
        self.assertEqual(S.SETTLEMENT_IS_NOT, "A_GUARANTEED_ZERO_SPREAD_EXIT")
        for r in ("RESOLUTION_RISK", "TIMING_RISK", "COLLATERAL_RISK",
                  "VENUE_RISK"):
            self.assertIn(r, S.SETTLEMENT_RISKS)
        self.assertIn("SUBJECT TO", S.SETTLEMENT_IS)

    def test_queue_imbalance_is_a_diagnostic_until_bettor_has_fills(self):
        self.assertEqual(S.QUEUE_IMBALANCE_STATUS, "MICROSTRUCTURE_DIAGNOSTIC")
        self.assertEqual(S.QUEUE_IMBALANCE_IS_NOT_YET,
                         "BETTOR_ADVERSE_SELECTION_CONDITIONAL_ON_FILL")

    def test_no_trade_is_not_forced_to_zero(self):
        self.assertFalse(S.EV_NO_TRADE_IS_FORCED_TO_ZERO)
        self.assertTrue(S.WAITING_MAY_HAVE_OPTION_VALUE)
        self.assertEqual(S.EV_WAIT_NUMERIC_VALUE, S.NOT_IDENTIFIED)

    def test_the_guards_are_reportable_in_one_block(self):
        g = S.semantic_guards()
        self.assertEqual(g["QUEUE_IMBALANCE_STATUS"],
                         "MICROSTRUCTURE_DIAGNOSTIC")
        self.assertFalse(g["EV_NO_TRADE_IS_FORCED_TO_ZERO"])


class TheModuleTouchesNothing(unittest.TestCase):

    def test_read_only_constants(self):
        self.assertTrue(S.THIS_MODULE_CONTACTS_NOTHING)
        self.assertEqual(S.ORDERS, 0)
        self.assertEqual(S.CAPITAL, 0)
        self.assertEqual(S.CREDENTIALS, "NONE")
        self.assertFalse(S.mirror_live)


if __name__ == "__main__":
    unittest.main()
