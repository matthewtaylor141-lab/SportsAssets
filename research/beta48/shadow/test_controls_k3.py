"""Tests for the ladder separation, archival versioning, and the class-B audit."""

import pytest

import asof_archive as AR
import bettor_internal_elo as ELO
import ev_core_registers as REG
import event_start_time as EST
import high_integrity_v3 as HI


# --- Sections 1-4. Two ladders, permanently separate. ----------------------

def test_the_two_ladders_are_separate_objects():
    assert REG.STANDALONE_LADDER is not REG.INCREMENTAL_LADDER
    assert set(REG.STANDALONE_LADDER) == set(REG.INCREMENTAL_LADDER)
    assert REG.LADDERS_MAY_NOT_BE_COMBINED is True


def test_the_primary_management_ladder_is_incremental():
    assert REG.PRIMARY_MANAGEMENT_EVIDENCE_LADDER == "INCREMENTAL"
    assert "standalone" in REG.WHY_INCREMENTAL_IS_PRIMARY
    assert "may not be quoted" in REG.WHY_INCREMENTAL_IS_PRIMARY


def test_the_two_questions_are_stated_differently():
    assert "itself beats B0" in REG.STANDALONE_QUESTION
    assert "improves the market forecast" in REG.INCREMENTAL_QUESTION


def test_the_incremental_requirement_is_smaller_than_the_standalone_one():
    for lane in REG.STANDALONE_LADDER:
        for d in (0.002, 0.005, 0.010, 0.020):
            assert (REG.INCREMENTAL_LADDER[lane][d]["POINT"]
                    < REG.STANDALONE_LADDER[lane][d]["POINT"]), (lane, d)


def test_every_rung_carries_every_required_quantile():
    for lad in (REG.STANDALONE_LADDER, REG.INCREMENTAL_LADDER):
        for lane, rungs in lad.items():
            for d, v in rungs.items():
                for k in ("POINT", "P50", "P75", "P90", "P95"):
                    assert k in v, (lane, d, k)
                assert v["P50"] <= v["P75"] <= v["P90"] <= v["P95"]


def test_the_old_incremental_sd_is_superseded_with_its_reason():
    assert REG.SUPERSEDED_INCREMENTAL_SD == 0.0301
    assert "almost no weight" in REG.WHY_THE_INCREMENTAL_SD_WAS_ALSO_WRONG
    assert REG.INCREMENTAL_SD_EVENT["P_HIGH_INTEGRITY"] > 0.2


def test_the_incremental_sd_came_from_out_of_fold_stacking():
    assert "out-of-fold" in REG.INCREMENTAL_SD_METHOD
    assert "fitted on other events only" in REG.INCREMENTAL_SD_METHOD


# --- Section 5. Every table carries its N. ---------------------------------

def test_the_ladder_set_declares_rows_events_weighting_and_range():
    s = REG.LADDER_EVALUATION_SET
    for k in REG.REQUIRED_TABLE_FIELDS:
        assert k in s or k == "INDEPENDENT_EVENTS"
    assert s["COMMON_EVENTS"] == 47
    assert s["CONTRACT_ROWS"] == 666
    assert s["EVENT_WEIGHTING_METHOD"] == "EVENT_EQUAL_WEIGHTED"
    assert s["BOTH_LANES_ON_THE_SAME_EVENT_SET"] is True


def test_both_incremental_results_carry_their_event_count():
    for lane in ("P_HIGH_INTEGRITY", "P_RESEARCH"):
        r = REG.INCREMENTAL_RESULT[lane]
        assert r["INDEPENDENT_EVENTS"] == 47
        assert r["CONTRACT_ROWS"] == 666


def test_a_not_directly_comparable_marker_exists():
    assert REG.NOT_DIRECTLY_COMPARABLE == "NOT_DIRECTLY_COMPARABLE"


# --- Section 7. Archival versioning. ---------------------------------------

