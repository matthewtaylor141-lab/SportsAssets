import asyncio
import json

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as R
from sportsassets import pinnapi_owner as O


class Lease:
    def __init__(self, writer=True):
        self.writer = writer
    async def holds(self):
        return True
    async def writer_holds(self, *_):
        return self.writer


class WS:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.once = False
    async def send(self, value):
        self.sent.append(value)
    async def recv(self):
        if not self.once:
            self.once = True
            return json.dumps({"type":"snapshot","stream":"live","sport_id":6,"events":[]})
        await asyncio.Event().wait()
    async def close(self):
        self.closed = True


def owner(cache, **kw):
    return O.FeedOwner(cache, sport_ids=[6], streams=["live"],
                       lease_factory=None, connect=kw.pop("connect", None),
                       liveness_s=.02, **kw)


@pytest.mark.asyncio
async def test_control_row_hang_revokes_cache_and_closes_socket(monkeypatch):
    monkeypatch.setenv("pinnapi_key", "synthetic-only")
    calls = 0
    async def armed():
        nonlocal calls
        calls += 1
        # Permit both initial ownership checks; hang during periodic guard.
        if calls <= 2:
            return True
        await asyncio.Event().wait()
    ws = WS()
    async def connect(*_):
        return ws
    cache = F.FeedCache()
    o = owner(cache, connect=connect, armed=armed)
    task = asyncio.create_task(o._own(Lease(), 0))
    try:
        await asyncio.sleep(.16)
        assert task.done(), "unbounded control read left the owner running"
        assert not cache.authority.granted
        assert ws.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_stop_revokes_before_any_async_cleanup():
    cache = F.FeedCache()
    cache.new_connection([("live",6)])
    o = owner(cache)
    o.stop()
    assert not cache.authority.granted
    assert cache.read(1,"test")["ok"] is False


@pytest.mark.asyncio
async def test_missing_writer_lock_never_opens_provider_socket(monkeypatch):
    monkeypatch.setenv("pinnapi_key", "synthetic-only")
    sockets = []
    async def connect(*_):
        ws = WS()
        sockets.append(ws)
        return ws
    o = owner(F.FeedCache(), writer_pid=123, writer_key=1, connect=connect)
    await asyncio.wait_for(o._own(Lease(writer=False), 0), .5)
    assert not sockets, "opened provider socket before verifying writer ownership"
    assert o.refused == O.R_WRITER_LOST


@pytest.mark.asyncio
async def test_writer_loss_during_connect_cannot_grant_new_epoch(monkeypatch):
    monkeypatch.setenv("pinnapi_key", "synthetic-only")
    lease = Lease()
    ws = WS()
    async def connect(*_):
        lease.writer = False
        return ws
    cache = F.FeedCache()
    o = owner(cache, writer_pid=123, writer_key=1, connect=connect)
    await asyncio.wait_for(o._own(lease, 0), .5)
    assert cache.authority.epoch == 0, "a new epoch was granted after writer ownership was lost"
    assert not cache.authority.granted and ws.closed


@pytest.mark.parametrize("payload", [
    {"state":"OWNER_SYNCED","unexpected":"x"*80000},
    {"state":"OWNER_SYNCED","cache":{"other":"x"*80000}},
    {"state":"é"*80000},
])
def test_capped_heartbeat_remains_valid_json_for_every_large_field(payload):
    data = R._capped(payload)
    assert len(data.encode("utf-8")) <= R.HEARTBEAT_MAX_BYTES
    decoded = json.loads(data)
    assert decoded.get("heartbeat_truncated") is True


def test_normal_heartbeat_is_unchanged():
    payload = {"state":"OWNER_SYNCED","cache":{"markets_by_sport_type_phase":{"6/ml/live":4}}}
    assert json.loads(R._capped(payload)) == payload


@pytest.mark.asyncio
async def test_lease_release_cannot_hang_cleanup(monkeypatch):
    monkeypatch.setattr(O, "CLOSE_TIMEOUT_S", .02)
    class HungConnection:
        async def execute(self, *_):
            await asyncio.Event().wait()
    lease = O.Lease(HungConnection())
    await asyncio.wait_for(lease.release(), .2)
