#!/usr/bin/env python3
"""SHADOW_EXIT_LEARNING_V1 boundaries, each one proved rather than described.

Every test here corresponds to a boundary that, if it silently broke, would
produce a result that LOOKS like evidence and is not. That is the failure mode
this phase is most exposed to: the machinery works, the rows accumulate, and
nobody notices that a touch became a fill or that a synthetic position entered
a profitability claim.
"""
import ast
import unittest
from decimal import Decimal as D
from pathlib import Path

import position_state as P
import lifecycle as L


class PhaseStagesAreSeparate(unittest.TestCase):
    """Market snapshots do not become a strategy P&L by being numerous."""

    def test_the_three_stages_are_named_and_we_are_in_the_first(self):
        self.assertEqual(P.PHASE_2_STAGE, P.PHASE_2A)
        self.assertIn("MARKET_STATE", P.PHASE_2A)
        self.assertIn("COUNTERFACTUAL", P.PHASE_2B)
        self.assertIn("POLICY_COMPARISON", P.PHASE_2C)

    def test_the_engine_pnl_is_not_identified_and_not_reportable(self):
        self.assertEqual(P.BETTOR_EXIT_ENGINE_PNL, P.NOT_IDENTIFIED)
        self.assertFalse(P.PROFITABILITY_REPORTABLE)


class ProvenanceIsExactlyOneClass(unittest.TestCase):

    def test_no_actual_position_is_possible_in_this_phase(self):
        self.assertFalse(P.ACTUAL_POSITIONS_POSSIBLE_THIS_PHASE)
        with self.assertRaises(P.ProvenanceViolation):
            P.open_position("p1", P.OBSERVED_ACTUAL_POSITION, {})

    def test_a_touch_is_not_a_fill_and_creates_no_position(self):
        """The single most dangerous silent failure in the whole phase: a
        position book built from prices that traded at our level while the
        queue ahead absorbed every contract."""
        touched = {"F3_TOUCH_ONLY": "YES",
                   "COUNTERFACTUAL_FILL_SUPPORTED_F1": "NO",
                   "TRADED_VOLUME_AT_PRICE": D("0")}
        with self.assertRaises(P.ProvenanceViolation):
            P.open_position("p2", P.COUNTERFACTUAL_MAKER_FILL, {},
                            fill_evidence=touched)

    def test_a_supported_counterfactual_fill_does_create_a_position(self):
        ok = {"COUNTERFACTUAL_FILL_SUPPORTED_F1": "YES",
              "TRADED_VOLUME_AT_PRICE": D("500")}
        pos = P.open_position("p3", P.COUNTERFACTUAL_MAKER_FILL,
                              {"price": "0.41"}, fill_evidence=ok)
        self.assertEqual(pos["ENTRY_PROVENANCE"], P.COUNTERFACTUAL_MAKER_FILL)
        self.assertTrue(pos["ADMISSIBLE_AS_STRATEGY_EVIDENCE"])
        self.assertEqual(pos["FILL_MODEL"], "F1")

    def test_absent_fill_evidence_is_refused_not_assumed(self):
        with self.assertRaises(P.ProvenanceViolation):
            P.open_position("p4", P.COUNTERFACTUAL_MAKER_FILL, {},
                            fill_evidence=None)

    def test_a_synthetic_position_exercises_the_engine_but_proves_nothing(self):
        pos = P.open_position("p5", P.SYNTHETIC_RESEARCH_POSITION, {})
        self.assertFalse(pos["ADMISSIBLE_AS_STRATEGY_EVIDENCE"])
        self.assertFalse(
            P.PROVENANCE_ADMISSIBLE_AS_STRATEGY_EVIDENCE[
                P.SYNTHETIC_RESEARCH_POSITION])

    def test_an_unknown_provenance_class_is_refused(self):
        with self.assertRaises(P.ProvenanceViolation):
            P.open_position("p6", "SOMETHING_ELSE", {})

    def test_every_position_carries_the_counterfactual_label(self):
        pos = P.open_position("p7", P.SYNTHETIC_RESEARCH_POSITION, {})
        self.assertEqual(pos["LABEL"], P.COUNTERFACTUAL)


