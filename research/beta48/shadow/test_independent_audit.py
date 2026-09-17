"""Regression tests for the INDEPENDENT audit of 8d23621.

Eighteen defects, each reproduced against the real source before the fix.
Every test below fails on the pre-fix code and passes on the post-fix code.
"""

import pytest

import action_ev_mc as MC
import bettor_dataset as BD
import drift_trust as DT
import edge_dashboard as ED
import ev_core_registers as R
import microstructure_v1 as MS
import prior_registry as PR
from prior_registry import Dist


def _resolved(**over):
    """Every economic term resolved, so IDENTIFIED is legitimately reachable."""
    t = {"P_FILL": Dist("BETA", {"alpha": 5.0, "beta": 15.0}),
         "VALUE_IF_FILL": Dist("NORMAL", {"mu": 0.02, "sigma": 0.004}),
         "TOXICITY": Dist("NORMAL", {"mu": 0.005, "sigma": 0.002}),
         "VALUE_IF_NO_FILL_STATE": "KNOWN_ZERO",
         "FEE_STATE": "KNOWN_ZERO",
         "REBATE_STATE": "NOT_APPLICABLE",
         "INVENTORY_COST_STATE": "KNOWN_ZERO",
         "EXIT_COST_STATE": "KNOWN_ZERO"}
    t.update(over)
    return t


# --- 1. UNKNOWN economics must never become zero. -------------------------

def test_the_exact_reported_reproduction_is_now_impossible():
    """P_FILL .5, VALUE_IF_FILL .02, TOXICITY 0, all other economics absent.

    Returned EV_MEAN = .01 with ACTION_EV_STATUS = IDENTIFIED.
    """
    r = MC.action_ev_mc("Q", {"P_FILL": 0.5, "VALUE_IF_FILL": 0.02,
                              "TOXICITY": 0.0}, draws=500)
    assert r["ACTION_EV_STATUS"] == "NOT_FULLY_IDENTIFIED"
    assert r["EV_MEAN"] == MC.NOT_IDENTIFIED
    assert r["PARTIAL_EV"] == pytest.approx(0.01)
    for t in ("VALUE_IF_NO_FILL", "FEE", "REBATE", "INVENTORY_COST",
              "EXIT_COST"):
        assert t in r["UNRESOLVED_ECONOMIC_TERMS"]


@pytest.mark.parametrize("term", ["FEE", "REBATE", "VALUE_IF_NO_FILL",
                                  "INVENTORY_COST", "EXIT_COST"])
def test_a_single_unknown_economic_term_blocks_identified(term):
    t = _resolved()
    t.pop("%s_STATE" % term)
    r = MC.action_ev_mc("Q", t, draws=300)
    assert r["ACTION_EV_STATUS"] == "NOT_FULLY_IDENTIFIED"
    assert r["BREAK_EVEN_UNKNOWN_TERM"] == term


def test_known_zero_is_the_only_state_that_contributes_a_numeric_zero():
    assert MC.CONTRIBUTES_ZERO == ("KNOWN_ZERO", "NOT_APPLICABLE")
    assert set(MC.TERM_STATES) == {
        "MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR", "KNOWN_ZERO",
        "NOT_APPLICABLE", "NOT_IDENTIFIED"}
    r = MC.action_ev_mc("Q", _resolved(), draws=300)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"
    assert r["TERM_STATES"]["FEE"] == "KNOWN_ZERO"
    assert r["TERM_STATES"]["REBATE"] == "NOT_APPLICABLE"


def test_a_state_that_contradicts_a_supplied_value_is_refused():
    r = MC.action_ev_mc("Q", _resolved(FEE=0.001, FEE_STATE="KNOWN_ZERO"),
                        draws=100)
    assert r["ACTION_EV_STATUS"] == "INVALID_MODEL_SPECIFICATION"
    assert r["TERM_STATE_CONFLICTS"][0]["WHY"] == \
        "STATE_CONTRADICTS_SUPPLIED_VALUE"


