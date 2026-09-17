"""Tests for the proprietary dataset stack: schema, labels, splits, gates."""

import datetime
from decimal import Decimal

import pytest

import action_ev as AE
import bettor_dataset as BD
import capture_v2_spec as V2
import confidence_gates as CG
import event_splits as ES
import external_alignment as EA
import fill_dataset as FD
import inventory_state as IS
import market_state_toxicity as MT


def _t(s, base_min=0):
    return (datetime.datetime(2026, 9, 17, 18, base_min,
                              tzinfo=datetime.timezone.utc)
            + datetime.timedelta(seconds=s)).isoformat()


def _book(bid=0.40, ask=0.42, bs=100, asz=300):
    return {"BEST_BID": bid, "BEST_ASK": ask,
            "BEST_BID_SIZE": bs, "BEST_ASK_SIZE": asz}


# --- Sections 1, 2. The dataset schema. ------------------------------------

def test_the_grain_is_market_x_timestamp_x_action():
    assert BD.GRAIN == "MARKET x DECISION_TIMESTAMP x CANDIDATE_ACTION"


def test_the_decision_id_is_deterministic():
    a = BD.decision_id("m1", _t(0), "POST_BID")
    b = BD.decision_id("m1", _t(0), "POST_BID")
    c = BD.decision_id("m1", _t(0), "POST_ASK")
    assert a == b and a != c


def test_derived_fields_are_computed_not_accepted():
    """A caller cannot supply a mid that disagrees with its own touch."""
    st = BD.native_state(dict(_book(), MID=0.99, SPREAD=0.99))
    assert st["MID"] == 0.41
    assert st["SPREAD"] == pytest.approx(0.02)


def test_the_microprice_weights_by_the_opposite_size():
    """Big ask size pushes the microprice DOWN toward the bid."""
    st = BD.native_state(_book(bs=100, asz=300))
    assert st["MICROPRICE"] < st["MID"]
    st2 = BD.native_state(_book(bs=300, asz=100))
    assert st2["MICROPRICE"] > st2["MID"]


def test_l1_imbalance_signs_with_the_bid():
    assert BD.native_state(_book(bs=300, asz=100))["BOOK_IMBALANCE_L1"] > 0
    assert BD.native_state(_book(bs=100, asz=300))["BOOK_IMBALANCE_L1"] < 0


def test_absent_depth_is_missing_not_zero():
    st = BD.native_state(_book())
    for f in ("DEPTH_L2", "DEPTH_L3", "DEPTH_L5"):
        assert st[f] == "MISSING"
    assert "not zero" in BD.DEPTH_IS_NEVER_FABRICATED


def test_a_row_records_which_identifiers_are_missing():
    r = BD.decision_row("ev1", "m1", _t(0), "POST_BID", _book())
    assert "SPORT" in r["MISSING_IDENTIFIERS"]
    assert r["DECISION_ID"]


def test_the_dataset_is_append_only():
    assert BD.APPEND_ONLY is True
    assert "no longer describes the moment" in BD.WHY_APPEND_ONLY


# --- Section 5. Short-horizon labels. --------------------------------------

def _series(n=30, step=24):
    return [BD.decision_row("ev1", "m1", _t(i * step), "POST_BID",
                            _book(bid=0.40 + 0.001 * i,
                                  ask=0.42 + 0.001 * i))
            for i in range(n)]


def test_labels_are_present_where_a_forward_observation_exists():
    s = _series()
    lab = BD.label_row(s[0], s)
    assert lab["LABEL_STATUS"]["30S"] == "PRESENT"
    assert lab["MID_T_PLUS_30S"] is not None


def test_a_label_beyond_the_series_is_missing_not_extrapolated():
    s = _series(n=3)
    lab = BD.label_row(s[-1], s)
    assert lab["MID_T_PLUS_300S"] == "MISSING"
    assert lab["LABEL_STATUS"]["300S"] == "MISSING"


def test_no_interpolation_beyond_the_horizon():
    assert "leaks information from after the target" in \
        BD.NO_INTERPOLATION_BEYOND_THE_HORIZON
    assert "never an interpolation" in BD.NO_INTERPOLATION_BEYOND_THE_HORIZON


def test_a_label_cannot_be_the_origin_row_itself():
    """Without the strictly-after rule every MID_MOVE_5S would be zero."""
    s = _series(n=10, step=24)
    lab = BD.label_row(s[0], s)
    assert lab["MID_T_PLUS_5S"] == "MISSING"      # a 24 s grid cannot label 5 s
    assert lab["MID_MOVE_30S"] != 0


