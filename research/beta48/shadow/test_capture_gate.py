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
              scientific_definitions=_defs())
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

def _series(n=100, markets=("m1", "m2"), event_of=None, start_s=0,
            step_s=24, bid=0.40, ask=0.42):
    base = datetime.datetime(2026, 9, 17, 18, 0,
                             tzinfo=datetime.timezone.utc)
    rows = []
    for seq in range(n):
        for m in markets:
            t = base + datetime.timedelta(seconds=start_s + seq * step_s)
            rows.append({"kind": "TICK", "MARKET_SLUG": m, "seq": seq,
                         "RECEIPT_UTC": t.isoformat(),
                         "EVENT_KEY": (event_of or (lambda s: "ev-" + s))(m),
                         "BID": bid, "ASK": ask})
    return rows


def test_a_clean_capture_passes_the_gate():
    rows = _series(100)
    q = CQ.quality(rows, [], planned_polls=200, nominal_revisit_s=24.0)
    assert q["CAPTURE_QUALITY_GATE"] == "PASS"
    assert q["SIGNAL_ANALYSIS_MAY_PROCEED"] is True
    assert q["TIMESTAMP_ROWS"] == 200
    assert q["INDEPENDENT_EVENTS"] == 2


def test_every_figure_carries_rows_and_events_together():
    q = CQ.quality(_series(10), [], planned_polls=20)
    assert "TIMESTAMP_ROWS" in q and "INDEPENDENT_EVENTS" in q


def test_a_crossed_book_fails_the_gate():
    rows = _series(10)
    rows[3]["BID"], rows[3]["ASK"] = 0.60, 0.40
    q = CQ.quality(rows, [], planned_polls=20)
    assert q["CROSSED_BOOK_COUNT"] == 1
    assert q["CAPTURE_QUALITY_GATE"] == "FAIL"
    assert "CROSSED_BOOKS_PRESENT" in q["QUALITY_GATE_FAILURES"]


def test_an_invalid_price_fails_the_gate():
    rows = _series(10)
    rows[2]["ASK"] = 1.7
    q = CQ.quality(rows, [], planned_polls=20)
    assert q["INVALID_BOOK_COUNT"] == 1
    assert "INVALID_BOOKS" in q["QUALITY_GATE_FAILURES"]


def test_missing_sides_are_counted_separately():
    rows = _series(5)
    rows[0]["BID"] = "NOT_IDENTIFIED"
    rows[1]["ASK"] = None
    q = CQ.quality(rows, [], planned_polls=10)
    assert q["MISSING_BID_COUNT"] == 1
    assert q["MISSING_ASK_COUNT"] == 1


def test_a_duplicate_snapshot_fails_the_gate():
    rows = _series(5)
    rows.append(dict(rows[0]))
    q = CQ.quality(rows, [], planned_polls=10)
    assert q["DUPLICATE_SNAPSHOT_COUNT"] == 1
    assert "DUPLICATE_SNAPSHOTS" in q["QUALITY_GATE_FAILURES"]


def test_a_backwards_clock_is_caught():
    rows = _series(5, markets=("m1",))
    rows[3]["RECEIPT_UTC"] = "2026-09-17T17:00:00+00:00"
    q = CQ.quality(rows, [], planned_polls=5)
    assert q["CLOCK_MONOTONICITY_STATUS"] == "NON_MONOTONE"
    assert "CLOCK_NOT_MONOTONE" in q["QUALITY_GATE_FAILURES"]


def test_low_coverage_fails_the_gate():
    q = CQ.quality(_series(10), [], planned_polls=100)
    assert q["OBSERVATION_COVERAGE_PCT"] < CQ.COVERAGE_PCT_FLOOR
    assert q["CAPTURE_QUALITY_GATE"] == "FAIL"


