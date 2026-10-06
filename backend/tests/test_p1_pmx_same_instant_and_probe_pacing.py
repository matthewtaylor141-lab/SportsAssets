"""PMX PRIMARY: SAME-INSTANT SAME-BOOK EVIDENCE, A PACED PROBE, A RESTART
THAT RESUBSCRIBES.

Production evidence (release 730325f, institutional_same_book_probe):

  * every pair whose retail transactTime EQUALS the stream read's venue clock
    agreed (43/43 AGREE_TOP_N); every DISAGREE (19) and AGREE_TOUCH_ONLY (39)
    had the retail book 19-28 s OLDER than the stream -- a cached retail
    representation compared against the live book;
  * 536 of the workers probe's retail reads in 60 min answered RateLimitError;
  * after a worker restart the stream sat IDLE_NO_SYMBOLS_REQUESTED: an empty
    focus set backed the loop off 30 s per two refdata bootstraps.

  §1  a sample compares only reads at ONE venue instant; a retail book older
      (newer) than the stream state is NOT_COMPARABLE, named; same-instant
      disagreement is still DISAGREE; an unknown clock compares as before
  §2  the evidence reader applies the same rule to persisted rows: old
      different-instant DISAGREE rows can neither contradict nor count
  §3  the probe spends a retail read only where a comparison is possible:
      none during the venue hold, none for a stream book not current, none
      while the stream state is younger than the retail cache horizon, at most
      the per-pass budget, rotating; deferred members are never persisted
  §4  the worker loop never backs off while the focus universe still has
      refdata to bootstrap or members to subscribe
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from sportsassets import institutional_same_book as SB
from sportsassets import institutional_stream as IS
from sportsassets import p5_runtime as P5R
from sportsassets import paper_market_data as PMD
from sportsassets.workers import institutional_md as WMD

try:
    from tests.test_institutional_contract_map import AEC, SLUG
except ImportError:                                             # pragma: no cover
    from test_institutional_contract_map import AEC, SLUG  # type: ignore

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

T0 = datetime(2026, 10, 6, 18, 27, 43, 123456, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


def retail_md(bids=(("0.45", "10"),), offers=(("0.47", "5"),), ts=None):
    md = {"bids": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                   for p, q in bids],
          "offers": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                     for p, q in offers],
          "state": "MARKET_STATE_OPEN"}
    if ts is not None:
        md["transactTime"] = ts
    return md


def books_at(venue_ts, bids=((450, 1000),), offers=((470, 500),),
             symbol=SLUG):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(symbol, AEC if symbol == SLUG else dict(AEC,
                                                             symbol=symbol))
    b.want([symbol])
    b.on_connected("grpc-test")
    b.on_update({"symbol": symbol, "bids": list(bids), "offers": list(offers),
                 "state": "INSTRUMENT_STATE_OPEN", "transact_time": venue_ts})
    return b


def _iso(dt, ns=False):
    s = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return (s + "789Z") if ns else (s + "Z")


def _read(md):
    return lambda slug: {"ok": True, "marketData": md, "error": None}


def _sample(b, md):
    return SB.sample(SLUG, record=AEC, books_current=b.current,
                     retail_read=_read(md))


# ── §1 the same venue instant ─────────────────────────────────────────

def test_a_same_instant_pair_is_compared_and_agrees():
    b = books_at(T0)
    r = _sample(b, retail_md(ts=_iso(T0, ns=True)))
    assert r["verdict"] == SB.V_AGREE, r


def test_a_retail_book_older_than_the_stream_state_is_not_comparable():
    # production: the retail book 22 s older, a different best bid
    b = books_at(T0)
    r = _sample(b, retail_md(bids=(("0.44", "10"),),
                             ts=_iso(T0 - timedelta(seconds=22))))
    assert r["verdict"] == SB.V_NC
    assert r["verdict_reason"] == SB.NC_RETAIL_OLDER


def test_a_stream_state_older_than_the_retail_book_is_not_comparable():
    b = books_at(T0)
    r = _sample(b, retail_md(ts=_iso(T0 + timedelta(seconds=5))))
    assert r["verdict"] == SB.V_NC
    assert r["verdict_reason"] == SB.NC_STREAM_OLDER


def test_a_same_instant_disagreement_is_still_a_disagreement():
    b = books_at(T0)
    r = _sample(b, retail_md(bids=(("0.30", "10"),), ts=_iso(T0)))
    assert r["verdict"] == SB.V_DISAGREE


def test_an_unknown_retail_clock_compares_as_before():
    b = books_at(T0)
    assert _sample(b, retail_md())["verdict"] == SB.V_AGREE
    assert _sample(b, retail_md(bids=(("0.30", "10"),)))["verdict"] == \
        SB.V_DISAGREE


def test_the_compared_reads_venue_clock_is_the_one_recorded():
    b = books_at(T0)
    r = _sample(b, retail_md(ts=_iso(T0)))
    assert SB._epoch_of(r["stream_venue_ts"]) == pytest.approx(T0.timestamp())


def test_venue_clock_parsing_drops_sub_microseconds_and_refuses_junk():
    assert SB._epoch_of("2026-10-06T18:27:43.123456789Z") == pytest.approx(
        T0.timestamp(), abs=1e-6)
    assert SB._epoch_of("2026-10-06T18:27:43Z") == pytest.approx(
        T0.replace(microsecond=0).timestamp())
    assert SB._epoch_of("yesterday") is None
    assert SB._epoch_of(None) is None


# ── §2 the evidence reader: persisted rows under the same rule ────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)

    async def fetch(self, sql, *a):
        return await self.conn.fetch(sql, *a)


def _row(verdict_md, stream_ts):
    b = books_at(stream_ts)
    return SB.sample(SLUG, record=AEC, books_current=b.current,
                     retail_read=_read(verdict_md))


@pg
def test_old_different_instant_disagreements_neither_contradict_nor_count():
    async def go():
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            # rows recorded BEFORE the rule: a different-instant pair written
            # with verdict DISAGREE and a stable stream (exactly the 19
            # production rows) -- forced through persist as recorded
            old = []
            for i in range(5):
                r = _row(retail_md(bids=(("0.44", "10"),), ts=_iso(T0)), T0)
                r = dict(r, verdict=SB.V_DISAGREE, verdict_reason=None,
                         stream_changed_in_window=False,
                         stream_venue_ts=_iso(T0 + timedelta(seconds=22)))
                old.append(r)
            fresh = [_row(retail_md(ts=_iso(T0)), T0) for _ in range(
                P5R.SAME_BOOK_MIN_COMPARABLE)]
            assert {r["verdict"] for r in fresh} == {SB.V_AGREE}
            await SB.persist(_Pool(conn), old + fresh,
                             process_id="test:same-instant", service="test")
            got = await PMD.same_book_by_symbol(conn, [SLUG])
            assert got[SLUG]["status"] == "SUPPORTED", got
            det = got[SLUG]["detail"]
            assert det["disagree"] == 0 and det["comparable"] == \
                P5R.SAME_BOOK_MIN_COMPARABLE
            assert det["not_comparable"] >= 5
        finally:
            await tx.rollback()
            await conn.close()
    asyncio.run(go())


@pg
def test_a_same_instant_disagreement_on_a_stable_stream_still_contradicts():
    async def go():
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            rows = [_row(retail_md(ts=_iso(T0)), T0) for _ in range(
                P5R.SAME_BOOK_MIN_COMPARABLE)]
            bad = _row(retail_md(bids=(("0.30", "10"),), ts=_iso(T0)), T0)
            assert bad["verdict"] == SB.V_DISAGREE
            await SB.persist(_Pool(conn), rows + [bad],
                             process_id="test:same-instant", service="test")
            got = await PMD.same_book_by_symbol(conn, [SLUG])
            assert got[SLUG]["status"] == "CONTRADICTED", got
        finally:
            await tx.rollback()
            await conn.close()
    asyncio.run(go())


# ── §3 the paced probe ────────────────────────────────────────────────

class _NoPool:
    def __init__(self):
        self.rows = []

    async def execute(self, sql, *a):
        self.rows.append(a)
        return "INSERT 0 1"

    async def fetch(self, sql, *a):
        return []


class _Store:
    def instrument(self, s):
        return {"record": AEC} if s == SLUG else None


def _probe(b, *, gate=None, quiet_s=30.0, max_reads=None, clock=None,
           symbols=(SLUG,), calls=None):
    calls = [] if calls is None else calls

    def read(slug):
        calls.append(slug)
        return {"ok": True, "marketData": retail_md(ts=_iso(T0)),
                "error": None}
    pool = _NoPool()
    out = asyncio.run(WMD.probe_same_book(
        pool, _Store(), list(symbols), process_id="test:probe",
        current=b.current, retail_read=read,
        gate=gate or (lambda: {"blocking": False}),
        clock=clock or (lambda: T0.timestamp() + 120.0),
        quiet_s=quiet_s, max_reads=max_reads))
    return out, calls


def _current_at(b, at):
    return lambda s, now=None: b.current(s, now=at)


def test_no_retail_read_while_the_venue_hold_is_in_force():
    b = books_at(T0)
    out, calls = _probe(b, gate=lambda: {"blocking": True})
    assert calls == [] and out["samples"] == 0
    assert out["deferred"] == {WMD.D_HOLD: 1}


def test_no_retail_read_while_the_stream_state_is_younger_than_the_horizon():
    b = books_at(T0)
    out, calls = _probe(b, clock=lambda: T0.timestamp() + 5.0)
    assert calls == [] and out["deferred"] == {WMD.D_RECENT: 1}


def test_a_quiet_current_stream_book_is_read_and_compared():
    b = books_at(T0)
    out, calls = _probe(b)
    assert calls == [SLUG] and out["retail_reads"] == 1
    assert out["by"] == {SB.V_AGREE: 1} and out["samples"] == 1


def test_no_retail_read_for_a_stream_book_that_is_not_current():
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(SLUG, AEC)
    b.want([SLUG])                  # subscribed, no connection
    out, calls = _probe(b)
    assert calls == [] and out["deferred"] == {WMD.D_STREAM: 1}


def test_the_pass_read_budget_bounds_reads_and_rotates():
    WMD._PROBE_CURSOR["i"] = 0
    b = books_at(T0)
    out, calls = _probe(b, max_reads=3, symbols=[SLUG] * 7)
    assert len(calls) == 3 and out["retail_reads"] == 3
    assert out["deferred"][WMD.D_BUDGET] == 4
    assert out["samples"] == 3          # deferred members never persisted
    assert WMD._PROBE_CURSOR["i"] == 3  # the next pass starts where it ran out


def test_the_default_budget_and_horizon_are_the_named_numbers():
    assert WMD.SAME_BOOK_MAX_READS_PER_PASS == 10
    assert WMD.SAME_BOOK_MIN_QUIET_S == 30.0
    assert SB.SAME_INSTANT_TOLERANCE_S == 0.001


def test_the_probe_reads_the_stream_as_the_held_mark_use():
    import inspect
    assert "istream.current_for_held_mark" in inspect.getsource(
        WMD.probe_same_book)


# ── §4 the restart bootstrap ──────────────────────────────────────────

def test_an_empty_focus_set_backs_off_only_with_no_universe_work():
    assert WMD.loop_sleep_s({"status": "no_focus_set"}) == \
        WMD.BOOTSTRAP_BACKOFF_S
    assert WMD.loop_sleep_s({"status": "no_focus_set",
                             "universeBootstrap": {"read": 2}}) == WMD.SWEEP_S
    assert WMD.loop_sleep_s({"status": "no_focus_set",
                             "universeSubscribed": 12}) == WMD.SWEEP_S
    assert WMD.loop_sleep_s({"status": "no_focus_set",
                             "universePending": 30}) == WMD.SWEEP_S
    assert WMD.loop_sleep_s({"status": "ok"}) == WMD.SWEEP_S


def test_the_run_loop_sleeps_through_loop_sleep_s():
    import inspect
    src = inspect.getsource(WMD.run)
    assert "await asyncio.sleep(loop_sleep_s(stats))" in src
    assert 'stats["universePending"]' in src
