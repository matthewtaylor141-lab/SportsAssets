"""Tests for the three scientific corrections.

1. The 5-second horizon is UNOBSERVABLE at the V1 capture cadence.
2. The interaction threshold was a round number, not a power analysis.
3. The zero-centred fill-selection prior is a starting belief, not a finding.

Each correction is pinned in two directions: the corrected behaviour holds,
AND the retracted behaviour cannot come back.
"""

import pytest

import action_ev_mc as MC
import bettor_dataset as BD
import edge_dashboard as ED
import ev_core_registers as R
import prior_registry as PR
from prior_registry import Dist


# =========================================================================
# CORRECTION 1. The 5-second horizon.
# =========================================================================

def test_the_five_second_horizon_is_declared_unobservable():
    assert BD.FIVE_SECOND_HORIZON_STATUS == \
        "UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY"
    assert BD.HORIZON_STATUS_V1[5] == "UNOBSERVABLE_AT_V1_CAPTURE_FREQUENCY"


def test_the_longer_horizons_remain_observable():
    for h in (30, 60, 300):
        assert BD.HORIZON_STATUS_V1[h] == "OBSERVABLE"


def test_the_arithmetic_behind_the_claim_holds():
    """A ~24 s revisit cannot place an observation within 12 s of T+5s."""
    nearest_after = BD.V1_NOMINAL_REVISIT_S          # the next grid point
    assert abs(nearest_after - 5.0) > BD.HORIZON_TOLERANCE_S


def test_the_five_second_gate_fails_however_good_the_rows_look():
    """The DECLARED status is checked first, before measured coverage."""
    rows = [{"LABEL_STATUS": {"5S": "PRESENT"}} for _ in range(1000)]
    g = BD.horizon_label_coverage_gate(rows, 5)
    assert g["MAY_EVALUATE"] is False
    assert g["HORIZON_LABEL_COVERAGE_GATE"] == "FAIL"
    assert g["RESULT"] == "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"


def test_an_observable_horizon_with_coverage_passes_the_gate():
    rows = [{"LABEL_STATUS": {"30S": "PRESENT"}} for _ in range(80)]
    rows += [{"LABEL_STATUS": {"30S": "MISSING"}} for _ in range(20)]
    g = BD.horizon_label_coverage_gate(rows, 30)
    assert g["MAY_EVALUATE"] is True
    assert g["LABEL_COVERAGE_PCT"] == 80.0
    assert g["RESULT"] is None


def test_an_observable_horizon_without_coverage_fails_the_gate():
    rows = [{"LABEL_STATUS": {"300S": "PRESENT"}} for _ in range(10)]
    rows += [{"LABEL_STATUS": {"300S": "MISSING"}} for _ in range(90)]
    g = BD.horizon_label_coverage_gate(rows, 300)
    assert g["MAY_EVALUATE"] is False
    assert g["RESULT"] == "NOT_MEASURABLE_UNDER_THIS_CAPTURE_DESIGN"


def test_the_gate_fails_closed_on_no_rows():
    g = BD.horizon_label_coverage_gate([], 60)
    assert g["MAY_EVALUATE"] is False
    assert g["LABEL_COVERAGE_PCT"] == BD.NOT_IDENTIFIED


def test_the_five_repairs_are_named_and_forbidden():
    for r in ("INTERPOLATE_ACROSS_THE_GAP",
              "WIDEN_THE_TOLERANCE_AFTER_THE_FACT",
              "SUBSTITUTE_PLUS_24S_AND_CALL_IT_PLUS_5S",
              "TRAIN_A_5_SECOND_MODEL",
              "SCORE_A_5_SECOND_CHALLENGER"):
        assert r in BD.FORBIDDEN_5S_REPAIRS


def test_the_worst_repair_is_named_as_the_worst():
    assert "nothing downstream can tell the difference" in BD.WHY_NO_REPAIR


def test_forward_observation_still_refuses_to_label_five_seconds():
    """The label machinery itself produces MISSING, not a substitute."""
    series = [{"DECISION_TIMESTAMP_UTC": "2026-09-17T18:00:%02dZ" % s}
              for s in (0, 24, 48)]
    row, off = BD.forward_observation(series, "2026-09-17T18:00:00Z", 5)
    assert row is None and off is None


