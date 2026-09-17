#!/usr/bin/env python3
"""BETTOR_EV_CORE_V1: the ways a fair-value engine lies to its owner.

THE FOUR FAILURE MODES THESE TESTS EXIST FOR.

  1. LEAKAGE. A model that has seen the answer looks brilliant and is worthless.
     Two forms matter here: a timestamp at or after settlement, and a fixture
     split across train and test so the model reads the answer off a sibling
     contract on the same game.
  2. AN INCOHERENT SURFACE. Deriving contracts independently lets P(over 2.5)
     come out below P(over 3.5), which is not a mispricing but an impossibility.
  3. FILLING IN A BLANK. Reporting a fill probability, an execution fair value
     or an action EV that nothing measured -- the receipt then lies and the
     reader cannot tell.
  4. SELECTING ON NOISE. With 112,535 rows, a subgroup whose interval excludes
     zero is easy to find. The scoring layer must make the honest comparison
     natural and the flattering one hard.

Nothing here contacts a venue.
"""
import math
import unittest
from pathlib import Path

import ev_core as EV
import ev_core_calibration as CAL
import ev_core_data as D
import ev_core_event_model as EM
import ev_core_models as M
import ev_core_outcomes as OC
import ev_core_walkforward as WF

NOT_IDENTIFIED = "NOT_IDENTIFIED"


def settle(slug, winner, sport="Soccer", resolved_at="2026-09-01T20:00:00Z",
           outcomes=("Yes", "No")):
    return {"market_slug": slug, "sport": sport, "resolved": True,
            "resolved_at": resolved_at,
            "tokens": [{"outcome": o} for o in outcomes],
            "payouts": ["1.0" if o == winner else "0.0" for o in outcomes]}


class OutcomeReconstructionRecoversTheEvent(unittest.TestCase):

    def test_a_totals_ladder_pins_the_total(self):
        recs = [settle("epl-aaa-bbb-2026-09-01-total-1pt5", "Over",
                       outcomes=("Over", "Under")),
                settle("epl-aaa-bbb-2026-09-01-total-2pt5", "Under",
                       outcomes=("Over", "Under"))]
        r = OC.reconstruct(recs)
        self.assertEqual(r["TOTAL_GOALS"], 2)

    def test_an_exact_score_yes_gives_the_score(self):
        recs = [settle("epl-aaa-bbb-2026-09-01-exact-score-2-1", "Yes")]
        r = OC.reconstruct(recs)
        self.assertEqual(r["RECONSTRUCTION_STATUS"], "UNIQUE")
        self.assertEqual((r["HOME_GOALS"], r["AWAY_GOALS"]), (2, 1))

    def test_a_halftime_draw_is_not_a_full_time_draw(self):
        """The bug that produced 29 false contradictions on real data.

        `-halftime-result-draw` also ends in `-draw`. If the segment pattern is
        not tested first, one fixture yields DRAW_YES and DRAW_NO at once.
        """
        recs = [settle("epl-aaa-bbb-2026-09-01-draw", "No"),
                settle("epl-aaa-bbb-2026-09-01-halftime-result-draw", "Yes"),
                settle("epl-aaa-bbb-2026-09-01-exact-score-0-2", "Yes")]
        r = OC.reconstruct(recs)
        self.assertNotEqual(r["RECONSTRUCTION_STATUS"], "CONTRADICTORY")
        self.assertEqual((r["HOME_GOALS"], r["AWAY_GOALS"]), (0, 2))

    def test_a_first_half_total_is_not_a_full_game_total(self):
        recs = [settle("epl-aaa-bbb-2026-09-01-first-half-total-2pt5", "Under",
                       outcomes=("Over", "Under")),
                settle("epl-aaa-bbb-2026-09-01-total-2pt5", "Over",
                       outcomes=("Over", "Under"))]
        r = OC.reconstruct(recs)
        self.assertNotEqual(r["RECONSTRUCTION_STATUS"], "CONTRADICTORY")

    def test_an_impossible_set_is_reported_not_averaged(self):
        recs = [settle("epl-aaa-bbb-2026-09-01-exact-score-1-0", "Yes"),
                settle("epl-aaa-bbb-2026-09-01-draw", "Yes")]
        r = OC.reconstruct(recs)
        self.assertEqual(r["RECONSTRUCTION_STATUS"], "CONTRADICTORY")

    def test_an_ambiguous_set_refuses_to_pick(self):
        recs = [settle("epl-aaa-bbb-2026-09-01-total-0pt5", "Over",
                       outcomes=("Over", "Under"))]
        r = OC.reconstruct(recs)
        self.assertEqual(r["RECONSTRUCTION_STATUS"], "UNDERDETERMINED")
        self.assertEqual(r["HOME_GOALS"], NOT_IDENTIFIED)

    def test_the_label_is_declared_unavailable_at_decision_time(self):
        r = OC.reconstruct([settle("epl-aaa-bbb-2026-09-01-draw", "Yes")])
        self.assertIn("never a feature", r["NOT_AVAILABLE_AT_DECISION_TIME"])


