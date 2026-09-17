"""Tests for the microstructure stack: zoo, simulator, rails, toxicity, EV."""

import datetime
from decimal import Decimal

import pytest

import action_ev as AE
import exec_sim as XS
import freshness_rails as FR
import micro_zoo as MZ
import toxicity_v1 as TX


# --- Section A. The ladder. ------------------------------------------------

def test_the_zoo_is_ordered_by_complexity():
    c = [MZ.COMPLEXITY_OF[n] for n in MZ.ZOO_NAMES]
    assert c == sorted(c)
    assert MZ.ZOO_NAMES[0] == "M0_NO_CHANGE"
    assert MZ.ZOO_NAMES[-1] == "M9_DEEPLOB"


def test_a_model_must_beat_every_simpler_rung():
    scores = {"M0_NO_CHANGE": 0.50, "M1_MIDPOINT": 0.48,
              "M2_SIMPLE_BOOK_IMBALANCE": 0.47,
              "M3_STOIKOV_MICROPRICE": 0.46}
    a = MZ.admit("M3_STOIKOV_MICROPRICE", scores)
    assert a["ADMITTED"] is True
    assert len(a["BEATS"]) == 3


def test_losing_to_one_simpler_model_blocks_admission():
    scores = {"M0_NO_CHANGE": 0.40, "M1_MIDPOINT": 0.48,
              "M2_SIMPLE_BOOK_IMBALANCE": 0.47,
              "M3_STOIKOV_MICROPRICE": 0.46}
    a = MZ.admit("M3_STOIKOV_MICROPRICE", scores)
    assert a["ADMITTED"] is False
    assert [d["MODEL"] for d in a["DOES_NOT_BEAT"]] == ["M0_NO_CHANGE"]


def test_an_unevaluated_simpler_model_blocks_admission():
    """Fails closed: a comparison that never happened is not a pass."""
    scores = {"M0_NO_CHANGE": 0.50, "M7_GRADIENT_BOOSTED_MICROSTRUCTURE": 0.30}
    a = MZ.admit("M7_GRADIENT_BOOSTED_MICROSTRUCTURE", scores)
    assert a["ADMITTED"] is False
    assert len(a["SIMPLER_MODELS_NOT_EVALUATED"]) == 6
    assert "did not happen" in a["WHY_NOT"]


def test_a_tie_is_a_loss():
    scores = {"M0_NO_CHANGE": 0.50, "M1_MIDPOINT": 0.50}
    a = MZ.admit("M1_MIDPOINT", scores)
    assert a["ADMITTED"] is False
    assert a["A_TIE_IS_A_LOSS"] is True


def test_the_objective_returns_the_simplest_survivor_not_the_best():
    """M1 and M3 both beat everything below; the SIMPLER one wins."""
    scores = {"M0_NO_CHANGE": 0.50, "M1_MIDPOINT": 0.40,
              "M2_SIMPLE_BOOK_IMBALANCE": 0.39,
              "M3_STOIKOV_MICROPRICE": 0.20}
    s = MZ.simplest_admitted(scores)
    assert s["MODEL"] == "M1_MIDPOINT"


def test_the_null_cannot_win_by_vacuity():
    """M0 has no simpler rung, so admit() passes it trivially.

    Returning it as the champion would dress a null result as a positive one.
    """
    assert MZ.admit("M0_NO_CHANGE", {"M0_NO_CHANGE": 0.50})["ADMITTED"] is True
    s = MZ.simplest_admitted({"M0_NO_CHANGE": 0.50, "M1_MIDPOINT": 0.60})
    assert s["ADMITTED"] is False
    assert s["WHY"] == "NOTHING_BEAT_THE_NULL"
    assert "finding about the market" in s["THIS_IS_A_REAL_RESULT"]


def test_an_unknown_model_is_refused():
    a = MZ.admit("M42_SOMETHING_CLEVER", {"M42_SOMETHING_CLEVER": 0.0})
    assert a["ADMITTED"] is False
    assert a["WHY"] == "UNKNOWN_MODEL_NOT_IN_THE_DECLARED_ZOO"