def test_v2_derives_cadence_from_horizons_and_v1_is_not_modified():
    assert "DERIVED" in BD.V2_DERIVES_CADENCE_FROM_HORIZONS
    assert "V1 is not modified" in BD.V2_DERIVES_CADENCE_FROM_HORIZONS
    assert R.V1_CAPTURE_IS_NOT_MODIFIED_TO_FIX_THIS


def test_the_register_carries_the_five_second_status():
    d = R.describe()
    assert d["FIVE_SECOND_HORIZON_STATUS"] == \
        BD.FIVE_SECOND_HORIZON_STATUS
    assert d["HORIZON_LABEL_COVERAGE_GATE_STATUS"] == \
        "BUILT_FAILS_CLOSED_BEFORE_EVALUATION"


# =========================================================================
# CORRECTION 2. The interaction threshold.
# =========================================================================

def test_the_round_number_constant_is_gone():
    assert not hasattr(ED, "MIN_EVENTS_FOR_AN_INTERACTION")


def test_the_interaction_status_is_pending_a_power_analysis():
    assert ED.EDGE_INTERACTION_STATUS == \
        "BUILT_THRESHOLD_NOT_IDENTIFIED_PENDING_POWER_ANALYSIS"
    assert R.EDGE_INTERACTION_STATUS == ED.EDGE_INTERACTION_STATUS
    assert R.EDGE_INTERACTION_THRESHOLD_STATUS == ED.EDGE_INTERACTION_STATUS


def test_without_a_power_analysis_the_answer_is_not_identified():
    """Not False, not True. A huge event count does not substitute."""
    row = ED.interaction("MICROPRICE_x_EXTERNAL_AGREE", event_n=100000)
    assert row["MAY_ESTIMATE"] == "NOT_IDENTIFIED"
    assert "no power analysis has been done" in row["WHY_NOT"]


def test_every_power_analysis_input_is_named():
    for k in ("INDEPENDENT_EVENT_N", "INTERACTION_DEGREES_OF_FREEDOM",
              "EFFECT_SIZE_OF_INTEREST", "EVENT_LEVEL_VARIANCE",
              "REGIME_SUPPORT", "PROSPECTIVE_PRECISION_OR_POWER_TARGET"):
        assert k in ED.POWER_ANALYSIS_INPUTS


def test_a_missing_power_input_is_named_not_defaulted():
    row = ED.interaction("X", event_n=500, effect_size_of_interest=0.004,
                         interaction_degrees_of_freedom=1,
                         regime_support={"REGIMES_REQUIRED": 2,
                                         "REGIMES_WITH_SUPPORT": 2},
                         power_target={"ALPHA": 0.05, "POWER": 0.80})
    assert row["MAY_ESTIMATE"] == "NOT_IDENTIFIED"
    assert "EVENT_LEVEL_VARIANCE" in row["BLOCKED_ON"]


def test_regime_support_is_supplied_never_assumed():
    row = ED.interaction("X", event_n=500, effect_size_of_interest=0.004,
                         event_level_variance=0.0004,
                         interaction_degrees_of_freedom=1,
                         power_target={"ALPHA": 0.05, "POWER": 0.80})
    assert row["MAY_ESTIMATE"] == "NOT_IDENTIFIED"
    assert "REGIME_SUPPORT" in row["BLOCKED_ON"]
    assert row["REGIME_SUPPORT"] == ED.NOT_IDENTIFIED


def test_a_complete_power_analysis_produces_a_derived_threshold():
    row = ED.interaction("MICROPRICE_x_EXTERNAL_AGREE", event_n=500,
                         effect_size_of_interest=0.004,
                         event_level_variance=0.0004,
                         interaction_degrees_of_freedom=1,
                         regime_support={"REGIMES_REQUIRED": 3,
                                         "REGIMES_WITH_SUPPORT": 3},
                         power_target={"ALPHA": 0.05, "POWER": 0.80})
    assert row["MAY_ESTIMATE"] is True
    assert isinstance(row["REQUIRED_EVENT_N"], int)
    assert row["POWER_ANALYSIS"]["DERIVED_NOT_ASSERTED"] is True


