"""THE RECONCILER NAMES EVERY LATE FILL AND EVERY HOLE IT LEAVES (RC6 identity lane).

Production, release 732cc0c6, reconciler run 5698 (finished 2026-10-09
03:35:42Z) logged "reconciliation ingested 10 missed fills: {'cov:0x...':
{'complete': False, ...}, ..." -- the whole per-wallet dict, twenty wallet
addresses long, which the log line cut off; it named no fill. The ten were
found only by elimination (research-sql run 37928503493): whale 40's poll
rows trade_id 227269616..227269628, fills at 03:24:27-03:25:11Z inserted
together at 03:35:34Z, 623-667 s after the fill, with no live order, AI
follower trade or rn1x paper position derived from any of them.

And no run ever compared its reach with the run before it: in the 34 runs of
2026-10-08/09 whale 40's walk stopped short of the previous run's newest row
in 14 (30,213 s swept by no run, worst 7,156 s), whale 26 in 7 (9,481 s),
whale 2 in one (339 s), while the heartbeat read 'ok' / 'drift'.

These tests drive the REAL reconcile_once walk (the crafted feed server of
test_reconciler_walk) and pin: every late fill itemised by trade id with what
our books derived from it; a bounded log line by whale id with no address;
the coverage hole between consecutive runs measured, named and never 'ok';
and the walk's own requests unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from types import SimpleNamespace

import pytest

import sportsassets.ingestion.reconciler as rec
from sportsassets import refusal_taxonomy_table as TT
from tests import test_reconciler_walk as W

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


class _Pool(W._FakePool):
    """The walk fixture's pool with a previous run and book links."""

    def __init__(self, prev_details=None, links=None):
        super().__init__()
        self.prev_details = prev_details
        self.links = links or {}
        self.stmts: list = []

    async def fetchrow(self, sql, *a, timeout=None):
        self.stmts.append(("fetchrow", sql, list(a)))
        if self.prev_details is None:
            return None
        return {"id": 5697, "details": json.dumps(self.prev_details)}

    async def fetch(self, sql, *a, timeout=None):
        self.stmts.append(("fetch", sql, list(a)))
        if "FROM trades t WHERE t.id = ANY" in sql:
            return [dict(self.links.get(i, {}), id=i,
                         live_orders=self.links.get(i, {}).get("live_orders", 0),
                         ai_trades=self.links.get(i, {}).get("ai_trades", 0),
                         rn1x_positions=self.links.get(i, {}).get(
                             "rn1x_positions", 0))
                    for i in a[0]]
        return [{"id": 40, "address": W.WALLET, "username": "w"}]


def _wire(monkeypatch, feed, pool, *, new_ids=()):
    calls, beats = [], []
    new_ids = set(new_ids)
    tid = {"n": 227269600}

    async def fake_get(http, path, params=None):
        calls.append(int(params["offset"]))
        return W._Resp(feed[params["offset"]:params["offset"] + 100])

    async def fake_pool():
        return pool

    async def fake_hb(name, status, detail=None):
        beats.append((name, status, detail))

    async def fake_sport(cond):
        return None

    async def fake_ingest(ev, notify=True):
        # rows whose asset index is in new_ids are first inserts (late
        # fills); every other row is a dedupe no-op
        i = int(ev.asset) - 10_000
        if i in new_ids:
            tid["n"] += 1
            return (tid["n"], True)
        return (1, False)

    monkeypatch.setattr(rec, "polite_get", fake_get)
    monkeypatch.setattr(rec, "get_pool", fake_pool)
    monkeypatch.setattr(rec, "heartbeat", fake_hb)
    monkeypatch.setattr(rec, "_sport_for_condition", fake_sport)
    monkeypatch.setattr(rec, "ingest_trade_result", fake_ingest)
    monkeypatch.setattr(
        rec, "settings",
        lambda: SimpleNamespace(data_api_base="http://feed.test"))
    return calls, beats


def _prev(newest: float) -> dict:
    return {"per_wallet": {"cov:" + W.WALLET: {
        "complete": False, "oldest": newest - 2000.0, "newest": newest,
        "dirty": 0}, W.WALLET: 0}}