def test_the_realised_offset_is_recorded_on_every_label():
    """A 30 s horizon on a 24 s grid resolves 6 s EARLY, and says so."""
    s = _series(n=30, step=24)
    lab = BD.label_row(s[0], s)
    assert lab["LABEL_REALISED_OFFSET_S"]["30S"] == -6.0
    assert lab["LABEL_REALISED_OFFSET_S"]["5S"] == "MISSING"


def test_the_nearest_observation_wins_not_the_first_after():
    """First-at-or-after would have picked +48 s for a 30 s horizon."""
    s = _series(n=30, step=24)
    lab = BD.label_row(s[0], s)
    assert abs(lab["LABEL_REALISED_OFFSET_S"]["30S"]) <= 12.0


def test_a_gap_wider_than_the_tolerance_is_missing():
    """A forward observation 10 minutes late does not label a 30s horizon."""
    a = BD.decision_row("ev1", "m1", _t(0), "POST_BID", _book())
    b = BD.decision_row("ev1", "m1", _t(600), "POST_BID", _book())
    lab = BD.label_row(a, [a, b])
    assert lab["MID_T_PLUS_30S"] == "MISSING"


def test_the_executable_move_is_not_the_mid_move():
    """Buy at the ask, sell at the bid: the round trip costs the spread."""
    s = _series()
    lab = BD.label_row(s[0], s)
    assert lab["EXECUTABLE_MOVE_30S"] < lab["MID_MOVE_30S"]


def test_the_tolerance_is_declared_not_discovered():
    assert "widening it after seeing" in BD.WHY_A_TOLERANCE


# --- Section 6. Economic move labels. --------------------------------------

def test_a_move_is_expressed_in_spread_units():
    s = _series()
    lab = BD.label_row(s[0], s)
    econ = BD.economic_labels(s[0], lab)
    assert econ["CURRENT_SPREAD"] == pytest.approx(0.02)
    assert isinstance(econ["REALIZED_MOVE_TO_SPREAD_30S"], float)


def test_a_small_move_through_a_wide_spread_is_not_an_edge():
    st = BD.decision_row("ev1", "m1", _t(0), "POST_BID",
                         _book(bid=0.39, ask=0.41))        # 2c spread
    econ = BD.economic_labels(st, {"MID_MOVE_30S": 0.002})  # 0.2c move
    assert econ["REALIZED_MOVE_TO_SPREAD_30S"] == pytest.approx(0.1)
    assert econ["MOVE_EXCEEDS_HALF_SPREAD_30S"] is False


def test_a_big_move_clears_every_threshold():
    st = BD.decision_row("ev1", "m1", _t(0), "POST_BID",
                         _book(bid=0.39, ask=0.41))
    econ = BD.economic_labels(st, {"MID_MOVE_30S": 0.05})
    assert econ["MOVE_EXCEEDS_HALF_SPREAD_30S"] is True
    assert econ["MOVE_EXCEEDS_FULL_SPREAD_30S"] is True
    assert econ["MOVE_EXCEEDS_2X_SPREAD_30S"] is True


def test_a_missing_move_yields_missing_economics():
    st = BD.decision_row("ev1", "m1", _t(0), "POST_BID", _book())
    econ = BD.economic_labels(st, {"MID_MOVE_30S": "MISSING"})
    assert econ["REALIZED_MOVE_TO_SPREAD_30S"] == "MISSING"


# --- Section 3. External alignment and the invariant. ----------------------

def test_a_future_external_snapshot_is_refused():
    v = EA.validate_asof(_t(0), _t(60))
    assert v["OK"] is False
    assert v["REASON"] == "REFUSAL_FUTURE_EXTERNAL_SNAPSHOT"
    assert "hindsight" in v["NO_FUTURE_ODDS"]


def test_a_past_external_snapshot_is_accepted_with_its_age():
    v = EA.validate_asof(_t(60), _t(0))
    assert v["OK"] is True
    assert v["EXTERNAL_AGE_SECONDS"] == 60.0


def test_a_violating_row_is_dropped_and_counted_never_restamped():
    row = BD.decision_row("ev1", "m1", _t(0), "POST_BID", _book())
    blk = EA.align(row, {"SNAPSHOT_TIMESTAMP": _t(60)})
    assert blk["EXTERNAL_ALIGNMENT_STATUS"] == \
        "REFUSAL_FUTURE_EXTERNAL_SNAPSHOT"
    assert blk["EXTERNAL_SNAPSHOT_TIMESTAMP"] == "MISSING"
    assert "never re-stamped" in EA.VIOLATION_IS_A_REFUSAL


def test_no_external_data_is_a_status_not_a_zero():
    row = BD.decision_row("ev1", "m1", _t(0), "POST_BID", _book())
    blk = EA.align(row, None)
    assert blk["EXTERNAL_ALIGNMENT_STATUS"] == "NO_EXTERNAL_DATA"
    assert blk["EXTERNAL_CONSENSUS_RAW"] == "MISSING"


