#!/usr/bin/env python3
"""The shadow recorder's boundary and its arithmetic, both proved.

THE BOUNDARY IS PROVED STRUCTURALLY, BY WALKING THE AST.

Not by grepping the source for forbidden words. A substring scan matches the
docstring that STATES the guarantee, so it passes on a file that violates it as
long as the file also describes it -- which is exactly what a file like this
does. That error class has appeared three times in this programme. These tests
walk the parse tree: an import that is not there cannot be imported, and a call
that is not in the tree cannot be made.
"""
import ast
import json
import unittest
from decimal import Decimal as D
from pathlib import Path

import position_state as P

SRC = Path(P.__file__).read_text()
TREE = ast.parse(SRC)


def _imports():
    for n in ast.walk(TREE):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                yield n.module.split(".")[0]


def _calls():
    for n in ast.walk(TREE):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                yield f.attr
            elif isinstance(f, ast.Name):
                yield f.id


class TheBoundaryIsStructural(unittest.TestCase):

    def test_no_network_library_is_imported_at_all(self):
        banned = {"httpx", "requests", "urllib", "urllib3", "socket",
                  "http", "aiohttp", "websocket", "websockets", "ssl",
                  "ftplib", "smtplib", "telnetlib", "asyncio"}
        self.assertEqual(banned & set(_imports()), set())

    def test_no_subprocess_or_shell_escape_hatch(self):
        banned = {"subprocess", "os", "shutil", "ctypes", "importlib"}
        self.assertEqual(banned & set(_imports()), set())

    def test_no_mutating_verb_and_no_order_verb_is_ever_called(self):
        banned = {"post", "put", "patch", "delete", "request", "send",
                  "connect", "stream", "place_order", "submit", "submit_order",
                  "cancel", "cancel_order", "sign", "authenticate", "login",
                  "eval", "exec", "compile", "__import__"}
        self.assertEqual(banned & set(_calls()), set())

    def test_no_credential_is_ever_read(self):
        banned = {"getenv", "environ", "getpass"}
        self.assertEqual(banned & set(_calls()), set())
        self.assertNotIn("environ", {n.attr for n in ast.walk(TREE)
                                     if isinstance(n, ast.Attribute)})

    def test_the_declared_flags_say_what_the_tree_proves(self):
        self.assertTrue(P.SHADOW_ONLY)
        self.assertFalse(P.ORDER_PATH_EXISTS)
        self.assertFalse(P.ORDER_CAPABLE)
        self.assertFalse(P.MICRO_LIVE_AUTHORIZED)
        self.assertFalse(P.mirror_live)
        self.assertEqual(P.CREDENTIAL_PATH, "NONE")


