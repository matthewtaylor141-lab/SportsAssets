"""THE STREAM'S RUNTIME EVIDENCE IS RECORDED FROM THE STREAM AND IS DURABLE.

The fake venue is grpcio's own server in process, serving the vendored
`polymarket.v1.MarketDataSubscriptionAPI` (the same FakeVenue the transport
tests use). The client is the production `GrpcBidiTransport`. The recorder
(`institutional_stream_evidence.StreamEvidence`) is attached as the books'
listener exactly as the workers attach it.

  §1  connect, complete book, disconnect, reconnect, fresh book: the minute
      rows carry connection identity + epoch, connects / reconnects, the
      first complete book after the reconnect, gap events opened and closed,
      venue transact_time vs receipt (age, skew), message and update counts,
      instrument state, top-N scaled by the instrument's own scales, and the
      stream's current() answer -- and the counters reset per minute
  §2  a backwards venue clock is a recorded gap event
  §3  persisted to migration 210 (pg): idempotent per (process, symbol,
      minute), counters ADD on a second flush, gap events append; the stored
      current() answer re-evaluates under P5 in another process
  §4  no token reaches a row; the listener never breaks the books
"""

from __future__ import annotations

import json
import os
import time

import asyncpg
import pytest

from sportsassets import institutional_stream as IS
from sportsassets import institutional_stream_evidence as SE
from sportsassets import live_book_currency as LBC

try:
    from tests.test_institutional_md_grpc_transport import (
        GOOD, SYM, Wait, ack, book, books_for, heartbeat,
        in_thread, transport, until, venue)  # noqa: F401  (fixture)
except ImportError:                                             # pragma: no cover
    from test_institutional_md_grpc_transport import (  # type: ignore
        GOOD, SYM, Wait, ack, book, books_for, heartbeat,
        in_thread, transport, until, venue)  # noqa: F401

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


def recorded_books():
    b = books_for()
    rec = SE.StreamEvidence(service="institutional_md")
    b.listener = rec
    return b, rec


def sym_row(rows, sym=SYM):
    return [r for r in rows if r["symbol"] == sym][0]


def proc_row(rows):
    return [r for r in rows if r["symbol"] == SE.PROCESS_SYMBOL][0]


# ── §1 ──────────────────────────────────────────────────────────────────

def test_connect_book_disconnect_reconnect_is_recorded_per_minute(venue):
    b, rec = recorded_books()
    g1, g2 = Wait(), Wait()
    venue.scripts = [[ack(), heartbeat(), book(), book(), g1],
                     [lambda: book(bids=((455, 700),), offers=((465, 300),)),
                      g2]]
    t = transport(b, venue)
    th, out = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"]
          and b.current(SYM)["evidence"]["snapshot"]["updates"] >= 2)
    g1.set()
    th.join(10)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"]
          and b.current(SYM)["evidence"]["connection"]["seq"] == 2)
    rows = rec.rows(b)
    r = sym_row(rows)
    p = proc_row(rows)
    # connection identity and epoch
    assert r["connection_epoch"] == 2 and r["connected"] is True
    assert r["connection_id"].startswith("grpc-")
    assert p["connects_total"] == 2 and p["reconnects_total"] == 1
    assert p["connects_in_minute"] == 2 and p["disconnects_in_minute"] == 1
    assert p["last_disconnect_why"] == "stream completed by the server"
    assert p["last_connect_at"] is not None
    ev = p["extra"]["connection_events_in_minute"]
    assert [e["event"] for e in ev] == ["connect", "disconnected", "connect"]
    # first complete book after the reconnect
    assert r["first_complete_book_at"] is not None
    assert r["first_complete_book_after_connect_s"] is not None
    assert 0 <= r["first_complete_book_after_connect_s"] < 10
    # gap opened by the disconnect, closed by the fresh complete book
    kinds = [(g["event"], g["reason"]) for g in r["gap_events"]]
    assert ("GAP_OPENED", "CONNECTION_DISCONNECTED") in kinds
    assert ("GAP_CLOSED_BY_COMPLETE_BOOK",
            IS.R_GAP_CONNECTION) in kinds
    assert r["gap_open"] is None
    # venue time vs receipt
    assert r["venue_ts"] is not None and r["received_at"] is not None
    assert r["receipt_age_s"] is not None and r["receipt_age_s"] >= 0
    assert abs(r["venue_receipt_skew_s"]) < 5
    assert r["skew_samples"] == 3
    assert r["skew_min_s"] <= r["skew_p50_s"] <= r["skew_max_s"]
    # counts, state, depth
    assert r["updates_total"] == 3 and r["updates_in_minute"] == 3
    assert p["messages_total"] >= 5          # ack, heartbeat, 3 books
    assert r["instrument_state"] == "INSTRUMENT_STATE_OPEN"
    assert r["state_source"] == "STREAM"
    assert (r["price_scale"], r["qty_scale"]) == (1000, 100)
    assert r["top_n"]["bids"] == [{"px": 455, "qty": 700, "price": "0.455",
                                   "size": "7"}]
    assert r["top_n"]["offers"][0]["price"] == "0.465"
    assert r["depth_bids"] == 1 and r["depth_offers"] == 1
    # the stream's own answer, with its evidence
    assert r["current_ok"] is True and r["current_refusal"] is None
    assert r["current_read"]["evidence"]["connection"]["seq"] == 2
    assert r["max_interarrival_s"] is not None
    # the minute counters reset; totals do not
    rows2 = rec.rows(b)
    r2 = sym_row(rows2)
    assert r2["updates_in_minute"] == 0 and r2["updates_total"] == 3
    assert r2["gap_events"] == [] and r2["skew_samples"] == 0
    assert proc_row(rows2)["connects_in_minute"] == 0
    assert proc_row(rows2)["connects_total"] == 2
    g2.set()
    th.join(10)
    # the server ending the second connection is the next minute's gap
    r3 = sym_row(rec.rows(b))
    assert [g["reason"] for g in r3["gap_events"]] == [
        "CONNECTION_DISCONNECTED"]
    assert r3["gap_open"] == IS.R_GAP_CONNECTION and not r3["current_ok"]


