"""THE POSITIONS DEAD-LETTER IS A COUNTED, CLASSIFIED CENSUS (RC6 identity lane).

Production, release 732cc0c6 (workers log 2026-10-09 04:20-05:20Z): every
analytics cycle logged "positions persist: 10902 row(s) still missing
condition_id after token-catalog rescue -- dead-lettered" -- a bare count,
no owner, no age, the same line every ~6 minutes. Research-sql run
37927888187 classified all 10,902: six pinned TRACKED research wallets (whale
ids 1, 2, 26, 5, 3, 21), every one holding shares with no fill since
2026-09-05 15:25Z (34+ days), chain-lane tokens the catalog never held; and
OUR books -- paper (0 open), live_orders (51 open, every one with a venue
slug and a catalog token), small-live / funded / Kalshi (none) -- hold no
ACTIVE position without identity.

These tests pin the census (owner x historical-debt / active), its log on
CHANGE only, the named refusal for an ACTIVE position of our own, the
snapshot persisting exactly as before, and the analytics heartbeat carrying
it (a refusal is never an 'ok' beat).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sportsassets.analytics import engine as eng
from sportsassets.analytics import identity_census as IC
from sportsassets.analytics.positions import Fill, Position
from sportsassets import refusal_taxonomy_table as TT

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

NOW = datetime(2026, 10, 9, 12, 6, 42, tzinfo=timezone.utc)
OLD = datetime(2026, 9, 5, 15, 25, 39, tzinfo=timezone.utc)   # prod newest


def _state(whale, tok, *, cid=None, last=OLD, buys=((10, 0.5),), sells=(),
           resolved=False):
    p = Position()
    for size, px in buys:
        p.apply(Fill("BUY", size, px))
    for size, px in sells:
        p.apply(Fill("SELL", size, px))
    if resolved:
        p.resolve(1.0)
    return eng.PositionState(
        whale_id=whale, condition_id=cid, token_id=tok, outcome="X",
        outcome_index=0, sport="unclassified", position=p, as_of=NOW,
        first_ts=last - timedelta(hours=1), last_ts=last)


@pytest.fixture(autouse=True)
def _fresh_signature():
    IC._LAST["signature"] = None
    IC._OWN_HIST.update(at=None, rows=None, as_of=None, error=None)
    yield
    IC._LAST["signature"] = None
    IC._OWN_HIST.update(at=None, rows=None, as_of=None, error=None)


def _own_rows(active=None, hist=None):
    """OWN_ACTIVE_SQL / OWN_HISTORICAL_SQL rows: every book read, zeros
    unless given ({book: (rows, unknown)})."""
    a = [{"book": b, "rows_n": (active or {}).get(b, (0, 0))[0],
          "unknown_n": (active or {}).get(b, (0, 0))[1]}
         for b in IC.OWN_BOOKS]
    h = [{"book": b, "rows_n": (hist or {}).get(b, (0, 0))[0],
          "unknown_n": (hist or {}).get(b, (0, 0))[1], "newest_at": OLD}
         for b, (kind, _u) in IC.OWN_BOOKS.items() if kind == "PAPER"]
    return a, h


# ── the census, pure ──────────────────────────────────────────────────

def test_production_shape_is_historical_research_debt_with_no_refusal():
    states = ([_state(1, "t%d" % i) for i in range(7)]
              + [_state(2, "u%d" % i) for i in range(3)])
    c = IC.census(states, now=NOW)
    assert c["dead_lettered"] == 10 and c["historical_debt"] == 10
    assert c["active"] == 0 and c["own_active"] == 0
    assert c["status"] == IC.ST_HISTORICAL and c["refusal"] is None
    assert c["by_owner"] == {IC.OWNER_RESEARCH: {
        IC.HISTORICAL: 10, IC.ACTIVE: 0,
        "by_class": {IC.C_OPEN_STALE: 10}}}
    assert [(w["whale_id"], w["states"]) for w in c["by_whale"]] == [
        (1, 7), (2, 3)]
    assert c["by_whale"][0]["last_fill_at"] == OLD.isoformat()


def test_each_class_and_the_30_day_line():
    recent = NOW - timedelta(days=2)
    edge = NOW - IC.ACTIVE_WINDOW                 # exactly on the line
    states = [_state(1, "flat", buys=((10, 0.5),), sells=((10, 0.6),)),
              _state(1, "res", resolved=True),
              _state(1, "stale"),
              _state(1, "recent", last=recent),
              _state(1, "edge", last=edge)]
    assert [IC.classify(s, NOW) for s in states] == [
        IC.C_FLAT, IC.C_RESOLVED, IC.C_OPEN_STALE, IC.C_OPEN_RECENT,
        IC.C_OPEN_RECENT]
    c = IC.census(states, now=NOW)
    assert c["historical_debt"] == 3 and c["active"] == 2
    assert c["status"] == IC.ST_ACTIVE_RESEARCH and c["refusal"] is None
    assert len(c["active_examples"]) == 2


def test_an_active_position_of_our_own_without_identity_is_refused_by_name():
    states = [_state(1, "res-old"),
              _state(9, "ours-live", last=NOW - timedelta(hours=3)),
              _state(9, "ours-old")]
    c = IC.census(states, now=NOW, own_whale_ids={9}, own_wallets_configured=1)
    assert c["refusal"] == IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN
    assert c["status"] == IC.ST_REFUSED and c["own_active"] == 1
    assert c["by_owner"][IC.OWNER_OWN] == {
        IC.HISTORICAL: 1, IC.ACTIVE: 1,
        "by_class": {IC.C_OPEN_RECENT: 1, IC.C_OPEN_STALE: 1}}
    assert c["active_examples"][0]["owner"] == IC.OWNER_OWN
    assert c["active_examples"][0]["token_id"] == "ours-live"
    # an own wallet's HISTORICAL debt alone is counted, never refused
    c2 = IC.census([_state(9, "ours-old")], now=NOW, own_whale_ids={9})
    assert c2["refusal"] is None and c2["status"] == IC.ST_HISTORICAL


def test_no_dead_letter_is_clean():
    c = IC.census([], now=NOW)
    assert c["status"] == IC.ST_CLEAN and c["dead_lettered"] == 0


def test_the_refusal_is_classified():
    cls, fam, _ = TT.TABLE[IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN]
    assert (cls, fam) == ("SOFTWARE", "MAPPING")


# ── the persist path: logged on CHANGE, snapshot unchanged ────────────

class _Ctx:
    def __init__(self, obj=None):
        self.obj = obj

    async def __aenter__(self):
        return self.obj

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, sink):
        self.sink = sink

    def transaction(self):
        return _Ctx()

    async def execute(self, sql, *a):
        self.sink["deleted"] = True

    async def executemany(self, sql, rows):
        self.sink["rows"] = rows


class _Pool:
    def __init__(self, own_ids=(), own_active=None, own_hist=None,
                 own_fails=False):
        self.sink = {}
        self.own_ids = list(own_ids)
        self.sql: list = []
        self.own = _own_rows(own_active, own_hist)
        self.own_fails = own_fails

    async def fetch(self, sql, arg):
        self.sql.append(sql)
        if "FROM whales" in sql:
            return [{"id": i} for i in self.own_ids]
        if sql in (IC.OWN_ACTIVE_SQL, IC.OWN_HISTORICAL_SQL):
            if self.own_fails:
                raise RuntimeError("own books unreadable")
            return self.own[0 if sql == IC.OWN_ACTIVE_SQL else 1]
        assert "market_tokens" in sql
        return [{"token_id": "tok-b", "condition_id": "0xrescued"}]

    def acquire(self):
        return _Ctx(_Conn(self.sink))


def _wire(monkeypatch, pool, funder=""):
    async def fake_get_pool():
        return pool
    monkeypatch.setattr(eng, "get_pool", fake_get_pool)
    import sportsassets.config as cfg
    monkeypatch.setattr(cfg, "settings",
                        lambda: SimpleNamespace(pm_funder=funder))


def _book():
    return [_state(2, "tok-a", cid="0xok"),       # enriched
            _state(2, "tok-b"),                    # rescued by the catalog
            _state(2, "tok-c"),                    # dead-lettered, 34 days
            _state(1, "tok-d")]                    # dead-lettered, 34 days


def test_the_dead_letter_logs_once_and_again_only_when_it_changes(
        monkeypatch, caplog):
    """THE BASE LOGGED THE SAME BARE COUNT ON EVERY CYCLE (10,902, every
    ~6 minutes). The census logs its first reading, stays quiet while it is
    unchanged, and logs again when the counts move."""
    pool = _Pool()
    _wire(monkeypatch, pool)
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        for _ in range(3):
            asyncio.run(eng._persist_positions(_book()))
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warns) == 1, [r.getMessage() for r in warns]
    msg = warns[0].getMessage()
    assert "2 dead-lettered" in msg and IC.OWNER_RESEARCH in msg
    assert "HISTORICAL_DEBT 2" in msg and IC.C_OPEN_STALE in msg
    # the snapshot persists exactly as before: the good and the rescued
    rows = pool.sink["rows"]
    assert {r[1] for r in rows} == {"0xok", "0xrescued"}
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        asyncio.run(eng._persist_positions(_book() + [_state(5, "tok-e")]))
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warns) == 1 and "3 dead-lettered" in warns[0].getMessage()
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["dead_lettered"] == 3 and c["refusal"] is None
    assert c["own_wallets_configured"] == 0


def test_an_own_active_dead_letter_is_an_error_and_a_named_refusal(
        monkeypatch, caplog):
    pool = _Pool(own_ids=[2])
    _wire(monkeypatch, pool, funder="0xOURFUNDER")
    book = _book() + [_state(2, "tok-live", last=NOW - timedelta(hours=1))]
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        asyncio.run(eng._persist_positions(book))
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errs) == 1
    assert IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN in errs[0].getMessage()
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["refusal"] == IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN
    assert c["own_active"] == 1 and c["own_wallets_configured"] == 1
    assert any("FROM whales" in s for s in pool.sql)
    # still nothing rescued past the catalog, nothing deleted from view
    assert {r[1] for r in pool.sink["rows"]} == {"0xok", "0xrescued"}


def test_no_own_wallet_configured_reads_no_roster(monkeypatch):
    pool = _Pool(own_ids=[2])
    _wire(monkeypatch, pool, funder="")
    asyncio.run(eng._persist_positions(_book()))
    assert not any("FROM whales" in s for s in pool.sql)
    assert eng.LAST_IDENTITY_CENSUS["census"]["own_wallets_configured"] == 0


# ── the heartbeat ─────────────────────────────────────────────────────

def test_the_analytics_heartbeat_carries_the_census_and_never_oks_a_refusal(
        monkeypatch):
    from sportsassets.workers import analytics as W

    beats = []

    async def fake_heartbeat(service, status, detail):
        beats.append((service, status, detail))
        if status not in ("running",):
            raise asyncio.CancelledError

    for refusal, want in ((None, "ok"),
                          (IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN, "refused")):
        beats.clear()
        census = IC.summary(IC.census([], now=NOW))
        census["refusal"] = refusal

        async def fake_cycle(census=census, refusal=refusal):
            return {"positions": 1, "rollup_rows": 0, "drift_alerts": [],
                    "engine_settled": 0, "ai_settled": 0,
                    "rss_mb_by_step": {}, "identity_debt": census,
                    "refusal": refusal}

        async def fake_sweep(client):
            return 0

        monkeypatch.setattr(W, "heartbeat", fake_heartbeat)
        monkeypatch.setattr(W, "run_cycle", fake_cycle)
        monkeypatch.setattr(W, "sweep_resolutions", fake_sweep)
        monkeypatch.setattr(W.gamma, "GammaClient", lambda: object())
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(W.main())
        (_, s1, _), (_, s2, d2) = beats
        assert s1 == "running" and s2 == want
        assert d2["identity_debt"]["version"] == IC.VERSION
        assert d2["refusal"] == refusal


# ── the roster read on the real schema ────────────────────────────────

@pg
def test_the_own_wallet_roster_read_runs_on_postgres(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    import sportsassets.config as cfg
    monkeypatch.setattr(cfg, "settings", lambda: SimpleNamespace(
        pm_funder="0xIdentityLaneOwnFunder"))

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            wid = await c.fetchval(
                "INSERT INTO whales (address, username) VALUES "
                "('0xidentitylaneownfunder', 'identity-lane-own') "
                "RETURNING id")
            got = await eng._own_whale_ids(c)
            return wid, got
        finally:
            await tx.rollback()
            await c.close()

    wid, (ids, n) = asyncio.run(main())
    assert ids == {wid} and n == 1


# ── OUR OWN BOOKS (read directly: our positions are never in the replay) ──
# Production (research-sql run 37927888187, 2026-10-09): live_orders 51 open,
# 0 without identity; the legacy PAPER AI follower 328,669 open rows, every
# one placed over 30 days ago, 68,449 with no condition and a token the
# catalog never held; paper_fills / rn1x 0; small live, funded, Kalshi and
# registered books empty. Before this, the census could name an own position
# only when our funder address was on the tracked roster -- which it is not
# -- so its refusal could never fire for a row of ours.

PROD_ACTIVE = {IC.BOOK_LIVE: (51, 0), IC.BOOK_RN1X: (40, 0),
               IC.BOOK_PAPER: (210, 0)}
PROD_HIST = {IC.BOOK_AI: (328_669, 68_449), IC.BOOK_RN1X: (2_371, 0),
             IC.BOOK_PAPER: (1_009, 0)}


def _own(active=None, hist=None, hist_read=True):
    a, h = _own_rows(active, hist)
    return IC.own_books_census(a, hist_rows=h if hist_read else None,
                               hist_as_of=NOW.isoformat() if hist_read
                               else None)


def test_production_own_books_are_counted_legacy_paper_debt_never_refused():
    ob = _own(PROD_ACTIVE, PROD_HIST)
    assert ob["status"] == IC.OWN_MEASURED and ob["active_unknown"] == 0
    assert ob["active_rows"] == 301
    assert ob["historical_unknown"] == 68_449
    assert ob["books"][IC.BOOK_AI][IC.HISTORICAL]["unknown_identity"] == 68_449
    assert ob["books"][IC.BOOK_AI]["kind"] == "PAPER"
    # an ACTUAL book has no historical scope: an ACTUAL row is ACTIVE at any age
    assert ob["books"][IC.BOOK_LIVE][IC.HISTORICAL] is None
    assert "PAPER_ENGINE_FILLS_LEGACY" in ob["not_scanned"]
    c = IC.census([], now=NOW, own_books=ob)
    assert c["refusal"] is None and c["own_active"] == 0
    # our legacy debt keeps the census off "clean": counted and visible
    assert c["status"] == IC.ST_HISTORICAL
    text = IC.line(c)
    assert "PAPER_AI_FOLLOWER 68449" in text and "ACTIVE 0 unknown of 301" in text


@pytest.mark.parametrize("book", [IC.BOOK_LIVE, IC.BOOK_SMALL_LIVE,
                                  IC.BOOK_FUNDED, IC.BOOK_KALSHI,
                                  IC.BOOK_REGISTERED, IC.BOOK_AI,
                                  IC.BOOK_PAPER, IC.BOOK_RN1X])
def test_an_active_row_of_any_own_book_without_identity_is_refused(book):
    active = dict(PROD_ACTIVE)
    rows, _ = active.get(book, (0, 0))
    active[book] = (rows + 1, 1)
    c = IC.census([], now=NOW, own_books=_own(active, PROD_HIST))
    assert c["refusal"] == IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN
    assert c["status"] == IC.ST_REFUSED and c["own_active"] == 1
    assert c["own_active_by_source"] == {"roster_wallet": 0, "own_books": 1}
    assert c["own_books"]["active_unknown_by_book"] == {book: 1}


def test_own_books_unreadable_is_unmeasured_never_clean():
    ob = IC.own_books_census(None, error="RuntimeError")
    assert ob["status"] == IC.OWN_UNMEASURED and ob["active_unknown"] is None
    c = IC.census([], now=NOW, own_books=ob)
    assert c["status"] == IC.ST_OWN_UNMEASURED and c["refusal"] is None
    # a book missing from the read is unmeasured too, never zero
    a, _h = _own_rows()
    partial = IC.own_books_census([r for r in a if r["book"] != IC.BOOK_LIVE])
    assert partial["status"] == IC.OWN_UNMEASURED
    assert partial["books_unread"] == [IC.BOOK_LIVE]
    assert IC.census([], now=NOW, own_books=partial)["status"] == \
        IC.ST_OWN_UNMEASURED


def test_row_totals_moving_do_not_relog_unknown_counts_moving_do():
    a = IC.census([], now=NOW, own_books=_own(PROD_ACTIVE, PROD_HIST))
    busier = dict(PROD_ACTIVE, **{IC.BOOK_LIVE: (60, 0)})
    b = IC.census([], now=NOW, own_books=_own(busier, PROD_HIST))
    assert IC.signature(a) == IC.signature(b)
    debt = dict(PROD_HIST, **{IC.BOOK_AI: (328_669, 68_450)})
    d = IC.census([], now=NOW, own_books=_own(PROD_ACTIVE, debt))
    assert IC.signature(a) != IC.signature(d)


def test_the_historical_read_runs_at_most_once_per_interval():
    class P:
        def __init__(self):
            self.sql = []
            self.own = _own_rows(PROD_ACTIVE, PROD_HIST)

        async def fetch(self, sql, start):
            self.sql.append(sql)
            assert start == NOW - IC.ACTIVE_WINDOW
            return self.own[0 if sql == IC.OWN_ACTIVE_SQL else 1]

    p, t = P(), [1000.0]
    for dt_s in (0.0, 600.0, 2999.0):
        t[0] = 1000.0 + dt_s
        ob = asyncio.run(IC.read_own_books(p, now=NOW, clock=lambda: t[0]))
        assert ob["historical_unknown"] == 68_449
        assert ob["historical_as_of"] == NOW.isoformat()
    assert p.sql.count(IC.OWN_ACTIVE_SQL) == 3
    assert p.sql.count(IC.OWN_HISTORICAL_SQL) == 1
    t[0] = 1000.0 + IC.OWN_HISTORICAL_EVERY_S
    asyncio.run(IC.read_own_books(p, now=NOW, clock=lambda: t[0]))
    assert p.sql.count(IC.OWN_HISTORICAL_SQL) == 2


def test_the_persist_refuses_an_active_own_book_row_with_no_funder_on_the_roster(
        monkeypatch, caplog):
    """THE GAP THIS CLOSES: no own wallet configured (production's roster
    holds none of ours), no dead-lettered own state -- and still an ACTIVE
    ACTUAL row of ours with no identity is refused by name, on the census
    the heartbeat carries."""
    pool = _Pool(own_active=dict(PROD_ACTIVE, **{IC.BOOK_LIVE: (52, 1)}),
                 own_hist=PROD_HIST)
    _wire(monkeypatch, pool, funder="")
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        asyncio.run(eng._persist_positions(_book()))
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errs) == 1
    assert IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN in errs[0].getMessage()
    assert "ACTUAL_LIVE_ORDERS 1" in errs[0].getMessage()
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["refusal"] == IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN
    assert c["own_wallets_configured"] == 0
    assert IC.summary(c)["own_books"]["active_unknown"] == 1
    # the snapshot still persists exactly as before
    assert {r[1] for r in pool.sink["rows"]} == {"0xok", "0xrescued"}


def test_the_persist_reads_own_books_every_cycle_even_with_no_dead_letter(
        monkeypatch, caplog):
    pool = _Pool(own_hist=PROD_HIST)
    _wire(monkeypatch, pool)
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        asyncio.run(eng._persist_positions([_state(2, "tok-a", cid="0xok")]))
    assert IC.OWN_ACTIVE_SQL in pool.sql
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["dead_lettered"] == 0 and c["status"] == IC.ST_HISTORICAL
    warns = [r.getMessage() for r in caplog.records
             if r.levelno >= logging.WARNING]
    assert len(warns) == 1 and "PAPER_AI_FOLLOWER 68449" in warns[0]


def test_the_persist_names_own_books_unmeasured_when_the_read_fails(
        monkeypatch, caplog):
    pool = _Pool(own_fails=True)
    _wire(monkeypatch, pool)
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        asyncio.run(eng._persist_positions(_book()))
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["status"] == IC.ST_OWN_UNMEASURED
    assert c["own_books"]["error"] == "RuntimeError"
    assert any("own books UNMEASURED" in r.getMessage()
               for r in caplog.records if r.levelno >= logging.WARNING)
    assert {r[1] for r in pool.sink["rows"]} == {"0xok", "0xrescued"}


@pg
def test_the_own_books_reads_run_on_postgres_and_count_what_they_say():
    """Both statements on the real schema: an ACTUAL open order with no slug,
    no condition and an unknown token is ACTIVE unknown at any age; a PAPER
    follower row is ACTIVE inside the window and HISTORICAL outside; a token
    the catalog knows, or a condition, is identity; closed rows and
    settled orders are not counted."""
    asyncpg = pytest.importorskip("asyncpg")

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            now = await c.fetchval("SELECT now()")
            base = await IC.read_own_books(c, now=now, clock=lambda: 0.0)
            IC._OWN_HIST.update(at=None, rows=None, as_of=None, error=None)
            await c.execute(
                "INSERT INTO markets (condition_id) VALUES ('0xidlknown')")
            await c.execute(
                "INSERT INTO market_tokens (token_id, condition_id) VALUES "
                "('idl-known', '0xidlknown')")
            old = now - IC.ACTIVE_WINDOW - timedelta(days=4)
            recent = now - timedelta(days=1)
            for tok, cid, status, at in (
                    ("idl-a1", None, "open", recent),      # ACTIVE unknown
                    ("idl-a2", "", "open", recent),         # ACTIVE unknown
                    ("idl-known", None, "open", recent),    # catalog: known
                    ("idl-a3", "0xidlc", "open", recent),   # condition: known
                    ("idl-h1", None, "open", old),          # HISTORICAL unknown
                    ("idl-s1", None, "settled", recent)):   # not open
                await c.execute(
                    "INSERT INTO ai_trades (asset, condition_id, side, "
                    "his_price, clip_target, shares, status, placed_at) "
                    "VALUES ($1, $2, 'BUY', 0.5, 1, 2, $3, $4)",
                    tok, cid, status, at)
            for tok, slug, cid, status, shares, at in (
                    ("idl-l1", None, None, "filled", 3, old),    # unknown
                    ("idl-l2", "us-slug", None, "filled", 3, old),
                    ("idl-l3", None, None, "exiting", 3, recent),  # unknown
                    ("idl-l4", None, None, "settled", 3, recent),
                    ("idl-l5", None, None, "filled", 0, recent)):
                await c.execute(
                    "INSERT INTO live_orders (asset, condition_id, side, "
                    "his_price, limit_price, requested_usd, requested_shares,"
                    " status, filled_shares, placed_at, us_market_slug) "
                    "VALUES ($1, $2, 'BUY', 0.5, 0.5, 1, 2, $3, $4, $5, $6)",
                    tok, cid, status, shares, at, slug)
            got = await IC.read_own_books(c, now=now, clock=lambda: 0.0)
            return base, got
        finally:
            await tx.rollback()
            await c.close()

    base, got = asyncio.run(main())
    assert base["status"] == got["status"] == IC.OWN_MEASURED
    b0, b1 = base["books"], got["books"]

    def delta(book, scope, key):
        return b1[book][scope][key] - b0[book][scope][key]

    assert delta(IC.BOOK_AI, IC.ACTIVE, "rows") == 4
    assert delta(IC.BOOK_AI, IC.ACTIVE, "unknown_identity") == 2
    assert delta(IC.BOOK_AI, IC.HISTORICAL, "rows") == 1
    assert delta(IC.BOOK_AI, IC.HISTORICAL, "unknown_identity") == 1
    assert delta(IC.BOOK_LIVE, IC.ACTIVE, "rows") == 3
    assert delta(IC.BOOK_LIVE, IC.ACTIVE, "unknown_identity") == 2
    c = IC.census([], now=NOW, own_books=got)
    assert c["refusal"] == IC.R_OWN_ACTIVE_IDENTITY_UNKNOWN
    assert c["own_books"]["active_unknown_by_book"][IC.BOOK_LIVE] >= 2


def test_the_research_file_runs_the_exact_statements_the_census_runs():
    """research/rc6_identity_own_books.sql is the production readback of
    this census: it must be the code's own two statements, $1 the window."""
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parents[2] / "research"
           / "rc6_identity_own_books.sql").read_text()
    w = "(now() - interval '30 days')"
    assert IC.OWN_ACTIVE_SQL.strip().replace("$1", w) + ";" in sql
    assert IC.OWN_HISTORICAL_SQL.strip().replace("$1", w) + ";" in sql
    assert IC.ACTIVE_WINDOW.days == 30