class FairValueRemainsADependency(unittest.TestCase):

    def test_an_absent_fair_value_is_not_identified(self):
        self.assertEqual(P.fair_value_status({}), P.NOT_IDENTIFIED)

    def test_venue_implied_is_recorded_and_does_not_count(self):
        """Using the venue's own mid as fair value makes every EV a tautology
        in which the market is always right and no edge can exist."""
        st = P.fair_value_status({"FAIR_VALUE": "0.42",
                                  "FV_BASIS": "VENUE_IMPLIED"})
        self.assertEqual(st, P.FV_VENUE_IMPLIED)
        self.assertNotEqual(st, P.FV_VALIDATED)

    def test_an_fv_dependent_ev_stays_unpriced_unless_validated(self):
        v, missing = P.fair_value_dependent_ev(
            P.FV_VENUE_IMPLIED, ("EDGE", D("0.02")))
        self.assertEqual(v, P.NOT_IDENTIFIED)
        self.assertEqual(missing, ("FAIR_VALUE",))

    def test_it_prices_only_when_fair_value_is_validated(self):
        v, missing = P.fair_value_dependent_ev(
            P.FV_VALIDATED, ("EDGE", D("0.02")), ("COST", D("-0.01")))
        self.assertEqual(missing, ())
        self.assertEqual(v, D("0.01"))

    def test_the_allocator_is_not_weakened_to_force_a_decision(self):
        """The expected Phase-2A output is no action, for a stated reason."""
        evs = {P.A_AGGRESSIVE_COMPLEMENT_PAIR: (P.NOT_IDENTIFIED,
                                                ("FAIR_VALUE",))}
        chosen, reason, _ = P.allocate(
            evs, (P.A_AGGRESSIVE_COMPLEMENT_PAIR,))
        self.assertEqual(chosen, P.A_NO_ACTION_RECORDED)
        self.assertEqual(reason, P.COMPARISON_NOT_IDENTIFIED)


class BothPriorsRunInParallel(unittest.TestCase):

    def setUp(self):
        self.pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")

    def test_prior3_is_the_frozen_primary_and_prior4_is_a_ghost(self):
        self.assertEqual(P.PRIMARY_PRIOR, P.PRIOR_3)
        self.assertTrue(P.PRIOR_3_IS_PREREGISTERED_NOT_PROVEN_BETTER)

    def test_both_priors_read_their_own_membership(self):
        l3 = P.lambda_prior(self.pri, "30s-60s", P.PRIOR_3)
        l4 = P.lambda_prior(self.pri, "30s-60s", P.PRIOR_4)
        self.assertAlmostEqual(l3, 0.095357, places=6)
        self.assertAlmostEqual(l4, 0.081806, places=6)
        self.assertNotEqual(l3, l4)

    def test_prior4_is_slower_at_every_measured_interval(self):
        """The sensitivity finding, re-asserted where the engine reads it: a
        4-account prior says a leg pairs LESS readily, and that is exactly the
        direction that changes how long waiting looks worthwhile."""
        for b, _, _ in P.TIME_UNPAIRED_BUCKETS:
            l3 = P.lambda_prior(self.pri, b, P.PRIOR_3)
            l4 = P.lambda_prior(self.pri, b, P.PRIOR_4)
            if P.NOT_IDENTIFIED in (l3, l4):
                continue
            self.assertLess(l4, l3, b)

    def test_the_block_records_both_and_their_disagreement(self):
        blk = P.dual_prior_block(self.pri, "30s-60s", 1.0)
        for k in ("COMPLETION_HAZARD_PRIOR3", "COMPLETION_HAZARD_PRIOR4",
                  "EV_WAIT_PRIOR3", "EV_WAIT_PRIOR4", "DELTA_EV_WAIT",
                  "ACTION_PRIOR3", "ACTION_PRIOR4", "ACTION_DISAGREEMENT"):
            self.assertIn(k, blk)
        self.assertTrue(blk["PRIOR_4_IS_A_GHOST_NOT_THE_PROTOCOL"])

    def test_both_ev_waits_are_unidentified_in_v1_for_the_same_reason(self):
        """Neither prior rescues EV_WAIT: the missing terms are fair value and
        an opportunity set, not the hazard."""
        blk = P.dual_prior_block(self.pri, "30s-60s", 1.0,
                                 evs3={"ev_if_completed": D("0.02")},
                                 evs4={"ev_if_completed": D("0.02")})
        self.assertEqual(blk["EV_WAIT_PRIOR3"], P.NOT_IDENTIFIED)
        self.assertEqual(blk["EV_WAIT_PRIOR4"], P.NOT_IDENTIFIED)
        self.assertIn("EXPECTED_CONTINUATION_VALUE",
                      blk["EV_WAIT_PRIOR3_MISSING"])

    def test_the_delta_is_unidentified_when_either_side_is(self):
        blk = P.dual_prior_block(self.pri, "30s-60s", 1.0)
        self.assertEqual(blk["DELTA_EV_WAIT"], P.NOT_IDENTIFIED)

    def test_an_unknown_prior_name_is_an_error(self):
        with self.assertRaises(ValueError):
            P.lambda_prior(self.pri, "30s-60s", "POLICY_PRIOR_9")


