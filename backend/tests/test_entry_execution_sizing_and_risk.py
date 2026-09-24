"""THE THREE PLACEHOLDERS, AND WHAT REPLACED THEM.

`size=1.0` and `risk={"permitted": True}` were inputs the scheduled loop
made up. These tests pin the properties that make their replacements not
placeholders: the fill probability is an OBSERVATION that can be below 1
and can be absent, the size comes from the frozen policy, and the risk
verdict comes from the engine and can refuse.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_risk_engine as risk
from sportsassets import shadow
from sportsassets import shadow_bettor_sizing as sizing


def _fee(qty, price, maker=False):
    return 0.016 * float(qty)


def _ladder(levels):
    return {"ok": True, "levels": [{"acquisition_price": p, "qty": q}
                                   for p, q in levels]}


# ── the execution estimate ───────────────────────────────────────────

def test_coverage_below_one_is_measured_not_assumed():
    """A thin ladder yields a coverage BELOW 1, computed from the levels
    actually shown -- which is the whole difference between an estimate and
    a default."""
    est = EX.estimate(ladder=_ladder([(0.62, 400.0), (0.64, 300.0)]),
                      fair_value=0.72, fee_fn=_fee, observation_age_s=2.5)
    assert est["ok"] is True, est
    assert 0.0 < est["p_fill"] < 1.0, est["p_fill"]
    assert est["size"] == pytest.approx(700.0)
    # THREE PRICES, AND THEY ARE NOT ONE PRICE. The submitted limit is the
    # break-even the belief implies; the vwap is what the walk actually
    # cost; the best level is where it started. An order does not fill
    # above its own limit, so every level taken is at or inside it.
    assert est["submitted_limit"] == est["limit_price"] == pytest.approx(
        0.72 - 0.016)
    assert 0.62 < est["vwap"] < 0.64, est["vwap"]
    assert est["best_acquisition_price"] == 0.62
    assert est["levels_taken"] == [
        {"price": 0.62, "qty": 400.0, "cost": 248.0,
         "level_qty_displayed": 400.0},
        {"price": 0.64, "qty": 300.0, "cost": 192.0,
         "level_qty_displayed": 300.0}]
    assert all(lv["price"] <= est["submitted_limit"]
               for lv in est["levels_taken"])
    assert est["vwap_is"].startswith("THE_REALISED_COST")
    assert est["limit_price_is"] == "THE_SUBMITTED_BREAK_EVEN_LIMIT"
    # THE SHORTFALL IS IN DOLLARS, because the intent is in dollars: the
    # ladder inside break-even held $440 of the $1,000 intended.
    assert est["unfilled_notional_usd"] > 0
    assert est["sizing"]["status"] == "LIQUIDITY_LIMITED"


def test_it_is_labelled_an_observation_and_names_the_engine_not_used():
    est = EX.estimate(ladder=_ladder([(0.62, 400.0)]), fair_value=0.72,
                      fee_fn=_fee, observation_age_s=2.5)
    assert est["basis"] == EX.MARKETABLE_BASIS
    assert est["is_forecast"] is False
    assert est["crossing"] is True
    assert est["resting_basis_not_used"] == "PRINT_THROUGH_WITH_QUEUE_SHARE_V1"
    assert est["execution_class"] == shadow.MARKETABLE_RECONSTRUCTED
    # THE LATENCY GAP IS REPORTED, NOT DISCOUNTED. A haircut invented here
    # would be the invented estimate the directive forbids.
    assert est["observation_age_s"] == 2.5
    assert est["latency_gap_is_reported_not_adjusted"] is True
    assert est["displayed_depth_is_not_a_queue"] is True


def test_a_full_ladder_gives_exactly_one_and_says_why_that_is_not_a_default():
    """1.0 is reachable, and only by the ladder covering the whole intended
    size inside break-even. The distinction from a default is that the
    unreadable case below yields no number at all."""
    est = EX.estimate(ladder=_ladder([(0.50, 100000.0)]), fair_value=0.72,
                      fee_fn=_fee)
    assert est["p_fill"] == 1.0
    assert est["sizing"]["status"] == "FULLY_SUPPORTED"
    assert est["sizing"]["intendedNotionalUsd"] == float(
        sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD)


def test_an_unreadable_ladder_produces_no_number_at_all():
    for bad in ({"ok": False, "levels": []}, {"ok": True, "levels": []},
                None):
        est = EX.estimate(ladder=bad, fair_value=0.72, fee_fn=_fee)
        assert est["ok"] is False
        assert "p_fill" not in est
        assert EX.R_NO_LADDER in est["refusals"]


def test_a_book_priced_beyond_break_even_is_a_different_refusal():
    est = EX.estimate(ladder=_ladder([(0.95, 500.0)]), fair_value=0.72,
                      fee_fn=_fee)
    assert est["ok"] is False
    assert est["refusals"] == [EX.R_NOTHING_INSIDE_LIMIT]
    assert EX.R_NO_LADDER not in est["refusals"]


def test_no_valuation_and_no_fee_function_each_refuse_by_name():
    assert EX.R_NO_FAIR_VALUE in EX.estimate(
        ladder=_ladder([(0.5, 10.0)]), fair_value=None,
        fee_fn=_fee)["refusals"]
    assert EX.R_NO_FEE_FN in EX.estimate(
        ladder=_ladder([(0.5, 10.0)]), fair_value=0.7,
        fee_fn=None)["refusals"]


def test_the_limit_comes_from_the_belief_not_from_the_book():
    """Two very different books, the same valuation, the same limit."""
    a = EX.estimate(ladder=_ladder([(0.10, 10.0)]), fair_value=0.72,
                    fee_fn=_fee)
    b = EX.estimate(ladder=_ladder([(0.10, 900000.0)]), fair_value=0.72,
                    fee_fn=_fee)
    assert a["break_even_limit"] == b["break_even_limit"]
    assert a["break_even_limit"] == pytest.approx(0.72 - 0.016)


def test_the_ladder_is_handed_over_without_re_denominating_it():
    """`acquisition_price` is already cost space, so acquiring is a BUY at
    that price and the levels go under asks unchanged. Converting twice is
    the mis-denomination the exit-price defect was."""
    book = EX.ladder_as_arrival_book(_ladder([(0.62, 5.0), (0.64, 7.0)]))
    assert book["asks"] == [{"price": 0.62, "qty": 5.0},
                            {"price": 0.64, "qty": 7.0}]
    assert book["bids"] == []


# ── the risk verdict ─────────────────────────────────────────────────

def test_every_limit_is_a_stated_multiple_of_the_frozen_notional():
    d = EX.declaration()
    std = float(sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD)
    assert d["derivedFrom"]["standardNotionalUsd"] == std
    for rail, mult in d["derivedFrom"]["multiples"].items():
        assert d["limits"][rail] == std * mult, rail
        assert d["derivedFrom"]["why"][rail]
    assert d["realOrderSubmissionEnabled"] is False
    assert d["realCapitalAtRisk"] == 0
    assert "not permission to trade funded capital" in \
        d["isNotACapitalAuthorization"]


def test_every_declared_rail_has_a_limit_so_none_is_silently_unevaluable():
    assert EX.UNDECLARED_RAILS == ()
    assert set(EX.PREDECLARED_LIMITS) == set(risk.RAILS)


def test_an_unread_book_blocks_rather_than_reading_as_empty():
    ex = EX.exposure_from_rows(None, condition_id="c", event_key="e",
                               proposed_cost_usd=100.0, proposed_qty=100.0)
    assert ex["refusals"] == [EX.R_BOOK_NOT_READ]
    assert ex["observed"] == {}
    v = EX.verdict("TAKE_YES", observed=ex["observed"],
                   state={k: True for k in risk.STATE_GATES})
    assert v["permitted"] is False
    assert len(v["railsNotPassed"]) == len(risk.RAILS)


def test_the_proposed_position_is_inside_every_measurement():
    """A rail that compared only what is already held would permit the
    trade that breaches it."""
    ex = EX.exposure_from_rows([], condition_id="c", event_key="e",
                               proposed_cost_usd=250.0, proposed_qty=400.0,
                               now=0.0)
    assert ex["observed"]["MAX_MARKET_EXPOSURE"] == 250.0
    assert ex["observed"]["MAX_EVENT_EXPOSURE"] == 250.0
    assert ex["observed"]["MAX_CAPITAL_DEPLOYED"] == 250.0
    assert ex["observed"]["MAX_RESIDUAL_INVENTORY"] == 400.0
    assert ex["includesTheProposedPosition"] is True


def test_correlated_exposure_assumes_the_worst_and_therefore_only_refuses():
    """No correlation structure is known. Every open position counts in
    full, which can refuse a low-correlation entry and can never permit a
    high-correlation one."""
    rows = [{"condition_id": "other", "event_key": "other-evt",
             "cost_usd": 900.0, "qty": 1000.0}]
    ex = EX.exposure_from_rows(rows, condition_id="c", event_key="e",
                               proposed_cost_usd=250.0, proposed_qty=400.0,
                               now=0.0)
    # Different market, different event, so those two rails see only ours.
    assert ex["observed"]["MAX_MARKET_EXPOSURE"] == 250.0
    assert ex["observed"]["MAX_EVENT_EXPOSURE"] == 250.0
    # But the correlation rail sees the whole book.
    assert ex["observed"]["MAX_CORRELATED_EXPOSURE"] == 1150.0
    v = EX.verdict("TAKE_YES", observed=ex["observed"],
                   state={k: True for k in risk.STATE_GATES})
    assert v["permitted"] is False
    assert "MAX_CORRELATED_EXPOSURE" in v["railsNotPassed"]


def test_an_unmarked_unsettled_position_counts_as_a_total_loss():
    rows = [{"condition_id": "x", "event_key": "y", "cost_usd": 400.0,
             "qty": 500.0}]
    ex = EX.exposure_from_rows(rows, condition_id="c", event_key="e",
                               proposed_cost_usd=100.0, proposed_qty=100.0,
                               now=0.0)
    assert ex["observed"]["MAX_DRAWDOWN"] == 500.0
    assert ex["positions_counted_as_total_loss"] == 2
    # A MARK CHANGES IT, and only downward from the worst case.
    marked = EX.exposure_from_rows(
        [dict(rows[0], marked_value_usd=380.0)], condition_id="c",
        event_key="e", proposed_cost_usd=100.0, proposed_qty=100.0, now=0.0)
    assert marked["observed"]["MAX_DRAWDOWN"] == 120.0


def test_capital_hours_without_an_instant_is_not_measured_as_zero():
    ex = EX.exposure_from_rows([], condition_id="c", event_key="e",
                               proposed_cost_usd=100.0, proposed_qty=100.0,
                               now=None)
    assert ex["observed"]["MAX_CAPITAL_HOURS"] is None
    v = EX.verdict("TAKE_YES", observed=ex["observed"],
                   state={k: True for k in risk.STATE_GATES})
    assert "MAX_CAPITAL_HOURS" in v["railsNotPassed"]
    assert v["permitted"] is False


def test_capital_hours_accrue_from_how_long_the_book_has_been_held():
    rows = [{"condition_id": "x", "event_key": "y", "cost_usd": 100.0,
             "qty": 100.0, "opened_at": 0.0}]
    ex = EX.exposure_from_rows(rows, condition_id="c", event_key="e",
                               proposed_cost_usd=1.0, proposed_qty=1.0,
                               now=7200.0)
    assert ex["observed"]["MAX_CAPITAL_HOURS"] == pytest.approx(200.0)
    assert "exit rule" in ex["capitalHoursIsAccruedNotForecast"]


# ── the state gates ──────────────────────────────────────────────────

def test_the_calibration_gate_is_the_one_that_stands():
    s = EX.state_from_evidence(freshness={"fresh": True},
                               settlement={"compatibility": "COMPATIBLE"},
                               probability=0.72, calibration=None)
    assert s["blocking"] == ["MODEL_TRUST_DRIFT"]
    assert EX.R_NO_CALIBRATION in s["why"]["MODEL_TRUST_DRIFT"]
    assert "does not lift by argument" in s["why"]["MODEL_TRUST_DRIFT"]


def test_a_measured_calibration_is_compared_and_not_just_accepted():
    bad = EX.state_from_evidence(
        freshness={"fresh": True},
        settlement={"compatibility": "COMPATIBLE"}, probability=0.72,
        calibration={"measured": True, "within_tolerance": False,
                     "why": "brier 0.31 beyond 0.24"})
    assert bad["state"]["MODEL_TRUST_DRIFT"] is False
    assert bad["blocking"] == ["MODEL_TRUST_DRIFT"]


def test_unknown_settlement_blocks_and_incompatible_settlement_fails():
    unknown = EX.state_from_evidence(settlement={"compatibility": "UNKNOWN"})
    assert unknown["state"]["UNRESOLVED_SETTLEMENT_SEMANTICS"] is None
    bad = EX.state_from_evidence(
        settlement={"compatibility": "INCOMPATIBLE",
                    "mismatched_conditions": ["C_CALLED_FINAL"]})
    assert bad["state"]["UNRESOLVED_SETTLEMENT_SEMANTICS"] is False
    assert "C_CALLED_FINAL" in bad["why"]["UNRESOLVED_SETTLEMENT_SEMANTICS"]


def test_an_unmeasured_clock_leaves_freshness_unknown():
    assert EX.state_from_evidence(freshness=None)["state"]["STALE_DATA"] \
        is None
    assert EX.state_from_evidence(
        freshness={"fresh": False})["state"]["STALE_DATA"] is False


def test_a_probability_outside_the_declared_support_is_out_of_distribution():
    for p, expect in ((0.5, True), (0.995, False), (0.001, False)):
        got = EX.state_from_evidence(probability=p)
        assert got["state"]["OUT_OF_DISTRIBUTION"] is expect, p


# ── the defect the wiring exposed ────────────────────────────────────

def test_an_action_outside_the_vocabulary_is_refused_not_permitted():
    """`BUY` -- the entry gate's own action name -- used to come back
    permitted, direction EXPOSURE_NEUTRAL, with no rail evaluated."""
    for bad in ("BUY", "buy_yes", "TAKE_EVERYTHING", ""):
        v = risk.evaluate(bad, observed={}, state={})
        assert v["permitted"] is False, bad
        assert v["refusal"] == risk.R_ACTION_UNKNOWN, bad
    # And the vocabulary's own names still resolve.
    assert risk.evaluate("TAKE_YES")["direction"] == "EXPOSURE_INCREASING"
    assert risk.evaluate("DIRECT_EXIT")["permitted"] is True


def test_a_limit_naming_an_unknown_rail_refuses_the_whole_set():
    v = risk.evaluate("TAKE_YES", observed={}, state={},
                      limits={"MAX_MARKET_EXPOSURES": 10.0})
    assert v["permitted"] is False
    assert v["refusal"] == risk.R_LIMIT_NAME_UNKNOWN
    assert v["limitsNamingUnknownRails"] == ["MAX_MARKET_EXPOSURES"]


def test_the_module_default_still_carries_no_numbers():
    """The lane's limits live with the lane. Nothing was written into the
    shared engine, so a lane that declares nothing is still refused."""
    for spec in risk.RAILS.values():
        assert spec["limit"] == risk.NOT_PREDECLARED
    v = risk.evaluate("TAKE_YES", observed={"MAX_MARKET_EXPOSURE": 1.0})
    assert v["permitted"] is False
    row = [r for r in v["rails"] if r["rail"] == "MAX_MARKET_EXPOSURE"][0]
    assert row["limitSource"] == "MODULE_DEFAULT"
    assert row["verdict"] == risk.NOT_EVALUABLE


def test_a_lane_supplied_limit_is_marked_as_the_lanes_own():
    v = EX.verdict("TAKE_YES", observed={"MAX_MARKET_EXPOSURE": 1.0},
                   state={k: True for k in risk.STATE_GATES})
    row = [r for r in v["rails"] if r["rail"] == "MAX_MARKET_EXPOSURE"][0]
    assert row["limitSource"] == "LANE_PREDECLARED"
    assert row["verdict"] == risk.PASS
    assert v["limitsSha"] == EX.LIMITS_SHA


def test_a_settled_position_stops_occupying_capital_but_keeps_its_loss():
    """Counting a settled position as open exposure is not conservatism, it
    is a wrong measurement: the basis came back. Left in the sums it would
    accumulate forever and refuse every entry on a book that is flat."""
    rows = [{"condition_id": "old", "event_key": "old-evt",
             "cost_usd": 900.0, "qty": 1000.0, "opened_at": 0.0,
             "realized_net_usd": -40.0}]
    ex = EX.exposure_from_rows(rows, condition_id="c", event_key="e",
                               proposed_cost_usd=250.0, proposed_qty=400.0,
                               now=3600.0)
    assert ex["settled_positions_excluded_from_exposure"] == 1
    # Exposure is the proposed position alone.
    assert ex["observed"]["MAX_CAPITAL_DEPLOYED"] == 250.0
    assert ex["observed"]["MAX_CORRELATED_EXPOSURE"] == 250.0
    assert ex["observed"]["MAX_RESIDUAL_INVENTORY"] == 400.0
    # THE LOSS STILL COUNTS, on the rail that governs realised damage,
    # and so do the capital-hours it actually consumed while it was open.
    assert ex["observed"]["MAX_DRAWDOWN"] == pytest.approx(40.0 + 250.0)
    assert ex["observed"]["MAX_CAPITAL_HOURS"] == pytest.approx(900.0)


# ── the decision clock, and re-ageing at it ──────────────────────────

def test_the_venue_book_is_reaged_at_the_decision_not_at_the_read():
    """THE HALF-FIX THIS COMPLETES. Moving the decision instant after the
    venue, rules and fixture reads is only half the repair: the book's age
    was still the age it had when it was READ, so a decision taken forty
    seconds later inherited "2 s fresh"."""
    from sportsassets.workers import ext_pinnacle_loop as loop

    # read at t=100 against a venue stamp of 98; decision at t=140.
    got = loop._entry_freshness({"observed_at": 130.0},
                                {"age_s": 2.0, "venue_ts": 98.0,
                                 "age_basis": "VENUE_TRANSACT_TIME"},
                                140.0)
    assert got["venue_age_at_read_s"] == 2.0
    assert got["venue_age_s"] == 42.0
    assert got["venue_age_basis"] == (
        "VENUE_TRANSACT_TIME_REAGED_AT_THE_DECISION")
    assert got["both_reaged_at_the_decision"] is True
    # AND IT REFUSES: 42 s is beyond the venue's own 30 s bound, which the
    # read-time age would have hidden.
    assert got["fresh"] is False


def test_without_the_venues_own_clock_freshness_stays_unmeasured():
    """There is nothing to re-age from, and our read time is not a
    substitute: using it would make every quote fresh by construction."""
    from sportsassets.workers import ext_pinnacle_loop as loop

    got = loop._entry_freshness({"observed_at": 130.0},
                                {"age_s": 2.0, "venue_ts": None,
                                 "age_basis": "VENUE_CLOCK_NOT_PROVIDED"},
                                140.0)
    assert got["venue_age_s"] is None
    assert got["fresh"] is None
    assert got["venue_age_basis"] == "VENUE_CLOCK_NOT_PROVIDED"
