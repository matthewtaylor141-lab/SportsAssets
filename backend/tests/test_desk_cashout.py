"""Desk cash-out (owner directive 2026-08-22): platform-side sells fail
CLOSED — never more than held, never without a live bid, protective
limit floored at $0.01 — and record proceeds on the manual sleeve."""

import asyncio

import pytest

from sportsassets import live_executor, pmus
from sportsassets.live_executor import sell_limit_price


# ── Pure limit math ──────────────────────────────────────────────────


def test_sell_limit_is_bid_minus_2c():
    assert sell_limit_price(0.55) == 0.53


def test_sell_limit_floors_at_one_cent():
    assert sell_limit_price(0.02) == 0.01
    assert sell_limit_price(0.01) == 0.01


def test_sell_limit_never_below_min_price():
    assert sell_limit_price(0.55, min_price=0.60) == 0.60
    assert sell_limit_price(0.55, min_price=0.10) == 0.53
    assert sell_limit_price(0.995, min_price=None) == 0.97


# ── Sell path with mocked venue ──────────────────────────────────────


class _Pool:
    def __init__(self):
        self.inserts = []

    async def fetchval(self, q, *a, **k):
        if "INSERT INTO live_orders" in q:
            self.inserts.append(a)
            return 77
        return None


def _wire(monkeypatch, pool, held=(30, 0.40), bid=0.55,
          venue="polymarket-us", fok=None, long_leg=True):
    async def fake_pool():
        return pool

    async def fake_held(_slug):
        return held

    # The desk READS which leg it holds (2026-08-31): _pm_held returns
    # a magnitude, so it cannot say. Stubbed here because the real one
    # calls the venue.
    async def fake_leg(_slug):
        return long_leg

    monkeypatch.setattr(live_executor, "get_pool", fake_pool)
    monkeypatch.setattr(live_executor, "active_venue", lambda: venue)
    monkeypatch.setattr(live_executor, "_pm_held", fake_held)
    monkeypatch.setattr(live_executor, "_pm_long_leg", fake_leg)
    monkeypatch.setattr(pmus, "slug_bid",
                        lambda _slug, long_leg=None: bid)
    monkeypatch.setattr(
        pmus, "submit_fok",
        fok or (lambda slug, limit, qty, sell, tif: {
            "ok": True, "order_id": "o1", "status": "filled",
            "fill_price": limit, "filled_shares": float(qty),
            "raw": {}}))


def test_sell_refuses_when_venue_not_armed(monkeypatch):
    _wire(monkeypatch, _Pool(), venue=None)
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert not r["ok"] and "venue" in r["error"]


def test_sell_refuses_nothing_held(monkeypatch):
    _wire(monkeypatch, _Pool(), held=(0, None))
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert not r["ok"] and "nothing held" in r["error"]


def test_sell_refuses_qty_over_held(monkeypatch):
    pool = _Pool()
    _wire(monkeypatch, pool, held=(30, 0.40))
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x", qty=31))
    assert not r["ok"] and "exceeds held" in r["error"]
    assert pool.inserts == []          # refused before any venue call


def test_sell_refuses_with_no_bid(monkeypatch):
    _wire(monkeypatch, _Pool(), bid=None)
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert not r["ok"] and "no live bid" in r["error"]


def test_sell_all_held_records_proceeds(monkeypatch):
    pool = _Pool()
    calls = {}

    def fok(slug, limit, qty, sell, tif):
        calls.update(slug=slug, limit=limit, qty=qty, sell=sell, tif=tif)
        return {"ok": True, "order_id": "o9", "status": "filled",
                "fill_price": 0.54, "filled_shares": float(qty), "raw": {}}

    _wire(monkeypatch, pool, held=(30, 0.40), bid=0.55, fok=fok)
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert r["ok"] is True
    assert calls["sell"] is True and calls["qty"] == 30
    assert calls["limit"] == 0.53                      # bid - 2c
    assert r["filled_shares"] == 30.0
    assert r["avg_price"] == 0.54
    assert r["proceeds_usd"] == round(30 * 0.54, 2)
    assert r["pnl"] == pytest.approx(round((0.54 - 0.40) * 30, 4))
    # Audit row: manual sleeve, terminal cashed_out, proceeds recorded.
    assert len(pool.inserts) == 1
    args = pool.inserts[0]
    assert args[5] == "cashed_out"
    assert args[11] == round(30 * 0.54, 2)


def test_sell_min_price_raises_the_limit(monkeypatch):
    calls = {}

    def fok(slug, limit, qty, sell, tif):
        calls["limit"] = limit
        return {"ok": False, "order_id": None, "status": "canceled",
                "fill_price": None, "filled_shares": 0.0, "raw": {}}

    pool = _Pool()
    _wire(monkeypatch, pool, held=(10, 0.40), bid=0.55, fok=fok)
    r = asyncio.run(live_executor.execute_manual_sell(
        "atc-mlb-x", qty=5, min_price=0.60))
    assert calls["limit"] == 0.60
    assert not r["ok"]
    assert r["filled_shares"] == 0.0
    assert pool.inserts and pool.inserts[0][5] == "unfilled"


# ── manual-order venue-scoped lookup (integration bug, 2026-08-22) ──
# live_orders ids and manual_kalshi_queue ids are independent serials;
# the single-table lookup left every Kalshi ticket polling found:false
# forever (or colliding with an unrelated PM order of the same id).


def test_manual_order_kalshi_branch_reads_the_queue():
    import inspect

    from sportsassets.api import app as app_mod

    src = inspect.getsource(app_mod.api_manual_order)
    assert "manual_kalshi_queue" in src, \
        "venue=kalshi must read the relay queue, not live_orders"
    assert 'venue: str = Query("polymarket")' in src
    # Terminal set for queue rows must include cancelled (desk cancel).
    assert '"cancelled"' in src


def test_sell_refuses_when_the_held_leg_is_unreadable(monkeypatch):
    """Both sides of these markets share one identifier, so an
    unreadable side means the sell would be priced off a coin flip.
    On a cheap long book the short leg is worth ~0.94 and the long bid
    is ~0.05 — the wrong guess sells it for a nickel."""
    _wire(monkeypatch, _Pool(), long_leg=None)
    r = asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert not r["ok"]
    assert "which side" in r["error"]


def test_a_held_short_is_priced_off_the_short_book(monkeypatch):
    """The leg reaches slug_bid rather than being assumed."""
    seen = {}

    def _bid(slug, long_leg=None):
        seen["leg"] = long_leg
        return 0.55

    _wire(monkeypatch, _Pool(), long_leg=False)
    monkeypatch.setattr(pmus, "slug_bid", _bid)
    asyncio.run(live_executor.execute_manual_sell("atc-mlb-x"))
    assert seen["leg"] is False, "the desk sold a short as if it were long"