def test_failures_are_named_by_kind_not_pooled():
    errs = [{"kind": "TICK_ERROR", "status": 429},
            {"kind": "TICK_ERROR", "status": 429},
            {"kind": "TICK_ERROR", "status": 500},
            {"kind": "TICK_ERROR", "error": "Read timed out"},
            {"kind": "TICK_ERROR", "error": "json decode failure"}]
    q = CQ.quality(_series(10), errs, planned_polls=25)
    assert q["HTTP_429"] == 2
    assert q["OTHER_HTTP_FAILURES"] == 1
    assert q["TIMEOUTS"] == 1
    assert q["PARSE_FAILURES"] == 1


def test_attempted_counts_errors_too():
    q = CQ.quality(_series(5), [{"kind": "TICK_ERROR", "status": 429}] * 3,
                   planned_polls=13)
    assert q["SUCCESSFUL_POLLS"] == 10
    assert q["ATTEMPTED_POLLS"] == 13


def test_a_big_hole_is_reported_and_fails_the_gate():
    rows = _series(5, markets=("m1",), step_s=24)
    rows[-1]["RECEIPT_UTC"] = "2026-09-17T19:00:00+00:00"   # a long jump
    q = CQ.quality(rows, [], planned_polls=5, nominal_revisit_s=24.0)
    assert q["MAX_OBSERVATION_GAP"] > 4 * 24
    assert any(f.startswith("MAX_GAP_EXCEEDS")
               for f in q["QUALITY_GATE_FAILURES"])


def test_complete_and_partial_series_are_separated():
    rows = _series(50, markets=("m1", "m2"))
    rows = [r for r in rows if not (r["MARKET_SLUG"] == "m2"
                                    and r["seq"] > 5)]
    q = CQ.quality(rows, [], planned_polls=100)
    assert q["MARKETS_WITH_COMPLETE_SERIES"] == 1
    assert q["MARKETS_WITH_PARTIAL_SERIES"] == 1


def test_the_thresholds_are_predeclared():
    assert "not a gate" in CQ.THRESHOLDS_ARE_PREDECLARED


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
    q = CQ.quality(_series(10), [], planned_polls=100)
    d = CQ.harvest_decision(q)
    assert d["HARVEST_DECISION"] == "REPAIR_CAPTURE_AND_REPEAT"


def test_a_clean_capture_with_findings_goes_larger():
    q = CQ.quality(_series(100), [], planned_polls=200)
    d = CQ.harvest_decision(q, signal_findings=["microprice at 30s"])
    assert d["HARVEST_DECISION"] == "GO_TO_LARGER_PROSPECTIVE_CAPTURE"


def test_stop_is_refused_on_a_three_event_pilot():
    q = CQ.quality(_series(100), [], planned_polls=200)
    d = CQ.harvest_decision(q, structural_negative=True,
                            independent_events=3)
    assert d["HARVEST_DECISION"] == "GO_TO_LARGER_PROSPECTIVE_CAPTURE"
    assert d["STOP_REFUSED_FOR_INSUFFICIENT_EVENTS"] is True


def test_stop_needs_both_a_structural_negative_and_enough_events():
    q = CQ.quality(_series(100), [], planned_polls=200)
    d = CQ.harvest_decision(q, structural_negative=True,
                            independent_events=200)
    assert d["HARVEST_DECISION"] == "STOP_THIS_MICROSTRUCTURE_PATH"


def test_exactly_one_of_three_decisions_is_returned():
    q = CQ.quality(_series(100), [], planned_polls=200)
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
    q = CQ.quality(_series(10), [], planned_polls=None)
    assert q["PLANNED_POLLS"] == "NOT_IDENTIFIED"
    assert q["OBSERVATION_COVERAGE_PCT"] == "NOT_IDENTIFIED"
    assert q["CAPTURE_QUALITY_GATE"] == "FAIL"
    assert "PLANNED_POLLS_NOT_IDENTIFIED" in q["QUALITY_GATE_FAILURES"]


def test_an_unknown_plan_forces_repair_not_go():
    q = CQ.quality(_series(10), [], planned_polls=None)
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
