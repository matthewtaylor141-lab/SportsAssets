"""Tests for the corrected billing unit, the two experiments, and the
lead/lag power model that is NOT the settlement ladder."""

import datetime

import pytest

import lead_lag as LL
import lead_lag_power as LP
import odds_request_planner as RP


# --- Sections 1, 7. The billable unit and the verified formula. ------------

def test_the_credit_formula_is_verified_with_a_date():
    assert RP.HISTORICAL_CREDIT_MULTIPLIER == 10
    assert RP.CREDIT_FORMULA_STATUS == "VERIFIED"
    assert RP.CREDIT_FORMULA_VERIFIED_ON == "2026-09-17"


def test_plan_pricing_is_verified_and_dated_and_not_treated_as_permanent():
    assert RP.PLAN_PRICING_STATUS == "VERIFIED"
    assert RP.PLAN_PRICING["20K"]["USD_PER_MONTH"] == 30
    assert RP.PLAN_PRICING["100K"]["USD_PER_MONTH"] == 59
    assert RP.PLAN_PRICING["5M"]["USD_PER_MONTH"] == 119
    assert RP.PLAN_PRICING["15M"]["USD_PER_MONTH"] == 249
    assert "snapshot, not a constant" in RP.PRICES_ARE_NOT_PERMANENT


def test_the_billable_unit_is_not_the_event():
    assert "SPORT_KEY" in RP.BILLABLE_UNIT
    assert "REQUESTED_SNAPSHOT_TIME" in RP.BILLABLE_UNIT
    assert "EVENT" not in RP.BILLABLE_UNIT
    assert RP.ONE_REQUEST_MAY_COVER_MANY_EVENTS is True


def test_events_sharing_a_kickoff_share_one_request():
    """Five matches at 15:00 in one league cost ONE request per snapshot."""
    evs = [{"DATE": "2026-08-29", "TIME": "15:00", "VENUE_LEAGUE": "epl"}
           for _ in range(5)]
    p = RP.plan_requests(evs, horizons_minutes=(120,),
                         markets=("h2h",), regions=("uk",))
    assert p["DEDUPED_API_REQUESTS"] == 1
    assert p["NAIVE_EVENT_MULTIPLICATION_REQUESTS"] == 5
    assert p["DEDUPLICATION_SAVINGS_PCT"] == pytest.approx(80.0)


def test_events_in_different_leagues_do_not_share_a_request():
    evs = [{"DATE": "2026-08-29", "TIME": "15:00", "VENUE_LEAGUE": lg}
           for lg in ("epl", "lal", "sea")]
    p = RP.plan_requests(evs, horizons_minutes=(120,))
    assert p["DEDUPED_API_REQUESTS"] == 3


def test_a_dense_grid_collides_across_different_kickoffs():
    """A 15:00 game's T-2H is the same request as a 13:00 game's T-0."""
    evs = [{"DATE": "2026-08-29", "TIME": "13:00", "VENUE_LEAGUE": "epl"},
           {"DATE": "2026-08-29", "TIME": "15:00", "VENUE_LEAGUE": "epl"}]
    p = RP.plan_requests(evs, dense_window_minutes=120, step_minutes=5)
    assert p["NAIVE_EVENT_MULTIPLICATION_REQUESTS"] == 50
    assert p["DEDUPED_API_REQUESTS"] < 50


def test_credits_are_ten_times_markets_times_regions():
    evs = [{"DATE": "2026-08-29", "TIME": "15:00", "VENUE_LEAGUE": "epl"}]
    p = RP.plan_requests(evs, horizons_minutes=(120,),
                         markets=("h2h", "spreads", "totals"),
                         regions=("uk", "eu"))
    assert p["CREDITS_PER_REQUEST"] == 10 * 3 * 2
    assert p["DEDUPED_API_REQUEST_CREDITS"] == 60


def test_an_event_without_a_kickoff_is_refused_and_counted():
    evs = [{"DATE": "2026-08-29", "TIME": None, "VENUE_LEAGUE": "epl"},
           {"DATE": "2026-08-29", "TIME": "15:00", "VENUE_LEAGUE": "epl"}]
    p = RP.plan_requests(evs, horizons_minutes=(120,))
    assert p["EVENTS_REFUSED_NO_KICKOFF"] == 1
    assert p["EVENTS_PLANNED"] == 1


