"""A WINNING SHORT IS PAID IN FULL WHEN THE LONG SIDE SETTLES AT ZERO.

The funded close paid a settled residual with `cash_for`, which is a FILL's
cost and returns 0 for a non-positive price whatever the side. So a short on a
market that settled NO (long-side price exactly 0) was booked at $0 instead of
its full quantity. Found by the reconciliation work (D5a); the close and the
correction writer now share `bettor_funded_book.settlement_cash`.

Driven through the production close, `bettor_funded_management
.reconcile_settlement`, with only the venue's settlement probe substituted.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_corrections as CO
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM

from tests import test_a_filled_order_is_not_a_closed_position as L

pg = L.pg
SHORT = "ORDER_INTENT_BUY_SHORT"


def _reported(px):
    return lambda c, s: {"terminal_reading": "REPORTED_SETTLEMENT",
                         "authoritative_payout_present": True,
                         "why": "the venue reported a price",
                         "reader_verdict": {"settlement_price": px}}


def test_settlement_cash_is_side_aware_at_both_ends():
    assert FB.cash_for(10, 0.0, SHORT) == 0.0          # a fill's cost
    assert FB.settlement_cash(10, 0.0, SHORT) == pytest.approx(10.0)
    assert FB.settlement_cash(10, 0.0, FX.LONG) == pytest.approx(0.0)
    assert FB.settlement_cash(10, 1.0, FX.LONG) == pytest.approx(10.0)
    assert FB.settlement_cash(10, 1.0, SHORT) == pytest.approx(0.0)
    assert FB.settlement_cash(10, 0.3, SHORT) == pytest.approx(7.0)
    # ONE FUNCTION: the correction writer pays exactly what the close pays.
    for px in (0.0, 0.3, 1.0):
        for side in (FX.LONG, SHORT):
            assert CO.settlement_payout(10, px, side) == \
                FB.settlement_cash(10, px, side)
    for bad in (-0.01, 1.01):
        with pytest.raises(ValueError):
            FB.settlement_cash(10, bad, SHORT)


@pg
@pytest.mark.asyncio
async def test_the_close_pays_a_winning_short_in_full_at_zero():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(L.DSN)
    try:
        await L._clean(conn)
        await L._seed(conn)
        await L._entry(conn, intent_id="fpi-short-zero", intent=SHORT,
                       qty=10, price=0.62)
        got = await FM.reconcile_settlement(
            conn, intent_id="fpi-short-zero", probe=_reported(0.0))
        assert got["closed"] is True
        assert got["settlement_usd"] == pytest.approx(10.0)
        pnl = await FB.pnl(conn, account_id=L.ACCT, venue=L.VENUE)
        # Paid 10 for a short that cost (1 - 0.62) x 10 = 3.80 plus fees.
        assert pnl["realised_pnl_usd"] > 5.0
    finally:
        await L._clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_settlement_price_outside_the_unit_interval_is_refused():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(L.DSN)
    try:
        await L._clean(conn)
        await L._seed(conn)
        await L._entry(conn, intent_id="fpi-bad-px")
        for bad in (-0.5, 1.5):
            got = await FM.reconcile_settlement(
                conn, intent_id="fpi-bad-px", probe=_reported(bad))
            assert got["closed"] is False
            assert got["refusal"] == FM.R_SETTLEMENT_NOT_AUTHORITATIVE
        assert await conn.fetchval(
            "SELECT closed_at FROM bettor_funded_intents "
            " WHERE intent_id='fpi-bad-px'") is None
    finally:
        await L._clean(conn)
        await conn.close()
