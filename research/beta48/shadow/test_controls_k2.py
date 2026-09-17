"""Tests for the second control set: canonical delta, bootstrap ladder,
archival provenance, five classes, internal Elo, and the two lanes."""

import random

import pytest

import asof_archive as AR
import asof_provenance as AP
import bettor_internal_elo as ELO
import ev_core_delta as DELTA
import ev_core_power as POW
import high_integrity_v3 as HI


# ---------------------------------------------------------------------------
# Section 1. The canonical delta sign. The tests the directive asked for.
# ---------------------------------------------------------------------------

def _rows(p_b0, p_ch, y, n=40):
    return [{"EVENT_KEY": "lg-a-b-2026-01-%02d" % (1 + i % 28), "Y": y,
             "P_MARKET": p_b0, "P_CH": p_ch} for i in range(n)]


def test_an_obviously_better_challenger_gives_a_positive_delta():
    """B0 says 0.20 when the answer is YES; the challenger says 0.90."""
    rows = _rows(0.20, 0.90, 1)
    d = DELTA.delta(rows, "P_CH")
    assert d["DELTA"] > 0
    assert d["INTERPRETATION"] == DELTA.POSITIVE_MEANS


def test_an_obviously_worse_challenger_gives_a_negative_delta():
    rows = _rows(0.90, 0.20, 1)
    d = DELTA.delta(rows, "P_CH")
    assert d["DELTA"] < 0
    assert d["INTERPRETATION"] == DELTA.NEGATIVE_MEANS


def test_an_identical_challenger_gives_exactly_zero():
    rows = _rows(0.62, 0.62, 1)
    assert DELTA.delta(rows, "P_CH")["DELTA"] == pytest.approx(0.0, abs=1e-12)
    assert DELTA.delta(rows, "P_CH")["INTERPRETATION"] == DELTA.ZERO_MEANS


def test_the_sign_holds_for_brier_too():
    better = DELTA.delta(_rows(0.20, 0.90, 1), "P_CH", score="BRIER")
    worse = DELTA.delta(_rows(0.90, 0.20, 1), "P_CH", score="BRIER")
    assert better["DELTA"] > 0 and worse["DELTA"] < 0


def test_the_sign_holds_when_the_outcome_is_no():
    """A challenger confident in the right direction wins either way."""
    better = DELTA.delta(_rows(0.80, 0.10, 0), "P_CH")
    worse = DELTA.delta(_rows(0.10, 0.80, 0), "P_CH")
    assert better["DELTA"] > 0 and worse["DELTA"] < 0


def test_per_event_differences_carry_the_same_orientation():
    d = DELTA.delta(_rows(0.20, 0.90, 1), "P_CH")
    assert all(v > 0 for v in d["DELTA_PER_EVENT"].values())


def test_both_scores_agree_on_direction():
    out = DELTA.delta_both_scores(_rows(0.20, 0.90, 1), "P_CH")
    assert out["DELTA_LOG_LOSS"]["DELTA"] > 0
    assert out["DELTA_BRIER"]["DELTA"] > 0


def test_the_definition_is_stated_and_singular():
    assert DELTA.CANONICAL_DELTA_DEFINITION == "SCORE_B0 - SCORE_CHALLENGER"
    assert DELTA.NO_OTHER_DELTA_ORIENTATION_MAY_BE_EMITTED is True
    assert DELTA.POSITIVE_MEANS == "CHALLENGER_IMPROVES_ON_B0"


def test_a_delta_is_always_printed_with_an_explicit_sign():
    assert DELTA.format_delta(0.01).startswith("+")
    assert DELTA.format_delta(-0.01).startswith("-")


def test_the_convention_covers_every_named_surface():
    for s in ("STANDALONE", "INCREMENTAL_BLEND", "ENSEMBLE", "SUBGROUPS",
              "REPORTS", "TABLES", "CONFIDENCE_INTERVALS"):
        assert s in DELTA.APPLIES_TO


# ---------------------------------------------------------------------------
# Section 2. Uncertainty around the ladder.
# ---------------------------------------------------------------------------

def _diffs(n=60, sd=0.3, seed=4):
    rnd = random.Random(seed)
    return {"e%d" % i: rnd.gauss(0.0, sd) for i in range(n)}


def test_the_bootstrap_reports_every_required_quantile():
    bs = POW.bootstrap_required_n(_diffs(), draws=400)
    row = bs["BY_EFFECT_SIZE"][0.010]
    for k in ("REQUIRED_N_POINT_ESTIMATE", "REQUIRED_N_BOOTSTRAP_MEDIAN",
              "REQUIRED_N_P75", "REQUIRED_N_P90", "REQUIRED_N_P95"):
        assert isinstance(row[k], int)


