"""One POSITION per game (owner audit order 2026-08-11 evening): a
game is one bet. Ladder rungs (O1.5/O2.5/O3.5), opposite-side
moneylines (the Halys+Kwon guaranteed-loss shape), and any other
second market on a held game are refused account-wide; only the first
market a whale entered copies."""

import asyncio
from datetime import date

from sportsassets import live_executor, pmus
from sportsassets.live_executor import _ladder_kind, _us_game_key

TODAY = date.today().isoformat()


def test_ladder_kind_flags_only_laddered_families():
    assert _ladder_kind(f"tsc-epl-ars-che-{TODAY}-o2pt5") == "tsc"
    assert _ladder_kind(f"asc-epl-ars-che-{TODAY}-ars-1pt5") == "asc"
    assert _ladder_kind(f"atc-epl-ars-che-{TODAY}-ars") is None
    assert _ladder_kind(f"aec-epl-ars-che-{TODAY}") is None
    assert _ladder_kind("") is None


def test_us_game_key_strips_kind_and_line():
    a = _us_game_key(f"tsc-epl-ars-che-{TODAY}-o2pt5")
    b = _us_game_key(f"tsc-epl-ars-che-{TODAY}-o3pt5")
    c = _us_game_key(f"tsc-epl-liv-mci-{TODAY}-o2pt5")
    assert a == b == f"epl-ars-che-{TODAY}"
    assert c != a
    assert _us_game_key("tsc-no-date-here") is None, "undated fails open"


class _LadderPool:
    """The mapping-path pool, plus `fetch` answering the ladder query
    with a configurable set of already-held us slugs."""

    def __init__(self, held):
        self.held = held
        self.updates = []
        self.ladder_queries = 0

    async def fetchval(self, sql, *a):
        if "INSERT INTO live_orders" in sql:
            return 101
        if "sum(pnl)" in sql:
            return getattr(self, "lost_24h", 0.0)
        if "bool_or" in sql:
            return getattr(self, "dog_owned", None)
        if "SELECT 1 FROM live_orders" in sql and "us_market_slug = $2" in sql:
            return 1 if getattr(self, "prior_market", None) == a[1] else None
        return None

    async def fetchrow(self, sql, *a):
        return {"day": 0.0, "total": 0.0}

    async def fetch(self, sql, *a):
        assert "us_market_slug IS NOT NULL" in sql
        assert "<> 'underdog'" in sql, \
            "the $2 sleeve must not consume a game's one copy slot"
        self.ladder_queries += 1
        return [{"us_market_slug": s} for s in self.held]

    async def execute(self, sql, *a):
        self.updates.append((" ".join(sql.split()), a))


def _payload(**over):
    p = {"id": 1, "whale_id": 2, "whale_username": "RN1", "asset": "123",
         "condition_id": "0xc", "side": "BUY", "outcome": "Over 3.5",
         "size": 10.0, "price": 0.55, "notional": 5.5,
         "market_title": None, "market_slug": None, "event_slug": None}
    p.update(over)
    return p


def _wire(monkeypatch, pool, mapped_slug):
    ctx = {"market_slug": f"epl-ars-che-{TODAY}-o3pt5",
           "event_slug": None, "market_title": "Arsenal vs Chelsea O/U",
           "event_title": None, "outcome": "Over 3.5"}

    async def fake_get_pool():
        return pool

    async def fake_paused(_pool):
        return False

    async def fake_ctx(_pool, _payload):
        return dict(ctx)

    from sportsassets import copy_sports as _cs
    monkeypatch.setattr(_cs, "HALTED_SPORTS", frozenset(),
                        raising=True)   # these tests exercise mapping/
    # ladder logic, not the soccer halt (which has its own tests)
    monkeypatch.setattr(live_executor, "get_pool", fake_get_pool)
    monkeypatch.setattr(live_executor, "_is_paused", fake_paused)
    monkeypatch.setattr(live_executor, "_market_context", fake_ctx)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-us")
    monkeypatch.setattr(pmus, "resolve_market_exact", lambda *a, **k: None)
    monkeypatch.setattr(
        pmus, "resolve_market",
        lambda *a, **k: {"market_slug": mapped_slug, "title": "O/U",
                         "outcome": "Over 3.5", "matched_by": "fuzzy",
                         "score": 1.0})
    monkeypatch.setattr(pmus, "account_holds", lambda slug: False)
    submitted = []

    def fake_submit(slug, limit, shares):
        submitted.append(slug)
        return {"ok": True, "filled_shares": float(shares),
                "fill_price": limit, "order_id": "o1", "raw": {}}

    monkeypatch.setattr(pmus, "submit_fok", fake_submit)
    return submitted


