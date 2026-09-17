#!/usr/bin/env python3
"""THROUGHPUT_V2: does the volume model still smuggle in a rule nobody wrote?

THE DEFECT THAT PROMPTED THIS MODULE. V1 reported "2,204 x $25 = $55,100" as an
arithmetic daily ceiling. It is a ceiling only if fills are capped at one per
market per day, and no such rule exists. The first test class makes that figure
permanently unable to call itself a maximum.

THE SECOND DEFECT, WHICH IS THE OPPOSITE ONE. Once many cycles per market are
allowed, order count becomes free: requote on every poll and the throughput
number climbs without a single extra dollar of economics. The anti-churn class
pins that closed.

THE THIRD IS THE ONE THE TOTALS WORK EXPOSED. 647 totals on 41 contests are
many execution opportunities and few risk units. A model that cannot tell those
apart reports a diversified book that is a concentrated bet.

Nothing here contacts a venue.
"""
import re
import unittest
from decimal import Decimal as D
from pathlib import Path

import throughput_v2 as V2

NOT_IDENTIFIED = "NOT_IDENTIFIED"


class TheCeilingLanguageIsCorrected(unittest.TestCase):

    def test_the_figure_is_named_single_pass_not_maximum(self):
        r = V2.single_pass_notional(2204, "25")
        self.assertEqual(r["SINGLE_PASS_ONE_FILL_PER_MARKET_NOTIONAL"], "55100")
        self.assertEqual(r["MAX_DAILY_GROSS_NOTIONAL"], NOT_IDENTIFIED)

    def test_it_declares_itself_not_a_ceiling(self):
        r = V2.single_pass_notional(2204, "25")
        self.assertFalse(r["IS_A_CEILING"])
        self.assertFalse(r["ONE_FILL_PER_MARKET_RULE_EXISTS"])
        self.assertIn("no such rule exists", r["NOT_A_CEILING"])

    def test_it_says_what_would_make_it_a_ceiling(self):
        r = V2.single_pass_notional(2204, "25")
        self.assertIn("risk rule capping fills per market",
                      r["WHAT_WOULD_MAKE_IT_A_CEILING"])

    def test_the_module_never_emits_a_max_daily_notional_number(self):
        """A regression guard on the exact phrase that caused the defect."""
        src = Path(V2.__file__).read_text()
        self.assertNotIn('"MAX_DAILY_GROSS_NOTIONAL": str', src)
        self.assertIn('"MAX_DAILY_GROSS_NOTIONAL": NOT_IDENTIFIED', src)


class OneMarketCanTradeManyTimes(unittest.TestCase):

    def test_the_four_counting_levels_are_distinct(self):
        r = V2.order_cycles(markets_touched=10, order_intents=120,
                            filled_orders=30, filled_notional="750")
        self.assertEqual(r["UNIQUE_MARKETS_TRADED"], 10)
        self.assertEqual(r["ORDER_INTENTS"], 120)
        self.assertEqual(r["FILLED_ORDERS"], 30)
        self.assertEqual(r["FILLED_NOTIONAL"], "750")

    def test_intents_per_market_can_exceed_one(self):
        r = V2.order_cycles(markets_touched=10, order_intents=120,
                            filled_orders=30)
        self.assertEqual(D(r["ORDER_INTENTS_PER_MARKET"]), D(12))
        self.assertEqual(D(r["FILLED_ORDERS_PER_MARKET"]), D(3))

    def test_no_cap_on_fills_per_market_exists(self):
        r = V2.order_cycles()
        self.assertEqual(r["FILLS_PER_MARKET_CAP"], NOT_IDENTIFIED)
        self.assertIn("no such rule is authorised", r["NO_ONE_PER_MARKET_CAP"])

    def test_the_three_cycle_paths_are_modelled(self):
        r = V2.order_cycles()
        self.assertEqual(len(r["CYCLE_PATHS"]), 3)
        flat = [step for p in r["CYCLE_PATHS"] for step in p]
        for step in ("CANCEL_OR_EXPIRE", "REQUOTE", "FILL", "PARTIAL_FILL",
                     "INVENTORY_ACTION"):
            self.assertIn(step, flat, step)

    def test_a_market_is_not_an_order(self):
        r = V2.order_cycles()
        self.assertIn("V1 defect", r["A_MARKET_IS_NOT_AN_ORDER"])