def test_alignment_computes_consensus_dispersion_and_the_gap():
    row = BD.decision_row("ev1", "m1", _t(300), "POST_BID", _book())
    blk = EA.align(row, {"SNAPSHOT_TIMESTAMP": _t(0),
                         "BOOKMAKER_PRICES": [0.44, 0.46, 0.45],
                         "OUTCOME_PRICES": [0.45, 0.60]})
    assert blk["EXTERNAL_ALIGNMENT_STATUS"] == "ALIGNED"
    assert blk["BOOKMAKER_COUNT"] == 3
    assert blk["EXTERNAL_CONSENSUS_RAW"] == pytest.approx(0.45)
    assert blk["EXTERNAL_DISPERSION"] > 0
    assert blk["POLY_MINUS_EXTERNAL"] is not None


def test_the_devig_method_travels_with_the_answer():
    d = EA.devig([0.55, 0.50], "MULTIPLICATIVE")
    assert d["METHOD"] == "MULTIPLICATIVE"
    assert sum(d["DEVIGGED"]) == pytest.approx(1.0)
    assert d["OVERROUND"] == pytest.approx(0.05)


def test_an_unimplemented_devig_method_refuses_rather_than_approximates():
    d = EA.devig([0.55, 0.50], "SHIN")
    assert d["DEVIGGED"] == "MISSING"
    assert d["REASON"] == "METHOD_NOT_IMPLEMENTED"


def test_a_batch_counts_its_refusals():
    rows = [BD.decision_row("ev1", "m1", _t(0), "POST_BID", _book())]
    _, stats = EA.align_many(rows, {"m1": [{"SNAPSHOT_TIMESTAMP": _t(60),
                                            "BOOKMAKER_PRICES": [0.4]}]})
    assert stats["ALIGNED"] == 0
    assert stats["NO_EXTERNAL_DATA"] == 1     # the future snapshot is unusable


# --- Section 4. The backfill planner. --------------------------------------

def test_the_planner_deduplicates_by_bucket():
    """Six polls inside one 5-minute bucket cost ONE external request."""
    rows = [BD.decision_row("ev1", "m1", _t(i * 24), "POST_BID", _book())
            for i in range(6)]
    for r in rows:
        r["SPORT"] = "soccer_epl"
    p = EA.plan_backfill(rows)
    assert p["INPUT_STATES"] == 6
    assert p["UNIQUE_EXTERNAL_REQUESTS"] == 1
    assert p["CREDITS"] == 10


def test_the_planner_bills_the_provider_unit():
    assert EA.plan_backfill([], markets=("h2h",), regions=("uk",))[
        "CREDITS_PER_REQUEST"] == 10
    assert EA.plan_backfill([], markets=("h2h", "spreads"),
                            regions=("uk", "us"))["CREDITS_PER_REQUEST"] == 40


def test_the_planner_does_not_query_per_poll():
    assert "thousands of calls" in EA.DO_NOT_QUERY_PER_POLL


def test_nothing_is_purchased():
    assert EA.plan_backfill([])["NOTHING_IS_PURCHASED"] is True
    assert EA.THIS_MODULE_CONTACTS_NOTHING is True


# --- Section 8. Event-safe validation. -------------------------------------

def _multi_event(n_events=10, per=5):
    rows = []
    for e in range(n_events):
        for i in range(per):
            rows.append({"EVENT_ID": "ev%d" % e, "MARKET_ID": "m%d" % e,
                         "DECISION_TIMESTAMP_UTC": _t(i * 24, base_min=e)})
    return rows


def test_no_event_appears_in_two_splits():
    rows = _multi_event()
    a = ES.split(rows)
    chk = ES.leak_check(a, rows)
    assert chk["LEAKAGE_CHECK"] == "CLEAN"
    assert chk["MAY_SCORE"] is True


def test_a_leaked_event_is_caught_and_blocks_scoring():
    bad = {"SPLITS": {"TRAIN_EVENTS": ["ev1", "ev2"],
                      "VALIDATION_EVENTS": ["ev2"],
                      "TEST_EVENTS": ["ev3"]}}
    chk = ES.leak_check(bad)
    assert chk["LEAKAGE_CHECK"] == "LEAKED"
    assert chk["MAY_SCORE"] is False
    assert chk["OVERLAPPING_EVENTS"][0]["EVENT_ID"] == "ev2"


def test_the_split_is_chronological_by_event_start():
    rows = _multi_event()
    a = ES.split(rows, chronological=True)
    assert a["SPLITS"]["TRAIN_EVENTS"][0] == "ev0"
    assert a["SPLITS"]["TEST_EVENTS"][-1] == "ev9"


