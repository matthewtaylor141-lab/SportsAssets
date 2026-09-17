"""Tests for MARKET_SURFACE_V1_ASOF and the leave-one-out circularity guard."""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ev_core_surface as SURF  # noqa: E402
import ev_core_surface_asof as A  # noqa: E402

BASE = datetime.datetime(2026, 8, 30, 12, 0, 0, tzinfo=datetime.timezone.utc)


def at(minutes):
    return (BASE + datetime.timedelta(minutes=minutes)).isoformat().replace(
        "+00:00", "Z")


def row(slug, outcome, price, minutes, resolved_at=None, settled=None):
    return {"MARKET_SLUG": slug, "OUTCOME": outcome, "P_VENUE_TRADE": price,
            "AS_OF": at(minutes), "EVENT_KEY": "epl-che-bri-2026-08-30",
            "RESOLVED_AT": resolved_at or at(600),
            "SETTLED_YES": settled}


def _world(minutes=0):
    """A fixture's linked prices, all quoted at the same moment by default."""
    return [
        row("epl-che-bri-2026-08-30-total-1pt5", "Over", 0.78, minutes),
        row("epl-che-bri-2026-08-30-total-2pt5", "Over", 0.52, minutes),
        row("epl-che-bri-2026-08-30-total-3pt5", "Over", 0.28, minutes),
        row("epl-che-bri-2026-08-30-draw", "Yes", 0.24, minutes),
        row("epl-che-bri-2026-08-30-btts", "Yes", 0.55, minutes),
        row("epl-che-bri-2026-08-30-exact-score-1-1", "Yes", 0.11, minutes),
        row("epl-che-bri-2026-08-30-exact-score-2-1", "Yes", 0.09, minutes),
    ]


# ---------------------------------------------------------------------------
# the as-of snapshot
# ---------------------------------------------------------------------------


def test_nothing_quoted_after_t_reaches_the_fit():
    rows = _world(0) + [row("epl-che-bri-2026-08-30-total-2pt5", "Over",
                            0.99, 60)]
    snap, rep = A.snapshot(rows, at(30))
    assert rep["DROPPED"]["AFTER_T"] == 1
    prices = {(r["MARKET_SLUG"], r["OUTCOME"]): r["P_VENUE_TRADE"]
              for r in snap}
    assert prices[("epl-che-bri-2026-08-30-total-2pt5", "Over")] == 0.52


def test_the_latest_observation_at_or_before_t_is_the_one_used():
    rows = [row("epl-che-bri-2026-08-30-draw", "Yes", 0.20, 0),
            row("epl-che-bri-2026-08-30-draw", "Yes", 0.26, 20),
            row("epl-che-bri-2026-08-30-draw", "Yes", 0.31, 50)]
    snap, _ = A.snapshot(rows, at(30))
    assert len(snap) == 1 and snap[0]["P_VENUE_TRADE"] == 0.26


def test_the_two_sides_of_one_market_are_separate_quotes():
    rows = [row("epl-che-bri-2026-08-30-total-2pt5", "Over", 0.52, 0),
            row("epl-che-bri-2026-08-30-total-2pt5", "Under", 0.48, 0)]
    snap, rep = A.snapshot(rows, at(10))
    assert rep["CONTRACTS"] == 2


def test_a_quote_older_than_the_age_limit_is_dropped_not_used_stale():
    rows = _world(0)
    snap, rep = A.snapshot(rows, at(600), max_quote_age_s=3600)
    assert snap == [] and rep["DROPPED"]["TOO_STALE"] == len(rows)


def test_quote_age_travels_with_every_input_and_is_summarised():
    rows = (_world(0)[:3] + _world(30)[3:])
    snap, rep = A.snapshot(rows, at(60))
    assert all("QUOTE_AGE_S" in r for r in snap)
    assert rep["QUOTE_AGE_MAX_S"] == 3600.0
    assert rep["QUOTE_AGE_MEDIAN_S"] in (1800.0, 3600.0)
    assert rep["QUOTE_AGE_P90_S"] >= rep["QUOTE_AGE_MEDIAN_S"]


