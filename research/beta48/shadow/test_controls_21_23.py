"""Tests for the three directive controls: 21 (power), 22 (nesting), 23 (as-of).

Each control exists to stop a specific wrong number being produced. These
tests produce the wrong number deliberately and check that the machinery
refuses it or flags it.
"""

import math
import random

import pytest

import ev_core_power as POW
import ev_core_nested as NEST
import asof_provenance as AP


# ---------------------------------------------------------------------------
# Section 21. Paired event-level power.
# ---------------------------------------------------------------------------

def _rows_with_rows_per_event(n_events, rows_per_event, gap=0.0, seed=7,
                              challenger_event_bias=0.10):
    """One fixture per event, many contract rows inside it.

    The challenger prices every row of a fixture from ONE score grid, so its
    error is shared across that fixture's rows -- `challenger_event_bias` is
    drawn once per event, not once per row. That is what makes the rows
    dependent, and it is the real shape of the data.
    """
    rnd = random.Random(seed)
    rows = []
    for e in range(n_events):
        ev = "lg-a-b-2026-01-%02d" % (1 + e % 28)
        hard = rnd.random() < 0.5          # a shared per-fixture difficulty
        bias = rnd.gauss(0, challenger_event_bias)   # ONE grid per fixture
        for i in range(rows_per_event):
            y = 1 if rnd.random() < (0.3 if hard else 0.7) else 0
            base = 0.3 if hard else 0.7
            rows.append({"EVENT_KEY": "%s#%d" % (ev, e), "Y": y,
                         "P_MARKET": min(max(base + rnd.gauss(0, .05), .01), .99),
                         "P_CH": min(max(base + gap + bias + rnd.gauss(0, .05),
                                         .01), .99)})
    return rows


def test_the_unit_is_the_event_not_the_row():
    rows = _rows_with_rows_per_event(40, 25)
    diffs, meta = POW.paired_event_differences(rows, "P_CH")
    assert meta["EVENTS"] == 40
    assert meta["ROWS_PAIRED"] == 40 * 25
    assert len(diffs) == 40


def test_row_variance_understates_the_shortfall():
    """Counting rows makes the sample look far closer to sufficient than it is."""
    rows = _rows_with_rows_per_event(60, 30)
    c = POW.contrast_with_forbidden(rows, "P_CH", delta=0.010)
    assert c["SHORTFALL_FACTOR_CORRECT"] > c["SHORTFALL_FACTOR_IF_ROWS_WERE_USED"]
    assert c["THE_FORBIDDEN_ACCOUNTING_UNDERSTATES_THE_SHORTFALL_BY"] > 2.0
    assert c["WHICH_ONE_GOVERNS"] == POW.POWER_VARIANCE_SOURCE


def test_the_understatement_tracks_the_within_event_correlation():
    """1 + (k-1)*rho is the size of the error; rho is what drives it."""
    dep = POW.contrast_with_forbidden(
        _rows_with_rows_per_event(60, 20, challenger_event_bias=0.15),
        "P_CH", delta=0.010)
    indep = POW.contrast_with_forbidden(
        _rows_with_rows_per_event(60, 20, challenger_event_bias=0.0),
        "P_CH", delta=0.010)
    assert (dep["WITHIN_EVENT_CORRELATION_OF_ROW_DIFFERENCES"]
            > indep["WITHIN_EVENT_CORRELATION_OF_ROW_DIFFERENCES"])
    assert (dep["THE_FORBIDDEN_ACCOUNTING_UNDERSTATES_THE_SHORTFALL_BY"]
            > indep["THE_FORBIDDEN_ACCOUNTING_UNDERSTATES_THE_SHORTFALL_BY"])


def test_the_forbidden_estimate_is_labelled_unusable():
    rows = _rows_with_rows_per_event(20, 10)
    bad = POW.row_level_sd_forbidden(rows, "P_CH")
    assert bad["THIS_MAY_NOT_BE_USED_FOR_POWER"] is True


def test_pairing_drops_rows_neither_side_priced_from_both_sides():
    rows = _rows_with_rows_per_event(10, 5)
    rows.append({"EVENT_KEY": "lg-a-b-2026-01-01#0", "Y": 1,
                 "P_MARKET": 0.5, "P_CH": None})
    diffs, meta = POW.paired_event_differences(rows, "P_CH")
    assert meta["ROWS_DROPPED_UNPAIRED"] == 1
    assert meta["ROWS_PAIRED"] == 50


