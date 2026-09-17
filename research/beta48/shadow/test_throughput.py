#!/usr/bin/env python3
"""THROUGHPUT_V1: can volume grow without the EV standard falling?

THE FAILURE MODE THESE TESTS GUARD AGAINST. A throughput diagnostic is the
easiest place in a trading system to smuggle in a lower bar. Three moves do it,
and all three are tested for here:

  1. counting a positive-EV opportunity when no EV exists -- a zero would claim
     we looked and found none; the honest answer is NOT_IDENTIFIED
  2. letting a fast-recycling opportunity rank its way into admissibility
  3. quietly treating a scenario fill probability as a measured one

The fourth is quieter and is also tested: calling a quote update a trade, or an
order intent a fill, which inflates every volume number at once.

Nothing here contacts a venue.
"""
import inspect
import unittest
from pathlib import Path

import throughput_v1 as T

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOW = "2026-09-17T12:00:00+00:00"


def contest(slug, start, tids, cls="SPORTS_MARKET_TYPE_SPREAD", league="cfb"):
    return {"slug": slug, "gameStartTime": start, "sportsMarketTypeV2": cls,
            "marketSides": [{"teamId": t, "team": {"id": t, "league": league}}
                            for t in tids]}


def future(slug, start, tid=900):
    return {"slug": slug, "gameStartTime": start,
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_FUTURE",
            "marketSides": [{"teamId": tid, "team": {"id": tid,
                                                     "league": "mlb"}}]}


def book():
    return {"bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.52", "size": "100"}]}


class TheFunnelStopsWhereTheEvidenceStops(unittest.TestCase):

    def board(self):
        rows = [contest("a1", "2026-09-17T12:30:00+00:00", [1, 2]),
                contest("a2", "2026-09-17T12:30:00+00:00", [1, 2]),
                contest("b1", "2026-09-17T13:00:00+00:00", [3, 4]),
                future("f1", "2027-06-01T00:00:00+00:00")]
        books = {"a1": book(), "a2": book(), "b1": book()}
        act = {s: {"HIGH_ACTIVITY_AT_DECISION": True}
               for s in ("a1", "a2", "b1")}
        return rows, books, act

    def test_every_declared_stage_is_present(self):
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        for s in T.FUNNEL_STAGES:
            self.assertIn(s, f, s)

    def test_the_countable_stages_are_counted(self):
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        self.assertEqual(f["BOARD_MARKETS"], 4)
        self.assertEqual(f["CANONICAL_EVENTS"], 2)
        self.assertEqual(f["IDENTITY_ELIGIBLE_MARKETS"], 3)
        self.assertEqual(f["ACTIVE_TRADABLE_MARKETS"], 3)
        self.assertEqual(f["BOOKS_OBSERVED"], 3)

    def test_positive_ev_counts_are_absent_not_zero(self):
        """A zero would claim we looked and found none. We cannot look."""
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        self.assertEqual(f["POSITIVE_EV_MAKER_CANDIDATES"], NOT_IDENTIFIED)
        self.assertEqual(f["POSITIVE_EV_TAKER_CANDIDATES"], NOT_IDENTIFIED)
        self.assertNotEqual(f["POSITIVE_EV_MAKER_CANDIDATES"], 0)
        self.assertIn("FAIR_VALUE", f["WHY_BLOCKED"])

    def test_a_futures_row_is_not_a_tradable_contest(self):
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        self.assertEqual(f["IDENTITY_ELIGIBLE_MARKETS"], 3)   # f1 excluded

    def test_a_zero_risk_admissible_count_is_explained_as_authorization(self):
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        self.assertEqual(f["RISK_ADMISSIBLE_CANDIDATES"], 0)
        self.assertIn("NOT_SET", f["WHY_RISK_ADMISSIBLE_MAY_BE_ZERO"])

    def test_zero_would_take_is_a_scope_statement_not_a_measurement(self):
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        self.assertEqual(f["WOULD_TAKE"], 0)
        self.assertIn("passive-maker only", f["WHY_WOULD_TAKE_IS_ZERO"])

    def test_the_funnel_creates_no_trade(self):
        rows, books, act = self.board()
        f = T.funnel(rows, books, act, NOW)
        self.assertEqual(f["WOULD_QUOTE"], 0)
        self.assertIn("A_TRADING_STRATEGY", f["THIS_IS_NOT"])