def test_a_surface_fitted_as_of_reports_its_snapshot_and_stays_not_alpha():
    out = A.surface_asof(_world(0), at(10), "che", "bri")
    assert out["OBJECT"] == "MARKET_SURFACE_V1_ASOF"
    assert out["AS_OF"] == at(10)
    assert out["SNAPSHOT"]["CONTRACTS"] == 7
    assert out["IS_INDEPENDENT_ALPHA"] is False


# ---------------------------------------------------------------------------
# horizons and anchors
# ---------------------------------------------------------------------------


def test_a_horizon_shorter_than_its_anchors_uncertainty_is_refused():
    ok, why = A.horizon_admissible(5 * 60, A.ANCHOR_RESOLVED_AT)
    assert ok is False and why == A.HORIZON_REFUSED_ANCHOR_TOO_UNCERTAIN
    ok, _ = A.horizon_admissible(24 * 3600, A.ANCHOR_RESOLVED_AT)
    assert ok is True


def test_the_settlement_anchor_refuses_every_short_horizon():
    out = A.surfaces_by_horizon(_world(0), anchor=A.ANCHOR_RESOLVED_AT,
                                home_code="che", away_code="bri")
    for h in ("T-1H", "T-15M", "T-5M"):
        assert out["HORIZONS"][h]["STATUS"] == "REFUSED"
        assert out["HORIZONS"][h]["REASON"] == \
            A.HORIZON_REFUSED_ANCHOR_TOO_UNCERTAIN


def test_the_public_kickoff_anchor_is_less_uncertain_but_still_not_exact():
    assert A.ANCHOR_UNCERTAINTY_S[A.ANCHOR_PUBLIC_KICKOFF] < \
        A.ANCHOR_UNCERTAINTY_S[A.ANCHOR_RESOLVED_AT]
    assert A.KICKOFF_TIMEZONE_STATUS == "NOT_IDENTIFIED"
    ok, why = A.horizon_admissible(3600, A.ANCHOR_PUBLIC_KICKOFF)
    assert ok is False and why == A.HORIZON_REFUSED_ANCHOR_TOO_UNCERTAIN


def test_an_event_with_no_anchor_refuses_every_horizon_by_name():
    rows = [dict(r, RESOLVED_AT=None) for r in _world(0)]
    out = A.surfaces_by_horizon(rows, anchor=A.ANCHOR_RESOLVED_AT)
    assert all(v["REASON"] == A.HORIZON_REFUSED_NO_ANCHOR
               for v in out["HORIZONS"].values())


def test_the_observation_anchor_needs_no_external_clock_and_claims_no_horizon():
    t, why = A.anchor_for(_world(0) + _world(40),
                          A.ANCHOR_OBSERVATION_QUANTILE)
    assert why == "OK" and t is not None
    assert A.ANCHOR_UNCERTAINTY_S[A.ANCHOR_OBSERVATION_QUANTILE] == 0
    assert "cannot be reported as a T-minus-anything" in \
        A.ANCHOR_OBSERVATION_QUANTILE_CARRIES_NO_HORIZON


def test_the_observation_anchor_lands_on_a_real_observation_time():
    rows = _world(0) + _world(40) + _world(80)
    t, _ = A.anchor_for(rows, A.ANCHOR_OBSERVATION_QUANTILE)
    assert t in {at(0), at(40), at(80)}


def test_a_live_surface_is_still_not_identified():
    assert A.LIVE_SURFACE_STATUS == "NOT_IDENTIFIED"
    assert "game state" in A.LIVE_SURFACE_WHY or "score, clock" in \
        A.LIVE_SURFACE_WHY


# ---------------------------------------------------------------------------
# families -- the ordering defect, caught the same way as before
# ---------------------------------------------------------------------------


def test_a_segment_family_is_never_filed_as_its_full_game_namesake():
    assert A.family_of("epl-a-b-2026-08-30-first-half-total-2pt5") == \
        "FIRST_HALF_TOTAL"
    assert A.family_of("epl-a-b-2026-08-30-total-2pt5") == "TOTAL"
    assert A.family_of("epl-a-b-2026-08-30-halftime-result-draw") == \
        "HALFTIME_RESULT"
    assert A.family_of("epl-a-b-2026-08-30-draw") == "DRAW"
    assert A.family_of("epl-a-b-2026-08-30-team-total-home-1pt5") == \
        "TEAM_TOTAL"
    assert A.family_of("epl-a-b-2026-08-30-corners-9pt5") == "OTHER"


