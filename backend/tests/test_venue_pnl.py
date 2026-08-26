"""The venue's own P&L, stored where code can read it.

Owner order 2026-08-26: "Pull the data directly from the POLYMARKET
ledger and check the actual profitability of each of the whale
accounts." Pulled -- and it contradicted our estimator by seven figures:
swisstony read -0.94% on the merge-graded instrument and +$23.6M
lifetime / +$1.36M per 30 days on the venue's own books. REDEEM is not
a trade, never enters our trades feed, and is how these whales realize
nearly everything -- the estimator was measuring exit mechanism, not
profitability, and two whales were cut on it.

The container's network policy blocks the venue, so runners pull the
numbers and POST them here. Display and grading input ONLY: nothing on
the order path reads the key, and roster changes stay owner decisions.
"""

from __future__ import annotations

import json

import pytest

from sportsassets.api import app as app_mod


class FakePool:
    def __init__(self, stored=None):
        self.stored = stored
        self.writes = []

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        self.stored = json.loads(args[1])

    async def fetchval(self, sql, *args):
        return self.stored


def _const(v):
    async def _f():
        return v
    return _f


def _body(**kw):
    base = {"username": "swisstony", "address": "0x204F72f3",
            "alltime": 23622370.0, "d30": 1356124.0,
            "points": 383, "first_t": 1754784000, "last_t": 1787774400}
    base.update(kw)
    return app_mod.VenuePnlWhale(**base)


class TestIngest:
    @pytest.mark.asyncio
    async def test_round_trip(self):
        pool = FakePool()
        app_mod.get_pool = _const(pool)
        out = await app_mod.api_venue_pnl_ingest(
            app_mod.VenuePnlBody(whales=[_body()]))
        assert out == {"ok": True, "stored": 1}
        got = await app_mod.api_venue_pnl()
        w = got["whales"]["swisstony"]
        assert w["alltime"] == 23622370.0
        assert w["d30"] == 1356124.0
        assert got["source"].startswith("user-pnl-api")
        assert "at" in got, "a snapshot without its age is a trap"

    @pytest.mark.asyncio
    async def test_usernames_key_lowercase(self):
        pool = FakePool()
        app_mod.get_pool = _const(pool)
        await app_mod.api_venue_pnl_ingest(app_mod.VenuePnlBody(
            whales=[_body(username="HomeRunHazard")]))
        got = await app_mod.api_venue_pnl()
        assert "homerunhazard" in got["whales"]

    @pytest.mark.asyncio
    async def test_missing_d30_survives_as_none_not_zero(self):
        """kch123 is dormant: d30 genuinely zero. A whale whose 30d
        fetch FAILED must not read the same as one who made nothing."""
        pool = FakePool()
        app_mod.get_pool = _const(pool)
        await app_mod.api_venue_pnl_ingest(app_mod.VenuePnlBody(
            whales=[_body(username="a", d30=None),
                    _body(username="b", d30=0.0)]))
        got = await app_mod.api_venue_pnl()
        assert got["whales"]["a"]["d30"] is None
        assert got["whales"]["b"]["d30"] == 0.0

    @pytest.mark.asyncio
    async def test_never_populated_says_so(self):
        app_mod.get_pool = _const(FakePool(stored=None))
        got = await app_mod.api_venue_pnl()
        assert got["whales"] == {}
        assert "never populated" in got["note"]

    def test_the_list_is_bounded(self):
        with pytest.raises(Exception):
            app_mod.VenuePnlBody(whales=[_body(username=f"w{i}")
                                         for i in range(51)])


class TestNothingOnTheOrderPathReadsIt:
    def test_live_executor_never_touches_the_key(self):
        import inspect

        from sportsassets import live_executor as le

        assert "venue_pnl" not in inspect.getsource(le), (
            "the venue P&L snapshot is grading input, not an order "
            "gate; wiring it into the executor is an owner decision "
            "that has not been made")
