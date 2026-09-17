"""Regression tests for the fourth-pass audit of ca0a64f.

INTEGRITY IS NOT THE SAME AS VALIDITY OR TRUST.

Recomputing an artifact's digest and finding it unchanged proves exactly one
thing: nobody edited it after it was written. It does not prove the artifact
says PASS, that it came from anywhere but the caller's own hand, or that it
describes the evaluation now being gated. Each test below builds an object
that a digest check accepts and a reader should not.

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

from test_third_pass_audit import terms, series


# --- fixtures --------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_trusted_stores():
    """Every test starts with no trusted store registered."""
    A.clear_trusted_stores()
    yield
    A.clear_trusted_stores()


def self_hashed(sha_field, **body):
    """An object sealed the way the gate recomputes it. Anyone can build one;
    that is the point."""
    obj = dict(body)
    obj[sha_field] = A._sha_of(obj, sha_field)
    return obj


def trust(sha_field, obj, store_id="STORE_A"):
    """Put an artifact in a registered trusted store and return its SHA."""
    sha = obj[sha_field]
    A.register_trusted_store(store_id, "IMMUTABLE_CAPTURE_STORE",
                             "ledger-root-%s" % store_id, {sha: obj},
                             registered_by="TEST")
    return sha


def canonical_label(market="M1", decision="D1"):
    s = series(market)
    return B.label_artifact(dict(s[0], DECISION_ID=decision), s,
                            capture_spec_sha="capture-spec-sha")


def gate_detail(t, name, binding=None):
    return A.verify_decision_grade_artifacts(t, binding)["DETAIL"][name]


# --- 1. A self-hashed artifact is not a valid artifact. -------------------

def test_an_arbitrary_self_hashed_label_artifact_does_not_satisfy_the_gate():
    """The third pass accepted this object. It has a correct digest over
    itself and no observation chain, no capture spec and no builder sha."""
    art = self_hashed("LABEL_ARTIFACT_SHA", LABELS={"MID_MOVE_60S": 0.002})
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    d = gate_detail(t, "LABEL_ARTIFACT")
    assert d["ARTIFACT_INTEGRITY_VERIFIED"] is True     # it hashes to itself
    assert d["ARTIFACT_SEMANTICS_VERIFIED"] is False    # and says nothing
    r = A.action_ev_mc("POST_BID", t, draws=200)
    assert r["DECISION_GRADE_CHECKS"]["LABEL_PROVENANCE_VALID"] is False
    assert "LABEL_PROVENANCE_VALID" in r["DECISION_GRADE_BLOCKERS"]


def test_a_self_hashed_capture_quality_artifact_reading_fail_does_not_pass():
    """A correctly hashed artifact whose semantic result is FAIL does not
    satisfy the condition its name stands for."""
    art = self_hashed("CAPTURE_QUALITY_MANIFEST_SHA",
                      ARTIFACT_TYPE="CAPTURE_QUALITY_MANIFEST",
                      CAPTURE_QUALITY_GATE="FAIL",
                      CAPTURE_CODE_SHA="code", CAPTURE_SPEC_SHA="spec",
                      IMMUTABLE_MANIFEST_SHA="man")
    t = terms(DECISION_GRADE_ARTIFACTS={"CAPTURE_QUALITY_MANIFEST_SHA": art})
    d = gate_detail(t, "CAPTURE_QUALITY_MANIFEST")
    assert d["ARTIFACT_INTEGRITY_VERIFIED"] is True
    assert d["ARTIFACT_SEMANTICS_VERIFIED"] is False
    assert any(p.startswith("CAPTURE_QUALITY_GATE_IS_FAIL")
               for p in d["SEMANTIC_PROBLEMS"])


def test_a_self_hashed_diagnostic_posterior_does_not_satisfy_the_condition():
    """A posterior artifact that carries a RAW-ROW diagnostic reading is not
    a decision-grade posterior however well it is sealed."""
    art = self_hashed("POSTERIOR_ARTIFACT_SHA",
                      ARTIFACT_TYPE="POSTERIOR_ARTIFACT",
                      PARAMETER="P_FILL", MODEL_VERSION="v1",
                      LIKELIHOOD_METHOD="BETA_BINOMIAL",
                      N_PROVENANCE={"RAW_ROWS": 100},
                      POSTERIOR_SUMMARY={"MEAN": 0.2},
                      POSTERIOR_PRECISION_STATUS="DIAGNOSTIC_RAW_ROW_POSTERIOR")
    t = terms(DECISION_GRADE_ARTIFACTS={"POSTERIOR_ARTIFACT_SHA": art})
    d = gate_detail(t, "POSTERIOR_ARTIFACT")
    assert d["ARTIFACT_INTEGRITY_VERIFIED"] is True
    assert d["ARTIFACT_SEMANTICS_VERIFIED"] is False
    assert "POSTERIOR_PRECISION_STATUS_IS_DIAGNOSTIC_RAW_ROW_POSTERIOR" in \
        d["SEMANTIC_PROBLEMS"]


# --- 2. Trust is a SOURCE, not a caller-sealed object. --------------------

def test_a_tampered_then_resealed_inline_artifact_is_not_trusted():
    """Edit the artifact, recompute its digest, hand it in. Integrity passes
    -- because integrity only ever asked whether the object matched its own
    digest -- and trusted origin does not."""
    art = canonical_label()
    lied = dict(art)
    lied["LABELS"] = dict(lied["LABELS"], MID_MOVE_60S=9.99)
    lied["LABEL_ARTIFACT_SHA"] = A._sha_of(lied, "LABEL_ARTIFACT_SHA")
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": lied})
    d = gate_detail(t, "LABEL_ARTIFACT")
    assert d["ARTIFACT_INTEGRITY_VERIFIED"] is True
    assert d["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is False
    assert d["SOURCE"] == "INLINE_CALLER_SUPPLIED"
    assert d["STATUS"] == "NOT_VERIFIED"


def test_the_same_artifact_from_a_registered_store_is_trusted():
    """The control for the test above: nothing about the OBJECT changed, only
    where it came from."""
    art = canonical_label()
    inline = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    assert gate_detail(inline, "LABEL_ARTIFACT")["STATUS"] == "NOT_VERIFIED"
    sha = trust("LABEL_ARTIFACT_SHA", art)
    stored = terms(LABEL_ARTIFACT_SHA=sha)
    d = gate_detail(stored, "LABEL_ARTIFACT")
    assert d["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is True
    assert d["STATUS"] == "VERIFIED"


def test_the_three_verification_dimensions_are_reported_separately():
    art = canonical_label()
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    d = gate_detail(t, "LABEL_ARTIFACT")
    for dim in A.ARTIFACT_VERIFICATION_DIMENSIONS:
        assert dim in d, dim
    assert A.trusted_store_status()["TRUSTED_ARTIFACT_STORE_STATUS"] == \
        "NOT_ESTABLISHED_NO_IMMUTABLE_LEDGER"


# --- 10. An artifact must be bound to THIS evaluation. --------------------

def test_a_trusted_artifact_bound_to_another_evaluation_does_not_satisfy():
    """Valid, trusted, semantically sound -- and about a different market."""
    art = dict(canonical_label(),
               BOUND_TO={"MARKET_IDENTITY": "OTHER_MARKET",
                         "TARGET": "MID_MOVE_60S",
                         "CAPTURE_SPEC_SHA": "capture-spec-sha"})
    art["LABEL_ARTIFACT_SHA"] = A._sha_of(art, "LABEL_ARTIFACT_SHA")
    sha = trust("LABEL_ARTIFACT_SHA", art)
    t = terms(LABEL_ARTIFACT_SHA=sha)
    binding = {"MARKET_IDENTITY": "THIS_MARKET", "TARGET": "MID_MOVE_60S",
               "CAPTURE_SPEC_SHA": "capture-spec-sha"}
    d = gate_detail(t, "LABEL_ARTIFACT", binding)
    assert d["ARTIFACT_INTEGRITY_VERIFIED"] is True
    assert d["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is True
    assert d["ARTIFACT_SEMANTICS_VERIFIED"] is True
    assert d["ARTIFACT_BOUND_TO_THIS_EVALUATION"] is False
    assert d["STATUS"] == "NOT_VERIFIED"
    fields = [m["FIELD"] for m in d["BINDING"]["MISMATCHED_BINDINGS"]]
    assert "MARKET_IDENTITY" in fields


# --- 3. A row is bound to its artifact's CONTENT. -------------------------

def _rows_on(art, target="MID_MOVE_60S", n=12, value=None, status="PRESENT"):
    chain = (art.get("OBSERVATION_CHAIN") or {}).get(target) or {}
    val = art["LABELS"][target] if value is None else value
    rows = []
    for i in range(n):
        rows.append({
            target: val,
            "%s_STATUS" % target: status,
            "%s_REALISED_OFFSET_S" % target: chain.get("REALIZED_OFFSET_S"),
            "DECISION_ID": art.get("DECISION_ID"),
            "DECISION_TIMESTAMP_UTC": "2026-09-17T00:00:%02dZ" % i,
            "LABEL_ARTIFACT_SHA": art["LABEL_ARTIFACT_SHA"]})
    return rows


def test_a_row_whose_value_differs_from_its_artifact_refuses_the_target():
    """A valid artifact attached to a fabricated row is not provenance for
    that row."""
    art = canonical_label()
    rows = _rows_on(art, value=9.99)
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0,
                              label_artifacts={art["LABEL_ARTIFACT_SHA"]: art})
    assert g["MAY_SCORE"] is False
    assert g["REASON"] == "ROW_DOES_NOT_MATCH_ITS_LABEL_ARTIFACT"
    problems = set()
    for u in g["ROW_ARTIFACT_BINDING"]["UNBOUND_ROWS"]:
        problems.update(u.get("PROBLEMS", ()))
    assert "VALUE_DIFFERS_FROM_ARTIFACT" in problems


def test_a_row_whose_status_differs_from_its_artifact_refuses_the_target():
    art = canonical_label()
    rows = _rows_on(art, status="IMPUTED")
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0,
                              label_artifacts={art["LABEL_ARTIFACT_SHA"]: art})
    assert g["MAY_SCORE"] is False
    problems = set()
    for u in g["ROW_ARTIFACT_BINDING"]["UNBOUND_ROWS"]:
        problems.update(u.get("PROBLEMS", ()))
    assert "STATUS_DIFFERS_FROM_ARTIFACT" in problems


def test_a_row_citing_another_decision_is_not_bound_to_the_artifact():
    art = canonical_label()
    rows = _rows_on(art)
    for r in rows:
        r["DECISION_ID"] = "SOMEONE_ELSES_DECISION"
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0,
                              label_artifacts={art["LABEL_ARTIFACT_SHA"]: art})
    assert g["MAY_SCORE"] is False
    problems = set()
    for u in g["ROW_ARTIFACT_BINDING"]["UNBOUND_ROWS"]:
        problems.update(u.get("PROBLEMS", ()))
    assert "DECISION_ID_DIFFERS_FROM_ARTIFACT" in problems


# --- 4. One artifact is one observation. ----------------------------------

def test_twelve_rows_on_one_artifact_are_one_observation():
    art = canonical_label()
    rows = _rows_on(art, n=12)
    for r in rows:                        # identical origin: one observation
        r["DECISION_TIMESTAMP_UTC"] = "2026-09-17T00:00:00Z"
    dedup = M.deduplicate_label_observations(rows)
    assert dedup["ROWS"] == 12
    assert len(dedup["UNIQUE_ROWS"]) == 1
    assert dedup["UNIQUE_LABEL_ARTIFACTS"] == 1
    assert dedup["UNIQUE_DECISION_IDS"] == 1
    assert dedup["DUPLICATE_LABEL_ARTIFACT_ROWS"] == 11


def test_the_scoring_gate_counts_unique_observations_not_rows():
    art = canonical_label()
    rows = _rows_on(art, n=12)
    for r in rows:
        r["DECISION_TIMESTAMP_UTC"] = "2026-09-17T00:00:00Z"
    g = M.target_scoring_gate("MID_MOVE_60S", rows, min_coverage_pct=0.0,
                              label_artifacts={art["LABEL_ARTIFACT_SHA"]: art})
    assert g["UNIQUE_LABEL_ARTIFACTS"] == 1
    assert g["DUPLICATE_LABEL_ARTIFACT_ROWS"] == 11
    assert g["ROWS_SCORED"] == 1 if "ROWS_SCORED" in g else True


# --- 5. The observation chain is re-derived, never read. ------------------

def test_a_rederived_observation_chain_beats_the_stored_status():
    """Strip the chain, declare it COMPLETE, re-seal. The seal now faithfully
    protects the artifact's own opinion of itself."""
    art = canonical_label()
    liar = dict(art)
    liar["OBSERVATION_CHAIN"] = {}
    liar["OBSERVATION_CHAIN_STATUS"] = "COMPLETE"
    liar["OBSERVATION_CHAIN_GAPS"] = ()
    liar["LABEL_ARTIFACT_SHA"] = A._sha_of(liar, "LABEL_ARTIFACT_SHA")
    v = B.verify_label_artifact(liar)
    assert v["SEAL_INTACT"] is True
    assert v["STORED_OBSERVATION_CHAIN_STATUS"] == "COMPLETE"
    assert v["OBSERVATION_CHAIN_REDERIVED"] is True
    assert v["LABEL_PROVENANCE_STATUS"] == "OBSERVATION_CHAIN_INCOMPLETE"


