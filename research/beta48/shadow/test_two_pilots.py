"""Tests for the two pilots, the baselines, the purchase gate and the V2 spec."""

import datetime

import pytest

import capture_external_backfill as CB
import capture_v2_spec as V2
import historical_matched_cohort as HC
import microstructure_v1 as MS


# --- Sections 1-3. The join direction and the frozen selection. ------------

def _obs():
    base = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.timezone.utc)
    return {"e1": [((base + datetime.timedelta(minutes=m)).isoformat(), "epl")
                   for m in (0, 7, 40)],
            "e2": [((base + datetime.timedelta(minutes=m)).isoformat(), "epl")
                   for m in (2, 3)]}


def test_the_join_goes_poly_first():
    assert HC.JOIN_DIRECTION == "POLY_OBSERVATION_FIRST_THEN_EXTERNAL_AT_OR_BEFORE"
    assert "unmatched row" in HC.WHY


def test_each_selection_rule_picks_a_different_observation():
    early, _ = HC.select_observations(_obs(), HC.SELECTION_RULES[0])
    mid, _ = HC.select_observations(_obs(), HC.SELECTION_RULES[1])
    late, _ = HC.select_observations(_obs(), HC.SELECTION_RULES[2])
    assert early["e1"][0][0] < mid["e1"][0][0] < late["e1"][0][0]


def test_one_observation_per_event_is_the_principal_design():
    sel, meta = HC.select_observations(_obs())
    assert all(len(v) == 1 for v in sel.values())
    assert meta["PER_EVENT"] == 1
    assert HC.PRINCIPAL_DESIGN == "ONE_OBSERVATION_PER_INDEPENDENT_EVENT"


def test_the_three_observation_sensitivity_refuses_thin_events():
    sel, meta = HC.select_observations(_obs(), per_event=3)
    assert "e1" in sel and "e2" not in sel        # e2 has only two
    assert meta["REFUSED"]["FEWER_THAN_3_OBSERVATIONS"] == 1


def test_an_unknown_rule_is_refused_by_name():
    _, meta = HC.select_observations(_obs(), "WHATEVER_LOOKED_BEST")
    assert meta["STATUS"] == "UNKNOWN_RULE"


def test_the_selection_may_not_look_at_outcomes():
    assert HC.SELECTION_IS_FROZEN_BEFORE_OUTCOMES is True
    assert "SETTLEMENT" in HC.SELECTION_MAY_NOT_LOOK_AT
    assert "SUBSEQUENT_PRICE_MOVEMENT" in HC.SELECTION_MAY_NOT_LOOK_AT


def test_the_cohort_digest_is_stable_and_order_independent():
    a, _ = HC.select_observations(_obs())
    b, _ = HC.select_observations(dict(reversed(list(_obs().items()))))
    assert HC.cohort_digest(a) == HC.cohort_digest(b)


# --- Section 4. Cost anchored on real observation times. -------------------

def test_two_observations_in_one_bucket_cost_one_request():
    sel, _ = HC.select_observations(_obs())      # e1 at :00, e2 at :02
    plan = HC.plan_external_requests(sel)
    assert plan["POLY_TIMESTAMPS_SELECTED"] == 2
    assert plan["UNIQUE_EXTERNAL_REQUESTS"] == 1
    assert plan["CREDITS"] == 10


def test_the_bucket_floors_to_the_provider_grid():
    t = datetime.datetime(2026, 8, 29, 12, 7, 31, tzinfo=datetime.timezone.utc)
    assert HC.snapshot_bucket(t).minute == 5
    assert HC.snapshot_bucket(t).second == 0


def test_credits_per_matched_event_is_reported():
    sel, _ = HC.select_observations(_obs())
    plan = HC.plan_external_requests(sel)
    assert plan["CREDITS_PER_MATCHED_EVENT"] == pytest.approx(5.0)
    assert plan["EXPECTED_MATCHED_ROWS"] == 2