def test_short_of_the_derived_threshold_the_answer_is_false():
    row = ED.interaction("X", event_n=10, effect_size_of_interest=0.004,
                         event_level_variance=0.0004,
                         interaction_degrees_of_freedom=1,
                         regime_support={"REGIMES_REQUIRED": 3,
                                         "REGIMES_WITH_SUPPORT": 3},
                         power_target={"ALPHA": 0.05, "POWER": 0.80})
    assert row["MAY_ESTIMATE"] is False
    assert "derived requirement" in row["WHY_NOT"]


def test_insufficient_regime_coverage_blocks_even_with_enough_events():
    row = ED.interaction("X", event_n=100000,
                         effect_size_of_interest=0.004,
                         event_level_variance=0.0004,
                         interaction_degrees_of_freedom=1,
                         regime_support={"REGIMES_REQUIRED": 4,
                                         "REGIMES_WITH_SUPPORT": 1},
                         power_target={"ALPHA": 0.05, "POWER": 0.80})
    assert row["MAY_ESTIMATE"] is False
    assert "regime support insufficient" in row["WHY_NOT"]


def test_the_requirement_moves_with_the_effect_size():
    """A derived threshold responds to its inputs; a round number does not."""
    def req(delta):
        return ED.required_events_for_interaction(
            effect_size_of_interest=delta, event_level_variance=0.0004,
            interaction_degrees_of_freedom=1,
            power_target={"ALPHA": 0.05, "POWER": 0.80})["REQUIRED_EVENT_N"]
    assert req(0.002) > req(0.004) > req(0.008)


def test_the_requirement_moves_with_the_variance_and_the_target():
    base = dict(effect_size_of_interest=0.004, event_level_variance=0.0004,
                interaction_degrees_of_freedom=1,
                power_target={"ALPHA": 0.05, "POWER": 0.80})
    noisier = dict(base, event_level_variance=0.0016)
    stricter = dict(base, power_target={"ALPHA": 0.01, "POWER": 0.95})
    more_df = dict(base, interaction_degrees_of_freedom=4)
    n0 = ED.required_events_for_interaction(**base)["REQUIRED_EVENT_N"]
    assert ED.required_events_for_interaction(
        **noisier)["REQUIRED_EVENT_N"] > n0
    assert ED.required_events_for_interaction(
        **stricter)["REQUIRED_EVENT_N"] > n0
    assert ED.required_events_for_interaction(
        **more_df)["REQUIRED_EVENT_N"] > n0


def test_a_zero_or_missing_effect_size_is_refused_not_defaulted():
    for bad in (None, 0, 0.0, "small"):
        r = ED.required_events_for_interaction(
            effect_size_of_interest=bad, event_level_variance=0.0004,
            interaction_degrees_of_freedom=1,
            power_target={"ALPHA": 0.05, "POWER": 0.80})
        assert r["REQUIRED_EVENT_N"] == ED.NOT_IDENTIFIED
        assert "EFFECT_SIZE_OF_INTEREST" in r["MISSING_INPUTS"]


def test_the_retraction_is_recorded_and_the_reason_given():
    assert "200" in ED.EARLIER_THRESHOLD_SAID
    assert "retracted" in ED.EARLIER_THRESHOLD_SAID
    assert "Sufficiency is a property" in ED.WHY_A_ROUND_N_IS_NOT_SUFFICIENCY
    assert "BUILT_REQUIRES_200_EVENTS" in R.EDGE_INTERACTION_EARLIER_REGISTER_SAID


def test_no_live_constant_still_asserts_a_round_interaction_threshold():
    """The phrase survives only inside the two retraction constants."""
    retractions = {"EARLIER_THRESHOLD_SAID"}
    for name in dir(ED):
        if name in retractions or name.startswith("_"):
            continue
        v = getattr(ED, name)
        if isinstance(v, str):
            assert "REQUIRES_200_EVENTS" not in v, name


def test_the_ladder_cutoffs_are_declared_a_separate_open_question():
    """edge_row()'s 30/200 label a status; they grant no permission."""
    assert "STATUS LABEL" in ED.LADDER_THRESHOLDS_ARE_A_SEPARATE_OPEN_QUESTION
    row = ED.edge_row("FILL_EDGE", estimate=0.001, oos_event_n=250)
    assert row["STATUS"] == "REPLICATED"
    assert "MAY_ESTIMATE" not in row


# =========================================================================
# CORRECTION 3. The fill-selection prior.
# =========================================================================