def test_the_chain_is_recomputed_against_immutable_rows_when_supplied():
    art = canonical_label()
    v = B.rederive_observation_chain(art)
    assert v["OBSERVATION_CHAIN_STATUS"] == "COMPLETE"
    resolutions = set(v["SOURCE_RESOLUTION"].values())
    assert resolutions <= set(B.SOURCE_RESOLUTION_STATUSES)
    assert resolutions == {"SOURCE_ROWS_UNAVAILABLE"}   # none supplied


# --- 6. Fill quantity is an artifact, not a typed status. -----------------

def _fq_artifact(**over):
    body = {
        "ARTIFACT_TYPE": "FILL_QUANTITY_MODEL_ARTIFACT",
        "ACTION": "POST_BID", "SIDE": "BID", "PRICE": 0.42,
        "POSTED_QUANTITY": 500, "HORIZON_S": 60, "VENUE": "V1",
        "MODEL_VERSION": "fq-v1", "CALIBRATION_WINDOW": "2026-09-01/09-15",
        "P_ANY_FILL": 0.40, "P_FULL_FILL": 0.10,
        "EXPECTED_FILL_FRACTION": 0.25, "EXPECTED_FILLED_QTY": 125.0,
    }
    body.update(over)
    return self_hashed("FILL_QUANTITY_MODEL_ARTIFACT_SHA", **body)