class TheFourCategoriesStayApart(unittest.TestCase):

    def test_every_rate_field_exists(self):
        r = T.rates(600, decisions=10)
        for k in T.RATE_FIELDS:
            self.assertIn(k, r, k)

    def test_actual_fills_cannot_be_set_by_any_caller(self):
        """There is deliberately no argument for it."""
        self.assertNotIn("actual_fills", inspect.signature(T.rates).parameters)
        r = T.rates(600, decisions=10)
        self.assertEqual(r["ACTUAL_FILLS_PER_MINUTE"], NOT_IDENTIFIED)

    def test_expected_fills_is_absent_unless_a_scenario_supplies_it(self):
        self.assertEqual(T.rates(600)["EXPECTED_FILLS_PER_MINUTE"],
                         NOT_IDENTIFIED)
        r = T.rates(600, expected_fills=3)
        self.assertAlmostEqual(r["EXPECTED_FILLS_PER_MINUTE"], 0.3, places=9)
        self.assertTrue(r["EXPECTED_FILLS_IS_SCENARIO_ONLY"])

    def test_a_quote_update_is_not_a_trade(self):
        r = T.rates(600)
        self.assertIn("moves no contracts", r["A_QUOTE_UPDATE_IS_NOT_A_TRADE"])
        self.assertIn("decision to try", r["AN_INTENT_IS_NOT_A_FILL"])
        self.assertEqual(list(r["THE_FOUR_ARE_DISTINCT"]),
                         ["DECISION", "ORDER_INTENT", "FILL", "TRADE"])

    def test_rates_are_arithmetic_not_invention(self):
        r = T.rates(3600, decisions=60, quote_opportunities=120)
        self.assertAlmostEqual(r["DECISIONS_PER_MINUTE"], 1.0, places=9)
        self.assertAlmostEqual(r["QUOTE_OPPORTUNITIES_PER_MINUTE"], 2.0,
                               places=9)

    def test_a_zero_window_yields_absent_rates_not_infinite_ones(self):
        r = T.rates(0, decisions=10)
        self.assertEqual(r["DECISIONS_PER_MINUTE"], NOT_IDENTIFIED)


class TheCapitalMetric(unittest.TestCase):

    def test_it_computes_when_every_input_is_present(self):
        m = T.capital_metric("2.00", "100.00", "1800", ev_admissible=True)
        self.assertEqual(str(m["EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR"]),
                         "0.02")
        # 0.02 per dollar over half an hour -> 0.04 per dollar per hour.
        self.assertAlmostEqual(
            float(m["EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR"]),
            0.04, places=9)

    def test_a_missing_input_leaves_the_ratio_absent(self):
        m = T.capital_metric(NOT_IDENTIFIED, "100.00", "1800",
                             ev_admissible=True)
        self.assertEqual(m["EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR"],
                         NOT_IDENTIFIED)
        self.assertIn("EXPECTED_NET_DOLLARS", m["MISSING_INPUTS"])

    def test_a_fast_recycle_cannot_make_an_inadmissible_trade_admissible(self):
        """The whole point of section 3's guardrail."""
        fast = T.capital_metric("5.00", "10.00", "60", ev_admissible=False)
        self.assertFalse(fast["ADMISSIBLE"])
        self.assertFalse(fast["RANK_ELIGIBLE"])
        self.assertTrue(fast["TURNOVER_CANNOT_RESCUE_NEGATIVE_EV"])

    def test_an_unidentified_ev_is_not_admissible_either(self):
        m = T.capital_metric("5.00", "10.00", "60", ev_admissible=None)
        self.assertEqual(m["EV_ADMISSIBLE"], NOT_IDENTIFIED)
        self.assertFalse(m["ADMISSIBLE"])

    def test_the_ranking_keeps_inadmissible_rows_in_a_separate_list(self):
        good = T.capital_metric("1.00", "100.00", "3600", ev_admissible=True)
        fast_bad = T.capital_metric("9.00", "10.00", "60", ev_admissible=False)
        r = T.rank_opportunities([fast_bad, good])
        self.assertEqual(r["RANKED_COUNT"], 1)
        self.assertEqual(r["NOT_RANKED_COUNT"], 1)
        self.assertIs(r["RANKED"][0], good)

    def test_a_small_fast_opportunity_may_outrank_a_large_slow_one(self):
        """Permitted -- both have already cleared the EV bar."""
        small_fast = T.capital_metric("1.00", "50.00", "600",
                                      ev_admissible=True)
        large_slow = T.capital_metric("10.00", "5000.00", "86400",
                                      ev_admissible=True)
        r = T.rank_opportunities([large_slow, small_fast])
        self.assertIs(r["RANKED"][0], small_fast)