def _rec():
    return {"ROW_KEY": "k", "FEATURE_NAME": "FULL_TIME_SCORE", "VERSIONS": [
        {"VERSION_INDEX": 0, "VALUE": (1, 1), "FIRST_SEEN_AT": "2026-01-10T00:00:00+00:00"},
        {"VERSION_INDEX": 1, "VALUE": (0, 2), "FIRST_SEEN_AT": "2026-03-05T00:00:00+00:00"},
    ]}


def test_a_correction_after_t_cannot_alter_the_value_at_t():
    v = AR.asof_row(_rec(), "2026-02-01T00:00:00+00:00")
    assert v["VALUE"] == (1, 1)
    assert v["VERSION_INDEX"] == 0
    assert v["A_LATER_VERSION_EXISTS"] is True


def test_a_correction_before_a_later_prediction_is_used_by_it():
    v = AR.asof_row(_rec(), "2026-06-01T00:00:00+00:00")
    assert v["VALUE"] == (0, 2)
    assert v["VERSION_INDEX"] == 1
    assert v["A_LATER_VERSION_EXISTS"] is False


def test_a_row_not_yet_in_the_archive_is_refused_not_defaulted():
    v = AR.asof_row(_rec(), "2025-01-01T00:00:00+00:00")
    assert v["STATUS"] == "NOT_YET_IN_THE_ARCHIVE_AT_T"
    assert "VALUE" not in v


def test_a_future_correction_does_not_alter_an_earlier_feature_vector():
    """The test the directive asked for, at the feature level."""
    base = [{"DATE": "2026-01-0%d" % d, "HOME": "a" if d % 2 else "b",
             "AWAY": "b" if d % 2 else "a", "FT_HOME": 1, "FT_AWAY": 0,
             "FIRST_PROVEN_AVAILABLE_AT": "2026-01-0%dT12:00:00+00:00" % d}
            for d in range(1, 8)]
    before = HI.build_high_integrity_frame(base)
    # a LATER correction to the last match, first seen after everything earlier
    corrected = [dict(m) for m in base]
    corrected[-1] = dict(corrected[-1], FT_HOME=4, FT_AWAY=4,
                         FIRST_PROVEN_AVAILABLE_AT="2026-06-01T00:00:00+00:00")
    after = HI.build_high_integrity_frame(corrected)
    assert before[:-1] == after[:-1], "an earlier feature vector moved"


def test_revised_rows_are_versioned_not_discarded():
    assert AR.value_asof(_rec()["VERSIONS"], "2026-02-01T00:00:00+00:00")["VALUE"] \
        == (1, 1)
    assert AR.value_asof([], "2026-02-01T00:00:00+00:00") is None


# --- Section 8. Archive observation is not source publication. -------------

def test_the_availability_bound_is_labelled_archive_first_seen():
    assert AR.AVAILABILITY_BOUND == "ARCHIVE_FIRST_SEEN"
    assert "VALUE_WAS_PRESENT_IN_THIS_ARCHIVE_BY_T" in \
        AR.WHAT_A_COMMIT_TIMESTAMP_PROVES
    assert "does NOT prove the original upstream publication" in \
        AR.WHAT_A_COMMIT_TIMESTAMP_PROVES


def test_the_resolved_row_carries_that_label():
    assert AR.asof_row(_rec(), "2026-06-01T00:00:00+00:00")["AVAILABILITY_BOUND"] \
        == "ARCHIVE_FIRST_SEEN"


# --- Section 9. The class-B audit and downgrade. ---------------------------

def test_two_mirrors_of_one_upstream_are_not_two_confirmations():
    assert EST.CLASS_B_INDEPENDENCE_STATUS == "NOT_ESTABLISHED"
    assert EST.CLASS_B_UPSTREAMS["DISTINCT_UPSTREAM_PAIRS"] == 1
    assert "not two confirmations" in EST.WHY_THE_DOWNGRADE