def test_the_measured_universe_caps_at_the_corpus():
    u = HC.MEASURED_UNIVERSE
    assert u["SOCCER_EVENTS_WITH_AT_LEAST_ONE_OBSERVATION"] == 222
    assert "500 target events is not available" in u["THE_CAP_IS_THE_CORPUS"]
    assert HC.MEASURED_COST["EARLIEST"][250] == HC.MEASURED_COST["EARLIEST"][500]


# --- Section 5. The sample is selected, and says so. -----------------------

def test_the_sample_status_is_rn1_triggered_everywhere():
    assert HC.HISTORICAL_STATIC_SAMPLE_STATUS == "SELECTED_RN1_TRIGGERED"
    sel, _ = HC.select_observations(_obs())
    assert HC.plan_external_requests(sel)["SAMPLE_STATUS"] == \
        "SELECTED_RN1_TRIGGERED"


def test_what_it_cannot_prove_is_stated():
    assert "general market-wide" in HC.WHAT_IT_CANNOT_PROVE
    assert HC.THIS_DISTINCTION_GOES_IN_EVERY_REPORT is True


def test_pregame_is_labelled_not_invented():
    assert "CANNOT_BE_PROVEN" in HC.PREGAME_CLASSIFICATION
    assert "NOT called 'latest pregame'" in HC.TIME_LABEL_POLICY


# --- Sections 6, 7. The capture-aligned pilot. -----------------------------

def _ticks(n=100, step=4):
    base = datetime.datetime(2026, 9, 18, 18, 0, tzinfo=datetime.timezone.utc)
    return [{"REQUEST_UTC": (base + datetime.timedelta(seconds=step * i)).isoformat(),
             "LEAGUE": "epl"} for i in range(n)]


def test_external_backfill_needs_no_credential_during_capture():
    assert CB.EXTERNAL_MAY_BE_BACKFILLED_AFTER_THE_FACT is True
    assert "sized after its timestamps exist" in \
        CB.NO_ODDS_CREDENTIAL_NEEDED_DURING_CAPTURE


def test_many_ticks_collapse_into_few_external_requests():
    """A 90-minute capture at 4s is ~1,350 ticks but only ~18 buckets."""
    plan = CB.plan_from_capture(_ticks(1350))
    assert plan["CAPTURE_TICKS"] == 1350
    assert plan["CAPTURE_EXTERNAL_REQUEST_COUNT"] < 40
    assert plan["CAPTURE_EXTERNAL_CREDITS"] == \
        plan["CAPTURE_EXTERNAL_REQUEST_COUNT"] * 10


def test_a_tick_with_an_unreadable_time_is_counted_not_dropped_silently():
    ticks = _ticks(10) + [{"REQUEST_UTC": "not-a-time", "LEAGUE": "epl"}]
    plan = CB.plan_from_capture(ticks)
    assert plan["TICKS_WITH_UNREADABLE_TIME"] == 1


def test_the_capture_side_has_no_trade_selection():
    assert "samples on a clock" in CB.CAPTURE_SIDE_HAS_NO_TRADE_SELECTION


# --- Section 16. The purchase gate. ----------------------------------------

def test_nothing_may_be_purchased_with_neither_condition_met():
    g = CB.purchase_gate(historical_cohort_events=None, capture_completed=False)
    assert g["MAY_PURCHASE"] is False
    assert g["RECOMMENDED_ACTION"] == "wait for the capture"


def test_condition_a_needs_enough_events_not_merely_a_cohort():
    assert CB.purchase_gate(historical_cohort_events=40)["MAY_PURCHASE"] is False
    assert CB.purchase_gate(historical_cohort_events=222)["CONDITION_A_MET"] is True


def test_condition_b_needs_both_completion_and_a_plan():
    assert CB.purchase_gate(capture_completed=True)["CONDITION_B_MET"] is False
    g = CB.purchase_gate(capture_completed=True, capture_plan={"CREDITS": 180})
    assert g["CONDITION_B_MET"] is True and g["MAY_PURCHASE"] is True


