"""Tests for the probabilistic edge engine and the learning infrastructure."""

import pytest

import action_ev_mc as MC
import drift_trust as DT
import edge_dashboard as ED
import learning_ledger as LL
import model_registry as MR
import prior_registry as PR


# --- Evidence classes and the prior registry. ------------------------------

def test_three_evidence_classes_exist():
    assert set(PR.EVIDENCE_CLASSES) == {
        "MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR", "NOT_IDENTIFIED"}


def test_a_not_identified_quantity_is_never_silently_substituted():
    assert "never silently substitute 0, 0.5" in PR.NEVER_SILENTLY_SUBSTITUTE
    p = PR.prior("CANCEL_LATENCY")
    assert p["PRIOR_AVAILABLE"] is False
    assert "MEAN" not in p


def test_an_undocumented_prior_is_refused():
    d = PR.Dist("NORMAL", {"mu": 0.0, "sigma": 1.0})
    r = PR.make_prior("SOMETHING", d, PR.ESTIMATED_PRIOR, "WEAK",
                      source_type=None, source_population=None,
                      transfer_assumptions=None)
    assert r["PRIOR_AVAILABLE"] is False
    assert r["REASON"] == "UNDOCUMENTED_PRIOR"
    assert "an opinion with a number attached" in r["NO_PRIOR_WITHOUT_A_REASON"]


def test_a_documented_prior_carries_full_provenance():
    p = PR.prior("FILL_SELECTION_EFFECT")
    assert p["PRIOR_AVAILABLE"] is True
    for f in ("EVIDENCE_CLASS", "PRIOR_STRENGTH", "SOURCE_TYPE",
              "SOURCE_POPULATION", "TRANSFER_ASSUMPTIONS", "TRANSFER_RISK",
              "MEAN", "P05", "P95", "PRIOR_SHA"):
        assert f in p, f


def test_strength_changes_uncertainty_not_the_mean():
    weak = PR.beta_from_mean_n(0.3, PR.STRENGTH_PSEUDO_N["WEAK"])
    strong = PR.beta_from_mean_n(0.3, PR.STRENGTH_PSEUDO_N["STRONG"])
    assert weak.mean() == pytest.approx(strong.mean())
    assert (weak.quantile(0.90) - weak.quantile(0.10)) > \
        (strong.quantile(0.90) - strong.quantile(0.10))
    assert "admitting ignorance would change the answer" in \
        PR.STRENGTH_AFFECTS_UNCERTAINTY_NOT_MEAN


def test_the_fill_selection_prior_is_centred_at_zero_not_adverse():
    """No direction is hard-coded. Centre zero, wide, and justified."""
    p = PR.prior("FILL_SELECTION_EFFECT")
    assert p["MEAN"] == pytest.approx(0.0)
    assert p["P05"] < 0 < p["P95"]
    assert "least-informative defensible choice" in p["TRANSFER_ASSUMPTIONS"]


def test_p_fill_has_no_prior_and_says_why():
    p = PR.prior("P_FILL")
    assert p["PRIOR_AVAILABLE"] is False
    assert "BOTH inputs are unmeasured" in p["WHY_NOT"]
    assert "BREAK_EVEN_P_FILL" in p["WHY_NOT"]


def test_most_parameters_have_no_prior_and_that_is_the_honest_state():
    c = PR.registry_census()
    assert c["NO_COUNT"] > c["YES_COUNT"]
    assert "research question" in c["MOST_ARE_NO_AND_THAT_IS_THE_HONEST_STATE"]


def test_a_name_in_the_list_does_not_create_a_prior():
    assert "do not create a prior merely because" in PR.A_NAME_IS_NOT_A_PRIOR


# --- The P_FILL mechanism. -------------------------------------------------

def test_the_mechanism_refuses_without_a_measured_intensity():
    r = PR.p_fill_from_mechanism(queue_ahead=100, trade_intensity_per_s=2.0,
                                 horizon_s=30)
    assert r["P_FILL"] == "NOT_IDENTIFIED"
    assert r["BLOCKED_ON"] == "TOUCH_TRADE_INTENSITY_MEASUREMENT"


def test_the_mechanism_computes_once_intensity_carries_evidence():
    r = PR.p_fill_from_mechanism(
        queue_ahead=10, trade_intensity_per_s=2.0, horizon_s=30,
        intensity_evidence_class=PR.ESTIMATED_PRIOR)
    assert 0.0 <= r["P_FILL"] <= 1.0
    assert r["P_FILL"] > 0.9          # lambda 60 vs queue 10
    assert r["NEVER_LABEL_THIS_MEASURED_UNTIL_BETTOR_ORDERS_EXIST"] is True


def test_a_deeper_queue_lowers_p_fill():
    kw = dict(trade_intensity_per_s=1.0, horizon_s=30,
              intensity_evidence_class=PR.ESTIMATED_PRIOR)
    shallow = PR.p_fill_from_mechanism(queue_ahead=5, **kw)["P_FILL"]
    deep = PR.p_fill_from_mechanism(queue_ahead=200, **kw)["P_FILL"]
    assert shallow > deep


# --- Bayesian update and shrinkage. ----------------------------------------

def test_a_beta_update_moves_toward_the_data():
    prior = PR.beta_from_mean_n(0.20, 10)
    r = PR.update_beta(prior, successes=40, trials=50)
    assert r["STATUS"] == "UPDATED"
    assert r["POSTERIOR"]["MEAN"] > r["PRIOR"]["MEAN"]
    assert r["PRIOR_TO_POSTERIOR_SHIFT"] > 0