class ChurnCannotMasqueradeAsThroughput(unittest.TestCase):

    def intent(self, i, **kw):
        base = {"INTENT_ID": "i%d" % i, "MARKET_SLUG": "m1", "SIDE": "BUY",
                "LIMIT_PRICE": "0.40", "SIZE": "100"}
        base.update(kw)
        return base

    def test_a_clean_stream_reads_all_zero(self):
        r = V2.anti_churn([self.intent(1)])
        self.assertTrue(r["ALL_ZERO"])
        self.assertEqual(r["ANTI_CHURN_STATUS"], "CLEAN")

    def test_a_requote_with_no_state_change_is_caught(self):
        i = self.intent(1, IS_REQUOTE=True)
        r = V2.anti_churn([i], transitions_by_intent={"i1": {
            "TIER_1": False, "TIER_2": False, "TIER_3": False,
            "IDENTICAL_DECISION_STATE": True}})
        self.assertEqual(r["COUNTERS"]["REQUOTE_WITHOUT_MATERIAL_STATE_CHANGE"],
                         1)
        self.assertEqual(r["ANTI_CHURN_STATUS"], "CHURN_DETECTED")

    def test_an_order_created_only_by_polling_is_caught(self):
        i = self.intent(1, IS_REQUOTE=True)
        r = V2.anti_churn([i], transitions_by_intent={"i1": {
            "TIER_1": False, "TIER_2": False, "TIER_3": False,
            "IDENTICAL_DECISION_STATE": True}})
        self.assertEqual(r["COUNTERS"]["ORDERS_CREATED_ONLY_BY_POLL_FREQUENCY"],
                         1)

    def test_a_requote_behind_a_real_state_change_is_not_churn(self):
        i = self.intent(1, IS_REQUOTE=True)
        r = V2.anti_churn([i], transitions_by_intent={"i1": {
            "TIER_1": True, "TIER_2": True, "TIER_3": True,
            "IDENTICAL_DECISION_STATE": False}})
        self.assertTrue(r["ALL_ZERO"])

    def test_a_duplicate_economic_intent_is_caught(self):
        r = V2.anti_churn([self.intent(1), self.intent(2)])
        self.assertEqual(r["COUNTERS"]["DUPLICATE_ECONOMIC_INTENT"], 1)

    def test_a_declared_supersede_is_not_a_duplicate(self):
        r = V2.anti_churn([self.intent(1),
                           self.intent(2, SUPERSEDES="i1")])
        self.assertEqual(r["COUNTERS"]["DUPLICATE_ECONOMIC_INTENT"], 0)

    def test_the_economic_key_ignores_ids_and_retries(self):
        """Two intents differing only by id are the same economic act."""
        a = self.intent(1)
        b = self.intent(2, RETRY_COUNT=7, SUBMITTED_AT="later")
        self.assertEqual(V2._intent_key(a), V2._intent_key(b))

    def test_a_round_trip_without_positive_ev_is_caught(self):
        i = self.intent(1, ROUND_TRIP_CLOSED=True)
        r = V2.anti_churn([i], ev_by_intent={"i1": NOT_IDENTIFIED})
        self.assertEqual(
            r["COUNTERS"]["ROUND_TRIP_WITHOUT_POSITIVE_EXPECTED_EV"], 1)

    def test_an_unidentified_ev_round_trip_is_churn_not_a_pass(self):
        i = self.intent(1, ROUND_TRIP_CLOSED=True)
        r = V2.anti_churn([i], ev_by_intent={})
        self.assertEqual(
            r["COUNTERS"]["ROUND_TRIP_WITHOUT_POSITIVE_EXPECTED_EV"], 1)

    def test_all_four_counters_are_named(self):
        self.assertEqual(len(V2.CHURN_COUNTERS), 4)
        r = V2.anti_churn([])
        for c in V2.CHURN_COUNTERS:
            self.assertIn(c, r["COUNTERS"], c)

    def test_the_scope_of_a_clean_reading_is_declared(self):
        r = V2.anti_churn([])
        self.assertIn("no live engine has produced any", r["SCOPE"])