def test_the_quantiles_are_ordered():
    row = POW.bootstrap_required_n(_diffs(), draws=800)["BY_EFFECT_SIZE"][0.010]
    assert (row["REQUIRED_N_BOOTSTRAP_MEDIAN"] <= row["REQUIRED_N_P75"]
            <= row["REQUIRED_N_P90"] <= row["REQUIRED_N_P95"])


def test_the_ladder_value_is_the_conservative_quantile_not_the_point():
    row = POW.bootstrap_required_n(_diffs(), draws=800)["BY_EFFECT_SIZE"][0.010]
    assert row["LADDER_VALUE"] == row["REQUIRED_N_P90"]
    assert row["LADDER_VALUE"] >= row["REQUIRED_N_POINT_ESTIMATE"]


def test_the_bootstrap_resamples_events_not_rows():
    bs = POW.bootstrap_required_n(_diffs(), draws=200)
    assert bs["RESAMPLING_UNIT"] == "EVENT_NOT_ROW"
    assert bs["POWER_ESTIMATE_UNCERTAINTY_STATUS"] == \
        "QUANTIFIED_BY_EVENT_BOOTSTRAP"


def test_a_smaller_sample_gives_a_wider_required_n_spread():
    small = POW.bootstrap_required_n(_diffs(20), draws=800)["BY_EFFECT_SIZE"][0.010]
    large = POW.bootstrap_required_n(_diffs(400), draws=800)["BY_EFFECT_SIZE"][0.010]
    sp = lambda r: r["REQUIRED_N_P95"] / max(r["REQUIRED_N_BOOTSTRAP_MEDIAN"], 1)
    assert sp(small) > sp(large)


def test_cluster_sensitivity_runs_and_is_marked_secondary():
    d = {"epl-a-b-%d" % i: random.Random(i).gauss(0, .3) for i in range(40)}
    d.update({"lal-a-b-%d" % i: random.Random(100 + i).gauss(0, .3)
              for i in range(40)})
    cs = POW.cluster_sensitivity(d, lambda e: e.split("-")[0], draws=200)
    assert cs["STATUS"] in ("MEASURED", "TOO_FEW_CLUSTERS")
    if cs["STATUS"] == "MEASURED":
        assert cs["THIS_IS_A_SENSITIVITY_NOT_THE_PRIMARY"] is True


def test_too_few_events_refuses_rather_than_guessing():
    assert POW.bootstrap_required_n({"a": 1.0, "b": 2.0})["STATUS"] == \
        "TOO_FEW_EVENTS"


# ---------------------------------------------------------------------------
# Sections 4, 5, 9. Archival provenance.
# ---------------------------------------------------------------------------

def test_the_forbidden_inference_is_named():
    assert "today's version" in AR.THE_FORBIDDEN_INFERENCE
    assert "latency" in AR.WHY_IT_IS_FORBIDDEN


def test_proven_before_is_strict_and_refuses_on_missing_data():
    rec = {"FIRST_PROVEN_AVAILABLE_AT": "2026-08-24T11:11:57+00:00"}
    assert AR.proven_before(rec, "2026-08-28T00:00:00+00:00") is True
    assert AR.proven_before(rec, "2026-08-20T00:00:00+00:00") is False
    assert AR.proven_before({}, "2026-08-28T00:00:00+00:00") == \
        AR.NOT_IDENTIFIED


def test_the_xgabora_repo_is_marked_unusable_for_high_integrity_with_a_reason():
    r = AR.REPOS["xgabora/Club-Football-Match-Data"]
    assert r["USABLE_FOR_HIGH_INTEGRITY"] is False
    assert "2025-06-27" in r["WHY_NOT"]


def test_openfootball_is_the_repo_that_makes_the_lane_possible():
    assert AR.REPOS["openfootball/football.json"]["USABLE_FOR_HIGH_INTEGRITY"]


def test_admissible_history_excludes_rows_committed_after_the_decision():
    fs = {
        "early": {"FIRST_PROVEN_AVAILABLE_AT": "2026-08-01T00:00:00+00:00",
                  "FILE_PATH": "2026-27/en.1.json"},
        "late": {"FIRST_PROVEN_AVAILABLE_AT": "2026-09-05T00:00:00+00:00",
                 "FILE_PATH": "2026-27/en.1.json"},
    }
    got = AR.admissible_history(fs, "2026-08-28T00:00:00+00:00")
    assert len(got) == 1
    assert got[0]["FIRST_PROVEN_AVAILABLE_AT"].startswith("2026-08-01")