class TheScenarioTables(unittest.TestCase):

    def table(self):
        return T.scenario_table("100", ["10", "25"], occupancy_hours="2")

    def test_every_cell_is_labelled_a_scenario(self):
        t = self.table()
        self.assertEqual(t["LABEL"], "SCENARIO - NOT MEASURED BETTOR PERFORMANCE")
        for c in t["CELLS"]:
            self.assertEqual(c["LABEL"],
                             "SCENARIO - NOT MEASURED BETTOR PERFORMANCE")

    def test_the_grid_covers_the_declared_probabilities(self):
        t = self.table()
        self.assertEqual(list(t["P_FILL_GRID"]),
                         ["0.05", "0.10", "0.20", "0.30", "0.40", "0.50"])
        self.assertEqual(t["CELL_COUNT"], 12)          # 2 clips x 6 rates

    def test_the_arithmetic_is_what_it_claims(self):
        t = self.table()
        c = [x for x in t["CELLS"]
             if x["HYPOTHETICAL_P_FILL"] == "0.20"
             and x["HYPOTHETICAL_CLIP_USD"] == "10"][0]
        self.assertEqual(c["FILLS_PER_DAY"], "20.00")
        self.assertEqual(c["GROSS_FILLED_NOTIONAL_PER_DAY"], "200.00")

    def test_bettor_p_fill_stays_unidentified_beside_the_grid(self):
        t = self.table()
        self.assertEqual(t["BETTOR_P_FILL"], NOT_IDENTIFIED)
        self.assertEqual(t["ACTUAL_FILLS_PER_DAY"], NOT_IDENTIFIED)

    def test_the_whale_completion_rate_is_named_and_forbidden(self):
        t = self.table()
        self.assertIn("another venue", t["WHALE_P_FILL_IS_FORBIDDEN"])

    def test_the_scenarios_declare_they_never_reach_the_live_engine(self):
        t = self.table()
        self.assertIn("never", t["SCENARIOS_NEVER_REACH_THE_LIVE_ENGINE"])

    def test_the_module_does_not_import_the_live_ev_engine(self):
        src = Path(T.__file__).read_text()
        self.assertNotIn("import ev_semantics", src)
        self.assertNotIn("import micro_live_ev", src)