class ExecutionUnitsAreNotRiskUnits(unittest.TestCase):

    def book(self):
        """One contest with 16 totals lines plus a moneyline and a spread."""
        rows = [{"MARKET_SLUG": "t%d" % i, "EVENT_ID": "E1",
                 "MARKET_FAMILY": "SPORTS_MARKET_TYPE_TOTAL",
                 "ACTIVE_QUOTES": 1, "GROSS_NOTIONAL": "25",
                 "SIGNED_EXPOSURE": "25", "CAPITAL_OCCUPIED": "25"}
                for i in range(16)]
        rows += [{"MARKET_SLUG": "ml", "EVENT_ID": "E1",
                  "MARKET_FAMILY": "SPORTS_MARKET_TYPE_MONEYLINE",
                  "ACTIVE_QUOTES": 1, "GROSS_NOTIONAL": "25",
                  "SIGNED_EXPOSURE": "-25", "CAPITAL_OCCUPIED": "25"},
                 {"MARKET_SLUG": "sp", "EVENT_ID": "E2",
                  "MARKET_FAMILY": "SPORTS_MARKET_TYPE_SPREAD",
                  "ACTIVE_QUOTES": 1, "GROSS_NOTIONAL": "25",
                  "SIGNED_EXPOSURE": "25", "CAPITAL_OCCUPIED": "25"}]
        return rows

    def test_every_required_event_field_is_present(self):
        r = V2.event_concentration(self.book())
        e = r["EVENTS"]["E1"]
        for f in ("MARKETS_IN_EVENT", "ACTIVE_QUOTES_IN_EVENT",
                  "GROSS_NOTIONAL_IN_EVENT",
                  "NET_DIRECTIONAL_EXPOSURE_IN_EVENT",
                  "CORRELATED_EXPOSURE_IN_EVENT",
                  "CAPITAL_OCCUPIED_BY_EVENT"):
            self.assertIn(f, e, f)

    def test_seventeen_markets_on_one_contest_are_one_risk_unit(self):
        r = V2.event_concentration(self.book())
        self.assertEqual(r["EVENTS"]["E1"]["MARKETS_IN_EVENT"], 17)
        self.assertEqual(r["RISK_UNITS"], 2)
        self.assertEqual(r["EXECUTION_UNITS"], 18)

    def test_net_and_correlated_exposure_differ(self):
        """Net nets the hedge; correlated does not, and that is the point."""
        r = V2.event_concentration(self.book())
        e = r["EVENTS"]["E1"]
        self.assertEqual(D(e["NET_DIRECTIONAL_EXPOSURE_IN_EVENT"]), D("375"))
        self.assertEqual(D(e["CORRELATED_EXPOSURE_IN_EVENT"]), D("425"))

    def test_the_correlated_basis_is_conservative_and_declared(self):
        r = V2.event_concentration(self.book())
        self.assertEqual(r["CORRELATED_EXPOSURE_BASIS"],
                         "FULLY_CORRELATED_WITHIN_EVENT")
        self.assertEqual(r["CORRELATION_COEFFICIENT_STATUS"], NOT_IDENTIFIED)
        self.assertIn("over-states concentration", r["WHY_THAT_BASIS"])

    def test_the_most_concentrated_event_is_surfaced(self):
        r = V2.event_concentration(self.book())
        self.assertEqual(r["MOST_CONCENTRATED_EVENT"], "E1")
        self.assertEqual(r["MOST_CONCENTRATED_EVENT_MARKETS"], 17)

    def test_a_market_without_identity_is_bucketed_not_dropped(self):
        rows = [{"MARKET_SLUG": "x", "GROSS_NOTIONAL": "25"}]
        r = V2.event_concentration(rows)
        self.assertIn(NOT_IDENTIFIED, r["EVENTS"])


class TheEquationKeepsItsUnknownsVisible(unittest.TestCase):

    def test_all_five_terms_unknown_gives_not_identified(self):
        r = V2.volume_equation(None)
        self.assertEqual(r["EXPECTED_EXECUTED_NOTIONAL_PER_DAY"],
                         NOT_IDENTIFIED)
        self.assertFalse(r["RESULT_IS_IDENTIFIED"])

    def test_a_missing_term_is_named_not_defaulted(self):
        r = V2.volume_equation("1000", pct_passing_ev=None,
                               pct_passing_risk="0.5", p_fill="0.2",
                               average_filled_size="25")
        self.assertIn("PCT_PASSING_EV", r["MISSING_TERMS"])
        self.assertEqual(r["EXPECTED_EXECUTED_NOTIONAL_PER_DAY"],
                         NOT_IDENTIFIED)

    def test_an_unknown_is_never_treated_as_zero(self):
        """A zero would claim we measured none passing. We measured nothing."""
        r = V2.volume_equation("1000")
        self.assertNotEqual(r["EXPECTED_EXECUTED_NOTIONAL_PER_DAY"], "0")
        self.assertIn("not a zero and not a one", r["NEVER_COLLAPSE_UNKNOWNS"])

    def test_the_arrival_term_survives_alone(self):
        r = V2.volume_equation("1000")
        self.assertEqual(r["CANDIDATE_ORDER_INTENTS_PER_DAY"], "1000")

    def test_a_fully_supplied_scenario_computes(self):
        r = V2.volume_equation("10000", "0.10", "0.50", "0.20", "25",
                               capital_occupancy_hours="2")
        self.assertTrue(r["RESULT_IS_IDENTIFIED"])
        self.assertEqual(D(r["EXPECTED_FILLED_ORDERS_PER_DAY"]), D("100.000"))
        self.assertEqual(D(r["EXPECTED_EXECUTED_NOTIONAL_PER_DAY"]), D("2500"))
        self.assertEqual(D(r["EXPECTED_CAPITAL_TURNS_PER_DAY"]), D("12"))

    def test_even_a_computed_scenario_says_measured_terms_are_absent(self):
        r = V2.volume_equation("10000", "0.10", "0.50", "0.20", "25")
        self.assertEqual(r["MEASURED_BETTOR_P_FILL"], NOT_IDENTIFIED)
        self.assertEqual(r["MEASURED_PCT_PASSING_EV"], NOT_IDENTIFIED)
        self.assertEqual(r["LABEL"], V2.SCENARIO_LABEL)