def test_second_rung_of_a_ladder_is_refused(monkeypatch):
    held = [f"tsc-epl-ars-che-{TODAY}-o2pt5"]     # O2.5 already filled
    pool = _LadderPool(held)
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert pool.ladder_queries == 1
    assert not submitted, "the O3.5 rung must not stack on the O2.5"
    assert any("one position per game" in str(a) for _, a in pool.updates)


def test_other_games_lines_do_not_block(monkeypatch):
    held = [f"tsc-epl-liv-mci-{TODAY}-o2pt5"]     # different game
    pool = _LadderPool(held)
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted == [f"tsc-epl-ars-che-{TODAY}-o3pt5"], \
        "a first line on a fresh game copies normally"


def test_moneyline_on_a_held_game_is_refused_too(monkeypatch):
    """The Halys+Kwon shape: a held total (or the other side's
    moneyline) on this game refuses the new moneyline — one position
    per game covers every market type."""
    pool = _LadderPool([f"tsc-epl-ars-che-{TODAY}-o2pt5"])
    submitted = _wire(monkeypatch, pool,
                      f"atc-epl-ars-che-{TODAY}-ars")
    asyncio.run(live_executor.maybe_execute(_payload(outcome="Arsenal"), 5.0))
    assert pool.ladder_queries == 1
    assert not submitted
    assert any("one position per game" in str(a) for _, a in pool.updates)


def test_moneyline_on_a_fresh_game_copies(monkeypatch):
    pool = _LadderPool([f"tsc-epl-liv-mci-{TODAY}-o2pt5"])
    submitted = _wire(monkeypatch, pool,
                      f"atc-epl-ars-che-{TODAY}-ars")
    asyncio.run(live_executor.maybe_execute(_payload(outcome="Arsenal"), 5.0))
    assert submitted


def test_same_market_never_adds_even_when_venue_is_blind(monkeypatch):
    """DB-side never-add (2026-08-11 afternoon, the $318 position): our
    own order ledger refuses a second buy on the exact market, so a
    stale venue snapshot can never let positions stack again."""
    pool = _LadderPool([])
    pool.prior_market = f"tsc-epl-ars-che-{TODAY}-o3pt5"
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert not submitted
    assert any("never-add" in str(a) for _, a in pool.updates)


def test_venue_holding_explained_by_sleeve_does_not_block_copy(monkeypatch):
    """Owner 2026-08-12 (sleeve v2 restart, 'completely independent'):
    the $2 sleeve buys EVERY MLB/tennis dog at T-5, so the venue holds
    nearly every game market. A holding whose only Postgres explanation
    is the sleeve must not read as a copy stack."""
    pool = _LadderPool([])
    pool.dog_owned = True
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(pmus, "account_holds", lambda slug: True)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted, "sleeve-owned holding must not veto the copy"


def test_unexplained_venue_holding_still_fails_closed(monkeypatch):
    """The carve-out is for sleeve-owned holdings ONLY: a venue holding
    with no Postgres row behind it (or a copy row) still refuses."""
    pool = _LadderPool([])
    pool.dog_owned = None
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    monkeypatch.setattr(pmus, "account_holds", lambda slug: True)
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert not submitted
    assert any("no-stack" in str(a) for _, a in pool.updates)


def test_rolling_loss_breaker_pauses_copies(monkeypatch):
    """Owner 2026-08-12 ($1500 threshold at the then-current clips;
    default scaled to $2250 with the +50% clip order 2026-08-17):
    realized copy losses at the floor over any rolling 24h pause the
    sleeve before any order or audit row is written; a smaller
    drawdown trades normally."""
    pool = _LadderPool([])
    pool.lost_24h = -2250.0
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert not submitted
    assert not pool.updates, "breaker fires before any row exists"

    pool2 = _LadderPool([])
    pool2.lost_24h = -2249.0
    submitted2 = _wire(monkeypatch, pool2,
                       f"tsc-epl-ars-che-{TODAY}-o3pt5")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted2, "under the threshold the sleeve trades normally"


def test_stale_positions_snapshot_fails_closed(monkeypatch):
    """account_holds past the staleness bound reports HELD: an outage
    starves the sleeve instead of blinding the no-stack guard."""
    import time as _t

    from sportsassets import pmus as _pmus

    monkeypatch.setattr(_pmus, "_get_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setitem(_pmus._pos_cache, "ts", _t.time() - 3600)
    monkeypatch.setitem(_pmus._pos_cache, "slugs", frozenset())
    assert _pmus.account_holds("tsc-anything") is True
    monkeypatch.setitem(_pmus._pos_cache, "ts", _t.time() - 60)
    assert _pmus.account_holds("tsc-anything") is False,         "a fresh-enough snapshot still answers normally"