class DensityByClass(unittest.TestCase):

    def rows(self):
        return [contest("s1", "2026-09-17T12:30:00+00:00", [1, 2]),
                contest("s2", "2026-09-17T12:30:00+00:00", [1, 2]),
                contest("m1", "2026-09-17T18:00:00+00:00", [3, 4],
                        cls="SPORTS_MARKET_TYPE_MONEYLINE", league="nfl"),
                future("f1", "2027-06-01T00:00:00+00:00")]

    def test_the_three_axes_are_reported(self):
        d = T.density_by_class(self.rows(), NOW)
        for axis in ("BY_MARKET_CLASS", "BY_LEAGUE", "BY_TIME_TO_KICKOFF"):
            self.assertIn(axis, d)

    def test_classes_come_from_the_venue_not_from_us(self):
        d = T.density_by_class(self.rows(), NOW)
        self.assertIn("SPORTS_MARKET_TYPE_SPREAD", d["BY_MARKET_CLASS"])
        self.assertIn("SPORTS_MARKET_TYPE_FUTURE", d["BY_MARKET_CLASS"])
        self.assertTrue(d["NO_NEW_TAXONOMY_INVENTED"])

    def test_identity_share_is_reported_per_class(self):
        d = T.density_by_class(self.rows(), NOW)
        spread = d["BY_MARKET_CLASS"]["SPORTS_MARKET_TYPE_SPREAD"]
        self.assertEqual(spread["IDENTITY_SHARE"], 1.0)
        fut = d["BY_MARKET_CLASS"]["SPORTS_MARKET_TYPE_FUTURE"]
        self.assertEqual(fut["IDENTITY_SHARE"], 0.0)

    def test_time_buckets_separate_live_from_distant(self):
        d = T.density_by_class(self.rows(), NOW)
        self.assertIn("WITHIN_1H", d["BY_TIME_TO_KICKOFF"])
        self.assertIn("BEYOND_72H", d["BY_TIME_TO_KICKOFF"])

    def test_the_truncated_instruction_is_declared_on_the_output(self):
        """Honest about how this section was specified."""
        d = T.density_by_class(self.rows(), NOW)
        self.assertTrue(d["SECTION_5_READ_FROM_A_TRUNCATED_INSTRUCTION"])


class ItCannotLowerTheStandard(unittest.TestCase):

    def test_the_module_places_no_order_and_contacts_nothing(self):
        src = Path(T.__file__).read_text()
        for bad in ("httpx", "requests", "submit", "place_order",
                    "polymarket", "https://"):
            self.assertNotIn(bad, src.lower(), bad)

    def test_density_is_never_described_as_edge(self):
        f = T.funnel([], {}, {}, NOW)
        self.assertIn("is not how often looking pays",
                      f["DENSITY_IS_NOT_EDGE"])

    def test_it_declares_itself_a_diagnostic(self):
        self.assertEqual(T.THIS_IS,
                         "OPPORTUNITY_DENSITY_AND_CAPACITY_DIAGNOSTIC")
        self.assertIn("AN_EV_ADMISSION_STANDARD", T.THIS_IS_NOT)


def uteam(tid, abbr):
    return {"teamId": tid, "team": {"id": tid, "abbreviation": abbr,
                                    "league": "nfl"}}


def ucontest(slug, start, a, b, cls="SPORTS_MARKET_TYPE_MONEYLINE"):
    return {"slug": slug, "id": slug, "gameStartTime": start,
            "sportsMarketTypeV2": cls,
            "marketSides": [uteam(*a), uteam(*b)]}


def utotal(slug, start):
    return {"slug": slug, "id": slug, "gameStartTime": start,
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL", "line": "44.5",
            "marketSides": [{"id": slug + "-o", "description": "Over"},
                            {"id": slug + "-u", "description": "Under"}]}


U_START = "2026-09-20T17:00:00Z"