def test_a_dependent_class_b_is_downgraded_and_its_uncertainty_rises():
    c = EST.classify(source_a=("2026-08-30", "15:00"),
                     source_b=("2026-08-30", "15:00"), offset_hours=0.0,
                     resolved_at="2026-08-30T18:00:00Z")
    assert c["START_TIME_STATUS"] == EST.CLASS_B
    d = EST.downgrade_for_dependence(c)
    assert d["START_TIME_STATUS"] == EST.CLASS_B_SHARED
    assert d["UNCERTAINTY_HOURS"] > c["UNCERTAINTY_HOURS"]
    assert d["DOWNGRADED_FROM"] == EST.CLASS_B


def test_the_downgrade_costs_the_two_hour_rung():
    assert EST.horizon_claimable(24.0, EST.CLASS_B_SHARED)[0] is True
    assert EST.horizon_claimable(6.0, EST.CLASS_B_SHARED)[0] is True
    assert EST.horizon_claimable(2.0, EST.CLASS_B_SHARED)[0] is False
    assert EST.horizon_claimable(0.25, EST.CLASS_B_SHARED)[0] is False


def test_class_a_and_c_are_untouched_by_the_downgrade():
    a = EST.classify(venue_native="2026-09-18T00:15:00Z")
    assert EST.downgrade_for_dependence(a) == a
    c = EST.classify(source_a=("2026-08-30", "18:00:00"))
    assert EST.downgrade_for_dependence(c) == c


def test_independence_of_names_the_shared_root():
    assert EST.independence_of("x", "x") == "SHARED_UPSTREAM"
    assert EST.independence_of("x", "y") == \
        "DISTINCT_COMPILATIONS_SHARED_ROOT_AUTHORITY"
    assert EST.independence_of(None, "y") == "NOT_IDENTIFIED"


def test_the_audit_row_carries_every_field_section_9_asked_for():
    c = EST.downgrade_for_dependence(EST.classify(
        source_a=("2026-08-30", "15:00"), source_b=("2026-08-30", "15:00"),
        offset_hours=0.0, resolved_at="2026-08-30T18:00:00Z"))
    row = EST.audit_row("e1", {"TIME": "15:00"}, {"TIME": "15:00:00"}, 0.0, c)
    for k in ("SOURCE_1", "SOURCE_2", "SOURCE_1_UPSTREAM", "SOURCE_2_UPSTREAM",
              "TIME_1", "TIME_2", "ABS_DIFFERENCE_SECONDS",
              "TIMEZONE_ASSUMPTION", "UNCERTAINTY_SECONDS"):
        assert k in row, k


def test_the_audited_class_counts_are_recorded():
    a = REG.START_TIME_CLASS_AUDIT
    assert a["CLASS_A_EVENTS"] == 0
    assert a["CLASS_B_EVENTS"] == 0
    assert a["CLASS_B_SHARED_UPSTREAM_EVENTS"] == 40
    assert a["TIGHT_HORIZONS_STILL_REQUIRE_CLASS_A"] is True


# --- Sections 10, 11, 13. Language and direction. --------------------------

def test_not_detected_is_not_proven_absent():
    assert REG.HIGH_INTEGRITY_INCREMENTAL_SIGNAL == "NOT_DETECTED"
    assert "not PROVEN_ABSENT" in REG.NOT_PROVEN_ABSENT
    assert "lane is not exhausted" in REG.NOT_PROVEN_ABSENT


def test_the_research_lane_result_does_not_justify_xg_procurement():
    x = REG.XG_PROCUREMENT_LANGUAGE
    assert x["WHAT_THAT_MEANS"] == "CURRENT_UNPROVEN_FUNDAMENTAL_EXTRAS_ADD_LITTLE"
    assert x["CORRECT_STATUS"] == "REAL_XG_REMAINS_UNTESTED"
    assert "no real xG at all" in x["WHAT_IT_DOES_NOT_MEAN"]


def test_the_next_cycle_is_not_more_soccer_model_tuning():
    assert REG.STATIC_SOCCER_MODEL_TUNING_IS_NOT_THE_NEXT_CYCLE is True
    assert REG.NEXT_LARGEST_EXPECTED_INFORMATION_GAIN[0] == \
        "EXACT_TIMESTAMP_EXTERNAL_ODDS"