class TheBacksolveIsCapacityPlanningNotAForecast(unittest.TestCase):

    def test_the_four_targets_are_covered(self):
        t = V2.backsolve_table()
        self.assertEqual(t["TARGETS"], ["5000000", "10000000", "20000000",
                                        "50000000"])

    def test_every_required_field_is_reported(self):
        r = V2.backsolve("20000000", "25", "0.20")
        for f in ("REQUIRED_DISTINCT_ORDER_INTENTS_PER_DAY",
                  "HYPOTHETICAL_P_FILL", "HYPOTHETICAL_CLIP_USD",
                  "REQUIRED_FILLED_ORDERS_PER_DAY",
                  "REQUIRED_GROSS_FILLED_NOTIONAL_PER_DAY",
                  "CAPITAL_REQUIRED", "CAPITAL_TURNS_PER_DAY"):
            self.assertIn(f, r, f)

    def test_the_arithmetic(self):
        r = V2.backsolve("20000000", "25", "0.20", capital_occupancy_hours="2")
        self.assertEqual(D(r["REQUIRED_GROSS_FILLED_NOTIONAL_PER_DAY"]),
                         D("666666.67"))
        self.assertEqual(D(r["REQUIRED_FILLED_ORDERS_PER_DAY"]), D("26667"))
        self.assertEqual(D(r["REQUIRED_DISTINCT_ORDER_INTENTS_PER_DAY"]),
                         D("133333"))
        self.assertEqual(D(r["CAPITAL_TURNS_PER_DAY"]), D("12"))

    def test_a_bigger_clip_needs_fewer_orders_for_the_same_volume(self):
        small = V2.backsolve("20000000", "25", "0.20")
        big = V2.backsolve("20000000", "250", "0.20")
        self.assertGreater(D(small["REQUIRED_FILLED_ORDERS_PER_DAY"]),
                           D(big["REQUIRED_FILLED_ORDERS_PER_DAY"]))
        # Capital is set by notional and holding time, not by clip size.
        self.assertEqual(D(small["CAPITAL_REQUIRED"]),
                         D(big["CAPITAL_REQUIRED"]))

    def test_the_opportunity_requirement_needs_ev_and_risk_rates(self):
        bare = V2.backsolve("20000000", "25", "0.20")
        self.assertEqual(bare["REQUIRED_OPPORTUNITIES_PER_DAY"],
                         NOT_IDENTIFIED)
        full = V2.backsolve("20000000", "25", "0.20", pct_passing_ev="0.10",
                            pct_passing_risk="0.50")
        self.assertNotEqual(full["REQUIRED_OPPORTUNITIES_PER_DAY"],
                            NOT_IDENTIFIED)

    def test_every_row_is_labelled_a_scenario(self):
        for r in V2.backsolve_table()["ROWS"]:
            self.assertEqual(r["LABEL"], "SCENARIO - NOT PREDICTED PERFORMANCE")
            self.assertIn("never admits an order", r["A_TARGET_IS_NOT_A_FORECAST"])

    def test_a_target_never_loosens_the_ev_gate(self):
        r = V2.backsolve("50000000", "25", "0.20")
        self.assertIn("fewer orders, not a lower bar",
                      r["VOLUME_TARGET_NEVER_LOOSENS_EV"])