class TheUniverseCountsTotalsWithoutDoubleCounting(unittest.TestCase):
    """Section 4. Totals join the addressable set; nothing is counted twice."""

    def board(self):
        return [
            ucontest("aec-nfl-det-buf", U_START, (11, "det"), (12, "buf")),
            ucontest("asc-nfl-det-buf-neg3", U_START, (11, "det"), (12, "buf"),
                     cls="SPORTS_MARKET_TYPE_SPREAD"),
            ucontest("asc-nfl-det-buf-neg7", U_START, (11, "det"), (12, "buf"),
                     cls="SPORTS_MARKET_TYPE_SPREAD"),
            utotal("tsc-nfl-det-buf-44pt5", U_START),
            # A second contest with a moneyline and a total but NO spread.
            ucontest("aec-nfl-kc-lv", U_START, (13, "kc"), (14, "lv")),
            utotal("tsc-nfl-kc-lv-47pt5", U_START),
            # A total whose contest the venue does not list at all.
            utotal("tsc-nfl-xxx-yyy-30pt5", U_START),
            future("f1", "2027-06-01T00:00:00+00:00"),
        ]

    def test_the_three_families_are_counted_separately(self):
        u = T.market_universe(self.board())
        self.assertEqual(u["MONEYLINE_MARKETS"], 2)
        self.assertEqual(u["SPREAD_MARKETS"], 2)
        self.assertEqual(u["BOUND_TOTAL_MARKETS"], 2)
        self.assertEqual(u["UNBOUND_TOTAL_MARKETS"], 1)

    def test_the_addressable_total_is_the_sum_of_disjoint_families(self):
        u = T.market_universe(self.board())
        self.assertEqual(u["TOTAL_CANONICALLY_ADDRESSABLE_SPORTS_MARKETS"],
                         u["MONEYLINE_MARKETS"] + u["SPREAD_MARKETS"]
                         + u["BOUND_TOTAL_MARKETS"])
        self.assertEqual(u["MARKETS_COUNTED_IN_TWO_FAMILIES"], 0)
        self.assertTrue(u["NO_DOUBLE_COUNTING"])

    def test_an_unbound_total_is_not_addressable(self):
        u = T.market_universe(self.board())
        self.assertEqual(u["TOTALS_MARKETS_ON_BOARD"], 3)
        self.assertNotIn("tsc-nfl-xxx-yyy-30pt5", str(u["EVENTS_WITH_TOTAL"]))
        self.assertEqual(u["BOUND_TOTAL_MARKETS"], 2)

    def test_event_coverage_per_family(self):
        u = T.market_universe(self.board())
        self.assertEqual(u["CANONICAL_EVENTS"], 2)
        self.assertEqual(u["EVENTS_WITH_MONEYLINE"], 2)
        self.assertEqual(u["EVENTS_WITH_SPREAD"], 1)
        self.assertEqual(u["EVENTS_WITH_TOTAL"], 2)
        self.assertEqual(u["EVENTS_WITH_ALL_THREE"], 1)

    def test_adding_totals_creates_no_new_canonical_event(self):
        """The architectural condition, seen from the universe's side."""
        b = self.board()
        without = T.market_universe([m for m in b
                                     if m["sportsMarketTypeV2"]
                                     != "SPORTS_MARKET_TYPE_TOTAL"])
        with_totals = T.market_universe(b)
        self.assertEqual(with_totals["CANONICAL_EVENTS"],
                         without["CANONICAL_EVENTS"])
        self.assertEqual(with_totals["TOTALS_ONLY_EVENTS"], 0)
        self.assertTrue(
            with_totals["EVERY_TOTAL_ATTACHED_TO_A_PREEXISTING_EVENT"])

    def test_futures_stay_outside_the_addressable_set(self):
        u = T.market_universe(self.board())
        self.assertEqual(u["NOT_ADDRESSABLE_MARKETS"],
                         u["BOARD_MARKETS"]
                         - u["TOTAL_CANONICALLY_ADDRESSABLE_SPORTS_MARKETS"])
        self.assertIn("no two-team venue binding", u["WHY_NOT_ADDRESSABLE"])

    def test_a_wider_universe_is_not_a_looser_standard(self):
        u = T.market_universe(self.board())
        self.assertIn("not because an", u["WIDER_IS_NOT_LOOSER"])
        self.assertIn("passes BETTOR EV and risk",
                      u["ADDING_TOTALS_IS_NOT_PERMISSION"])