# --- 2. break_even may not zero other unknown terms. ----------------------

def test_break_even_refuses_while_other_economics_are_unknown():
    be = MC.break_even("Q", {"P_FILL": 0.5, "VALUE_IF_FILL": 0.02,
                             "TOXICITY": 0.0}, "P_FILL")
    assert be["STATUS"] == "MORE_THAN_ONE_ECONOMIC_UNKNOWN"
    for t in ("FEE", "REBATE", "VALUE_IF_NO_FILL", "INVENTORY_COST",
              "EXIT_COST"):
        assert t in be["ALSO_NOT_IDENTIFIED"]
    assert "BREAK_EVEN_VALUE" not in be


def test_break_even_solves_once_every_other_term_is_resolved():
    t = _resolved()
    t["P_FILL"] = MC.NOT_IDENTIFIED
    be = MC.break_even("Q", t, "P_FILL")
    assert be["STATUS"] == "SOLVED"
    assert 0.0 < be["BREAK_EVEN_P_FILL"] < 1.0


# --- 3. The target gate fails closed without label provenance. ------------

def test_a_target_without_its_status_column_is_refused():
    rows = [{"MID_MOVE_30S": 0.01}] * 10
    g = MS.target_scoring_gate("MID_MOVE_30S", rows)
    assert g["MAY_SCORE"] is False
    assert g["REASON"] == "LABEL_PROVENANCE_ABSENT"
    assert MS.score_baselines(rows, "MID_MOVE_30S")["STATUS"] == "REFUSED"


def test_there_is_no_not_checked_therefore_yes_path():
    for name in dir(MS):
        v = getattr(MS, name)
        if isinstance(v, str):
            assert "NOT_CHECKED_NO_LABEL_STATUS_ON_ROWS" not in v, name


def test_one_row_missing_provenance_refuses_the_whole_target():
    rows = [{"MID_MOVE_30S": 0.01, "MID_MOVE_30S_STATUS": "PRESENT"}] * 9
    rows.append({"MID_MOVE_30S": 0.01})
    assert MS.target_scoring_gate("MID_MOVE_30S", rows)["MAY_SCORE"] is False


# --- 4. build_targets enforces market identity. ---------------------------

def _interleaved():
    return [
        {"MARKET_ID": "A", "REQUEST_UTC": "2026-09-17T18:00:00Z",
         "BEST_BID": 0.40, "BEST_ASK": 0.42},
        {"MARKET_ID": "B", "REQUEST_UTC": "2026-09-17T18:00:24Z",
         "BEST_BID": 0.70, "BEST_ASK": 0.72},
        {"MARKET_ID": "A", "REQUEST_UTC": "2026-09-17T18:00:48Z",
         "BEST_BID": 0.40, "BEST_ASK": 0.42},
    ]


def test_interleaved_markets_never_label_each_other():
    rows, meta = MS.build_targets(_interleaved(), horizons=(30,))
    assert meta["GROUPED_BY_MARKET_IDENTITY"] is True
    a = [r for r in rows if r["MARKET_ID"] == "A"][0]
    assert a["MID_MOVE_30S"] != pytest.approx(0.30)
    assert a["MID_MOVE_30S"] in (None, pytest.approx(0.0))


def test_an_event_id_alone_is_not_a_market_identity():
    assert "EVENT_ID" not in MS.MARKET_IDENTITY_KEYS
    ticks = [dict(t, EVENT_ID="e1") for t in _interleaved()]
    for t in ticks:
        t.pop("MARKET_ID")
    rows, meta = MS.build_targets(ticks, horizons=(30,))
    # No market identity anywhere: treated as one series only because
    # nothing distinguishes them, and EVENT_ID is explicitly not consulted.
    assert "EVENT_ID_IS_NOT_A_MARKET_IDENTITY" in MS.__dict__