class TheAsOfGateRefusesTheFuture(unittest.TestCase):

    def test_an_observation_after_settlement_is_illegal(self):
        self.assertIn("TIME_TO_SETTLEMENT_S", D.ILLEGAL_AS_FEATURES)
        self.assertIn("SETTLED_YES", D.ILLEGAL_AS_FEATURES)

    def test_every_feature_declares_its_availability(self):
        for name, meta in D.FEATURES.items():
            self.assertIn("SOURCE", meta, name)
            self.assertIn("AVAILABLE_AT_DECISION_TIME", meta, name)

    def test_time_to_settlement_is_marked_as_using_the_future(self):
        self.assertFalse(
            D.FEATURES["TIME_TO_SETTLEMENT_S"]["AVAILABLE_AT_DECISION_TIME"])
        self.assertIn("USES THE FUTURE", D.FEATURES["TIME_TO_SETTLEMENT_S"]["NOTE"])

    def test_segment_families_are_not_read_as_full_game(self):
        self.assertEqual(D.market_family("x-first-half-total-2pt5"),
                         "FIRST_HALF_TOTAL")
        self.assertEqual(D.market_family("x-total-2pt5"), "TOTAL")
        self.assertEqual(D.market_family("x-halftime-result-draw"),
                         "HALFTIME_RESULT")
        self.assertEqual(D.market_family("x-draw"), "DRAW")


class FoldsNeverSplitAFixture(unittest.TestCase):

    def rows(self, n_events=40, per_event=6):
        out = []
        for e in range(n_events):
            for m in range(per_event):
                out.append({
                    "EVENT_KEY": "E%03d" % e, "MARKET_SLUG": "E%03d-m%d" % (e, m),
                    "MARKET_FAMILY": "TOTAL", "SPORT": "Soccer",
                    "AS_OF": "2026-09-%02dT12:00:00Z" % (1 + e % 28),
                    "P_VENUE_TRADE": 0.3 + 0.4 * ((e * 7 + m) % 10) / 10.0,
                    "SETTLED_YES": (e + m) % 2,
                })
        return out

    def test_no_event_appears_in_both_train_and_test(self):
        f = WF.make_folds(self.rows())
        for fold in f["FOLDS"]:
            self.assertFalse(set(fold["TRAIN_EVENTS"])
                             & set(fold["TEST_EVENTS"]))

    def test_the_holdout_never_reaches_a_fold(self):
        f = WF.make_folds(self.rows())
        hold = set(f["HOLDOUT_EVENTS"])
        for fold in f["FOLDS"]:
            self.assertFalse(hold & set(fold["TRAIN_EVENTS"]))
            self.assertFalse(hold & set(fold["TEST_EVENTS"]))

    def test_training_never_reaches_past_the_test_boundary(self):
        f = WF.make_folds(self.rows())
        for fold in f["FOLDS"]:
            self.assertLessEqual(fold["TRAIN_UNTIL"], fold["TEST_FROM"])

    def test_the_leakage_check_reports_clean_on_clean_folds(self):
        rows = self.rows()
        self.assertTrue(WF.leakage_check(WF.make_folds(rows), rows)["CLEAN"])

    def test_the_leakage_check_catches_a_split_fixture(self):
        """Prove the detector detects, rather than always saying CLEAN."""
        rows = self.rows()
        f = WF.make_folds(rows)
        f["FOLDS"][0]["TEST_EVENTS"] = list(f["FOLDS"][0]["TEST_EVENTS"]) + \
            [f["FOLDS"][0]["TRAIN_EVENTS"][0]]
        self.assertFalse(WF.leakage_check(f, rows)["CLEAN"])

    def test_the_holdout_is_not_scored_by_the_comparison_run(self):
        rep = WF.run(self.rows(), {"B0": M.b0_venue_price}, draws=5)
        self.assertTrue(rep["HOLDOUT_NOT_SCORED_HERE"])

    def test_the_number_of_comparisons_is_reported(self):
        rep = WF.run(self.rows(), M.ZOO, draws=5)
        self.assertGreater(rep["COMPARISONS_RUN"], 0)
        self.assertIn("not surprising at this many looks",
                      rep["MULTIPLICITY_NOTE"])