def test_a_weak_prior_yields_to_data_faster_than_a_strong_one():
    data = dict(successes=40, trials=50)
    weak = PR.update_beta(PR.beta_from_mean_n(0.2, 2), **data)
    strong = PR.update_beta(PR.beta_from_mean_n(0.2, 200), **data)
    assert weak["POSTERIOR"]["MEAN"] > strong["POSTERIOR"]["MEAN"]


def test_the_prior_is_never_overwritten():
    prior = PR.beta_from_mean_n(0.20, 10)
    r = PR.update_beta(prior, 40, 50, prior_version="v1")
    assert r["PRIOR"]["MEAN"] == pytest.approx(0.20)
    assert prior.mean() == pytest.approx(0.20)
    assert r["PRIOR_VERSION"] == "v1"
    assert r["POSTERIOR_VERSION"] != "v1"


def test_a_normal_update_reduces_uncertainty():
    prior = PR.Dist("NORMAL", {"mu": 0.0, "sigma": 0.01})
    r = PR.update_normal(prior, obs_mean=0.004, obs_sigma=0.01, n=100)
    assert r["UNCERTAINTY_FELL_BY"] > 0
    assert 0 < r["POSTERIOR"]["MEAN"] < 0.004


def test_a_sparse_subgroup_shrinks_toward_its_parent():
    s = PR.shrink(subgroup_mean=0.9, subgroup_n=5, parent_mean=0.3, k=20)
    assert 0.3 < s["SHRUNK_MEAN"] < 0.9
    assert s["SHRUNK_MEAN"] < 0.5      # five observations barely move it
    assert s["WEIGHT_ON_SUBGROUP"] == pytest.approx(0.2)


def test_a_dense_subgroup_earns_its_own_answer():
    s = PR.shrink(0.9, 2000, 0.3, k=20)
    assert s["SHRUNK_MEAN"] > 0.88


def test_an_empty_subgroup_is_the_parent():
    s = PR.shrink(None, 0, 0.3)
    assert s["SHRUNK_MEAN"] == 0.3
    assert s["WEIGHT_ON_SUBGROUP"] == 0.0


# --- Monte Carlo action EV. ------------------------------------------------

def _terms(**over):
    t = {
        "P_FILL": PR.beta_from_mean_n(0.25, 20),
        "VALUE_IF_FILL": PR.Dist("NORMAL", {"mu": 0.020, "sigma": 0.004}),
        "VALUE_IF_NO_FILL": 0.0,
        "TOXICITY": PR.Dist("NORMAL", {"mu": 0.005, "sigma": 0.002}),
        "FEE": 0.001, "REBATE": 0.0005, "INVENTORY_COST": 0.0002,
        "EXIT_COST": 0.0, "CAPITAL_REQUIRED": 40.0,
        "OCCUPANCY_SECONDS": 1800.0,
    }
    t.update(over)
    return t


def test_the_ev_distribution_is_computed_with_quantiles():
    r = MC.action_ev_mc("POST_BID", _terms(), draws=4000)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"
    for k in ("EV_MEAN", "EV_P05", "EV_P10", "EV_P50", "EV_P90", "EV_P95",
              "P_EV_GT_0", "CONSERVATIVE_EV_P10"):
        assert k in r, k
    assert r["EV_P05"] <= r["EV_P50"] <= r["EV_P95"]


def test_the_result_is_reproducible_under_a_seed():
    a = MC.action_ev_mc("POST_BID", _terms(), draws=3000, seed=7)
    b = MC.action_ev_mc("POST_BID", _terms(), draws=3000, seed=7)
    assert a["EV_MEAN"] == b["EV_MEAN"]


def test_a_missing_critical_term_makes_the_ev_not_fully_identified():
    r = MC.action_ev_mc("POST_BID", _terms(P_FILL=MC.NOT_IDENTIFIED))
    assert r["ACTION_EV_STATUS"] == "NOT_FULLY_IDENTIFIED"
    assert r["EV_MEAN"] == "NOT_IDENTIFIED"
    assert "break_even()" in r["WHAT_TO_DO_INSTEAD"]


def test_the_double_count_guard_changes_the_answer():
    sep = MC.action_ev_mc("POST_BID", _terms(), "SEPARATE_TERM", draws=4000,
                          seed=11)
    emb = MC.action_ev_mc("POST_BID", _terms(),
                          "EMBEDDED_IN_VALUE_IF_FILL", draws=4000, seed=11)
    assert emb["EV_MEAN"] > sep["EV_MEAN"]
    assert "kills a real edge" in MC.DOUBLE_COUNT_GUARD


def test_toxicity_is_not_critical_under_the_embedded_convention():
    r = MC.action_ev_mc("POST_BID", _terms(TOXICITY=MC.NOT_IDENTIFIED),
                        "EMBEDDED_IN_VALUE_IF_FILL", draws=2000)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"


def test_toxicity_is_critical_under_the_separate_convention():
    r = MC.action_ev_mc("POST_BID", _terms(TOXICITY=MC.NOT_IDENTIFIED),
                        "SEPARATE_TERM")
    assert r["ACTION_EV_STATUS"] == "NOT_FULLY_IDENTIFIED"
    assert "TOXICITY" in r["MISSING_CRITICAL_TERMS"]