def test_partially_identified_input_is_refused_outright():
    ticks = _interleaved()
    ticks[1].pop("MARKET_ID")
    _, meta = MS.build_targets(ticks, horizons=(30,))
    assert meta["STATUS"] == "REFUSED_MIXED_IDENTITY"


# --- 5. The equal-distance tie rule is frozen and deterministic. ----------

def test_the_nearest_label_is_invariant_to_input_order():
    origin = {"MARKET_ID": "m", "DECISION_TIMESTAMP_UTC":
              "2026-09-17T18:00:00Z"}
    a = {"MARKET_ID": "m", "DECISION_TIMESTAMP_UTC": "2026-09-17T18:00:48Z",
         "TAG": "EARLIER"}
    b = {"MARKET_ID": "m", "DECISION_TIMESTAMP_UTC": "2026-09-17T18:01:12Z",
         "TAG": "LATER"}
    fwd_ab, _ = BD.forward_observation([a, b], origin[
        "DECISION_TIMESTAMP_UTC"], 60, origin=origin)
    fwd_ba, _ = BD.forward_observation([b, a], origin[
        "DECISION_TIMESTAMP_UTC"], 60, origin=origin)
    assert fwd_ab["TAG"] == fwd_ba["TAG"] == "EARLIER"
    assert BD.TIE_BREAK_RULE == \
        "MIN_TUPLE_ABS_TARGET_ERROR_THEN_OBSERVATION_TIMESTAMP"


def test_both_modules_use_the_same_frozen_tie_rule():
    assert MS.TIE_BREAK_RULE == BD.TIE_BREAK_RULE
    ticks = [{"MARKET_ID": "m",
              "REQUEST_UTC": "2026-09-17T18:0%d:%02dZ" % divmod(s, 60),
              "BEST_BID": 0.40 + i * 0.01, "BEST_ASK": 0.42 + i * 0.01}
             for i, s in enumerate((0, 48, 72))]
    fwd, meta = MS.build_targets(ticks, horizons=(60,))
    rev, _ = MS.build_targets(list(reversed(ticks)), horizons=(60,))
    assert fwd[0]["MID_MOVE_60S_REALISED_OFFSET_S"] == \
        rev[0]["MID_MOVE_60S_REALISED_OFFSET_S"] == -12.0
    assert meta["TIE_BREAK_RULE"] == BD.TIE_BREAK_RULE


# --- 6. Pareto dominance. -------------------------------------------------

def test_the_reported_counterexample_is_no_longer_dominance():
    r = MC.dominated([
        {"ACTION": "A", "EV_MEAN": 1, "EV_SD": 1,
         "CAPITAL_OCCUPANCY_MEAN": 1},
        {"ACTION": "B", "EV_MEAN": 2, "EV_SD": 0.5,
         "CAPITAL_OCCUPANCY_MEAN": 100}])
    a = [x for x in r["ROWS"] if x["ACTION"] == "A"][0]
    assert a["DOMINANCE_STATUS"] == "NOT_DOMINATED"
    assert r["DOMINATED_COUNT"] == 0


def test_genuine_three_axis_dominance_is_still_detected():
    r = MC.dominated([
        {"ACTION": "A", "EV_MEAN": 1, "EV_SD": 1,
         "CAPITAL_OCCUPANCY_MEAN": 1},
        {"ACTION": "C", "EV_MEAN": 2, "EV_SD": 0.5,
         "CAPITAL_OCCUPANCY_MEAN": 1}])
    a = [x for x in r["ROWS"] if x["ACTION"] == "A"][0]
    assert a["DOMINANCE_STATUS"] == "DOMINATED"
    assert a["DOMINATED_BY"] == "C"


def test_an_exact_tie_on_every_axis_is_not_dominance():
    r = MC.dominated([
        {"ACTION": "A", "EV_MEAN": 1, "EV_SD": 1,
         "CAPITAL_OCCUPANCY_MEAN": 1},
        {"ACTION": "B", "EV_MEAN": 1, "EV_SD": 1,
         "CAPITAL_OCCUPANCY_MEAN": 1}])
    assert r["DOMINATED_COUNT"] == 0