# ---------------------------------------------------------------------------
# Section 6. Five classes.
# ---------------------------------------------------------------------------

def test_there_are_five_classes_and_d_is_excluded_everywhere():
    assert AP.CLASSES == (AP.PROVEN_NATIVE, AP.PROVEN_ARCHIVAL,
                          AP.PROVEN_DERIVED, AP.CONSERVATIVELY_BOUNDED,
                          AP.NOT_PROVEN)
    assert AP.NOT_PROVEN not in AP.HIGH_INTEGRITY_ADMITS
    assert AP.NOT_PROVEN not in AP.RESEARCH_ADMITS
    assert AP.D_IS_EXCLUDED_FROM_ANY_ASOF_MODEL is True


def test_high_integrity_admits_only_a_b_e():
    assert set(AP.HIGH_INTEGRITY_ADMITS) == {
        AP.PROVEN_NATIVE, AP.PROVEN_ARCHIVAL, AP.PROVEN_DERIVED}
    assert AP.CONSERVATIVELY_BOUNDED not in AP.HIGH_INTEGRITY_ADMITS


def test_research_lane_adds_c_and_only_c():
    assert AP.CONSERVATIVELY_BOUNDED in AP.RESEARCH_ADMITS
    assert set(AP.RESEARCH_ADMITS) - set(AP.HIGH_INTEGRITY_ADMITS) == {
        AP.CONSERVATIVELY_BOUNDED}


def test_only_the_high_integrity_lane_may_become_fair_value():
    assert AP.LANE_RULES["HIGH_INTEGRITY"][
        "MAY_BECOME_BETTOR_INDEPENDENT_FAIR_VALUE"] is True
    assert AP.LANE_RULES["RESEARCH"][
        "MAY_BECOME_BETTOR_INDEPENDENT_FAIR_VALUE"] is False


def test_a_source_cannot_promote_itself_to_proven_native():
    r = AP.source_record("x", publication_timestamp_available=False,
                         event_timestamp_field="d", status=AP.PROVEN_NATIVE)
    assert r["ASOF_PROVENANCE_STATUS"] == AP.NOT_PROVEN


def test_the_archive_gives_class_b_not_class_a():
    """openfootball has no publication stamp; the commit is what proves it."""
    assert AP.status_of("OPENFOOTBALL_ARCHIVAL_RESULTS") == AP.PROVEN_ARCHIVAL


def test_repository_elo_and_xg_stay_not_proven():
    assert AP.status_of("XGABORA_ELO") == AP.NOT_PROVEN
    assert AP.status_of("XG_ANY_PROVIDER") == AP.NOT_PROVEN
    assert AP.status_of("PLAYER_AVAILABILITY") == AP.NOT_PROVEN


# ---------------------------------------------------------------------------
# Sections 7 and 8. Derived features and the internal Elo.
# ---------------------------------------------------------------------------

def _series(n=200, seed=1):
    import datetime
    rnd = random.Random(seed)
    d0 = datetime.date(2025, 1, 1)
    clubs = ["c%d" % i for i in range(8)]
    out = []
    for k in range(n):
        h, a = rnd.sample(clubs, 2)
        out.append({"DATE": (d0 + datetime.timedelta(days=k)).isoformat(),
                    "HOME": h, "AWAY": a,
                    "FT_HOME": rnd.randint(0, 3), "FT_AWAY": rnd.randint(0, 3)})
    return out


def test_a_future_result_cannot_change_any_earlier_elo_state():
    """THE test. This is what separates PROVEN_DERIVED from regenerated."""
    ms = _series()
    future = {"DATE": "2099-01-01", "HOME": "c0", "AWAY": "c1",
              "FT_HOME": 9, "FT_AWAY": 0}
    v = ELO.assert_causal(ms, future, snapshot_every=10)
    assert v["EARLIER_FEATURE_ROWS_IDENTICAL"] is True
    assert v["EARLIER_STATE_SNAPSHOTS_IDENTICAL"] is True
    assert v["CAUSAL"] is True


def test_elo_reads_state_before_the_match_it_consumes():
    e = ELO.InternalElo()
    before = e.read_before("a", "b")
    assert before["INT_ELO_HOME"] == ELO.ELO_START
    e.update("a", "b", 5, 0)
    assert e.read_before("a", "b")["INT_ELO_HOME"] > ELO.ELO_START


def test_elo_refuses_an_out_of_order_update():
    e = ELO.InternalElo()
    e.update("a", "b", 1, 0, "2026-01-02")
    with pytest.raises(ValueError):
        e.update("a", "b", 1, 0, "2026-01-01")