def _fs_terms(**over):
    t = {"P_FILL": Dist("BETA", {"alpha": 2.0, "beta": 20.0}),
         "VALUE_IF_FILL": Dist("NORMAL", {"mu": 0.004, "sigma": 0.003}),
         "TOXICITY": Dist("NORMAL", {"mu": 0.0, "sigma": 0.006}),
         "FILL_SELECTION_EFFECT": Dist("NORMAL", {"mu": 0.0,
                                                  "sigma": 0.004})}
    t.update(over)
    return t


def test_the_prior_is_classified_exactly():
    e = PR.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"]
    assert e["FILL_SELECTION_PRIOR_SOURCE"] == \
        "STRUCTURAL_NONDIRECTIONAL_PRIOR"
    assert e["EVIDENCE_CLASS"] == "ESTIMATED_PRIOR"
    assert e["PRIOR_STRENGTH"] == "WEAK"
    assert e["PRIOR_CENTER"] == "ZERO"
    assert e["DIRECTION_ASSUMED"] == "NO"
    assert e["BETTOR_NATIVE_OBSERVATIONS"] == 0


def test_the_source_type_names_what_the_prior_is():
    e = PR.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"]
    assert e["SOURCE_TYPE"] == "STRUCTURAL_NONDIRECTIONAL_PRIOR"
    assert "PRINCIPLED_LEAST_INFORMATIVE" in PR.EARLIER_SOURCE_LABEL_SAID


def test_a_zero_centre_is_not_a_zero_finding():
    e = PR.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"]
    assert "NOT evidence that FILL_SELECTION_EFFECT = 0" in \
        e["NOT_EVIDENCE_THAT_THE_EFFECT_IS_ZERO"]
    assert "NOT evidence" in R.FILL_SELECTION_PRIOR_IS_NOT_A_ZERO_FINDING


def test_the_posterior_may_move_in_either_direction():
    e = PR.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"]
    assert "ADVERSE or" in e["POSTERIOR_MAY_MOVE_EITHER_WAY"]
    assert "FAVOURABLE" in e["POSTERIOR_MAY_MOVE_EITHER_WAY"]


def test_the_prior_keeps_its_width():
    e = PR.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"]
    assert e["PRIOR_AVAILABLE"] is True
    assert e["DISTRIBUTION_FAMILY"] == "NORMAL"
    assert e["UNCERTAINTY_STATUS"] == "WIDE_BY_CONSTRUCTION"
    assert e["MEAN"] == 0.0
    # The mean alone says nothing; the published quantiles are the content.
    assert e["P10"] < 0 < e["P90"]
    assert e["REQUIRED_EV_EXPOSURE"] == MC.REQUIRED_FILL_SELECTION_EXPOSURE


def test_the_register_carries_the_classification():
    d = R.describe()
    for token in ("STRUCTURAL_NONDIRECTIONAL_PRIOR",
                  "EVIDENCE_CLASS=ESTIMATED_PRIOR", "PRIOR_STRENGTH=WEAK",
                  "PRIOR_CENTER=ZERO", "DIRECTION_ASSUMED=NO",
                  "BETTOR_NATIVE_OBSERVATIONS=0"):
        assert token in d["FILL_SELECTION_PRIOR_CLASSIFICATION"]


# --- The EV exposure. ------------------------------------------------------

def test_an_ev_carrying_the_term_publishes_the_three_quantiles():
    row = MC.action_ev_mc("QUOTE_BID", _fs_terms(), draws=3000)
    assert row["FILL_SELECTION_STATUS"] == "CARRIED_AS_SEPARATE_TERM"
    for k in MC.REQUIRED_FILL_SELECTION_EXPOSURE:
        assert isinstance(row[k], float)


def test_the_quantile_evs_are_ordered_by_the_prior_they_come_from():
    row = MC.action_ev_mc("QUOTE_BID", _fs_terms(), draws=3000)
    assert row["EV_AT_FILL_SELECTION_P10"] < row["EV_AT_FILL_SELECTION_P50"]
    assert row["EV_AT_FILL_SELECTION_P50"] < row["EV_AT_FILL_SELECTION_P90"]
    assert row["FILL_SELECTION_P10"] < 0 < row["FILL_SELECTION_P90"]
    assert row["FILL_SELECTION_P50"] == 0.0