def test_the_evidence_mix_is_reported():
    r = MC.action_ev_mc("POST_BID", _terms(), draws=2000)
    m = r["EVIDENCE_MIX"]
    assert m["MEASURED"] + m["ESTIMATED"] + m["UNIDENTIFIED"] == \
        len(MC.EV_TERMS)


def test_nothing_is_ever_recommended_in_shadow():
    r = MC.action_ev_mc("POST_BID", _terms(), draws=2000)
    assert r["RECOMMENDED"] is False
    assert r["STATUS"] == "SHADOW_ONLY"
    assert r["NO_ORDER_IS_PLACED"] is True


def test_capital_hour_rate_needs_both_capital_and_time():
    r = MC.action_ev_mc("POST_BID",
                        _terms(CAPITAL_REQUIRED=MC.NOT_IDENTIFIED),
                        draws=2000)
    assert r["EV_PER_CAPITAL_HOUR_MEAN"] == "NOT_IDENTIFIED"
    assert "guessed denominator" in r["WHY_NO_CAPITAL_RATE"]


# --- Break-even: the engine that makes unknowns actionable. ----------------

def test_break_even_p_fill_is_solved_when_p_fill_is_unknown():
    r = MC.break_even("POST_BID", _terms(P_FILL=MC.NOT_IDENTIFIED), "P_FILL")
    assert r["STATUS"] == "SOLVED"
    assert 0.0 < r["BREAK_EVEN_P_FILL"] < 1.0
    assert "research question with a number attached" in \
        r["BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS"]


def test_the_break_even_is_the_threshold_not_an_estimate():
    r = MC.break_even("POST_BID", _terms(P_FILL=MC.NOT_IDENTIFIED), "P_FILL")
    assert "it is the threshold the term must clear" in \
        r["THIS_IS_NOT_AN_ESTIMATE_OF_THE_TERM"]


def test_break_even_verified_against_the_ev_at_that_point():
    """At the solved P_FILL the mean EV should be ~zero; above it, positive."""
    t = _terms(P_FILL=MC.NOT_IDENTIFIED)
    r = MC.break_even("POST_BID", t, "P_FILL")
    x = r["BREAK_EVEN_P_FILL"]
    at = MC.action_ev_mc("POST_BID", dict(t, P_FILL=x), draws=4000, seed=3)
    above = MC.action_ev_mc("POST_BID", dict(t, P_FILL=min(x * 2, 0.99)),
                            draws=4000, seed=3)
    assert abs(at["EV_MEAN"]) < 1e-4
    assert above["EV_MEAN"] > at["EV_MEAN"]


def test_an_action_that_never_pays_reports_no_solution():
    t = _terms(P_FILL=MC.NOT_IDENTIFIED, FEE=1.0)   # fee dwarfs any value
    r = MC.break_even("POST_BID", t, "P_FILL")
    assert r["STATUS"] == "NO_SOLUTION_IN_RANGE"
    assert r["ALWAYS_NEGATIVE"] is True
    assert "no attainable value of this term rescues the action" in \
        r["INTERPRETATION"]


def test_two_unknowns_make_a_surface_not_a_threshold():
    t = _terms(P_FILL=MC.NOT_IDENTIFIED, VALUE_IF_FILL=MC.NOT_IDENTIFIED)
    r = MC.break_even("POST_BID", t, "P_FILL")
    assert r["STATUS"] == "MORE_THAN_ONE_UNKNOWN"
    assert "a surface, not a threshold" in r["WHY"]


# --- Sensitivity and value of information. ---------------------------------

def test_the_tornado_ranks_terms_by_ev_swing():
    s = MC.sensitivity("POST_BID", _terms())
    assert s["STATUS"] == "COMPUTED"
    swings = [r["EV_SWING"] for r in s["TORNADO"]]
    assert swings == sorted(swings, reverse=True)
    assert len(s["TOP_EV_SENSITIVITY_DRIVERS"]) <= 3


def test_a_point_term_contributes_no_swing():
    s = MC.sensitivity("POST_BID", _terms())
    assert "FEE" not in [r["TERM"] for r in s["TORNADO"]]