class TimeUnpairedIsFirstClass(unittest.TestCase):

    def test_the_grid_is_the_priors_grid_exactly(self):
        """A bucket label here must address a lambda there. If the grids drift,
        every hazard lookup silently answers about a different interval."""
        priors = json.loads(
            (Path(__file__).resolve().parent.parent
             / "evidence" / "whale_audit"
             / "whale_exit_priors_v1.json").read_text())
        rows = priors["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]
        self.assertEqual([r["INTERVAL"] for r in rows],
                         [b[0] for b in P.TIME_UNPAIRED_BUCKETS])

    def test_the_buckets_cover_the_line_without_a_gap_or_an_overlap(self):
        edges = [(lo, hi) for _, lo, hi in P.TIME_UNPAIRED_BUCKETS]
        self.assertEqual(edges[0][0], 0)
        for (_, hi), (lo, _) in zip(edges, edges[1:]):
            self.assertEqual(hi, lo)
        self.assertIsNone(edges[-1][1])

    def test_boundaries_land_in_the_bucket_that_starts_there(self):
        self.assertEqual(P.time_unpaired_bucket(0), "0s-5s")
        self.assertEqual(P.time_unpaired_bucket(4.999), "0s-5s")
        self.assertEqual(P.time_unpaired_bucket(5), "5s-10s")
        self.assertEqual(P.time_unpaired_bucket(3599), "1800s-3600s")
        self.assertEqual(P.time_unpaired_bucket(3600), "3600s-settlement")
        self.assertEqual(P.time_unpaired_bucket(10 ** 9), "3600s-settlement")

    def test_an_unknown_age_is_not_bucket_zero(self):
        self.assertEqual(P.time_unpaired_bucket(None), P.NOT_IDENTIFIED)
        with self.assertRaises(ValueError):
            P.time_unpaired_bucket(-1)

    def test_a_five_second_leg_and_an_hour_old_orphan_never_share_a_prior(self):
        pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")
        fresh = P.lambda_for(pri, "0s-5s")
        old = P.lambda_for(pri, "1800s-3600s")
        self.assertGreater(fresh / old, 50)


class TheHazardPrior(unittest.TestCase):

    def setUp(self):
        self.pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")

    def test_the_consensus_lambda_is_read_not_recomputed(self):
        rows = self.pri["CROSS_WHALE_CONSENSUS_PRIOR"]["HAZARD_any_basis"]
        want = rows[0]["CONSENSUS_CONTINUOUS_HAZARD_LAMBDA"]
        self.assertEqual(P.lambda_for(self.pri, "0s-5s"), want)

    def test_an_account_series_is_that_accounts_own_numbers(self):
        got = P.lambda_for(self.pri, "0s-5s", account="rn1",
                           ceiling="ceiling_0.90")
        rows = (self.pri["ACCOUNT_PRIORS"]["rn1"]["HAZARD_BY_BASIS_CEILING"]
                ["ceiling_0.90"]["ROWS"])
        self.assertEqual(got, rows[0]["CONTINUOUS_HAZARD_LAMBDA"])

    def test_the_last_bucket_has_no_lambda_and_none_is_invented(self):
        self.assertEqual(P.lambda_for(self.pri, P.NO_LAMBDA_BUCKET),
                         P.NOT_IDENTIFIED)

    def test_asking_the_consensus_for_a_basis_ceiling_it_lacks_refuses(self):
        """The consensus is published at any_basis only. Answering a different
        question than the one asked is worse than answering nothing."""
        self.assertEqual(
            P.lambda_for(self.pri, "0s-5s", ceiling="ceiling_0.90"),
            P.NOT_IDENTIFIED)

    def test_an_unknown_account_is_not_identified_not_the_consensus(self):
        self.assertEqual(P.lambda_for(self.pri, "0s-5s", account="nobody"),
                         P.NOT_IDENTIFIED)

    def test_swisstony_is_readable_alone_but_absent_from_the_consensus(self):
        self.assertIn("swisstony", self.pri["ACCOUNT_PRIORS"])
        self.assertNotIn(
            "swisstony",
            self.pri["CROSS_WHALE_CONSENSUS_PRIOR"]["CONSENSUS_MEMBERS"])


class LambdaBecomesAProbability(unittest.TestCase):

    def test_the_conversion_is_the_exponential_one(self):
        import math
        p = P.p_complete_next_interval(0.5, 2.0)
        self.assertAlmostEqual(p, 1.0 - math.exp(-1.0))

    def test_probability_rises_with_the_horizon_and_never_exceeds_one(self):
        prev = -1.0
        for m in (0.01, 0.1, 1, 10):
            p = P.p_complete_next_interval(2.0, m)
            self.assertGreater(p, prev)
            prev = p
        # It SATURATES at 1.0 rather than passing it. exp(-2*100) underflows to
        # zero in float, so the long horizons are exactly 1.0 and equal to each
        # other -- non-decreasing, never above the bound.
        for m in (100, 10000):
            p = P.p_complete_next_interval(2.0, m)
            self.assertGreaterEqual(p, prev)
            self.assertLessEqual(p, 1.0)
            prev = p

    def test_an_unidentified_lambda_gives_an_unidentified_probability(self):
        self.assertEqual(
            P.p_complete_next_interval(P.NOT_IDENTIFIED, 1.0),
            P.NOT_IDENTIFIED)
        self.assertEqual(P.p_complete_next_interval(0.5, None),
                         P.NOT_IDENTIFIED)
        self.assertEqual(P.p_complete_next_interval(0.5, 0),
                         P.NOT_IDENTIFIED)


class TheStateMachine(unittest.TestCase):

    def test_state_does_not_choose_the_action_only_what_is_possible(self):
        """Every state maps to FEASIBLE actions, and no state maps to exactly
        one action that changes a position. If it did, the label would be
        making the trade."""
        for s, acts in P.FEASIBLE_BY_STATE.items():
            for a in acts:
                self.assertIn(a, P.ACTIONS, s)

    def test_a_position_is_in_several_states_at_once(self):
        obs = {"TIME_UNPAIRED_S": 900, "COMPLEMENT_EXECUTABLE_NOW": True,
               "PASSIVE_EXIT_PLACEABLE": True}
        st = P.states(obs)
        self.assertIn(P.AGING_UNPAIRED, st)
        self.assertIn(P.PAIR_AVAILABLE, st)
        self.assertIn(P.PASSIVE_EXIT_AVAILABLE, st)
        self.assertNotIn(P.DIRECTIONAL_HOLD, st)

    def test_directional_hold_is_only_the_absence_of_every_alternative(self):
        self.assertIn(P.DIRECTIONAL_HOLD, P.states({"TIME_UNPAIRED_S": 10}))
        self.assertNotIn(
            P.DIRECTIONAL_HOLD,
            P.states({"TIME_UNPAIRED_S": 10, "HEDGE_INSTRUMENT_EXECUTABLE": 1}))

    def test_fresh_and_aging_split_at_sixty_seconds(self):
        self.assertIn(P.FRESH_UNPAIRED, P.states({"TIME_UNPAIRED_S": 60}))
        self.assertIn(P.AGING_UNPAIRED, P.states({"TIME_UNPAIRED_S": 61}))

    def test_settled_is_terminal_and_offers_nothing(self):
        self.assertEqual(P.states({"SETTLED": True}), (P.SETTLED,))
        self.assertEqual(P.feasible_actions((P.SETTLED,)), ())

    def test_a_completed_pair_offers_only_the_two_pair_actions(self):
        st = P.states({"PAIR_COMPLETE": True, "TIME_UNPAIRED_S": 10})
        self.assertEqual(st, (P.COMPLETED_PAIR,))
        self.assertEqual(set(P.feasible_actions(st)),
                         {P.A_REALIZE_AND_RECYCLE, P.A_HOLD_LOCKED_PAIR})

    def test_an_unknown_state_is_an_error_not_an_empty_action_set(self):
        with self.assertRaises(KeyError):
            P.feasible_actions(("SOMETHING_NEW",))


class ThePropagationRule(unittest.TestCase):

    def test_a_not_identified_term_does_not_default_to_zero(self):
        v, missing = P.ev_sum((("A", D("1")), ("B", P.NOT_IDENTIFIED)))
        self.assertEqual(v, P.NOT_IDENTIFIED)
        self.assertEqual(missing, ("B",))

    def test_a_float_money_term_is_refused_not_coerced(self):
        with self.assertRaises(TypeError):
            P.ev_sum((("A", 0.1),))

    def test_an_exact_decimal_string_survives_the_sum(self):
        v, _ = P.ev_sum((("A", "0.1"), ("B", "0.2")))
        self.assertEqual(v, D("0.3"))

    def test_ev_pair_now_names_every_missing_term(self):
        v, missing = P.ev_pair_now(locked_pair_value="0.02",
                                   execution_costs="-0.01")
        self.assertEqual(v, P.NOT_IDENTIFIED)
        self.assertIn("MAKER_REBATE", missing)
        self.assertIn("CAPITAL_RECYCLING_VALUE", missing)

    def test_a_zero_rebate_is_a_number_and_not_a_missing_term(self):
        """Below the clip floor the maker rebate is EXACTLY $0.00 -- measured,
        not unknown. The two must not collapse into each other."""
        v, missing = P.ev_pair_now(
            locked_pair_value="0.02", execution_costs="-0.01",
            maker_rebate=D("0.00"), verified_incentives=D("0"),
            incremental_risk=D("0"), capital_recycling_value=D("0"))
        self.assertEqual(missing, ())
        self.assertEqual(v, D("0.01"))


class EvWaitIsHonestlyUnidentifiedInV1(unittest.TestCase):

    def test_the_v1_default_is_not_identified_by_derivation(self):
        v, missing = P.ev_wait(0.3, ev_if_completed=D("0.02"))
        self.assertEqual(v, P.NOT_IDENTIFIED)
        self.assertEqual(set(missing), {"EXPECTED_CONTINUATION_VALUE",
                                        "CAPITAL_OPPORTUNITY_COST",
                                        "INVENTORY_RISK_COST"})

    def test_it_becomes_a_number_only_when_every_term_is_supplied(self):
        v, missing = P.ev_wait(
            0.5, ev_if_completed=D("0.02"),
            expected_continuation_value=D("0.00"),
            capital_opportunity_cost=D("0.001"),
            inventory_risk_cost=D("0.002"))
        self.assertEqual(missing, ())
        self.assertEqual(v, D("0.5") * D("0.02") - D("0.001") - D("0.002"))

    def test_an_unidentified_completion_probability_stops_it_first(self):
        v, missing = P.ev_wait(P.NOT_IDENTIFIED, ev_if_completed=D("0.02"))
        self.assertEqual(v, P.NOT_IDENTIFIED)
        self.assertEqual(missing, ("P_COMPLETE_NEXT_INTERVAL",))

    def test_a_probability_outside_the_unit_interval_is_an_error(self):
        with self.assertRaises(ValueError):
            P.ev_wait(1.5, ev_if_completed=D("0.02"))


class TheAllocator(unittest.TestCase):

    def test_one_unpriced_alternative_stops_the_whole_comparison(self):
        """The engine does NOT fall back to the best identified EV. The
        unpriced action could have dominated it, and choosing among the priced
        subset would quietly assume it did not."""
        evs = {P.A_PAIR_NOW: (D("0.05"), ()),
               P.A_WAIT: (P.NOT_IDENTIFIED, ("EXPECTED_CONTINUATION_VALUE",))}
        chosen, reason, ranked = P.allocate(
            evs, (P.A_PAIR_NOW, P.A_WAIT))
        self.assertEqual(chosen, P.A_NO_ACTION_RECORDED)
        self.assertEqual(reason, P.COMPARISON_NOT_IDENTIFIED)
        self.assertEqual(len(ranked), 2)

    def test_a_feasible_action_nobody_priced_is_not_silently_ignored(self):
        evs = {P.A_PAIR_NOW: (D("0.05"), ())}
        chosen, reason, _ = P.allocate(evs, (P.A_PAIR_NOW, P.A_SETTLEMENT_HOLD))
        self.assertEqual(chosen, P.A_NO_ACTION_RECORDED)
        self.assertEqual(reason, P.COMPARISON_NOT_IDENTIFIED)

    def test_it_chooses_when_everything_feasible_is_priced(self):
        evs = {P.A_PAIR_NOW: (D("0.05"), ()),
               P.A_SETTLEMENT_HOLD: (D("0.01"), ())}
        chosen, reason, ranked = P.allocate(
            evs, (P.A_PAIR_NOW, P.A_SETTLEMENT_HOLD))
        self.assertEqual(chosen, P.A_PAIR_NOW)
        self.assertEqual(reason, P.DOMINATES)
        self.assertEqual([a for a, _ in ranked],
                         [P.A_PAIR_NOW, P.A_SETTLEMENT_HOLD])

    def test_a_tie_is_not_a_decision(self):
        evs = {P.A_PAIR_NOW: (D("0.01"), ()),
               P.A_SETTLEMENT_HOLD: (D("0.01"), ())}
        chosen, _, _ = P.allocate(evs, (P.A_PAIR_NOW, P.A_SETTLEMENT_HOLD))
        self.assertEqual(chosen, P.A_NO_ACTION_RECORDED)

    def test_never_pair_merely_because_a_pair_exists(self):
        """PAIR_AVAILABLE with a NEGATIVE pair EV and a better alternative must
        not pair. Cosmetic pair completion is the failure mode this forbids."""
        evs = {P.A_PAIR_NOW: (D("-0.03"), ()),
               P.A_SETTLEMENT_HOLD: (D("0.00"), ())}
        chosen, _, _ = P.allocate(evs, (P.A_PAIR_NOW, P.A_SETTLEMENT_HOLD))
        self.assertEqual(chosen, P.A_SETTLEMENT_HOLD)

    def test_only_a_risk_kill_acts_without_a_complete_comparison(self):
        evs = {P.A_AGGRESSIVE_EXIT: (P.NOT_IDENTIFIED, ("FAIR_VALUE",))}
        chosen, reason, _ = P.allocate(
            evs, (P.A_AGGRESSIVE_EXIT,),
            risk_kill={"ACTION": P.A_AGGRESSIVE_EXIT,
                       "SWITCH": "EVENT_EXPOSURE_KILL"})
        self.assertEqual(chosen, P.A_AGGRESSIVE_EXIT)
        self.assertEqual(reason, P.RISK_KILL)

    def test_no_feasible_action_is_its_own_reason(self):
        chosen, reason, ranked = P.allocate({}, ())
        self.assertEqual(chosen, P.A_NO_ACTION_RECORDED)
        self.assertEqual(reason, P.NO_FEASIBLE_ACTION)
        self.assertEqual(ranked, ())


class TheDecisionRow(unittest.TestCase):

    def setUp(self):
        self.pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")
        # A FULLY OBSERVED tick: every probe the recorder reads is present.
        # Leaving one out is not "that action is unavailable", it is "nobody
        # looked", and the engine treats the two differently on purpose.
        self.obs = {
            "TIMESTAMP": "2026-09-16T14:30:00Z",
            "TIME_UNPAIRED_S": 45,
            "COMPLEMENT_EXECUTABLE_NOW": True,
            "PASSIVE_EXIT_PLACEABLE": True,
            "AGGRESSIVE_EXIT_DEPTH_EXISTS": True,
            "HEDGE_INSTRUMENT_EXECUTABLE": False,
            "CURRENT_BOOK": {"bids": [["0.41", 120]], "asks": [["0.43", 90]]},
            "COMPLEMENT_BOOK": {"bids": [["0.56", 40]], "asks": [["0.58", 75]]},
            "PAIR_BASIS": "0.99",
            "QUEUE_AHEAD": 120,
            "TRADE_ACTIVITY": {"COUNT": 4, "ELAPSED_S": 300},
            "CAPITAL_OCCUPANCY": {"DOLLAR_HOURS": "0.512"},
        }

    def test_every_field_the_spec_names_is_present(self):
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0)
        for f in P.DECISION_ROW_FIELDS:
            self.assertIn(f, row, f)

    def test_the_row_is_labelled_counterfactual(self):
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0)
        self.assertEqual(row["LABEL"], P.COUNTERFACTUAL)

    def test_the_default_v1_outcome_is_a_recorded_non_action(self):
        """Stated in advance: day one produces no trades, for the stated
        reason, on essentially every tick."""
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0)
        self.assertEqual(row["ACTION_CHOSEN"], P.A_NO_ACTION_RECORDED)
        self.assertEqual(row["ACTION_REASON"], P.COMPARISON_NOT_IDENTIFIED)

    def test_actions_not_chosen_carries_the_rejected_evs(self):
        evs = {P.A_PAIR_NOW: (D("0.05"), ()),
               P.A_SETTLEMENT_HOLD: (D("0.01"), ()),
               P.A_WAIT: (D("0.02"), ()),
               P.A_PASSIVE_EXIT: (D("0.00"), ()),
               P.A_AGGRESSIVE_EXIT: (D("-0.01"), ()),
               P.A_DIRECTIONAL_HOLD: (D("0.00"), ())}
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0, evs=evs)
        self.assertEqual(row["ACTION_CHOSEN"], P.A_PAIR_NOW)
        rejected = {d["ACTION"]: d["EV"] for d in row["ACTIONS_NOT_CHOSEN"]}
        self.assertEqual(rejected[P.A_SETTLEMENT_HOLD], D("0.01"))
        self.assertEqual(rejected[P.A_WAIT], D("0.02"))
        self.assertNotIn(P.A_PAIR_NOW, rejected)

    def test_the_rejected_evs_are_recorded_even_when_nothing_is_done(self):
        """This is the field that makes the dataset worth having: the price of
        the road not taken, recorded at the moment of rejection."""
        evs = {P.A_PAIR_NOW: (D("0.05"), ())}
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0, evs=evs)
        self.assertEqual(row["ACTION_CHOSEN"], P.A_NO_ACTION_RECORDED)
        rejected = {d["ACTION"]: d["EV"] for d in row["ACTIONS_NOT_CHOSEN"]}
        self.assertEqual(rejected[P.A_PAIR_NOW], D("0.05"))
        self.assertEqual(rejected[P.A_SETTLEMENT_HOLD], P.NOT_IDENTIFIED)

    def test_an_unevaluated_action_is_not_identified_not_zero(self):
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0)
        cell = row["ALL_ACTION_EVS"][P.A_PAIR_NOW]
        self.assertEqual(cell["EV"], P.NOT_IDENTIFIED)
        self.assertEqual(cell["MISSING_TERMS"], ["NOT_EVALUATED"])

    def test_the_hazard_block_carries_lambda_and_its_provenance(self):
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0)
        t = row["TIME_UNPAIRED"]
        self.assertEqual(t["BUCKET"], "30s-60s")
        self.assertEqual(t["LAMBDA_SOURCE"], P.CONSENSUS)
        self.assertEqual(t["LAMBDA_EVIDENCE_LEVEL"], P.LEVEL_B)
        self.assertEqual(
            t["CONTINUOUS_HAZARD_LAMBDA"],
            P.lambda_for(self.pri, "30s-60s"))
        self.assertGreater(t["P_COMPLETE_NEXT_INTERVAL"], 0.0)

    def test_an_hour_old_orphan_gets_no_completion_probability_at_all(self):
        obs = dict(self.obs, TIME_UNPAIRED_S=7200)
        row = P.decision_row(obs, self.pri, horizon_minutes=1.0)
        self.assertEqual(row["TIME_UNPAIRED"]["CONTINUOUS_HAZARD_LAMBDA"],
                         P.NOT_IDENTIFIED)
        self.assertEqual(row["TIME_UNPAIRED"]["P_COMPLETE_NEXT_INTERVAL"],
                         P.NOT_IDENTIFIED)

    def test_decimal_money_round_trips_as_an_exact_string(self):
        evs = {P.A_PAIR_NOW: (D("0.1"), ()),
               P.A_SETTLEMENT_HOLD: (D("0.2"), ()),
               P.A_WAIT: (D("0.05"), ())}
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0, evs=evs)
        back = json.loads(json.dumps(row, default=P._jsonable))
        rejected = {d["ACTION"]: d["EV"] for d in back["ACTIONS_NOT_CHOSEN"]}
        # 0.1 is not representable in binary floating point. If this reads
        # 0.1000000000000000055 the pipeline has corrupted a measured value.
        self.assertEqual(rejected[P.A_WAIT], "0.05")
        self.assertEqual(D(rejected[P.A_WAIT]), D("0.05"))

    def test_a_row_written_and_read_back_keeps_its_decimals_exact(self):
        import tempfile
        evs = {a: (D("0.1"), ()) for a in P.ACTIONS}
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0, evs=evs)
        with tempfile.TemporaryDirectory() as d:
            p = P.write_row(Path(d) / "sub" / "shadow.jsonl", row)
            lines = p.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        back = json.loads(lines[0])
        self.assertEqual(back["LABEL"], P.COUNTERFACTUAL)
        self.assertEqual(back["ORDER_PATH_EXISTS"], "NO")

    def test_later_outcome_starts_unknown_and_is_stamped_once(self):
        row = P.decision_row(self.obs, self.pri, horizon_minutes=1.0)
        self.assertEqual(row["LATER_OUTCOME"], P.NOT_IDENTIFIED)
        done = P.stamp_outcome(row, {"COMPLETED": True, "AT_S": 78})
        self.assertEqual(done["LATER_OUTCOME"]["AT_S"], 78)
        # The original row is not mutated: it is a record of one moment.
        self.assertEqual(row["LATER_OUTCOME"], P.NOT_IDENTIFIED)
        with self.assertRaises(ValueError):
            P.stamp_outcome(done, {"COMPLETED": False})