class TheThreeTurnoverConceptsAreKeptApart(unittest.TestCase):
    """Section 5. The correction: P_FILL moves B, not A, and cancels in C."""

    def model(self, p):
        return T.turnover_model(opportunity_arrival_per_day="1000",
                                p_fill=p, average_filled_notional="25",
                                capital_occupancy_hours="2")

    def test_opportunity_throughput_does_not_move_with_p_fill(self):
        a, b = self.model("0.10"), self.model("0.50")
        self.assertEqual(a["CANDIDATE_ORDER_INTENTS_PER_DAY"],
                         b["CANDIDATE_ORDER_INTENTS_PER_DAY"])

    def test_executed_turnover_scales_directly_with_p_fill(self):
        """The sentence that was wrong, now pinned in the other direction."""
        a, b = self.model("0.10"), self.model("0.50")
        self.assertEqual(float(a["EXPECTED_FILLED_ORDERS_PER_DAY"]), 100.0)
        self.assertEqual(float(b["EXPECTED_FILLED_ORDERS_PER_DAY"]), 500.0)
        self.assertEqual(
            float(b["EXPECTED_GROSS_FILLED_NOTIONAL_PER_DAY"]),
            5 * float(a["EXPECTED_GROSS_FILLED_NOTIONAL_PER_DAY"]))
        self.assertTrue(T.P_FILL_RAISES_EXECUTED_TURNOVER)

    def test_capital_velocity_is_set_by_holding_time(self):
        a, b = self.model("0.10"), self.model("0.50")
        self.assertEqual(float(a["EXPECTED_CAPITAL_TURNS_PER_DAY"]),
                         float(b["EXPECTED_CAPITAL_TURNS_PER_DAY"]))
        self.assertAlmostEqual(
            float(a["EXPECTED_CAPITAL_TURNS_PER_DAY"]), 12.0, places=6)
        slow = T.turnover_model("1000", "0.10", "25", "6")
        self.assertAlmostEqual(
            float(slow["EXPECTED_CAPITAL_TURNS_PER_DAY"]), 4.0, places=6)

    def test_capital_occupied_does_move_with_p_fill(self):
        """Both numerator and denominator rise -- which is why turns cancel."""
        a, b = self.model("0.10"), self.model("0.50")
        self.assertEqual(float(b["AVERAGE_CAPITAL_OCCUPIED"]),
                         5 * float(a["AVERAGE_CAPITAL_OCCUPIED"]))

    def test_the_correction_is_recorded_rather_than_quietly_fixed(self):
        m = self.model("0.20")
        self.assertIn("true ONLY of", m["THE_CORRECTED_STATEMENT"])
        self.assertIn("cancels from the RATIO only",
                      m["WHY_TURNS_DO_NOT_MOVE_WITH_P_FILL"])

    def test_the_four_inputs_are_modelled_separately(self):
        sig = inspect.signature(T.turnover_model).parameters
        for p in ("opportunity_arrival_per_day", "p_fill",
                  "average_filled_notional", "capital_occupancy_hours"):
            self.assertIn(p, sig, p)

    def test_the_seven_required_scenario_fields_are_emitted(self):
        m = self.model("0.20")
        for f in T.TURNOVER_SCENARIO_FIELDS:
            self.assertIn(f, m, f)
        self.assertEqual(len(T.TURNOVER_SCENARIO_FIELDS), 7)

    def test_peak_capital_is_absent_not_guessed(self):
        m = self.model("0.20")
        self.assertEqual(m["PEAK_CAPITAL_OCCUPIED"], NOT_IDENTIFIED)
        self.assertTrue(m["PEAK_IS_AN_UPPER_BOUND_NOT_AN_ESTIMATE"])
        self.assertNotEqual(m["PEAK_CAPITAL_OCCUPIED_UPPER_BOUND"],
                            NOT_IDENTIFIED)
        # The bound really is a bound.
        self.assertGreater(float(m["PEAK_CAPITAL_OCCUPIED_UPPER_BOUND"]),
                           float(m["AVERAGE_CAPITAL_OCCUPIED"]))

    def test_net_ev_per_capital_dollar_is_absent_without_an_ev(self):
        m = self.model("0.20")
        self.assertEqual(m["EXPECTED_NET_EV_PER_CAPITAL_DOLLAR_PER_DAY"],
                         NOT_IDENTIFIED)
        self.assertEqual(m["MEASURED_BETTOR_P_FILL"], NOT_IDENTIFIED)

    def test_every_scenario_carries_the_label(self):
        g = T.turnover_scenarios("1000", "25", "2")
        self.assertEqual(g["LABEL"], T.SCENARIO_LABEL)
        self.assertIn("NOT MEASURED BETTOR PERFORMANCE", g["LABEL"])
        for s in g["SCENARIOS"]:
            self.assertEqual(s["LABEL"], T.SCENARIO_LABEL)
            self.assertEqual(s["MEASURED_BETTOR_P_FILL"], NOT_IDENTIFIED)

    def test_the_whale_fill_rate_is_still_forbidden(self):
        m = self.model("0.20")
        self.assertIn("is not a BETTOR fill probability",
                      m["WHALE_P_FILL_IS_FORBIDDEN"])

    def test_the_three_concepts_are_named_on_every_scenario(self):
        m = self.model("0.20")
        names = [c[0] for c in m["CONCEPTS"]]
        self.assertEqual(names, ["A_OPPORTUNITY_THROUGHPUT",
                                 "B_EXECUTED_TURNOVER", "C_CAPITAL_VELOCITY"])