def test_the_spread_is_reported_and_a_sign_change_is_the_materiality_test():
    row = MC.action_ev_mc("QUOTE_BID", _fs_terms(), draws=3000)
    lo = row["EV_AT_FILL_SELECTION_P10"]
    hi = row["EV_AT_FILL_SELECTION_P90"]
    assert row["EV_SPREAD_ACROSS_FILL_SELECTION"] == pytest.approx(
        hi - lo, abs=1e-9)
    assert row["FILL_SELECTION_MATERIAL_TO_THIS_ACTION"] is (lo < 0 < hi)
    assert "not a chosen cutoff" in \
        MC.MATERIALITY_IS_A_SIGN_CHANGE_NOT_A_THRESHOLD


def test_a_wide_prior_can_flip_the_sign_of_a_positive_mean_ev():
    """The whole point: the mean says pay, the P10 says do not."""
    row = MC.action_ev_mc("QUOTE_BID", _fs_terms(), draws=6000)
    assert row["EV_MEAN"] > 0
    assert row["EV_AT_FILL_SELECTION_P10"] < 0
    assert row["FILL_SELECTION_MATERIAL_TO_THIS_ACTION"] is True


def test_an_absent_term_is_excluded_and_never_silently_zero():
    terms = _fs_terms()
    terms.pop("FILL_SELECTION_EFFECT")
    row = MC.action_ev_mc("QUOTE_BID", terms, draws=1000)
    assert row["FILL_SELECTION_STATUS"] == "EXCLUDED_NOT_ZERO"
    for k in MC.REQUIRED_FILL_SELECTION_EXPOSURE:
        assert row[k] == "EXCLUDED_NOT_ZERO"
    assert row["FILL_SELECTION_MATERIAL_TO_THIS_ACTION"] == MC.NOT_IDENTIFIED
    assert row["EVIDENCE_CLASS_BY_TERM"]["FILL_SELECTION_EFFECT"] == \
        MC.NOT_IDENTIFIED


def test_excluded_and_zero_produce_different_reports_not_just_numbers():
    terms = _fs_terms()
    absent = dict(terms)
    absent.pop("FILL_SELECTION_EFFECT")
    zeroed = dict(terms, FILL_SELECTION_EFFECT=0.0)
    a = MC.action_ev_mc("Q", absent, draws=800)
    z = MC.action_ev_mc("Q", zeroed, draws=800)
    assert a["EV_MEAN"] == pytest.approx(z["EV_MEAN"], abs=1e-9)
    assert a["FILL_SELECTION_STATUS"] != z["FILL_SELECTION_STATUS"]


def test_an_embedded_term_is_not_added_twice():
    row = MC.action_ev_mc("Q", _fs_terms(
        FILL_SELECTION_CONVENTION="EMBEDDED_IN_VALUE_IF_FILL"), draws=800)
    assert row["FILL_SELECTION_STATUS"] == \
        "EMBEDDED_IN_VALUE_IF_FILL_NOT_ADDED"
    plain = MC.action_ev_mc("Q", {k: v for k, v in _fs_terms().items()
                                  if k != "FILL_SELECTION_EFFECT"},
                            draws=800)
    assert row["EV_MEAN"] == pytest.approx(plain["EV_MEAN"], abs=1e-9)


def test_an_unknown_fill_selection_convention_is_refused():
    row = MC.action_ev_mc("Q", _fs_terms(
        FILL_SELECTION_CONVENTION="IGNORE_IT"), draws=100)
    assert row["STATUS"] == "UNKNOWN_FILL_SELECTION_CONVENTION"


def test_the_sign_convention_is_frozen_and_favourable_is_positive():
    assert "MARKOUT_FILLED - " in MC.FILL_SELECTION_SIGN_CONVENTION
    assert "POSITIVE is FAVOURABLE" in MC.FILL_SELECTION_SIGN_CONVENTION
    good = MC.action_ev_mc("Q", _fs_terms(
        FILL_SELECTION_EFFECT=0.004), draws=800)
    bad = MC.action_ev_mc("Q", _fs_terms(
        FILL_SELECTION_EFFECT=-0.004), draws=800)
    assert good["EV_MEAN"] > bad["EV_MEAN"]


