"""A SELL that died on an exception was erased; now it pends.

execute_copy's blanket handler returned None on every exception.
whale_exits reads a non-pending return as "handled" and advances its
snapshot -- so a transient DB blip or the sweep's 60s cancellation did
not delay an exit, it ERASED it: next cycle's baseline already agreed
the whale was out, and we held against him to resolution.

The old refusal to retry ("an order may be in flight; selling twice is
worse") is answered by machinery that exists now: the atomic claim
leaves a retried row in 'exiting' (mx_already_claimed, itself pending),
a mid-venue cancellation marks for reconciliation without releasing,
and _reap_stale_exiting resolves stranded rows against the venue. A
retry cannot double-sell; it re-enters a machine that refuses
duplicates.
"""

from __future__ import annotations

import asyncio

from sportsassets import live_executor as le


class TestTheHandler:
    def _boom(self, monkeypatch):
        async def boom(payload):
            raise RuntimeError("db blip")

        monkeypatch.setattr(le, "mirror_exit", boom)

    def test_a_sell_exception_returns_the_pending_reason(self, monkeypatch):
        self._boom(monkeypatch)
        out = asyncio.run(le.execute_copy({
            "side": "SELL", "whale_username": "rn1", "asset": "tok",
            "closed_frac": 1.0}))
        assert out == "mx_exception_pending"

    def test_the_reason_is_in_the_pending_allowlist(self):
        """Without this, the return is just a new name for silence:
        whale_exits pins ONLY reasons on the allowlist."""
        assert "mx_exception_pending" in le.EXIT_PENDING_REASONS

    def test_a_buy_exception_still_returns_none(self, monkeypatch):
        """No caller reads the BUY return; inventing one would create
        an interface nothing consumes and someone later trusts."""
        async def boom(payload, reaction):
            raise RuntimeError("db blip")

        monkeypatch.setattr(le, "maybe_execute", boom)
        out = asyncio.run(le.execute_copy({
            "side": "BUY", "whale_username": "rn1", "asset": "tok",
            "notional": 50, "price": 0.5}))
        assert out is None

    def test_it_is_counted_in_the_census(self, monkeypatch):
        self._boom(monkeypatch)
        before = dict(le._EXIT_CENSUS)
        asyncio.run(le.execute_copy({
            "side": "SELL", "whale_username": "rn1", "asset": "tok",
            "closed_frac": 1.0}))
        after = le._EXIT_CENSUS
        assert after.get("mx_exception_pending", 0) == \
            before.get("mx_exception_pending", 0) + 1


class TestSafetyOfTheRetry:
    def test_already_claimed_is_pending_so_the_retry_waits(self):
        """The retry's first collision: the row is 'exiting' from the
        failed attempt. mx_already_claimed pending means the ratchet
        holds the exit rather than dropping it while the reaper
        resolves the row."""
        assert "mx_already_claimed" in le.EXIT_PENDING_REASONS

    def test_the_reaper_exists_and_is_wired(self):
        import inspect

        from sportsassets.workers import copy_sweep

        assert hasattr(le, "_reap_stale_exiting")
        assert "_reap_stale_exiting(pool)" in inspect.getsource(
            copy_sweep.sweep_once)
