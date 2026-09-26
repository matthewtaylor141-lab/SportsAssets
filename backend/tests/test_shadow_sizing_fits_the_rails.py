"""Sizing asks the rails what is left, then spends at most that.

THE PRODUCTION DEFECT THESE PIN. Run 71 evaluated 142 candidates, five of
which priced positively, and every one of the five carried

    rails_failed [MAX_EVENT_EXPOSURE, MAX_MARKET_EXPOSURE,
                  MAX_RESIDUAL_INVENTORY, MAX_CORRELATED_EXPOSURE,
                  MAX_DRAWDOWN]

on a book holding NOTHING. That is not a portfolio that is full; it is
arithmetic that cannot be satisfied. A STANDARD-dollar budget reserved at
the break-even limit costs STANDARD * (limit / vwap), and `limit > vwap`
is what having an edge means -- so the dollar rails, which are STANDARD
times one, were breached by exactly the edge. `test_the_old_arithmetic_
was_unsatisfiable` states that as a property rather than as a story.

No limit is changed by the repair. The standard trade stays the ceiling
and the rails only ever reduce it.
"""

import math

import pytest

from sportsassets import bettor_entry_execution as X
from sportsassets import shadow_bettor_sizing as sizing


STANDARD = float(sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD)


def _ladder(price, qty):
    return {"ok": True,
            "levels": [{"acquisition_price": price, "qty": qty}]}


def _deep_ladder(price, qty=10_000_000.0):
    return _ladder(price, qty)


def _fee(qty=1.0, price=0.0, **_):
    """A flat 2% of notional, charged per contract. Sign-free."""
    return 0.02 * float(qty) * float(price)


def _empty_headroom(now=1_000_000.0):
    return X.headroom_from_rows([], condition_id="c1", event_key="e1",
                                now=now)


# ── 1 · THE PROPERTY THAT MADE ENTRY IMPOSSIBLE ──────────────────────

def test_the_old_arithmetic_was_unsatisfiable():
    """Reserving a STANDARD budget at the limit always breaks a STANDARD rail.

    Not a regression test on a number -- a proof over the whole price
    range that the un-capped path could only ever pass with zero edge.
    """
    breached = 0
    for price in (0.05, 0.15, 0.275, 0.315, 0.50, 0.75, 0.95):
        fv = price + 0.05                      # a real, positive edge
        est = X.estimate(ladder=_deep_ladder(price), fair_value=fv,
                         fee_fn=_fee, observation_age_s=1.0)
        assert est["ok"], est.get("why")
        reserved = est["size"] * est["worst_case_cost_per_contract"]
        # The reservation exceeds the dollar rail whenever there is edge.
        if reserved > X.PREDECLARED_LIMITS["MAX_EVENT_EXPOSURE"] + 1e-6:
            breached += 1
    assert breached == 7, (
        "every price should have breached the event rail before the "
        "repair; got %d of 7" % breached)


def test_the_contract_rail_was_unreachable_on_every_underdog():
    """A STANDARD budget buys more than 2000 contracts below $0.50."""
    limit = X.PREDECLARED_LIMITS["MAX_RESIDUAL_INVENTORY"]
    for price in (0.05, 0.15, 0.275, 0.315, 0.49):
        est = X.estimate(ladder=_deep_ladder(price),
                         fair_value=price + 0.05, fee_fn=_fee)
        assert est["size"] > limit, price


# ── 2 · HEADROOM IS THE SAME MEASUREMENT, ASKED WITH NOTHING PROPOSED ─

def test_headroom_on_an_empty_book_is_the_whole_limit():
    head = _empty_headroom()
    assert head["ok"], head
    assert head["limits"] == X.PREDECLARED_LIMITS
    for name, limit in X.PREDECLARED_LIMITS.items():
        assert head["headroom"][name] == pytest.approx(limit), name
    assert head["does_not_change_any_limit"] is True


def test_headroom_subtracts_what_the_book_already_holds():
    rows = [{"condition_id": "c1", "event_key": "e1", "cost_usd": 120.0,
             "qty": 300.0, "opened_at": 999_000.0,
             "marked_value_usd": 100.0}]
    head = X.headroom_from_rows(rows, condition_id="c1", event_key="e1",
                                now=1_000_000.0)
    assert head["ok"], head
    assert head["headroom"]["MAX_MARKET_EXPOSURE"] == pytest.approx(
        X.PREDECLARED_LIMITS["MAX_MARKET_EXPOSURE"] - 120.0)
    assert head["headroom"]["MAX_RESIDUAL_INVENTORY"] == pytest.approx(
        X.PREDECLARED_LIMITS["MAX_RESIDUAL_INVENTORY"] - 300.0)
    # Marked down by 20, so 20 of drawdown is used -- not the whole basis.
    assert head["headroom"]["MAX_DRAWDOWN"] == pytest.approx(
        X.PREDECLARED_LIMITS["MAX_DRAWDOWN"] - 20.0)


