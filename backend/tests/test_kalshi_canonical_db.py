"""KALSHI CANONICAL VENUE AGAINST POSTGRES (migration 314 + 265).

The worker's persistence, the shared assembler both readers use, Adriana's
claim-first scan recorded through her own record() into the 265 tables
(SHADOW, claim pair and topology on the row), and GET /api/command/venues
reading it all back with KALSHI_HEALTH and POLYMARKET_HEALTH apart. Every
write happens inside a transaction that is rolled back.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal

import pytest

from sportsassets import canonical_claims_db as CDB
from sportsassets import kalshi_claims as KCL
from sportsassets import kalshi_market_data as KMD
from sportsassets.agents import adriana as ADR
from sportsassets.api import command_venues as V
from sportsassets.market_plane import rules as RULES
from sportsassets.workers import kalshi_market_data as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
EV = "KXMLBGAME-26OCT072000TBNYY"
TERMS = {"status": "ESTABLISHED", "settlement": {
    "overtime_included": True, "draw_rule": "SCALAR_0_50",
    "void_rule": "SCALAR_0_50", "postponement_window_hours": 48.0,
    "postponement_payout": "SCALAR_0_50"},
    "verification_sources": ["MLB"]}


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self, **_kw):
        c = self.c

        class A:
            async def __aenter__(self_):
                return c

            async def __aexit__(self_, *a):
                return False
        return A()


@pg
def test_persist_assemble_record_and_read_back():
    import asyncpg

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            for t in ("canonical_route_receipts", "canonical_claim_aliases",
                      "kalshi_books_current", "kalshi_fixtures_current"):
                await c.execute("DELETE FROM %s" % t)
            now = time.time()
            start = now + 3 * 3600
            k = KMD.KalshiFixture(
                event_ticker=EV, series_ticker="KXMLBGAME",
                sport="BASEBALL", league="MLB", start_epoch=start,
                home_id="H", away_id="A", home_code="NYY", away_code="TB",
                tie_ticker=None, team_tickers=(EV + "-NYY", EV + "-TB"),
                outcome_kind="TWO_WAY", status="ESTABLISHED", reasons=(),
                milestone_id="m1")
            await W.persist_fixture(c, k, None)
            asks = {EV + "-NYY": ("0.4400", "0.5700"),
                    EV + "-TB": ("0.4500", "0.5600")}
            for t, (ya, na) in asks.items():
                ob = {"orderbook_fp": {
                    "no_dollars": [[str(1 - Decimal(ya)), "100.00"]],
                    "yes_dollars": [[str(1 - Decimal(na)), "100.00"]]}}
                b = KMD.book_from_orderbook(ob, observed_at=now)
                await W.persist_book(c, t, EV, b, {})
                await RULES.upsert(c, [{
                    "contract_id": "kalshi:%s" % t, "venue": "KALSHI",
                    "rules_published": True, "rules_field": "rules_primary",
                    "rules_sha256": "%064d" % len(t),
                    "rules_text": "test rules for %s" % t,
                    "rules_secondary": None, "parse_status": "ESTABLISHED",
                    "evidence": TERMS, "parser_version": "TEST",
                    "source": "TEST"}], now=now)
            later = now + 1.0     # the pass runs after the books were read
            got = await CDB.assemble(c, now=later)
            assert len(got) == 1
            fx, built, insts = got[0]
            assert fx.event_key == KCL.event_key("MLB", start, "TB", "NYY")
            assert len(insts) == 4 and len(built["classes"]) == 2
            # the worker's pass: aliases + best-route receipts persisted
            res = await W.claims_pass(_Pool(c), now=later, record=True)
            assert res["fixtures_priced"] == 1 and res["routes"] == 2
            assert await c.fetchval(
                "SELECT count(*) FROM canonical_claim_aliases") == 4
            assert await c.fetchval(
                "SELECT count(*) FROM canonical_route_receipts") == 2
            # Adriana's own scan over the same evidence, recorded by her
            cen = await CDB.claims_census(c, now=later)
            n = len(cen["opportunities"]) + len(cen["refusals"])
            assert n >= 1
            if await ADR.schema(c):
                rec = await ADR.record(
                    c, cen, started=now, finished=now + 1,
                    scan_id="adr-claims-test-%d" % int(now), status="OK",
                    why=None)
                assert rec["created"]
                if cen["opportunities"]:
                    eco = await c.fetchval(
                        "SELECT economics FROM adriana_arb_opportunities "
                        " WHERE scan_id = $1 LIMIT 1", rec["scan_id"])
                    eco = json.loads(eco) if isinstance(eco, str) else eco
                    assert eco["claim_pair"]["topology"] == "SAME_VENUE"
                # the readback
                data = await V.read(c, now=later)
                assert data["kalshi_live_money"] == "NOT_ACTIVATED"
                assert data["adriana"] == "SHADOW_ONLY"
                assert set(data["health"]) == {"KALSHI_HEALTH",
                                               "POLYMARKET_HEALTH"}
                assert data["health"]["KALSHI_HEALTH"]["domain"] == \
                    "KALSHI_HEALTH"
                assert data["claims"] and data["claims"][0]["claims"]
                route = data["claims"][0]["claims"][0]["route"]
                assert route and route["chosen"], route["candidates"]
                assert data["arbitrage"]["status"] == "OK"
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())


def test_the_venues_route_is_get_only_command_auth_and_registered():
    import pathlib
    routes = [r for r in V.router.routes if getattr(r, "path", "") == V.PATH]
    assert len(routes) == 1 and routes[0].methods == {"GET"}
    assert "require_read" in [d.call.__name__
                              for d in routes[0].dependant.dependencies]
    root = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
    src = (root / "api" / "command_venues.py").read_text()
    assert "readonly=True" in src and '"private, no-store"' in src
    assert "command_venues" in (root / "api" / "app.py").read_text()
    for w in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert w not in src
