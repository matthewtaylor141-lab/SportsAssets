"""The cell gate must judge the RESOLVED market identity, not the raw
payload: fresh Path-A detections reach maybe_execute before enrichment
(no slug in the payload), and feeding the fail-closed parser an empty
string silently dropped every fresh copy for ~3h on 2026-08-06."""

import asyncio
from datetime import date

import pytest

from sportsassets import live_executor


class _FakePool:
    """Progress marker: reaching the one-copy-per-asset check proves the
    cell gate passed; answering 'taken' stops maybe_execute right there."""

    def __init__(self):
        self.fetchval_calls = 0

    async def fetchval(self, *a, **k):
        self.fetchval_calls += 1
        return 1

    async def fetchrow(self, *a, **k):
        return None

    async def execute(self, *a, **k):
        return None


def _payload(**over):
    p = {"id": 1, "whale_id": 2, "whale_username": "HomeRunHazard",
         "asset": "123", "condition_id": "0xc", "side": "BUY",
         "outcome": "Dallas Wings", "outcome_index": 0, "size": 10.0,
         "price": 0.55, "notional": 5.5,
         "market_title": None, "market_slug": None, "event_slug": None}
    p.update(over)
    return p


def _wire(monkeypatch, pool, ctx):
    async def fake_get_pool():
        return pool

    async def fake_paused(_pool):
        return False

    async def fake_ctx(_pool, _payload):
        return dict(ctx)

    monkeypatch.setattr(live_executor, "get_pool", fake_get_pool)
    monkeypatch.setattr(live_executor, "_is_paused", fake_paused)
    monkeypatch.setattr(live_executor, "_market_context", fake_ctx)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-us")


def test_unenriched_fresh_payload_resolves_slug_before_cell_gate(monkeypatch):
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": f"wnba-dal-chi-{date.today().isoformat()}",
        "event_slug": None, "market_title": "Wings v Sky",
        "event_title": None, "outcome": "Dallas Wings",
    })
    asyncio.run(live_executor.maybe_execute(_payload(), None))
    assert pool.fetchval_calls > 0, (
        "an allowed cell must pass the gate once the slug is resolved "
        "from the metadata tables")


def test_resolved_slug_outside_cells_is_dropped(monkeypatch):
    # HomeRunHazard has no basketball cell — resolution must not weaken
    # the policy, only inform it.
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": f"nba-bos-lal-{date.today().isoformat()}",
        "event_slug": None, "market_title": "Celtics v Lakers",
        "event_title": None, "outcome": "Boston Celtics",
    })
    asyncio.run(live_executor.maybe_execute(_payload(), None))
    assert pool.fetchval_calls == 0


def test_unresolvable_market_still_fails_closed(monkeypatch):
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": None, "event_slug": None, "market_title": None,
        "event_title": None, "outcome": None,
    })
    asyncio.run(live_executor.maybe_execute(_payload(), None))
    assert pool.fetchval_calls == 0