def test_the_objective_is_the_simplest_model_not_the_fanciest():
    assert "SIMPLEST" in MZ.THE_OBJECTIVE
    assert "not the fanciest" in MZ.THE_OBJECTIVE


# --- The upstream audit. ---------------------------------------------------

def test_every_upstream_source_records_its_licence():
    for s in MZ.UPSTREAM_SOURCES:
        assert s["LICENCE"]
        assert s["TAKEN"]


def test_simulator_tuned_constants_are_refused_by_name():
    octavi = [s for s in MZ.UPSTREAM_SOURCES
              if "octavi42" in s["SOURCE"]][0]
    assert "SIMULATOR_TUNED_CONSTANTS" in octavi["NOT_TAKEN"]
    assert "14_OVER_PROB" in octavi["NOT_TAKEN"]
    assert octavi["STATUS"] == "HYPOTHESES_ONLY"


def test_unlicensed_code_is_not_copied():
    for name in ("Stoikov microprice", "DeepLOB"):
        s = [x for x in MZ.UPSTREAM_SOURCES if x["SOURCE"] == name][0]
        assert s["LICENCE"] == "METHODOLOGY_REFERENCE_ONLY"


def test_a_foreign_constant_is_not_evidence():
    assert "have not earned" in MZ.A_CONSTANT_FROM_ANOTHER_VENUE_IS_NOT_EVIDENCE


# --- Section B. Four target systems. ---------------------------------------

def test_there_are_four_systems_not_one():
    assert set(MZ.TARGET_SYSTEMS) == {"PRICE_MOVE", "TOXICITY", "FILL", "QUEUE"}


def test_fill_is_not_identified_and_may_not_be_trained():
    t = MZ.target_status("FILL")
    assert t["STATUS"] == "NOT_IDENTIFIED"
    assert MZ.may_train("FILL")["MAY_TRAIN"] is False
    assert t["BLOCKED_ON"] == "BETTOR_NATIVE_ORDER_EVIDENCE"


def test_price_move_may_be_trained_once_data_exists():
    assert MZ.may_train("PRICE_MOVE")["MAY_TRAIN"] is True


def test_queue_is_distributional_never_an_exact_index():
    q = MZ.target_status("QUEUE")
    assert q["STATUS"] == "DISTRIBUTIONAL_ONLY"
    assert "fake precision" in q["WHY"]
    assert "do not infer an exact queue position" in MZ.QUEUE_MAY_NOT_BE_FAKED


def test_every_system_carries_all_four_horizons():
    for s in ("PRICE_MOVE", "TOXICITY", "FILL"):
        assert len(MZ.TARGET_SYSTEMS[s]["TARGETS"]) == 4


# --- Section K. DeepLOB. ---------------------------------------------------

def test_deeplob_may_not_train_on_the_pilot():
    g = MZ.deeplob_gate(independent_events=3, event_hours=4.5,
                        book_transitions=1000, depth_observations=1350)
    assert g["MAY_TRAIN"] is False
    assert g["ADMITTED"] is False
    assert len(g["DATA_SHORTFALL"]) == 4


def test_deeplob_needs_data_and_wins_and_the_economic_test():
    enough = dict(independent_events=500, event_hours=1000,
                  book_transitions=10**6, depth_observations=10**7)
    g = MZ.deeplob_gate(**enough, beats=MZ.DEEPLOB_MUST_BEAT,
                        economic_test_passed=False)
    assert g["MAY_TRAIN"] is True
    assert g["ADMITTED"] is False          # economic test not passed
    g2 = MZ.deeplob_gate(**enough, beats=MZ.DEEPLOB_MUST_BEAT,
                         economic_test_passed=True)
    assert g2["ADMITTED"] is True


def test_accuracy_alone_cannot_admit_deeplob():
    assert "earn nothing" in MZ.CLASSIFICATION_ACCURACY_CANNOT_ADMIT_IT
    assert "ECONOMIC_MOVE_RELATIVE_TO_SPREAD" in MZ.DEEPLOB_MUST_BEAT_ON


# --- Section L. No RL. -----------------------------------------------------