def test_an_unread_book_measures_no_headroom_and_blocks():
    head = X.headroom_from_rows(None, condition_id="c1", event_key="e1",
                                now=1.0)
    assert head["ok"] is False
    assert X.R_BOOK_NOT_READ in head["refusals"]
    cap = X.qty_cap_from_headroom(head, worst_case_cost_per_contract=0.3)
    assert cap["ok"] is False
    assert X.R_HEADROOM_NOT_MEASURED in cap["refusals"]


def test_no_observation_instant_leaves_capital_hours_unsizable():
    """A rail the proposal cannot move must be MEASURED, not assumed."""
    head = X.headroom_from_rows([], condition_id="c1", event_key="e1",
                                now=None)
    cap = X.qty_cap_from_headroom(head, worst_case_cost_per_contract=0.3)
    assert cap["ok"] is False
    assert X.R_HEADROOM_NOT_MEASURED in cap["refusals"]
    assert "MAX_CAPITAL_HOURS" in (cap.get("why") or "")


# ── 3 · THE CAP, AND WHICH RAIL BINDS ────────────────────────────────

def test_the_contract_rail_binds_on_a_cheap_contract():
    cap = X.qty_cap_from_headroom(_empty_headroom(),
                                  worst_case_cost_per_contract=0.286564)
    assert cap["ok"], cap
    # dollars: 1000 / 0.286564 = 3489.99 contracts; contracts rail: 2000.
    assert cap["binding_rail"] == "MAX_RESIDUAL_INVENTORY"
    assert cap["qty_cap"] == pytest.approx(
        X.PREDECLARED_LIMITS["MAX_RESIDUAL_INVENTORY"])


def test_a_dollar_rail_binds_on_an_expensive_contract():
    cap = X.qty_cap_from_headroom(_empty_headroom(),
                                  worst_case_cost_per_contract=0.716897)
    assert cap["ok"], cap
    assert cap["binding_rail"] in X.DOLLAR_SCALING_RAILS
    expected = X.PREDECLARED_LIMITS["MAX_EVENT_EXPOSURE"] / 0.716897
    assert cap["qty_cap"] == pytest.approx(math.floor(expected * 1e6) / 1e6)


def test_the_cap_is_floored_never_rounded_up():
    """Rounding up hands back a cap that breaches by a fraction."""
    cap = X.qty_cap_from_headroom(_empty_headroom(),
                                  worst_case_cost_per_contract=0.7)
    raw = X.PREDECLARED_LIMITS["MAX_EVENT_EXPOSURE"] / 0.7
    assert cap["qty_cap"] <= raw
    assert cap["qty_cap"] * 0.7 <= X.PREDECLARED_LIMITS["MAX_EVENT_EXPOSURE"]


def test_a_full_book_refuses_by_name_rather_than_sizing_to_dust():
    rows = [{"condition_id": "c1", "event_key": "e1",
             "cost_usd": STANDARD, "qty": 10.0, "opened_at": 999_000.0,
             "marked_value_usd": STANDARD}]
    head = X.headroom_from_rows(rows, condition_id="c1", event_key="e1",
                                now=1_000_000.0)
    cap = X.qty_cap_from_headroom(head, worst_case_cost_per_contract=0.3)
    assert cap["ok"] is False
    assert X.R_NO_HEADROOM in cap["refusals"]


def test_a_breached_non_scaling_rail_is_a_refusal_not_a_smaller_trade():
    """No quantity reduces capital-hours already accrued."""
    huge = X.PREDECLARED_LIMITS["MAX_CAPITAL_HOURS"] * 2.0
    rows = [{"condition_id": "other", "event_key": "other",
             "cost_usd": 1.0, "qty": 1.0,
             "opened_at": 0.0, "realized_net_usd": 0.0,
             "marked_value_usd": 1.0}]
    head = X.headroom_from_rows(rows, condition_id="c1", event_key="e1",
                                now=huge * 3600.0)
    assert head["headroom"]["MAX_CAPITAL_HOURS"] < 0
    cap = X.qty_cap_from_headroom(head, worst_case_cost_per_contract=0.3)
    assert cap["ok"] is False
    assert X.R_NO_HEADROOM in cap["refusals"]
    assert "MAX_CAPITAL_HOURS" in cap["why"]


# ── 4 · THE WHOLE ESTIMATE, SIZED TO FIT ─────────────────────────────