def test_value_of_information_ranks_by_variance_removed():
    v = MC.value_of_information("POST_BID", _terms(), draws=1500)
    assert v["STATUS"] == "COMPUTED"
    rows = v["MARGINAL_VALUE_OF_REDUCING_UNCERTAINTY"]
    vals = [r["VARIANCE_REMOVED_IF_RESOLVED"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    assert v["TOP_VALUE_OF_INFORMATION_TERM"] in MC.EV_TERMS


def test_voi_points_at_the_term_worth_an_experiment():
    """A term with huge uncertainty should dominate the ranking."""
    t = _terms(VALUE_IF_FILL=PR.Dist("NORMAL", {"mu": 0.02, "sigma": 0.05}))
    v = MC.value_of_information("POST_BID", t, draws=1500)
    assert v["TOP_VALUE_OF_INFORMATION_TERM"] == "VALUE_IF_FILL"


def test_voi_defers_to_break_even_when_a_critical_term_is_unknown():
    v = MC.value_of_information("POST_BID",
                                _terms(P_FILL=MC.NOT_IDENTIFIED))
    assert v["STATUS"] == "NOT_FULLY_IDENTIFIED"
    assert "break_even()" in v["WHY"]


# --- Robustness, dominance, shadow output. ---------------------------------

def test_robustly_positive_uses_the_conservative_tail():
    r = MC.action_ev_mc("POST_BID", _terms(), draws=4000)
    rob = MC.robustly_positive(r)
    assert rob["ROBUSTLY_POSITIVE"] == (r["EV_P10"] > 0)
    assert "management decision" in rob["ROBUSTNESS_THRESHOLD_NOT_CHOSEN"]


def test_an_unidentified_ev_is_never_robustly_positive():
    rob = MC.robustly_positive({"EV_P10": MC.NOT_IDENTIFIED})
    assert rob["ROBUSTLY_POSITIVE"] is False


def test_a_dominated_action_is_marked():
    rows = [{"ACTION": "A", "EV_MEAN": 0.01, "EV_SD": 0.05,
             "CAPITAL_OCCUPANCY_MEAN": 100},
            {"ACTION": "B", "EV_MEAN": 0.01, "EV_SD": 0.01,
             "CAPITAL_OCCUPANCY_MEAN": 50}]
    d = MC.dominated(rows)
    by = {r["ACTION"]: r for r in d["ROWS"]}
    assert by["A"]["DOMINATED"] is True
    assert by["A"]["DOMINATED_BY"] == "B"
    assert by["B"]["DOMINATED"] is False


def test_the_shadow_decision_never_submits():
    r = MC.action_ev_mc("POST_BID", _terms(), draws=2000)
    s = MC.shadow_decision("POST_BID", "0.47", 500, r,
                           sens=MC.sensitivity("POST_BID", _terms()))
    assert s["STATUS"] == "SHADOW_ONLY"
    assert s["NO_ORDER_SUBMITTED"] is True
    assert s["TOP_UNCERTAINTY_DRIVER"] in MC.EV_TERMS


def test_guaranteed_profit_language_is_forbidden():
    assert "GUARANTEED_PROFIT" in MC.FORBIDDEN_CLAIMS
    assert "may never report" in MC.NO_GUARANTEED_PROFIT_LANGUAGE


# --- The learning ledger. --------------------------------------------------

def test_every_event_carries_its_envelope():
    e = LL.event("MARKET_STATE_EVENT", "2026-09-17T18:00:00+00:00",
                 "VENUE", "abc123", recorded_at_utc="2026-09-17T18:00:01Z")
    assert e["ENVELOPE_COMPLETE"] is True
    for f in LL.REQUIRED_ENVELOPE:
        assert f in e


def test_a_missing_envelope_field_is_named_not_defaulted():
    e = LL.event("MARKET_STATE_EVENT", "2026-09-17T18:00:00+00:00",
                 "VENUE", "abc123")            # no recorded_at
    assert e["ENVELOPE_COMPLETE"] is False
    assert "RECORDED_AT_UTC" in e["MISSING_ENVELOPE_FIELDS"]


def test_an_undeclared_event_type_is_refused():
    assert LL.event("SOMETHING", "t", "s", "sha")["STATUS"] == \
        "UNKNOWN_EVENT_TYPE"


def test_recorded_at_is_not_source_timestamp():
    assert "hides a stale feed" in LL.RECORDED_AT_IS_NOT_SOURCE_TIMESTAMP


def test_a_no_trade_is_logged_with_everything_a_trade_carries():
    d = LL.no_trade_decision(state={"SPREAD": 0.02}, predictions={"MOVE": 0.0},
                             candidate_actions=["POST_BID"],
                             expected_ev={"POST_BID": -0.001},
                             reason_rejected="EV_BELOW_BUFFER",
                             source_timestamp="2026-09-17T18:00:00+00:00",
                             code_sha="abc")
    assert d["ACTION"] == "NO_TRADE"
    assert d["MISSING_NO_TRADE_FIELDS"] == []
    assert d["FUTURE_OUTCOMES_STILL_LABELLED"] is True
    assert "calls it a discovery" in d["NO_TRADE_IS_LOGGED_LIKE_A_TRADE"]


def test_selection_bias_between_traded_and_skipped_states():
    traded = [{"SPREAD": 0.01}, {"SPREAD": 0.012}]
    skipped = [{"SPREAD": 0.05}, {"SPREAD": 0.06}]
    b = LL.selection_bias(traded, skipped, "SPREAD")
    assert b["STATUS"] == "COMPARED"
    assert b["DIFFERENCE"] < 0


# --- Point-in-time features and lineage. -----------------------------------

def test_a_feature_known_after_the_decision_is_not_valid():
    f = LL.feature("XG", 1.4, "v1", "t", "2026-09-17T19:00:00+00:00",
                   decision_timestamp="2026-09-17T18:00:00+00:00")
    assert f["POINT_IN_TIME_VALID"] == "NO"
    assert f["WHY_NOT_VALID"] == "INFORMATION_AVAILABLE_AFTER_THE_DECISION"


def test_a_feature_known_before_the_decision_is_valid():
    f = LL.feature("XG", 1.4, "v1", "t", "2026-09-17T17:00:00+00:00",
                   decision_timestamp="2026-09-17T18:00:00+00:00")
    assert f["POINT_IN_TIME_VALID"] == "YES"


def test_a_feature_without_a_timestamp_is_not_valid():
    f = LL.feature("XG", 1.4, "v1", "t", None)
    assert f["POINT_IN_TIME_VALID"] == "NO"
    assert f["WHY_NOT_VALID"] == "NO_INFORMATION_AVAILABLE_AT"


def test_one_leaked_feature_blocks_the_whole_fit():
    good = LL.feature("A", 1, "v1", "t", "2026-09-17T17:00:00+00:00",
                      decision_timestamp="2026-09-17T18:00:00+00:00")
    bad = LL.feature("B", 1, "v1", "t", "2026-09-17T19:00:00+00:00",
                     decision_timestamp="2026-09-17T18:00:00+00:00")
    t = LL.trainable([good, bad])
    assert t["MAY_TRAIN"] is False
    assert "contaminates the whole fit" in t["WHY_NOT"]


def test_current_file_reconstruction_is_named_as_the_leak():
    assert "unknowable then" in LL.NO_CURRENT_FILE_RECONSTRUCTION


def test_a_correction_creates_a_version_and_names_the_derived_features():
    lin = LL.Lineage()
    lin.declare("BOOK_L1", "v1").declare("BOOK_L2", "v1")
    lin.declare("MULTI_LEVEL_OFI", "v1",
                [("BOOK_L1", "v1"), ("BOOK_L2", "v1")])
    lin.declare("TOXICITY_FEATURE", "v1", [("MULTI_LEVEL_OFI", "v1")])
    c = lin.correct("BOOK_L1", "v1", "v2")
    assert c["HISTORICAL_DATA_REWRITTEN"] is False
    assert ("MULTI_LEVEL_OFI", "v1") in c["DERIVED_FEATURES_NEEDING_NEW_VERSIONS"]
    assert ("TOXICITY_FEATURE", "v1") in \
        c["DERIVED_FEATURES_NEEDING_NEW_VERSIONS"]
    assert "silently becomes a model nobody validated" in \
        c["CORRECTION_CREATES_A_VERSION"]


def test_the_lineage_walks_transitively():
    lin = LL.Lineage()
    lin.declare("A", "v1")
    lin.declare("B", "v1", [("A", "v1")])
    lin.declare("C", "v1", [("B", "v1")])
    assert ("A", "v1") in lin.upstream("C", "v1")


# --- Label maturity. -------------------------------------------------------

def test_a_pending_label_is_not_zero():
    m = LL.label_maturity("SETTLEMENT_OUTCOME", 60, 86400)
    assert m["LABEL_STATUS"] == "PENDING"
    assert "invents the answer" in m["PENDING_IS_NOT_ZERO"]


def test_a_mature_label_is_consumable():
    assert LL.label_maturity("PRICE_MOVE_5S", 30, 5)["LABEL_STATUS"] == "MATURE"


def test_a_job_must_declare_which_maturity_class_it_consumes():
    assert LL.consumable([], "FAST", "SLOW")["MAY_CONSUME"] is False


def test_pending_rows_are_excluded_not_counted_as_outcomes():
    rows = [{"LABEL_STATUS": "MATURE"}, {"LABEL_STATUS": "PENDING"}]
    c = LL.consumable(rows, "FAST", "FAST")
    assert c["MATURE_N"] == 1 and c["PENDING_EXCLUDED_N"] == 1


def test_policy_version_travels_with_the_data():
    rows = [{"POLICY_VERSION_ACTIVE_AT_T": "p1"}] * 3 + \
           [{"POLICY_VERSION_ACTIVE_AT_T": "p2"}]
    d = LL.data_by_policy_version(rows)
    assert d["DATA_BY_POLICY_VERSION"] == {"p1": 3, "p2": 1}
    assert "self-induced selection effect" in d["POLICY_FEEDBACK_GUARD"]


# --- Model registry, manifest, promotion. ----------------------------------

def test_a_model_without_lineage_may_not_enter_production():
    r = MR.register("m1", "SHORT_HORIZON_PRICE_MOVE", "v1")
    assert r["LINEAGE_COMPLETE"] is False
    assert r["MAY_ENTER_PRODUCTION"] is False
    assert "roll back to" in r["NO_PRODUCTION_WITHOUT_LINEAGE"]


def test_a_full_lineage_still_may_not_auto_promote():
    kw = {f: "x" for f in MR.REGISTRY_FIELDS}
    kw.pop("MODEL_ID"), kw.pop("MODEL_FAMILY"), kw.pop("VERSION")
    r = MR.register("m1", "SHORT_HORIZON_PRICE_MOVE", "v1", **kw)
    assert r["LINEAGE_COMPLETE"] is True
    assert r["MAY_ENTER_PRODUCTION"] is False
    assert MR.AUTO_PRODUCTION_PROMOTION == "DISABLED"


def test_the_training_manifest_is_hashed_and_detects_edits():
    m = MR.training_manifest(["f1"], "PRICE_MOVE_30S", ["e1"],
                             ("t0", "t1"), {"lr": 0.1},
                             {"TRAIN": ["e1"]}, ("LOG_LOSS",), ("ECON",))
    assert m["FROZEN"] is True
    assert MR.manifest_intact(m)["INTACT"] is True
    m["METRICS"] = ("ACCURACY",)
    assert MR.manifest_intact(m)["INTACT"] is False


def test_the_metric_may_not_be_chosen_after_the_result():
    assert "how a null becomes a discovery" in \
        MR.TRAINING_CODE_MAY_NOT_DECIDE_AFTER_THE_FACT


def test_accuracy_alone_cannot_promote():
    g = MR.promotion_gate("SHORT_HORIZON_PRICE_MOVE",
                          conditions_met=MR.PROMOTION_CONDITIONS,
                          economic_improvement=-0.001,
                          accuracy_improvement=0.05, authorized=True)
    assert g["MAY_PROMOTE"] is False
    assert any("ACCURACY_CANNOT_PROMOTE" in n for n in g["NOTES"])


def test_a_tie_is_not_a_win():
    g = MR.promotion_gate("SHORT_HORIZON_PRICE_MOVE",
                          conditions_met=MR.PROMOTION_CONDITIONS,
                          economic_improvement=0.0, authorized=True)
    assert g["MAY_PROMOTE"] is False
    assert any("TIE_IS_NOT_A_WIN" in n for n in g["NOTES"])


def test_passing_every_gate_still_needs_a_human():
    g = MR.promotion_gate("SHORT_HORIZON_PRICE_MOVE",
                          conditions_met=MR.PROMOTION_CONDITIONS,
                          economic_improvement=0.01, authorized=False)
    assert g["AUTOMATED_GATES_PASS"] is True
    assert g["MAY_PROMOTE"] is False
    assert "no human authorized it" in g["WHY_NOT"]


def test_an_execution_model_needs_real_fill_calibration():
    g = MR.promotion_gate("FILL_PROBABILITY",
                          conditions_met=MR.PROMOTION_CONDITIONS,
                          economic_improvement=0.01, is_execution_model=True,
                          authorized=True)
    assert "REAL_FILL_CALIBRATION" in g["CONDITIONS_NOT_MET"]
    assert g["MAY_PROMOTE"] is False


def test_online_weight_mutation_is_refused():
    assert MR.NO_ONLINE_WEIGHT_MUTATION is True
    assert "never the thing anybody validated" in MR.WHY_NO_ONLINE_MUTATION


def test_retraining_thresholds_are_not_invented():
    r = MR.retrain_check({"NEW_INDEPENDENT_EVENTS": 40})
    assert r["RETRAIN_TRIGGER_THRESHOLDS"] == \
        "NOT_IDENTIFIED_PENDING_PROSPECTIVE_DATA"
    assert r["ANY_TRIGGER_FIRED"] == "NOT_IDENTIFIED"


def test_a_burned_holdout_is_burned():
    h = MR.holdout_state(burned=["PROSPECTIVE_VALIDATION"])
    assert h["PROSPECTIVE_VALIDATION_AVAILABLE"] is False
    assert h["NEXT_ACTION"] == "create a new future holdout"


# --- Rollback, kill switches, levels, failed capture. ----------------------

def test_rollback_needs_a_target_and_an_authorization():
    r = MR.rollback_state(current="v3", previous="v2", last_known_good="v1",
                          degradation_detected=True, authorized=False)
    assert r["RECOMMENDS_ROLLBACK"] is True
    assert r["MAY_EXECUTE_ROLLBACK"] is False


def test_an_unreadable_kill_condition_counts_as_tripped():
    k = MR.kill_switch({})
    assert k["HALT"] is True
    assert len(k["UNREADABLE"]) == len(MR.KILL_CONDITIONS)
    assert "not a passing one" in k["FAIL_CLOSED"]


def test_all_clear_kill_switches_still_permit_no_trading():
    k = MR.kill_switch({c: False for c in MR.KILL_CONDITIONS})
    assert k["HALT"] is False
    assert k["TRADING_PERMITTED"] is False


def test_a_level_may_not_be_skipped():
    g = MR.level_gate("L0_OFFLINE_RESEARCH", "L3_MICRO_LIVE_RESEARCH",
                      gates_passed=["x"], authorized=True)
    assert g["MAY_PROMOTE"] is False
    assert g["REASON"] == "LEVEL_SKIP_REFUSED"
    assert "not a reason" in g["NO_LEVEL_SKIPPING"]


def test_the_current_level_is_offline_research():
    assert MR.CURRENT_LEVEL == "L0_OFFLINE_RESEARCH"


def test_failed_capture_data_may_only_debug_the_capture():
    u = MR.capture_data_use("FAIL")
    assert u["MAY_TRAIN"] is False
    assert u["PERMITTED_USE"] == "CAPTURE_DEBUGGING_ONLY"
    assert "learns the breakage" in u["NEVER_TRAIN_ON_FAILED_CAPTURE"]


def test_passing_capture_data_may_train():
    assert MR.capture_data_use("PASS")["MAY_TRAIN"] is True


def test_ope_is_blocked_without_propensities():
    o = MR.ope("IPS")
    assert o["MAY_ESTIMATE"] is False
    assert "does NOT identify counterfactual policy value" in \
        o["WITHOUT_PROPENSITIES_NO_OPE"]


def test_the_bandit_is_blocked():
    assert MR.CONTEXTUAL_BANDIT_STATUS == "BLOCKED_NO_LIVE_AUTHORIZATION"


# --- Drift, trust, OOD, calibration. ---------------------------------------

def test_a_sudden_shift_is_detected():
    ref = [1.0, 1.1, 0.9, 1.05, 0.95] * 10
    d = DT.drift(ref, [5.0, 5.1, 4.9, 5.05, 4.95], "SPREAD_DRIFT")
    assert d["DRIFT_KIND"] == "SUDDEN"
    assert d["DRIFT_DETECTED"] is True


def test_a_gradual_slide_is_detected_separately_from_a_jump():
    ref = [1.0, 1.1, 0.9, 1.05, 0.95] * 10
    creeping = [1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35]
    d = DT.drift(ref, creeping, "SPREAD_DRIFT")
    assert d["DRIFT_KIND"] == "GRADUAL"
    assert abs(d["Z_SCORE"]) < 3.0          # no single point is surprising


def test_ordinary_noise_is_not_gradual_drift():
    """The earlier threshold scaled with n and flagged noise as drift."""
    ref = [1.0, 1.1, 0.9, 1.05, 0.95] * 10
    noisy = [1.0, 1.05, 0.98, 1.02, 1.0]
    assert DT.drift(ref, noisy, "SPREAD_DRIFT")["DRIFT_KIND"] == "NONE"


def test_a_flat_reference_cannot_judge_drift_and_says_so():
    d = DT.drift([1.0] * 50, [5.0] * 10, "SPREAD_DRIFT")
    assert d["STATUS"] == "INSUFFICIENT_DATA"
    assert d["DRIFT_DETECTED"] == "NOT_IDENTIFIED"
    assert "not a passing one" in d["WHY_NOT_CALM"]


def test_a_stable_window_shows_no_drift():
    ref = [1.0, 1.1, 0.9, 1.05, 0.95] * 10
    d = DT.drift(ref, [1.0, 1.05, 0.98, 1.02, 1.0], "SPREAD_DRIFT")
    assert d["DRIFT_DETECTED"] is False


def test_drift_never_auto_deploys():
    r = DT.drift_response([{"MONITOR": "MARKOUT_DRIFT", "DRIFT_DETECTED": True,
                            "DRIFT_KIND": "SUDDEN"}])
    assert r["AUTO_DEPLOY"] is False
    assert "NO_TRADE" in r["RESPONSES"]
    assert "least stable" in r["DRIFT_NEVER_AUTO_DEPLOYS"]


def test_gradual_drift_gets_a_different_response_from_sudden():
    sudden = DT.drift_response([{"MONITOR": "M", "DRIFT_DETECTED": True,
                                 "DRIFT_KIND": "SUDDEN"}])
    gradual = DT.drift_response([{"MONITOR": "M", "DRIFT_DETECTED": True,
                                  "DRIFT_KIND": "GRADUAL"}])
    assert "NO_TRADE" in sudden["RESPONSES"]
    assert "NO_TRADE" not in gradual["RESPONSES"]
    assert "TRIGGER_CHALLENGER_RETRAIN" in gradual["RESPONSES"]


def test_trust_weights_are_not_manufactured():
    t = DT.model_trust({"DRIFT": 0.1})
    assert t["MODEL_TRUST_SCORE"] == "NOT_IDENTIFIED"
    assert "made-up number that then scales real capital" in \
        t["TRUST_WEIGHTS_NOT_MANUFACTURED"]


def test_epistemic_uncertainty_says_whether_more_data_helps():
    u = DT.uncertainty(point=0.5, aleatoric=0.1, epistemic=0.9)
    assert u["MORE_DATA_WOULD_HELP"] is True
    v = DT.uncertainty(point=0.5, aleatoric=0.9, epistemic=0.1)
    assert v["MORE_DATA_WOULD_HELP"] is False


def test_sizing_never_compensates_for_unidentified_ev():
    assert "a larger unknown, not a better trade" in \
        DT.SIZING_NEVER_COMPENSATES


def test_an_unseen_state_is_out_of_distribution_and_forces_no_trade():
    o = DT.ood("epl_new_market", training_keys=["epl_moneyline"])
    assert o["OOD_STATUS"] == "OUT_OF_DISTRIBUTION"
    assert "NO_TRADE" in o["FORCES"]


def test_thin_support_widens_the_buffer_rather_than_halting():
    o = DT.ood("epl_moneyline", ["epl_moneyline"], support_n=5)
    assert o["OOD_STATUS"] == "WEAK_SUPPORT"
    assert "WIDEN_UNCERTAINTY_BUFFER" in o["FORCES"]


def test_an_overconfident_interval_is_detected():
    ivs = [(0.0, 1.0)] * 10
    obs = [0.5] * 4 + [9.0] * 6
    c = DT.interval_coverage(ivs, obs, level=0.90)
    assert c["OVERCONFIDENT"] is True
    assert c["EMPIRICAL_COVERAGE"] == pytest.approx(0.4)
    assert "every EV built on it is overconfident" in \
        c["A_90_INTERVAL_SHOULD_CONTAIN_90_PERCENT"]


def test_a_systematic_posterior_miss_lowers_trust():
    p = DT.posterior_predictive("FILLS", predicted_mean=0.5,
                                observed_mean=0.1, predicted_sd=0.05, n=100)
    assert p["SYSTEMATIC_MISS"] is True
    assert "lower trust" in p["EFFECT"]


def test_a_persistent_prior_shift_is_proprietary_knowledge():
    s = DT.prior_shift_log("P_FILL", [0.1, 0.12, 0.09, 0.11])
    assert s["PERSISTENTLY_ONE_DIRECTION"] is True
    assert s["TRANSFERRED_PRIOR_LIKELY_WRONG_HERE"] is True
    assert "nobody else has that map" in \
        s["PRIOR_SHIFT_IS_PROPRIETARY_KNOWLEDGE"]


# --- Edge dashboard. -------------------------------------------------------

def test_every_edge_component_is_not_identified_today():
    b = ED.edge_board()
    assert len(b) == len(ED.EDGE_COMPONENTS)
    assert all(r["STATUS"] == "NOT_IDENTIFIED" for r in b.values())


def test_edge_status_is_derived_from_event_count():
    assert ED.edge_row("FILL_EDGE", 0.01, 0.005, 10)["STATUS"] == "HYPOTHESIS"
    assert ED.edge_row("FILL_EDGE", 0.01, 0.005, 100)["STATUS"] == "DETECTED"
    assert ED.edge_row("FILL_EDGE", 0.01, 0.005, 500)["STATUS"] == "REPLICATED"


def test_production_validated_cannot_be_reached_offline():
    r = ED.edge_row("FILL_EDGE", 0.01, 0.005, 5000)
    assert r["STATUS"] != "PRODUCTION_VALIDATED"
    assert "has never happened" in r["PRODUCTION_VALIDATED_REQUIRES_PRODUCTION"]


def test_there_is_no_binary_edge_flag():
    assert "compresses estimate, uncertainty" in ED.NO_BINARY_EDGE_FLAG


def test_a_weakening_edge_is_de_rated():
    d = ED.edge_decay(recent=0.002, medium=0.005, long=0.010)
    assert d["WEAKENING"] is True
    assert d["DE_RATE"] is True
    assert d["RECOMMENDED_STATUS_IF_WEAKENING"] == "DEGRADED"


def test_the_objective_is_not_that_edge_always_grows():
    assert "will keep trading it" in ED.THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS


def test_attribution_is_never_one_blended_number():
    a = ED.attribute({"SPREAD_CAPTURE": 0.01, "TOXICITY": 0.008})
    assert a["NET"] == "NOT_IDENTIFIED"      # most components unattributed
    assert len(a["UNATTRIBUTED_COMPONENTS"]) > 0
    assert "opposite responses" in ED.NOT_ONE_BLENDED_ALPHA


def test_an_interaction_needs_its_own_event_count():
    assert ED.interaction("MICROPRICE_X_EXTERNAL", 20)["MAY_ESTIMATE"] is False
    assert ED.interaction("MICROPRICE_X_EXTERNAL", 500)["MAY_ESTIMATE"] is True


def test_experiments_rank_by_information_per_dollar():
    r = ED.rank_experiments([
        {"EXPERIMENT": "CHEAP_BIG", "EXPECTED_REDUCTION_IN_DECISION_UNCERTAINTY": 0.8,
         "ECONOMIC_RELEVANCE": 1.0, "COST": 100},
        {"EXPERIMENT": "DEAR_SMALL", "EXPECTED_REDUCTION_IN_DECISION_UNCERTAINTY": 0.2,
         "ECONOMIC_RELEVANCE": 1.0, "COST": 5000}])
    assert r["TOP"] == "CHEAP_BIG"


def test_an_experiment_missing_a_term_is_refused_not_scored_zero():
    r = ED.rank_experiments([{"EXPERIMENT": "X", "COST": 100}])
    assert r["RANKED"] == []
    assert "not scored as zero" in r["REFUSED"][0]["WHY"]


def test_guaranteed_profit_language_is_caught():
    assert ED.language_check("this is a risk free trade")["TEXT_OK"] is False
    assert ED.language_check(
        "POSTERIOR_EXPECTED_EV_POSITIVE")["TEXT_OK"] is True


def test_confidence_is_not_an_arbitrary_number():
    d = ED.dashboard()
    assert all(v == "NOT_IDENTIFIED" for v in d["CONFIDENCE_BY_AREA"].values())
    assert "tied to explicit evidence gates" in \
        d["NO_ARBITRARY_CONFIDENCE_NUMBER"]


def test_the_daily_report_may_not_replace_a_model():
    r = ED.daily_report()
    assert r["MAY_REPLACE_A_MODEL"] is False
    assert "noise at this event count" in \
        r["A_DAILY_FLUCTUATION_CANNOT_REPLACE_A_MODEL"]


def test_the_weekly_review_recommends_but_never_promotes():
    w = ED.weekly_review()
    assert w["MAY_RECOMMEND_PROMOTION"] is True
    assert w["MAY_PROMOTE_AUTOMATICALLY"] is False


# --- The hard boundaries. --------------------------------------------------

def test_nothing_in_this_stack_trains_promotes_or_places():
    assert PR.NOTHING_IS_TRAINED_HERE is True
    assert MC.NOTHING_IS_TRAINED_HERE is True
    assert MC.NO_ORDER_IS_PLACED is True
    assert LL.NO_ORDER_IS_PLACED is True
    assert MR.AUTO_PRODUCTION_PROMOTION == "DISABLED"
    assert LL.AUTO_PRODUCTION_PROMOTION == "DISABLED"
    assert DT.NOTHING_IS_TRAINED_HERE is True
    assert ED.NOTHING_IS_TRAINED_HERE is True


def test_the_frozen_capture_and_thresholds_are_untouched():
    import capture_quality as CQ
    assert CQ.thresholds_intact()["INTACT"] is True
    assert CQ.FROZEN_QUALITY_THRESHOLDS_SHA == (
        "70e6ddda88ad4141ae3ed58691d8322cea4bf37a83c89efd481792c47243e7fb")


def test_the_register_agrees_with_the_modules():
    import ev_core_registers as R
    assert R.MODEL_SELECTION_PERFORMED == "NO"
    assert R.TRAINING_PERFORMED == "NO"
    assert R.PARAMETER_TUNING_PERFORMED == "NO"
    assert R.LIVE_ORDER_ACTIVITY == "NONE"
    assert R.CONTEXTUAL_BANDIT_STATUS == MR.CONTEXTUAL_BANDIT_STATUS
    assert R.OFF_POLICY_EVALUATION_STATUS == MR.OFF_POLICY_EVALUATION_STATUS
    c = PR.registry_census()
    assert R.PRIOR_REGISTRY_STATUS == "BUILT_%d_OF_%d_AVAILABLE" % (
        c["YES_COUNT"], c["DECLARED"])