def test_too_few_events_reports_an_empty_split_rather_than_hiding_it():
    rows = _multi_event(n_events=2, per=3)
    a = ES.split(rows)
    assert a["STATUS"] == "SPLIT_WITH_EMPTY_PARTS"
    assert a["EMPTY_SPLITS"]
    assert "cannot fill three splits" in a["WHY_EMPTY"]


def test_a_random_row_split_is_named_as_the_failure():
    assert "memorises rather than learns" in ES.NO_RANDOM_ROW_SPLIT


def test_every_score_carries_rows_events_markets_hours_and_range():
    rows = _multi_event()
    a = ES.split(rows)
    sc = ES.scored(rows, a, "TEST_EVENTS", score=0.42, metric="LOG_LOSS")
    for f in ES.MANDATORY_SCALE_FIELDS:
        assert f in sc, f
    assert sc["SCORE"] == 0.42


def test_rows_over_few_events_is_a_different_claim():
    assert "cannot tell them apart" in ES.WHY_SCALE_TRAVELS_WITH_EVERY_SCORE


def test_an_unassigned_event_is_leakage_too():
    rows = _multi_event(n_events=3, per=2)
    partial = {"SPLITS": {"TRAIN_EVENTS": ["ev0"], "VALIDATION_EVENTS": [],
                          "TEST_EVENTS": ["ev1"]}}
    chk = ES.leak_check(partial, rows)
    assert chk["UNASSIGNED_EVENTS"] == ["ev2"]
    assert chk["MAY_SCORE"] is False


# --- Section 9. Market-state toxicity, frozen signs. -----------------------

def test_a_bid_quote_is_hurt_by_a_falling_market():
    t = MT.toxicity("BID", 0.40, 0.38)
    assert t["TOXICITY"] > 0
    assert t["MORE_ADVERSE"] is True


def test_a_bid_quote_is_helped_by_a_rising_market():
    assert MT.toxicity("BID", 0.40, 0.42)["TOXICITY"] < 0


def test_an_ask_quote_is_hurt_by_a_rising_market():
    t = MT.toxicity("ASK", 0.40, 0.42)
    assert t["TOXICITY"] > 0
    assert t["MORE_ADVERSE"] is True


def test_an_ask_quote_is_helped_by_a_falling_market():
    assert MT.toxicity("ASK", 0.40, 0.38)["TOXICITY"] < 0


def test_the_two_sides_are_exact_mirrors():
    a = MT.toxicity("BID", 0.40, 0.38)["TOXICITY"]
    b = MT.toxicity("ASK", 0.40, 0.38)["TOXICITY"]
    assert a == -b


def test_the_sign_convention_is_frozen_and_positive_is_worse():
    assert MT.SIGN_FROZEN_BEFORE_ANY_LABEL is True
    assert MT.SIGN_CONVENTION == "POSITIVE_IS_MORE_ADVERSE_FOR_THE_QUOTING_SIDE"
    assert "swapped to match" in MT.WHY_FREEZE_THE_SIGN


def test_toxicity_is_measured_on_the_executable_price_by_default():
    assert MT.DEFAULT_VALUE_BASIS == "EXECUTABLE"
    assert MT.EXECUTABLE_EXIT_PRICE["BID"] == "BEST_BID"
    assert MT.EXECUTABLE_EXIT_PRICE["ASK"] == "BEST_ASK"
    assert "half a spread" in MT.WHY_EXECUTABLE


def test_the_label_is_never_called_fill_conditional():
    assert MT.CONDITIONING == "UNCONDITIONAL_ON_BETTOR_FILL"
    assert MT.NOT_THIS == "FILL_CONDITIONAL_TOXICITY"
    s = MT.summarise([], "BID", 30)
    assert s["MEAN_TOXICITY"] == "NOT_IDENTIFIED"


def test_both_sides_are_labelled_separately():
    s = _series()
    lab = BD.label_row(s[0], s)
    both = MT.label_both_sides(s[0], lab)
    assert set(both) == {"BID", "ASK"}
    assert both["BID"]["MARKET_STATE_TOXICITY_30S"] == \
        -both["ASK"]["MARKET_STATE_TOXICITY_30S"]


# --- Section 10. The future fill schema. -----------------------------------

def test_every_declared_order_field_is_present():
    rec = FD.order_record("o1", "d1", "BID", "0.40", "100")
    for f in FD.ORDER_FIELDS:
        assert f in rec, f


def test_an_unknown_fate_is_not_a_non_fill():
    rec = FD.order_record("o1", "d1", "BID", "0.40", "100")
    out = FD.fill_outcome(rec)
    assert out["FILLED"] == "NOT_IDENTIFIED"
    assert "bias every fill rate downward" in out["UNKNOWN_IS_NOT_NOT_FILLED"]


