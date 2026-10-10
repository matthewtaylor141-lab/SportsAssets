"""THE KALSHI BOOK ROWS AND EVERY READER, ON POSTGRES (RC6.2 review).

The real Subscriber and the worker's own writers -- the gap sink's
immediate GAP write (write_gaps), flush, reassert, the wanted-set refresh
(prune_untracked + retire_untracked) -- run against the documented venue
(tests.test_rc62_kalshi_ws_venue_membership.MemberVenue) and write the real
kalshi_books_current. Before EVERY step of the venue, every reader the
decision path, the route comparison and the freshness accounting use is
run on that table:

  ws_current      workers.kalshi_market_data.ws_current (REST skips them)
  db_freshness    workers.kalshi_market_data.db_freshness (counted current)
  route           canonical_claims_db.kalshi_books -> kalshi_claims.
                  kalshi_instruments -> canonical_claims.package_book ->
                  canonical_venue.quotes.acquisition_cost at the claim
                  scan's ROUTE_MAX_AGE_S (the decision path and the route
                  comparison: STALE_BOOK / no book are not routable)
  plane_window    market_plane.freshness_window.classify (KALSHI_SQL)
  sentinel        redteam.sentinel.current_books + its MAX_BOOK_AGE_S

and NONE of them may serve a book as current unless the runtime holds that
market's book CURRENT right then -- after a gap (before the next flush,
before the next frame), after the venue's `unsubscribed`, after a
delete_markets (the worker dropped it), after a disconnect, for an
untracked market, and for a market dropped and wanted again while its
pre-delete snapshot was in flight (the safety review's blocking finding).
Right after a flush they serve exactly the CURRENT books, with their levels.

Production 2026-10-09 also left STALE UNTRACKED rows flagged readable: 106
WebSocket-basis rows aged ~19 h and 38 REST rows aged ~44 h. They are seeded
here and no reader may consume any of them as current (each reader's own
age bound, and the WebSocket refresh retires the WS ones).

SMALL LIVE = SHADOW: market data only; nothing here touches an order path.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import types
from decimal import Decimal

import pytest

from sportsassets import canonical_claims as CC
from sportsassets import canonical_claims_db as KCDB
from sportsassets import kalshi_claims as KCL
from sportsassets import kalshi_market_data as KMD
from sportsassets import kalshi_ws as KWS
from sportsassets.canonical_venue import quotes as Q
from sportsassets.market_plane import freshness_window as FW
from sportsassets.redteam import sentinel as SEN
from sportsassets.workers import kalshi_market_data as RESTW
from sportsassets.workers import kalshi_ws_market_data as W
from tests import test_rc62_kalshi_ws_venue_membership as VM

DSN = os.environ.get("RN1X_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

EVENT = "KXPF-26OCT10EV1"
A, B, C = EVENT + "-AAA", EVENT + "-BBB", EVENT + "-TIE"
LIVE = (A, B, C)
STALE_WS = ["KXPFOLD-26OCT09EV%03d-WS" % i for i in range(106)]
STALE_REST = ["KXPFOLD-26OCT08EV%03d-RS" % i for i in range(38)]
ALL = list(LIVE) + STALE_WS + STALE_REST
FIXTURE = KMD.KalshiFixture(
    event_ticker=EVENT, series_ticker="KXPF", sport="soccer", league="epl",
    start_epoch=1_791_950_000.0, home_id="h", away_id="a", home_code="AAA",
    away_code="BBB", tie_ticker=C, team_tickers=(A, B),
    outcome_kind="THREE_WAY", status="ESTABLISHED", reasons=(),
    milestone_id=None)


async def readers(conn, now) -> dict:
    """{reader: set of tickers it serves as a current book now}."""
    out = {"ws_current": await RESTW.ws_current(conn, ALL)}
    fr = await RESTW.db_freshness(conn, ALL, now=now)
    per = set()
    for t in LIVE:
        if (await RESTW.db_freshness(conn, [t], now=now))["numerator"]:
            per.add(t)
    # every current row of the whole set is one of the live markets'
    assert fr["numerator"] == len(per), (fr, per)
    out["db_freshness"] = per
    books = await KCDB.kalshi_books(conn, list(LIVE))
    fx = types.SimpleNamespace(event_key="epl:test")
    routed = set()
    for i in KCL.kalshi_instruments(FIXTURE, {}, books, {}):
        pb = CC.package_book(i, max_age_s=RESTW.ROUTE_MAX_AGE_S)
        if pb is None:
            continue
        c = Q.acquisition_cost(CC.package_instrument(fx, i), pb, 1, now=now,
                               fee_fn=lambda q, p: Decimal(0))
        if c.reason not in ("STALE_BOOK", "BOOK_NOT_EXPLICIT",
                            "BOOK_TIME_IN_FUTURE"):
            routed.add(i.market_id)
    out["route"] = routed
    rows = {r["ticker"]: dict(r) for r in await conn.fetch(FW.KALSHI_SQL,
                                                            ALL)}
    out["plane_window"] = {
        t for t in ALL if FW.classify(
            {"contract_id": t, "venue": FW.VENUE_KALSHI}, mgr=None,
            refreshed={}, entries={}, paper={}, kalshi=rows, now=now,
            sla_s=KMD.BOOK_SLA_S)[0] == FW.C_KALSHI}
    seen = await SEN.current_books(conn, [{"venue": "KALSHI",
                                           "market_id": t} for t in ALL])
    out["sentinel"] = {m for (_v, m), at in seen.items()
                       if 0 <= now - at <= SEN.MAX_BOOK_AGE_S}
    return out


def test_no_reader_serves_a_book_the_runtime_does_not_hold_current():
    record = {"checks": 0, "events": {}, "served_after_flush": []}

    async def go():
        import asyncpg
        conn = await asyncpg.connect(DSN)
        tr = conn.transaction()
        await tr.start()
        try:
            await scenario(conn, record)
        finally:
            await tr.rollback()
            await conn.close()
    asyncio.run(go())
    # every kind of event the proof is about was checked
    for k in ("live", "gap_before_next_frame", "recovered",
              "dropped_delete_sent", "untracked", "rewant_pre_delete_snap",
              "unsubscribed_by_venue", "disconnect"):
        assert record["events"].get(k), (k, record["events"])
    assert record["checks"] > 40


async def scenario(conn, record):
    now0 = time.time()
    # the production residue: untracked rows flagged readable, long stale
    await conn.executemany(
        "INSERT INTO kalshi_books_current (ticker, event_ticker, "
        " series_ticker, yes_asks, book_basis, readable, observed_at) "
        "VALUES ($1, $2, 'KXPFOLD', '[[\"0.40\", 5]]'::jsonb, $3, true, "
        " to_timestamp($4))",
        [(t, t.rsplit("-", 1)[0], KWS.BOOK_BASIS, now0 - 19 * 3600)
         for t in STALE_WS] +
        [(t, t.rsplit("-", 1)[0], KMD.ORDERBOOK_PROTOCOL["basis"],
          now0 - 44 * 3600) for t in STALE_REST])
    assert await conn.fetchval(
        "SELECT count(*) FROM kalshi_books_current WHERE readable AND "
        " ticker = ANY($1::text[])", STALE_WS + STALE_REST) == 144

    lock = asyncio.Lock()
    written: dict = {}
    want = list(LIVE)
    books = KWS.WsBooks(clock=time.time)

    class Pool:
        def acquire(self):
            class Acq:
                async def __aenter__(self):
                    return conn

                async def __aexit__(self, *a):
                    return False
            return Acq()

    async def get_pool():
        return Pool()
    sub = KWS.Subscriber(None, books, wanted=lambda: list(want),
                         clock=time.time, idle_recheck_s=5.0,
                         gap_sink=W.gap_sink(get_pool, books, written, lock))
    state = {"event": "start"}

    async def check(v=None, *, synced=False):
        now = time.time()
        served = await readers(conn, now)
        cur = {t for t in LIVE if books.current(t)["ok"]}
        record["checks"] += 1
        if v is not None and v.delivered_truth is not None:
            # a CURRENT book is the venue's TRUE book of that market as of
            # the highest seq delivered (the venue moves a market it does
            # not hold without a delta)
            for t in sorted(cur):
                assert VM._code_book(books.current(t)) == \
                    v.delivered_truth.get(t), (state["event"], "STALE", t)
        for name, ts in served.items():
            bad = set(ts) - cur
            assert not bad, (state["event"], name, sorted(bad), sorted(cur))
            assert not set(ts) & set(STALE_WS + STALE_REST), name
            if synced:
                assert set(ts) == cur, (state["event"], name, ts, cur)
        if synced:
            record["served_after_flush"].append(sorted(cur))
            for t in cur:
                row = await conn.fetchrow(
                    "SELECT yes_bids FROM kalshi_books_current WHERE "
                    " ticker = $1", t)
                fp = books.current(t)["book"]["orderbook_fp"]["yes_dollars"]
                assert sorted([p, int(Decimal(q))] for p, q in fp) == \
                    sorted(json.loads(row["yes_bids"])), t
        record["events"][state["event"]] = True

    def mark(name):
        async def f(v):
            state["event"] = name
            await check(v)
        return f

    async def flush(v):
        await W.flush(conn, books, written, now=time.time(), lock=lock)
        await check(v, synced=True)

    async def reassert(v):
        await W.reassert(conn, books, now=time.time(), written=written)
        await check(v)

    def refresh(new):
        async def f(v):
            want[:] = list(new)
            W.prune_untracked(sub, written, want)
            await W.retire_untracked(conn, want)
            await check(v)
        return f

    def expect_current(ts):
        async def f(v):
            assert {t for t in LIVE if books.current(t)["ok"]} == set(ts), \
                (state["event"], ts)
        return f

    v = VM.MemberVenue()
    v.aprobe = lambda: check(v)
    v.steps.extend([
        "process", "deliver", "deliver", "deliver", "deliver",
        mark("live"), expect_current(LIVE), flush, reassert,
        # a gap: a delta lost -- every row unreadable before the next
        # frame, before any flush (the pre-gap reader window)
        lambda v_: v_.activity(A, "0.41", "3"), "lose",
        lambda v_: v_.activity(B, "0.42", "4"), "deliver",
        mark("gap_before_next_frame"), expect_current(()), reassert,
        "process",                                # the get_snapshot
        lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
        mark("recovered"), flush, expect_current(LIVE),
        # the worker drops C: its row retired in the same pass, its book
        # gone; the delete goes out with the next idle tick
        refresh([A, B]), mark("untracked"), "idle",
        mark("dropped_delete_sent"), reassert,
        # C wanted again while its pre-delete snapshot is in flight: a
        # get_snapshot answer queued before the delete is processed
        lambda v_: v_.out.append(v_._snap(C)),
        lambda v_: want.append(C), "idle",        # add_markets C
        "deliver",                                # the pre-delete snap
        mark("rewant_pre_delete_snap"), flush,
        "process",                                # delete: ok [A, B]
        lambda v_: v_.activity(C, "0.45", "99"),  # C moves, not streamed
        lambda v_: v_.activity(A, "0.43", "2"),
        "deliver", "deliver", flush, reassert,
        "process", "deliver", "deliver",          # add: ok, C's snapshot
        "process",
        lambda v_: v_.steps.extendleft(["deliver"] * len(v_.out)),
        flush, expect_current(LIVE),
        # the venue ends the subscription: unsubscribed
        lambda v_: v_.out.append({"type": "unsubscribed", "id": 0,
                                  "sid": 1, "seq": v_._seq()}),
        mark("unsubscribed_by_venue"), "deliver"])

    async def session(venue):
        sub.connect = _const(venue)
        try:
            await sub.session()
        except Exception as exc:                                # noqa: BLE001
            return exc
        return None

    end = await session(v)
    state["event"] = "unsubscribed_by_venue"
    assert isinstance(end, KWS.SubscriptionEnded), end
    await check()
    assert {t for t in LIVE if books.current(t)["ok"]} == set()
    # pass 2: a fresh session, all CURRENT, then the connection drops
    v2 = VM.MemberVenue()
    v2.aprobe = lambda: check(v2)
    v2.steps.extend([
        "process", "deliver", "deliver", "deliver", "deliver",
        mark("live"), flush, expect_current(LIVE),
        lambda v_: v_.steps.clear()])
    end = await session(v2)
    state["event"] = "disconnect"
    assert isinstance(end, ConnectionError), end
    await check()
    assert {t for t in LIVE if books.current(t)["ok"]} == set()
    # the disconnect left nothing readable: every row of the live markets
    # is unreadable, named by the disconnect
    errs = {r["ticker"]: (r["readable"], r["error"]) for r in await
            conn.fetch("SELECT ticker, readable, error FROM "
                       " kalshi_books_current WHERE ticker = ANY($1)",
                       list(LIVE))}
    assert all(not r for r, _e in errs.values()), errs
    assert record["served_after_flush"][0] == sorted(LIVE)


def _const(x):
    async def f():
        return x
    return f
