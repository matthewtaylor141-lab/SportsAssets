"""Regression tests for the third-pass correctness/integration audit of
179a38c.

Each test reproduces a defect against the real behaviour of the module. Where
the second pass fixed a NAME, these check the ARITHMETIC; where it added a
condition, these check that the condition cannot be satisfied by writing a
string.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

import hashlib
import json

import pytest

import action_ev_mc as A
import bettor_dataset as B
import ev_core_registers as R
import microstructure_v1 as M
import prior_registry as P
from prior_registry import Dist, NOT_IDENTIFIED


# --- fixtures --------------------------------------------------------------

_FS_PRIOR = P.PRIOR_REGISTRY["FILL_SELECTION_EFFECT"]


def terms(**over):
    """A fill-bearing action with every economic term resolved."""
    t = {
        "P_FILL": Dist("BETA", {"alpha": 3, "beta": 40}),
        "VALUE_IF_FILL": Dist("NORMAL", {"mu": 0.004, "sigma": 0.002}),
        "TOXICITY": Dist("NORMAL", {"mu": 0.001, "sigma": 0.0005}),
        "QUANTITY": 500,
        "FILL_SELECTION_CONVENTION": "EMBEDDED_IN_VALUE_IF_FILL",
        "VALUE_IF_FILL_INCLUDES_FILL_SELECTION": True,
    }
    for k in ("VALUE_IF_NO_FILL", "FEE", "REBATE", "INVENTORY_COST",
              "EXIT_COST"):
        t["%s_STATE" % k] = "KNOWN_ZERO"
    t.update(over)
    return {k: v for k, v in t.items() if v is not None}


def series(market="M1", n=12, step=24):
    rows = []
    for i in range(n):
        sec = i * step
        rows.append({
            "MARKET_ID": market,
            "DECISION_TIMESTAMP_UTC": "2026-09-17T00:%02d:%02dZ"
                                      % (sec // 60, sec % 60),
            "MID": 0.50 + i * 0.001,
            "BEST_BID": 0.49 + i * 0.001,
            "BEST_ASK": 0.51 + i * 0.001})
    return rows


def sealed_artifact(sha_field, **body):
    """An artifact sealed with its own digest, the way the gate checks it."""
    obj = dict(body)
    obj[sha_field] = hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()
    return obj


# --- 1. Effective N must change the posterior MATHS. ----------------------

def test_a_validated_effective_n_does_not_make_the_raw_posterior_decision_grade():
    prov = P.effective_n(raw_rows=100, effective_n=12,
                         effective_n_method="BLOCK_BOOTSTRAP_OVER_EVENTS",
                         relation_to_raw_rows="CLUSTERED_BY_EVENT")
    assert prov["EFFECTIVE_N_VALIDATION"]["VALID"] is True
    out = P.update_beta(P.beta_from_mean_n(0.2, 10), successes=5, trials=100,
                        n_provenance=prov)
    assert out["DECISION_GRADE_POSTERIOR"] == NOT_IDENTIFIED
    assert out["DIAGNOSTIC_RAW_ROW_POSTERIOR"] != NOT_IDENTIFIED
    assert out["POSTERIOR_PRECISION_STATUS"] == "DIAGNOSTIC_RAW_ROW_POSTERIOR"


def test_the_posterior_is_identical_whether_or_not_effective_n_validated():
    """The whole point: validating N did not change the distribution."""
    raw = P.update_beta(P.beta_from_mean_n(0.2, 10), 5, 100)
    prov = P.effective_n(raw_rows=100, effective_n=12,
                         effective_n_method="MEASURED_ICC_DESIGN_EFFECT",
                         relation_to_raw_rows="CLUSTERED_BY_EVENT")
    val = P.update_beta(P.beta_from_mean_n(0.2, 10), 5, 100,
                        n_provenance=prov)
    assert raw["DIAGNOSTIC_RAW_ROW_POSTERIOR_SUMMARY"]["MEAN"] == \
        val["DIAGNOSTIC_RAW_ROW_POSTERIOR_SUMMARY"]["MEAN"]
    assert raw["DIAGNOSTIC_RAW_ROW_POSTERIOR_SUMMARY"]["P05"] == \
        val["DIAGNOSTIC_RAW_ROW_POSTERIOR_SUMMARY"]["P05"]
    # Neither is decision grade, because neither was built from 12.
    assert raw["DECISION_GRADE_POSTERIOR"] == NOT_IDENTIFIED
    assert val["DECISION_GRADE_POSTERIOR"] == NOT_IDENTIFIED


def test_the_posterior_declares_the_counts_it_was_actually_built_from():
    out = P.update_beta(P.beta_from_mean_n(0.2, 10), 5, 100)
    assert out["POSTERIOR_BUILT_FROM"] == {"SUCCESSES": 5, "TRIALS": 100,
                                           "COUNT_BASIS": "RAW_TRIALS"}


def test_the_effective_likelihood_contract_is_declared_and_unfilled():
    out = P.update_beta(P.beta_from_mean_n(0.2, 10), 5, 100)
    c = out["EFFECTIVE_LIKELIHOOD_CONTRACT"]
    for f in P.EFFECTIVE_LIKELIHOOD_FIELDS:
        assert c[f] == NOT_IDENTIFIED, f
    assert c["EFFECTIVE_N_LIKELIHOOD_STATUS"] == \
        "NOT_IMPLEMENTED_RAW_ROW_LIKELIHOOD_ONLY"


def test_update_normal_cannot_narrow_from_raw_n_while_effective_n_is_unapplied():
    prior = Dist("NORMAL", {"mu": 0.0, "sigma": 0.01})
    prov = P.effective_n(raw_rows=100, effective_n=12,
                         effective_n_method="EVENT_CLUSTERED_DESIGN_EFFECT",
                         relation_to_raw_rows="CLUSTERED_BY_EVENT")
    out = P.update_normal(prior, obs_mean=0.004, obs_sigma=0.01, n=100,
                          n_provenance=prov)
    # It did narrow, on raw n -- readable only under the diagnostic name.
    assert out["UNCERTAINTY_FELL_BY"] == NOT_IDENTIFIED
    assert out["DIAGNOSTIC_RAW_ROW_UNCERTAINTY_FELL_BY"] > 0
    assert out["DECISION_GRADE_POSTERIOR"] == NOT_IDENTIFIED
    assert out["POSTERIOR_PRECISION_STATUS"] == "DIAGNOSTIC_RAW_ROW_POSTERIOR"


def test_update_normal_carries_its_n_provenance_at_all():
    out = P.update_normal(Dist("NORMAL", {"mu": 0.0, "sigma": 0.01}),
                          0.004, 0.01, 10)
    assert "N_PROVENANCE" in out
    assert out["POSTERIOR_PRECISION_STATUS"] in \
        P.POSTERIOR_PRECISION_STATUSES


def test_both_updaters_speak_one_posterior_vocabulary():
    b = P.update_beta(P.beta_from_mean_n(0.2, 10), 5, 100)
    n = P.update_normal(Dist("NORMAL", {"mu": 0.0, "sigma": 0.01}),
                        0.004, 0.01, 10)
    assert b["POSTERIOR_PRECISION_STATUS"] in P.POSTERIOR_PRECISION_STATUSES
    assert n["POSTERIOR_PRECISION_STATUS"] in P.POSTERIOR_PRECISION_STATUSES
    assert "MODELLED" not in P.POSTERIOR_PRECISION_STATUSES


# --- 2. Capital-hour accounting. ------------------------------------------

def _cap_terms(qty):
    return terms(QUANTITY=qty, CAPITAL_REQUIRED=40.0,
                 OCCUPANCY_SECONDS=1800.0)


def test_the_capital_hour_rate_scales_with_quantity():
    a = A.action_ev_mc("POST_BID", _cap_terms(500), draws=2000)
    b = A.action_ev_mc("POST_BID", _cap_terms(5000), draws=2000)
    assert a["EV_PER_CAPITAL_HOUR_MEAN"] != NOT_IDENTIFIED
    ratio = b["EV_PER_CAPITAL_HOUR_MEAN"] / a["EV_PER_CAPITAL_HOUR_MEAN"]
    assert ratio == pytest.approx(10.0, rel=1e-6)


def test_capital_hours_has_the_dimensions_of_dollar_hours():
    r = A.action_ev_mc("POST_BID", _cap_terms(500), draws=500)
    # 40 USD held for 1800 s = 0.5 h -> 20 dollar-hours.
    assert r["CAPITAL_HOURS_MEAN"] == pytest.approx(20.0)
    assert r["CAPITAL_REQUIRED_MEAN_USD"] == pytest.approx(40.0)
    assert r["OCCUPANCY_TIME_MEAN_HOURS"] == pytest.approx(0.5)
    assert r["CAPITAL_RATE_UNIT"] == "USD_PER_CAPITAL_HOUR"


def test_the_rate_equals_total_net_usd_over_capital_hours():
    r = A.action_ev_mc("POST_BID", _cap_terms(500), draws=2000)
    assert r["TOTAL_NET_USD_MEAN"] == pytest.approx(r["EV_MEAN"] * 500,
                                                    rel=1e-6)
    assert r["EV_PER_CAPITAL_HOUR_MEAN"] == pytest.approx(
        r["TOTAL_NET_USD_MEAN"] / r["CAPITAL_HOURS_MEAN"], rel=1e-6)


def test_both_rates_are_published_and_named_apart():
    r = A.action_ev_mc("POST_BID", _cap_terms(500), draws=500)
    assert isinstance(r["E_EXPECTED_RATIO"], float)
    assert isinstance(r["RATIO_OF_EXPECTATIONS"], float)
    assert "RATIO_OF_EXPECTATIONS" in \
        r["THE_TWO_RATES_ARE_NOT_THE_SAME_NUMBER"]


def test_the_capital_rate_needs_a_quantity():
    t = _cap_terms(500)
    t.pop("QUANTITY")
    r = A.action_ev_mc("POST_BID", t, draws=500)
    assert r["EV_PER_CAPITAL_HOUR_MEAN"] == NOT_IDENTIFIED
    assert r["CAPITAL_RATE_STATUS"] == "QUANTITY_NOT_DECLARED"


def test_capital_occupancy_mean_is_gone_as_a_name_for_dollars():
    r = A.action_ev_mc("POST_BID", _cap_terms(500), draws=500)
    assert "CAPITAL_OCCUPANCY_MEAN" not in r


# --- 3. The canonical label artifact is load-bearing at the scorer. -------

def _artifact_rows(target="MID_MOVE_60S", n=12, tamper=False,
                   with_sha=True):
    s = series()
    art = B.label_artifact(dict(s[0], DECISION_ID="D1"), s,
                           capture_spec_sha="capture-spec-sha")
    if tamper:
        art = dict(art)
        art["LABELS"] = dict(art["LABELS"])
        art["LABELS"]["MID_MOVE_60S"] = 9.99
    rows = []
    for _ in range(n):
        r = {target: 0.002, "%s_STATUS" % target: "PRESENT"}
        if with_sha:
            r["LABEL_ARTIFACT_SHA"] = art["LABEL_ARTIFACT_SHA"]
        rows.append(r)
    return rows, {art["LABEL_ARTIFACT_SHA"]: art}


def test_a_hand_built_status_column_cannot_enter_scoring():
    rows = [{"MID_MOVE_60S": 0.002, "MID_MOVE_60S_STATUS": "PRESENT"}] * 12
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0)
    assert g["MAY_SCORE"] is False
    assert g["REASON"] == "LABEL_ARTIFACT_SHA_ABSENT"


def test_a_sha_with_no_artifact_to_check_is_not_verified():
    rows, _ = _artifact_rows()
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0)
    assert g["MAY_SCORE"] is False
    assert g["REASON"] == "LABEL_ARTIFACT_NOT_VERIFIED"
    assert g["UNRESOLVABLE_ARTIFACT_SHAS"]


def test_a_tampered_artifact_refuses_the_target():
    rows, arts = _artifact_rows(tamper=True)
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0,
                              label_artifacts=arts)
    assert g["MAY_SCORE"] is False
    assert g["REASON"] == "LABEL_ARTIFACT_NOT_VERIFIED"


def test_a_verified_artifact_admits_the_target():
    rows, arts = _artifact_rows()
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0,
                              label_artifacts=arts)
    assert g["MAY_SCORE"] is True
    assert g["LABEL_PROVENANCE"] == "VERIFIED_CANONICAL_ARTIFACT"


def test_score_baselines_refuses_without_verified_artifacts():
    rows, _ = _artifact_rows()
    out = M.score_baselines(rows, "MID_MOVE_60S", min_coverage_pct=0.0)
    assert out["STATUS"] == "REFUSED"


# --- 3b. The observation chain. -------------------------------------------

def test_the_artifact_binds_the_origin_and_forward_rows():
    s = series()
    art = B.label_artifact(dict(s[0], DECISION_ID="D1"), s,
                           capture_spec_sha="capture-spec-sha")
    rec = art["OBSERVATION_CHAIN"]["MID_MOVE_60S"]
    for f in B.OBSERVATION_CHAIN_FIELDS:
        assert rec[f] not in (None, NOT_IDENTIFIED), f
    assert rec["SOURCE_ORIGIN_ROW_HASH"] != rec["SOURCE_FORWARD_ROW_HASH"]
    assert rec["TARGET_HORIZON_S"] == 60


def test_the_artifact_names_the_code_spec_and_capture_it_came_from():
    s = series()
    art = B.label_artifact(dict(s[0], DECISION_ID="D1"), s,
                           capture_spec_sha="capture-spec-sha")
    assert len(art["LABEL_BUILDER_CODE_SHA"]) == 64
    assert len(art["LABEL_SPEC_SHA"]) == 64
    assert art["CAPTURE_SPEC_SHA"] == "capture-spec-sha"


def test_an_artifact_with_no_capture_spec_is_chain_incomplete():
    s = series()
    art = B.label_artifact(dict(s[0], DECISION_ID="D1"), s)
    assert art["OBSERVATION_CHAIN_STATUS"] == "INCOMPLETE"
    assert B.verify_label_artifact(art)["LABEL_PROVENANCE_STATUS"] == \
        "OBSERVATION_CHAIN_INCOMPLETE"


def test_two_different_forward_rows_give_two_different_chains():
    a = B.label_artifact(dict(series("M1")[0], DECISION_ID="D1"),
                         series("M1"), capture_spec_sha="c")
    b = B.label_artifact(dict(series("M2")[0], DECISION_ID="D1"),
                         series("M2"), capture_spec_sha="c")
    ca = a["OBSERVATION_CHAIN"]["MID_MOVE_60S"]
    cb = b["OBSERVATION_CHAIN"]["MID_MOVE_60S"]
    assert ca["SOURCE_FORWARD_ROW_HASH"] != cb["SOURCE_FORWARD_ROW_HASH"]
    assert a["LABEL_ARTIFACT_SHA"] != b["LABEL_ARTIFACT_SHA"]


# --- 4. Fill selection fails closed on a fill-bearing action. -------------

@pytest.mark.parametrize("action", A.FILL_BEARING_ACTIONS)
def test_excluded_is_refused_on_every_fill_bearing_action(action):
    r = A.action_ev_mc(action, terms(FILL_SELECTION_CONVENTION="EXCLUDED"),
                       draws=200)
    assert r["ACTION_EV_STATUS"] == \
        "FILL_SELECTION_EXCLUDED_ON_FILL_BEARING_ACTION"
    assert r["EV_MEAN"] == NOT_IDENTIFIED


def test_separate_term_without_the_effect_is_not_fully_identified():
    r = A.action_ev_mc(
        "POST_BID", terms(FILL_SELECTION_CONVENTION="SEPARATE_TERM",
                          VALUE_IF_FILL_INCLUDES_FILL_SELECTION=None),
        draws=200)
    assert r["ACTION_EV_STATUS"] == "NOT_FULLY_IDENTIFIED"
    assert r["FILL_SELECTION_CHECK"]["REASON"] == \
        "FILL_SELECTION_EFFECT_NOT_RESOLVED"


def test_separate_term_with_a_provenance_backed_effect_is_admitted():
    r = A.action_ev_mc("POST_BID", terms(
        FILL_SELECTION_CONVENTION="SEPARATE_TERM",
        VALUE_IF_FILL_INCLUDES_FILL_SELECTION=None,
        FILL_SELECTION_EFFECT=Dist("NORMAL", {"mu": 0.0, "sigma": 0.004}),
        FILL_SELECTION_EFFECT_EVIDENCE_CLASS="ESTIMATED_PRIOR",
        FILL_SELECTION_EFFECT_PRIOR_SHA=_FS_PRIOR["PRIOR_SHA"]), draws=200)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"
    assert r["FILL_SELECTION_CHECK"]["PROVENANCE_BACKED"] is True


def test_embedded_must_be_declared_by_value_if_fill_itself():
    r = A.action_ev_mc("POST_BID", terms(
        VALUE_IF_FILL_INCLUDES_FILL_SELECTION=None), draws=200)
    assert r["ACTION_EV_STATUS"] == "FILL_SELECTION_EMBEDDING_NOT_DECLARED"


def test_a_non_fill_bearing_action_is_unaffected():
    r = A.action_ev_mc("CANCEL", terms(
        FILL_SELECTION_CONVENTION="EXCLUDED",
        VALUE_IF_FILL_INCLUDES_FILL_SELECTION=None), draws=200)
    assert r["ACTION_EV_STATUS"] == "IDENTIFIED"


# --- 5. Provenance is verified, not merely present. -----------------------

def test_a_typed_in_prior_sha_does_not_verify_provenance():
    t = terms(P_FILL_EVIDENCE_CLASS="ESTIMATED_PRIOR",
              P_FILL_PRIOR_SHA="anything")
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert "P_FILL" in r["UNVERIFIED_INPUT_TERMS"]
    assert r["EVIDENCE_PROVENANCE_VERIFIED"] is False
    prov = r["EVIDENCE_PROVENANCE"]["P_FILL"]
    assert prov["REFERENCE_PRESENT"] is True
    assert prov["REFERENCE_VERIFIED"] is False
    assert prov["REFERENCE_RESOLUTION"] == "UNRESOLVABLE"


def test_a_real_sealed_prior_sha_verifies():
    t = terms(P_FILL=Dist("NORMAL", {"mu": 0.0, "sigma": 0.004}),
              P_FILL_EVIDENCE_CLASS="ESTIMATED_PRIOR",
              P_FILL_PRIOR_SHA=_FS_PRIOR["PRIOR_SHA"])
    res = A.resolve_terms(t)
    prov = res["EVIDENCE_PROVENANCE"]["P_FILL"]
    assert prov["REFERENCE_VERIFIED"] is True
    assert prov["STATE"] == "ESTIMATED_PRIOR"


def test_a_narrative_source_reference_alone_is_not_decision_grade():
    t = terms(P_FILL_EVIDENCE_CLASS="ESTIMATED_PRIOR",
              P_FILL_SOURCE_REFERENCE="a paper I read")
    res = A.resolve_terms(t)
    prov = res["EVIDENCE_PROVENANCE"]["P_FILL"]
    assert prov["SOURCE_REFERENCED"] is True
    assert prov["REFERENCE_VERIFIED"] is False
    assert prov["REFERENCE_RESOLUTION"] == "NARRATIVE_ONLY"
    assert prov["STATE"] == A.UNVERIFIED_INPUT


def test_a_measurement_manifest_must_recompute():
    body = {"CAPTURE": "run85", "ROWS": 9269}
    good = sealed_artifact("MEASUREMENT_MANIFEST_SHA", **body)
    t = terms(P_FILL_EVIDENCE_CLASS="MEASURED_BETTOR_NATIVE",
              P_FILL_MEASUREMENT_MANIFEST_SHA=good["MEASUREMENT_MANIFEST_SHA"],
              EVIDENCE_SOURCES={"MEASUREMENT_MANIFESTS": [good]})
    res = A.resolve_terms(t)
    assert res["EVIDENCE_PROVENANCE"]["P_FILL"]["REFERENCE_VERIFIED"] is True
    bad = dict(good, ROWS=1)
    t2 = terms(P_FILL_EVIDENCE_CLASS="MEASURED_BETTOR_NATIVE",
               P_FILL_MEASUREMENT_MANIFEST_SHA=good[
                   "MEASUREMENT_MANIFEST_SHA"],
               EVIDENCE_SOURCES={"MEASUREMENT_MANIFESTS": [bad]})
    res2 = A.resolve_terms(t2)
    assert res2["EVIDENCE_PROVENANCE"]["P_FILL"]["REFERENCE_VERIFIED"] is False


# --- 6. The gate is bound to artifacts, not strings. ----------------------

def test_writing_valid_four_times_does_not_pass_four_conditions():
    t = terms(POSTERIOR_PRECISION_STATUS="DECISION_GRADE_POSTERIOR",
              LABEL_PROVENANCE_STATUS="VALID",
              COMMON_SUPPORT_STATUS="VALID",
              DATA_QUALITY_GATE="PASS")
    r = A.action_ev_mc("POST_BID", t, draws=200)
    for c in ("POSTERIOR_PRECISION_VALID", "LABEL_PROVENANCE_VALID",
              "COMMON_SUPPORT_EVALUATION_VALID", "DATA_QUALITY_GATE_PASSED"):
        assert c in r["DECISION_GRADE_BLOCKERS"], c


def test_a_sealed_artifact_no_longer_satisfies_its_own_condition():
    """SUPERSEDED BY THE FOURTH PASS. This object hashes to itself and says
    nothing: no observation chain, no capture spec, no trusted origin. The
    third pass accepted it, which is the defect the fourth pass fixed --
    integrity is not validity, and neither is trust. The inverted assertion
    is kept here so the old behaviour cannot come back."""
    art = sealed_artifact("LABEL_ARTIFACT_SHA", LABELS={"MID_MOVE_60S": 0.002})
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["DECISION_GRADE_CHECKS"]["LABEL_PROVENANCE_VALID"] is False
    assert "LABEL_PROVENANCE_VALID" in r["DECISION_GRADE_BLOCKERS"]


def test_a_tampered_gate_artifact_does_not_satisfy_its_condition():
    art = sealed_artifact("LABEL_ARTIFACT_SHA", LABELS={"MID_MOVE_60S": 0.002})
    art = dict(art, LABELS={"MID_MOVE_60S": 9.99})
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["DECISION_GRADE_CHECKS"]["LABEL_PROVENANCE_VALID"] is False


def test_the_posterior_vocabulary_is_shared_between_the_modules():
    assert "DECISION_GRADE_POSTERIOR" in P.POSTERIOR_PRECISION_STATUSES


# --- 7. UNIT and BASIS must agree; conditioning must fit the term. --------

def test_an_incompatible_unit_basis_pair_is_refused():
    t = terms(FEE=0.01, FEE_UNIT="USD_PER_ORDER",
              FEE_BASIS="PER_FILLED_SHARE")
    t.pop("FEE_STATE")
    r = A.action_ev_mc("POST_BID", t)
    assert r["ACTION_EV_STATUS"] == "INVALID_UNIT_CONTRACT"
    assert any(v["WHY"] == "UNIT_BASIS_INCOMPATIBLE"
               for v in r["UNIT_CONTRACT_VIOLATIONS"])


def test_value_if_fill_cannot_be_declared_on_no_fill():
    r = A.action_ev_mc("POST_BID", terms(
        VALUE_IF_FILL_APPLIES_WHEN="ON_NO_FILL"))
    assert r["ACTION_EV_STATUS"] == "INVALID_UNIT_CONTRACT"
    assert any(v["WHY"] == "INADMISSIBLE_CONDITIONING_FOR_TERM"
               for v in r["UNIT_CONTRACT_VIOLATIONS"])


def test_value_if_no_fill_cannot_be_declared_on_fill():
    t = terms(VALUE_IF_NO_FILL=0.0, VALUE_IF_NO_FILL_APPLIES_WHEN="ON_FILL")
    t.pop("VALUE_IF_NO_FILL_STATE")
    r = A.action_ev_mc("POST_BID", t)
    assert r["ACTION_EV_STATUS"] == "INVALID_UNIT_CONTRACT"


def test_a_fee_may_legitimately_be_always_or_on_fill():
    for when, unit, basis in (("ALWAYS", "USD_PER_POSTED_SHARE",
                               "PER_POSTED_SHARE"),
                              ("ON_FILL", "USD_PER_FILLED_SHARE",
                               "PER_FILLED_SHARE")):
        t = terms(FEE=0.001, FEE_APPLIES_WHEN=when, FEE_UNIT=unit,
                  FEE_BASIS=basis)
        t.pop("FEE_STATE")
        r = A.action_ev_mc("POST_BID", t, draws=200)
        assert r["ACTION_EV_STATUS"] == "IDENTIFIED", when


def test_the_default_contract_is_versioned_and_travels_in_the_evaluation_id():
    assert "DEFAULT_TERM_CONTRACT_SHA" in A.EVALUATION_ID_FIELDS
    eid = A.evaluation_id("POST_BID", terms())
    assert eid["DEFAULT_TERM_CONTRACT_SHA"] == A.default_term_contract_sha()
    assert len(eid["DEFAULT_TERM_CONTRACT_SHA"]) == 64


# --- 8. No Monte Carlo clipping. -----------------------------------------

def test_an_unbounded_probability_family_is_not_clipped_into_a_bounded_law():
    # sigma 0.16 keeps the P001..P999 envelope inside [0, 1] so the
    # specification is admitted, while the tails still land outside.
    # The fourth pass took this further: the draws are not clipped AND they
    # are not multiplied either. The diagnostic run is where the excursion
    # count is now readable, and it publishes no EV statistic.
    t = terms(P_FILL=Dist("NORMAL", {"mu": 0.5, "sigma": 0.16}))
    r = A.action_ev_mc("POST_BID", t, draws=20000,
                       shadow_distribution_diagnostic=True)
    diag = r["SHADOW_DISTRIBUTION_DIAGNOSTIC"]
    assert diag["DIAGNOSTIC_DOMAIN_EXCURSION_DRAWS"].get("P_FILL", 0) > 0
    assert r["MONTE_CARLO_CLIPPING"] == "REMOVED"
    assert "P_FILL" in r["SHADOW_APPROXIMATION_ONLY_TERMS"]
    assert "DISTRIBUTION_DOMAINS_DECISION_GRADE" in \
        r["DECISION_GRADE_BLOCKERS"]
    assert r["EV_MEAN"] == NOT_IDENTIFIED


def test_clipping_is_gone_from_the_source_of_the_draw_loop():
    import inspect
    src = inspect.getsource(A.action_ev_mc)
    assert "x = min(x, hi)" not in src
    assert "x = max(x, lo)" not in src


def test_a_bounded_family_has_no_excursions_at_all():
    r = A.action_ev_mc("POST_BID", terms(), draws=2000)
    assert r["DOMAIN_EXCURSION_DRAWS"].get("P_FILL", 0) == 0


# --- 9. No market identity, no forward label. -----------------------------

def test_a_series_with_no_market_identity_anywhere_is_refused():
    ticks = [{"BEST_BID": 0.49, "BEST_ASK": 0.51,
              "REQUEST_UTC": "2026-09-17T18:0%d:00Z" % i} for i in range(4)]
    rows, meta = M.build_targets(ticks, horizons=(30,))
    assert rows == []
    assert meta["STATUS"] == "REFUSED_NO_MARKET_IDENTITY"


def test_caller_asserted_single_market_is_gone():
    import inspect
    src = inspect.getsource(M)
    assert 'identity_status = "CALLER_ASSERTED_SINGLE_MARKET"' not in src
    ticks = [{"MARKET_ID": "A", "BEST_BID": 0.49, "BEST_ASK": 0.51,
              "REQUEST_UTC": "2026-09-17T18:0%d:00Z" % i} for i in range(4)]
    _, meta = M.build_targets(ticks, horizons=(30,))
    assert meta["IDENTITY_STATUS"] == "MARKET_IDENTITY_ESTABLISHED"


# --- 10. Unknown is not negative. ----------------------------------------

def test_a_blocked_gate_returns_not_identified_not_false():
    r = A.action_ev_mc("POST_BID", terms(), draws=500)
    out = A.robustly_positive(r)
    # The fourth pass replaced the truthy word with None and moved the word
    # into its own status field.
    assert out["ROBUSTLY_POSITIVE"] is None
    assert out["ROBUSTLY_POSITIVE"] is not False
    assert out["ROBUST_POSITIVITY_STATUS"] == NOT_IDENTIFIED
    assert out["REASON"] == "DECISION_GRADE_BLOCKED"
    assert isinstance(out["SHADOW_ROBUSTNESS_DIAGNOSTIC"], float)


def test_an_unidentified_ev_is_also_not_identified_not_false():
    out = A.robustly_positive({"EV_P10": NOT_IDENTIFIED})
    assert out["ROBUSTLY_POSITIVE"] is None
    assert out["ROBUST_POSITIVITY_STATUS"] == NOT_IDENTIFIED
    assert "UNKNOWN_IS_NOT_NEGATIVE" in out


def test_the_unknown_reading_is_never_truthy_in_the_boolean_field():
    """SUPERSEDED BY THE FOURTH PASS. The third pass put the word
    NOT_IDENTIFIED in ROBUSTLY_POSITIVE, and a non-empty string is truthy:
    `if robustly_positive(...)["ROBUSTLY_POSITIVE"]` read 'we do not know' as
    'yes'. A boolean field now holds True, False or None, and the word lives
    in ROBUST_POSITIVITY_STATUS where nothing tests it for truth."""
    out = A.robustly_positive({"EV_P10": NOT_IDENTIFIED})
    assert bool(out["ROBUSTLY_POSITIVE"]) is False
    assert out["ROBUSTLY_POSITIVE"] is not True
    assert out["ROBUSTLY_POSITIVE"] is not False
    assert out["ROBUST_POSITIVITY_STATUS"] == NOT_IDENTIFIED


# --- 11. The fill-quantity model is a load-bearing blocker. --------------

def test_a_fill_bearing_action_is_blocked_on_the_fill_quantity_model():
    r = A.action_ev_mc("POST_BID", terms(), draws=200)
    assert r["DECISION_GRADE_CHECKS"]["FILL_QUANTITY_MODEL_VALIDATED"] \
        is False
    assert "FILL_QUANTITY_MODEL_VALIDATED" in r["DECISION_GRADE_BLOCKERS"]


def test_a_non_fill_bearing_action_is_not_blocked_on_it():
    t = terms(FILL_SELECTION_CONVENTION="EXCLUDED",
              VALUE_IF_FILL_INCLUDES_FILL_SELECTION=None)
    r = A.action_ev_mc("CANCEL", t, draws=200)
    assert r["DECISION_GRADE_CHECKS"]["FILL_QUANTITY_MODEL_VALIDATED"] is True


def test_a_partial_fill_quantity_model_does_not_satisfy_the_blocker():
    t = terms(FILL_QUANTITY_MODEL={"FILL_QUANTITY_STATUS": "VALIDATED",
                                   "P_ANY_FILL": 0.2,
                                   "P_FULL_FILL": NOT_IDENTIFIED,
                                   "EXPECTED_FILL_FRACTION": 0.1,
                                   "EXPECTED_FILLED_QTY": 50})
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["DECISION_GRADE_CHECKS"]["FILL_QUANTITY_MODEL_VALIDATED"] \
        is False


def test_the_condition_is_in_the_declared_gate_list():
    assert "FILL_QUANTITY_MODEL_VALIDATED" in A.DECISION_GRADE_CONDITIONS


# --- 12. No vacuous assertions; the register carries the statuses. -------

def test_no_test_in_this_suite_asserts_a_tautology():
    """The second pass left one assertion that passed whatever the module
    did, because it was disjoined with a literal truth. The two forbidden
    fragments are built here rather than written out, so this check does not
    match its own source."""
    import inspect
    import sys
    src = inspect.getsource(sys.modules[__name__])
    tautology = " " + "or" + " True"
    dir_probe = "in " + "dir("
    assert tautology not in src
    assert dir_probe not in src


def test_the_register_carries_every_third_pass_status():
    d = R.describe()
    for k in ("EFFECTIVE_N_POSTERIOR_MATH_STATUS",
              "NORMAL_POSTERIOR_EFFECTIVE_N_STATUS",
              "CAPITAL_HOUR_DIMENSIONAL_STATUS",
              "CANONICAL_LABEL_SCORER_BINDING_STATUS",
              "LABEL_OBSERVATION_CHAIN_STATUS",
              "FILL_SELECTION_FAIL_CLOSED_STATUS",
              "PROVENANCE_VERIFICATION_STATUS",
              "DECISION_GATE_ARTIFACT_BINDING_STATUS",
              "UNIT_BASIS_COMPATIBILITY_STATUS",
              "MONTE_CARLO_CLIPPING_STATUS",
              "NO_IDENTITY_LABEL_STATUS",
              "ROBUST_POSITIVITY_UNKNOWN_STATUS",
              "FILL_QUANTITY_DECISION_GATE_STATUS"):
        assert k in d, k
        assert d[k] != NOT_IDENTIFIED


def test_the_decision_grade_gate_remains_blocked_and_says_why():
    r = A.action_ev_mc("POST_BID", terms(), draws=200)
    assert r["DECISION_GRADE_ACTION_EV_STATUS"] == "BLOCKED"
    assert "DEPENDENCE_MODEL_VALIDATED" in r["DECISION_GRADE_BLOCKERS"]
    assert r["NO_ORDER_IS_PLACED"] is True
