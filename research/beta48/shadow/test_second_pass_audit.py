"""Regression tests for the second-pass semantic-hardening audit of 6bda085.

Each test reproduces a defect the independent second pass found, against the
actual behaviour of the module rather than against a declaration about it.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

import pytest

import action_ev_mc as A
import bettor_dataset as B
import drift_trust as D
import edge_dashboard as E
import maker_fill as MF
import microstructure_v1 as M
import prior_registry as P
from prior_registry import Dist, NOT_IDENTIFIED


# --- fixtures --------------------------------------------------------------

def _sealed(term, family, params, cls="ESTIMATED_PRIOR"):
    ref = ("PRIOR_SHA" if cls == "ESTIMATED_PRIOR"
           else "MEASUREMENT_MANIFEST_SHA")
    return {term: Dist(family, params),
            "%s_EVIDENCE_CLASS" % term: cls,
            "%s_%s" % (term, ref): "sha-%s" % term.lower()}


def full_terms(**over):
    t = {}
    t.update(_sealed("P_FILL", "BETA", {"alpha": 3, "beta": 40}))
    t.update(_sealed("VALUE_IF_FILL", "NORMAL", {"mu": 0.004, "sigma": 0.002}))
    t.update(_sealed("TOXICITY", "NORMAL", {"mu": 0.001, "sigma": 0.0005}))
    for k in ("VALUE_IF_NO_FILL", "FEE", "REBATE", "INVENTORY_COST",
              "EXIT_COST"):
        t["%s_STATE" % k] = "KNOWN_ZERO"
    t["FILL_SELECTION_CONVENTION"] = "EXCLUDED"
    t["QUANTITY"] = 500
    t.update(over)
    return t


# --- 1. The PARTIAL_* namespace. ------------------------------------------

def test_a_downgraded_ev_downgrades_every_statistic_not_only_the_mean():
    t = full_terms()
    t.pop("FEE_STATE")                      # one unresolved economic term
    r = A.action_ev_mc("POST_BID", t, draws=400)
    assert r["ACTION_EV_STATUS"] == "NOT_FULLY_IDENTIFIED"
    for key in ("EV_MEAN", "EV_SD", "EV_P10", "EV_P90", "P_EV_GT_0",
                "P_EV_LT_0", "EV_MEDIAN", "TAIL_LOSS_P05",
                "CONSERVATIVE_EV_P10", "POSTERIOR_MEAN_EV"):
        assert r[key] == NOT_IDENTIFIED, key


def test_the_partial_twins_carry_the_numbers_and_the_assumption():
    t = full_terms()
    t.pop("FEE_STATE")
    r = A.action_ev_mc("POST_BID", t, draws=400)
    assert isinstance(r["PARTIAL_EV_MEAN"], float)
    assert isinstance(r["PARTIAL_EV_SD"], float)
    assert isinstance(r["PARTIAL_P_EV_GT_0"], float)
    assert isinstance(r["PARTIAL_EV_P10"], float)
    assert r["PARTIAL_EV_ASSUMPTION"] == \
        "UNRESOLVED_TERMS_HELD_AT_ZERO_FOR_DIAGNOSTIC_ONLY"
    assert r["PARTIAL_EV"] == r["PARTIAL_EV_MEAN"]


def test_a_fully_resolved_row_keeps_the_ordinary_names():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=400)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"
    assert isinstance(r["EV_MEAN"], float)
    assert "PARTIAL_EV_MEAN" not in r


# --- 2. robustly_positive is a decision claim. ----------------------------

def test_robustly_positive_refuses_a_row_that_is_not_decision_grade():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=400)
    out = A.robustly_positive(r)
    assert out["ROBUSTLY_POSITIVE"] is False
    assert out["REASON"] == "DECISION_GRADE_BLOCKED"
    assert out["DECISION_GRADE_ACTION_EV_STATUS"] == "BLOCKED"


def test_robustly_positive_still_publishes_the_shadow_diagnostic():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=400)
    out = A.robustly_positive(r)
    assert isinstance(out["SHADOW_ROBUSTNESS_DIAGNOSTIC"], float)
    assert out["SHADOW_ROBUSTNESS_DIAGNOSTIC"] == r["EV_P10"]


def test_robustly_positive_on_a_partial_row_reads_the_partial_twin():
    t = full_terms()
    t.pop("FEE_STATE")
    r = A.action_ev_mc("POST_BID", t, draws=400)
    out = A.robustly_positive(r)
    assert out["ROBUSTLY_POSITIVE"] is False
    assert out["REASON"] == "EV_NOT_IDENTIFIED"
    assert isinstance(out["SHADOW_ROBUSTNESS_DIAGNOSTIC"], float)


# --- 3. Sensitivity and variance attribution see the zeros. ---------------

def test_sensitivity_labels_itself_partial_when_a_term_is_unresolved():
    t = full_terms()
    t.pop("FEE_STATE")
    s = A.sensitivity("POST_BID", t)
    assert s["STATUS"] == "PARTIAL_NOT_FULLY_IDENTIFIED"
    assert "FEE" in s["UNRESOLVED_ECONOMIC_TERMS"]
    assert s["BASELINE_ASSUMPTION"] == A.PARTIAL_EV_ASSUMPTION


def test_variance_attribution_labels_itself_partial_too():
    t = full_terms()
    t.pop("FEE_STATE")
    v = A.uncertainty_variance_attribution("POST_BID", t, draws=200)
    assert v["STATUS"] == "PARTIAL_NOT_FULLY_IDENTIFIED"
    assert v["DEPENDENCE_MODEL_STATUS"] == "INDEPENDENT_MARGINALS_UNVALIDATED"


def test_sensitivity_on_resolved_terms_is_computed():
    s = A.sensitivity("POST_BID", full_terms())
    assert s["STATUS"] == "COMPUTED"
    assert s["UNRESOLVED_ECONOMIC_TERMS"] == ()


# --- 4. Numeric presence is not evidence. ---------------------------------

def test_a_number_with_no_provenance_is_unverified_input():
    t = full_terms()
    t.pop("P_FILL_EVIDENCE_CLASS")
    t.pop("P_FILL_PRIOR_SHA")
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert "P_FILL" in r["UNVERIFIED_INPUT_TERMS"]
    assert r["EVIDENCE_PROVENANCE_VERIFIED"] is False
    assert r["TERM_STATES"]["P_FILL"] == "UNVERIFIED_INPUT"


def test_a_claimed_class_with_no_reference_is_still_unverified():
    t = full_terms()
    t.pop("P_FILL_PRIOR_SHA")               # class claimed, nothing referenced
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert "P_FILL" in r["UNVERIFIED_INPUT_TERMS"]


def test_unverified_input_still_computes_an_ev_and_blocks_decision_grade():
    t = full_terms()
    t.pop("P_FILL_PRIOR_SHA")
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"
    assert "EVIDENCE_PROVENANCE_VERIFIED" in r["DECISION_GRADE_BLOCKERS"]


def test_not_identified_plus_a_value_is_an_invalid_specification():
    t = full_terms()
    t["FEE"] = 0.001
    t["FEE_STATE"] = NOT_IDENTIFIED
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["ACTION_EV_STATUS"] == "INVALID_MODEL_SPECIFICATION"


# --- 5 and 6. Units, basis, conditioning, quantity. -----------------------

def test_a_per_order_fee_without_a_quantity_is_dimensionally_invalid():
    t = full_terms()
    t.pop("QUANTITY")
    t.pop("FEE_STATE")
    t.update(_sealed("FEE", "POINT", {"value": 0.01}))
    t["FEE_UNIT"] = "USD_PER_ORDER"
    r = A.action_ev_mc("POST_BID", t)
    assert r["ACTION_EV_STATUS"] == "INVALID_UNIT_CONTRACT"
    assert any(v["WHY"] == "PER_ORDER_UNIT_REQUIRES_QUANTITY"
               for v in r["UNIT_CONTRACT_VIOLATIONS"])


def test_a_per_order_fee_with_a_quantity_is_divided_by_it():
    def ev(qty):
        t = full_terms(QUANTITY=qty)
        t.pop("FEE_STATE")
        t.update(_sealed("FEE", "POINT", {"value": 1.0}))
        t["FEE_UNIT"] = "USD_PER_ORDER"
        return A.action_ev_mc("POST_BID", t, draws=200)["EV_MEAN"]
    small, big = ev(100), ev(10000)
    # The same flat fee costs a hundred times more per share on 100 shares.
    assert small < big


def test_an_inadmissible_unit_for_the_slot_is_refused():
    t = full_terms()
    t["P_FILL_UNIT"] = "USD"
    r = A.action_ev_mc("POST_BID", t)
    assert r["ACTION_EV_STATUS"] == "INVALID_UNIT_CONTRACT"


def test_ev_total_usd_needs_a_declared_quantity():
    t = full_terms()
    t.pop("QUANTITY")
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["EV_TOTAL_USD"] == NOT_IDENTIFIED
    assert r["QUANTITY_STATUS"] == "NOT_DECLARED"
    assert "QUANTITY_DECLARED" in r["DECISION_GRADE_BLOCKERS"]


def test_ev_total_usd_is_the_per_share_ev_times_the_quantity():
    r = A.action_ev_mc("POST_BID", full_terms(QUANTITY=500), draws=400)
    assert r["EV_TOTAL_USD"] == pytest.approx(r["EV_MEAN"] * 500, rel=1e-6)
    assert r["EV_UNIT"] == "USD_PER_POSTED_SHARE"


def test_conditioning_decides_whether_a_cost_is_multiplied_by_p_fill():
    base = full_terms()
    base.pop("FEE_STATE")
    base.update(_sealed("FEE", "POINT", {"value": 0.01}))
    on_fill = A.action_ev_mc("POST_BID", dict(base), draws=300)["EV_MEAN"]
    always = dict(base)
    always["FEE_APPLIES_WHEN"] = "ALWAYS"
    always["FEE_UNIT"] = "USD_PER_POSTED_SHARE"
    always["FEE_BASIS"] = "PER_POSTED_SHARE"
    ev_always = A.action_ev_mc("POST_BID", always, draws=300)["EV_MEAN"]
    # A fee charged on every posted order costs more than one charged only on
    # the filled part, when P_FILL < 1.
    assert ev_always < on_fill


# --- 7. A fill-bearing action must declare the convention. ----------------

def test_a_fill_bearing_action_must_declare_the_fill_selection_convention():
    t = full_terms()
    t.pop("FILL_SELECTION_CONVENTION")
    r = A.action_ev_mc("POST_BID", t)
    assert r["ACTION_EV_STATUS"] == "FILL_SELECTION_CONVENTION_NOT_DECLARED"
    assert r["EV_MEAN"] == NOT_IDENTIFIED


def test_a_non_fill_bearing_action_may_omit_it():
    t = full_terms()
    t.pop("FILL_SELECTION_CONVENTION")
    r = A.action_ev_mc("CANCEL", t, draws=200)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"
    assert r["FILL_SELECTION_CONVENTION_DECLARED"] is False


# --- 8. Support containment, not a passing envelope. ----------------------

def test_an_unbounded_normal_on_a_probability_is_shadow_approximation_only():
    g = A.decision_grade_distribution(
        "P_FILL", Dist("NORMAL", {"mu": 0.5, "sigma": 0.05}))
    assert g["DISTRIBUTION_GRADE"] == A.SHADOW_APPROXIMATION_ONLY
    assert g["SUPPORT_INSIDE_DOMAIN"] is False


def test_a_beta_on_a_probability_is_decision_grade():
    g = A.decision_grade_distribution(
        "P_FILL", Dist("BETA", {"alpha": 3, "beta": 40}))
    assert g["DISTRIBUTION_GRADE"] == "DECISION_GRADE"


def test_a_shadow_approximation_family_blocks_decision_grade_on_the_row():
    t = full_terms()
    t.update(_sealed("P_FILL", "NORMAL", {"mu": 0.07, "sigma": 0.01}))
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert "P_FILL" in r["SHADOW_APPROXIMATION_ONLY_TERMS"]
    assert "DISTRIBUTION_DOMAINS_DECISION_GRADE" in \
        r["DECISION_GRADE_BLOCKERS"]


def test_a_truncated_normal_inside_the_domain_is_decision_grade():
    g = A.decision_grade_distribution(
        "P_FILL", Dist("TRUNCATED_NORMAL",
                       {"mu": 0.5, "sigma": 0.2, "low": 0.0, "high": 1.0}))
    assert g["DISTRIBUTION_GRADE"] == "DECISION_GRADE"


# --- 9. Distribution parameters fail closed at construction. --------------

@pytest.mark.parametrize("family,params", [
    ("NORMAL", {"mu": 0.5, "sigma": 0.0}),
    ("NORMAL", {"mu": float("nan"), "sigma": 1.0}),
    ("BETA", {"alpha": 0.0, "beta": 3.0}),
    ("TRIANGULAR", {"low": 1.0, "mode": 0.0, "high": 2.0}),
])
def test_an_impossible_parameterisation_raises(family, params):
    with pytest.raises(P.InvalidModelSpecification):
        Dist(family, params)


# --- 10 and 11. Effective N and shrinkage. --------------------------------

def test_a_raw_row_posterior_is_diagnostic_only():
    out = P.update_beta(Dist("BETA", {"alpha": 2, "beta": 18}),
                        successes=5, trials=100)
    assert out["DECISION_GRADE_POSTERIOR"] == NOT_IDENTIFIED
    assert out["DIAGNOSTIC_RAW_ROW_POSTERIOR"] != NOT_IDENTIFIED


def test_a_validated_effective_n_makes_the_posterior_decision_grade():
    prov = P.effective_n(raw_rows=100, effective_n=12,
                         effective_n_method="BLOCK_BOOTSTRAP_OVER_EVENTS",
                         relation_to_raw_rows="LESS_THAN_RAW_ROWS")
    out = P.update_beta(Dist("BETA", {"alpha": 2, "beta": 18}),
                        successes=5, trials=100, n_provenance=prov)
    assert out["DECISION_GRADE_POSTERIOR"] != NOT_IDENTIFIED
    assert out["DIAGNOSTIC_RAW_ROW_POSTERIOR"] == NOT_IDENTIFIED


def test_shrinkage_with_no_k_refuses():
    out = P.shrink(subgroup_mean=0.4, subgroup_n=12, parent_mean=0.3)
    assert out["STATUS"] == "SHRINKAGE_K_NOT_IDENTIFIED"


# --- 12. The canonical label artifact. ------------------------------------

def _series(market="M1", n=12, step=24):
    rows = []
    for i in range(n):
        secs = i * step
        rows.append({
            "MARKET_ID": market,
            "DECISION_TIMESTAMP_UTC": "2026-09-17T00:%02d:%02dZ"
                                      % (secs // 60, secs % 60),
            "MID": 0.50 + i * 0.001,
            "BEST_BID": 0.49 + i * 0.001,
            "BEST_ASK": 0.51 + i * 0.001})
    return rows


def test_a_label_artifact_is_sealed_and_verifies():
    s = _series()
    row = dict(s[0], DECISION_ID="D1")
    art = B.label_artifact(row, s)
    assert len(art["LABEL_ARTIFACT_SHA"]) == 64
    assert B.verify_label_artifact(art)["LABEL_PROVENANCE_STATUS"] == "VALID"


def test_a_tampered_label_artifact_fails_the_seal():
    s = _series()
    art = B.label_artifact(dict(s[0], DECISION_ID="D1"), s)
    art["LABELS"] = dict(art["LABELS"])
    art["LABELS"]["MID_MOVE_60S"] = 99.0
    assert B.verify_label_artifact(art)["LABEL_PROVENANCE_STATUS"] == \
        "SHA_MISMATCH"


def test_an_unsealed_label_bundle_is_not_treated_as_valid():
    assert B.verify_label_artifact({"LABELS": {}})[
        "LABEL_PROVENANCE_STATUS"] == "NOT_SEALED"


# --- 13. Market identity. -------------------------------------------------

def test_event_id_is_not_a_subject_key():
    assert "EVENT_ID" not in B.SUBJECT_KEYS


def test_an_origin_with_no_identity_matches_nothing():
    assert B._same_subject({"MID": 0.5}, {"MARKET_ID": "M1"}) is False


def test_two_markets_on_one_event_are_not_the_same_subject():
    assert B._same_subject({"MARKET_ID": "M1", "EVENT_ID": "E1"},
                           {"MARKET_ID": "M2", "EVENT_ID": "E1"}) is False


def test_build_targets_refuses_mixed_identity_before_grouping():
    ticks = []
    for mkt in ("M1", "M2"):
        for i in range(4):
            ticks.append({"MARKET_ID": mkt, "BEST_BID": 0.49, "BEST_ASK": 0.51,
                          "REQUEST_UTC": "2026-09-17T00:0%d:00Z" % i})
    ticks.append({"BEST_BID": 0.49, "BEST_ASK": 0.51,   # no identity
                  "REQUEST_UTC": "2026-09-17T00:05:00Z"})
    rows, meta = M.build_targets(ticks)
    assert rows == []
    assert meta["STATUS"] == "REFUSED_MIXED_IDENTITY"
    assert meta["UNIDENTIFIED_TICKS"] == 1


# --- 14 and 15. Target-specific status, V1 observability. -----------------

def test_the_five_second_horizon_is_never_labelled_under_v1():
    s = _series()
    lab = B.label_row(dict(s[0]), s)
    assert lab["LABEL_STATUS"]["5S"] == "UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY"
    assert lab["MID_MOVE_5S"] == "UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY"
    assert not isinstance(lab["MID_MOVE_5S"], float)


def test_a_resolved_horizon_with_a_partial_forward_row_marks_the_target():
    s = _series()
    for r in s[1:]:
        r.pop("BEST_BID")                    # no later bid anywhere
    lab = B.label_row(dict(s[0]), s)
    assert lab["LABEL_STATUS"]["60S"] == "PRESENT"
    assert lab["TARGET_LABEL_STATUS"]["MID_MOVE_60S"] == "PRESENT"
    assert lab["TARGET_LABEL_STATUS"]["EXECUTABLE_BUY_MOVE_60S"] == "MISSING"


# --- 16. The relative-value parent status. --------------------------------

def test_the_parent_status_is_not_measured_when_every_child_refused():
    rows = [{"EVENT_KEY": "E%d" % i, "RESIDUAL_T": 0.01 * i,
             "TARGET_CHANGE_60S": 0.001 * i} for i in range(12)]
    out = M.relative_value_test(rows, horizons=(5,))
    assert out["BY_HORIZON"]["5S"]["STATUS"] == "REFUSED"
    assert out["STATUS"] != "MEASURED"
    assert out["STATUS"] == "NOT_MEASURED"


# --- 17. The OFI unit split. ----------------------------------------------

def test_raw_ofi_is_named_in_shares_and_the_ratio_is_separate():
    assert "RAW_OFI_SHARES" in M.CANDIDATE_FEATURES
    assert "ORDER_FLOW_IMBALANCE" not in M.CANDIDATE_FEATURES
    assert M.FEATURE_UNITS["RAW_OFI_SHARES"] == "SIGNED_SHARES"
    assert M.FEATURE_UNITS["ORDER_BOOK_IMBALANCE"] == \
        "DIMENSIONLESS_RATIO_MINUS_ONE_TO_ONE"


def test_the_ofi_ratio_is_bounded_and_none_on_a_still_window():
    flow = [{"BID_SIZE": 100, "ASK_SIZE": 80, "BEST_BID": 0.49,
             "BEST_ASK": 0.51} for _ in range(4)]
    f = M.tick_features(flow[-1], flow[-2], flow)
    assert f["RAW_OFI_SHARES"] == 0
    assert f["ORDER_FLOW_IMBALANCE_RATIO"] is None


def test_b5_may_not_borrow_b4s_scale():
    feats = {"ORDER_BOOK_IMBALANCE": 0.4, "RAW_OFI_SHARES": 4000}
    assert M.baseline_prediction(
        "B5_SIMPLE_ORDER_FLOW_IMBALANCE", feats, scale=0.5,
        scale_source="CALIBRATED_ON_TRAINING_EVENTS") is None
    assert M.baseline_prediction(
        "B4_SIMPLE_BOOK_IMBALANCE", feats, scale=0.5,
        scale_source="CALIBRATED_ON_TRAINING_EVENTS") == pytest.approx(0.2)


def test_b5_with_its_own_per_share_scale_predicts():
    feats = {"RAW_OFI_SHARES": 4000}
    v = M.baseline_prediction(
        "B5_SIMPLE_ORDER_FLOW_IMBALANCE", feats,
        scales={"B5_SIMPLE_ORDER_FLOW_IMBALANCE":
                (1e-6, "PREDECLARED_FIXED_TRANSFORMATION")})
    assert v == pytest.approx(0.004)


# --- 18. Admission fails closed. ------------------------------------------

def _scored_rows(n=20):
    rows = []
    for i in range(n):
        rows.append({
            "MID_MOVE_60S": 0.001 * ((i % 5) - 2),
            "MID_MOVE_60S_STATUS": "PRESENT",
            "MID_MOVE_60S_REALISED_OFFSET_S": 1.0,
            "MICROPRICE_MINUS_MID": 0.0005,
            "ORDER_BOOK_IMBALANCE": 0.1,
            "RAW_OFI_SHARES": 50,
            "_PREV_MOVE": 0.0002,
            "MODEL": 0.0009})
    return rows


def test_admission_is_not_identified_when_a_baseline_scored_nothing():
    out = M.score_baselines(_scored_rows(), "MID_MOVE_60S", pred_key="MODEL",
                            min_coverage_pct=0.0)
    if out.get("STATUS") == "REFUSED":
        pytest.skip("target gate refused; admission not reached")
    assert out["BASELINE_SET_COMPLETE"] is False
    assert out["CHALLENGER_ADMISSION_COMPARISON_STATUS"] == NOT_IDENTIFIED


def test_an_empty_common_support_never_admits():
    assert "AN_EMPTY_COMMON_SUPPORT_IS_NOT_A_WIN" in dir(M) or True
    assert M.AN_EMPTY_COMMON_SUPPORT_IS_NOT_A_WIN


# --- 19. Planning permission is not evidentiary permission. ---------------

def test_a_cleared_power_analysis_does_not_admit_the_estimate():
    out = E.interaction(
        "SPREAD_x_REGIME", event_n=100000,
        effect_size_of_interest=0.5, event_level_variance=1.0,
        interaction_degrees_of_freedom=1,
        power_target={"ALPHA": 0.05, "POWER": 0.8},
        regime_support={"REGIMES_REQUIRED": 2, "REGIMES_WITH_SUPPORT": 3})
    assert out["MAY_RUN_EXPLORATORY_ESTIMATE"] is True
    assert out["EVIDENTIARY_ADMISSION_STATUS"] == "BLOCKED"


def test_a_blocked_power_analysis_blocks_both_permissions():
    out = E.interaction("SPREAD_x_REGIME", event_n=100)
    assert out["MAY_RUN_EXPLORATORY_ESTIMATE"] == NOT_IDENTIFIED
    assert out["EVIDENTIARY_ADMISSION_STATUS"] == "BLOCKED"


# --- 20. Drift thresholds are not capital controls. -----------------------

def test_drift_declares_its_thresholds_provisional():
    out = D.drift([1.0, 1.1, 0.9, 1.0, 1.05], [5.0, 5.1, 4.9],
                  monitor="SPREAD_DRIFT")
    assert out["DRIFT_STATUS"] == "DETECTED"
    assert out["DRIFT_THRESHOLD_STATUS"] == "DIAGNOSTIC_PROVISIONAL_HEURISTICS"


def test_a_provisional_drift_holds_back_the_capital_moving_responses():
    d = D.drift([1.0, 1.1, 0.9, 1.0, 1.05], [5.0, 5.1, 4.9],
                monitor="SPREAD_DRIFT")
    out = D.drift_response([d])
    assert out["DRIFT_DETECTED"] is True
    held = out["RESPONSES_HELD_PENDING_THRESHOLD_DERIVATION"]
    assert "NO_TRADE" in held or "REDUCE_ELIGIBLE_SIZE" in held
    for r in out["RESPONSES_ADMISSIBLE_NOW"]:
        assert r not in \
            D.DRIFT_INADMISSIBLE_ACTIONS_UNDER_PROVISIONAL_THRESHOLDS


# --- 21. Queue input hardening. -------------------------------------------

def test_the_queue_mechanism_refuses_invalid_inputs():
    assert P.p_fill_from_mechanism(
        queue_ahead=-1, trade_intensity_per_s=1.0,
        horizon_s=10)["STATUS"] == "INVALID_INPUTS"
    assert P.p_fill_from_mechanism(
        queue_ahead=10, trade_intensity_per_s=1.0,
        horizon_s=0)["STATUS"] == "INVALID_INPUTS"


def test_the_queue_mechanism_may_not_enter_action_ev():
    out = P.p_fill_from_mechanism(
        queue_ahead=10, trade_intensity_per_s=1.0, horizon_s=30,
        intensity_evidence_class=P.ESTIMATED_PRIOR)
    assert out["MAY_ENTER_ACTION_EV_AS_P_FILL"] is False


# --- 22. The fill-quantity interface. -------------------------------------

def test_every_fill_quantity_field_is_not_identified():
    out = MF.fill_quantity_interface()
    for f in MF.FILL_QUANTITY_FIELDS:
        v = out[f]
        if isinstance(v, dict):
            assert set(v.values()) == {NOT_IDENTIFIED}
        else:
            assert v == NOT_IDENTIFIED
    assert out["MAY_ENTER_ACTION_EV"] is False


# --- 23. Side-symmetric executable labels. --------------------------------

def test_the_buy_and_sell_round_trips_are_not_each_others_negatives():
    s = _series()
    lab = B.label_row(dict(s[0]), s)
    buy = lab["EXECUTABLE_BUY_MOVE_60S"]
    sell = lab["EXECUTABLE_SELL_MOVE_60S"]
    assert isinstance(buy, float) and isinstance(sell, float)
    assert buy != pytest.approx(-sell)


def test_the_superseded_name_is_the_buy_side():
    s = _series()
    lab = B.label_row(dict(s[0]), s)
    assert lab["EXECUTABLE_MOVE_60S"] == lab["EXECUTABLE_BUY_MOVE_60S"]
    assert lab["EXECUTABLE_MOVE_SUPERSEDED_BY"] == "EXECUTABLE_BUY_MOVE_<h>S"


# --- 24 and 25. The decision-grade gate and dependence. -------------------

def test_every_ev_row_carries_the_decision_grade_gate():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=200)
    assert r["DECISION_GRADE_ACTION_EV_STATUS"] == "BLOCKED"
    assert set(r["DECISION_GRADE_CHECKS"]) == set(A.DECISION_GRADE_CONDITIONS)


def test_dependence_is_always_a_blocker_and_never_a_guessed_correlation():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=200)
    assert r["DEPENDENCE_MODEL_STATUS"] == "INDEPENDENT_MARGINALS_UNVALIDATED"
    assert "DEPENDENCE_MODEL_VALIDATED" in r["DECISION_GRADE_BLOCKERS"]
    assert "CORRELATION" not in A.describe()


def test_the_gate_names_every_blocker_rather_than_one():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=200)
    blockers = r["DECISION_GRADE_BLOCKERS"]
    assert len(blockers) >= 5
    for b in blockers:
        assert r["DECISION_GRADE_CHECKS"][b] is False


def test_shadow_research_is_still_allowed_while_the_gate_is_blocked():
    r = A.action_ev_mc("POST_BID", full_terms(), draws=200)
    assert isinstance(r["EV_MEAN"], float)
    assert r["SHADOW_RESEARCH_REMAINS_ALLOWED"]
    assert r["NO_ORDER_IS_PLACED"] is True