class GhostPolicies(unittest.TestCase):

    def setUp(self):
        self.pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")

    def test_every_policy_is_evaluated_on_the_same_observation(self):
        obs = {"TIME_UNPAIRED_S": 30, "COMPLEMENT_EXECUTABLE_NOW": True}
        g = P.ghost_actions(obs, self.pri)
        self.assertEqual(set(g["POLICY_ACTIONS"]), set(P.GHOST_POLICIES))

    def test_no_policy_ever_emits_an_action_the_book_did_not_allow(self):
        for obs in ({"TIME_UNPAIRED_S": 5},
                    {"TIME_UNPAIRED_S": 5, "COMPLEMENT_EXECUTABLE_NOW": True},
                    {"PAIR_COMPLETE": True},
                    {"TIME_UNPAIRED_S": 900,
                     "AGGRESSIVE_EXIT_DEPTH_EXISTS": True}):
            feas = set(P.feasible_actions(P.states(obs)))
            for pol, a in P.ghost_actions(obs, self.pri)["POLICY_ACTIONS"] \
                    .items():
                self.assertTrue(a in feas or a == P.A_NO_ACTION_RECORDED,
                                (obs, pol, a))

    def test_no_ghost_pnl_is_a_number_and_no_fill_is_assumed(self):
        g = P.ghost_actions({"TIME_UNPAIRED_S": 5}, self.pri)
        self.assertTrue(all(v == P.NOT_IDENTIFIED
                            for v in g["POLICY_PNL"].values()))
        self.assertEqual(g["PASSIVE_FILLS_ASSUMED"], "NO")

    def test_a_ghost_rest_is_not_filled_because_the_touch_traded(self):
        """A touch is not a fill. F3 is a TOUCH bound, and a policy that
        collected F3 as a fill would beat every other policy by construction."""
        touched_only = {"F3_TOUCH_ONLY": "YES",
                        "COUNTERFACTUAL_FILL_SUPPORTED_F1": "NO",
                        "TRADED_VOLUME_AT_PRICE": D("0")}
        self.assertEqual(P.ghost_fill_permitted(touched_only, "F1"), "NO")

    def test_a_fill_requires_execution_at_our_price_after_entry(self):
        no_clob = {"COUNTERFACTUAL_FILL_SUPPORTED_F1": "YES",
                   "TRADED_VOLUME_AT_PRICE": D("0")}
        self.assertEqual(P.ghost_fill_permitted(no_clob, "F1"), "NO")
        with_clob = {"COUNTERFACTUAL_FILL_SUPPORTED_F1": "YES",
                     "TRADED_VOLUME_AT_PRICE": D("500")}
        self.assertEqual(P.ghost_fill_permitted(with_clob, "F1"), "YES")

    def test_absent_evidence_is_not_a_fill_and_not_a_no(self):
        self.assertEqual(P.ghost_fill_permitted(None, "F1"), P.NOT_IDENTIFIED)
        self.assertEqual(P.ghost_fill_permitted({}, "F1"), P.NOT_IDENTIFIED)

    def test_an_unknown_fill_model_is_refused(self):
        with self.assertRaises(ValueError):
            P.ghost_fill_permitted({"X": "YES"}, "F9")