def test_within_event_dependence_is_removed_by_differencing():
    """A shared per-fixture shock must not inflate SD(D_EVENT).

    Both forecasters see the same hard/easy fixtures, so differencing inside
    the event should cancel most of that shared component.
    """
    rows = _rows_with_rows_per_event(80, 20, gap=0.02)
    diffs, _ = POW.paired_event_differences(rows, "P_CH")
    paired_sd = POW.paired_summary(diffs)["SD_D_EVENT"]

    # the unpaired alternative: SD of the challenger's own event scores
    by = {}
    for r in rows:
        by.setdefault(r["EVENT_KEY"], []).append(
            POW._log_loss(r["P_CH"], r["Y"]))
    own = [sum(v) / len(v) for v in by.values()]
    unpaired_sd = POW._sd(own)
    assert paired_sd < unpaired_sd


def test_events_required_scales_as_one_over_delta_squared():
    n1 = POW.events_required(0.010, 0.168)
    n2 = POW.events_required(0.005, 0.168)
    assert n2 / n1 == pytest.approx(4.0, rel=0.02)


def test_events_required_matches_the_closed_form():
    sd, delta = 0.168, 0.010
    z = POW.Z_ALPHA_TWO_SIDED_95 + POW.Z_POWER[0.80]
    assert POW.events_required(delta, sd) == int(
        math.ceil(z ** 2 * sd ** 2 / delta ** 2))


def test_the_ladder_reports_its_own_shortfall():
    rows = _rows_with_rows_per_event(30, 8)
    diffs, _ = POW.paired_event_differences(rows, "P_CH")
    lad = POW.ladder_from_paired(diffs)
    assert lad["N_EVENTS_OBSERVED"] == 30
    assert lad["SHORTFALL_FACTOR"][0.002] > lad["SHORTFALL_FACTOR"][0.020]


def test_bootstrap_interval_is_reported_beside_the_normal_one():
    rows = _rows_with_rows_per_event(50, 6)
    diffs, _ = POW.paired_event_differences(rows, "P_CH")
    s = POW.paired_summary(diffs, draws=400)
    assert "CI95_NORMAL" in s and "CI95_EVENT_BOOTSTRAP" in s
    assert s["SKEW_D_EVENT"] != POW.NOT_IDENTIFIED


def test_raw_outcome_variance_is_named_forbidden():
    assert "RAW_OUTCOME_VARIANCE" in POW.FORBIDDEN_VARIANCE_SOURCES
    assert "CONTRACT_ROW_VARIANCE" in POW.FORBIDDEN_VARIANCE_SOURCES
    assert POW.POWER_VARIANCE_SOURCE == "PAIRED_EVENT_LEVEL_SCORE_DIFFERENCES"


def test_the_question_is_stated_in_events():
    assert "events" in POW.THE_QUESTION
    assert "contract observations" in POW.THE_QUESTION_IT_IS_NOT


# ---------------------------------------------------------------------------
# Section 22. Nesting.
# ---------------------------------------------------------------------------

def test_leak_check_catches_a_shared_event():
    w = {"CALIBRATION": ["e1", "e2"], "DEVELOPMENT": ["e3"],
         "TEST": ["e2", "e4"]}
    lc = NEST.leak_check(w)
    assert lc["NESTING_STATUS"] == "LEAKED"
    assert lc["VIOLATIONS"][0]["EARLIER"] == "CALIBRATION"
    assert lc["VIOLATIONS"][0]["LATER"] == "TEST"


def test_leak_check_passes_on_disjoint_windows():
    w = {"CALIBRATION": ["e1"], "DEVELOPMENT": ["e2"], "TEST": ["e3"]}
    assert NEST.leak_check(w)["NESTING_STATUS"] == "CLEAN"


def test_disjointness_is_by_event_not_by_row():
    assert NEST.leak_check({"DEVELOPMENT": ["e1"], "TEST": ["e1"]}
                           )["UNIT_OF_DISJOINTNESS"] == "EVENT_NOT_ROW"
    assert NEST.leak_check({"DEVELOPMENT": ["e1"], "TEST": ["e1"]}
                           )["NESTING_STATUS"] == "LEAKED"


