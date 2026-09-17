"""Tests for the capture manifest (S4) and the quality gate (S5-S7, S14, S20)."""

import datetime
import os
import tempfile

import capture_manifest as CM
import capture_quality as CQ


def _defs():
    return {"TRANSITION_CLASSES": list(CQ.TRANSITION_CLASSES),
            "TARGET_HORIZONS_SECONDS": [5, 30, 60, 300],
            "INDEPENDENCE_UNIT": "EVENT"}


def _manifest(**over):
    kw = dict(code_sha="bbce49dae0f20d9bad86bfc8f3322f28dc097859",
              capture_start_utc="2026-09-17T18:00:00+00:00",
              capture_seconds=5400,
              event_ids=["e2", "e1", "e3"],
              market_ids=["m1", "m2", "m3", "m4", "m5", "m6"],
              market_families=["MONEYLINE", "SPREAD"],
              poll_interval_s=4.0, rate_rps="0.25",
              request_policy={"METHOD": "GET", "CREDENTIAL": "NONE"},
              selection_sha="a" * 64, capture_spec_sha="b" * 64,
              run_id="12345", host="github-hosted",
              rate_limit_parameters={"RPS": "0.25"},
              scientific_definitions=_defs(),
              frozen_quality_thresholds=CQ.FROZEN_QUALITY_THRESHOLDS,
              frozen_quality_thresholds_sha=CQ.FROZEN_QUALITY_THRESHOLDS_SHA)
    kw.update(over)
    return CM.build_manifest(**kw)


# --- Section 4. The manifest. ----------------------------------------------

def test_the_manifest_carries_every_required_field():
    m = _manifest()
    assert m["MANIFEST_COMPLETE"] is True
    assert m["MISSING_REQUIRED_FIELDS"] == []
    for f in CM.REQUIRED_FIELDS:
        assert f in m


def test_the_planned_end_is_derived_not_supplied():
    m = _manifest()
    assert m["PLANNED_END_UTC"] == "2026-09-17T19:30:00+00:00"


def test_the_hash_does_not_depend_on_dict_order():
    a = _manifest(event_ids=["e1", "e2", "e3"])
    b = _manifest(event_ids=["e3", "e2", "e1"])
    assert a["MANIFEST_SHA"] == b["MANIFEST_SHA"]


def test_changing_any_definition_changes_the_hash():
    a = _manifest()
    b = _manifest(capture_seconds=5401)
    assert a["MANIFEST_SHA"] != b["MANIFEST_SHA"]


def test_an_edited_manifest_is_detected():
    m = _manifest()
    assert CM.manifest_is_intact(m)["INTACT"] is True
    m["CAPTURE_SECONDS"] = 9999
    bad = CM.manifest_is_intact(m)
    assert bad["INTACT"] is False
    assert bad["REASON"] == "MANIFEST_EDITED_AFTER_HASHING"


def test_the_manifest_is_write_once():
    m = _manifest()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "manifest.json")
        first = CM.write_manifest(p, m)
        assert first["WRITTEN"] is True
        second = CM.write_manifest(p, m)
        assert second["WRITTEN"] is False
        assert second["STATUS"] == "REFUSED_MANIFEST_ALREADY_EXISTS"


def test_a_written_manifest_reads_back_identically():
    m = _manifest()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "manifest.json")
        CM.write_manifest(p, m)
        back = CM.read_manifest(p)
    assert CM.manifest_is_intact(back)["INTACT"] is True
    assert back["MANIFEST_SHA"] == m["MANIFEST_SHA"]


def test_the_harvest_may_proceed_when_the_definition_matches():
    m = _manifest()
    live = {k: m[k] for k in CM.COMPARED_AT_HARVEST}
    v = CM.verify_against(m, live)
    assert v["STATUS"] == "MATCH"
    assert v["HARVEST_MAY_PROCEED"] is True