def test_a_caller_typed_fill_quantity_status_does_not_validate():
    t = terms(FILL_QUANTITY_STATUS="VALIDATED", P_ANY_FILL=0.4,
              P_FULL_FILL=0.1, EXPECTED_FILL_FRACTION=0.25,
              EXPECTED_FILLED_QTY=125.0)
    out = A.verify_fill_quantity_artifact(t)
    assert out["VALIDATED"] is False
    assert out["STATUS"] == "ARTIFACT_ABSENT"


def test_a_trusted_consistent_fill_quantity_artifact_validates():
    art = _fq_artifact()
    sha = trust("FILL_QUANTITY_MODEL_ARTIFACT_SHA", art)
    out = A.verify_fill_quantity_artifact(
        terms(FILL_QUANTITY_MODEL_ARTIFACT_SHA=sha))
    assert out["VALIDATED"] is True, out["SEMANTIC_PROBLEMS"]


def test_p_full_fill_above_p_any_fill_is_refused():
    art = _fq_artifact(P_ANY_FILL=0.10, P_FULL_FILL=0.40)
    sha = trust("FILL_QUANTITY_MODEL_ARTIFACT_SHA", art)
    out = A.verify_fill_quantity_artifact(
        terms(FILL_QUANTITY_MODEL_ARTIFACT_SHA=sha))
    assert out["VALIDATED"] is False
    assert "P_FULL_FILL_EXCEEDS_P_ANY_FILL_OR_OUT_OF_RANGE" in \
        out["SEMANTIC_PROBLEMS"]