def test_the_term_appears_in_the_tornado_and_the_information_ranking():
    t = _fs_terms()
    assert "FILL_SELECTION_EFFECT" in [
        r["TERM"] for r in MC.sensitivity("Q", t)["TORNADO"]]
    voi = MC.value_of_information("Q", t, draws=600)
    assert "FILL_SELECTION_EFFECT" in [
        r["TERM"] for r in voi["MARGINAL_VALUE_OF_REDUCING_UNCERTAINTY"]]


def test_break_even_can_be_solved_on_the_fill_selection_effect():
    be = MC.break_even("Q", _fs_terms(), "FILL_SELECTION_EFFECT",
                       lo=-0.05, hi=0.05)
    assert be["STATUS"] == "SOLVED"
    assert be["BREAK_EVEN_FILL_SELECTION_EFFECT"] < 0


def test_break_even_refuses_a_term_that_is_embedded_elsewhere():
    be = MC.break_even("Q", _fs_terms(
        FILL_SELECTION_CONVENTION="EMBEDDED_IN_VALUE_IF_FILL"),
        "FILL_SELECTION_EFFECT", lo=-0.05, hi=0.05)
    assert be["STATUS"] == "TERM_NOT_CARRIED_SEPARATELY"


def test_a_report_that_hides_the_width_is_incomplete():
    row = MC.action_ev_mc("Q", _fs_terms(), draws=800)
    assert MC.fill_selection_exposure(row)["REPORT_STATUS"] == "COMPLETE"
    stripped = {k: v for k, v in row.items()
                if k not in MC.REQUIRED_FILL_SELECTION_EXPOSURE}
    assert MC.fill_selection_exposure(stripped)["REPORT_STATUS"] == \
        "REPORT_INCOMPLETE"


def test_the_sensitivity_row_alone_also_satisfies_the_exposure_rule():
    t = _fs_terms()
    row = MC.action_ev_mc("Q", t, draws=800)
    stripped = {k: v for k, v in row.items()
                if k not in MC.REQUIRED_FILL_SELECTION_EXPOSURE}
    chk = MC.fill_selection_exposure(stripped, MC.sensitivity("Q", t))
    assert chk["REPORT_OK"] is True
    assert chk["HAS_SENSITIVITY_ROW"] is True


def test_a_row_that_never_carried_the_term_needs_no_exposure():
    terms = _fs_terms()
    terms.pop("FILL_SELECTION_EFFECT")
    chk = MC.fill_selection_exposure(MC.action_ev_mc("Q", terms, draws=400))
    assert chk["EXPOSURE_REQUIRED"] is False
    assert chk["REPORT_OK"] is True


def test_the_shadow_decision_carries_the_width_to_the_reader():
    t = _fs_terms()
    row = MC.action_ev_mc("Q", t, draws=800)
    sd = MC.shadow_decision("QUOTE_BID", 0.42, 10, row, MC.sensitivity("Q", t))
    for k in MC.REQUIRED_FILL_SELECTION_EXPOSURE:
        assert isinstance(sd[k], float)
    assert sd["FILL_SELECTION_EXPOSURE"]["REPORT_OK"] is True
    assert sd["NO_ORDER_SUBMITTED"] is True


# =========================================================================
# The frozen capture and the safety rails are untouched by all three.
# =========================================================================

def test_the_frozen_capture_and_thresholds_are_intact():
    import capture_quality as CQ
    assert CQ.thresholds_intact()["INTACT"] is True
    assert CQ.FROZEN_QUALITY_THRESHOLDS_SHA == (
        "70e6ddda88ad4141ae3ed58691d8322cea4bf37a83c89efd481792c47243e7fb")


def test_nothing_here_trains_tunes_or_trades():
    assert R.TRAINING_PERFORMED == "NO"
    assert R.PARAMETER_TUNING_PERFORMED == "NO"
    assert R.MODEL_SELECTION_PERFORMED == "NO"
    assert R.LIVE_ORDER_ACTIVITY == "NONE"
    assert MC.SHADOW_ONLY is True
    assert MC.NO_ORDER_IS_PLACED is True
    assert ED.NOTHING_IS_TRAINED_HERE is True
    assert BD.NOTHING_IS_TRAINED_HERE is True
    assert PR.NOTHING_IS_TRAINED_HERE is True