class TheCriticalQuestionIsAnsweredFromEvidence(unittest.TestCase):

    def test_the_answer_is_not_identified(self):
        r = V2.critical_question(
            {"DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR": 20.88},
            addressable_markets=2204, measured_markets=6,
            addressable_measured=2)
        self.assertEqual(r["ANSWER"], NOT_IDENTIFIED)

    def test_the_permitted_answers_are_exactly_three(self):
        r = V2.critical_question()
        self.assertEqual(r["PERMITTED_ANSWERS"], ["YES", "NO", NOT_IDENTIFIED])

    def test_a_big_market_count_does_not_produce_a_yes(self):
        """The V1 inference, explicitly refused."""
        r = V2.critical_question(
            {"DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR": 1000.0},
            addressable_markets=100000, measured_markets=6,
            addressable_measured=2)
        self.assertEqual(r["ANSWER"], NOT_IDENTIFIED)
        self.assertIn("repeat the V1 defect",
                      r["ADDRESSABLE_COUNT_IS_NOT_AN_ANSWER"])

    def test_the_blockers_name_the_small_sample(self):
        r = V2.critical_question(
            {"DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR": 20.88},
            addressable_markets=2204, measured_markets=6,
            addressable_measured=2)
        self.assertIn("ARRIVAL_MEASURED_ON_TOO_FEW_ADDRESSABLE_MARKETS",
                      r["BLOCKERS"])
        self.assertIn("PCT_PASSING_EV_NOT_IDENTIFIED", r["BLOCKERS"])
        self.assertIn("P_FILL_NOT_IDENTIFIED", r["BLOCKERS"])

    def test_both_directions_are_argued(self):
        r = V2.critical_question()
        self.assertIn("assumption", r["WHY_NOT_YES"])
        self.assertIn("as unevidenced as YES", r["WHY_NOT_NO"])

    def test_what_would_settle_it_is_actionable(self):
        r = V2.critical_question()
        self.assertGreaterEqual(len(r["WHAT_WOULD_SETTLE_IT"]), 4)


class TheFairValueGapIsDocumentedNotSolved(unittest.TestCase):

    def test_the_status_is_not_identified(self):
        r = V2.fair_value_gap()
        self.assertEqual(r["FAIR_VALUE_CURRENT_STATUS"], NOT_IDENTIFIED)

    def test_all_five_required_fields_are_present(self):
        r = V2.fair_value_gap()
        for f in ("FAIR_VALUE_CURRENT_STATUS", "FAIR_VALUE_MISSING_INPUTS",
                  "FAIR_VALUE_DATA_ALREADY_AVAILABLE",
                  "FAIR_VALUE_DATA_NOT_YET_AVAILABLE",
                  "SHORTEST_PATH_TO_FIRST_PROSPECTIVE_FAIR_VALUE"):
            self.assertIn(f, r, f)

    def test_available_and_missing_data_are_separated(self):
        r = V2.fair_value_gap()
        self.assertTrue(r["FAIR_VALUE_DATA_ALREADY_AVAILABLE"])
        self.assertTrue(r["FAIR_VALUE_DATA_NOT_YET_AVAILABLE"])
        joined = " ".join(r["FAIR_VALUE_DATA_ALREADY_AVAILABLE"])
        self.assertNotIn("independent outcome model", joined)

    def test_the_path_freezes_the_rule_before_seeing_outcomes(self):
        r = V2.fair_value_gap()
        path = " ".join(r["SHORTEST_PATH_TO_FIRST_PROSPECTIVE_FAIR_VALUE"])
        self.assertIn("BEFORE seeing outcomes", path)
        self.assertIn("out of sample", path)

    def test_no_model_was_invented_to_clear_the_field(self):
        r = V2.fair_value_gap()
        self.assertIn("would make every downstream", r["DO_NOT_INVENT_A_MODEL"])
        src = Path(V2.__file__).read_text().lower()
        for bad in ("def predict", "def fair_value(", "logistic",
                    "def score(", "sklearn", "numpy"):
            self.assertNotIn(bad, src, bad)
        # Whole words only -- "elo" is a substring of "below" and "developed".
        self.assertIsNone(re.search(r"\belo\b", src))

    def test_calendar_time_is_not_guessed(self):
        r = V2.fair_value_gap()
        self.assertEqual(r["ESTIMATED_CALENDAR_TIME"], NOT_IDENTIFIED)


class ItPlacesNoOrderAndContactsNothing(unittest.TestCase):

    def test_no_network_and_no_submit(self):
        src = Path(V2.__file__).read_text().lower()
        for bad in ("httpx", "requests", "submit", "place_order", "https://"):
            self.assertNotIn(bad, src, bad)


if __name__ == "__main__":
    unittest.main()