def test_a_silently_changed_horizon_is_caught_and_named():
    m = _manifest()
    live = {k: m[k] for k in CM.COMPARED_AT_HARVEST}
    drifted = dict(_defs())
    drifted["TARGET_HORIZONS_SECONDS"] = [5, 30, 60, 600]   # 300 -> 600
    live["SCIENTIFIC_DEFINITIONS"] = drifted
    v = CM.verify_against(m, live)
    assert v["STATUS"] == "DRIFTED"
    assert v["HARVEST_MAY_PROCEED"] is False
    assert "SCIENTIFIC_DEFINITIONS" in v["DRIFTED_FIELDS"]


def test_drift_is_never_repaired():
    assert "never reconciled" in CM.DRIFT_IS_NOT_REPAIRED


def test_the_manifest_module_contacts_nothing():
    assert CM.describe()["THIS_MODULE_CONTACTS_NOTHING"] is True


# --- Section 6. The bound that does not travel. ----------------------------

def test_a_transition_count_is_a_lower_bound():
    b = CQ.bound_label("OBSERVED_MID_CHANGES", assumptions_hold=True)
    assert b["CLAIM"] == "LOWER_BOUND_ON_TRUE_TRANSITIONS"


def test_a_frequency_does_not_inherit_the_bound():
    b = CQ.bound_label("MID_CHANGES_PER_HOUR")
    assert b["CLAIM"] == "NO_BOUND"
    assert "DENOMINATOR" in b["WHY"]


def test_the_bound_needs_its_assumptions_asserted():
    b = CQ.bound_label("OBSERVED_BOOK_CHANGES", assumptions_hold=False)
    assert b["CLAIM"] == "POINT_ESTIMATE_NO_BOUND"


def test_polling_may_miss_but_may_not_manufacture():
    assert "never manufacture" in CQ.POLLING_MAY_MISS_MAY_NOT_MANUFACTURE


# --- Section 7. Rows are not events. ---------------------------------------

def test_three_events_is_a_pilot_however_many_rows():
    s = CQ.scale_label(3)
    assert s["LABEL"] == "PILOT_PROSPECTIVE_MICROSTRUCTURE_EVIDENCE"


def test_enough_events_earns_the_stronger_label():
    assert CQ.scale_label(50)["LABEL"] == "PROSPECTIVE_MICROSTRUCTURE_EVIDENCE"


# --- Section 5. The quality gate. ------------------------------------------