# ── the census can never freeze the snapshot (the 2026-08-11 rule) ──────

class _GenericPool(_Pool):
    """A pool that answers EVERY fetch with the catalog-rescue shape -- the
    fake test_positions.py's incident test uses: the own-books reads get rows
    that are not (book, rows_n, unknown_n)."""

    async def fetch(self, sql, arg):
        self.sql.append(sql)
        return [{"token_id": "tok-b", "condition_id": "0xrescued"}]


def test_malformed_own_book_rows_are_unmeasured_and_the_snapshot_persists(
        monkeypatch):
    pool = _GenericPool()
    _wire(monkeypatch, pool)
    asyncio.run(eng._persist_positions(_book()))
    assert {r[1] for r in pool.sink["rows"]} == {"0xok", "0xrescued"}
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["status"] == IC.ST_OWN_UNMEASURED
    assert c["own_books"]["error"] == "MALFORMED_ROWS"
    assert c["own_books"]["malformed_rows"] >= 1
    assert c["own_books"]["historical_status"] == IC.OWN_UNMEASURED


def test_a_census_that_raises_never_stops_the_persist(monkeypatch, caplog):
    pool = _Pool()
    _wire(monkeypatch, pool)

    def boom(*a, **k):
        raise ZeroDivisionError("census bug")

    monkeypatch.setattr(IC, "census", boom)
    with caplog.at_level(logging.WARNING, logger=eng.__name__):
        asyncio.run(eng._persist_positions(_book()))
    assert {r[1] for r in pool.sink["rows"]} == {"0xok", "0xrescued"}
    c = eng.LAST_IDENTITY_CENSUS["census"]
    assert c["status"] == IC.ST_CENSUS_FAILED and c["error"] == "ZeroDivisionError"
    assert c["dead_lettered"] == 2 and c["refusal"] is None
    assert IC.summary(c)["status"] == IC.ST_CENSUS_FAILED
    assert any(IC.ST_CENSUS_FAILED in r.getMessage() for r in caplog.records
               if r.levelno >= logging.ERROR)