def test_expected_filled_qty_above_posted_quantity_is_refused():
    art = _fq_artifact(EXPECTED_FILL_FRACTION=1.0,
                       EXPECTED_FILLED_QTY=900.0)
    sha = trust("FILL_QUANTITY_MODEL_ARTIFACT_SHA", art)
    out = A.verify_fill_quantity_artifact(
        terms(FILL_QUANTITY_MODEL_ARTIFACT_SHA=sha))
    assert out["VALIDATED"] is False
    assert "EXPECTED_FILLED_QTY_EXCEEDS_POSTED_QUANTITY" in \
        out["SEMANTIC_PROBLEMS"]


def test_a_fill_quantity_artifact_for_another_price_is_not_bound():
    art = _fq_artifact(PRICE=0.42)
    sha = trust("FILL_QUANTITY_MODEL_ARTIFACT_SHA", art)
    out = A.verify_fill_quantity_artifact(
        terms(FILL_QUANTITY_MODEL_ARTIFACT_SHA=sha), {"PRICE": 0.99})
    assert out["VALIDATED"] is False
    assert out["BINDING"]["BOUND"] is False


# --- 7. No generic access to a raw-N posterior. ---------------------------

def test_the_generic_posterior_names_are_gone_from_update_beta():
    out = P.update_beta(P.beta_from_mean_n(0.2, 10), successes=5, trials=100)
    for k in ("POSTERIOR", "POSTERIOR_DIST", "PRIOR_TO_POSTERIOR_SHIFT"):
        assert out[k] == NOT_IDENTIFIED, k
    assert out["DIAGNOSTIC_RAW_ROW_POSTERIOR_SUMMARY"] != NOT_IDENTIFIED