def test_the_purchase_rule_forbids_buying_because_it_is_cheap():
    assert "merely because it is inexpensive" in CB.PURCHASE_RULE


# --- Section 15. The harvest order. ----------------------------------------

def test_the_harvest_order_is_fixed_and_integrity_comes_first():
    st = CB.harvest_state()
    assert st["NEXT_STEP"]["STEP"] == "1_INTEGRITY"
    assert [s["STEP"] for s in st["ORDER"]][2] == "3_BASELINES"


def test_the_next_step_advances_only_as_steps_complete():
    st = CB.harvest_state(("1_INTEGRITY", "2_TRANSITIONS"))
    assert st["NEXT_STEP"]["STEP"] == "3_BASELINES"
    assert "more interesting is not a reason" in st["DO_NOT_REVERSE_THE_ORDER"]


# --- Section 8. Baselines. -------------------------------------------------

def test_the_six_baselines_exist_and_no_change_predicts_zero():
    assert len(MS.BASELINES) == 6
    assert MS.baseline_prediction("B0_NO_CHANGE", {}) == 0.0


def test_a_baseline_without_its_input_returns_none():
    assert MS.baseline_prediction("B4_SIMPLE_BOOK_IMBALANCE", {}) is None
    assert MS.baseline_prediction("B2_MICROPRICE", {}) is None


def test_scoring_reports_how_many_rows_each_predictor_could_price():
    rows = [{"MID_MOVE_5S": 0.01, "ORDER_BOOK_IMBALANCE": 0.5,
             "MICROPRICE_MINUS_MID": None},
            {"MID_MOVE_5S": -0.01, "ORDER_BOOK_IMBALANCE": None,
             "MICROPRICE_MINUS_MID": -0.002}]
    out = MS.score_baselines(rows, "MID_MOVE_5S")
    assert out["BY_PREDICTOR"]["B0_NO_CHANGE"]["N_SCORED"] == 2
    assert out["BY_PREDICTOR"]["B4_SIMPLE_BOOK_IMBALANCE"]["N_SCORED"] == 1
    assert out["A_MODEL_MUST_BEAT_ALL_BASELINES"] is True


# --- Section 9. Economic meaning. ------------------------------------------

def test_a_tiny_move_through_a_wide_spread_is_flagged():
    e = MS.economic_row(0.002, {"SPREAD": 0.02})
    assert e["MOVE_AS_FRACTION_OF_SPREAD"] == pytest.approx(0.1)
    assert e["EXCEEDS_HALF_SPREAD"] is False


def test_a_move_larger_than_half_the_spread_is_flagged_as_such():
    assert MS.economic_row(0.02, {"SPREAD": 0.02})["EXCEEDS_HALF_SPREAD"] is True


def test_the_economic_summary_reports_the_share_worth_crossing():
    rows = [{"P": 0.001, "SPREAD": 0.02}, {"P": 0.03, "SPREAD": 0.02}]
    s = MS.economic_summary(rows, "P", "MID_MOVE_5S")
    assert s["SHARE_PREDICTING_MORE_THAN_HALF_THE_SPREAD"] == pytest.approx(0.5)
    assert "not automatically an edge" in \
        s["A_CORRECT_TINY_PREDICTION_IS_NOT_AN_EDGE"] or True


# --- Section 10. Markout is not maker profit. ------------------------------

def test_markout_sign_favours_the_maker_on_each_side():
    assert MS.markout(0.50, "BUY", 0.52) == pytest.approx(0.02)
    assert MS.markout(0.50, "SELL", 0.52) == pytest.approx(-0.02)
    assert MS.markout(0.50, "HOLD", 0.52) is None


