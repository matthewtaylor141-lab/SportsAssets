"""THE bettor_state HEARTBEAT WROTE NOTHING, EVERY TICK (production log):

    bettor_state: heartbeat write failed: TypeError: Object of type datetime
    is not JSON serializable

`workers/bettor_state.run` hands each tick's stats to `db.heartbeat` as the
detail, and `tick` put the observation bucket in them as a datetime
(`bettor_state_capture.bucket_of`). `db.heartbeat` serializes STRICTLY, on
purpose (tests/test_shadow_bettor.py pins "no blanket default=": it would
fix this symptom and hide the next), so the fix is at the producer: the
bucket travels as ISO-8601 UTC text. These proofs drive the real `tick`
(its early return, which carries the same stats) and the real heartbeat
writer on a held connection; before the fix both raised the TypeError.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from sportsassets import db as DB
from sportsassets.workers import bettor_state as BS


class _Con:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *args, **kw):
        self.executed.append((sql, args))
        return "INSERT 0 1"


async def _stats(monkeypatch) -> dict:
    async def no_premap(pool):
        raise RuntimeError("premap unreadable in this proof")
    monkeypatch.setattr(BS, "_candidates", no_premap)
    stats = await BS.tick(object())
    assert stats["status"] == "premap_unreadable"
    return stats


@pytest.mark.asyncio
async def test_the_tick_stats_are_strict_json(monkeypatch):
    stats = await _stats(monkeypatch)
    assert not any(isinstance(v, datetime) for v in stats.values()), stats
    out = json.loads(json.dumps(stats))          # strict: no default=
    assert datetime.fromisoformat(out["bucket"]).utcoffset().total_seconds() \
        == 0
    assert datetime.fromisoformat(out["bucket"]).second == 0


@pytest.mark.asyncio
async def test_the_loop_heartbeat_with_those_stats_is_written(monkeypatch):
    stats = await _stats(monkeypatch)
    stats["tickS"] = 0.01                        # what run() adds
    con = _Con()
    await DB.heartbeat("bettor_state", str(stats["status"]), stats, con=con)
    (_, args), = con.executed
    assert json.loads(args[2])["bucket"] == stats["bucket"]


def test_the_shared_heartbeat_serializer_is_still_strict():
    """The premise: db.heartbeat itself still refuses a datetime -- the
    producer, not the writer, owns its payload's shape."""
    with pytest.raises(TypeError, match="datetime is not JSON serializable"):
        json.dumps({"bucket": datetime(2026, 10, 4)})