def test_both_naive_and_deduped_are_reported_side_by_side():
    evs = [{"DATE": "2026-08-29", "TIME": "15:00", "VENUE_LEAGUE": "epl"}] * 4
    p = RP.plan_requests(evs, horizons_minutes=(120, 15))
    assert p["NAIVE_EVENT_MULTIPLICATION_CREDITS"] > \
        p["DEDUPED_API_REQUEST_CREDITS"]


# --- Sections 3, 5, 6. Two experiments; a narrow phase 1. ------------------

def test_static_and_dense_are_separate_experiments():
    assert RP.STATIC_QUESTION != RP.DENSE_QUESTION
    assert "cannot estimate" in RP.WHY_THEY_ARE_SEPARATE


def test_phase_one_is_h2h_and_one_region():
    assert RP.PHASE_1_MARKETS == ("h2h",)
    assert RP.PHASE_1_REGIONS_COUNT == 1
    assert "sixth of the price" in RP.WHY_PHASE_1_IS_NARROW


def test_the_region_choice_is_provisional_and_says_how_to_settle_it():
    assert RP.REGION_RECOMMENDATION_STATUS == "PROVISIONAL_NOT_MEASURED"
    assert RP.BOOKMAKERS_IN_REGION == "NOT_IDENTIFIED"
    assert "BEFORE spending historical credits" in RP.HOW_TO_SETTLE_IT_CHEAPLY
    assert "doubles every historical request" in \
        RP.DO_NOT_BUY_TWO_REGIONS_BY_DEFAULT


def test_the_recommended_plan_is_the_smallest_tier_that_fits():
    assert RP.recommend_plan(13_390)["RECOMMENDED_PLAN"] == "20K"
    assert RP.recommend_plan(70_810)["RECOMMENDED_PLAN"] == "100K"
    assert RP.recommend_plan(111_650)["RECOMMENDED_PLAN"] == "5M"


def test_the_recommendation_carries_a_real_price():
    r = RP.recommend_plan(13_390)
    assert r["ESTIMATED_COST_USD_PER_MONTH"] == 30
    assert r["PRICING_STATUS"] == "VERIFIED"


# --- Section 11. Level and change are different signals. -------------------

def _series():
    base = datetime.datetime(2026, 8, 29, 13, 0, tzinfo=datetime.timezone.utc)
    out = {}
    for e in range(10):
        rows = []
        for i in range(25):
            t = base + datetime.timedelta(minutes=5 * i)
            rows.append({"T": t.isoformat(), "P_EXTERNAL": 0.50 + 0.001 * i,
                         "P_POLY": 0.48 + 0.001 * i})
        out["e%d" % e] = rows
    return out


def test_level_and_change_are_built_separately():
    rows, _ = LL.change_rows(_series())
    r = rows[10]
    assert r["LEVEL_DISAGREEMENT"] == pytest.approx(0.02, abs=1e-9)
    assert r["EXTERNAL_MOVE_5M"] == pytest.approx(0.001, abs=1e-9)
    assert LL.LEVEL_SIGNAL != LL.CHANGE_SIGNAL
    assert "constant offset is not a lead" in LL.TWO_SIGNALS_NOT_ONE


def test_forward_points_are_taken_only_where_they_exist():
    rows, missing = LL.change_rows(_series())
    assert any(k.startswith("NO_POLY_FORWARD_") for k in missing)
    assert all(r.get("POLY_MOVE_NEXT_5M") is not None for r in rows[:5])


def test_no_interpolation_is_used_for_forward_points():
    """A gap wider than the tolerance yields None, never a filled value."""
    base = datetime.datetime(2026, 8, 29, 13, 0, tzinfo=datetime.timezone.utc)
    s = {"e": [{"T": base.isoformat(), "P_EXTERNAL": 0.5, "P_POLY": 0.5},
               {"T": (base + datetime.timedelta(minutes=45)).isoformat(),
                "P_EXTERNAL": 0.6, "P_POLY": 0.6}]}
    rows, _ = LL.change_rows(s)
    assert all(r.get("POLY_MOVE_NEXT_5M") is None for r in rows)


