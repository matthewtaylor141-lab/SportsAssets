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
    # Fixture whale must PASS the cell gate so the plumbing under test
    # (slug resolution, claims, venue split) is reachable — the 0x2c33
    # wallet is unrestricted. (Was HomeRunHazard until the owner paused
    # him 2026-08-11; a paused whale refuses before any of this runs.)
    p = {"id": 1, "whale_id": 2,
         "whale_username": "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563"
                           "-1759935795465",
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
    # A PMUS-side asset: this test is about slug resolution reaching the
    # cell gate, not the venue split — a kalshi-first asset would defer
    # before ever touching the pool.
    asyncio.run(live_executor.maybe_execute(_payload(asset=_asset(False)),
                                            None))
    assert pool.fetchval_calls > 0, (
        "an allowed cell must pass the gate once the slug is resolved "
        "from the metadata tables")


def test_resolved_slug_outside_cells_is_dropped(monkeypatch):
    # The paused HomeRunHazard copies nothing — a RESOLVED slug must
    # not weaken that policy, only inform it. (The old fixture only
    # "dropped" via the Kalshi-first deference, which is now off —
    # PM-only routing, owner 2026-08-17 night.)
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": f"nba-bos-lal-{date.today().isoformat()}",
        "event_slug": None, "market_title": "Celtics v Lakers",
        "event_title": None, "outcome": "Boston Celtics",
    })
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="HomeRunHazard"), None))
    assert pool.fetchval_calls == 0


def test_unresolvable_market_still_fails_closed(monkeypatch):
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": None, "event_slug": None, "market_title": None,
        "event_title": None, "outcome": None,
    })
    # A CELL-GATED whale: fail-closed means no slug -> no sport -> refused
    # before the pool. (Unrestricted whales legitimately pass this gate
    # and get rejected later at mapping.)
    asyncio.run(live_executor.maybe_execute(
        _payload(whale_username="kch123"), None))
    assert pool.fetchval_calls == 0


# ── Venue split (owner directive 2026-08-07): Kalshi first claim ─────


# The venue-split mechanics below need assets on BOTH sides of the
# split; the production default is now 100% Kalshi-first (owner
# 2026-08-09), so pin the historical 50/50 for these tests.
import os as _os
_os.environ["KALSHI_FIRST_PCT"] = "50"


def _asset(want_kalshi_first: bool) -> str:
    from sportsassets.copy_sports import kalshi_first
    return next(str(i) for i in range(200)
                if kalshi_first(str(i)) is want_kalshi_first)


def _wnba_ctx():
    from datetime import date
    return {"market_slug": f"wnba-dal-chi-{date.today().isoformat()}",
            "event_slug": None, "market_title": "Wings v Sky",
            "event_title": None, "outcome": "Dallas Wings"}


def test_kalshi_first_fresh_copy_defers_to_the_engine(monkeypatch):
    """Half the fresh flow in Kalshi-listed sports belongs to the
    Kalshi leg — the split MACHINERY, testable behind its re-arm
    switch (PMUS_ALL_COPIES=0). The DEFAULT since the firm price rule
    (owner 2026-08-17 late night) is no deference: PMUS executes every
    copy immediately and the engine's sweep takes only positions whose
    Kalshi price provably beats the visible PMUS ask."""
    monkeypatch.setenv("PMUS_ALL_COPIES", "0")
    pool = _FakePool()
    _wire(monkeypatch, pool, _wnba_ctx())
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(True)), None))
    assert pool.fetchval_calls == 0


def test_tennis_never_defers_to_kalshi(monkeypatch):
    """Owner 2026-08-17: tennis is never traded on Kalshi — a tennis
    copy executes PMUS-side immediately, even on a kalshi-first asset
    (tennis is out of KALSHI_FIRST_SPORTS)."""
    monkeypatch.delenv("PMUS_ALL_COPIES", raising=False)
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": f"atp-sinner-alcaraz-{date.today().isoformat()}",
        "event_slug": None, "market_title": "Sinner v Alcaraz",
        "event_title": None, "outcome": "Jannik Sinner",
    })
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(True), outcome="Jannik Sinner"), None))
    assert pool.fetchval_calls > 0,         "tennis must reach the PMUS order plumbing, never defer"


def test_pm_only_switch_routes_everything_to_pmus(monkeypatch):
    """PMUS_ALL_COPIES=1 remains the whole-book switch: every copy
    (any sport) executes here."""
    monkeypatch.setenv("PMUS_ALL_COPIES", "1")
    pool = _FakePool()
    _wire(monkeypatch, pool, _wnba_ctx())
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(True)), None))
    assert pool.fetchval_calls > 0


def test_sweep_recovery_rows_never_defer(monkeypatch):
    """The hourly sweep IS the reclaim leg: anything Kalshi could not
    price must place on PMUS, never bounce back to Kalshi."""
    pool = _FakePool()
    _wire(monkeypatch, pool, _wnba_ctx())
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(True), sweep_recovery=True), None))
    assert pool.fetchval_calls > 0


def test_pm_first_fresh_copy_proceeds(monkeypatch):
    pool = _FakePool()
    _wire(monkeypatch, pool, _wnba_ctx())
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(False)), None))
    assert pool.fetchval_calls > 0


def test_soccer_stays_pmus_first_even_when_hash_says_kalshi(monkeypatch):
    """Kalshi's soccer coverage is thin; swisstony's soccer flow keeps
    the fast fee-free venue regardless of the hash split. (Routing
    behavior — the 2026-08-11 soccer HALT is pinned out; it has its own
    tests in test_copy_sports.)"""
    from datetime import date

    from sportsassets import copy_sports as _cs
    monkeypatch.setattr(_cs, "HALTED_SPORTS", frozenset())
    pool = _FakePool()
    _wire(monkeypatch, pool, {
        "market_slug": f"epl-ars-che-{date.today().isoformat()}",
        "event_slug": None, "market_title": "Arsenal v Chelsea",
        "event_title": None, "outcome": "Arsenal",
    })
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(True), whale_username="swisstony"), None))
    assert pool.fetchval_calls > 0


def test_kalshi_claimed_position_is_taken(monkeypatch):
    """A position the engine copied on Kalshi (kalshi_claims) must never
    be bought again on PMUS — the reverse direction of the one-copy
    rule, previously unguarded."""
    class _ClaimPool(_FakePool):
        def __init__(self):
            super().__init__()
            self.queries = []

        async def fetchval(self, q, *a, **k):
            self.fetchval_calls += 1
            self.queries.append(q)
            if "live_orders" in q:
                return None          # no PMUS copy yet...
            if "kalshi_claims" in q:
                return 1             # ...but Kalshi holds it
            raise AssertionError(f"unexpected query past the claim: {q}")

    pool = _ClaimPool()
    _wire(monkeypatch, pool, _wnba_ctx())
    asyncio.run(live_executor.maybe_execute(
        _payload(asset=_asset(False)), None))
    assert any("kalshi_claims" in q for q in pool.queries)
    assert pool.fetchval_calls == 2   # both taken-checks, nothing beyond
