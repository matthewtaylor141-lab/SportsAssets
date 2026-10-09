"""rc6.2 agent-truth: the Kalshi worker's claims pass uses the READ-TIME clock.

Production 24 h: 2,124 Kalshi route candidates refused BOOK_TIME_IN_FUTURE.
workers/kalshi_market_data.py took claims_pass(now=time.time()) BEFORE the
assembler read the books, so a WebSocket book persisted while the pass was
reading was "observed after now" and canonical_venue/quotes refused it.
Adriana's census already evaluates at the read time
(canonical_claims_db.claims_census). Receipts stay SHADOW.
"""
from __future__ import annotations
import ast
import asyncio
import inspect
import textwrap
import os
import time
from decimal import Decimal
import pytest
from sportsassets import canonical_claims_db as CDB
from sportsassets import kalshi_market_data as KMD
from sportsassets.market_plane import rules as RULES
from sportsassets.workers import kalshi_market_data as W
from tests.test_kalshi_canonical_db import EV, TERMS, _Pool

DSN = os.environ.get("RN1X_TEST_DSN")


def test_the_worker_calls_the_claims_pass_on_the_live_read_clock():
    src = inspect.getsource(W.run)
    calls = [n for n in ast.walk(ast.parse(textwrap.dedent(src)))
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "claims_pass"]
    assert calls, "run() no longer calls claims_pass"
    for c in calls:
        assert "now" not in {k.arg for k in c.keywords}, \
            "the worker must not pin the pass clock before the books are read"


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
def test_a_book_persisted_during_the_pass_is_not_in_the_future(monkeypatch):
    import asyncpg
    # the rules writer's process-local fingerprint cache would remember this
    # test's rolled-back rows and make a later test's identical upsert a no-op
    monkeypatch.setattr(RULES, "_SEEN", {})

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            for t in ("canonical_route_receipts", "canonical_claim_aliases",
                      "kalshi_books_current", "kalshi_fixtures_current"):
                await c.execute("DELETE FROM %s" % t)
            await W.persist_fee_terms(c, [{
                "id": "test-rc62-%d" % int(time.time() * 1e6),
                "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1,
                "scheduled_ts": "2025-10-04T07:00:00Z",
                "series_ticker": "KXMLBGAME"}], kind="SERIES_CHANGE")
            t0 = time.time()
            k = KMD.KalshiFixture(
                event_ticker=EV, series_ticker="KXMLBGAME",
                sport="BASEBALL", league="MLB", start_epoch=t0 + 3 * 3600,
                home_id="H", away_id="A", home_code="NYY", away_code="TB",
                tie_ticker=None, team_tickers=(EV + "-NYY", EV + "-TB"),
                outcome_kind="TWO_WAY", status="ESTABLISHED", reasons=(),
                milestone_id="m1")
            await W.persist_fixture(c, k, None)
            asks = {EV + "-NYY": ("0.4400", "0.5700"),
                    EV + "-TB": ("0.4500", "0.5600")}
            for t in asks:
                await RULES.upsert(c, [{
                    "contract_id": "kalshi:%s" % t, "venue": "KALSHI",
                    "rules_published": True, "rules_field": "rules_primary",
                    "rules_sha256": "%064d" % len(t),
                    "rules_text": "test rules for %s" % t,
                    "rules_secondary": None, "parse_status": "ESTABLISHED",
                    "evidence": TERMS, "parser_version": "TEST",
                    "source": "TEST"}], now=t0)

            async def ws_writes(observed_at):
                for t, (ya, na) in asks.items():
                    ob = {"orderbook_fp": {
                        "no_dollars": [[str(1 - Decimal(ya)), "100.00"]],
                        "yes_dollars": [[str(1 - Decimal(na)), "100.00"]]}}
                    b = KMD.book_from_orderbook(ob, observed_at=observed_at)
                    await W.persist_book(c, t, EV, b, {})

            real = CDB.assemble

            async def assemble_while_the_ws_writes(conn, **kw):
                # the WebSocket runtime persists the books AFTER the pass
                # started (its clock was read) and before the read
                await asyncio.sleep(0.02)
                await ws_writes(time.time())
                return await real(conn, **kw)

            monkeypatch.setattr(CDB, "assemble", assemble_while_the_ws_writes)
            # the defect's shape: a clock pinned before the read refuses them
            pinned = await W.claims_pass(_Pool(c), now=time.time(), record=False)
            assert pinned["routes"] == 2 and pinned["best_route_receipts"] == []
            # the worker's live clock: evaluated as of the read
            live = await W.claims_pass(_Pool(c), record=True)
            assert live["routes"] == 2 and live["best_route_receipts"], live
            rows = await c.fetch(
                "SELECT mode, production_effect, candidates::text AS cand"
                " FROM canonical_route_receipts")
            assert rows and all(r["mode"] == "SHADOW" and r["production_effect"] == "NONE" for r in rows)
            assert not any("BOOK_TIME_IN_FUTURE" in r["cand"] for r in rows)
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())