@pytest.mark.parametrize("price,fv", [(0.05, 0.10), (0.15, 0.30),
                                      (0.275, 0.29656), (0.315, 0.72690),
                                      (0.50, 0.60), (0.75, 0.85),
                                      (0.95, 0.99)])
def test_every_rail_passes_after_sizing_to_the_headroom(price, fv):
    """The point of the repair: a positive edge now CLEARS the rails."""
    head = _empty_headroom()
    est = X.estimate(ladder=_deep_ladder(price), fair_value=fv,
                     fee_fn=_fee, observation_age_s=1.0, headroom=head)
    assert est["ok"], est.get("why")
    cost = est["size"] * est["worst_case_cost_per_contract"]
    exposure = X.exposure_from_rows(
        [], condition_id="c1", event_key="e1", proposed_cost_usd=cost,
        proposed_qty=est["size"], now=1_000_000.0,
        proposed_cost_basis="SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT")
    # TAKE_YES is the action this lane actually proposes -- it crosses,
    # so never MAKE_*. The state gates are forced CLEAR here because this
    # test is about the RAILS; the gates have their own tests and are the
    # blockers production still reports.
    verdict = X.verdict("TAKE_YES", observed=exposure["observed"],
                        state={"STALE_DATA": True,
                               "UNRESOLVED_SETTLEMENT_SEMANTICS": True,
                               "OUT_OF_DISTRIBUTION": True,
                               "MODEL_TRUST_DRIFT": True})
    # `railsNotPassed` is the engine's OWN key. An earlier draft of this
    # test read `railsFailed`, which the verdict does not carry -- so the
    # assertion was vacuously true and only `permitted` was doing work.
    assert "railsNotPassed" in verdict, sorted(verdict)
    assert not (verdict.get("railsNotPassed") or []), (
        price, fv, verdict.get("railsNotPassed"), exposure["observed"])
    assert verdict.get("permitted") is True, verdict


def test_the_two_production_candidates_now_clear_the_rails():
    """tb-phi and cin-tor, at the prices production actually recorded."""
    for price, fv, expect_qty in ((0.275, 0.29656440187988087, 2000.0),
                                  (0.315, 0.726897097384449, None)):
        est = X.estimate(ladder=_deep_ladder(price), fair_value=fv,
                         fee_fn=_fee, observation_age_s=1.0,
                         headroom=_empty_headroom())
        assert est["ok"], est.get("why")
        cost = est["size"] * est["worst_case_cost_per_contract"]
        for rail in X.DOLLAR_SCALING_RAILS:
            assert cost <= X.PREDECLARED_LIMITS[rail] + 1e-6, (price, rail)
        assert est["size"] <= X.PREDECLARED_LIMITS[
            "MAX_RESIDUAL_INVENTORY"] + 1e-6
        if expect_qty is not None:
            assert est["size"] == pytest.approx(expect_qty, abs=1e-3)


def test_the_standard_notional_is_a_ceiling_the_rails_never_raise():
    """Huge headroom must not buy more than the frozen policy intends."""
    rows = []
    head = X.headroom_from_rows(rows, condition_id="c1", event_key="e1",
                                now=1_000_000.0)
    # Pretend every rail is enormous; the intent must still govern.
    head = dict(head, headroom={k: 1e12 for k in head["headroom"]})
    est = X.estimate(ladder=_deep_ladder(0.90), fair_value=0.99,
                     fee_fn=_fee, headroom=head)
    assert est["ok"], est.get("why")
    assert est["notional_reduced_by_rails"] is False
    assert est["intended_notional_usd"] == pytest.approx(STANDARD)
    assert est["policy_notional_usd"] == pytest.approx(STANDARD)


def test_the_reduction_is_recorded_with_the_rail_that_caused_it():
    est = X.estimate(ladder=_deep_ladder(0.275), fair_value=0.29656,
                     fee_fn=_fee, headroom=_empty_headroom())
    assert est["notional_reduced_by_rails"] is True
    assert "MAX_RESIDUAL_INVENTORY" in est["notional_reduction_why"]
    assert est["policy_notional_usd"] == pytest.approx(STANDARD)
    assert est["intended_notional_usd"] < STANDARD


def test_thin_depth_still_governs_when_it_is_tighter_than_the_rails():
    """Executable depth is the other cap, and it is not overridden."""
    est = X.estimate(ladder=_ladder(0.275, 40.0), fair_value=0.29656,
                     fee_fn=_fee, headroom=_empty_headroom())
    assert est["ok"], est.get("why")
    assert est["size"] == pytest.approx(40.0)
    # The rails left room for 2000; the book had 40. Depth bound, and the
    # frozen policy's own status says so rather than the rail cap hiding it.
    assert est["sizing"]["status"] == "LIQUIDITY_LIMITED"
    assert est["size"] < X.PREDECLARED_LIMITS["MAX_RESIDUAL_INVENTORY"]


