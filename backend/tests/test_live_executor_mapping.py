"""Auto-copy mapping order (2026-08-10, unmapped-funnel work): the
deterministic US slug grammar runs FIRST — the manual desk and underdog
sleeve already map this way — and the fuzzy search pipeline only takes
what exact lookup refuses. Also pins the probe/execution split's new
entry point (execute_copy stamps its own reaction)."""

import asyncio
from datetime import date, datetime, timedelta, timezone

from sportsassets import live_executor, pmus


class _MapPool:
    """Answers every gate on the road to the mapping block: not taken,
    caps clear, INSERT returns a row id; UPDATEs are captured."""

    def __init__(self):
        self.updates = []

    async def fetchval(self, sql, *a):
        if "INSERT INTO live_orders" in sql:
            return 101
        return None

    async def fetchrow(self, sql, *a):
        return {"day": 0.0, "total": 0.0}

    async def execute(self, sql, *a):
        self.updates.append((" ".join(sql.split()), a))


def _payload(**over):
    p = {"id": 1, "whale_id": 2, "whale_username": "RN1", "asset": "123",
         "condition_id": "0xc", "side": "BUY", "outcome": "Arsenal",
         "size": 10.0, "price": 0.55, "notional": 5.5,
         "market_title": None, "market_slug": None, "event_slug": None}
    p.update(over)
    return p


def _wire(monkeypatch, pool):
    # Soccer slug: outside KALSHI_FIRST_SPORTS, so the venue split never
    # defers and the test exercises mapping, not routing.
    ctx = {"market_slug": f"epl-ars-che-{date.today().isoformat()}",
           "event_slug": None, "market_title": "Arsenal vs Chelsea",
           "event_title": None, "outcome": "Arsenal"}

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


def test_exact_grammar_maps_first_and_fuzzy_never_runs(monkeypatch):
    pool = _MapPool()
    _wire(monkeypatch, pool)
    calls = {"exact": 0, "fuzzy": 0}

    def fake_exact(slugs, outcome):
        calls["exact"] += 1
        assert slugs and slugs[0].startswith("atc-epl-ars-che-"), \
            "side-coded atc candidate must lead"
        return {"market_slug": slugs[0], "title": "Arsenal",
                "outcome": outcome, "matched_by": "desk_exact",
                "score": 1.0}

    def fake_fuzzy(*a, **k):
        calls["fuzzy"] += 1
        return None

    submitted = []

    def fake_submit(slug, limit, shares):
        submitted.append((slug, limit, shares))
        return {"ok": True, "filled_shares": float(shares),
                "fill_price": limit, "order_id": "o1", "raw": {}}

    monkeypatch.setattr(pmus, "resolve_market_exact", fake_exact)
    monkeypatch.setattr(pmus, "resolve_market", fake_fuzzy)
    monkeypatch.setattr(pmus, "account_holds", lambda slug: False)
    monkeypatch.setattr(pmus, "submit_fok", fake_submit)

    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert calls == {"exact": 1, "fuzzy": 0}
    assert submitted and submitted[0][0].startswith("atc-epl-ars-che-")


def test_exact_miss_still_falls_through_to_fuzzy(monkeypatch):
    pool = _MapPool()
    _wire(monkeypatch, pool)
    calls = {"exact": 0, "fuzzy": 0}

    def fake_exact(slugs, outcome):
        calls["exact"] += 1
        return None

    def fake_fuzzy(*a, **k):
        calls["fuzzy"] += 1
        return None

    monkeypatch.setattr(pmus, "resolve_market_exact", fake_exact)
    monkeypatch.setattr(pmus, "resolve_market", fake_fuzzy)

    asyncio.run(live_executor.maybe_execute(_payload(), 5.0))
    assert calls == {"exact": 1, "fuzzy": 1}
    assert any("status='rejected'" in sql for sql, _ in pool.updates), \
        "a double miss must still record the unmapped rejection"


def test_execute_copy_stamps_its_own_reaction(monkeypatch):
    seen = []

    async def fake_me(payload, reaction):
        seen.append(reaction)

    monkeypatch.setattr(live_executor, "maybe_execute", fake_me)
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)) \
        .isoformat().replace("+00:00", "Z")
    asyncio.run(live_executor.execute_copy({"id": 1, "ts": ts}))
    assert seen and 29.0 <= seen[0] <= 60.0
    # No fill timestamp: reaction unknown, execution still proceeds —
    # and deliberately no 120s ceiling (the sweep-forfeit fix).
    asyncio.run(live_executor.execute_copy({"id": 2}))
    assert seen[1] is None