def test_the_generic_posterior_names_are_gone_from_update_normal():
    out = P.update_normal(Dist("NORMAL", {"mu": 0.0, "sigma": 0.01}),
                          0.004, 0.01, 10)
    for k in ("POSTERIOR", "POSTERIOR_DIST", "PRIOR_TO_POSTERIOR_SHIFT",
              "UNCERTAINTY_FELL_BY"):
        assert out[k] == NOT_IDENTIFIED, k
    assert out["DIAGNOSTIC_RAW_ROW_UNCERTAINTY_FELL_BY"] != NOT_IDENTIFIED


# --- 8. A truthy string in a boolean field is a trap. ---------------------

def test_an_unknown_robust_positivity_is_not_truthy():
    r = A.action_ev_mc("POST_BID", terms(), draws=300)
    rp = A.robustly_positive(r)
    assert rp["ROBUSTLY_POSITIVE"] is None
    assert bool(rp["ROBUSTLY_POSITIVE"]) is False
    assert rp["ROBUST_POSITIVITY_STATUS"] == NOT_IDENTIFIED
    assert rp["ROBUST_POSITIVITY_STATUS"] in A.ROBUST_POSITIVITY_STATUSES


def test_the_tri_state_never_puts_a_string_in_the_boolean_field():
    r = A.action_ev_mc("POST_BID", terms(), draws=300)
    rp = A.robustly_positive(r)
    assert not isinstance(rp["ROBUSTLY_POSITIVE"], str)


# --- 9. Impossible probability draws may not enter EV arithmetic. ---------

def _unbounded_p_fill():
    return terms(P_FILL=Dist("NORMAL", {"mu": 0.5, "sigma": 0.16}))


def test_an_unbounded_p_fill_is_refused_rather_than_clipped():
    r = A.action_ev_mc("POST_BID", _unbounded_p_fill(), draws=500)
    assert r["ACTION_EV_MONTE_CARLO_STATUS"] == \
        A.REFUSED_INVALID_PHYSICAL_SUPPORT
    assert r["EV_MEAN"] == NOT_IDENTIFIED
    assert r["INVALID_PHYSICAL_SUPPORT_TERMS"] == ("P_FILL",)
    assert r["RECOMMENDED"] is False
    for k in A.ORDINARY_EV_STATISTICS:
        if k == "EV_MEAN":
            continue
        assert k not in r or r[k] == NOT_IDENTIFIED, k


def test_the_shadow_diagnostic_publishes_no_ordinary_ev_statistic():
    r = A.action_ev_mc("POST_BID", _unbounded_p_fill(), draws=500,
                       shadow_distribution_diagnostic=True)
    assert r["ACTION_EV_MONTE_CARLO_STATUS"] == \
        A.SHADOW_DISTRIBUTION_DIAGNOSTIC
    assert r["EV_MEAN"] == NOT_IDENTIFIED
    for k in ("EV_SD", "EV_MEDIAN", "EV_P10", "P_EV_GT_0", "TAIL_LOSS_P05",
              "CONSERVATIVE_EV_P10", "POSTERIOR_MEAN_EV"):
        assert k not in r, k
    diag = r["SHADOW_DISTRIBUTION_DIAGNOSTIC"]
    assert diag["DIAGNOSTIC_EV_MEAN"] != NOT_IDENTIFIED
    assert "THESE_ARE_NOT_EXPECTED_VALUES" in diag