# ── §2 ──────────────────────────────────────────────────────────────────

def test_a_backwards_venue_clock_is_a_recorded_gap_event(venue):
    b, rec = recorded_books()
    g1, g2 = Wait(), Wait()
    now = time.time()
    venue.scripts = [[book(at=now), book(at=now - 5, bids=((999, 1),)), g1,
                      lambda: book(at=now + 0.5, bids=((451, 3),)), g2]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["refusal"] == IS.R_GAP_CLOCK)
    r = sym_row(rec.rows(b))
    assert r["regressions_in_minute"] == 1 and r["regressions_total"] == 1
    assert r["gap_open"] == IS.R_GAP_CLOCK and r["current_ok"] is False
    assert [g["reason"] for g in r["gap_events"]] == [
        "VENUE_CLOCK_WENT_BACKWARDS"]
    g1.set()
    until(lambda: b.current(SYM)["ok"])
    r = sym_row(rec.rows(b))
    assert [g["event"] for g in r["gap_events"]] == [
        "GAP_CLOSED_BY_COMPLETE_BOOK"]
    assert r["top_n"]["bids"][0]["px"] == 451     # never the 999
    g2.set()
    th.join(10)


# ── §3 ──────────────────────────────────────────────────────────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)


@pg
async def test_rows_persist_idempotently_and_reevaluate_under_p5(venue):
    b, rec = recorded_books()
    gate = Wait()
    venue.scripts = [[ack(), book(), gate]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    at = time.time()
    rows = rec.rows(b, now=at, identity_for=lambda s: {
        "ok": True, "institutional_symbol": s, "refusal": None})
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        pool = _Pool(conn)
        assert await SE.persist(pool, rows) == 2
        # a second flush in the SAME minute adds counters, appends gaps
        r2 = [dict(x, messages_in_minute=3, updates_in_minute=1,
                   gap_events=[{"event": "GAP_OPENED", "reason": "X"}])
              for x in rows]
        assert await SE.persist(pool, r2) == 2
        got = await conn.fetch(
            "SELECT * FROM institutional_stream_evidence WHERE process_id=$1"
            " ORDER BY symbol", rec.process_id)
        assert [g["symbol"] for g in got] == [SE.PROCESS_SYMBOL, SYM]
        s = dict(got[1])
        assert s["updates_in_minute"] == rows[1]["updates_in_minute"] + 1
        assert len(json.loads(s["gap_events"])) == \
            len(rows[1]["gap_events"]) + 1
        assert s["connection_epoch"] == 1 and s["current_ok"] is True
        assert s["minute"].second == 0
        # another process re-evaluates P5 on the recorded current() answer
        read = SE.stream_read_from_row(s)
        v = LBC.evaluate(stream_read=read,
                         identity={"status": "EXACT", "symbol": SYM},
                         now=s["evaluated_at_epoch"], priced_from=None)
        comps = {c["component"]: c["passed"] for c in v["components"]}
        for cid in ("C1_IDENTITY_EXACT", "C2_STREAM_RUNNING",
                    "C3_CONNECTION_EPOCH_ALIVE",
                    "C4_COMPLETE_BOOK_ON_THIS_EPOCH", "C6_VENUE_TS_PRESENT",
                    "C7_VENUE_TS_MONOTONIC_IN_EPOCH", "C8_RECEIPT_AGE",
                    "C9_VENUE_RECEIPT_SKEW", "C10_MARKET_OPEN",
                    "C11_STREAM_BOOK_CURRENT"):
            assert comps[cid] is True, cid
        assert v["stream_book_verdict"] == LBC.ESTABLISHED
        # a minute that is not truncated is refused by the table
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO institutional_stream_evidence (minute, "
                    "symbol, process_id, service) VALUES "
                    "(now() - interval '1.5 seconds', 'x', 'p', 's')")
    finally:
        await tx.rollback()
        await conn.close()
    gate.set()
    th.join(10)


def test_persist_never_raises_on_a_broken_pool():
    class Broken:
        async def execute(self, *a):
            raise RuntimeError("connection reset")
    import asyncio
    assert asyncio.run(SE.persist(Broken(), [{"symbol": "x"}])) == 0


# ── §4 ──────────────────────────────────────────────────────────────────

def test_no_token_reaches_a_row_and_a_raising_listener_is_ignored(venue):
    b, rec = recorded_books()
    gate = Wait()
    venue.scripts = [[book(), gate]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    assert GOOD not in json.dumps(rec.rows(b), default=str)

    def boom(event, data):
        raise RuntimeError("listener bug")
    b.listener = boom
    b.on_heartbeat()
    b.on_update({"symbol": SYM, "bids": [(1, 1)], "offers": [],
                 "transact_time": None})
    assert b.current(SYM)["evidence"]["snapshot"]["updates"] >= 2
    gate.set()
    th.join(10)


def test_install_is_idempotent_per_books():
    b = IS.ResidentBooks()
    r1 = SE.install(b)
    assert SE.install(b) is r1 and b.listener is r1
    assert r1.process_id.startswith("institutional_md:")


def test_scaled_levels_use_the_instruments_own_scales_never_a_default():
    assert SE.scaled_levels([(450, 1000)], 1000, 100) == [
        {"px": 450, "qty": 1000, "price": "0.45", "size": "10"}]
    assert SE.scaled_levels([(450, 1000)], None, None) == [
        {"px": 450, "qty": 1000}]


# ── §5 the workers wiring ───────────────────────────────────────────────

def test_the_worker_attaches_the_recorder_before_the_stream_starts():
    import inspect
    from sportsassets.workers import institutional_md as W
    src = inspect.getsource(W.run)
    i = src.index("sevid.install(istream.BOOKS")
    assert "if istream.enabled() else None" in src[i:i + 120]
    assert i < src.index("istream.start_default()")
    # recorded and probed only when the recorder exists (stream enabled)
    assert "if recorder is not None and \\" in src
    assert 'not _off("INSTITUTIONAL_SAME_BOOK_PROBE")' in src
    assert W.STREAM_EVIDENCE_EVERY_S == 60.0 and W.SAME_BOOK_EVERY_S == 60.0


async def test_record_stream_evidence_writes_one_row_per_symbol_plus_process():
    from sportsassets.workers import institutional_md as W
    from tests.test_institutional_contract_map import AEC, SLUG

    class Store:
        def instrument(self, s):
            return {"record": AEC} if s == SLUG else None

    class Pool:
        def __init__(self):
            self.sql = []

        async def fetch(self, sql, *a):
            return []                        # no retail rows needed (aec)

        async def execute(self, sql, *a):
            self.sql.append((sql, a))
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "t")
    b.set_instrument(SLUG, AEC)
    b.want([SLUG, "aec-nfl-x-y-2026-10-04"])
    rec = SE.StreamEvidence()
    b.listener = rec
    b.on_connected("grpc-t")
    b.on_update({"symbol": SLUG, "bids": [(450, 100)], "offers": [(470, 100)],
                 "state": "INSTRUMENT_STATE_OPEN", "transact_time": time.time()})
    pool = Pool()
    out = await W.record_stream_evidence(pool, rec, Store(), books=b)
    assert out == {"rows": 3, "written": 3}
    idx = SE.COLUMNS.index("identity")
    ids = {a[SE.COLUMNS.index("symbol")]: json.loads(a[idx])
           for _s, a in pool.sql}
    assert ids[SLUG]["ok"] is True
    assert ids[SLUG]["institutional_symbol"] == SLUG
    assert ids["aec-nfl-x-y-2026-10-04"]["ok"] is False
    assert all(s.startswith("INSERT INTO institutional_stream_evidence")
               for s, _a in pool.sql)