def test_chronological_windows_put_later_events_in_test():
    evs = ["lg-a-b-2026-0%d-01" % m for m in range(1, 10)]
    w = NEST.chronological_windows(evs, (0.0, 0.0, 0.6, 0.4))
    assert NEST.leak_check(w)["NESTING_STATUS"] == "CLEAN"
    assert max(w["DEVELOPMENT"]) < min(w["TEST"])


def test_rounding_remainder_never_lands_in_an_earlier_window():
    evs = ["lg-a-b-2026-01-%02d" % d for d in range(1, 8)]
    w = NEST.chronological_windows(evs, (0.0, 0.0, 0.5, 0.5))
    total = sum(len(v) for v in w.values())
    assert total == len(evs)


def test_run_nested_refuses_when_the_windows_leak():
    rows = [{"EVENT_KEY": "e1", "Y": 1, "P_MARKET": .5, "P_CH": .6}]
    out = NEST.run_nested(rows, "P_CH",
                          windows={"DEVELOPMENT": ["e1"], "TEST": ["e1"]})
    assert out["STATUS"] == "REFUSED_NESTING_LEAKED"


def test_calibrator_selection_happens_in_the_calibration_window():
    pairs = [(0.9, 1) for _ in range(200)] + [(0.9, 0) for _ in range(200)]
    cal = NEST.fit_and_select_calibrator(pairs, n_cal_events=400)
    assert cal["SELECTED_ON"] == "CALIBRATION_WINDOW_W1_KFOLD_CV"
    assert cal["METHOD_SELECTION_IS_PART_OF_FITTING"] is True


def test_a_calibrator_actually_corrects_a_biased_forecaster():
    """A forecaster that always says 0.9 when the truth is 0.5 gets pulled in."""
    rnd = random.Random(11)
    pairs = [(0.9, 1 if rnd.random() < 0.5 else 0) for _ in range(600)]
    cal = NEST.fit_and_select_calibrator(pairs, n_cal_events=600)
    corrected = NEST.apply_calibrator(cal["SELECTED"], 0.9)
    assert abs(corrected - 0.5) < abs(0.9 - 0.5)


def test_isotonic_is_refused_below_the_event_floor():
    pairs = [(i / 100.0, i % 2) for i in range(1, 100)]
    out = NEST._fit_isotonic(pairs, n_events=10)
    assert out is None


def test_identity_calibrator_is_a_no_op():
    assert NEST.apply_calibrator({"METHOD": "IDENTITY"}, 0.37) == pytest.approx(0.37)
    assert NEST.apply_calibrator(None, 0.37) == pytest.approx(0.37)


def test_isotonic_is_monotone():
    rnd = random.Random(3)
    pairs = [(p, 1 if rnd.random() < p else 0)
             for p in [i / 400.0 for i in range(1, 400)]]
    cal = NEST._fit_isotonic(pairs, n_events=500)
    vals = [NEST.apply_calibrator(cal, p) for p in
            [i / 50.0 for i in range(1, 50)]]
    assert all(b >= a - 1e-9 for a, b in zip(vals, vals[1:]))


def test_the_nested_run_declares_that_the_test_outcomes_were_never_used():
    rnd = random.Random(5)
    rows = []
    for e in range(60):
        ev = "lg-a-b-2026-01-%02d" % (1 + e % 28) + "#%d" % e
        for _ in range(6):
            y = 1 if rnd.random() < 0.55 else 0
            rows.append({"EVENT_KEY": ev, "Y": y,
                         "P_MARKET": min(max(.55 + rnd.gauss(0, .08), .02), .98),
                         "P_CH": min(max(.55 + rnd.gauss(0, .12), .02), .98)})
    evs = sorted({r["EVENT_KEY"] for r in rows})
    w = {"DEVELOPMENT": evs[:36], "TEST": evs[36:]}
    cal_pairs = [(0.6, 1 if rnd.random() < 0.6 else 0) for _ in range(300)]
    out = NEST.run_nested(rows, "P_CH", windows=w, calibration_pairs=cal_pairs,
                          n_calibration_events=300, population_transport=True,
                          draws=100)
    assert out["STATUS"] == "MEASURED"
    assert out["EVERY_PROBABILITY_IN_THE_TEST_WAS_GENERATED_WITHOUT_TEST_OUTCOMES"]
    assert out["POPULATION_TRANSPORT_ASSUMED"] is True
    assert out["LEAK_CHECK"]["NESTING_STATUS"] == "CLEAN"