def test_paired_timestamps_measure_latency():
    rec = FD.order_record("o1", "d1", "BID", "0.40", "100",
                          submit_timestamp=_t(0), ACK_TIMESTAMP=_t(0.25),
                          FIRST_FILL_TIMESTAMP=_t(3),
                          LAST_FILL_TIMESTAMP=_t(5))
    lat = FD.latency(rec)
    assert lat["SUBMIT_TO_ACK_S"] == pytest.approx(0.25)
    assert lat["FIRST_TO_LAST_FILL_S"] == pytest.approx(2.0)


def test_no_order_is_placed_by_the_schema():
    rec = FD.order_record("o1", "d1", "BID", "0.40", "100")
    assert rec["THIS_IS_A_SCHEMA_NO_ORDER_WAS_PLACED"] is True
    assert FD.NO_ORDER_IS_PLACED is True
    assert FD.ORDER_PATH_EXISTS is False


# --- Section 12. Queue and fill. -------------------------------------------

def test_no_exact_queue_position_is_produced_from_l2():
    q = FD.queue_distribution(250)
    assert q["EXACT_QUEUE_POSITION"] == "NOT_IDENTIFIED"
    assert q["QUEUE_AHEAD_DISTRIBUTION"]["KIND"] == "UNIFORM"
    assert q["IS_AN_ASSUMPTION_NOT_AN_OBSERVATION"] is True


def test_each_queue_assumption_gives_a_different_distribution():
    assert FD.queue_distribution(250, "ARRIVED_LAST")[
        "QUEUE_AHEAD_DISTRIBUTION"]["AT"] == 250
    assert FD.queue_distribution(250, "ARRIVED_FIRST")[
        "QUEUE_AHEAD_DISTRIBUTION"]["AT"] == 0


def test_measured_queue_needs_real_orders():
    q = FD.queue_distribution(250, "MEASURED_FROM_ORDER_EVIDENCE")
    assert q["QUEUE_AHEAD"] == "NOT_IDENTIFIED"
    assert "needs real orders" in q["WHY"]


def test_p_fill_is_not_identified_and_is_not_zero():
    p = FD.p_fill(horizon_s=30)
    assert p["P_FILL"] == "NOT_IDENTIFIED"
    assert "not a fill probability of zero" in p["WHY"]
    assert p["BLOCKED_ON"] == "BETTOR_NATIVE_ORDER_EVIDENCE"


# --- Section 11. Matched fill-selection. -----------------------------------

def _st(**kw):
    base = {"SIDE": "BID", "PRICE": 0.40, "SPREAD": 0.02, "DEPTH": 100,
            "IMBALANCE": 0.1, "OFI": 0.0, "VOLATILITY": 0.01,
            "TIME_TO_EVENT": 3600, "MARKET_FAMILY": "MONEYLINE",
            "QUOTE_AGE": 5, "EXTERNAL_MARKET_STATE": "NONE",
            "CROSS_MARKET_RESIDUAL": 0.0}
    base.update(kw)
    return base


def test_an_unmatchable_filled_state_is_reported_not_dropped():
    m = FD.matched_pairs([_st(SIDE="BID")], [_st(SIDE="ASK")],
                         tolerance={k: 0.001 for k in FD.MATCH_CONTROLS})
    assert m["MATCHED"] == 0
    assert m["UNMATCHED_FILLED_STATES"] == 1
    assert m["UNMATCHED_ARE_REPORTED_NOT_DROPPED"] is True


def test_a_matched_pair_yields_a_delta_with_a_frozen_sign():
    tol = {k: 0.001 for k in FD.MATCH_CONTROLS}
    f = _st(MARKOUT_30S=-0.010)
    q = _st(MARKOUT_30S=-0.002)
    m = FD.matched_pairs([f], [q], tolerance=tol)
    d = FD.selection_delta(m["PAIRS"], 30)
    assert d["FILL_SELECTION_MARKOUT_DELTA"] == pytest.approx(-0.008)
    assert d["FILL_SELECTION_EFFECT"] == "MORE_ADVERSE"


def test_the_analysis_can_return_less_adverse():
    tol = {k: 0.001 for k in FD.MATCH_CONTROLS}
    m = FD.matched_pairs([_st(MARKOUT_30S=-0.001)],
                         [_st(MARKOUT_30S=-0.009)], tolerance=tol)
    d = FD.selection_delta(m["PAIRS"], 30)
    assert d["FILL_SELECTION_EFFECT"] == "LESS_ADVERSE"


def test_no_direction_is_assumed():
    assert FD.NO_DIRECTION_ASSUMED is True
    assert set(FD.FILL_SELECTION_OUTCOMES) == {
        "MORE_ADVERSE", "NO_MATERIAL_DIFFERENCE", "LESS_ADVERSE"}