def test_omitting_the_headroom_leaves_the_old_behaviour_exactly():
    a = X.estimate(ladder=_deep_ladder(0.40), fair_value=0.50, fee_fn=_fee)
    assert "qty_cap" not in a
    assert a["intended_notional_usd"] == pytest.approx(STANDARD)


# ── 5 · NOTHING HERE TOUCHES A FUNDED LIMIT ──────────────────────────

def test_the_predeclared_limits_and_their_sha_are_unchanged():
    """The repair sizes INTO the limits; it must not move one."""
    assert X.PREDECLARED_LIMITS["MAX_MARKET_EXPOSURE"] == STANDARD * 1
    assert X.PREDECLARED_LIMITS["MAX_EVENT_EXPOSURE"] == STANDARD * 1
    assert X.PREDECLARED_LIMITS["MAX_CAPITAL_DEPLOYED"] == STANDARD * 3
    assert X.PREDECLARED_LIMITS["MAX_CORRELATED_EXPOSURE"] == STANDARD * 1
    assert X.PREDECLARED_LIMITS["MAX_RESIDUAL_INVENTORY"] == 2000.0
    assert X.PREDECLARED_LIMITS["MAX_DRAWDOWN"] == STANDARD * 1
    assert X.LIMITS_SHA == X._sha(X.PREDECLARED_LIMITS)
    assert X.REAL_ORDER_SUBMISSION_ENABLED is False
    assert X.REAL_CAPITAL_AT_RISK == 0


def test_every_rail_is_classified_as_scaling_or_not():
    """An unclassified rail would be silently ignored by the cap."""
    classified = set(X.DOLLAR_SCALING_RAILS) | set(
        X.QTY_SCALING_RAILS) | set(X.NON_SCALING_RAILS)
    assert classified == set(X.PREDECLARED_LIMITS)


# ── 6 · THE ROUNDING CLASS, CLOSED BY SWEEP ──────────────────────────
#
# THE HAZARD, CAUGHT IN REVIEW RATHER THAN IN PRODUCTION. The cap was
# computed from the UNROUNDED break-even limit while `_entry_plan`
# measures exposure as `size * round(limit, 6)`. Half a micro-dollar per
# contract, times two thousand contracts, is a tenth of a cent past a
# rail the position was sized to fit -- and `evaluate_rail` compares with
# Decimal, so a tenth of a cent is a failure. One sweep, many prices, no
# tolerance in the assertion.

@pytest.mark.parametrize("price", [round(0.01 * k, 2) for k in range(1, 99)])
def test_no_price_lets_a_sized_position_breach_a_dollar_rail(price):
    est = X.estimate(ladder=_deep_ladder(price), fair_value=min(0.999,
                                                                price + 0.07),
                     fee_fn=_fee, headroom=_empty_headroom())
    if not est.get("ok"):
        return                      # refused for another reason; not ours
    cost = round(est["size"] * est["worst_case_cost_per_contract"], 6)
    for rail in X.DOLLAR_SCALING_RAILS:
        assert cost <= X.PREDECLARED_LIMITS[rail], (price, rail, cost)
    assert round(est["size"], 6) <= X.PREDECLARED_LIMITS[
        "MAX_RESIDUAL_INVENTORY"], price


@pytest.mark.parametrize("price", [round(0.01 * k, 2) for k in range(1, 99)])
def test_no_price_lets_the_real_verdict_report_a_failed_rail(price):
    """The engine's own comparison, not a re-implementation of it."""
    est = X.estimate(ladder=_deep_ladder(price), fair_value=min(0.999,
                                                                price + 0.07),
                     fee_fn=_fee, headroom=_empty_headroom())
    if not est.get("ok"):
        return
    cost = est["size"] * est["worst_case_cost_per_contract"]
    exposure = X.exposure_from_rows(
        [], condition_id="c1", event_key="e1", proposed_cost_usd=cost,
        proposed_qty=est["size"], now=1_000_000.0,
        proposed_cost_basis="SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT")
    v = X.verdict("TAKE_YES", observed=exposure["observed"],
                  state={"STALE_DATA": True,
                         "UNRESOLVED_SETTLEMENT_SEMANTICS": True,
                         "OUT_OF_DISTRIBUTION": True,
                         "MODEL_TRUST_DRIFT": True})
    assert not (v.get("railsNotPassed") or []), (
        price, v.get("railsNotPassed"), exposure["observed"])
    assert v.get("permitted") is True, (price, v.get("reason"))