class TheObjectiveCarriesItsConstraint(unittest.TestCase):
    """Section 6. High throughput SUBJECT TO positive net EV."""

    def test_the_objective_is_stated(self):
        self.assertEqual(T.OBJECTIVE,
                         "HIGH_THROUGHPUT_SUBJECT_TO_POSITIVE_NET_EV")
        self.assertEqual(len(T.OBJECTIVE_SEEKS), 4)

    def test_throughput_cannot_admit_a_negative_ev_order(self):
        r = T.admissible_under_objective(False, throughput_gain="1000x")
        self.assertFalse(r["ADMISSIBLE"])
        self.assertTrue(r["THROUGHPUT_GAIN_WAS_IGNORED"])

    def test_throughput_cannot_admit_an_unidentified_ev_order(self):
        for verdict in (None, NOT_IDENTIFIED, "NOT_IDENTIFIED", "", 0):
            r = T.admissible_under_objective(verdict, throughput_gain="1000x")
            self.assertFalse(r["ADMISSIBLE"], repr(verdict))

    def test_a_positive_ev_order_is_admitted_on_its_ev_alone(self):
        r = T.admissible_under_objective("POSITIVE_EV")
        self.assertTrue(r["ADMISSIBLE"])
        self.assertEqual(r["THROUGHPUT_GAIN_OFFERED"], NOT_IDENTIFIED)
        self.assertIn("positive EV alone", r["WHY"])

    def test_the_objective_is_not_scored_on_its_measurable_part_alone(self):
        u = T.market_universe([])
        s = T.objective_status(u)
        self.assertEqual(s["OBJECTIVE_ACHIEVED"], NOT_IDENTIFIED)
        self.assertEqual(s["MANY_INDEPENDENT_POSITIVE_EV_OPPORTUNITIES"],
                         NOT_IDENTIFIED)
        self.assertEqual(s["MEASURED_BETTOR_P_FILL"], NOT_IDENTIFIED)

    def test_independence_is_part_of_the_objective(self):
        s = T.objective_status()
        self.assertIn("correlated legs on one contest",
                      s["INDEPENDENCE_IS_PART_OF_THE_OBJECTIVE"]
                      .replace("\n", " "))

    def test_the_three_statuses_are_reported(self):
        s = T.objective_status(T.market_universe([]))
        self.assertEqual(s["EXECUTED_TURNOVER_STATUS"],
                         "SCENARIO_ONLY_NO_BETTOR_FILL_EVIDENCE")
        self.assertEqual(s["CAPITAL_VELOCITY_STATUS"],
                         "SCENARIO_ONLY_NO_BETTOR_HOLDING_TIME_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