def test_the_quote_size_experiment_is_prepared_not_run():
    e = FD.size_experiment()
    assert e["STATUS"] == "PREPARED_NOT_RUN"
    assert "hypothesis" in e["LARGER_IS_NOT_ASSUMED_BETTER"]


# --- Section 16. Inventory, unnetted. --------------------------------------

def test_a_matched_pair_is_not_flat():
    inv = IS.inventory("100", "0.45", "100", "0.52")
    assert inv["IS_GENUINELY_FLAT"] is False
    assert inv["IS_MATCHED_NOT_FLAT"] is True
    assert inv["MATCHED_QTY"] == "100"


def test_a_genuinely_flat_book_occupies_nothing():
    inv = IS.inventory("0", None, "0", None)
    assert inv["IS_GENUINELY_FLAT"] is True
    assert Decimal(inv["CAPITAL_OCCUPIED"]) == 0


def test_the_pair_basis_and_locked_pnl_are_exact():
    """0.45 + 0.52 = 0.97 basis, so 100 pairs lock 0.03 * 100 = 3.00."""
    inv = IS.inventory("100", "0.45", "100", "0.52")
    assert Decimal(inv["PAIR_BASIS"]) == Decimal("0.97")
    assert Decimal(inv["LOCKED_PNL"]) == Decimal("3.00")
    assert Decimal(inv["CAPITAL_OCCUPIED"]) == Decimal("97.00")


def test_a_basis_above_one_locks_a_loss():
    inv = IS.inventory("100", "0.55", "100", "0.52")
    assert Decimal(inv["LOCKED_PNL"]) < 0


def test_the_residual_leg_carries_the_exposure():
    inv = IS.inventory("150", "0.45", "100", "0.52")
    assert inv["RESIDUAL_YES"] == "50"
    assert inv["RESIDUAL_NO"] == "0"
    assert Decimal(inv["UNLOCKED_EXPOSURE"]) == 50


def test_the_legs_are_never_netted():
    assert "loses all three facts" in IS.DO_NOT_NET_THE_LEGS


def test_an_unknown_cost_leaves_locked_pnl_unidentified_not_zero():
    inv = IS.inventory("100", None, "100", "0.52")
    assert inv["LOCKED_PNL"] == "NOT_IDENTIFIED"
    assert "It is not zero" in inv["WHY_LOCKED_PNL_UNKNOWN"]


def test_a_float_cost_is_a_hard_error():
    with pytest.raises(TypeError, match="FLOAT_IN_A_MONEY_PATH"):
        IS.inventory("100", 0.45, "100", "0.52")


# --- Section 15. Capital hours. --------------------------------------------