class IncentivesStaySeparate(unittest.TestCase):

    def test_trading_net_is_reported_before_and_apart_from_total(self):
        out = P.incentive_split({"SPREAD_CAPTURE": D("0.01")},
                                {"MAKER_REBATE": D("0.005"),
                                 "LIQUIDITY_INCENTIVE": D("0"),
                                 "OTHER_INCENTIVE": D("0")})
        self.assertTrue(out["REPORTED_TRADING_FIRST"])
        self.assertEqual(out["TRADING_NET_EX_INCENTIVES"], D("0.01"))
        self.assertEqual(out["TOTAL_NET"], D("0.015"))

    def test_a_rebate_cannot_make_a_losing_trade_look_good(self):
        """The rule 'never hold a materially negative-EV position to collect a
        rebate' is enforced by LABELLING, so the number cannot be quoted
        without the label."""
        out = P.incentive_split({"SPREAD_CAPTURE": D("-0.02")},
                                {"MAKER_REBATE": D("0.03"),
                                 "LIQUIDITY_INCENTIVE": D("0"),
                                 "OTHER_INCENTIVE": D("0")})
        self.assertEqual(out["TOTAL_NET"], D("0.01"))
        self.assertEqual(out["INCENTIVE_DEPENDENT"], "YES")
        self.assertLess(out["TRADING_NET_EX_INCENTIVES"], 0)

    def test_an_unmeasured_trading_term_does_not_default_to_zero(self):
        out = P.incentive_split({"SPREAD_CAPTURE": D("0.01"),
                                 "ADVERSE_SELECTION": P.NOT_IDENTIFIED},
                                {"MAKER_REBATE": D("0.005")})
        self.assertEqual(out["TRADING_NET_EX_INCENTIVES"], P.NOT_IDENTIFIED)
        self.assertEqual(out["TOTAL_NET"], P.NOT_IDENTIFIED)
        self.assertIn("ADVERSE_SELECTION", out["TRADING_MISSING_TERMS"])

    def test_the_three_incentive_channels_are_never_one_number(self):
        out = P.incentive_split({}, {"MAKER_REBATE": D("0.005")})
        for k in P.INCENTIVE_TERMS:
            self.assertIn(k, out)


class CapacityIsNotMarketCount(unittest.TestCase):

    def test_788_is_a_candidate_universe_not_788_trades(self):
        self.assertEqual(P.HIGH_ACTIVITY_CANDIDATE_MARKETS, 788)
        self.assertEqual(P.HIGH_ACTIVITY_MEANS,
                         "OBSERVED_HIGH_ACTIVITY_CANDIDATE_MARKETS")
        for wrong in ("INDEPENDENT_OPPORTUNITIES", "ELIGIBLE_POSITIONS",
                      "POSITIVE_EV_ENTRIES"):
            self.assertIn(wrong, P.HIGH_ACTIVITY_DOES_NOT_MEAN)

    def test_without_a_validated_event_key_capacity_is_not_identified(self):
        out = P.candidate_universe(["m1", "m2", "m3"])
        self.assertEqual(out["CANDIDATE_MARKETS"], 3)
        self.assertEqual(out["INDEPENDENT_CAPACITY"], P.NOT_IDENTIFIED)
        self.assertFalse(out["MARKET_COUNT_USED_AS_CAPACITY"])
        self.assertIn("FULLY_CORRELATED", out["CORRELATION_TREATMENT"])

    def test_with_a_validated_key_the_universe_is_stratified_by_event(self):
        keys = {"m1": "e1", "m2": "e1", "m3": "e2"}
        out = P.candidate_universe(list(keys), event_key_of=keys.get)
        self.assertEqual(out["CANDIDATE_MARKETS"], 3)
        self.assertEqual(out["VALIDATED_EVENTS"], 2)
        self.assertEqual(out["INDEPENDENT_CAPACITY"], 2)

    def test_unresolved_identity_retains_its_uncertainty(self):
        """Markets whose event we could not establish are NOT quietly counted
        as independent -- capacity goes back to NOT_IDENTIFIED."""
        keys = {"m1": "e1", "m2": None, "m3": "e2"}
        out = P.candidate_universe(list(keys), event_key_of=keys.get)
        self.assertEqual(out["MARKETS_WITH_UNRESOLVED_EVENT_IDENTITY"], 1)
        self.assertEqual(out["INDEPENDENT_CAPACITY"], P.NOT_IDENTIFIED)
        self.assertEqual(out["INDEPENDENT_CAPACITY_LOWER_BOUND"], 2)


