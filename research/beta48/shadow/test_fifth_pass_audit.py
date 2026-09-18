"""Regression tests for the fifth-pass source review of bc53f55.

Two integration defects, both of the same shape as the fourth pass: a check
that was correct where it was written and bypassed where the work happened.

1. TRUSTED ORIGIN CAME FROM THE PAYLOAD. The gate read
   ARTIFACT_RETRIEVED_FROM == "TRUSTED_STORE" off the artifact -- a field any
   caller can type. Origin is now the return of the resolver.

2. THE SCORER SCORED A DIFFERENT POPULATION. target_scoring_gate() verified
   and de-duplicated one set of rows and then handed back permission, after
   which score_baselines() went back to the caller's originals. Twelve copies
   of one labelled observation became twelve scored rows.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

import copy

import pytest

import action_ev_mc as A
import bettor_dataset as B
import label_fixture_support as LF
import microstructure_v1 as M
from prior_registry import NOT_IDENTIFIED

from test_third_pass_audit import terms, series


# --- fixtures --------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_trusted_stores():
    A.clear_trusted_stores()
    yield
    A.clear_trusted_stores()


SCALES = {
    "B4_SIMPLE_BOOK_IMBALANCE": (0.01, "PREDECLARED_FIXED_TRANSFORMATION"),
    "B5_SIMPLE_ORDER_FLOW_IMBALANCE": (0.0001,
                                       "PREDECLARED_FIXED_TRANSFORMATION"),
}

TARGET = "MID_MOVE_30S"


def canonical_label(market="M1", decision="D1"):
    s = series(market)
    return B.label_artifact(dict(s[0], DECISION_ID=decision), s,
                            capture_spec_sha="capture-spec-sha")


def scored_rows(n=10):
    """n rows every predictor can price, each with its own real artifact."""
    rows = [{
        TARGET: 0.001 * ((i % 5) - 2),
        "%s_STATUS" % TARGET: "PRESENT",
        "MICROPRICE_MINUS_MID": 0.0005,
        "ORDER_BOOK_IMBALANCE": 0.1,
        "RAW_OFI_SHARES": 50,
        "_PREV_MOVE": 0.0002,
        "MODEL": 0.0009,
    } for i in range(n)]
    return rows, LF.bind(rows, TARGET)


def score(rows, arts):
    return M.score_baselines(rows, TARGET, pred_key="MODEL",
                             min_coverage_pct=0.0, baseline_scales=SCALES,
                             label_artifacts=arts)


# --- 1. Trusted origin comes from the RESOLVER. ---------------------------

def _claims_to_be_trusted(art, sha_field="LABEL_ARTIFACT_SHA"):
    """The artifact a caller builds, then decorates with the store's own
    vocabulary. Nothing about it came from a store."""
    return dict(art,
                ARTIFACT_RETRIEVED_FROM="TRUSTED_STORE",
                ARTIFACT_STORE_ID="TOTALLY_REAL_LEDGER",
                ARTIFACT_STORE_KIND="IMMUTABLE_CAPTURE_STORE")


def test_a_self_declared_trusted_inline_artifact_is_not_trusted_empty_store():
    art = _claims_to_be_trusted(canonical_label())
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    d = A.verify_decision_grade_artifacts(t)["DETAIL"]["LABEL_ARTIFACT"]
    assert d["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is False
    assert d["SOURCE"] == "INLINE_CALLER_SUPPLIED"
    assert d["STATUS"] == "NOT_VERIFIED"
    assert "ARTIFACT_RETRIEVED_FROM" in \
        d["CLAIMED_RESOLVER_FIELDS_ON_INLINE_ARTIFACT"]


def test_a_self_declared_trusted_inline_artifact_is_not_trusted_other_store():
    """An UNRELATED store is registered. The resolver still does not find
    this artifact, so origin stays false."""
    A.register_trusted_store("SOMEONE_ELSES", "IMMUTABLE_CAPTURE_STORE",
                             "root-other", {"0" * 64: {"X": 1}},
                             registered_by="TEST")
    art = _claims_to_be_trusted(canonical_label())
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": art})
    d = A.verify_decision_grade_artifacts(t)["DETAIL"]["LABEL_ARTIFACT"]
    assert d["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is False
    assert d["STATUS"] == "NOT_VERIFIED"


def test_the_decoration_does_not_even_survive_into_the_integrity_check():
    """The resolver-only fields are stripped before anything reads the
    object, so decorating an artifact cannot change its digest either."""
    art = canonical_label()
    decorated = _claims_to_be_trusted(art)
    t = terms(DECISION_GRADE_ARTIFACTS={"LABEL_ARTIFACT_SHA": decorated})
    d = A.verify_decision_grade_artifacts(t)["DETAIL"]["LABEL_ARTIFACT"]
    assert d["ARTIFACT_INTEGRITY_VERIFIED"] is True
    assert d["RECOMPUTED_SHA"] == art["LABEL_ARTIFACT_SHA"]


def test_a_self_declared_trusted_fill_quantity_artifact_is_not_trusted():
    body = {
        "ARTIFACT_TYPE": "FILL_QUANTITY_MODEL_ARTIFACT",
        "ACTION": "POST_BID", "SIDE": "BID", "PRICE": 0.42,
        "POSTED_QUANTITY": 500, "HORIZON_S": 60, "VENUE": "V1",
        "MODEL_VERSION": "fq-v1", "CALIBRATION_WINDOW": "2026-09-01/09-15",
        "P_ANY_FILL": 0.40, "P_FULL_FILL": 0.10,
        "EXPECTED_FILL_FRACTION": 0.25, "EXPECTED_FILLED_QTY": 125.0,
    }
    body["FILL_QUANTITY_MODEL_ARTIFACT_SHA"] = A._sha_of(
        body, "FILL_QUANTITY_MODEL_ARTIFACT_SHA")
    body.update(ARTIFACT_RETRIEVED_FROM="TRUSTED_STORE",
                ARTIFACT_STORE_ID="TOTALLY_REAL_LEDGER",
                ARTIFACT_STORE_KIND="IMMUTABLE_CAPTURE_STORE")
    out = A.verify_fill_quantity_artifact(
        terms(FILL_QUANTITY_MODEL_ARTIFACT=body))
    assert out["ARTIFACT_INTEGRITY_VERIFIED"] is True   # it does hash
    assert out["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is False
    assert out["VALIDATED"] is False
    assert "ARTIFACT_RETRIEVED_FROM" in \
        out["CLAIMED_RESOLVER_FIELDS_ON_INLINE_ARTIFACT"]


def test_origin_is_still_granted_by_an_actual_registered_store():
    """The control: the same object, reached through the resolver."""
    art = canonical_label()
    sha = art["LABEL_ARTIFACT_SHA"]
    A.register_trusted_store("STORE_A", "IMMUTABLE_CAPTURE_STORE", "root-a",
                             {sha: art}, registered_by="TEST")
    d = A.verify_decision_grade_artifacts(
        terms(LABEL_ARTIFACT_SHA=sha))["DETAIL"]["LABEL_ARTIFACT"]
    assert d["ARTIFACT_TRUSTED_ORIGIN_VERIFIED"] is True
    assert d["SOURCE"] == "TRUSTED_STORE"


def test_the_operational_store_status_is_still_not_established():
    """No fixture is registered to clear this. It reports the real state of
    the system, which is that no evidence store exists."""
    assert A.trusted_store_status()["TRUSTED_ARTIFACT_STORE_STATUS"] == \
        "NOT_ESTABLISHED_NO_IMMUTABLE_LEDGER"


# --- 2. Score the population the gate verified. ---------------------------

def test_repeating_an_observation_does_not_increase_n_scored():
    rows, arts = scored_rows(10)
    once = score(rows, arts)
    twice = score([copy.deepcopy(r) for r in rows] * 2, arts)
    for name in once["BY_PREDICTOR"]:
        assert once["BY_PREDICTOR"][name]["N_SCORED"] == \
            twice["BY_PREDICTOR"][name]["N_SCORED"], name
    assert twice["ROWS_SUPPLIED"] == 20
    assert twice["CANONICAL_OBSERVATIONS_SCORED"] == 10
    assert twice["DUPLICATE_ROWS_COLLAPSED"] == 10


def test_repeating_an_observation_does_not_change_the_scores():
    rows, arts = scored_rows(10)
    once = score(rows, arts)
    five = score([copy.deepcopy(r) for r in rows] * 5, arts)
    for name in once["BY_PREDICTOR"]:
        a, b = once["BY_PREDICTOR"][name], five["BY_PREDICTOR"][name]
        assert a["MEAN_ABSOLUTE_ERROR"] == b["MEAN_ABSOLUTE_ERROR"], name
        assert a["DIRECTION_ACCURACY"] == b["DIRECTION_ACCURACY"], name


def test_repeating_an_observation_does_not_change_common_support():
    rows, arts = scored_rows(10)
    once = score(rows, arts)
    twice = score([copy.deepcopy(r) for r in rows] * 2, arts)
    assert once["COMMON_EVALUATION_SUPPORT_ROWS"] > 0     # not a vacuous pass
    assert once["COMMON_EVALUATION_SUPPORT_ROWS"] == \
        twice["COMMON_EVALUATION_SUPPORT_ROWS"]
    assert once["SCORABLE_ROWS"] == twice["SCORABLE_ROWS"]
    assert once["CHALLENGER_ADMISSION_COMPARISON_STATUS"] == \
        twice["CHALLENGER_ADMISSION_COMPARISON_STATUS"]
    assert once["BASELINE_SET_COMPLETE"] == twice["BASELINE_SET_COMPLETE"]


def test_altering_a_copied_rows_origin_timestamp_is_rejected():
    """The old dedup key included the row's own timestamp, so retyping it
    minted a second observation from one labelled event."""
    rows, arts = scored_rows(10)
    forged = dict(copy.deepcopy(rows[0]),
                  DECISION_TIMESTAMP_UTC="2030-01-01T00:00:00Z")
    out = score([copy.deepcopy(r) for r in rows] + [forged], arts)
    assert out["STATUS"] == "REFUSED"
    assert out["GATE"]["REASON"] == "ROW_DOES_NOT_MATCH_ITS_LABEL_ARTIFACT"
    problems = set()
    for u in out["GATE"]["ROW_ARTIFACT_BINDING"]["UNBOUND_ROWS"]:
        problems.update(u.get("PROBLEMS", ()))
    assert "ORIGIN_TIMESTAMP_DIFFERS_FROM_ARTIFACT" in problems


def test_a_copy_that_disagrees_on_a_scored_field_is_refused_not_resolved():
    """Two rows citing one observation and disagreeing about the model's
    prediction are not a duplicate and not two observations."""
    rows, arts = scored_rows(10)
    conflicting = dict(copy.deepcopy(rows[0]), MODEL=0.5)
    gate = M.target_scoring_gate(
        TARGET, [copy.deepcopy(r) for r in rows] + [conflicting],
        min_coverage_pct=0.0, label_artifacts=arts)
    assert gate["MAY_SCORE"] is False
    assert gate["REASON"] == "CONFLICTING_COPIES_OF_ONE_OBSERVATION"
    assert gate["LABEL_OBSERVATION_DEDUPLICATION"]["CONFLICTING_COPIES"]


def test_permuting_valid_rows_preserves_the_result():
    rows, arts = scored_rows(10)
    forward = score(rows, arts)
    backward = score(list(reversed([copy.deepcopy(r) for r in rows])), arts)
    assert forward["BY_PREDICTOR"] == backward["BY_PREDICTOR"]
    assert forward["COMMON_EVALUATION_SUPPORT_ROWS"] == \
        backward["COMMON_EVALUATION_SUPPORT_ROWS"]
    assert forward["CHALLENGER_ADMISSION_COMPARISON_STATUS"] == \
        backward["CHALLENGER_ADMISSION_COMPARISON_STATUS"]


def test_observation_identity_is_the_artifact_and_target_not_the_stamp():
    rows, arts = scored_rows(4)
    dedup = M.deduplicate_label_observations(
        [copy.deepcopy(r) for r in rows] * 3, TARGET,
        {a["LABEL_ARTIFACT_SHA"]: a for a in arts.values()})
    assert dedup["OBSERVATION_IDENTITY_FIELDS"] == \
        ("LABEL_ARTIFACT_SHA", "TARGET")
    assert dedup["UNIQUE_OBSERVATIONS"] == 4
    assert dedup["ROWS"] == 12
    assert dedup["DUPLICATE_LABEL_ARTIFACT_ROWS"] == 8
    assert dedup["CONFLICTING_COPIES"] == ()


def test_the_origin_timestamp_is_hydrated_from_the_artifact():
    """A row that carries no stamp gets the artifact's, so the population is
    dated by its provenance rather than by whoever built the row."""
    rows, arts = scored_rows(3)
    by_sha = {a["LABEL_ARTIFACT_SHA"]: a for a in arts.values()}
    for r in rows:
        r.pop("DECISION_TIMESTAMP_UTC", None)
    dedup = M.deduplicate_label_observations(rows, TARGET, by_sha)
    for r in dedup["UNIQUE_ROWS"]:
        art = by_sha[r["LABEL_ARTIFACT_SHA"]]
        chain = art["OBSERVATION_CHAIN"][TARGET]
        assert r["DECISION_TIMESTAMP_UTC"] == chain["ORIGIN_TIMESTAMP"]


def test_the_gate_publishes_the_population_it_verified():
    rows, arts = scored_rows(6)
    gate = M.target_scoring_gate(TARGET, [copy.deepcopy(r) for r in rows] * 2,
                                min_coverage_pct=0.0, label_artifacts=arts)
    assert gate["MAY_SCORE"] is True
    assert len(gate["CANONICAL_OBSERVATION_POPULATION"]) == 6
    assert gate["UNIQUE_OBSERVATIONS"] == 6


def test_relative_value_correlates_over_the_deduplicated_population():
    obs = []
    for i in range(60):
        res = (i % 9 - 4) / 100.0
        obs.append({"EVENT_KEY": "e%d" % (i % 12), "MARKET": "m", "T": "t",
                    "P_TARGET": 0.50 + res, "P_SURFACE_EX_TARGET": 0.50,
                    "TARGET_LATER": {30: 0.50 + 0.3 * res},
                    "TARGET_LATER_STATUS": {30: "PRESENT"}})
    rows, _ = M.relative_value_rows(obs, horizons=(30,))
    arts = LF.bind(rows, "TARGET_CHANGE_30S")
    once = M.relative_value_test(rows, horizons=(30,), min_coverage_pct=50.0,
                                 label_artifacts=arts)
    twice = M.relative_value_test([copy.deepcopy(r) for r in rows] * 2,
                                  horizons=(30,), min_coverage_pct=50.0,
                                  label_artifacts=arts)
    assert once["BY_HORIZON"]["30S"]["STATUS"] == "MEASURED"
    assert once["BY_HORIZON"]["30S"]["N_OBSERVATIONS"] == 60
    assert once["BY_HORIZON"]["30S"]["N_OBSERVATIONS"] == \
        twice["BY_HORIZON"]["30S"]["N_OBSERVATIONS"]
    assert once["BY_HORIZON"]["30S"]["N_EVENTS"] == \
        twice["BY_HORIZON"]["30S"]["N_EVENTS"]
    assert once["BY_HORIZON"]["30S"]["CORRELATION"] == \
        twice["BY_HORIZON"]["30S"]["CORRELATION"]


def test_no_test_in_this_suite_asserts_a_tautology():
    import os
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "test_fifth_pass_audit.py")).read()
    for frag in ("assert" + " True", "assert" + " 1 ==" + " 1",
                 " " + "or" + " True"):
        assert frag not in src, frag