# ── the hole: a burst wallet's walk stops short of the previous run ───

def test_a_walk_that_stops_short_of_the_previous_run_is_a_named_gap_never_ok(
        monkeypatch, caplog):
    """A burst wallet: 700 fills at 10 s apart span under two hours, and the
    previous run's newest row is 3 h before this run's newest -- the walk's
    600 rows (depth + border) do not reach it. The seconds between are swept
    by no run: a named refusal on a non-success heartbeat."""
    feed = W._feed()
    prev_newest = float(W.TOP_TS - 3 * 3600)
    pool = _Pool(prev_details=_prev(prev_newest))
    calls, beats = _wire(monkeypatch, feed, pool)
    with caplog.at_level(logging.WARNING, logger=rec.__name__):
        out = asyncio.run(rec.reconcile_once(depth=500))
    reach = float(W.TOP_TS - 681 * W.STEP)        # the border page's tail
    cov = out["coverage"]
    assert cov["wallets_with_hole"] == 1 and cov["reached"] == 0
    assert cov["holes"] == [{"whale_id": 40, "hole_s": reach - prev_newest,
                             "from_ts": prev_newest, "to_ts": reach}]
    assert cov["refusal"] == rec.R_COVERAGE_HOLE
    assert cov["previous_run_id"] == 5697
    (name, status, detail), = beats
    assert name == "reconciler" and status == rec.STATUS_COVERAGE_GAP
    assert status not in ("ok", "drift")
    assert detail["refusal"] == rec.R_COVERAGE_HOLE
    assert detail["wallets_with_hole"] == 1
    assert detail["hole_seconds"] == reach - prev_newest
    # the details row carries the same account
    assert pool.details["coverage"]["holes"][0]["whale_id"] == 40
    # the log names the gap by whale id, never the address
    text = caplog.text
    assert rec.R_COVERAGE_HOLE in text and "whale id 40" in text
    assert W.WALLET not in text
    # THE WALK'S REQUESTS ARE UNCHANGED: depth + one border page
    assert calls == [0, 97, 194, 291, 388, 485, 582]


def test_a_late_indexed_old_row_mid_walk_cannot_fake_the_reach(monkeypatch):
    """The round-21 shape: one genuinely old row served mid-page. A bare
    min() over the ingested rows would read it as the walk's reach and call
    the burst wallet continuous; the reach is the DEEPEST row served."""
    feed = W._feed()
    prev_newest = float(W.TOP_TS - 3 * 3600)
    feed[50] = W._raw(int(prev_newest) - 86_400, 50)   # a day older
    pool = _Pool(prev_details=_prev(prev_newest))
    _calls, beats = _wire(monkeypatch, feed, pool)
    out = asyncio.run(rec.reconcile_once(depth=500))
    assert out["coverage"]["wallets_with_hole"] == 1
    # the misordered row dirtied the walk, so no border page: the deepest
    # row served is the last in-cap page's tail, row 584
    assert pool.details["per_wallet"]["cov:" + W.WALLET]["dirty"] > 0
    assert out["coverage"]["holes"][0]["to_ts"] == float(
        W.TOP_TS - 584 * W.STEP)
    assert beats[0][1] == rec.STATUS_COVERAGE_GAP


def test_a_walk_that_reaches_the_previous_run_is_continuous(monkeypatch):
    feed = W._feed()
    pool = _Pool(prev_details=_prev(float(W.TOP_TS - 3000)))
    _calls, beats = _wire(monkeypatch, feed, pool)
    out = asyncio.run(rec.reconcile_once(depth=500))
    assert out["coverage"]["reached"] == 1
    assert out["coverage"]["wallets_with_hole"] == 0
    assert out["coverage"]["refusal"] is None
    assert beats[0][1] == "ok"


