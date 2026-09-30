"""WHAT THE PROPOSED PILOT LIMITS ACTUALLY PERMIT -- computed with the rail code.

The proposed values are per-market 25, event 50, capital 250, max-exposure
250, cumulative loss stop 75. They are FOR THE OWNER'S REVIEW; nothing here
approves them. Every number the decision form states is derived here from
`bettor_entry_execution.exposure_from_rows` and the effective limits, with the
database's capacity rule (ONE open portfolio group at a time, at most one
PRIMARY and one HEDGE leg in it).
"""
from __future__ import annotations

import math

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA

PROPOSED = {"capital_usd": 250, "per_market_usd": 25,
            "event_exposure_usd": 50, "max_exposure_usd": 250,
            "cumulative_loss_stop_usd": 75}
EFF = EX.effective_limits(FA.normalise_limit_keys(dict(PROPOSED)))["effective"]
RAILS = ("MAX_MARKET_EXPOSURE", "MAX_EVENT_EXPOSURE", "MAX_CAPITAL_DEPLOYED",
         "MAX_CORRELATED_EXPOSURE", "MAX_RESIDUAL_INVENTORY", "MAX_DRAWDOWN")


def _passes(rows, *, cid, ev, cost, qty):
    obs = EX.exposure_from_rows(rows, condition_id=cid, event_key=ev,
                                proposed_cost_usd=cost, proposed_qty=qty,
                                now=0.0)["observed"]
    return all(obs[r] <= float(EFF[r]) + 1e-9 for r in RAILS)


def max_primary_qty(p1):
    q = 0
    while _passes([], cid="m1", ev="e1", cost=(q + 1) * p1, qty=q + 1):
        q += 1
    return q


def max_hedge_qty(p1, q1, p2, *, primary_marked_at=None):
    row = {"condition_id": "m1", "event_key": "e1", "cost_usd": q1 * p1,
           "qty": q1, "opened_at": 0.0}
    if primary_marked_at is not None:
        row["marked_value_usd"] = q1 * primary_marked_at
    h = 0
    while _passes([row], cid="m2", ev="e1", cost=(h + 1) * p2, qty=h + 1):
        h += 1
    return h


def test_the_primary_is_bounded_by_the_per_market_cap():
    assert max_primary_qty(0.40) == 62        # $24.80
    assert max_primary_qty(0.522) == 47       # $24.53
    assert max_primary_qty(0.65) == 38


@pytest.mark.parametrize("p1,p2", [(0.40, 0.40), (0.40, 0.60), (0.40, 0.80),
                                   (0.52, 0.60), (0.65, 0.40), (0.30, 0.985)])
def test_hedge_coverage_is_the_price_ratio_when_the_hedge_is_dearer(p1, p2):
    """A hedge in the SAME event is capped at $25 by its own market rail and
    by the $50 event rail minus the primary. Matched coverage is therefore
    min(1, floor(25/p2) / q1): full only when the hedge is no dearer than the
    primary. The uncovered remainder is held and valued as such."""
    q1 = max_primary_qty(p1)
    h = max_hedge_qty(p1, q1, p2)
    assert h == math.floor(25.0 / p2 + 1e-9) or h == math.floor(
        (50.0 - q1 * p1) / p2 + 1e-9)
    covered = min(h, q1)
    if p2 <= p1:
        assert covered == q1
    else:
        assert covered < q1
        assert covered / q1 == pytest.approx(p1 / p2, abs=0.05)


def test_the_owners_example_62_contracts_at_40_cents_cannot_be_matched_at_80():
    q1 = max_primary_qty(0.40)
    assert q1 == 62
    h = max_hedge_qty(0.40, q1, 0.80)
    assert h == 31                     # $24.80: half the position covered
    assert q1 - h == 31                # 31 contracts stay unpaired


def test_the_inventory_rail_binds_below_about_one_and_a_quarter_cents():
    """No price floor is enforced on entries: the only bound is the
    probability support [0.02, 0.98], and a buy needs its price below the
    probability. At 1 cent a $25 primary would be 2,500 contracts; the
    2,000-contract inventory rail caps it at 2,000 ($20)."""
    assert max_primary_qty(0.02) == 1250
    assert max_primary_qty(0.01) == 2000
    assert float(EFF["MAX_RESIDUAL_INVENTORY"]) == 2000.0


def test_the_loss_stop_admits_three_unmarked_25_dollar_positions_not_four():
    rows = [{"condition_id": "m%d" % i, "event_key": "e%d" % i,
             "cost_usd": 25.0, "qty": 50, "opened_at": 0.0} for i in (1, 2)]
    assert _passes(rows, cid="m3", ev="e3", cost=25.0, qty=50) is True
    rows.append({"condition_id": "m3", "event_key": "e3", "cost_usd": 25.0,
                 "qty": 50, "opened_at": 0.0})
    assert _passes(rows, cid="m4", ev="e4", cost=25.0, qty=50) is False


def test_capital_and_max_exposure_would_bind_only_above_one_group():
    """With one open group (a $25 primary and at most a $25 hedge) the book
    never holds more than about $50, so the $250 capital and max-exposure
    values cannot bind while the one-group rule stands."""
    q1 = max_primary_qty(0.40)
    h = max_hedge_qty(0.40, q1, 0.40)
    assert q1 * 0.40 + h * 0.40 <= 50.0
    assert float(EFF["MAX_CAPITAL_DEPLOYED"]) == 250.0


# ── THE DATABASE'S CAPACITY RULE ─────────────────────────────────────

import os                                                     # noqa: E402

DSN = os.environ.get("RN1X_TEST_DSN", "")


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_the_database_admits_one_open_group_at_a_time():
    """Migration 131 `bettor_funded_one_open_group` is a unique index on
    (true) over open groups: ONE open portfolio group in the whole database,
    whatever the account, so 'ten concurrent markets' is not permitted however
    large the dollar limits are."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_book as FB
    from sportsassets import bettor_funded_execution as FX
    from tests.test_the_funded_lifecycle_is_complete import (
        ACCT, VENUE, _clean, _seed)
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)

        async def _rec(iid, slug, ev, acct=ACCT):
            return await FB.record_intent(
                conn, intent_id=iid, account_id=acct, venue=VENUE,
                venue_class=FA.VENUE_FUNDED, us_market_slug=slug,
                event_key=ev, order_intent=FX.LONG, limit_price=0.40,
                quantity=10, collateral_usd=4.0, effective_digest="d",
                payout_event="X", held_is_long=True)
        first = await _rec("cap-1", "aec-a-1", "ev-a")
        assert first["ok"] is True, first
        second = await _rec("cap-2", "aec-b-2", "ev-b")
        assert second["ok"] is False
        # named by what occupies the slot: the live intent, else the group
        assert second["refusal"] in (FB.R_ANOTHER_GROUP_IS_OPEN,
                                     "ANOTHER_FUNDED_INTENT_IS_ALREADY_LIVE")
        third = await _rec("cap-3", "aec-c-3", "ev-c", acct=ACCT + "-other")
        assert third["ok"] is False
    finally:
        await conn.execute("DELETE FROM bettor_funded_intents "
                           "WHERE intent_id LIKE 'cap-%'")
        await conn.execute("DELETE FROM bettor_funded_portfolio_groups "
                           "WHERE group_id LIKE 'grp:cap-%'")
        await _clean(conn)
        await conn.close()