def test_level_vs_change_reports_both_signals():
    rows, _ = LL.change_rows(_series())
    out = LL.level_vs_change(rows, draws=100)
    assert LL.LEVEL_SIGNAL in out["BY_SIGNAL"]
    assert "EXTERNAL_MOVE_5M" in out["BY_SIGNAL"]


# --- Section 12. Ordering, and the resolution limit. -----------------------

def test_the_resolution_limit_is_declared():
    assert LL.RESOLUTION_LIMIT_MINUTES == 5
    assert "UNRESOLVABLE" in LL.RESOLUTION_LIMITATION
    assert "upper bound on shared news" in LL.RESOLUTION_LIMITATION


def test_ordering_classifies_and_admits_what_it_cannot_see():
    out = LL.ordering(_series())
    assert set(LL.ORDERING_FEATURES) >= {"EXTERNAL_MOVE_BEFORE_POLY",
                                         "SIMULTANEOUS_MOVE",
                                         "TIME_TO_CONVERGENCE"}
    assert out["RESOLUTION_LIMIT_MINUTES"] == 5
    assert "do not close it" in out["COMMON_INFORMATION_CAVEAT"]


# --- Section 13. The lead/lag power model. ---------------------------------

def test_this_is_explicitly_not_the_settlement_ladder():
    assert LP.THIS_IS_NOT_THE_SETTLEMENT_LADDER is True
    assert "4,654" in LP.DO_NOT_USE_THE_SETTLEMENT_LADDER_HERE
    assert LP.THE_TRAP == "5,000 TIME POINTS ARE NOT 5,000 INDEPENDENT EVENTS"


def test_the_design_effect_grows_with_icc_and_repeats():
    assert LP.design_effect(25, 0.0) == pytest.approx(1.0)
    assert LP.design_effect(25, 0.05) == pytest.approx(2.2)
    assert LP.design_effect(25, 0.5) == pytest.approx(13.0)


def test_effective_n_collapses_to_events_when_icc_is_one():
    assert LP.effective_n(100, 25, 1.0) == pytest.approx(100.0)
    assert LP.effective_n(100, 25, 0.0) == pytest.approx(2500.0)


def test_more_clustering_demands_more_events():
    low = LP.events_required(0.05, 25, 0.05)
    high = LP.events_required(0.05, 25, 0.50)
    assert high > low * 3


def test_a_larger_target_correlation_needs_fewer_events():
    assert LP.events_required(0.20, 25, 0.10) < LP.events_required(0.05, 25, 0.10)


def test_the_icc_is_measured_from_data_not_assumed():
    assert "NOT_IDENTIFIED" in LP.ICC_IS_UNMEASURED_UNTIL_DATA_EXISTS
    assert "must not be quoted" in LP.ILLUSTRATIVE_ONLY


def test_icc_detects_clustering_when_it_is_present():
    clustered = {"a": [1.0, 1.1, 0.9], "b": [-1.0, -1.1, -0.9],
                 "c": [2.0, 2.1, 1.9]}
    spread = {"a": [1.0, -1.0, 2.0], "b": [1.1, -1.1, 2.1],
              "c": [0.9, -0.9, 1.9]}
    assert LP.icc(clustered) > LP.icc(spread)


def test_the_ladder_from_rows_reports_how_far_naive_counting_overstates():
    rows = []
    for e in range(12):
        for i in range(20):
            rows.append({"EVENT_KEY": "e%d" % e,
                         "EXTERNAL_DISAGREEMENT": 0.01 * (e - 6) + 0.001 * i,
                         "POLY_CHANGE_5M": 0.005 * (e - 6) + 0.0005 * i})
    out = LP.ladder_from_rows(rows)
    assert out["STATUS"] == "MEASURED"
    assert out["EVENTS"] == 12
    assert out["OBSERVATIONS"] == 240
    assert out["NAIVE_OVERSTATES_BY"] > 1.0