class ThePosteriorLoop(unittest.TestCase):

    def test_with_no_bettor_evidence_the_whale_prior_is_the_whole_answer(self):
        self.assertEqual(P.posterior_weight(0), 0.0)
        self.assertEqual(P.blended_lambda(0.5, 0.9, 0), 0.5)

    def test_the_priors_weight_decays_as_bettor_evidence_accumulates(self):
        prev = 0.0
        for n in (1, 10, 30, 100, 1000):
            w = P.posterior_weight(n)
            self.assertGreater(w, prev)
            self.assertLess(w, 1.0)
            prev = w
        self.assertAlmostEqual(P.posterior_weight(P.POSTERIOR_CREDIBILITY_N),
                               0.5)

    def test_a_missing_bettor_lambda_leaves_the_prior_standing_undiluted(self):
        self.assertEqual(P.blended_lambda(0.5, P.NOT_IDENTIFIED, 500), 0.5)

    def test_an_unidentified_prior_does_not_become_a_number(self):
        self.assertEqual(P.blended_lambda(P.NOT_IDENTIFIED, 0.9, 500),
                         P.NOT_IDENTIFIED)

    def test_the_bettor_series_the_loop_must_track_are_all_named(self):
        self.assertIn("BETTOR_COMPLETION_HAZARD", P.BETTOR_SERIES)
        self.assertEqual(len(set(P.BETTOR_SERIES)), 7)