class MakerFirstSplitsEveryExecutionDecision(unittest.TestCase):

    def test_pair_and_exit_are_each_two_actions(self):
        for a in (P.A_PASSIVE_COMPLEMENT_PAIR, P.A_AGGRESSIVE_COMPLEMENT_PAIR,
                  P.A_PASSIVE_SELL_EXIT, P.A_AGGRESSIVE_SELL_EXIT):
            self.assertIn(a, P.ACTIONS)
        self.assertEqual(len(P.ACTIONS), 10)

    def test_the_passive_and_aggressive_sets_do_not_overlap(self):
        self.assertEqual(set(P.PASSIVE_ACTIONS) & set(P.AGGRESSIVE_ACTIONS),
                         set())

    def test_each_has_its_own_probe_so_availability_can_differ(self):
        """A market can offer somewhere to rest and no depth to cross, or the
        reverse. One probe for both would invent the missing one."""
        f = P.feasibility({"TIME_UNPAIRED_S": 30,
                           "COMPLEMENT_PASSIVE_PLACEABLE": True,
                           "COMPLEMENT_EXECUTABLE_NOW": False,
                           "PASSIVE_EXIT_PLACEABLE": False,
                           "AGGRESSIVE_EXIT_DEPTH_EXISTS": False,
                           "HEDGE_INSTRUMENT_EXECUTABLE": False})
        self.assertEqual(f[P.A_PASSIVE_COMPLEMENT_PAIR], P.FEASIBLE)
        self.assertEqual(f[P.A_AGGRESSIVE_COMPLEMENT_PAIR], P.INFEASIBLE)

    def test_a_passive_pair_can_be_chosen_over_an_aggressive_one(self):
        """The whole point of maker-first: when both are available and the
        passive one is worth more, the engine rests."""
        obs = {"TIME_UNPAIRED_S": 30,
               "COMPLEMENT_PASSIVE_PLACEABLE": True,
               "COMPLEMENT_EXECUTABLE_NOW": True,
               "PASSIVE_EXIT_PLACEABLE": False,
               "AGGRESSIVE_EXIT_DEPTH_EXISTS": False,
               "HEDGE_INSTRUMENT_EXECUTABLE": False}
        f = P.feasibility(obs)
        play = P.actions_in_play(f)
        evs = {a: (D("0.00"), ()) for a in play}
        evs[P.A_PASSIVE_COMPLEMENT_PAIR] = (D("0.02"), ())
        evs[P.A_AGGRESSIVE_COMPLEMENT_PAIR] = (D("0.01"), ())
        chosen, reason, _ = P.allocate(evs, play, feas=f)
        self.assertEqual(chosen, P.A_PASSIVE_COMPLEMENT_PAIR)
        self.assertEqual(reason, P.DOMINATES)