def test_both_the_level_and_the_rate_are_reported():
    c = IS.capital_hours("3.00", "97.00", "1800")
    assert c["EXPECTED_NET_DOLLARS"] == "3.00"
    assert c["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] != "NOT_IDENTIFIED"


def test_the_rate_is_exact():
    """3.00 / (97.00 * 0.5h) = 0.06185567..."""
    c = IS.capital_hours("3.00", "97.00", "1800")
    assert Decimal(c["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"]) == \
        Decimal("0.06185567")


def test_a_tiny_fast_trade_has_a_huge_rate_and_still_earns_a_cent():
    c = IS.capital_hours("0.01", "1.00", "1")
    assert Decimal(c["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"]) == Decimal("36")
    assert Decimal(c["EXPECTED_NET_DOLLARS"]) == Decimal("0.01")
    assert "still earns one cent" in c["BOTH_ARE_REPORTED"]


def test_an_unknown_denominator_gives_no_rate():
    c = IS.capital_hours("3.00", None, "1800")
    assert c["EXPECTED_NET_DOLLARS_PER_CAPITAL_HOUR"] == "NOT_IDENTIFIED"
    assert "guessed denominator" in c["WHY_NOT"]


# --- Section 18. The action vocabulary. ------------------------------------

def test_every_required_action_is_declared():
    for a in ("NO_TRADE", "POST_BID", "POST_ASK", "IMPROVE_BID",
              "IMPROVE_ASK", "HOLD", "PAIR", "HEDGE", "PASSIVE_EXIT",
              "AGGRESSIVE_EXIT", "SETTLE"):
        assert a in AE.ACTIONS, a


def test_an_undeclared_action_is_refused_not_priced():
    r = AE.action_ev("SOMETHING_CLEVER", p_fill="0.5",
                     expected_value_if_filled="0.02",
                     expected_adverse_selection="0.005", fee="0.001")
    assert r["STATUS"] == "UNKNOWN_ACTION"
    assert r["RECOMMENDED"] is False


def test_pair_and_settle_are_real_alternatives_to_quoting():
    assert "cannot see them" in AE.WHY_PAIR_AND_SETTLE_ARE_ACTIONS
    r = AE.action_ev("PAIR", p_fill="1", expected_value_if_filled="0.03",
                     expected_adverse_selection="0", fee="0")
    assert r["EXPECTED_NET_DOLLARS"] == "0.030000"


# --- Section 19. Disagreement. ---------------------------------------------

def test_the_three_differences_are_computed():
    d = CG.disagreement(p_market=0.50, p_external=0.53, p_fundamental=0.58)
    assert d["FUNDAMENTAL_MINUS_MARKET"] == pytest.approx(0.08)
    assert d["EXTERNAL_MINUS_MARKET"] == pytest.approx(0.03)
    assert d["FUNDAMENTAL_MINUS_EXTERNAL"] == pytest.approx(0.05)


def test_a_missing_source_leaves_its_differences_unidentified():
    d = CG.disagreement(p_market=0.50, p_fundamental=0.58)
    assert d["EXTERNAL_MINUS_MARKET"] == "NOT_IDENTIFIED"
    assert d["FUNDAMENTAL_MINUS_MARKET"] == pytest.approx(0.08)


def test_the_buckets_are_pre_registered():
    assert CG.BUCKETS_PRE_REGISTERED is True
    assert "a result, not a design" in \
        CG.DO_NOT_TUNE_BUCKETS_ON_THE_EVALUATION_SAMPLE
    assert CG.bucket_of(0.01) == "0.00-0.02"
    assert CG.bucket_of(0.30) == "0.10-1.00"


def test_handicapping_is_not_the_primary_thread():
    assert "NOT the primary thread" in CG.NOT_THE_PRIMARY_THREAD
    assert "incremental question" in CG.THE_QUESTION


# --- Section 20. The data contract. ----------------------------------------

def test_a_fact_without_a_timestamp_is_not_high_integrity():
    c = CG.contract_row("XG", value=1.4)
    assert c["LANE"] == "RESEARCH_ONLY"
    assert c["ELIGIBLE_FOR_HIGH_INTEGRITY"] is False


def test_a_timestamped_fact_before_the_decision_is_high_integrity():
    c = CG.contract_row("CONFIRMED_LINEUP", value="XI",
                        information_available_at=_t(0),
                        decision_timestamp=_t(60))
    assert c["LANE"] == "HIGH_INTEGRITY"


def test_a_fact_known_only_after_the_decision_is_excluded():
    c = CG.contract_row("CONFIRMED_LINEUP", value="XI",
                        information_available_at=_t(60),
                        decision_timestamp=_t(0))
    assert c["LANE"] == "EXCLUDED"
    assert "AFTER the decision" in c["WHY"]


def test_every_named_fundamental_field_is_in_the_contract():
    for f in ("XG", "NPXG", "PLAYER_STRENGTH", "CONFIRMED_LINEUP",
              "EXPECTED_LINEUP", "INJURY", "SUSPENSION", "GOALKEEPER",
              "REST", "TRAVEL"):
        assert f in CG.PLAYER_XG_FIELDS, f


# --- Sections 22, 23, 25. Gates, claims, moat. -----------------------------

def test_no_gate_passes_today():
    st = CG.programme_status()
    assert st["ANY_GATE_PASSED"] is False
    assert st["FIRST_UNPASSED_GATE"] == "SHORT_HORIZON_STRUCTURE_GATE"


def test_a_gate_names_exactly_what_is_missing():
    g = CG.gate("EXECUTION_EDGE_GATE", met=["REAL_BETTOR_ORDERS_EXIST"])
    assert g["PASSED"] is False
    assert "P_FILL_IDENTIFIED" in g["NOT_MET"]
    assert len(g["MET"]) == 1


def test_a_later_gate_is_marked_blocked_by_an_earlier_one():
    st = CG.programme_status({"EXECUTION_EDGE_GATE":
                              CG.EXECUTION_EDGE_GATE_CONDITIONS})
    by = {g["GATE"]: g for g in st["GATES"]}
    assert by["EXECUTION_EDGE_GATE"]["PASSED"] is True
    assert by["EXECUTION_EDGE_GATE"]["BLOCKED_BY_EARLIER_GATE"] is True
    assert "not a result" in st["GATES_ARE_ORDERED"]


def test_the_first_capture_may_not_claim_scalability():
    c = CG.may_claim("SCALABILITY_CLAIM", independent_events=3)
    assert c["PERMITTED"] is False
    assert "cannot establish that the effect survives at size" in \
        c["WHY"].replace("\n", " ")


def test_the_first_capture_may_claim_what_it_is_for():
    for claim in CG.FIRST_CAPTURE_IS_FOR:
        assert CG.may_claim(claim, 3)["PERMITTED"] is True


def test_production_admission_is_refused():
    assert CG.may_claim("PRODUCTION_ADMISSION", 3)["PERMITTED"] is False


def test_the_moat_is_not_one_model():
    m = CG.moat()
    assert m["MOAT_IS_NOT"] == "ONE_GREAT_PREDICTION_MODEL"
    assert m["THE_DECISION_OBJECT"] == "FILL_CONDITIONED_ACTION_EV"
    assert m["MOAT_STATUS"] == "CANDIDATE_ARCHITECTURE_NOT_DEMONSTRATED"
    assert len(m["MOAT_CANDIDATES"]) == 7


def test_confidence_is_not_updated_emotionally():
    assert "mood, not evidence" in CG.DO_NOT_UPDATE_CONFIDENCE_EMOTIONALLY


# --- Section 21. Retention metrics. ----------------------------------------

def test_the_asset_is_counted_by_events_not_only_rows():
    rows = _series(10)
    r = BD.retention(rows)
    assert r["TOTAL_DECISION_STATES"] == 10
    assert r["TOTAL_INDEPENDENT_EVENTS"] == 1
    assert r["TOTAL_EVENT_HOURS"] > 0


def test_zero_orders_today_is_a_fact_not_a_gap():
    r = BD.retention(_series(5), orders=[])
    assert r["TOTAL_REAL_ORDERS"] == 0
    assert r["TOTAL_REAL_FILLS"] == 0


def test_growth_is_reported_per_metric():
    g = BD.growth({"TOTAL_DECISION_STATES": 100},
                  {"TOTAL_DECISION_STATES": 150})
    assert g["TOTAL_DECISION_STATES"]["GROWTH_PCT"] == 50.0


def test_growth_from_zero_is_not_infinite():
    g = BD.growth({"TOTAL_REAL_ORDERS": 0}, {"TOTAL_REAL_ORDERS": 5})
    assert g["TOTAL_REAL_ORDERS"]["GROWTH_PCT"] == "NOT_IDENTIFIED"


# --- Section 24. The V2 template. ------------------------------------------

def test_v2_values_are_deferred_until_v1_measures_them():
    t = V2.v2_template()
    assert t["MAY_FREEZE_VALUES"] is False
    assert len(t["MEASUREMENTS_MISSING"]) == 4
    assert t["VALUES"]["TARGET_INDEPENDENT_EVENTS"] == "NOT_IDENTIFIED"
    assert t["DISPATCHED"] is False


def test_v2_may_freeze_once_all_four_are_measured():
    t = V2.v2_template(measured=V2.V2_VALUES_DEFERRED_UNTIL)
    assert t["MAY_FREEZE_VALUES"] is True


def test_events_and_markets_per_event_are_never_traded_off():
    assert "never traded against each other" in \
        V2.V2_DIMENSIONS_ARE_CHOSEN_SEPARATELY


# --- Section 26. The hard freeze. ------------------------------------------

def test_nothing_in_this_stack_trains_places_or_purchases():
    assert BD.NOTHING_IS_TRAINED_HERE is True
    assert MT.NOTHING_IS_TRAINED_HERE is True
    assert CG.NOTHING_IS_TRAINED_HERE is True
    assert EA.NOTHING_IS_PURCHASED is True
    assert FD.NO_ORDER_IS_PLACED is True
    assert IS.NOTHING_IS_PLACED is True


def test_the_frozen_capture_and_thresholds_are_untouched():
    import capture_quality as CQ
    import ev_core_registers as R
    assert CQ.thresholds_intact()["INTACT"] is True
    assert CQ.FROZEN_QUALITY_THRESHOLDS_SHA == (
        "70e6ddda88ad4141ae3ed58691d8322cea4bf37a83c89efd481792c47243e7fb")
    assert R.FROZEN_QUALITY_THRESHOLDS_SHA == \
        CQ.FROZEN_QUALITY_THRESHOLDS_SHA


def test_the_register_agrees_with_every_module():
    import ev_core_registers as R
    assert R.TRAINING_PERFORMED == "NO"
    assert R.PARAMETER_TUNING_PERFORMED == "NO"
    assert R.LIVE_ORDER_ACTIVITY == "NONE"
    assert R.CAPTURE_V2_TEMPLATE_STATUS == V2.V2_TEMPLATE_STATUS
    assert R.MOAT_IS_NOT == CG.MOAT_IS_NOT
    assert R.MOAT_STATUS == CG.MOAT_STATUS
    assert R.THE_DECISION_OBJECT == CG.THE_DECISION_OBJECT
    assert "24 s nominal revisit CANNOT label a 5-second" in \
        R.THE_LABEL_GRID_LIMIT