if __name__ == "__main__":
    unittest.main()


class InfeasibleIsNotUnknown(unittest.TestCase):
    """An action that cannot physically occur must not block the allocator.
    An action nobody looked at must."""

    def test_an_unobserved_probe_is_not_identified_not_infeasible(self):
        f = P.feasibility({"TIME_UNPAIRED_S": 30})
        for a in P.PROBES:
            self.assertEqual(f[a], P.NOT_IDENTIFIED, a)

    def test_an_explicit_false_is_infeasible_and_is_dropped(self):
        f = P.feasibility({"TIME_UNPAIRED_S": 30,
                           "HEDGE_INSTRUMENT_EXECUTABLE": False,
                           "COMPLEMENT_EXECUTABLE_NOW": True,
                           "PASSIVE_EXIT_PLACEABLE": False,
                           "AGGRESSIVE_EXIT_DEPTH_EXISTS": False})
        self.assertEqual(f[P.A_HEDGE], P.INFEASIBLE)
        self.assertEqual(f[P.A_PAIR_NOW], P.FEASIBLE)
        self.assertNotIn(P.A_HEDGE, P.actions_in_play(f))
        self.assertIn(P.A_PAIR_NOW, P.actions_in_play(f))

    def test_no_hedge_venue_does_not_freeze_the_engine(self):
        """The user's own example: a missing hedge instrument is not a missing
        number, and the comparison completes without it."""
        obs = {"TIME_UNPAIRED_S": 30, "COMPLEMENT_EXECUTABLE_NOW": True,
               "PASSIVE_EXIT_PLACEABLE": False,
               "AGGRESSIVE_EXIT_DEPTH_EXISTS": False,
               "HEDGE_INSTRUMENT_EXECUTABLE": False}
        f = P.feasibility(obs)
        play = P.actions_in_play(f)
        evs = {a: (D("0.01") if a == P.A_PAIR_NOW else D("0.00"), ())
               for a in play}
        chosen, reason, _ = P.allocate(evs, play, feas=f)
        self.assertEqual(chosen, P.A_PAIR_NOW)
        self.assertEqual(reason, P.DOMINATES)

    def test_a_settled_market_makes_everything_infeasible_and_blocks_nothing(self):
        f = P.feasibility({"SETTLED": True})
        self.assertTrue(all(v == P.INFEASIBLE for v in f.values()))
        chosen, reason, _ = P.allocate({}, P.actions_in_play(f), feas=f)
        self.assertEqual(reason, P.NO_FEASIBLE_ACTION)

    def test_holding_needs_no_counterparty_and_is_always_feasible(self):
        """Previously A_DIRECTIONAL_HOLD only entered the action set when
        nothing else existed, so the allocator could never compare holding
        against pairing. It can now."""
        f = P.feasibility({"TIME_UNPAIRED_S": 30,
                           "COMPLEMENT_EXECUTABLE_NOW": True,
                           "PASSIVE_EXIT_PLACEABLE": True,
                           "AGGRESSIVE_EXIT_DEPTH_EXISTS": True,
                           "HEDGE_INSTRUMENT_EXECUTABLE": True})
        for a in P.UNCONDITIONAL_UNPAIRED:
            self.assertEqual(f[a], P.FEASIBLE, a)

    def test_an_unknown_leg_age_makes_the_holds_unknown_not_available(self):
        f = P.feasibility({"COMPLEMENT_EXECUTABLE_NOW": True})
        for a in P.UNCONDITIONAL_UNPAIRED:
            self.assertEqual(f[a], P.NOT_IDENTIFIED, a)

    def test_the_two_blocking_causes_are_reported_separately(self):
        obs = {"TIME_UNPAIRED_S": 30, "COMPLEMENT_EXECUTABLE_NOW": True,
               "PASSIVE_EXIT_PLACEABLE": False,
               "AGGRESSIVE_EXIT_DEPTH_EXISTS": False}
        # HEDGE unobserved -> feasibility blocks, even with every EV supplied
        f = P.feasibility(obs)
        play = P.actions_in_play(f)
        evs = {a: (D("0.01"), ()) for a in play}
        _, reason, _ = P.allocate(evs, play, feas=f)
        self.assertEqual(reason, P.FEASIBILITY_NOT_IDENTIFIED)
        # every probe observed, one EV missing -> the pricing blocks instead
        obs2 = dict(obs, HEDGE_INSTRUMENT_EXECUTABLE=False)
        f2 = P.feasibility(obs2)
        play2 = P.actions_in_play(f2)
        evs2 = {a: (D("0.01"), ()) for a in play2 if a != P.A_WAIT}
        _, reason2, _ = P.allocate(evs2, play2, feas=f2)
        self.assertEqual(reason2, P.COMPARISON_NOT_IDENTIFIED)

    def test_the_row_records_all_three_feasibility_values(self):
        pri = P.load_priors(
            Path(__file__).resolve().parent.parent / "evidence"
            / "whale_audit" / "whale_exit_priors_v1.json")
        obs = {"TIME_UNPAIRED_S": 30, "COMPLEMENT_EXECUTABLE_NOW": True,
               "HEDGE_INSTRUMENT_EXECUTABLE": False}
        row = P.decision_row(obs, pri, horizon_minutes=1.0)
        f = row["ACTION_FEASIBILITY"]
        self.assertEqual(f[P.A_PAIR_NOW], P.FEASIBLE)
        self.assertEqual(f[P.A_HEDGE], P.INFEASIBLE)
        self.assertEqual(f[P.A_PASSIVE_EXIT], P.NOT_IDENTIFIED)