class ScoringRewardsHonestyNotConfidence(unittest.TestCase):

    def test_a_perfect_forecast_scores_zero(self):
        self.assertAlmostEqual(CAL.brier([(1.0, 1), (0.0, 0)]), 0.0, places=9)

    def test_a_confident_wrong_forecast_is_punished_hard(self):
        bad = CAL.log_loss([(0.999, 0)])
        mid = CAL.log_loss([(0.5, 0)])
        self.assertGreater(bad, mid * 5)

    def test_accuracy_is_absent_from_the_module(self):
        src = Path(CAL.__file__).read_text()
        code = src.split('"""', 2)[2]
        self.assertNotIn("def accuracy", code)
        self.assertNotIn("win_rate", code)

    def test_the_decomposition_identity_holds(self):
        pairs = [(0.2, 0), (0.2, 1), (0.8, 1), (0.8, 1), (0.5, 0), (0.5, 1)]
        d = CAL.brier_decomposition(pairs)
        self.assertAlmostEqual(d["DECOMPOSITION_IDENTITY"],
                               CAL.brier(pairs), places=6)

    def test_a_flat_forecast_has_zero_resolution(self):
        pairs = [(0.5, i % 2) for i in range(100)]
        self.assertAlmostEqual(CAL.brier_decomposition(pairs)["RESOLUTION"],
                               0.0, places=9)

    def test_an_overconfident_forecast_shows_a_slope_below_one(self):
        rows = []
        for i in range(400):
            true = 0.3 + 0.4 * (i % 10) / 10.0
            stated = _sharpen(true, 2.0)
            rows.append((stated, 1 if (i * 7919) % 1000 < true * 1000 else 0))
        s = CAL.calibration_slope_intercept(rows)["CALIBRATION_SLOPE"]
        self.assertLess(s, 1.0)

    def test_the_bootstrap_resamples_events_not_rows(self):
        rows = [{"EVENT_KEY": "E%d" % (i // 20), "P": 0.5, "Y": i % 2}
                for i in range(200)]
        ci = CAL.event_bootstrap(rows, CAL.brier, draws=50)
        self.assertEqual(ci["RESAMPLED_UNIT"], "EVENT")
        self.assertEqual(ci["EVENTS"], 10)

    def test_clustering_widens_the_interval_versus_pretending_independence(self):
        """The whole reason for clustering, demonstrated."""
        # Outcome varies BY EVENT, so which events are drawn changes the
        # statistic. Ten clustered events carry the same information as ten
        # independent rows -- not two hundred.
        clustered = [{"EVENT_KEY": "E%d" % (i // 20), "P": 0.3,
                      "Y": (i // 20) % 2} for i in range(200)]
        independent = [{"EVENT_KEY": "E%d" % i, "P": 0.3, "Y": i % 2}
                       for i in range(200)]
        a = CAL.event_bootstrap(clustered, CAL.brier, draws=200)
        b = CAL.event_bootstrap(independent, CAL.brier, draws=200)
        self.assertGreater(a["UPPER"] - a["LOWER"], b["UPPER"] - b["LOWER"])

    def test_overlapping_intervals_are_not_called_a_win(self):
        a = {"LABEL": "a", "LOG_LOSS": 0.50,
             "LOG_LOSS_CI": {"LOWER": 0.45, "UPPER": 0.55}}
        b = {"LABEL": "b", "LOG_LOSS": 0.52,
             "LOG_LOSS_CI": {"LOWER": 0.47, "UPPER": 0.57}}
        c = CAL.compare(a, b)
        self.assertTrue(c["LOG_LOSS_INTERVALS_OVERLAP"])
        self.assertFalse(c["A_IS_DISTINGUISHABLY_BETTER"])


def _sharpen(p, k):
    z = math.log(p / (1 - p)) * k
    return 1.0 / (1.0 + math.exp(-z))


class EveryContractComesFromOneDistribution(unittest.TestCase):

    def grid(self):
        return EM.score_grid(1.4, 1.1, EM.FAMILY_DIXON_COLES, rho=-0.05)

    def test_the_grid_is_a_probability_distribution(self):
        self.assertAlmostEqual(sum(self.grid().values()), 1.0, places=9)

    def test_home_away_and_draw_partition_the_outcome(self):
        g = self.grid()
        self.assertAlmostEqual(EM.p_home_win(g) + EM.p_away_win(g)
                               + EM.p_draw(g), 1.0, places=9)

    def test_the_totals_ladder_is_monotone(self):
        """P(over) can never rise at a higher line. The §1 requirement."""
        g = self.grid()
        prev = None
        for L in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
            o = EM.p_total_over(g, L)
            if prev is not None:
                self.assertLessEqual(o, prev + 1e-12,
                                     "P(over %.1f) exceeded the lower line" % L)
            prev = o

    def test_over_and_under_are_complements_on_a_half_line(self):
        g = self.grid()
        for L in (0.5, 2.5, 4.5):
            self.assertAlmostEqual(EM.p_total_over(g, L)
                                   + EM.p_total_under(g, L), 1.0, places=9)

    def test_a_whole_number_total_is_refused_not_guessed(self):
        g = self.grid()
        self.assertEqual(EM.p_total_over(g, 2.0), NOT_IDENTIFIED)

    def test_a_whole_number_handicap_reports_its_push(self):
        g = self.grid()
        r = EM.p_spread_home_cover(g, 0.0)
        self.assertTrue(r["PUSH_IS_A_REAL_OUTCOME"])
        self.assertAlmostEqual(r["P_COVER"] + r["P_PUSH"] + r["P_NOT_COVER"],
                               1.0, places=9)

    def test_the_coherence_engine_passes_a_derived_surface(self):
        g = self.grid()
        self.assertTrue(EM.coherence(g)["COHERENT"])

    def test_the_coherence_engine_catches_an_incoherent_surface(self):
        """Prove the detector detects. Hand-build the impossible ladder."""
        g = self.grid()
        priced = EM.price_all(g)
        priced["TOTALS"]["3.5"]["OVER"] = priced["TOTALS"]["2.5"]["OVER"] + 0.1
        priced["TOTALS"]["3.5"]["UNDER"] = 1.0 - priced["TOTALS"]["3.5"]["OVER"]
        c = EM.coherence(g, priced)
        self.assertFalse(c["COHERENT"])
        self.assertTrue(any(f["CHECK"] == "TOTALS_LADDER_MONOTONE"
                            for f in c["FAILURES"]))

    def test_dixon_coles_lifts_the_low_score_draws(self):
        """The correction's whole purpose, against independent Poisson."""
        ind = EM.score_grid(1.4, 1.1, EM.FAMILY_INDEPENDENT_POISSON)
        dc = EM.score_grid(1.4, 1.1, EM.FAMILY_DIXON_COLES, rho=-0.08)
        self.assertGreater(dc[(0, 0)], ind[(0, 0)])

    def test_the_family_is_a_challenger_not_a_champion(self):
        self.assertEqual(EM.MODEL_STATUS, "CHALLENGER_NOT_VALIDATED")

    def test_fit_status_refuses_a_thin_sample(self):
        events = [{"RECONSTRUCTION_STATUS": "UNIQUE", "HOME_CODE": "t%d" % i,
                   "AWAY_CODE": "u%d" % i} for i in range(30)]
        s = EM.fit_status(events)
        self.assertEqual(s["FIT_STATUS"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(s["FITTABLE"])


class TheReceiptDoesNotInventWhatWasNotMeasured(unittest.TestCase):

    def rec(self):
        return EV.price("E1", "M1", "TOTAL", "OVER", "2026-09-01T12:00:00Z",
                        0.42)

    def test_every_contract_field_is_present(self):
        c = EV.completeness(self.rec())
        self.assertEqual(c["FIELDS_MISSING_FROM_RECEIPT"], [])
        self.assertTrue(c["CONTRACT_COMPLETE"])

    def test_settlement_fair_value_is_the_venue_price(self):
        r = self.rec()
        self.assertEqual(r["SETTLEMENT_FAIR_VALUE"], 0.42)
        self.assertIn("the market's", r["CHAMPION_IS_THE_MARKET"])

    def test_p_fill_is_absent_and_says_why(self):
        r = self.rec()
        self.assertEqual(r["P_FILL"], NOT_IDENTIFIED)
        self.assertEqual(r["P_FILL_STATUS"], "NOT_IDENTIFIED")
        self.assertIn("ONE observation", r["ONE_ORDER_DOES_NOT_IDENTIFY_P_FILL"])

    def test_every_execution_horizon_is_absent(self):
        r = self.rec()
        for h in EV.EXECUTION_HORIZONS_S:
            self.assertEqual(r["EXECUTION_FAIR_VALUE_%dS" % h], NOT_IDENTIFIED)

    def test_every_action_ev_is_absent(self):
        r = self.rec()
        for a in ("MAKE", "TAKE", "HOLD", "PAIR", "HEDGE", "PASSIVE_EXIT",
                  "AGGRESSIVE_EXIT", "NO_TRADE"):
            self.assertEqual(r["EV_%s" % a], NOT_IDENTIFIED)

    def test_no_trade_is_declared_a_refusal_not_a_winner(self):
        r = self.rec()
        self.assertEqual(r["BEST_ACTION"], "NO_TRADE")
        self.assertIn("not the winner of a comparison", r["WHY_NO_TRADE"])

    def test_every_absent_component_names_its_reason(self):
        r = self.rec()
        for k in ("P_EXTERNAL_CONSENSUS", "P_FUNDAMENTAL", "P_FILL",
                  "VALUE_CONDITIONAL_ON_FILL"):
            self.assertIn(k, r["ABSENT_BECAUSE"], k)

    def test_the_whale_residual_is_recorded_as_tested_and_rejected(self):
        r = self.rec()
        self.assertIn("failed walk-forward",
                      r["ABSENT_BECAUSE"]["P_WHALE_RESIDUAL"])

    def test_fair_value_status_is_not_yet_validated(self):
        self.assertEqual(EV.FAIR_VALUE_STATUS, "NOT_YET_VALIDATED")
        self.assertEqual(EV.MODEL_VERSION, "B0_VENUE_PRICE")

    def test_the_calibration_version_is_identity_and_says_why(self):
        self.assertEqual(EV.CALIBRATION_VERSION, "IDENTITY_NO_RECALIBRATION")
        self.assertIn("lost to the raw venue price", EV.WHY_IDENTITY_CALIBRATION)

    def test_a_bad_price_does_not_produce_a_fair_value(self):
        for bad in (None, "x", 0.0, 1.0, 1.5):
            r = EV.price("E", "M", "TOTAL", "OVER", "t", bad)
            self.assertEqual(r["SETTLEMENT_FAIR_VALUE"], NOT_IDENTIFIED)


class NoModuleCanPlaceAnOrder(unittest.TestCase):

    def test_no_network_and_no_submit_anywhere_in_the_ev_core(self):
        import ev_core_data, ev_core_outcomes, ev_core_walkforward
        mods = (EV, CAL, EM, M, ev_core_data, ev_core_outcomes,
                ev_core_walkforward)
        for mod in mods:
            src = Path(mod.__file__).read_text()
            # Scan the CODE, not the prose: these modules legitimately promise
            # in their docstrings not to do the things being scanned for.
            code = (src.split('"""', 2)[2] if src.count('"""') >= 2
                    else src).lower()
            # Call syntax, not the word: NO_SUBMIT_PATH_EXISTS is a key name
            # declaring the absence, which is the opposite of a violation.
            for bad in ("httpx", "requests.", "submit(", ".submit",
                        "place_order", "https://", "api_key", "private_key"):
                self.assertNotIn(bad, code, "%s in %s" % (bad, mod.__name__))

    def test_the_core_declares_it_has_no_submit_path(self):
        self.assertTrue(EV.describe()["NO_SUBMIT_PATH_EXISTS"])


if __name__ == "__main__":
    unittest.main()


class TheGenerationOneResultIsFrozen(unittest.TestCase):
    """B0 earned BASELINE, not INDEPENDENT FAIR VALUE. Different claims."""

    def test_b0_is_the_baseline_not_the_independent_fair_value(self):
        self.assertEqual(EV.B0_VENUE_RAW, "CURRENT_STRONGEST_BASELINE")
        self.assertEqual(EV.B0_IS_NOT, "BETTOR_INDEPENDENT_FAIR_VALUE")

    def test_the_result_is_recorded_so_it_cannot_be_tuned_away(self):
        r = EV.GENERATION_1_RESULT
        self.assertEqual(r["LABELLED_OBSERVATIONS"], 112535)
        self.assertEqual(r["INDEPENDENT_EVENTS"], 4103)
        self.assertEqual(r["AS_OF_VIOLATIONS_CAUGHT"], 1684)
        self.assertEqual(r["OUTCOME_CONTRADICTIONS_AFTER_PARSER_REPAIR"], 0)
        self.assertEqual(r["WALK_FORWARD_WINNER"], "B0_VENUE_PRICE")
        self.assertTrue(r["EVERY_RECALIBRATION_LOST_OUT_OF_SAMPLE"])

    def test_the_holdout_is_burned_and_reporting_only(self):
        self.assertEqual(EV.GENERATION_1_FINAL_HOLDOUT,
                         "BURNED_FOR_MODEL_SELECTION")
        self.assertEqual(EV.HOLDOUT_PERMITTED_USE, "REPORTING_ONLY")

    def test_every_selection_use_of_the_holdout_is_forbidden(self):
        for use in ("FEATURE_SELECTION", "MODEL_CLASS_SELECTION",
                    "CALIBRATION_SELECTION", "HYPERPARAMETER_SELECTION",
                    "SPORT_SELECTION", "MARKET_FAMILY_SELECTION",
                    "THRESHOLD_TUNING", "ENSEMBLE_WEIGHTS"):
            self.assertIn(use, EV.HOLDOUT_FORBIDDEN_USES, use)

    def test_the_next_generation_needs_a_new_untouched_holdout(self):
        self.assertIn("NEW untouched chronological holdout",
                      EV.NEXT_GENERATION_REQUIRES)


class TheFourProbabilityObjectsAreNeverConflated(unittest.TestCase):

    def test_all_four_are_declared_with_distinct_status(self):
        o = EV.PROBABILITY_OBJECTS
        for k in ("P_MARKET_RAW", "P_MARKET_SURFACE", "P_BETTOR_INDEPENDENT",
                  "P_BETTOR_ENSEMBLE"):
            self.assertIn(k, o, k)

    def test_only_the_independent_object_is_alpha(self):
        o = EV.PROBABILITY_OBJECTS
        self.assertFalse(o["P_MARKET_RAW"]["INDEPENDENT_ALPHA"])
        self.assertFalse(o["P_MARKET_SURFACE"]["INDEPENDENT_ALPHA"])
        self.assertTrue(o["P_BETTOR_INDEPENDENT"]["INDEPENDENT_ALPHA"])

    def test_the_independent_object_does_not_exist_yet(self):
        o = EV.PROBABILITY_OBJECTS
        self.assertEqual(o["P_BETTOR_INDEPENDENT"]["STATUS"], "DOES_NOT_EXIST")
        self.assertEqual(o["P_BETTOR_ENSEMBLE"]["STATUS"], "DOES_NOT_EXIST")

    def test_a_receipt_carries_all_four_separately(self):
        r = EV.price("E", "M", "TOTAL", "OVER", "t", 0.42)
        self.assertEqual(r["P_MARKET_RAW"], 0.42)
        self.assertEqual(r["P_MARKET_SURFACE"], NOT_IDENTIFIED)
        self.assertEqual(r["P_BETTOR_INDEPENDENT"], NOT_IDENTIFIED)
        self.assertEqual(r["P_BETTOR_ENSEMBLE"], NOT_IDENTIFIED)


class TheMarketSurfaceIsCoherentAndNotAlpha(unittest.TestCase):

    def event(self, n_totals=5):
        rows = [{"MARKET_SLUG": "epl-aaa-bbb-2026-09-01-aaa", "OUTCOME": "Yes",
                 "P_VENUE_TRADE": 0.45, "EVENT_KEY": "epl-aaa-bbb-2026-09-01"},
                {"MARKET_SLUG": "epl-aaa-bbb-2026-09-01-draw", "OUTCOME": "Yes",
                 "P_VENUE_TRADE": 0.27, "EVENT_KEY": "epl-aaa-bbb-2026-09-01"},
                {"MARKET_SLUG": "epl-aaa-bbb-2026-09-01-btts", "OUTCOME": "Yes",
                 "P_VENUE_TRADE": 0.52, "EVENT_KEY": "epl-aaa-bbb-2026-09-01"}]
        for i in range(n_totals):
            rows.append({"MARKET_SLUG": "epl-aaa-bbb-2026-09-01-total-%dpt5" % i,
                         "OUTCOME": "Over", "P_VENUE_TRADE": max(0.05,
                                                                 0.92 - 0.2 * i),
                         "EVENT_KEY": "epl-aaa-bbb-2026-09-01"})
        return rows

    def test_a_rich_event_fits(self):
        import ev_core_surface as SF
        s = SF.surface(self.event(), "aaa", "bbb")
        self.assertEqual(s["SURFACE_STATUS"], "FITTED")

    def test_the_fitted_surface_is_coherent(self):
        import ev_core_surface as SF
        s = SF.surface(self.event(), "aaa", "bbb")
        self.assertTrue(s["COHERENT_BY_CONSTRUCTION"])
        self.assertTrue(s["COHERENCE"]["COHERENT"])

    def test_a_thin_event_is_refused_not_fitted(self):
        import ev_core_surface as SF
        s = SF.surface(self.event(n_totals=0)[:2], "aaa", "bbb")
        self.assertEqual(s["SURFACE_STATUS"], "INSUFFICIENT_LINKED_CONTRACTS")
        self.assertEqual(s["P_MARKET_SURFACE"], NOT_IDENTIFIED)

    def test_an_unfittable_price_set_is_refused(self):
        """Prices that no score distribution can produce must not yield one."""
        import ev_core_surface as SF
        rows = self.event()
        for r in rows:
            r["P_VENUE_TRADE"] = 0.99      # everything at once is impossible
        s = SF.surface(rows, "aaa", "bbb")
        self.assertIn(s["SURFACE_STATUS"],
                      ("POOR_FIT_REFUSED", "INSUFFICIENT_LINKED_CONTRACTS"))
        self.assertEqual(s["P_MARKET_SURFACE"], NOT_IDENTIFIED)

    def test_the_surface_declares_it_is_not_independent_alpha(self):
        import ev_core_surface as SF
        s = SF.surface(self.event(), "aaa", "bbb")
        self.assertFalse(s["IS_INDEPENDENT_ALPHA"])
        self.assertTrue(s["IS_MARKET_DERIVED"])
        self.assertIn("cannot say the market is wrong", s["NOT_ALPHA"])

    def test_a_gap_from_raw_is_labelled_a_consistency_signal(self):
        import ev_core_surface as SF
        s = SF.surface(self.event(), "aaa", "bbb")
        self.assertIn("INTERNAL", s["DISAGREEMENT_MEANS"])
        self.assertEqual(s["VALIDATION_STATUS"],
                         "NOT_YET_TESTED_AGAINST_SETTLEMENTS")

    def test_segment_contracts_are_excluded_from_the_fit(self):
        import ev_core_surface as SF
        rows = self.event()
        rows.append({"MARKET_SLUG": "epl-aaa-bbb-2026-09-01-halftime-result-draw",
                     "OUTCOME": "Yes", "P_VENUE_TRADE": 0.3,
                     "EVENT_KEY": "epl-aaa-bbb-2026-09-01"})
        obs = SF.observations(rows, "aaa", "bbb")
        self.assertFalse(any("HALFTIME" in n for n, _, _ in obs))