def _series(n=225, markets=None, start_s=0, step_s=24, bid=0.40, ask=0.42):
    """The frozen capture's real shape: 3 events x 2 markets, 24 s revisit."""
    markets = markets or [("ev%d" % (i // 2 + 1), "m%d" % (i + 1))
                          for i in range(6)]
    base = datetime.datetime(2026, 9, 17, 18, 0,
                             tzinfo=datetime.timezone.utc)
    rows = []
    for seq in range(n):
        for ev, m in markets:
            t = base + datetime.timedelta(seconds=start_s + seq * step_s)
            rows.append({"kind": "TICK", "MARKET_SLUG": m, "seq": seq,
                         "RECEIPT_UTC": t.isoformat(), "EVENT_KEY": ev,
                         "BID": bid, "ASK": ask})
    return rows


PLANNED = 225 * 6


def test_a_clean_capture_passes_the_gate():
    q = CQ.quality(_series(), [], planned_polls=PLANNED,
                   nominal_revisit_s=24.0)
    assert q["CAPTURE_QUALITY_GATE"] == "PASS", q["QUALITY_GATE_FAILURES"]
    assert q["SIGNAL_ANALYSIS_MAY_PROCEED"] is True
    assert q["TIMESTAMP_ROWS"] == PLANNED
    assert q["INDEPENDENT_EVENTS"] == 3
    assert q["MARKETS_WITH_SUFFICIENT_SERIES"] == 6
    assert q["EVENTS_WITH_SUFFICIENT_SERIES"] == 3


def test_every_figure_carries_rows_and_events_together():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    assert "TIMESTAMP_ROWS" in q and "INDEPENDENT_EVENTS" in q


def test_every_check_reports_observed_beside_the_frozen_limit():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    for c in q["QUALITY_GATE_CHECKS"]:
        assert "OBSERVED" in c and "FROZEN_LIMIT" in c and "PASS" in c
    names = {c["CHECK"] for c in q["QUALITY_GATE_CHECKS"]}
    for k in CQ.FROZEN_QUALITY_THRESHOLDS:
        if k in ("COMPLETE_SERIES_FRACTION", "MIN_OBSERVATIONS_PER_MARKET",
                 "MAX_NON_MONOTONE_STEPS"):
            continue        # consumed inside other checks, not their own row
        assert k in names, k


def test_one_crossed_book_fails_the_gate_at_zero_tolerance():
    rows = _series()
    rows[3]["BID"], rows[3]["ASK"] = 0.60, 0.40
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["CROSSED_BOOK_COUNT"] == 1
    assert q["CAPTURE_QUALITY_GATE"] == "FAIL"
    assert "MAX_CROSSED_BOOK_RATE" in q["QUALITY_GATE_FAILURES"]


def test_an_invalid_price_fails_the_gate():
    rows = _series()
    rows[2]["ASK"] = 1.7
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["INVALID_BOOK_COUNT"] == 1
    assert "MAX_INVALID_BOOK_RATE" in q["QUALITY_GATE_FAILURES"]


def test_missing_sides_are_counted_separately():
    rows = _series()
    rows[0]["BID"] = "NOT_IDENTIFIED"
    rows[1]["ASK"] = None
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["MISSING_BID_COUNT"] == 1
    assert q["MISSING_ASK_COUNT"] == 1


def test_a_duplicate_snapshot_fails_the_gate():
    rows = _series()
    rows.append(dict(rows[0]))
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["DUPLICATE_SNAPSHOT_COUNT"] == 1
    assert "MAX_DUPLICATE_SNAPSHOTS" in q["QUALITY_GATE_FAILURES"]


def test_a_backwards_clock_is_caught():
    rows = _series()
    rows[18]["RECEIPT_UTC"] = "2026-09-17T17:00:00+00:00"
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["CLOCK_MONOTONICITY_STATUS"] == "NON_MONOTONE"
    assert "CLOCK_MONOTONICITY_REQUIRED" in q["QUALITY_GATE_FAILURES"]


def test_low_coverage_fails_the_gate():
    q = CQ.quality(_series(60), [], planned_polls=PLANNED)
    assert q["OBSERVATION_COVERAGE_PCT"] < \
        CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"]
    assert "MIN_OBSERVATION_COVERAGE_PCT" in q["QUALITY_GATE_FAILURES"]


def test_the_429_rate_is_a_rate_not_a_count():
    """2% of attempts is the frozen limit, so scale matters, not the raw n."""
    clean = _series()
    few = [{"kind": "TICK_ERROR", "status": 429}] * 10      # ~0.7%
    q = CQ.quality(clean, few, planned_polls=len(clean) + 10)
    assert "MAX_HTTP_429_RATE" not in q["QUALITY_GATE_FAILURES"]
    many = [{"kind": "TICK_ERROR", "status": 429}] * 200    # ~12.9%
    q2 = CQ.quality(clean, many, planned_polls=len(clean) + 200)
    assert "MAX_HTTP_429_RATE" in q2["QUALITY_GATE_FAILURES"]


def test_a_single_parse_failure_is_nearly_intolerable():
    clean = _series()
    errs = [{"kind": "TICK_ERROR", "error": "json decode failure"}] * 12
    q = CQ.quality(clean, errs, planned_polls=len(clean) + 12)
    assert "MAX_PARSE_FAILURE_RATE" in q["QUALITY_GATE_FAILURES"]


def test_failures_are_named_by_kind_not_pooled():
    errs = [{"kind": "TICK_ERROR", "status": 429},
            {"kind": "TICK_ERROR", "status": 429},
            {"kind": "TICK_ERROR", "status": 500},
            {"kind": "TICK_ERROR", "error": "Read timed out"},
            {"kind": "TICK_ERROR", "error": "json decode failure"}]
    q = CQ.quality(_series(), errs, planned_polls=PLANNED + 5)
    assert q["HTTP_429"] == 2
    assert q["OTHER_HTTP_FAILURES"] == 1
    assert q["TIMEOUTS"] == 1
    assert q["PARSE_FAILURES"] == 1


def test_attempted_counts_errors_too():
    q = CQ.quality(_series(), [{"kind": "TICK_ERROR", "status": 429}] * 3,
                   planned_polls=PLANNED + 3)
    assert q["SUCCESSFUL_POLLS"] == PLANNED
    assert q["ATTEMPTED_POLLS"] == PLANNED + 3


def test_a_five_minute_hole_fails_the_frozen_maximum_gap():
    rows = _series()
    rows[-1]["RECEIPT_UTC"] = "2026-09-17T21:00:00+00:00"
    q = CQ.quality(rows, [], planned_polls=PLANNED, nominal_revisit_s=24.0)
    assert q["MAX_OBSERVATION_GAP"] > \
        CQ.FROZEN_QUALITY_THRESHOLDS["MAX_MAXIMUM_OBSERVATION_GAP_S"]
    assert "MAX_MAXIMUM_OBSERVATION_GAP_S" in q["QUALITY_GATE_FAILURES"]


def test_one_market_may_halt_but_an_event_may_not_be_lost():
    """5 of 6 markets is the frozen floor; all 3 events is also the floor."""
    rows = [r for r in _series()
            if not (r["MARKET_SLUG"] == "m2" and r["seq"] > 5)]
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["MARKETS_WITH_SUFFICIENT_SERIES"] == 5
    assert q["EVENTS_WITH_SUFFICIENT_SERIES"] == 3   # m1 still carries ev1
    assert "MIN_MARKETS_WITH_SUFFICIENT_SERIES" not in \
        q["QUALITY_GATE_FAILURES"]


def test_losing_a_whole_event_fails_even_though_four_markets_survive():
    rows = [r for r in _series()
            if r["MARKET_SLUG"] not in ("m5", "m6")]     # both of ev3
    q = CQ.quality(rows, [], planned_polls=PLANNED)
    assert q["EVENTS_WITH_SUFFICIENT_SERIES"] == 2
    assert "MIN_EVENTS_WITH_SUFFICIENT_SERIES" in q["QUALITY_GATE_FAILURES"]


def test_the_thresholds_are_predeclared():
    assert "not a gate" in CQ.THRESHOLDS_ARE_PREDECLARED


# --- The frozen threshold block. -------------------------------------------

def test_the_thresholds_are_frozen_and_hashed():
    b = CQ.thresholds_block()
    assert b["FROZEN_QUALITY_THRESHOLDS_STATUS"] == "FROZEN_AND_HASHED"
    assert b["FROZEN_BEFORE_ANY_CAPTURE_DATA"] is True
    assert len(b["FROZEN_QUALITY_THRESHOLDS_SHA"]) == 64


def test_every_named_threshold_is_present_by_value():
    T = CQ.FROZEN_QUALITY_THRESHOLDS
    for k in ("MIN_OBSERVATION_COVERAGE_PCT", "MAX_HTTP_429_RATE",
              "MAX_OTHER_FAILURE_RATE", "MAX_PARSE_FAILURE_RATE",
              "MAX_INVALID_BOOK_RATE", "MAX_CROSSED_BOOK_RATE",
              "MAX_MEDIAN_OBSERVATION_GAP_S", "MAX_P90_OBSERVATION_GAP_S",
              "MAX_MAXIMUM_OBSERVATION_GAP_S",
              "MIN_MARKETS_WITH_SUFFICIENT_SERIES",
              "CLOCK_MONOTONICITY_REQUIRED"):
        assert k in T, k
        assert not isinstance(T[k], str)        # by value, never a reference


def test_editing_a_threshold_is_detected_and_fails_the_gate():
    original = CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"]
    try:
        CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"] = 1.0
        assert CQ.thresholds_intact()["INTACT"] is False
        q = CQ.quality(_series(60), [], planned_polls=PLANNED)
        assert "FROZEN_THRESHOLDS_EDITED_AFTER_HASHING" in \
            q["QUALITY_GATE_FAILURES"]
    finally:
        CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"] = original
    assert CQ.thresholds_intact()["INTACT"] is True


def test_loosening_a_threshold_cannot_rescue_a_failed_capture():
    """The escape hatch this closes: fail, then lower the bar, then pass."""
    rows = _series(60)
    before = CQ.quality(rows, [], planned_polls=PLANNED)
    assert before["CAPTURE_QUALITY_GATE"] == "FAIL"
    original = CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"]
    try:
        CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"] = 1.0
        after = CQ.quality(rows, [], planned_polls=PLANNED)
        assert after["CAPTURE_QUALITY_GATE"] == "FAIL"
    finally:
        CQ.FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"] = original


def test_a_revision_applies_only_to_the_next_experiment():
    assert CQ.NEXT_EXPERIMENT_THRESHOLD_REVISIONS == ()
    assert CQ.REVISIONS_DO_NOT_APPLY_TO_THE_CURRENT_CAPTURE is True
    assert "ONLY to the NEXT" in CQ.THRESHOLD_CHANGE_RULE
    assert "choosing the answer" in CQ.THRESHOLD_CHANGE_RULE


def test_the_gate_says_which_rule_it_evaluated_under():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    assert q["EVALUATED_UNDER"] == "THE_FROZEN_RULE"
    assert q["FROZEN_QUALITY_THRESHOLDS_SHA"] == \
        CQ.FROZEN_QUALITY_THRESHOLDS_SHA


def test_the_report_carries_the_thresholds_by_value():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    assert q["FROZEN_QUALITY_THRESHOLDS"] == CQ.FROZEN_QUALITY_THRESHOLDS
    assert q["FROZEN_QUALITY_THRESHOLDS_STATUS"] == "FROZEN_AND_HASHED"


# --- Section 14. Version advances. -----------------------------------------

def test_no_venue_version_means_not_identified_never_zero():
    v = CQ.version_advance(_series(5))
    assert v["VENUE_VERSION_AVAILABLE"] is False
    assert v["POTENTIAL_UNOBSERVED_TRANSITIONS"] == "NOT_IDENTIFIED"
    assert "not a count of zero" in v["NO_VERSION_MEANS_UNKNOWN"]


def test_a_venue_advance_with_identical_state_is_an_unobserved_transition():
    rows = [{"MARKET_SLUG": "m1", "seq": 0, "VERSION_ID": 1,
             "BID": 0.4, "ASK": 0.42},
            {"MARKET_SLUG": "m1", "seq": 1, "VERSION_ID": 2,
             "BID": 0.4, "ASK": 0.42},
            {"MARKET_SLUG": "m1", "seq": 2, "VERSION_ID": 3,
             "BID": 0.41, "ASK": 0.42}]
    v = CQ.version_advance(rows)
    assert v["VENUE_VERSION_ADVANCES"] == 2
    assert v["BETTOR_OBSERVED_DECISION_STATE_CHANGES"] == 1
    assert v["VENUE_CHANGES_WITH_IDENTICAL_CAPTURED_STATE"] == 1
    assert v["POTENTIAL_UNOBSERVED_TRANSITIONS"] == 1


def test_a_state_change_with_no_venue_advance_is_reported_too():
    rows = [{"MARKET_SLUG": "m1", "seq": 0, "VERSION_ID": 7,
             "BID": 0.4, "ASK": 0.42},
            {"MARKET_SLUG": "m1", "seq": 1, "VERSION_ID": 7,
             "BID": 0.5, "ASK": 0.52}]
    v = CQ.version_advance(rows)
    assert v["OBSERVED_STATE_CHANGES_WITHOUT_VENUE_CHANGE"] == 1


# --- Section 20. The decision. ---------------------------------------------

def test_a_failed_quality_gate_forces_repair():
    q = CQ.quality(_series(60), [], planned_polls=PLANNED)
    d = CQ.harvest_decision(q)
    assert d["HARVEST_DECISION"] == "REPAIR_CAPTURE_AND_REPEAT"


def test_a_clean_capture_with_findings_goes_larger():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    d = CQ.harvest_decision(q, signal_findings=["microprice at 30s"])
    assert d["HARVEST_DECISION"] == "GO_TO_LARGER_PROSPECTIVE_CAPTURE"


def test_stop_is_refused_on_a_three_event_pilot():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    d = CQ.harvest_decision(q, structural_negative=True,
                            independent_events=3)
    assert d["HARVEST_DECISION"] == "GO_TO_LARGER_PROSPECTIVE_CAPTURE"
    assert d["STOP_REFUSED_FOR_INSUFFICIENT_EVENTS"] is True


def test_stop_needs_both_a_structural_negative_and_enough_events():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    d = CQ.harvest_decision(q, structural_negative=True,
                            independent_events=200)
    assert d["HARVEST_DECISION"] == "STOP_THIS_MICROSTRUCTURE_PATH"


def test_exactly_one_of_three_decisions_is_returned():
    q = CQ.quality(_series(), [], planned_polls=PLANNED)
    for kw in ({}, {"structural_negative": True},
               {"independent_events": 3}):
        d = CQ.harvest_decision(q, **kw)
        assert d["HARVEST_DECISION"] in CQ.DECISIONS


def test_an_underpowered_null_is_not_a_falsification():
    assert "not an underpowered null" in CQ.STOP_REQUIRES_A_STRUCTURAL_NEGATIVE


def test_an_unknown_plan_does_not_become_100_percent_coverage():
    """A missing denominator must not pass the gate's main threshold.

    Defaulting PLANNED_POLLS to ATTEMPTED_POLLS would make coverage 100% by
    construction on a capture nobody planned.
    """
    q = CQ.quality(_series(), [], planned_polls=None)
    assert q["PLANNED_POLLS"] == "NOT_IDENTIFIED"
    assert q["OBSERVATION_COVERAGE_PCT"] == "NOT_IDENTIFIED"
    assert q["CAPTURE_QUALITY_GATE"] == "FAIL"
    assert "MIN_OBSERVATION_COVERAGE_PCT" in q["QUALITY_GATE_FAILURES"]
    row = [c for c in q["QUALITY_GATE_CHECKS"]
           if c["CHECK"] == "MIN_OBSERVATION_COVERAGE_PCT"][0]
    assert "PLANNED_POLLS_NOT_IDENTIFIED" in row["NOTE"]


def test_an_unknown_plan_forces_repair_not_go():
    q = CQ.quality(_series(), [], planned_polls=None)
    d = CQ.harvest_decision(q, signal_findings=["looked promising"])
    assert d["HARVEST_DECISION"] == "REPAIR_CAPTURE_AND_REPEAT"


# --- Sections 1, 16, 17. The register's freezes. ---------------------------

def test_the_historical_pilot_is_available_but_not_priority():
    import ev_core_registers as R
    assert R.HISTORICAL_STATIC_PILOT == "AVAILABLE_NOT_PRIORITY"
    assert R.PRIMARY_EXTERNAL_ODDS_EXPERIMENT == \
        "PROSPECTIVE_CAPTURE_ALIGNED_BACKFILL"


def test_the_register_keeps_the_bound_from_travelling():
    import ev_core_registers as R
    b = R.TRANSITION_BOUND_RULE
    assert b["COUNT"] == "LOWER_BOUND_ON_TRUE_TRANSITIONS"
    assert b["FREQUENCY_PER_OBSERVED_TRANSITION"] == "NO_BOUND"


def test_v2_is_not_dispatched_automatically_on_v1_completion():
    import ev_core_registers as R
    assert "NOT automatic" in R.CAPTURE_V2_DISPATCH_RULE
    assert "TARGET_EVENT_N" in R.CAPTURE_V2_DISPATCH_RULE


def test_v2_must_raise_events_not_only_markets():
    import ev_core_registers as R
    assert "one is never traded for the other" in R.CAPTURE_V2_DISPATCH_RULE


# --- The thresholds travel into the manifest and the spec record. ----------

def test_the_manifest_carries_the_thresholds_by_value():
    m = _manifest()
    assert m["FROZEN_QUALITY_THRESHOLDS"] == CQ.FROZEN_QUALITY_THRESHOLDS
    assert m["FROZEN_QUALITY_THRESHOLDS_SHA"] == \
        CQ.FROZEN_QUALITY_THRESHOLDS_SHA
    assert m["MANIFEST_COMPLETE"] is True


def test_a_loosened_threshold_changes_the_manifest_hash():
    a = _manifest()
    loosened = dict(CQ.FROZEN_QUALITY_THRESHOLDS)
    loosened["MIN_OBSERVATION_COVERAGE_PCT"] = 1.0
    b = _manifest(frozen_quality_thresholds=loosened)
    assert a["MANIFEST_SHA"] != b["MANIFEST_SHA"]


def test_a_loosened_threshold_is_caught_as_drift_at_harvest():
    """The failure mode: a failed capture passed by lowering the bar."""
    m = _manifest()
    live = {k: m[k] for k in CM.COMPARED_AT_HARVEST}
    loosened = dict(CQ.FROZEN_QUALITY_THRESHOLDS)
    loosened["MAX_CROSSED_BOOK_RATE"] = 0.5
    live["FROZEN_QUALITY_THRESHOLDS"] = loosened
    v = CM.verify_against(m, live)
    assert v["STATUS"] == "DRIFTED"
    assert v["HARVEST_MAY_PROCEED"] is False
    assert "FROZEN_QUALITY_THRESHOLDS" in v["DRIFTED_FIELDS"]


def test_threshold_drift_is_named_as_the_worst_kind():
    assert "whether the measurement was allowed to count" in \
        CM.THRESHOLD_DRIFT_IS_THE_WORST_KIND


def test_the_capture_spec_record_binds_parameters_to_the_frozen_gate():
    import substantive_capture as SC
    spec = CM.capture_spec_record(
        {"RATE_RPS": SC.RATE_RPS, "REQUEST_INTERVAL_S": SC.REQUEST_INTERVAL_S,
         "CAPTURE_SECONDS": SC.CAPTURE_SECONDS, "EVENTS": SC.EVENTS,
         "MARKETS": SC.MARKETS, "NOMINAL_REVISIT_S": SC.NOMINAL_REVISIT_S},
        CQ.FROZEN_QUALITY_THRESHOLDS, CQ.FROZEN_QUALITY_THRESHOLDS_SHA)
    assert spec["FROZEN_QUALITY_THRESHOLDS"] == CQ.FROZEN_QUALITY_THRESHOLDS
    assert len(spec["CAPTURE_SPEC_RECORD_SHA"]) == 64
    assert spec["THRESHOLDS_FROZEN_BEFORE_ANY_CAPTURE_DATA"] is True


def test_the_spec_record_explains_why_the_capture_source_is_untouched():
    spec = CM.capture_spec_record({}, CQ.FROZEN_QUALITY_THRESHOLDS,
                                  CQ.FROZEN_QUALITY_THRESHOLDS_SHA)
    assert "pinned code SHA" in spec["WHY_NOT_IN_THE_CAPTURE_SOURCE"]


def test_the_frozen_capture_source_was_not_edited():
    """The thresholds must NOT have been written into the pinned capture."""
    import substantive_capture as SC
    src = open(SC.__file__).read()
    assert "FROZEN_QUALITY_THRESHOLDS" not in src


def test_the_register_records_the_frozen_threshold_sha():
    """The register's copy must match the module, or two artifacts disagree."""
    import ev_core_registers as R
    assert R.FROZEN_QUALITY_THRESHOLDS_SHA == CQ.FROZEN_QUALITY_THRESHOLDS_SHA
    assert R.FROZEN_QUALITY_THRESHOLDS_STATUS == "FROZEN_AND_HASHED"
    assert R.THRESHOLDS_FROZEN_BEFORE_ANY_CAPTURE_DATA is True
    assert "choosing the answer" in R.THRESHOLD_CHANGE_RULE
