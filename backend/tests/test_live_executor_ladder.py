"""One line per ladder (owner approval 2026-08-11): a whale laddering
one game across nested totals/spread lines (O1.5/O2.5/O3.5) is ONE
correlated bet wearing several tickets — the first line copies, later
rungs on the same game and family are refused account-wide."""

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
        return None

    async def fetchrow(self, sql, *a):
        return {"day": 0.0, "total": 0.0}

    async def fetch(self, sql, *a):
        assert "us_market_slug LIKE" in sql
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
    assert any("same-game ladder" in str(a) for _, a in pool.updates)


def test_other_games_lines_do_not_block(monkeypatch):
    held = [f"tsc-epl-liv-mci-{TODAY}-o2pt5"]     # different game
    pool = _LadderPool(held)
    submitted = _wire(monkeypatch, pool,
                      f"tsc-epl-ars-che-{TODAY}-o3pt5")
    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert submitted == [f"tsc-epl-ars-che-{TODAY}-o3pt5"], \
        "a first line on a fresh game copies normally"


def test_moneylines_skip_the_ladder_query_entirely(monkeypatch):
    pool = _LadderPool([f"tsc-epl-ars-che-{TODAY}-o2pt5"])
    submitted = _wire(monkeypatch, pool,
                      f"atc-epl-ars-che-{TODAY}-ars")
    asyncio.run(live_executor.maybe_execute(_payload(outcome="Arsenal"), 5.0))
    assert pool.ladder_queries == 0, "moneylines are not a ladder family"
    assert submitted