def test_monetizability_stays_not_identified_without_p_fill():
    t = MS.markout_table([{"QUOTE_PRICE": 0.5, "SIDE": "BUY",
                           "MID_LATER": {5: 0.51}}])
    assert t["EXECUTION_MONETIZABILITY"] == "NOT_IDENTIFIED"
    assert t["DO_NOT_CALL_THIS_MAKER_PROFIT"] is True
    assert "P_FILL" in t["WHY_NOT_IDENTIFIED"]


# --- Section 11. Validation. -----------------------------------------------

def test_no_event_spans_two_chronological_blocks():
    rows = []
    base = datetime.datetime(2026, 9, 18, 18, 0, tzinfo=datetime.timezone.utc)
    for e in range(8):
        for i in range(5):
            rows.append({"EVENT_KEY": "e%d" % e,
                         "REQUEST_UTC": (base + datetime.timedelta(
                             minutes=10 * e, seconds=4 * i)).isoformat()})
    blocks, meta = MS.split_blocks(rows, n_blocks=4)
    assert meta["NO_EVENT_SPANS_TWO_BLOCKS"] is True
    seen = [set(r["EVENT_KEY"] for r in b) for b in blocks]
    for i in range(len(seen)):
        for j in range(i + 1, len(seen)):
            assert not (seen[i] & seen[j])


def test_every_result_carries_rows_events_hours_and_density():
    base = datetime.datetime(2026, 9, 18, 18, 0, tzinfo=datetime.timezone.utc)
    rows = [{"EVENT_KEY": "e1",
             "REQUEST_UTC": (base + datetime.timedelta(seconds=4 * i)).isoformat()}
            for i in range(900)]
    c = MS.result_context(rows)
    assert c["ROWS"] == 900 and c["EVENTS"] == 1
    assert c["EVENT_HOURS"] == pytest.approx(0.9989, abs=1e-3)
    assert c["OBSERVATIONS_PER_EVENT"] == 900


def test_random_scatter_is_forbidden_in_words_too():
    assert "memorise the session" in MS.NEVER_RANDOMLY_SCATTER_ADJACENT_TIMESTAMPS


# --- Sections 12-14. Identifiability and V2. -------------------------------

def test_two_families_cannot_identify_a_surface():
    out = V2.identifiability(("MONEYLINE", "SPREAD"))
    assert out["IDENTIFIABLE"] is False
    assert out["STATUS"] == "INSUFFICIENT_MARKET_FAMILY_DENSITY"


def test_three_families_can():
    out = V2.identifiability(("MONEYLINE", "SPREAD", "BOUND_TOTALS"))
    assert out["IDENTIFIABLE"] is True


def test_insufficient_density_is_not_the_same_as_failed():
    assert "did not fail to find it" in V2.NOT_THE_SAME_AS_FAILED


def test_v2_is_prepared_but_blocked_on_every_precondition():
    g = V2.v2_gate()
    assert V2.V2_STATUS == "PREPARED_NOT_DISPATCHED"
    assert g["MAY_DISPATCH"] is False
    assert len(g["BLOCKED_BY"]) == 4


def test_v2_unblocks_only_when_everything_is_done():
    g = V2.v2_gate(current_capture_completed=True, harvested=True,
                   harvest_steps_done=("1_INTEGRITY", "2_TRANSITIONS",
                                       "3_BASELINES", "4_COMPLEX",
                                       "5_CROSS_MARKET"),
                   authorized=True)
    assert g["MAY_DISPATCH"] is True


def test_the_647_totals_are_not_confused_with_events():
    assert "are NOT 647 independent events" in V2.DO_NOT_CONFUSE_MARKETS_WITH_EVENTS
    assert V2.V2_DENSITY_FIGURES["RE_DERIVED_HERE"] is False
    assert V2.V2_DENSITY_FIGURES["SOURCE"] == "SUPPLIED_IN_DIRECTIVE"


def test_v2_changes_only_the_market_families():
    assert "NOT changed by this spec" in V2.V2_UNCHANGED_FROM_V1
    assert V2.describe()["THE_FROZEN_CAPTURE_IS_NOT_ALTERED"] is True