def test_no_previous_run_is_unmeasured_never_a_hole(monkeypatch):
    pool = _Pool(prev_details=None)
    _calls, beats = _wire(monkeypatch, W._feed(), pool)
    out = asyncio.run(rec.reconcile_once(depth=500))
    assert out["coverage"]["unmeasured"] == [40]
    assert out["coverage"]["wallets_with_hole"] == 0
    assert beats[0][2]["unmeasured"] == 1


# ── the late fills: itemised by trade id, reconciled to our books ─────

def test_every_late_fill_is_itemised_with_its_book_links_and_a_bounded_log(
        monkeypatch, caplog):
    """Ten rows the poller never saw land as first inserts: each is in
    details.missed_fills by trade id, side, fill time, ingest lag and what
    our books derived from it; the warning is one bounded line by whale id
    -- the per-wallet dict (with every wallet's address) is never logged."""
    feed = W._feed()
    late = list(range(300, 310))
    links = {227269601: {"ai_trades": 1}}       # one derived AI-follower row
    pool = _Pool(prev_details=_prev(float(W.TOP_TS - 3000)), links=links)
    _calls, beats = _wire(monkeypatch, feed, pool, new_ids=late)
    with caplog.at_level(logging.WARNING, logger=rec.__name__):
        out = asyncio.run(rec.reconcile_once(depth=500))
    assert out["missed"] == 10
    mf = out["missed_fills"]
    assert [m["trade_id"] for m in mf] == list(range(227269601, 227269611))
    assert {m["whale_id"] for m in mf} == {40}
    assert [m["fill_ts"] for m in mf] == [float(W.TOP_TS - i * W.STEP)
                                          for i in late]
    assert all(m["side"] == "BUY" and m["ingest_lag_s"] > 0 for m in mf)
    assert mf[0]["book_links"] == {"live_orders": 0, "ai_trades": 1,
                                   "rn1x_positions": 0}
    assert all(m["book_links"] == {"live_orders": 0, "ai_trades": 0,
                                   "rn1x_positions": 0} for m in mf[1:])
    assert pool.details["missed_fills"] == mf
    assert pool.details["missed_fills_truncated"] is False
    # the book-links read is ONE statement over the late trade ids
    q = [s for s in pool.stmts if "FROM trades t WHERE t.id = ANY" in s[1]]
    assert len(q) == 1 and q[0][2] == [list(range(227269601, 227269611))]
    assert beats[0][1] == "drift"
    assert beats[0][2]["missed_fills_recorded"] == 10
    warn = [r.getMessage() for r in caplog.records
            if "missed fill" in r.getMessage()]
    assert len(warn) == 1
    assert "40=10" in warn[0] and "details.missed_fills" in warn[0]
    assert W.WALLET not in caplog.text and "cov:" not in caplog.text


def test_the_itemised_list_is_bounded_and_says_so(monkeypatch):
    monkeypatch.setattr(rec, "MISSED_RECORD_MAX", 3)
    pool = _Pool(prev_details=_prev(float(W.TOP_TS - 3000)))
    _wire(monkeypatch, W._feed(), pool, new_ids=range(10, 18))
    out = asyncio.run(rec.reconcile_once(depth=500))
    assert out["missed"] == 8 and len(out["missed_fills"]) == 3
    assert pool.details["missed_fills_truncated"] is True


# ── pure parts ────────────────────────────────────────────────────────

def test_previous_newest_reads_only_wallets_whose_walk_did_not_fail():
    d = {"per_wallet": {
        "cov:a": {"newest": 1_791_000_000.0}, "a": 0,
        "cov:b": {"newest": 1_791_000_100.0}, "failed:b": 1,
        "cov:c": {"newest": None}, "cov:d": {"newest": True},
        "cov:e": {"newest": 5.0}}}
    assert rec.previous_newest(d) == {"a": 1_791_000_000.0}
    assert rec.previous_newest(json.dumps(d)) == {"a": 1_791_000_000.0}
    assert rec.previous_newest(None) == {} and rec.previous_newest("x") == {}