def test_elo_is_zero_sum_within_a_match():
    e = ELO.InternalElo()
    e.update("a", "b", 3, 0)
    assert e.get("a") + e.get("b") == pytest.approx(2 * ELO.ELO_START)


def test_goal_difference_elo_moves_further_on_a_rout():
    plain, gd = ELO.InternalElo(), ELO.InternalElo(goal_difference=True)
    plain.update("a", "b", 5, 0)
    gd.update("a", "b", 5, 0)
    assert gd.get("a") > plain.get("a")


def test_the_repository_elo_is_rejected_with_its_reason():
    assert "date it was computed" in ELO.WHY_NOT_THE_REPOSITORY_ELO


def test_derived_features_are_class_e_not_their_sources_class():
    assert AP.feature_status("HI_ELO_DIFF") == AP.PROVEN_DERIVED
    assert AP.status_of("OPENFOOTBALL_ARCHIVAL_RESULTS") == AP.PROVEN_ARCHIVAL


def test_every_high_integrity_feature_is_admitted_to_the_lane():
    lane = AP.lane_features(AP.HIGH_INTEGRITY_FEATURES, "HIGH_INTEGRITY")
    assert lane["EXCLUDED_COUNT"] == 0
    assert lane["ADMITTED_COUNT"] == len(AP.HIGH_INTEGRITY_FEATURES)


def test_shots_and_repository_elo_stay_out_of_the_high_integrity_lane():
    for f in ("SHOTS5_HOME", "TARGET5_HOME", "CORNERS5_HOME", "ELO_DIFF"):
        assert AP.admit(f, "HIGH_INTEGRITY")[0] is False
    for f in ("SHOTS5_HOME", "TARGET5_HOME", "CORNERS5_HOME"):
        assert AP.admit(f, "RESEARCH")[0] is True
    # the repository Elo is NOT_PROVEN, so even the research lane refuses it
    assert AP.admit("ELO_DIFF", "RESEARCH")[0] is False


def test_the_exclusions_are_documented_with_reasons():
    ex = AP.FEATURES_DELIBERATELY_EXCLUDED_FROM_HIGH_INTEGRITY
    assert "2025-06-27" in ex["SHOTS_TARGET_CORNERS_CARDS"]
    assert "PROVEN_DERIVED" in ex["REPOSITORY_ELO"]


# ---------------------------------------------------------------------------
# Section 12. The lanes, and the gate that feeds them.
# ---------------------------------------------------------------------------

def test_the_decision_time_is_midnight_not_kickoff():
    assert HI.decision_time("2026-08-28") == "2026-08-28T00:00:00+00:00"
    assert "class A is empty" in HI.WHY_MIDNIGHT


def test_the_archive_gate_fails_closed_without_a_proving_commit():
    ok, why = HI.archive_gate({"FEATURES_PROVEN_BY": None}, "2026-08-28T00:00:00+00:00")
    assert ok is False
    assert why == "NO_PROVING_COMMIT_FOR_THE_CONTRIBUTING_HISTORY"


def test_the_archive_gate_refuses_history_committed_after_the_decision():
    row = {"FEATURES_PROVEN_BY": "2026-09-05T00:00:00+00:00"}
    ok, why = HI.archive_gate(row, "2026-08-28T00:00:00+00:00")
    assert ok is False
    assert why == "CONTRIBUTING_HISTORY_COMMITTED_AFTER_THE_DECISION"


def test_the_archive_gate_admits_history_proven_earlier():
    row = {"FEATURES_PROVEN_BY": "2026-08-24T11:11:57+00:00"}
    ok, why = HI.archive_gate(row, "2026-08-28T00:00:00+00:00")
    assert ok is True and why == AP.PROVEN_ARCHIVAL


def test_the_feature_builder_never_lets_a_row_see_its_own_result():
    ms = _series(60)
    frame = HI.build_high_integrity_frame(ms)
    assert frame[0]["HI_MATCHES_SEEN_HOME"] == 0
    assert frame[0]["HI_GF5_HOME"] is None
    assert frame[-1]["HI_MATCHES_SEEN_HOME"] > 0


def test_the_research_lane_can_never_earn_production_status():
    assert HI.RESEARCH_LANE_MAY_NOT_EARN_PRODUCTION_STATUS is True
    assert HI.RESEARCH_RESULTS_ARE_LABELLED == "RESEARCH_ONLY"


def test_the_lane_is_not_expected_to_improve():
    assert "negative answer is a result" in HI.describe()["NOT_EXPECTED_TO_IMPROVE"]
