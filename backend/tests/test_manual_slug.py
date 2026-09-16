"""Slug-direct manual execution (owner order 2026-08-12: every market
on a game's venue board must be executable). A desk row sourced from
the venue's own event listing has no catalog asset — it executes by
its orderable slug, server-requoted, FOK-bounded."""

import asyncio

from sportsassets import live_executor, pmus


class _Pool:
    def __init__(self):
        self.inserted = None
        self.updates = []

    async def fetchval(self, sql, *a):
        if "sum(filled_usd)" in sql:
            return 0.0
        if "status = 'submitting'" in sql:
            return None                      # nothing in flight
        if "INSERT INTO live_orders" in sql:
            self.inserted = a
            return 77
        return None

    async def execute(self, sql, *a):
        self.updates.append((" ".join(sql.split()), a))


def _wire(monkeypatch, pool, ask):
    async def fake_get_pool():
        return pool

    async def fake_paused(_pool):
        return False

    monkeypatch.setattr(live_executor, "get_pool", fake_get_pool)
    monkeypatch.setattr(live_executor, "_is_paused", fake_paused)
    monkeypatch.setattr(live_executor, "active_venue",
                        lambda: "polymarket-us")
    monkeypatch.setattr(pmus, "slug_ask", lambda slug: ask)
    submitted = []

    def fake_submit(slug, limit, shares, *a, **k):
        submitted.append((slug, limit, shares))
        return {"ok": True, "filled_shares": float(shares),
                "fill_price": limit, "order_id": "o9", "raw": {}}

    monkeypatch.setattr(pmus, "submit_fok", fake_submit)
    return submitted


def test_slug_row_executes_with_server_requote(monkeypatch):
    pool = _Pool()
    submitted = _wire(monkeypatch, pool, ask=0.44)
    r = asyncio.run(live_executor.execute_manual(
        "", 25.0, "Set 1 Winner — Giron",
        us_slug="astatc-atp-gir-roc-2026-08-12-set1-gir",
        ask_hint=0.99))                     # client hint must NOT win
    assert r["ok"] and r["filled_shares"] == 54.0
    assert submitted == [("astatc-atp-gir-roc-2026-08-12-set1-gir",
                          0.46, 54)]       # server ask 0.44 + 2c
    # The ledger row carries the slug surrogate + us_market_slug.
    assert pool.inserted[0].startswith("slug:astatc-")
    assert pool.inserted[-1] == "astatc-atp-gir-roc-2026-08-12-set1-gir"


def test_slug_row_with_no_quote_anywhere_refuses(monkeypatch):
    pool = _Pool()
    submitted = _wire(monkeypatch, pool, ask=None)
    r = asyncio.run(live_executor.execute_manual(
        "", 25.0, "x", us_slug="tsc-nowhere-2026-08-12-o2pt5",
        ask_hint=None))
    assert not r["ok"] and "no live quote" in r["error"]
    assert not submitted


def test_slug_row_falls_back_to_bounded_client_hint(monkeypatch):
    pool = _Pool()
    submitted = _wire(monkeypatch, pool, ask=None)
    r = asyncio.run(live_executor.execute_manual(
        "", 25.0, "x", us_slug="tsc-x-y-2026-08-12-o2pt5",
        ask_hint=0.50))
    assert r["ok"]
    assert submitted[0][1] == 0.52, "hint ask + 2c FOK bound"