def test_rl_is_not_eligible_today():
    g = MZ.rl_gate()
    assert g["MAY_DEPLOY_RL"] is False
    assert g["RL_STATUS"] == "NOT_ELIGIBLE"
    assert len(g["BLOCKED_BY"]) == 4


def test_rl_becomes_eligible_only_on_all_four_preconditions():
    g = MZ.rl_gate(True, True, True, True)
    assert g["MAY_DEPLOY_RL"] is True


def test_rl_over_an_uncalibrated_simulator_learns_its_defects():
    assert "simulator's defects" in MZ.WHY_NOT_RL_YET


# --- Section C. The simulator's sources. -----------------------------------

def test_a_crypto_assumption_is_refused():
    r = XS.declare_behaviour("MAKER_REBATE", "BINANCE_ASSUMPTION",
                             value="0.0002", evidence="their docs")
    assert r["ACCEPTED"] is False
    assert r["REASON"] == "FORBIDDEN_SOURCE"


def test_a_plausible_guess_is_refused():
    r = XS.declare_behaviour("CANCEL_LATENCY", "PLAUSIBLE_GUESS",
                             value="50ms", evidence="feels right")
    assert r["ACCEPTED"] is False
    assert "most dangerous input" in r["WHY_A_GUESS_IS_REFUSED"]


def test_a_venue_rule_with_evidence_is_accepted():
    r = XS.declare_behaviour("SETTLEMENT", XS.SOURCE_VENUE_RULE,
                             value="0_OR_1", evidence="venue rules page")
    assert r["ACCEPTED"] is True


def test_a_source_without_evidence_is_refused():
    r = XS.declare_behaviour("FEED_LATENCY", XS.SOURCE_BETTOR_MEASURED,
                             value="12ms", evidence=None)
    assert r["ACCEPTED"] is False
    assert r["REASON"] == "NO_VALUE_OR_NO_EVIDENCE"


def test_the_simulator_may_not_produce_trusted_fills_yet():
    r = XS.readiness()
    assert r["MAY_PRODUCE_TRUSTED_FILLS"] is False
    assert "ORDER_REQUEST_LATENCY" in r["CAPABILITIES_NOT_SOURCED"]


def test_a_binary_contract_is_not_a_perpetual_future():
    assert "variance collapses" in XS.NOT_A_PERPETUAL_FUTURE


def test_every_required_capability_is_declared():
    for cap in XS.REQUIRED_CAPABILITIES:
        assert cap in XS.CAPABILITY_SOURCE


# --- Section D. Calibration. -----------------------------------------------

def test_an_uncalibrated_simulator_is_not_trusted():
    c = XS.calibration()
    assert c["SIMULATOR_MAY_BE_TRUSTED"] is False
    assert c["SIMULATOR_STATUS"] == "UNVALIDATED"
    assert len(c["NOT_MEASURED"]) == 4


def test_sophistication_does_not_confer_trust():
    assert "matches measured reality" in XS.SOPHISTICATION_IS_NOT_TRUST


def test_alignment_on_a_different_period_does_not_validate():
    obs = {s: {"SIM": 1.0, "REAL": 1.0} for s, _ in XS.CALIBRATION_PAIRS}
    c = XS.calibration(obs, same_period=False)
    assert c["SIMULATOR_MAY_BE_TRUSTED"] is False
    c2 = XS.calibration(obs, same_period=True)
    assert c2["SIMULATOR_MAY_BE_TRUSTED"] is True


def test_one_misaligned_pair_blocks_validation():
    obs = {s: {"SIM": 1.0, "REAL": 1.0} for s, _ in XS.CALIBRATION_PAIRS}
    obs["SIMULATED_FILL_RATE"] = {"SIM": 0.9, "REAL": 0.2}
    c = XS.calibration(obs, same_period=True)
    assert c["SIMULATOR_MAY_BE_TRUSTED"] is False


def test_a_partly_measured_calibration_still_fails_closed():
    obs = {"SIMULATED_FILL_RATE": {"SIM": 1.0, "REAL": 1.0}}
    c = XS.calibration(obs, same_period=True)
    assert c["SIMULATOR_MAY_BE_TRUSTED"] is False
    assert len(c["NOT_MEASURED"]) == 3