def test_the_window_order_is_the_declared_one():
    assert NEST.WINDOW_ORDER == ("TRAIN", "CALIBRATION", "DEVELOPMENT", "TEST")
    assert "TEST_OUTCOMES" in NEST.CALIBRATOR_MAY_NOT_SEE
    assert "DEVELOPMENT_OUTCOMES" in NEST.CALIBRATOR_MAY_NOT_SEE


# ---------------------------------------------------------------------------
# Section 23. As-of provenance.
# ---------------------------------------------------------------------------

def test_a_source_without_a_publication_timestamp_cannot_be_proven():
    r = AP.source_record("anything", publication_timestamp_available=False,
                         event_timestamp_field="date", status=AP.PROVEN)
    assert r["ASOF_PROVENANCE_STATUS"] != AP.PROVEN


def test_elo_is_not_proven_and_the_reason_names_revision():
    assert AP.status_of("XGABORA_ELO") == AP.NOT_PROVEN
    assert "regenerated" in AP.SOURCES["XGABORA_ELO"]["DETAIL"]


def test_xg_and_player_data_are_not_proven_in_advance():
    assert AP.status_of("XG_ANY_PROVIDER") == AP.NOT_PROVEN
    assert AP.status_of("PLAYER_AVAILABILITY") == AP.NOT_PROVEN


def test_player_availability_binds_on_announcement_not_match_date():
    d = AP.SOURCES["PLAYER_AVAILABILITY"]["DETAIL"]
    assert "ANNOUNCEMENT" in d and "NOT the match date" in d


def test_the_venue_stamps_are_proven():
    assert AP.status_of("VENUE_SETTLEMENT") == AP.PROVEN
    assert AP.status_of("VENUE_OBSERVATION") == AP.PROVEN


def test_known_before_refuses_rather_than_guessing():
    assert AP.known_before("2026-01-02T00:00:00Z", None) == AP.NOT_IDENTIFIED
    assert AP.known_before(None, "2026-01-01T00:00:00Z") == AP.NOT_IDENTIFIED
    assert AP.known_before("2026-01-02T00:00:00Z",
                           "2026-01-01T00:00:00Z") is True
    assert AP.known_before("2026-01-01T00:00:00Z",
                           "2026-01-02T00:00:00Z") is False


def test_no_current_feature_enters_the_high_integrity_lane():
    """The gate is strict and currently refuses everything. That is the finding."""
    import p_bettor_independent_v2 as V2
    import p_bettor_independent_v3 as V3
    feats = list(V2.GLM_FEATURES) + list(V3.INTERNAL_STRENGTH_FEATURES)
    lane = AP.lane_features(feats, "HIGH_INTEGRITY")
    assert lane["ADMITTED_COUNT"] == 0
    assert lane["EXCLUDED_COUNT"] == len(feats)


def test_every_model_feature_has_a_declared_source():
    import p_bettor_independent_v2 as V2
    import p_bettor_independent_v3 as V3
    feats = list(V2.GLM_FEATURES) + list(V3.INTERNAL_STRENGTH_FEATURES)
    missing = [f for f in feats if f not in AP.FEATURE_SOURCES]
    assert missing == []


def test_exploratory_lane_admits_what_high_integrity_refuses():
    ok_hi, _ = AP.admit("SHOTS5_HOME", "HIGH_INTEGRITY")
    ok_lo, _ = AP.admit("SHOTS5_HOME", "EXPLORATORY")
    assert ok_hi is False and ok_lo is True


def test_an_undeclared_feature_is_refused_by_both_lanes():
    assert AP.admit("SOME_NEW_FEATURE", "HIGH_INTEGRITY")[0] is False
    assert AP.admit("SOME_NEW_FEATURE", "EXPLORATORY")[0] is False


def test_a_tight_asof_claim_is_currently_refused():
    out = AP.tight_asof_claim_allowed(["ELO_DIFF", "SHOTS5_HOME"])
    assert out["TIGHT_ASOF_CLAIM_ALLOWED"] is False
    assert out["UNPROVEN_COUNT"] == 2


def test_assumed_bounded_is_explicitly_not_enough():
    assert AP.ASSUMED_BOUNDED not in AP.HIGH_INTEGRITY_ADMITS
    assert "tight as-of claim" in AP.WHY_ASSUMED_BOUNDED_IS_NOT_ENOUGH


def test_the_rule_is_stated():
    assert "existed somewhere in a historical database later" in AP.THE_RULE
