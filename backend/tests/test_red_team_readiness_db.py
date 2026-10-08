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


import pytest as _pytest  # noqa: E402

_FAIL = {
    "venue_health": ("sportsassets.redteam.venue_health", "read"),
    "truth_quorum": ("sportsassets.redteam.controls", "quorum_rows"),
    "attribution": ("sportsassets.redteam.controls", "attributed_positions"),
    "holdout_registry": ("sportsassets.redteam.controls", "holdout_registry"),
    "capacity": ("sportsassets.redteam.readiness", "capacity_points"),
    "fee_evidence": ("sportsassets.redteam.fees", "census"),
    "canonical_exposure": ("sportsassets.redteam.exposure", "census"),
}


@_pytest.mark.parametrize("name", sorted(_FAIL))
def test_a_failed_read_is_red_never_a_vacuous_pass(monkeypatch, name):
    """Production 088af82 cancelled a statement and lost the whole read.
    Each interlock read now runs in its own savepoint; a failed one turns
    every control it feeds RED with READ_UNAVAILABLE, and the rest read."""
    import importlib
    from sportsassets.redteam import readiness as R
    mod, attr = _FAIL[name]

    async def boom(*_a, **_k):
        raise RuntimeError("injected")
    monkeypatch.setattr(importlib.import_module(mod), attr, boom)

    async def fn(conn):
        async with conn.transaction(readonly=True):
            return await R.evaluate(conn, now=time.time())
    r = run(fn)
    assert r["status"] == "PAPER_SHADOW_ONLY"
    assert r["read_timings"][name]["ok"] is False
    for ctl in R.SECTION_CONTROLS[name]:
        c = r["controls"][ctl]
        assert c["status"] == "RED", (ctl, c)
        assert "READ_UNAVAILABLE:%s" % name in c["blockers"]
    ok = [k for k, v in r["read_timings"].items() if v["ok"]]
    assert len(ok) >= len(R.SECTION_CONTROLS) - 1