# --- Section E. Freshness rails. -------------------------------------------

def _t(s):
    return (datetime.datetime(2026, 9, 17, 18, 0,
                              tzinfo=datetime.timezone.utc)
            + datetime.timedelta(seconds=s)).isoformat()


def test_heartbeats_do_not_make_a_book_fresh():
    """The exact bug: messages keep arriving, content is frozen."""
    tr = FR.FreshnessTracker(content_stale_after_s=30.0)
    tr.on_message(_t(0), _t(0), {"BID": 0.40, "ASK": 0.42})
    for s in range(1, 60, 5):
        st = tr.on_message(_t(s), heartbeat=True)
    assert st["MESSAGES"] > 10
    assert st["FEED_CONTENT_STALE"] is True
    assert st["QUOTING_PERMITTED"] is False
    assert "MESSAGES_CONTINUED" in st["WHY_STALE"]


def test_a_repeated_identical_book_is_not_a_content_change():
    tr = FR.FreshnessTracker(content_stale_after_s=30.0)
    tr.on_message(_t(0), _t(0), {"BID": 0.40, "ASK": 0.42})
    for s in (5, 10, 40):
        st = tr.on_message(_t(s), _t(s), {"BID": 0.40, "ASK": 0.42})
    assert st["MATERIAL_CHANGES"] == 1
    assert st["FEED_CONTENT_STALE"] is True


def test_a_real_move_refreshes_the_content_clock():
    tr = FR.FreshnessTracker(content_stale_after_s=30.0)
    tr.on_message(_t(0), _t(0), {"BID": 0.40, "ASK": 0.42})
    st = tr.on_message(_t(25), _t(25), {"BID": 0.41, "ASK": 0.42})
    assert st["MATERIAL_CHANGES"] == 2
    assert st["FEED_CONTENT_STALE"] is False
    assert st["QUOTING_PERMITTED"] is True


def test_the_content_hash_ignores_arrival_time():
    a = FR.content_hash({"BID": 0.40, "ASK": 0.42, "RECEIPT_UTC": _t(0)})
    b = FR.content_hash({"BID": 0.40, "ASK": 0.42, "RECEIPT_UTC": _t(99)})
    assert a == b


def test_the_content_hash_notices_a_real_change():
    a = FR.content_hash({"BID": 0.40, "ASK": 0.42})
    b = FR.content_hash({"BID": 0.41, "ASK": 0.42})
    assert a != b


def test_a_tracker_that_has_seen_nothing_is_stale_not_fresh():
    tr = FR.FreshnessTracker()
    st = tr.on_message(_t(0), heartbeat=True)
    assert st["FEED_CONTENT_STALE"] is True
    assert st["WHY_STALE"] == "NO_MATERIAL_CONTENT_CHANGE_EVER_OBSERVED"


def test_venue_state_lag_is_tracked_separately_from_receipt():
    tr = FR.FreshnessTracker(venue_lag_alarm_s=15.0)
    st = tr.on_message(_t(60), _t(20), {"BID": 0.40, "ASK": 0.42})
    assert st["VENUE_STATE_LAG_S"] == 40.0
    assert st["VENUE_STATE_LAG_ALARM"] is True


def test_the_four_clocks_are_never_collapsed():
    assert "three different facts" in FR.CLOCKS_ARE_NEVER_COLLAPSED
    assert len(FR.CLOCKS) == 4


# --- Section G. Toxicity. --------------------------------------------------

def test_v1_is_market_state_toxicity_not_fill_conditional():
    lab = TX.label(has_native_fill_evidence=False)
    assert lab["LABEL"] == "MARKET_STATE_TOXICITY"
    assert lab["FILL_CONDITIONAL"] is False
    assert lab["THIS_UNDERSTATES_ADVERSE_SELECTION"] is True


def test_it_upgrades_only_on_native_fill_evidence():
    lab = TX.label(has_native_fill_evidence=True)
    assert lab["LABEL"] == "VALUE_CONDITIONAL_ON_FILL"


