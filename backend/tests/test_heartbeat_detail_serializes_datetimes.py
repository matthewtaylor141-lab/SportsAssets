"""THE bettor_state HEARTBEAT WROTE NOTHING, EVERY TICK (production log):

    bettor_state: heartbeat write failed: TypeError: Object of type datetime
    is not JSON serializable

`workers/bettor_state.run` passes its tick stats to `db.heartbeat` as the
detail, and the stats carry `bucket` = `bettor_state_capture.bucket_of(at)`,
a timezone-aware datetime. `db.heartbeat` serialized the detail with a bare
`json.dumps`, so every heartbeat of that loop raised before it reached the
database and the loop's accounting was lost.

THE FIX IS AT THE SHARED LAYER: `db.heartbeat` serializes its detail with
`db.heartbeat_json` (datetimes / dates as ISO-8601, anything else that JSON
cannot carry as its str), on both the held-connection and the pool path, so
no caller can lose a heartbeat to a non-JSON value again.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sportsassets import bettor_state_capture as sc
from sportsassets import db as DB


class _Con:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args, **kw):
        self.executed.append((sql, args))
        return "INSERT 0 1"


class _Pool:
    def __init__(self, con):
        self.con = con

    def acquire(self, timeout=None):
        con = self.con

        class _Ctx:
            async def __aenter__(self):
                return con

            async def __aexit__(self, *a):
                return False
        return _Ctx()


def _tick_stats() -> dict:
    """The shape bettor_state.tick returns: `bucket` is a datetime."""
    at = datetime(2026, 10, 4, 12, 0, 7, tzinfo=UTC)
    return {"status": "ok", "cycle": sc.cycle_of(at),
            "bucket": sc.bucket_of(at), "tick": sc.tick_index(at),
            "read": 3, "written": 3, "tickS": 0.41}


def test_the_production_stats_carry_a_datetime():
    """The reproduction's premise: a bare json.dumps of these stats raises
    exactly the logged TypeError."""
    stats = _tick_stats()
    assert isinstance(stats["bucket"], datetime)
    with pytest.raises(TypeError, match="datetime is not JSON serializable"):
        json.dumps(stats)


@pytest.mark.asyncio
async def test_heartbeat_on_a_held_connection_writes_datetime_detail():
    con = _Con()
    await DB.heartbeat("bettor_state", "ok", _tick_stats(), con=con)
    (_, args), = con.executed
    detail = json.loads(args[2])
    assert detail["bucket"] == "2026-10-04T12:00:00+00:00"
    assert detail["read"] == 3


@pytest.mark.asyncio
async def test_heartbeat_through_the_pool_writes_datetime_detail(monkeypatch):
    con = _Con()

    async def _get_pool():
        return _Pool(con)
    monkeypatch.setattr(DB, "get_pool", _get_pool)
    await DB.heartbeat("bettor_state", "ok", _tick_stats())
    (_, args), = con.executed
    assert json.loads(args[2])["bucket"].startswith("2026-10-04T12:00")


def test_heartbeat_json_is_total():
    out = json.loads(DB.heartbeat_json({
        "at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "day": date(2026, 1, 2), "px": Decimal("0.52"), "s": {1, 2} - {1},
        "nested": [{"t": datetime(2026, 1, 2, tzinfo=UTC)}]}))
    assert out["at"] == "2026-01-02T03:04:05+00:00"
    assert out["day"] == "2026-01-02"
    assert out["px"] == "0.52"
    assert out["nested"][0]["t"].startswith("2026-01-02T00:00")
    assert DB.heartbeat_json(None) == "{}"