class ReentryIsANewDecision(unittest.TestCase):

    def setUp(self):
        self.open = P.open_position("old-1", P.SYNTHETIC_RESEARCH_POSITION,
                                    {"price": "0.41"})
        self.closed = L.close_position(self.open, "EXITED_AGGRESSIVE", 1000.0,
                                       realised_pnl=D("-12.50"))

    def test_closing_converts_pnl_to_sunk_and_forbids_reopening(self):
        self.assertEqual(self.closed["SUNK_PNL"], D("-12.50"))
        self.assertFalse(self.closed["REOPENABLE"])
        self.assertTrue(
            self.closed["SUNK_PNL_IS_NOT_AN_INPUT_TO_ANY_FUTURE_DECISION"])

    def test_a_position_closes_once(self):
        with self.assertRaises(ValueError):
            L.close_position(self.closed, "SETTLED", 1100.0)

    def test_reentry_is_permitted_when_the_current_gate_clears(self):
        """BETTOR MAY re-enter. Refusing would let one bad exit blacklist a
        market whose economics have since become good."""
        d = L.reentry_decision(self.closed, "new-1",
                               ["MARKET_ID", "CURRENT_BOOK", "FAIR_VALUE"],
                               gate_passes=True, size=D("10"))
        self.assertTrue(d["REENTRY_PERMITTED"])
        self.assertTrue(d["NEW_ENTRY_DECISION"])
        self.assertEqual(d["NEW_POSITION_ID"], "new-1")
        self.assertEqual(d["SUNK_PNL_INFLUENCED_THIS_DECISION"], "NO")
        self.assertEqual(d["SIZE_SOURCE"], "CURRENT_ECONOMICS_ONLY")

    def test_reusing_the_old_id_would_reopen_a_closed_trade(self):
        with self.assertRaises(L.ReentryViolation):
            L.reentry_decision(self.closed, "old-1", ["MARKET_ID"], True)

    def test_a_still_open_position_cannot_be_re_entered(self):
        with self.assertRaises(L.ReentryViolation):
            L.reentry_decision(self.open, "new-2", ["MARKET_ID"], True)

    def test_handing_the_gate_the_old_pnl_is_refused_even_if_unused(self):
        """Refused on presence, not on use, because the next edit of the gate
        will use whatever it was given."""
        for leak in L.ENTRY_GATE_FORBIDDEN_INPUTS:
            with self.assertRaises(L.ReentryViolation):
                L.reentry_decision(self.closed, "new-3",
                                   ["MARKET_ID", leak], True)

    def test_the_three_prohibited_motives_are_refused_by_name(self):
        for motive in (L.LOSS_RECOVERY_SIZING, L.REVENGE_REENTRY,
                       L.BREAKEVEN_TARGETING):
            with self.assertRaises(L.ReentryViolation):
                L.reentry_decision(self.closed, "new-4", ["MARKET_ID"], True,
                                   motive=motive)

    def test_clearing_the_checks_does_not_by_itself_authorise_anything(self):
        d = L.reentry_decision(self.closed, "new-5", ["MARKET_ID"],
                               gate_passes=False)
        self.assertFalse(d["REENTRY_PERMITTED"])
        self.assertFalse(d["ENTRY_GATE_CLEARED"])

    def test_two_identical_states_size_identically_whatever_was_lost(self):
        """The property the prohibition actually asserts."""
        small_loss = L.close_position(
            P.open_position("a", P.SYNTHETIC_RESEARCH_POSITION, {}),
            "SETTLED", 1.0, realised_pnl=D("-1"))
        big_loss = L.close_position(
            P.open_position("b", P.SYNTHETIC_RESEARCH_POSITION, {}),
            "SETTLED", 1.0, realised_pnl=D("-10000"))
        a = L.reentry_decision(small_loss, "a2", ["MARKET_ID"], True,
                               size=D("10"))
        b = L.reentry_decision(big_loss, "b2", ["MARKET_ID"], True,
                               size=D("10"))
        self.assertTrue(L.size_is_independent_of_sunk(a["SIZE"], b["SIZE"]))


class TheGateCannotSeeTheSunkPnl(unittest.TestCase):
    """Structural, by walking the AST -- the prohibition must not depend on
    anyone believing the docstring."""

    def setUp(self):
        self.tree = ast.parse(Path(L.__file__).read_text())

    def test_sunk_pnl_is_not_among_the_gate_inputs(self):
        self.assertNotIn("SUNK_PNL", L.ENTRY_GATE_INPUTS)
        self.assertIn("SUNK_PNL", L.ENTRY_GATE_FORBIDDEN_INPUTS)

    def test_the_gate_input_list_and_the_forbidden_list_are_disjoint(self):
        self.assertEqual(
            set(L.ENTRY_GATE_INPUTS) & set(L.ENTRY_GATE_FORBIDDEN_INPUTS),
            set())

    def test_lifecycle_imports_no_network_or_order_capability(self):
        imports = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                imports |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.add(n.module.split(".")[0])
        banned = {"httpx", "requests", "urllib", "socket", "http", "os",
                  "subprocess", "asyncio"}
        self.assertEqual(banned & imports, set())

    def test_no_sizing_function_reads_a_realised_loss(self):
        """A size that is a function of a past loss IS loss-recovery sizing,
        whatever it is called."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if "size" not in node.name:
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(node)
                     if isinstance(n, ast.Attribute)}
            for bad in ("SUNK_PNL", "REALISED_LOSS", "BREAKEVEN_PRICE"):
                self.assertNotIn(bad, names | attrs, node.name)


if __name__ == "__main__":
    unittest.main()