def test_coverage_continuity_classifies_reached_hole_and_unmeasured():
    prev = {"a": 1000.0, "b": 1000.0, "c": 1000.0, "e": 1000.0}
    walks = [
        {"whale_id": 1, "address": "a", "reach_oldest": 900.0},     # reached
        {"whale_id": 2, "address": "b", "reach_oldest": 1500.0},    # hole 500
        {"whale_id": 3, "address": "c", "reach_oldest": 2000.0,
         "complete": True},                                        # exhausted
        {"whale_id": 4, "address": "d", "reach_oldest": 1.0},       # no prev
        {"whale_id": 5, "address": "e", "reach_oldest": None,
         "failed": True},                                          # failed
        {"whale_id": 6, "address": "a", "reach_oldest": 1000.0}]    # equal
    c = rec.coverage_continuity(prev, walks)
    assert c["reached"] == 3 and c["wallets_with_hole"] == 1
    assert c["holes"] == [{"whale_id": 2, "hole_s": 500.0, "from_ts": 1000.0,
                           "to_ts": 1500.0}]
    assert c["unmeasured"] == [4, 5] and c["refusal"] == rec.R_COVERAGE_HOLE
    assert c["hole_seconds"] == 500.0 and c["max_hole_s"] == 500.0


def test_run_status_never_reads_a_hole_as_ok():
    hole = {"wallets_with_hole": 1}
    assert rec.run_status(0, 0, 3, hole) == rec.STATUS_COVERAGE_GAP
    assert rec.run_status(5, 0, 3, hole) == rec.STATUS_COVERAGE_GAP
    assert rec.run_status(0, 3, 3, hole) == "error"
    assert rec.run_status(2, 0, 3, {}) == "drift"
    assert rec.run_status(0, 0, 3, {"wallets_with_hole": 0}) == "ok"


def test_the_coverage_refusal_is_classified():
    cls, fam, _stage = TT.TABLE[rec.R_COVERAGE_HOLE]
    assert cls == "SOFTWARE" and fam == "FRESHNESS_PLUMBING"


# ── the two new statements against the real schema ────────────────────

@pg
def test_the_previous_coverage_and_book_link_reads_run_on_postgres():
    asyncpg = pytest.importorskip("asyncpg")

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            wid = await c.fetchval(
                "INSERT INTO whales (address, username) VALUES "
                "('0xidentitylanetestwallet', 'identity-lane-test') "
                "RETURNING id")
            tids = []
            for i in range(2):
                tids.append(await c.fetchval(
                    "INSERT INTO trades (whale_id, tx_hash, asset, side, "
                    "size, price, notional, ts, source, detected_at, "
                    "dedupe_key) VALUES ($1, $2, '777', 'BUY', 10, 0.5, 5, "
                    "now() - interval '11 minutes', 'poll', now(), $3) "
                    "RETURNING id", wid, "0xtx%d" % i, "idl-test-%d" % i))
            await c.execute(
                "INSERT INTO live_orders (trade_id, asset, side, his_price, "
                "limit_price, requested_usd, requested_shares, status) "
                "VALUES ($1, '777', 'BUY', 0.5, 0.5, 5, 10, 'unfilled')",
                tids[0])
            r1 = await c.fetchval(
                "INSERT INTO reconciliation_runs (finished_at, missed, "
                "details) VALUES (now(), 0, $1::jsonb) RETURNING id",
                json.dumps(_prev(1_791_000_000.0)))
            r2 = await c.fetchval(
                "INSERT INTO reconciliation_runs DEFAULT VALUES RETURNING id")
            prev = await c.fetchrow(rec.PREV_COVERAGE_SQL, r2)
            links = {r["id"]: dict(r) for r in await c.fetch(
                rec.BOOK_LINKS_SQL, tids)}
            return r1, prev, links, tids
        finally:
            await tx.rollback()
            await c.close()

    r1, prev, links, tids = asyncio.run(main())
    assert prev["id"] == r1
    assert rec.previous_newest(prev["details"]) == {W.WALLET: 1_791_000_000.0}
    assert links[tids[0]]["live_orders"] == 1
    assert links[tids[1]]["live_orders"] == 0
    assert links[tids[0]]["ai_trades"] == 0
    assert links[tids[0]]["rn1x_positions"] == 0