# ---------------------------------------------------------------------------
# the circularity guard
# ---------------------------------------------------------------------------


def test_the_holdouts_run_and_report_all_three_residuals():
    rep = A.holdout_residuals(_world(0), at(10), "che", "bri")
    assert rep["STATUS"] == "MEASURED"
    assert rep["CONTRACTS"] >= 4
    for r in rep["RESIDUALS"]:
        assert "RESIDUAL_IN_FIT" in r
        assert "RESIDUAL_LOCO" in r
        assert "RESIDUAL_LOFO" in r


def test_leaving_the_family_out_removes_every_sibling_not_just_the_contract():
    rep = A.holdout_residuals(_world(0), at(10), "che", "bri")
    totals = [r for r in rep["RESIDUALS"] if r["FAMILY"] == "TOTAL"]
    assert totals, "the fixture has three totals lines"
    # three totals in, so a leave-one-family-out fit sees four contracts
    assert all(r["LOFO_CONTRACTS_REMAINING"] == 4 for r in totals)


def test_the_held_out_residual_is_not_the_in_fit_one():
    rep = A.holdout_residuals(_world(0), at(10), "che", "bri")
    moved = [r for r in rep["RESIDUALS"]
             if isinstance(r["RESIDUAL_LOFO"], float)
             and abs(r["RESIDUAL_LOFO"] - r["RESIDUAL_IN_FIT"]) > 1e-9]
    assert moved, "holding a family out must change its own re-price"


def test_too_few_contracts_refuses_rather_than_fitting():
    rep = A.holdout_residuals(_world(0)[:3], at(10), "che", "bri")
    assert rep["STATUS"] == "INSUFFICIENT_CONTRACTS"


def test_the_binding_holdout_is_named_and_it_is_the_family_one():
    rep = A.holdout_residuals(_world(0), at(10), "che", "bri")
    assert rep["BINDING_HOLDOUT"] == A.LOFO
    assert set(rep["HOLDOUTS"]) == {A.LOCO, A.LOFO}
    assert "near-duplicates" in rep["NO_RESIDUAL_IS_INFORMATIVE_UNTIL"]


def test_the_surface_never_claims_to_be_alpha():
    rep = A.holdout_residuals(_world(0), at(10), "che", "bri")
    assert rep["IS_INDEPENDENT_ALPHA"] is False
    assert A.IS_MARKET_DERIVED is True


# ---------------------------------------------------------------------------
# the settlement-direction read
# ---------------------------------------------------------------------------


def test_a_thin_family_says_too_few_rather_than_reporting_a_number():
    rep = A.holdout_residuals(_world(0), at(10), "che", "bri")
    for r in rep["RESIDUALS"]:
        r["SETTLED_YES"] = 1
    out = A.residual_predicts_settlement([rep])
    assert all(v.get("STATUS") == "TOO_FEW" for v in out["BY_FAMILY"].values())


def test_the_direction_read_is_not_offered_as_a_trading_result():
    out = A.residual_predicts_settlement([])
    assert "clear the spread" in out["THIS_IS_NOT_A_TRADING_RESULT"]
    assert out["IS_INDEPENDENT_ALPHA"] is False


def test_the_sign_convention_is_reported_explicitly():
    reps = []
    for i in range(12):
        reps.append({"STATUS": "MEASURED", "RESIDUALS": [
            {"FAMILY": "TOTAL", "RESIDUAL_LOFO": 0.05, "SETTLED_YES": 1},
            {"FAMILY": "TOTAL", "RESIDUAL_LOFO": -0.05, "SETTLED_YES": 0}]})
    out = A.residual_predicts_settlement(reps)
    t = out["BY_FAMILY"]["TOTAL"]
    assert t["SIGN_IS_RIGHT_WAY_ROUND"] is True
    assert t["DIFFERENCE"] > 0