def test_a_missing_comparison_axis_is_not_identified():
    r = MC.dominated([{"ACTION": "A", "EV_MEAN": 1, "EV_SD": 1},
                      {"ACTION": "B", "EV_MEAN": 2, "EV_SD": 0.5}])
    for x in r["ROWS"]:
        assert x["DOMINANCE_STATUS"] == MC.NOT_IDENTIFIED
    assert r["NOT_IDENTIFIED_COUNT"] == 2


# --- 7. Distribution domains. --------------------------------------------

def test_a_probability_centred_above_one_is_a_specification_error():
    r = MC.action_ev_mc("Q", _resolved(
        P_FILL=Dist("NORMAL", {"mu": 2.0, "sigma": 0.1})), draws=100)
    assert r["ACTION_EV_STATUS"] == "INVALID_MODEL_SPECIFICATION"
    assert r["INVALID_DOMAINS"][0]["TERM"] == "P_FILL"
    assert r["INVALID_DOMAINS"][0]["DOMAIN"] == "PROBABILITY"


def test_a_negative_capital_distribution_is_refused():
    r = MC.action_ev_mc("Q", _resolved(
        CAPITAL_REQUIRED=Dist("NORMAL", {"mu": -50.0, "sigma": 1.0}),
        OCCUPANCY_SECONDS=1800.0), draws=100)
    assert r["ACTION_EV_STATUS"] == "INVALID_MODEL_SPECIFICATION"


def test_a_negative_occupancy_distribution_is_refused():
    r = MC.action_ev_mc("Q", _resolved(
        CAPITAL_REQUIRED=40.0,
        OCCUPANCY_SECONDS=Dist("NORMAL", {"mu": -10.0, "sigma": 1.0})),
        draws=100)
    assert r["ACTION_EV_STATUS"] == "INVALID_MODEL_SPECIFICATION"


def test_an_admissible_bounded_term_declares_its_tail_mass():
    chk = MC.check_domain("P_FILL", Dist("NORMAL", {"mu": 0.5,
                                                    "sigma": 0.05}))
    assert chk["VALID"] is True
    assert chk["DOMAIN_STATUS"] == "ADMITTED_WITH_TAIL_MASS_DECLARED"
    assert chk["TAIL_MASS_OUTSIDE_DOMAIN"] >= 0.0


def test_every_term_carries_a_declared_domain():
    for t in MC.ALL_EV_TERMS:
        assert t in MC.TERM_DOMAINS, t
        assert MC.TERM_DOMAINS[t] in MC.DOMAINS


# --- 8. Artifact binding by EVALUATION_ID. --------------------------------

def _fs(**over):
    return _resolved(FILL_SELECTION_EFFECT=Dist("NORMAL", {"mu": 0.0,
                                                           "sigma": 0.004}),
                     **over)


def test_the_same_action_with_different_terms_cannot_satisfy_exposure():
    a = _fs()
    b = _fs(PRICE=0.41)            # same ACTION, different economic state
    row = MC.action_ev_mc("POST_BID", a, draws=300)
    stripped = {k: v for k, v in row.items()
                if k not in MC.REQUIRED_FILL_SELECTION_EXPOSURE}
    foreign = MC.sensitivity("POST_BID", b)
    chk = MC.fill_selection_exposure(stripped, foreign)
    assert chk["REPORT_OK"] is False
    assert "SENSITIVITY" in chk["UNBOUND_ARTIFACTS"]
    own = MC.sensitivity("POST_BID", a)
    assert MC.fill_selection_exposure(stripped, own)["REPORT_OK"] is True


def test_the_evaluation_id_covers_every_declared_field():
    for f in ("ACTION", "PRICE", "SIZE", "TERM_MANIFEST_SHA",
              "PRIOR_VERSION_MANIFEST_SHA", "CONVENTION", "MODEL_VERSION",
              "DECISION_ID"):
        assert f in MC.EVALUATION_ID_FIELDS