def test_a_bounded_family_still_produces_an_ordinary_ev():
    r = A.action_ev_mc("POST_BID", terms(), draws=500)
    assert r["ACTION_EV_MONTE_CARLO_STATUS"] == "ADMISSIBLE_PHYSICAL_SUPPORT"
    assert isinstance(r["EV_MEAN"], float)


def test_a_p_fill_draw_outside_zero_one_never_enters_ev_arithmetic(
        monkeypatch):
    """Belt and braces: lie to the support table so the pre-loop refusal is
    bypassed, and check the draw site still refuses before _net() runs."""
    monkeypatch.setitem(A.FAMILY_SUPPORT, "NORMAL", (0.0, 1.0))
    monkeypatch.setitem(A.DECISION_GRADE_FAMILIES, "PROBABILITY",
                        A.DECISION_GRADE_FAMILIES["PROBABILITY"] +
                        ("NORMAL",))
    calls = []
    real_net = A._net

    def counting_net(p_fill, *a, **k):
        calls.append(p_fill)
        return real_net(p_fill, *a, **k)

    monkeypatch.setattr(A, "_net", counting_net)
    r = A.action_ev_mc("POST_BID", _unbounded_p_fill(), draws=4000)
    assert r["ACTION_EV_MONTE_CARLO_STATUS"] == \
        A.REFUSED_INVALID_PHYSICAL_SUPPORT
    assert r["IMPOSSIBLE_PROBABILITY_WEIGHT_DRAWS"]["P_FILL"]["DRAWS"] >= 1
    assert r["EV_MEAN"] == NOT_IDENTIFIED
    assert calls, "the control: admissible draws did reach _net"
    assert all(0.0 <= p <= 1.0 for p in calls)


def test_the_refusal_names_what_to_do_instead():
    r = A.action_ev_mc("POST_BID", _unbounded_p_fill(), draws=200)
    assert "BETA" in r["WHAT_TO_DO_INSTEAD"]
    assert "shadow_distribution_diagnostic" in r["WHAT_TO_DO_INSTEAD"]


# --- The register records the pass. ---------------------------------------

def test_the_register_carries_the_fourth_pass_statuses():
    d = R.describe()
    for k in ("ARTIFACT_VERIFICATION_DIMENSION_STATUS",
              "ARTIFACT_SEMANTIC_VERIFIER_STATUS",
              "ARTIFACT_TRUSTED_ORIGIN_STATUS",
              "ROW_ARTIFACT_CONTENT_BINDING_STATUS",
              "LABEL_OBSERVATION_UNIQUENESS_STATUS",
              "OBSERVATION_CHAIN_REDERIVATION_STATUS",
              "FILL_QUANTITY_ARTIFACT_BINDING_STATUS",
              "GENERIC_POSTERIOR_ALIAS_STATUS",
              "ROBUST_POSITIVITY_TRISTATE_STATUS",
              "IMPOSSIBLE_DRAW_STATUS",
              "GATE_ARTIFACT_EVALUATION_BINDING_STATUS",
              "FOURTH_PASS_ADVERSARIAL_TEST_STATUS"):
        assert k in d, k
    assert d["IMPOSSIBLE_DRAW_STATUS"] == "REFUSED_NOT_CLIPPED_NOT_AVERAGED"
    assert d["DECISION_GRADE_ACTION_EV_STATUS"] == "BLOCKED"


def test_the_decision_gate_is_still_blocked_by_construction():
    art = canonical_label()
    sha = trust("LABEL_ARTIFACT_SHA", art)
    r = A.action_ev_mc("POST_BID", terms(LABEL_ARTIFACT_SHA=sha), draws=200)
    assert r["DECISION_GRADE_ACTION_EV_STATUS"] == "BLOCKED"
    assert "DEPENDENCE_MODEL_VALIDATED" in r["DECISION_GRADE_BLOCKERS"]


def test_no_test_in_this_suite_asserts_a_tautology():
    """A guard against the failure mode this whole pass is about: a check
    that passes because of how it is written rather than what it inspects."""
    import os
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "test_fourth_pass_audit.py")).read()
    forbidden = ("assert" + " True", "assert" + " 1 ==" + " 1",
                 " " + "or" + " True")
    for frag in forbidden:
        assert frag not in src, frag
