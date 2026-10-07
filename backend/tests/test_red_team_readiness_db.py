"""The red-team final readiness interlock over a MIGRATED database (315):
nothing proven = PAPER_SHADOW_ONLY with named blockers; the runner appends
receipts (deduplicated by evidence hash), every receipt table refuses edits
and deletes, and nothing grants authority."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

DSN = os.environ.get("RN1X_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def run(coro_fn):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()
    return asyncio.run(go())


def test_evaluate_with_nothing_proven_is_paper_shadow_only():
    from sportsassets.redteam import readiness as R

    async def fn(conn):
        async with conn.transaction(readonly=True):
            return await R.evaluate(conn, now=time.time())
    r = run(fn)
    assert r["status"] == "PAPER_SHADOW_ONLY"
    assert r["blockers"]
    assert r["auto_activation"] is False
    assert r["authority"] == {"small_live": "SHADOW",
                              "kalshi_live_money": "NOT_ACTIVATED",
                              "adriana": "SHADOW_ONLY",
                              "capital_authority_granted": False}
    for k in ("VENUE_HEALTH", "TRUTH_QUORUM", "PROFIT_BREAKERS",
              "DIGITAL_TWIN", "CAPACITY", "CREDENTIAL_CLASSES",
              "MIGRATION_INTEGRITY", "CANONICAL_EXPOSURE"):
        assert k in r["controls"], k
    # venue health is one entry per venue, never blended
    vh = r["controls"]["VENUE_HEALTH"]["evidence"]
    assert set(vh["venues"]) >= {"KALSHI", "POLYMARKET_US"}


def test_runner_appends_receipts_once_per_evidence_and_never_edits():
    from sportsassets.redteam import runner as RN

    async def fn(conn):
        now = time.time()
        q = ("SELECT count(*) FROM red_team_control_receipts WHERE "
             " control <> 'VENUE_HEALTH'")
        a = await RN.pass_once(conn, now=now)
        n1 = await conn.fetchval(q)
        r1 = await conn.fetchval("SELECT count(*) FROM "
                                 "red_team_readiness_receipts")
        b = await RN.pass_once(conn, now=now + 1)
        n2 = await conn.fetchval(q)
        r2 = await conn.fetchval("SELECT count(*) FROM "
                                 "red_team_readiness_receipts")
        vh = await conn.fetchval(
            "SELECT count(*) FROM red_team_control_receipts WHERE "
            " control = 'VENUE_HEALTH'")
        refused = []
        for t in ("red_team_readiness_receipts", "red_team_control_receipts"):
            for sql in ("UPDATE %s SET status = status" % t,
                        "DELETE FROM %s" % t):
                try:
                    async with conn.transaction():
                        await conn.execute(sql)
                except Exception as exc:                        # noqa: BLE001
                    refused.append(type(exc).__name__)
        return a, b, n1, n2, r2 - r1, vh, refused
    a, b, n1, n2, verdicts, vh, refused = run(fn)
    assert a["status"] == b["status"] == "PAPER_SHADOW_ONLY"
    assert n1 >= 1 and n2 == n1          # same control evidence -> no new row
    assert verdicts == 1                 # the interlock's verdict every pass
    assert vh >= 2                       # venue health written every pass
    assert len(refused) == 4             # append-only