def test_a_changed_term_changes_the_evaluation_id():
    a = MC.evaluation_id("POST_BID", _fs())
    b = MC.evaluation_id("POST_BID", _fs(FEE=0.001))
    assert a["EVALUATION_ID"] != b["EVALUATION_ID"]


# --- 9. Bayesian counts and effective N. ----------------------------------

def test_a_fractional_beta_update_is_refused_not_truncated():
    r = PR.update_beta(PR.beta_from_mean_n(0.2, 10), successes=0.9,
                       trials=1.9)
    assert r["STATUS"] == "INVALID_COUNTS"
    assert r["REQUIRED"] == "NON_NEGATIVE_INTEGER"


@pytest.mark.parametrize("s,n", [(True, True), ("4", "5"), (-1, 5), (4.0, 5)])
def test_non_integer_counts_are_refused(s, n):
    assert PR.update_beta(PR.beta_from_mean_n(0.2, 10), s,
                          n)["STATUS"] == "INVALID_COUNTS"


def test_successes_may_not_exceed_trials():
    assert PR.update_beta(PR.beta_from_mean_n(0.2, 10), 9,
                          5)["STATUS"] == "INVALID_DATA"


def test_a_normal_update_validates_its_own_n():
    d = PR.Dist("NORMAL", {"mu": 0.0, "sigma": 0.01})
    assert PR.update_normal(d, 0.004, 0.01, 1.5)["STATUS"] == "INVALID_COUNTS"
    assert PR.update_normal(d, 0.004, 0.0, 10)["STATUS"] == "INVALID_DATA"
    assert PR.update_normal(d, 0.004, 0.01, 10)["UNCERTAINTY_FELL_BY"] > 0


def test_posterior_precision_is_unidentified_without_a_dependence_model():
    r = PR.update_beta(PR.beta_from_mean_n(0.2, 10), 40, 50)
    assert r["POSTERIOR_PRECISION_STATUS"] == \
        "NOT_IDENTIFIED_PENDING_DEPENDENCE_MODEL"
    for f in PR.EFFECTIVE_N_FIELDS:
        assert f in r["N_PROVENANCE"], f


def test_raw_rows_are_carried_apart_from_independent_events():
    p = PR.effective_n(raw_rows=9000, markets=6, independent_events=3,
                       event_hours=4.5)
    assert p["RAW_ROWS"] == 9000 and p["INDEPENDENT_EVENTS"] == 3
    assert p["EFFECTIVE_N"] == PR.NOT_IDENTIFIED
    assert p["POSTERIOR_PRECISION_STATUS"] == \
        "NOT_IDENTIFIED_PENDING_DEPENDENCE_MODEL"


# --- 10. PRIOR_SHA over the final object. ---------------------------------

def test_every_shipped_prior_recomputes_to_its_own_digest():
    for name, row in PR.PRIOR_REGISTRY.items():
        v = PR.verify_prior_sha(row)
        assert v["MATCHES"] is True, name
        assert v["PRIOR_SHA_STATUS"] == "SEALED_OVER_FINAL_OBJECT"


