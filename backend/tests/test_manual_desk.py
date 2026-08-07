"""Manual trade desk (owner directive 2026-08-07): admin-directed trades
run as their own sleeve — own budget, own P&L line, invisible to the
autonomous paths, and never blended into the AI's public record."""

import asyncio
from datetime import datetime, timezone

import pytest

from sportsassets import live_executor
from sportsassets.api.track_record import build

TS_AUG1 = 1785542400.0
TS_AUG2 = TS_AUG1 + 86_400 + 16 * 3600


class _Pool:
    def __init__(self, day_spent=0.0):
        self.day_spent = day_spent
        self.executed = []

    async def fetchval(self, q, *a, **k):
        if "sum(filled_usd)" in q:
            return self.day_spent
        if "INSERT INTO live_orders" in q:
            return 42
        return None

    async def fetchrow(self, *a, **k):
        return None

    async def execute(self, q, *a, **k):
        self.executed.append(q)


def _wire(monkeypatch, pool, ctx=None, ask=0.50, venue="polymarket-us"):
    async def fake_pool():
        return pool

    async def fake_paused(_):
        return False

    async def fake_ctx(_pool, _payload):
        return dict(ctx or {})

    async def fake_ask(_cfg, _asset):
        return ask

    monkeypatch.setattr(live_executor, "get_pool", fake_pool)
    monkeypatch.setattr(live_executor, "_is_paused", fake_paused)
    monkeypatch.setattr(live_executor, "_market_context", fake_ctx)
    monkeypatch.setattr(live_executor, "_clob_best_ask", fake_ask)
    monkeypatch.setattr(live_executor, "active_venue", lambda: venue)


def test_manual_refuses_when_venue_not_armed(monkeypatch):
    _wire(monkeypatch, _Pool(), venue=None)
    r = asyncio.run(live_executor.execute_manual("1", 50.0))
    assert not r["ok"] and "venue" in r["error"]


def test_manual_refuses_oversize_ticket(monkeypatch):
    _wire(monkeypatch, _Pool())
    r = asyncio.run(live_executor.execute_manual(
        "1", live_executor.MANUAL_MAX_PER_ORDER_USD + 1))
    assert not r["ok"] and "size" in r["error"]


def test_manual_respects_its_own_day_budget(monkeypatch):
    _wire(monkeypatch, _Pool(day_spent=live_executor.MANUAL_DAILY_USD),
          ctx={"outcome": "Yankees"})
    r = asyncio.run(live_executor.execute_manual("1", 50.0))
    assert not r["ok"] and "budget" in r["error"]


def test_manual_refuses_unknown_asset(monkeypatch):
    _wire(monkeypatch, _Pool(), ctx={"outcome": None})
    r = asyncio.run(live_executor.execute_manual("1", 50.0))
    assert not r["ok"] and "unknown asset" in r["error"]


def test_manual_respects_the_global_kill_switch(monkeypatch):
    pool = _Pool()
    _wire(monkeypatch, pool, ctx={"outcome": "Yankees"})

    async def paused(_):
        return True

    monkeypatch.setattr(live_executor, "_is_paused", paused)
    r = asyncio.run(live_executor.execute_manual("1", 50.0))
    assert not r["ok"] and "paused" in r["error"]


# ── Track record: manual money is disclosed, never the AI's ──────────


def _trade(slug, ts, qty, price):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": qty,
                      "price": {"value": price}, "createTime": ts * 1000}}


def _resolution(slug, ts):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "timestamp": ts * 1000,
            "positionResolution": {"marketSlug": slug}}


def _pos(qty, cost, value, realized=0.0, expired=False):
    return {"netPosition": qty, "cost": cost, "cashValue": value,
            "realized": realized, "expired": expired,
            "marketMetadata": {"title": "T", "outcome": "Yes"}}


def test_manual_rows_are_excluded_and_disclosed():
    positions = {
        "ai-won": _pos(0, 1.0, 0.0, realized=1.1, expired=True),
        "manual-won": _pos(0, 50.0, 0.0, realized=60.0, expired=True),
    }
    acts = [_trade("ai-won", TS_AUG2, 2, 0.5),
            _resolution("ai-won", TS_AUG2 + 3600),
            _trade("manual-won", TS_AUG2, 100, 0.5),
            _resolution("manual-won", TS_AUG2 + 3600)]
    out = build(positions, acts, TS_AUG1, manual_slugs={"manual-won"})
    assert [r["market_slug"] for r in out["trades"]] == ["ai-won"]
    assert out["summary"]["net_pnl"] == 1.1
    assert out["manual_desk"] == {"count": 1, "open": 0,
                                  "stake": 50.0, "net_pnl": 60.0}
    assert out["account"]["net_pnl"] == round(1.1 + 60.0, 2)


def test_manual_outranks_the_copy_sleeve_tag():
    """A slug in both sets is manual money — the copy sleeve's grading
    must not absorb the admin's ticket."""
    positions = {"both": _pos(0, 40.0, 0.0, realized=15.0, expired=True)}
    acts = [_trade("both", TS_AUG2, 80, 0.5),
            _resolution("both", TS_AUG2 + 3600)]
    out = build(positions, acts, TS_AUG1,
                copy_slugs={"both"}, manual_slugs={"both"})
    assert out["copy_sleeve"]["count"] == 0
    assert out["manual_desk"]["count"] == 1
    assert out["manual_desk"]["net_pnl"] == 15.0
