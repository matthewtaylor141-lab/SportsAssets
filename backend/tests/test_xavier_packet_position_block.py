"""XAVIER'S PACKET CARRIES THE POSITION, INFORMATIONALLY (closeout).

The management packet now carries the canonical position identity, entry,
cost basis, realized and unrealized-at-bid P&L, horizon, correlated groups
in the fixture and standing orders. It gates nothing -- the six elements of
xavier_packet do -- and a read that fails is null with its reason."""
from __future__ import annotations

import asyncio
import contextlib

from sportsassets import xavier_packet as XPK
from sportsassets.agents import paper_xavier as PX


class _Conn:
    def __init__(self, fail=False):
        self.fail = fail

    @contextlib.asynccontextmanager
    async def _tx(self):
        yield

    def transaction(self):
        return self._tx()

    async def fetchval(self, sql, *a):
        if self.fail:
            raise RuntimeError("down")
        return 1791400000.0 if "game_start" in sql else 2


POS = {"position_key": "pk", "group_id": "g", "strategy": "S",
       "us_market_slug": "aec-nfl-a-b", "holding_side": "LONG",
       "fixture": "fx", "label": {}, "open_qty": 100.0,
       "avg_cost_per_contract_incl_fees": 0.5, "first_fill_at": 1.0,
       "cost_basis_usd": 50.0, "realized_pnl_usd": 0.0}


def test_the_block_reads_identity_pnl_horizon_and_correlation():
    out = asyncio.run(PX.packet_position(
        _Conn(), "acct", pos=POS, mark={"bid": 0.55}, standing=[{}],
        at=1791399000.0))
    assert out["position_key"] == "pk" and out["venue"] == "PMUS"
    assert out["unrealized_at_bid_usd"] == 5.0
    assert out["seconds_to_start"] == 1000.0
    assert out["correlated_groups_in_fixture"] == 2
    assert out["standing_orders"] == 1


def test_a_failed_read_is_null_with_its_reason_and_gates_nothing():
    out = asyncio.run(PX.packet_position(
        _Conn(fail=True), "acct", pos=POS, mark={}, standing=[], at=0.0))
    assert out["event_start"] is None and out["event_start_unread"]
    assert out["correlated_groups_in_fixture"] is None
    assert out["unrealized_at_bid_usd"] is None
    assert "position" not in dict(XPK._KEYS)