def test_the_hash_covers_the_classification_block():
    import copy
    row = copy.deepcopy(PR.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"])
    assert PR.verify_prior_sha(row)["MATCHES"] is True
    row["PRIOR_STRENGTH"] = "STRONG"
    row["DIRECTION_ASSUMED"] = "YES"
    assert PR.verify_prior_sha(row)["MATCHES"] is False


# --- 11. Drift is a tri-state enum. ---------------------------------------

def test_an_undetermined_monitor_cannot_fire_the_drift_response():
    rows = [{"MONITOR": "M1", "DRIFT_DETECTED": "NOT_IDENTIFIED",
             "DRIFT_STATUS": "NOT_IDENTIFIED"}]
    r = DT.drift_response(rows)
    assert r["DRIFT_DETECTED"] is False
    assert r["DRIFT_STATUS"] == "NOT_IDENTIFIED"
    assert r["RESPONSES"] == []
    assert "M1" in r["UNDETERMINED_MONITORS"]
    assert "DO_NOT_TREAT_AS_STABLE" in r["UNDETERMINED_POLICY"]


def test_only_detected_enters_the_fired_set():
    rows = [{"MONITOR": "U", "DRIFT_STATUS": "NOT_IDENTIFIED"},
            {"MONITOR": "D", "DRIFT_STATUS": "DETECTED",
             "DRIFT_KIND": "SUDDEN"},
            {"MONITOR": "N", "DRIFT_STATUS": "NOT_DETECTED"}]
    r = DT.drift_response(rows)
    assert r["MONITORS_FIRED"] == ["D"]
    assert r["UNDETERMINED_MONITORS"] == ["U"]


def test_the_enum_has_exactly_three_states():
    assert set(DT.DRIFT_STATUSES) == {"DETECTED", "NOT_DETECTED",
                                      "NOT_IDENTIFIED"}


# --- 12. Relative value needs canonical label provenance. -----------------

def test_relative_value_refuses_a_hand_built_target_column():
    rows = [{"EVENT_KEY": "e%d" % i, "RESIDUAL_T": 0.01,
             "TARGET_CHANGE_30S": -0.01} for i in range(12)]
    out = MS.relative_value_test(rows, horizons=(30,))
    assert out["BY_HORIZON"]["30S"]["STATUS"] == "REFUSED"
    assert out["BY_HORIZON"]["30S"]["REASON"] == "LABEL_PROVENANCE_ABSENT"


def test_relative_value_proceeds_once_provenance_is_present():
    rows = [{"EVENT_KEY": "e%d" % i, "RESIDUAL_T": (i % 9 - 4) / 100.0,
             "TARGET_CHANGE_30S": -0.3 * (i % 9 - 4) / 100.0,
             "TARGET_CHANGE_30S_STATUS": "PRESENT"} for i in range(60)]
    out = MS.relative_value_test(rows, horizons=(30,), min_coverage_pct=50.0)
    assert out["BY_HORIZON"]["30S"]["STATUS"] == "MEASURED"


# --- 13. The 30/200 evidential ladder is gone. ----------------------------

@pytest.mark.parametrize("n", [0, 5, 29, 30, 150, 199, 200, 5000])
def test_no_event_count_ever_assigns_detected_or_replicated(n):
    row = ED.edge_row("FILL_EDGE", estimate=0.001, oos_event_n=n)
    assert row["STATUS"] == "NOT_IDENTIFIED"
    assert row["SAMPLE_SIZE_TIER"] in ED.SAMPLE_SIZE_TIERS


def test_the_tiers_are_non_evaluative():
    for t in ED.SAMPLE_SIZE_TIERS:
        assert "DETECT" not in t and "REPLICAT" not in t


def test_detected_and_replicated_say_what_they_require():
    assert "BEFORE looking" in ED.WHAT_DETECTED_REQUIRES
    assert "PROSPECTIVE" in ED.WHAT_REPLICATED_REQUIRES


# --- 14. Interaction power is planning-only. ------------------------------

def test_the_power_calculation_is_classified_planning_only():
    assert ED.INTERACTION_POWER_STATUS == "PLANNING_APPROXIMATION_ONLY"
    row = ED.interaction("X", 500, effect_size_of_interest=0.004,
                         event_level_variance=0.0004,
                         interaction_degrees_of_freedom=1,
                         regime_support={"REGIMES_REQUIRED": 3,
                                         "REGIMES_WITH_SUPPORT": 3},
                         power_target={"ALPHA": 0.05, "POWER": 0.80})
    assert row["INTERACTION_POWER_STATUS"] == "PLANNING_APPROXIMATION_ONLY"
    assert "design matrix" in ED.WHAT_FINAL_ADMISSION_NEEDS


# --- 15. Variance attribution is not EVPI. --------------------------------

def test_variance_attribution_does_not_claim_value_of_information():
    v = MC.uncertainty_variance_attribution("Q", _resolved(), draws=400)
    assert v["TOP_VARIANCE_ATTRIBUTION_TERM"] in MC.EV_TERMS
    assert v["TOP_VALUE_OF_INFORMATION_TERM"] == MC.NOT_IDENTIFIED
    assert "NOT the expected value of perfect information" in \
        MC.VARIANCE_ATTRIBUTION_IS_NOT_EVPI


def test_evpi_and_evsi_return_their_reason_never_a_number():
    e = MC.expected_value_of_perfect_information()
    s = MC.expected_value_of_sample_information()
    assert e["EVPI"] == MC.NOT_IDENTIFIED and s["EVSI"] == MC.NOT_IDENTIFIED
    assert e["STATUS"].startswith("NOT_IMPLEMENTED")
    assert "OBSERVATION_MODEL" in s["REQUIRES"]


# --- 16. Queue mechanism dimensional contract. ----------------------------

def test_the_queue_mechanism_is_labelled_uncalibrated():
    r = PR.p_fill_from_mechanism(
        queue_ahead=10, trade_intensity_per_s=2.0, horizon_s=30,
        intensity_evidence_class=PR.ESTIMATED_PRIOR)
    assert r["QUEUE_MECHANISM_STATUS"] == "UNCALIBRATED_TOY_MECHANISM"
    assert r["MECHANISM"].endswith("UNCALIBRATED")
    assert "not a measured P_FILL" in r["NEVER_CALL_THIS_MEASURED_P_FILL"]


def test_the_queue_units_are_frozen_and_the_mismatch_named():
    c = PR.QUEUE_DIMENSIONAL_CONTRACT
    for k in ("QUEUE_AHEAD", "TRADE_INTENSITY_PER_SECOND",
              "POISSON_EVENT_UNIT", "THE_MISMATCH", "WHAT_WOULD_FIX_IT"):
        assert k in c
    assert "one share" in c["THE_MISMATCH"]


# --- 17. Baseline units and common evaluation support. --------------------

def test_a_dimensionless_baseline_refuses_without_a_declared_scale():
    feats = {"ORDER_BOOK_IMBALANCE": 0.5, "ORDER_FLOW_IMBALANCE": 0.5}
    for b in MS.DIMENSIONLESS_BASELINES:
        assert MS.baseline_prediction(b, feats) is None
        assert MS.baseline_prediction(b, feats, scale=0.01) is None
        assert MS.baseline_prediction(
            b, feats, scale=0.01,
            scale_source="CALIBRATED_ON_TRAINING_EVENTS") is not None


def test_the_evaluation_fold_is_never_an_admissible_scale_source():
    assert "EVALUATION" not in " ".join(MS.BASELINE_SCALE_SOURCES)
    assert "the fold the baseline is scored against" in \
        MS.NEVER_CALIBRATE_ON_THE_EVALUATION_FOLD


def test_scores_are_reported_on_a_common_evaluation_support():
    # Row 1 is priceable by every predictor; row 2 is not (no imbalance).
    rows = [{"MID_MOVE_30S": 0.01, "MID_MOVE_30S_STATUS": "PRESENT",
             "ORDER_BOOK_IMBALANCE": 0.5, "ORDER_FLOW_IMBALANCE": 0.4,
             "MICROPRICE_MINUS_MID": 0.001, "_PREV_MOVE": 0.002},
            {"MID_MOVE_30S": -0.01, "MID_MOVE_30S_STATUS": "PRESENT",
             "ORDER_BOOK_IMBALANCE": None, "ORDER_FLOW_IMBALANCE": None,
             "MICROPRICE_MINUS_MID": -0.002, "_PREV_MOVE": -0.001}]
    out = MS.score_baselines(
        rows, "MID_MOVE_30S", min_coverage_pct=50.0, baseline_scale=0.01,
        baseline_scale_source="PREDECLARED_FIXED_TRANSFORMATION")
    assert out["SCORABLE_ROWS"] == 2
    assert out["COMMON_EVALUATION_SUPPORT_ROWS"] == 1
    b0 = out["BY_PREDICTOR"]["B0_NO_CHANGE"]
    assert b0["N_SCORED"] == 2
    assert b0["ON_COMMON_SUPPORT"]["N_SCORED"] == 1
    assert out["BY_PREDICTOR"]["B4_SIMPLE_BOOK_IMBALANCE"][
        "ABSTAINED_ROWS"] == 1


def test_an_abstaining_predictor_cannot_win_on_an_easier_subset():
    """The whole point of the common support: a thin predictor is visible."""
    rows = [{"MID_MOVE_30S": 0.01 if i % 2 else 0.20,
             "MID_MOVE_30S_STATUS": "PRESENT",
             "MICROPRICE_MINUS_MID": (0.01 if i % 2 else None),
             "_PREV_MOVE": 0.0,
             "ORDER_BOOK_IMBALANCE": 0.1, "ORDER_FLOW_IMBALANCE": 0.1}
            for i in range(10)]
    out = MS.score_baselines(
        rows, "MID_MOVE_30S", min_coverage_pct=50.0, baseline_scale=0.01,
        baseline_scale_source="PREDECLARED_FIXED_TRANSFORMATION")
    thin = out["BY_PREDICTOR"]["B2_MICROPRICE"]
    assert thin["ABSTAINED_ROWS"] == 5          # skipped every hard row
    assert thin["ON_COMMON_SUPPORT"]["N_SCORED"] == 5
    assert out["COMMON_EVALUATION_SUPPORT_ROWS"] == 5


# --- The register and the standing rails. ---------------------------------

def test_the_register_carries_every_audit_status():
    d = R.describe()
    for k in ("SILENT_ZERO_STATUS", "BREAK_EVEN_MULTI_UNKNOWN_STATUS",
              "TARGET_GATE_FAIL_CLOSED_STATUS",
              "BUILD_TARGETS_IDENTITY_STATUS", "HORIZON_TIE_BREAK_STATUS",
              "PARETO_DOMINANCE_STATUS", "DISTRIBUTION_DOMAIN_STATUS",
              "FILL_SELECTION_ARTIFACT_BINDING_STATUS",
              "BAYESIAN_COUNT_VALIDATION_STATUS",
              "POSTERIOR_EFFECTIVE_N_STATUS",
              "PRIOR_HASH_FINAL_OBJECT_STATUS", "DRIFT_TRISTATE_STATUS",
              "RELATIVE_VALUE_PROVENANCE_GATE_STATUS",
              "EDGE_STATUS_LADDER_STATUS",
              "INTERACTION_POWER_CLASSIFICATION",
              "UNCERTAINTY_VARIANCE_ATTRIBUTION_STATUS",
              "TRUE_EVPI_EVSI_STATUS",
              "QUEUE_DIMENSIONAL_CONTRACT_STATUS",
              "BASELINE_UNIT_CONTRACT_STATUS",
              "COMMON_EVALUATION_SUPPORT_STATUS"):
        assert k in d, k


def test_the_frozen_capture_and_rails_are_untouched():
    import capture_quality as CQ
    assert CQ.thresholds_intact()["INTACT"] is True
    assert CQ.FROZEN_QUALITY_THRESHOLDS_SHA == (
        "70e6ddda88ad4141ae3ed58691d8322cea4bf37a83c89efd481792c47243e7fb")
    assert R.TRAINING_PERFORMED == "NO"
    assert R.PARAMETER_TUNING_PERFORMED == "NO"
    assert R.MODEL_SELECTION_PERFORMED == "NO"
    assert R.LIVE_ORDER_ACTIVITY == "NONE"
    assert MC.SHADOW_ONLY is True and MC.NO_ORDER_IS_PLACED is True
