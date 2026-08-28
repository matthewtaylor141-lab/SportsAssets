"""History-backfill carrier — round-24 kills, made permanent.

The round-23 silent-success class was alive in this third carrier,
worse: an HTTP-200 error envelope scanned 0 fills and then durably
marked the whale's ENTIRE deep history imported (history_backfilled=
TRUE, ok heartbeat, nothing ever resets the flag) — while one junk
element in a page escaped the narrow per-whale except tuple, aborted
the whole pass, and starved every whale after the poisoned one on a
60-second crash loop. These drive the REAL backfill functions with
only the venue HTTP / config / DB seams faked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import sportsassets.ingestion.history as hist

RAW = {
    "transactionHash": "0x" + "cd" * 32,
    "asset": "555001",
    "side": "buy",
    "size": "50",
    "price": "0.40",
    "timestamp": 1_760_000_000,
}
RAW2 = dict(RAW, transactionHash="0x" + "ef" * 32, asset="555002")
STUB_NO_SIDE = {
    "transactionHash": "0x" + "aa" * 32,
    "asset": "555003",
    "size": "50",
    "price": "0.40",
    "timestamp": 1_760_000_000,
}

W_A = {"id": 1, "address": "0xaaa", "username": "wa"}
W_B = {"id": 2, "address": "0xbbb", "username": "wb"}


class _Resp:
    def __init__(self, body):
        self._b = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


class _Conn:
    def __init__(self, sink):
        self._sink = sink

    async def executemany(self, sql, rows):
        self._sink.extend(rows)


class _Acquire:
    def __init__(self, conn):
        self._c = conn

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, whales):
        self.whales = whales
        self.inserted: list[tuple] = []
        self.executes: list[tuple] = []

    async def fetch(self, sql, *a):
        if "FROM whales" in sql:
            return self.whales
        return []                                   # sport map

    async def execute(self, sql, *a):
        self.executes.append((sql, a))

    def acquire(self):
        return _Acquire(_Conn(self.inserted))


def _wire(monkeypatch, whales, pages):
    """pages: {address: [body, body, ...]} — served in order, then []."""
    pool = _Pool(whales)
    beats: list[tuple] = []

    async def fake_pool():
        return pool

    async def fake_get(http, path, params=None):
        q = pages.get(params["user"], [])
        return _Resp(q.pop(0) if q else [])

    async def fake_beat(name, status, detail=None):
        beats.append((name, status, detail))

    monkeypatch.setattr(hist, "get_pool", fake_pool)
    monkeypatch.setattr(hist, "polite_get", fake_get)
    monkeypatch.setattr(hist, "heartbeat", fake_beat)
    monkeypatch.setattr(hist, "settings", lambda: SimpleNamespace(
        history_max_trades=10_000,
        history_start_date="2026-08-20",
        data_api_base="http://feed.test"))
    return pool, beats


def _backfilled_ids(pool):
    return [a[0] for sql, a in pool.executes
            if "history_backfilled" in sql]


def test_error_envelope_leaves_the_whale_pending(monkeypatch):
    """r24 major #1: an HTTP-200 error dict on /activity used to scan
    0 fills and still durably mark the whale imported. It must raise
    like its siblings (poller r23, reconciler r19) so the per-whale
    handler leaves the whale PENDING and the next cycle retries."""
    pool, beats = _wire(monkeypatch, [W_A],
                        {"0xaaa": [{"error": "upstream", "code": 503}]})
    total = asyncio.run(hist._backfill_pending_inner())
    assert total == 0
    assert _backfilled_ids(pool) == [], \
        "a venue burp must never become a durable success marker"
    assert any(s == "error" for _, s, _ in beats), \
        "the outage is visible, not an ok heartbeat"


def test_one_junk_element_costs_one_row_not_the_pass(monkeypatch):
    """r24 major #2: one JSON null aborted the ENTIRE pass and
    starved every whale after the poisoned one, forever, on a 60s
    crash loop. One junk row costs one row; the pass completes."""
    pool, beats = _wire(monkeypatch, [W_A, W_B],
                        {"0xaaa": [[None, RAW]], "0xbbb": [[RAW2]]})
    total = asyncio.run(hist._backfill_pending_inner())
    assert total == 2, "both whales' healthy rows imported"
    assert sorted(_backfilled_ids(pool)) == [1, 2]
    assert ("backfill", "ok", {"scanned": 2}) in beats


def test_all_junk_page_fails_the_whale_not_the_pass(monkeypatch):
    pool, beats = _wire(monkeypatch, [W_A, W_B],
                        {"0xaaa": [[None, "junk", {}]],
                         "0xbbb": [[RAW2]]})
    total = asyncio.run(hist._backfill_pending_inner())
    assert total == 1, "the healthy whale still backfills"
    assert _backfilled_ids(pool) == [2], \
        "the poisoned whale stays pending; the healthy one completes"
    assert any(s == "error" and (d or {}).get("whale") == "0xaaa"
               for _, s, d in beats)


def test_side_less_stub_never_reaches_the_insert(monkeypatch):
    """The old gate checked only tx/size — a side-less stub killed
    the whole executemany batch on the side CHECK constraint. The
    shared validity gate now guards this INSERT like both sibling
    carriers' (rounds 12-14)."""
    pool, _ = _wire(monkeypatch, [W_A],
                    {"0xaaa": [[STUB_NO_SIDE, RAW]]})
    asyncio.run(hist._backfill_pending_inner())
    assert len(pool.inserted) == 1
    assert pool.inserted[0][4] == "BUY", \
        "only gate-passing rows reach the batch insert"


def test_db_failure_leaves_the_whale_pending_not_the_pass_dead(
        monkeypatch):
    """The per-whale except is broad now: any failure (DB constraint,
    parse surprise) costs that whale one cycle, never the roster."""
    pool, beats = _wire(monkeypatch, [W_A, W_B],
                        {"0xaaa": [[RAW]], "0xbbb": [[RAW2]]})

    class _BoomPool(_Pool):
        def acquire(self):
            raise RuntimeError("constraint drift")

    boom = _BoomPool([W_A, W_B])
    calls = {"n": 0}
    orig = hist.backfill_whale_history

    async def flaky(http, whale):
        calls["n"] += 1
        if whale["address"] == "0xaaa":
            raise RuntimeError("constraint drift")
        return await orig(http, whale)

    monkeypatch.setattr(hist, "backfill_whale_history", flaky)
    total = asyncio.run(hist._backfill_pending_inner())
    assert calls["n"] == 2, "the pass reached the second whale"
    assert total == 1
    assert _backfilled_ids(pool) == [2]
    assert any(s == "error" for _, s, _ in beats)