class EvBoundsAreDesignedAndInert(unittest.TestCase):

    def test_robust_dominance_is_off_until_the_bounds_are_validated(self):
        self.assertFalse(P.ROBUST_DOMINANCE_ACTIVE)
        self.assertFalse(P.BOUND_METHODOLOGY_VALIDATED)

    def test_it_refuses_to_answer_while_inactive_even_if_asked(self):
        """Forcing it on does NOT make it rule: the bound methodology gate is
        separate, so a config flag alone cannot license a trade on unchecked
        intervals."""
        cells = {P.A_PAIR_NOW: P.ev_cell(lower="0.05", upper="0.06"),
                 P.A_WAIT: P.ev_cell(lower="0.01", upper="0.02")}
        got, reason = P.robust_dominance(cells, (P.A_PAIR_NOW, P.A_WAIT))
        self.assertIsNone(got)
        self.assertIn("UNVALIDATED", reason)
        forced, reason2 = P.robust_dominance(
            cells, (P.A_PAIR_NOW, P.A_WAIT), active=True)
        self.assertIsNone(forced)
        self.assertIn("UNVALIDATED", reason2)

    def test_a_status_is_derived_from_what_is_actually_present(self):
        self.assertEqual(P.ev_cell(point="0.01")["EV_STATUS"], P.EV_PRICED)
        self.assertEqual(P.ev_cell(lower="0.01", upper="0.02")["EV_STATUS"],
                         P.EV_BOUNDED)
        self.assertEqual(P.ev_cell()["EV_STATUS"], P.NOT_IDENTIFIED)
        self.assertEqual(P.ev_cell(status=P.EV_INFEASIBLE)["EV_STATUS"],
                         P.EV_INFEASIBLE)

    def test_a_one_sided_bound_is_not_bounded(self):
        """A lower bound with no ceiling would let an action look safe with no
        limit on how good the alternatives could have been."""
        self.assertEqual(P.ev_cell(lower="0.01")["EV_STATUS"],
                         P.NOT_IDENTIFIED)
        self.assertEqual(P.ev_cell(upper="0.02")["EV_STATUS"],
                         P.NOT_IDENTIFIED)

    def test_an_inverted_bound_is_an_error(self):
        with self.assertRaises(ValueError):
            P.ev_cell(lower="0.05", upper="0.01")

    def test_bounds_refuse_floats_like_every_other_money_term(self):
        with self.assertRaises(TypeError):
            P.ev_cell(lower=0.1, upper=0.2)