def test_fills_are_selected_and_that_is_why_the_two_differ():
    assert "SELECTED" in TX.WHY_THE_TWO_DIFFER
    assert "correlates with being right" in TX.WHY_THE_TWO_DIFFER


def test_toxicity_targets_the_executable_price_not_the_mid():
    assert "EXECUTABLE" in TX.TARGET
    assert "half a spread" in TX.WHY_EXECUTABLE_NOT_MID


def test_blocked_features_are_listed_not_dropped():
    a = TX.available_features(capture_supplies=("SPREAD", "BOOK_IMBALANCE",
                                                "QUOTE_AGE"))
    assert a["USABLE_N"] == 3
    assert a["BLOCKED"]["EXTERNAL_ODDS_DISAGREEMENT"] == \
        "BLOCKED_NO_EXTERNAL_ODDS"
    assert a["BLOCKED"]["TIME_TO_EVENT"] == \
        "BLOCKED_START_TIME_CLASS_A_IS_EMPTY"


# --- Section H. Benign flow. -----------------------------------------------

def test_benign_flow_capacity_is_not_identified_and_has_no_default():
    c = TX.benign_flow_capacity(state={"SPREAD": 0.02}, horizon_s=30)
    assert c["BENIGN_FLOW_CAPACITY"] == "NOT_IDENTIFIED"
    assert "no default is supplied" in c["WHY"].replace("No", "no")


def test_the_foreign_constants_are_refused_by_name():
    assert "14_OVER_PROB" in TX.DO_NOT_IMPORT
    assert "85_OVER_PROB" in TX.DO_NOT_IMPORT
    assert "different market" in TX.WHY_NOT_IMPORT


def test_the_capacity_test_refuses_without_both_arms():
    rows = [{"BENIGN_FLOW_CAPACITY": 100, "SIZE": 50, "MARKOUT": -0.01}]
    t = TX.capacity_test(rows)
    assert t["STATUS"] == "NOT_TESTABLE"


def test_the_capacity_test_compares_markouts_when_testable():
    rows = [{"BENIGN_FLOW_CAPACITY": 100, "SIZE": 50, "MARKOUT": -0.001},
            {"BENIGN_FLOW_CAPACITY": 100, "SIZE": 500, "MARKOUT": -0.02}]
    t = TX.capacity_test(rows)
    assert t["STATUS"] == "MEASURED"
    assert t["MEAN_MARKOUT_ABOVE_CAPACITY"] < t["MEAN_MARKOUT_AT_OR_BELOW"]
    assert t["THIS_IS_NOT_A_FILL_CONDITIONAL_RESULT"] is True


def test_rows_without_a_capacity_are_counted_not_guessed():
    rows = [{"BENIGN_FLOW_CAPACITY": "NOT_IDENTIFIED", "SIZE": 50,
             "MARKOUT": -0.01}]
    t = TX.capacity_test(rows)
    assert t["ROWS_WITHOUT_A_CAPACITY"] == 1


# --- Section M. Action EV. -------------------------------------------------

def test_a_missing_p_fill_makes_the_whole_ev_unidentified():
    r = AE.action_ev("MAKER_QUOTE", price="0.40", size="100",
                     p_fill=None, expected_value_if_filled="0.02",
                     expected_adverse_selection="0.005", fee="0.001")
    assert r["EXPECTED_NET_DOLLARS"] == "NOT_IDENTIFIED"
    assert r["RECOMMENDED"] is False
    assert "P_FILL" in r["MISSING_CRITICAL_TERMS"]


def test_an_unknown_term_is_never_treated_as_zero():
    assert "is not 1.0" in AE.UNKNOWN_IS_NOT_ZERO
    assert "is not 0.0" in AE.UNKNOWN_IS_NOT_ZERO


def test_a_float_in_a_money_path_is_a_hard_error():
    """An economic threshold decided by binary floating point is a defect."""
    with pytest.raises(TypeError, match="FLOAT_IN_A_MONEY_PATH"):
        AE.action_ev("MAKER_QUOTE", price="0.40", size="100",
                     p_fill=0.5, expected_value_if_filled="0.02",
                     expected_adverse_selection="0.005", fee="0.001")


