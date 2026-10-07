"""The contract-terms binding through the SHARED ASSEMBLER (both the
worker's claims pass and Adriana's census read it), against Postgres: real
Kalshi rule text in market_plane_rules, the worker's rulebook record in
ingestion_state. Without a live-verified rulebook every alias stays
UNKNOWN_STATES; with it the aliases fingerprint. Rolled back."""
from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal

import pytest

from sportsassets import canonical_claims_db as CDB
from sportsassets import kalshi_contract_terms as KCT
from sportsassets import kalshi_market_data as KMD
from sportsassets import settlement_rule_registry as SR
from sportsassets.market_plane import rules as RULES
from sportsassets.workers import kalshi_market_data as W

DSN = os.environ.get("RN1X_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
EV = "KXMLBGAME-26OCT071600CLECWS"
SECONDARY = (
    "The following market refers to the Cleveland vs Chicago WS professional "
    "baseball game originally scheduled for Oct 7, 2026 at 4:00 PM EDT. If "
    "this game is postponed or delayed, the market will remain open and "
    "close after the rescheduled game has finished (within two days). If the "
    "game is cancelled or rescheduled to over two days away, the market will "
    "resolve to a fair price in accordance with the rules.")


def _market(t, team):
    return {"ticker": t, "event_ticker": EV, "rules_primary": (
        "If %s wins the Cleveland vs Chicago WS professional baseball game "
        "originally scheduled for Oct 7, 2026 at 4:00 PM EDT, then the market "
        "resolves to Yes." % team), "rules_secondary": SECONDARY}


async def _seed(c, now):
    # each test rolls back: the upsert's process-local fingerprint cache must
    # not remember rows the database no longer holds
    RULES._SEEN.clear()
    for t in ("canonical_route_receipts", "canonical_claim_aliases",
              "kalshi_books_current", "kalshi_fixtures_current"):
        await c.execute("DELETE FROM %s" % t)
    await c.execute("DELETE FROM ingestion_state WHERE key = $1",
                    KCT.STATE_KEY)
    await W.persist_fee_terms(c, [{
        "id": "ct-x1-%d" % int(now * 1e6),
        "fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1,
        "scheduled_ts": "2025-10-04T07:00:00Z",
        "series_ticker": "KXMLBGAME"}], kind="SERIES_CHANGE")
    k = KMD.KalshiFixture(
        event_ticker=EV, series_ticker="KXMLBGAME", sport="BASEBALL",
        league="MLB", start_epoch=now + 3 * 3600, home_id="H", away_id="A",
        home_code="CWS", away_code="CLE", tie_ticker=None,
        team_tickers=(EV + "-CWS", EV + "-CLE"), outcome_kind="TWO_WAY",
        status="ESTABLISHED", reasons=(), milestone_id="m1")
    await W.persist_fixture(c, k, None)
    for t, team, ya in ((EV + "-CWS", "Chicago WS", "0.18"),
                        (EV + "-CLE", "Cleveland", "0.83")):
        na = str(Decimal("1.01") - Decimal(ya))
        b = KMD.book_from_orderbook({"orderbook_fp": {
            "no_dollars": [[str(1 - Decimal(ya)), "100.00"]],
            "yes_dollars": [[str(1 - Decimal(na)), "100.00"]]}},
            observed_at=now)
        await W.persist_book(c, t, EV, b, {})
        await RULES.upsert(c, [RULES.kalshi_row(_market(t, team))], now=now)


def _run(state):
    import asyncpg

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            now = time.time()
            await _seed(c, now)
            if state is not None:
                await W.persist_contract_terms(c, state(now))
            got = await CDB.assemble(c, now=now + 1.0)
            return [(i.market_id, i.side, i.fingerprint, list(i.refusals),
                     dict(i.settlement or {}))
                    for _fx, _b, insts in got for i in insts]
        finally:
            await tr.rollback()
            await c.close()
    return asyncio.run(go())


def _state(sha):
    def make(now):
        return {"version": KCT.VERSION,
                "series": {"KXMLBGAME": "BASEBALLGAMEWIN"},
                "rulebooks": {"BASEBALLGAMEWIN": {
                    "status": "OK", "sha256": sha, "fetched_at": now}}}
    return make


def test_without_a_verified_rulebook_every_alias_stays_unknown():
    for st in (None, _state("0" * 64)):
        rows = _run(st)
        assert len(rows) == 4
        for mid, side, fp, refusals, s in rows:
            assert fp is None, (mid, side)
            assert any(r.startswith("UNKNOWN_STATES") for r in refusals)
            assert s.get("overtime_included") is None


def test_with_the_live_verified_rulebook_the_aliases_fingerprint():
    rows = _run(_state(KCT.RULEBOOKS["BASEBALLGAMEWIN"]["pdf_sha256"]))
    assert len(rows) == 4
    for mid, side, fp, refusals, s in rows:
        assert s["overtime_included"] is True
        assert s["draw_rule"] == SR.SCALAR_0_50
        assert s["postponement_window_hours"] == 48.0
        assert fp is not None and not any(
            r.startswith("UNKNOWN_STATES") for r in refusals), (mid, refusals)
    # one market's YES and NO are complements, never the same claim
    by = {(m, sd): fp for m, sd, fp, _r, _s in rows}
    assert by[(EV + "-CLE", "YES")] != by[(EV + "-CLE", "NO")]
    json.dumps(rows, default=str)
