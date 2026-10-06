"""THE VENUE-SETTLEMENT JOIN: ONE READ PER MARKET, HELD POSITIONS FIRST
(closeout, production 2026-10-06).

The queue held 14,806 unjoined valuation rows (7,935 never asked) drained at
~240 venue reads an hour, one read PER ROW -- one held market alone had 412
rows -- while 53 open PAPER positions sat on ended games whose settlement
never arrived; Xavier's value-add, written only when a position closes, had
7 rows. A market's settlement is one fact: one read now answers every
unjoined row of the market (each still classed on its own intent and ladder
side), and the markets of open, unsettled PAPER positions are asked first --
at most once per HELD_REASK_S, so an unfinished held game cannot hold the
budget."""
from __future__ import annotations

import time
import uuid

import pytest

from tests import paper_harness as H
from tests.test_settlement_exception_risk import _purge, _valuation

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


@pg
async def test_one_read_per_market_and_held_markets_first(monkeypatch):
    from sportsassets import bettor_live_read as lr
    from sportsassets.workers import ext_pinnacle_loop as X
    conn = await H.connect()
    tag = uuid.uuid4().hex[:6]
    prefix = "aec-jq%s-" % tag
    old = time.time() - 30 * 3600
    a, b, c = (prefix + x + "-2026-10-05" for x in ("aaa", "bbb", "ccc"))
    try:
        # A: an ordinary market, oldest in the queue (3 rows)
        for i in range(3):
            await _valuation(conn, slug=a, family="baseball",
                             event_key="ev-a-%d" % i, decided_at=old - 600 + i)
        # B: a HELD market, newer (2 rows); C: a HELD market not yet settled
        for i in range(2):
            await _valuation(conn, slug=b, family="baseball",
                             event_key="ev-b-%d" % i, decided_at=old + i)
        await _valuation(conn, slug=c, family="baseball",
                         event_key="ev-c", decided_at=old + 5)
        monkeypatch.setattr(
            X, "HELD_UNSETTLED_SQL",
            "SELECT unnest(ARRAY['%s', '%s'])::text AS us_market_slug"
            % (b, c))
        calls = []
        answers = {a: {"status": lr.RESOLVED, "settlement_price": 1.0,
                       "settlement_price_raw": "1"},
                   b: {"status": lr.RESOLVED, "settlement_price": 0.0,
                       "settlement_price_raw": "0"},
                   c: {"status": lr.PENDING}}

        def read(slug):
            calls.append(slug)
            return answers.get(slug, {"status": lr.PENDING})
        monkeypatch.setattr(X, "_read_resolution_blocking", read)

        got = await X.join_outcomes(conn, limit=2)
        assert got["ran"], got
        # the two HELD markets first, each read once
        assert sorted(calls) == sorted([b, c]), calls
        assert got["held_markets_asked"] == 2
        n_b = await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE us_market_slug=$1"
            " AND outcome_basis IS NOT NULL", b)
        assert n_b == 2                       # one read answered both rows
        # C was asked moments ago: not due again; A (ours, oldest) is next
        calls.clear()
        got = await X.join_outcomes(conn, limit=1)
        assert calls and calls[0] != c, calls
        calls.clear()
        await X.join_outcomes(conn, limit=10_000)
        assert calls.count(a) <= 1
        n_a = await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE us_market_slug=$1"
            " AND outcome_basis IS NOT NULL", a)
        assert n_a == 3                       # one read answered three rows
        pend = await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE us_market_slug=$1"
            " AND outcome_basis IS NULL AND settlement_read_at IS NOT NULL",
            c)
        assert pend == 1                      # attempt stamped, never joined
    finally:
        await _purge(conn, prefix)
        await conn.close()


@pg
async def test_the_held_market_query_runs_on_the_real_ledger():
    from sportsassets.workers import ext_pinnacle_loop as X
    conn = await H.connect()
    try:
        rows = await conn.fetch(X.HELD_UNSETTLED_SQL)
        assert all(r["us_market_slug"] for r in rows)
    finally:
        await conn.close()
