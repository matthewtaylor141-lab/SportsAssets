"""CAPITAL-HOURS ARE INTEGRATED OVER THE HOLDING PERIOD, NOT TO NOW.

The frozen MAX_CAPITAL_HOURS rail (72,000 USD-hours) is not owner-tightenable.
The funded book integrated every entry fill ever made up to now(), so a closed
position kept consuming it forever and a flat book would eventually refuse
every entry -- a multi-day pilot would stop on a rail that measures nothing it
holds. A closed position now stops accruing at its close.
"""
from __future__ import annotations

import os

import pytest

from sportsassets import bettor_funded_book as FB
from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, VENUE, _clean, _entry, _seed)

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@pg
@pytest.mark.asyncio
async def test_a_closed_position_stops_consuming_capital_hours():
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, intent_id="fpi-ch", qty=10, price=0.60)
        # filled 100 h ago, closed 90 h ago: held for 10 h
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute(
                "UPDATE bettor_funded_fills SET at = now() - interval "
                "'100 hours' WHERE intent_id='fpi-ch'")
            await conn.execute(
                "UPDATE bettor_funded_intents SET closed_at = now() - "
                "interval '90 hours', closed_reason='SETTLED_BY_THE_VENUE', "
                "residual_qty=0 WHERE intent_id='fpi-ch'")
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        # $6.00 of entry cash held for 10 h = 60 USD-h, not 600
        assert exp["capital_hours_usd_h"] == pytest.approx(60.0, rel=1e-3)
        # an OPEN position still accrues to now
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute(
                "UPDATE bettor_funded_intents SET closed_at = NULL, "
                "closed_reason = NULL, residual_qty = 10 "
                "WHERE intent_id='fpi-ch'")
        exp2 = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp2["capital_hours_usd_h"] == pytest.approx(600.0, rel=1e-3)
    finally:
        await _clean(conn)
        await conn.close()
