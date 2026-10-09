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
    yield
    IC._LAST["signature"] = None


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
    def __init__(self, own_ids=()):
        self.sink = {}
        self.own_ids = list(own_ids)
        self.sql: list = []

    async def fetch(self, sql, arg):
        self.sql.append(sql)
        if "FROM whales" in sql:
            return [{"id": i} for i in self.own_ids]
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