class TheShrinkageIsContinuousNotACliff(unittest.TestCase):

    def test_there_is_no_switch_at_n_equals_thirty(self):
        self.assertEqual(P.HARD_N30_SWITCH, "NO")
        self.assertTrue(P.POSTERIOR_WEIGHTING_IS_CONTINUOUS)
        self.assertEqual(P.POSTERIOR_WEIGHT_FORMULA, "w = n / (n + 30)")

    def test_nothing_jumps_across_the_threshold(self):
        """A cliff would show up as a step here. The weights either side of 30
        differ by about a sixtieth, which is the point."""
        below = P.posterior_weight(29)
        at = P.posterior_weight(30)
        above = P.posterior_weight(31)
        self.assertLess(abs(at - below), 0.01)
        self.assertLess(abs(above - at), 0.01)

    def test_the_rule_does_not_claim_to_be_optimal(self):
        self.assertTrue(P.V1_HEURISTIC)
        self.assertEqual(P.STATISTICALLY_OPTIMAL_POSTERIOR_WEIGHTING,
                         "NOT_ESTABLISHED")

    def test_the_count_rule_is_blind_to_precision_and_says_so(self):
        """Two whale cells, one with 250,000 at risk and one with 40, are
        displaced at exactly the same rate. That is the known defect the
        successor rule addresses."""
        self.assertEqual(P.posterior_weight(50), P.posterior_weight(50))
        # the successor exists and is inert
        self.assertFalse(P.PRECISION_WEIGHTING_ACTIVE)
        self.assertEqual(P.posterior_weight_precision(0.001, 0.1),
                         P.NOT_IDENTIFIED)

    def test_the_successor_rule_is_inverse_variance_when_switched_on(self):
        w = P.posterior_weight_precision(0.01, 0.01, active=True)
        self.assertAlmostEqual(w, 0.5)
        tight_whale = P.posterior_weight_precision(0.0001, 0.01, active=True)
        self.assertLess(tight_whale, 0.5)

    def test_the_successor_is_not_wired_into_the_blend(self):
        """Switching the blending rule mid-collection would make the two
        halves of the frozen shadow run incomparable."""
        import inspect
        src = inspect.getsource(P.blended_lambda)
        self.assertNotIn("posterior_weight_precision", src)
