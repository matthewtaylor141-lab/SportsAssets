"""Tests for start-time classes, the repaired B4, internal strength,
calibration, and the pre-registered contracts."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ev_core_registers as REG  # noqa: E402
import event_start_time as EST  # noqa: E402
import p_bettor_independent_v3 as V3  # noqa: E402


# ---------------------------------------------------------------------------
# Section 1: the retraction and the venue-native search
# ---------------------------------------------------------------------------


def test_the_pregame_proven_claim_is_withdrawn_not_quietly_dropped():
    assert EST.PREGAME_PROVEN_CLAIM == "WITHDRAWN"
    assert "settlement time is not kick-off time" in EST.WHY_WITHDRAWN
    assert EST.RECLASSIFIED_AS == "PREGAME_LIKELY_BY_SETTLEMENT_LAG"


def test_the_venue_native_coverage_is_zero_and_says_why():
    d = EST.VENUE_NATIVE_COVERAGE_DETAIL
    assert EST.VENUE_NATIVE_START_TIME_COVERAGE == 0.0
    assert d["SETTLED_EVENTS_WITH_VENUE_NATIVE_START"] == 0
    assert d["SETTLED_SOCCER_EVENTS_WITH_VENUE_NATIVE_START"] == 0
    # the field exists; it is the overlap that does not
    assert d["RETAINED_SLUGS_WITH_THE_FIELD"] > 2000
    assert "gameStartTime" in EST.VENUE_NATIVE_START_TIME_SOURCE


def test_a_futures_start_time_is_flagged_as_a_placeholder():
    assert EST.VENUE_NATIVE_IS_EXACT_FOR_FIXTURES_ONLY is True
    assert "not a kick-off" in EST.FUTURES_GAMESTARTTIME_IS_A_PLACEHOLDER


# ---------------------------------------------------------------------------
# Section 2: the three classes
# ---------------------------------------------------------------------------


def test_a_venue_native_time_is_class_a():
    c = EST.classify(venue_native="2026-09-18T00:15:00Z")
    assert c["START_TIME_STATUS"] == EST.CLASS_A
    assert c["UNCERTAINTY_HOURS"] < 0.1


def test_two_agreeing_sources_are_class_b():
    # offset_hours is (source_b - source_a), and is subtracted from source_b
    # before the comparison. Here b runs an hour ahead of a, so the offset is
    # +1.0; passing -1.0 would push them two hours apart, not zero.
    c = EST.classify(source_a=("2026-08-30", "18:00:00"),
                     source_b=("2026-08-30", "19:00:00"),
                     offset_hours=1.0,
                     resolved_at="2026-08-30T20:00:00Z")
    assert c["START_TIME_STATUS"] == EST.CLASS_B
    assert c["CROSS_SOURCE_GAP_HOURS"] < 0.01
    assert 1.0 <= c["IMPLIED_SETTLEMENT_LAG_HOURS"] <= 4.5


def test_sources_that_disagree_beyond_the_known_offset_fall_to_class_c():
    c = EST.classify(source_a=("2026-08-30", "18:00:00"),
                     source_b=("2026-08-30", "22:00:00"),
                     offset_hours=1.0)
    assert c["START_TIME_STATUS"] == EST.CLASS_C
    assert c["BASIS"] == "PUBLIC_SOURCES_DISAGREE_BEYOND_THE_KNOWN_OFFSET"


def test_an_implausible_settlement_lag_demotes_to_class_c():
    c = EST.classify(source_a=("2026-08-30", "18:00:00"),
                     source_b=("2026-08-30", "19:00:00"),
                     offset_hours=1.0,
                     resolved_at="2026-09-01T20:00:00Z")
    assert c["START_TIME_STATUS"] == EST.CLASS_C
    assert c["BASIS"] == "SOURCES_AGREE_BUT_SETTLEMENT_LAG_IMPLAUSIBLE"


def test_one_source_alone_is_never_cross_validated():
    c = EST.classify(source_a=("2026-08-30", "18:00:00"))
    assert c["START_TIME_STATUS"] == EST.CLASS_C
    assert c["BASIS"] == "ONE_PUBLIC_SOURCE_ONLY_NOT_CROSS_VALIDATED"


def test_no_start_time_at_all_is_refused_by_name():
    c = EST.classify()
    assert c["START_TIME_UTC"] == "NOT_IDENTIFIED"
    assert c["BASIS"] == "NO_START_TIME_AT_ALL"


def test_a_tight_horizon_needs_a_class_a_clock():
    assert EST.TIGHT_HORIZONS_REQUIRE == EST.CLASS_A
    ok, _ = EST.horizon_claimable(0.5, EST.CLASS_B)
    assert ok is False
    ok, _ = EST.horizon_claimable(0.25, EST.CLASS_C)
    assert ok is False
    ok, _ = EST.horizon_claimable(0.5, EST.CLASS_A)
    assert ok is True


def test_a_long_horizon_survives_a_class_b_clock():
    assert EST.horizon_claimable(24.0, EST.CLASS_B)[0] is True
    assert EST.horizon_claimable(6.0, EST.CLASS_B)[0] is True
    # but two hours does not, at an hour of uncertainty
    assert EST.horizon_claimable(1.5, EST.CLASS_B)[0] is False


# ---------------------------------------------------------------------------
# Section 12: the repaired bivariate Poisson
# ---------------------------------------------------------------------------


def test_the_shared_component_changes_the_joint_distribution():
    """The defect was that it did not. This is the test the directive asked for."""
    a = V3.bivariate_grid(1.4, 1.1, 0.0)
    b = V3.bivariate_grid(1.4, 1.1, 0.25)
    assert a != b
    moved = sum(1 for k in a if abs(a[k] - b[k]) > 1e-9)
    assert moved > 20, "changing l3 must move many cells, not a rounding edge"


def test_the_covariance_equals_the_shared_component():
    for l3 in (0.0, 0.05, 0.15, 0.30):
        cov = V3.bivariate_covariance(1.4, 1.1, l3)
        assert abs(cov - l3) < 1e-6, (l3, cov)


def test_zero_shared_component_recovers_independent_poisson():
    g = V3.bivariate_grid(1.4, 1.1, 0.0)
    for (x, y), p in list(g.items())[:40]:
        ind = (math.exp(-1.4) * 1.4 ** x / math.factorial(x)
               * math.exp(-1.1) * 1.1 ** y / math.factorial(y))
        assert abs(p - ind) < 2e-4, (x, y, p, ind)


def test_the_grid_is_always_a_distribution():
    for l3 in (0.0, 0.1, 0.4):
        g = V3.bivariate_grid(2.1, 0.7, l3)
        assert abs(sum(g.values()) - 1.0) < 1e-9
        assert all(p >= 0 for p in g.values())


def test_the_pmf_is_symmetric_under_swapping_both_arguments():
    for l3 in (0.0, 0.2):
        a = V3.bivariate_poisson_pmf(2, 1, 1.3, 0.9, l3)
        b = V3.bivariate_poisson_pmf(1, 2, 0.9, 1.3, l3)
        assert abs(a - b) < 1e-12


def test_the_repair_status_and_the_fit_mode_are_both_stated():
    assert V3.B4_REPAIR_STATUS == \
        "REPAIRED_SHARED_COMPONENT_DRIVES_THE_EMITTED_PMF"
    assert V3.B4_FIT_MODE == "SHARED_COMPONENT_ON_FIXED_DIXON_COLES_STRENGTHS"
    assert "weaker fit" in V3.B4_FIT_MODE_NOTE or \
        "stronger experiment" in V3.B4_FIT_MODE_NOTE


# ---------------------------------------------------------------------------
# Section 6: internal strength
# ---------------------------------------------------------------------------


def _matches(n=300, seed=3):
    import datetime
    import random
    rnd = random.Random(seed)
    d0 = datetime.date(2024, 1, 1)
    clubs = ["c%d" % i for i in range(10)]
    out = []
    for k in range(n):
        h, a = rnd.sample(clubs, 2)
        # club 0 is deliberately strong, club 9 deliberately weak
        gh = rnd.randint(0, 2) + (2 if h == "c0" else 0)
        ga = rnd.randint(0, 2) + (2 if a == "c0" else 0)
        if h == "c9":
            gh = max(0, gh - 1)
        if a == "c9":
            ga = max(0, ga - 1)
        out.append({"Division": "E0",
                    "MatchDate": (d0 + datetime.timedelta(days=k)).isoformat(),
                    "MatchTime": "15:00:00", "HomeTeam": h, "AwayTeam": a,
                    "FTHome": str(gh), "FTAway": str(ga)})
    return out


def test_internal_strength_is_read_before_the_match_updates_it():
    rows = _matches(50)
    st = V3.internal_strength(rows)
    assert st[0]["INT_ELO_HOME"] == V3.ELO_START
    assert st[0]["INT_ELO_AWAY"] == V3.ELO_START
    assert st[0]["INT_EWMA_GF_HOME"] is None


def test_internal_elo_learns_which_club_is_strong():
    st = V3.internal_strength(_matches(600))
    last = {}
    for r in st:
        last[r["HOME"]] = r["INT_ELO_HOME"]
        last[r["AWAY"]] = r["INT_ELO_AWAY"]
    assert last["c0"] > last["c9"], last


def test_goal_difference_elo_is_a_different_state_from_classic_elo():
    st = V3.internal_strength(_matches(600))
    diffs = [abs(r["INT_ELO_DIFF"] - r["INT_GD_ELO_DIFF"]) for r in st[-50:]]
    assert max(diffs) > 1.0, "the two ratings must not collapse into one"


def test_the_internal_states_are_named_and_say_why_they_exist():
    d = V3.describe()
    assert "CLASSIC_ELO" in d["INTERNAL_STRENGTH_STATES"]
    assert "GOAL_DIFFERENCE_ELO" in d["INTERNAL_STRENGTH_STATES"]
    assert "provisional" in d["WHY_INTERNAL"]


# ---------------------------------------------------------------------------
# Section 3: calibration and its limits
# ---------------------------------------------------------------------------


def _cal_rows(n=600, seed=9, over=2.0):
    import random
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        z = rnd.gauss(0, 1)
        p_true = 1 / (1 + math.exp(-z))
        y = 1 if rnd.random() < p_true else 0
        p_over = 1 / (1 + math.exp(-over * z))       # overconfident forecast
        rows.append({"EVENT_KEY": "e%d" % (i // 4), "Y": y, "P": p_over})
    return rows


def test_platt_pulls_an_overconfident_forecast_back():
    rows = _cal_rows()
    cal = V3.fit_calibrator(rows, "P", "PLATT")
    assert cal["FITTED"] is True
    # the fitted slope on the logit scale should be well below one
    assert cal["B"][1] < 0.85, cal["B"]


def test_every_calibrator_returns_a_probability():
    rows = _cal_rows()
    for m in ("PLATT", "BETA", "TEMPERATURE"):
        cal = V3.fit_calibrator(rows, "P", m)
        assert cal["FITTED"] is True, m
        for p in (0.01, 0.3, 0.5, 0.97):
            q = V3.apply_calibrator(cal, p)
            assert 0.0 < q < 1.0, (m, p, q)


def test_calibration_is_monotone_so_it_cannot_reorder_forecasts():
    rows = _cal_rows()
    for m in ("PLATT", "BETA", "TEMPERATURE"):
        cal = V3.fit_calibrator(rows, "P", m)
        ps = [0.02, 0.1, 0.25, 0.5, 0.75, 0.9, 0.98]
        qs = [V3.apply_calibrator(cal, p) for p in ps]
        assert all(qs[i] <= qs[i + 1] + 1e-12 for i in range(len(qs) - 1)), m


def test_isotonic_refuses_on_a_small_event_sample():
    cal = V3.fit_calibrator(_cal_rows(), "P", "ISOTONIC", n_events=40)
    assert cal["FITTED"] is False
    assert cal["REASON"] == V3.ISOTONIC_REFUSAL
    assert "interpolate noise" in cal["WHY"]


def test_calibration_does_not_claim_to_create_information():
    d = V3.describe()
    assert d["CALIBRATION_CAN_IMPROVE_PROBABILITY_QUALITY"] is True
    assert d["CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION"] is True
    assert "monotone" in d["WHY_CALIBRATION_IS_NOT_INFORMATION"].lower()
    assert "PROSPECTIVE" in d["WHY_CALIBRATION_IS_NOT_INFORMATION"]


def test_too_few_rows_refuses_rather_than_fitting():
    cal = V3.fit_calibrator([{"P": 0.5, "Y": 1}], "P", "PLATT")
    assert cal["FITTED"] is False and cal["REASON"] == "TOO_FEW_ROWS"


# ---------------------------------------------------------------------------
# Section 10: the evidence ladder
# ---------------------------------------------------------------------------


def test_the_ladder_says_the_current_sample_can_resolve_nothing():
    st = REG.ladder_status(REG.INDEPENDENT_TEST_EVENTS_CURRENT)
    assert st["ANY_RUNG_POWERED"] is False
    assert st["BY_EFFECT_SIZE"]["0.020"]["SHORTFALL_FACTOR"] > 15
    assert st["BY_EFFECT_SIZE"]["0.010"]["SHORTFALL_FACTOR"] > 50


def test_the_required_events_scale_as_the_inverse_square_of_the_effect():
    a = REG.events_required(0.010)
    b = REG.events_required(0.020)
    assert abs(a / b - 4.0) < 0.05


def test_the_ladder_was_fixed_before_the_next_result():
    assert REG.THE_LADDER_WAS_SET_BEFORE_THE_NEXT_RESULT is True
    assert "BEFORE the test is run" in \
        REG.MINIMUM_EVIDENCE_BEFORE_A_NEGATIVE_RESULT


def test_not_detected_is_explained_as_arithmetic_about_the_sample():
    assert "never a result about football" in REG.WHAT_THE_LADDER_MEANS


# ---------------------------------------------------------------------------
# Sections 7, 8, 5, 17, 15, 16: the registers
# ---------------------------------------------------------------------------


def test_the_three_consensus_timing_statuses_are_kept_apart():
    assert REG.OPENING_CONSENSUS_STATUS == \
        "NOT_AVAILABLE_FOR_THE_EVALUATION_WINDOW"
    assert REG.CLOSING_CONSENSUS_STATUS == \
        "COARSE_PREMATCH_UNTIMESTAMPED_SETTLEMENT_ONLY"
    assert REG.EXACT_TIMESTAMP_CONSENSUS_STATUS == "NOT_IDENTIFIED"
    joined = " ".join(REG.CONSENSUS_TIMING_RULES)
    assert "never be combined" in joined


def test_the_odds_provider_options_carry_the_point_in_time_question():
    for p in REG.EXACT_TIMESTAMP_ODDS_PROVIDER_OPTIONS:
        for k in ("PROVIDER", "HISTORICAL_DEPTH", "SNAPSHOT_FREQUENCY",
                  "SPORTS", "MARKETS", "ACCESS", "ARE_THEY_POINT_IN_TIME"):
            assert k in p, (p.get("PROVIDER"), k)
    assert REG.NOTHING_HAS_BEEN_PURCHASED is True
    assert REG.NOTHING_HAS_BEEN_REQUESTED is True


def test_the_player_availability_contract_forbids_a_backfilled_status():
    c = REG.PLAYER_AVAILABILITY_CONTRACT
    assert REG.PLAYER_AVAILABILITY_DATA_STATUS == "NOT_IDENTIFIED"
    assert REG.DO_NOT_FABRICATE_AVAILABILITY_HISTORY is True
    assert "OBSERVED_AT" in c["REQUIRED_FIELDS"]
    assert "back-filled" in c["WHAT_MAKES_A_ROW_USELESS"]
    assert "expectation" in REG.PLAYER_AVAILABILITY_WHY


def test_the_live_model_is_not_identified_and_needs_game_state():
    assert REG.P_BETTOR_LIVE_INDEPENDENT_STATUS == "NOT_IDENTIFIED"
    req = REG.LIVE_MODEL_CONTRACT["REQUIRED_STATE_AT_T"]
    for f in ("SCORE_HOME", "MINUTES_ELAPSED", "RED_CARDS_HOME"):
        assert f in req
    assert "REMAINING" in REG.LIVE_MODEL_CONTRACT["THE_MODEL_IS_CONDITIONAL"]


def test_the_regimes_were_named_before_the_evaluation():
    r = REG.PREREGISTERED_REGIMES
    assert set(r) == {"LIQUIDITY", "PREGAME_PHASE", "PRICE_BAND",
                      "MARKET_FAMILY", "MODEL_DISAGREEMENT"}
    for k, v in r.items():
        assert v["WHY_PLAUSIBLE"], k
    assert "subgroup finding" in REG.NO_OTHER_REGIME_MAY_BE_REPORTED
    # the regime that cannot be run says so
    assert "BLOCKED_BY" in r["PREGAME_PHASE"]


def test_the_exact_score_hypothesis_is_frozen_and_not_called_alpha():
    h = REG.GEN2_EXACT_SCORE_RELATIVE_VALUE_HYPOTHESIS
    assert h["STATUS"] == "FROZEN_PREREGISTERED_NOT_VALIDATED"
    assert h["IT_IS_NOT_ALPHA_TODAY"] is True
    assert h["MAY_NOT_BE_TRADED"] is True
    assert h["MINIMUM_RESIDUAL_MAGNITUDE"] == 0.02
    for k in ("DEFINITION", "EVALUATION_METHOD", "HORIZON",
              "SUCCESS_CRITERION"):
        assert h[k]
    assert "failure" in h["FAILURE_IS_A_RESULT"].lower() or \
        "fails" in h["FAILURE_IS_A_RESULT"]


def test_the_two_weightings_are_never_averaged():
    assert REG.FORECAST_VALIDATION_WEIGHTING == \
        "EVENT_EQUAL_ONE_EVENT_ONE_VOTE"
    assert REG.ECONOMIC_WEIGHTING == "CONTRACT_NOTIONAL_WEIGHTED"
    assert "never be averaged" in REG.THEY_ANSWER_DIFFERENT_QUESTIONS