def test_a_complete_action_prices_exactly():
    r = AE.action_ev("MAKER_QUOTE", price="0.40", size="100",
                     p_fill="0.25", expected_value_if_filled="0.0200",
                     expected_adverse_selection="0.0050", fee="0.0010",
                     rebate_or_incentive="0.0005", inventory_cost="0.0002",
                     capital_required="40.00", expected_occupancy_hours="0.5")
    # 0.25 * (0.02 - 0.005) - 0.001 + 0.0005 - 0.0002 = 0.003050
    assert r["EXPECTED_NET_DOLLARS"] == "0.003050"
    assert r["RECOMMENDED"] is True
    # 0.003050 / (40.00 * 0.5) = 0.0001525 -> banker's rounding -> 0.000152
    assert r["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] == "0.000152"


def test_the_embedded_convention_does_not_double_count():
    kw = dict(price="0.40", size="100", p_fill="0.25",
              expected_value_if_filled="0.0200", fee="0.0010")
    sep = AE.action_ev("MAKER_QUOTE", expected_adverse_selection="0.0050",
                       adverse_selection_convention="SEPARATE_TERM", **kw)
    emb = AE.action_ev("MAKER_QUOTE",
                       adverse_selection_convention=
                       "EMBEDDED_IN_VALUE_CONDITIONAL_ON_FILL", **kw)
    assert sep["EXPECTED_NET_DOLLARS"] == "0.002750"
    assert emb["EXPECTED_NET_DOLLARS"] == "0.004000"
    assert emb["EXPECTED_NET_DOLLARS"] != sep["EXPECTED_NET_DOLLARS"]


def test_the_embedded_convention_does_not_demand_the_separate_term():
    r = AE.action_ev("MAKER_QUOTE", price="0.40", size="100", p_fill="0.25",
                     expected_value_if_filled="0.02", fee="0.001",
                     adverse_selection_convention=
                     "EMBEDDED_IN_VALUE_CONDITIONAL_ON_FILL")
    assert r["MISSING_CRITICAL_TERMS"] == []


def test_a_negative_ev_action_is_not_recommended():
    r = AE.action_ev("MAKER_QUOTE", price="0.40", size="100", p_fill="0.05",
                     expected_value_if_filled="0.0100",
                     expected_adverse_selection="0.0090", fee="0.0100")
    assert Decimal(r["EXPECTED_NET_DOLLARS"]) < 0
    assert r["RECOMMENDED"] is False


def test_every_declared_action_term_is_present_on_the_receipt():
    r = AE.action_ev("MAKER_QUOTE", price="0.40", size="100", p_fill="0.25",
                     expected_value_if_filled="0.02",
                     expected_adverse_selection="0.005", fee="0.001")
    for t in AE.ACTION_TERMS:
        assert t in r, t


# --- Section J. The gate. --------------------------------------------------

def test_an_unidentified_ev_never_quotes():
    g = AE.quote_gate({"EXPECTED_NET_DOLLARS": "NOT_IDENTIFIED"}, "0.001")
    assert g["QUOTE"] is False
    assert "does not clear any buffer" in g["WHY_NOT"]


def test_the_gate_needs_the_ev_to_exceed_the_buffer():
    assert AE.quote_gate({"EXPECTED_NET_DOLLARS": "0.005"}, "0.001")["QUOTE"]
    assert not AE.quote_gate({"EXPECTED_NET_DOLLARS": "0.0005"},
                             "0.001")["QUOTE"]


def test_the_simple_challenger_is_evaluated_as_a_peer():
    c = AE.simple_gate_challenger("0.02", "0.005", "2.0")
    assert c["QUOTE"] is True
    assert "as a peer" in c["WHY_A_SIMPLE_CHALLENGER"]


def test_zero_volatility_gives_no_ratio_rather_than_infinity():
    c = AE.simple_gate_challenger("0.02", "0", "2.0")
    assert c["QUOTE"] is False
    assert c["RATIO"] == "NOT_IDENTIFIED"


def test_thresholds_are_not_tuned_on_test_data():
    assert "fitted to the test" in AE.DO_NOT_TUNE_ON_TEST


# --- Section I. Inventory skew. --------------------------------------------

def test_no_skew_winner_is_named_until_all_four_are_evaluated():
    s = AE.skew_comparison({"NO_SKEW": 1.0, "SYMMETRIC_SKEW": 2.0})
    assert s["BEST"] == "NOT_IDENTIFIED"
    assert len(s["NOT_EVALUATED"]) == 2


def test_the_best_skew_is_named_once_all_four_exist():
    s = AE.skew_comparison({"NO_SKEW": 1.0, "SYMMETRIC_SKEW": 2.0,
                            "ASYMMETRIC_HEAVY_SIDE_SKEW": 3.0,
                            "EV_OPTIMAL_SKEW": 2.5})
    assert s["BEST"] == "ASYMMETRIC_HEAVY_SIDE_SKEW"


def test_flattening_is_not_assumed_correct():
    assert "strictly worse than holding" in \
        AE.FLATTENING_IS_NOT_AUTOMATICALLY_RIGHT
    assert "SETTLEMENT_VALUE" in AE.SKEW_MUST_CONSIDER


# --- Section F. The money engine is ALREADY exact -- proven, not rewritten. -

def test_the_money_path_rejects_a_binary_float():
    with pytest.raises(TypeError):
        AE._d(0.1)


def test_decimal_arithmetic_is_exact_where_float_is_not():
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert 0.1 + 0.2 != 0.3          # the defect being guarded against


def test_the_existing_ev_receipt_uses_decimal():
    import micro_live_ev as EV
    assert isinstance(EV._d("0.1"), Decimal)
    assert EV._d("0.1") == Decimal("0.1")


def test_the_existing_maker_fill_module_names_the_float_trap():
    import maker_fill as MF
    src = open(MF.__file__).read()
    assert "float(0.1) is not 0.1" in src


def test_an_economic_threshold_is_not_decided_by_rounding_error():
    """0.1+0.2 > 0.3 is TRUE in binary float and FALSE in exact decimal."""
    assert (0.1 + 0.2) > 0.3                      # float says yes
    assert not (Decimal("0.1") + Decimal("0.2") > Decimal("0.3"))
    r = AE.quote_gate({"EXPECTED_NET_DOLLARS": "0.3"}, "0.30000000000000004")
    assert r["QUOTE"] is False


# --- Section N. The register agrees with the modules. ----------------------

def test_the_register_matches_the_modules():
    import ev_core_registers as R
    assert R.TOXICITY_V1_LABEL == TX.TOXICITY_V1
    assert R.DEEPLOB_STATUS == MZ.DEEPLOB_STATUS
    assert R.RL_STATUS == MZ.RL_STATUS
    assert R.FINAL_EDGE == AE.THE_FINAL_EDGE
    assert R.RL_PRECONDITIONS == MZ.RL_PRECONDITIONS
    assert R.BENIGN_FLOW_CAPACITY_STATUS == TX.BENIGN_FLOW_CAPACITY_STATUS
    for s, st in R.TARGET_SYSTEMS_STATUS.items():
        assert MZ.TARGET_SYSTEMS[s]["STATUS"] == st


def test_nothing_in_this_stack_trains_or_places():
    assert MZ.NOTHING_IS_TRAINED_HERE is True
    assert TX.NOTHING_IS_TRAINED_HERE is True
    assert AE.NOTHING_IS_PLACED is True
    assert XS.THIS_MODULE_CONTACTS_NOTHING is True
    assert XS.ORDER_PATH_EXISTS is False
    assert XS.MIRROR_LIVE is False


def test_the_frozen_capture_and_thresholds_are_untouched():
    import capture_quality as CQ
    import ev_core_registers as R
    assert CQ.thresholds_intact()["INTACT"] is True
    assert R.FROZEN_QUALITY_THRESHOLDS_SHA == CQ.FROZEN_QUALITY_THRESHOLDS_SHA
