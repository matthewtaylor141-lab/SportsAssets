"""THE ONE PINNAPI SOCKET OWNER.

PinnAPI allows one WebSocket per account; a second connection evicts the
first. So exactly one process may hold the socket, and a process that is no
longer sure it is that process must stop publishing usable prices BEFORE it
does anything else.

  lease     a Postgres session advisory lock (FEED_LOCK_KEY, the same key the
            bounded sampler takes) held on a DEDICATED connection. A standby
            re-asks every STANDBY_S. The sampler and the owner exclude each
            other through it.
  liveness  every LIVENESS_S the owner asks the server whether its own backend
            still holds the key (pg_locks, granted, pg_backend_pid()). A
            failed or negative answer -> authority.revoke() at once (reads
            answer FEED_OWNERSHIP_NOT_HELD), then the socket is closed with a
            bounded wait, then the lease connection is discarded and the
            owner contends again from scratch.
  epochs    every socket opened under a held lease is a new epoch; the cache
            drops all earlier state and serves nothing until the provider's
            snapshot for every subscribed (stream, sport) has arrived on that
            epoch (resynchronization).
  shutdown  stop flag -> revoke -> bounded socket close -> unlock -> close the
            lease connection. Never the other order.

Socket and lease are injected (`connect`, `lease_factory`) so the ownership
rules are tested against a real Postgres without the provider.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Callable, Optional

from . import pinnapi_feed as F
from . import pinnapi_probe as PP

log = logging.getLogger(__name__)

FEED_LOCK_KEY = PP.FEED_LOCK_KEY
LIVENESS_S = 5.0
STANDBY_S = 5.0
CLOSE_TIMEOUT_S = 5.0
SILENCE_S = 75.0                 # provider pings every 30 s
BACKOFF = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
STOP_ON = ("unauthorized", "plan_lacks_ws")

HOLDS_SQL = """SELECT EXISTS (SELECT 1 FROM pg_locks
   WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid()
     AND ((classid::bigint << 32) | objid::bigint) = $1)"""

R_LEASE_LOST = "FEED_LEASE_CONNECTION_LOST_OR_NOT_HELD"
R_SOCKET_CLOSED = "FEED_SOCKET_CLOSED"
R_SILENCE = "FEED_PROVIDER_SILENT"
R_STOPPED = "FEED_OWNER_STOPPED"
R_PROVIDER_REFUSED = "FEED_PROVIDER_REFUSED"


class Lease:
    """The advisory lock on its own connection."""

    def __init__(self, conn):
        self.conn = conn

    @classmethod
    async def open(cls, dsn: str):
        import asyncpg
        return cls(await asyncpg.connect(dsn))

    async def try_acquire(self) -> bool:
        return bool(await self.conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", FEED_LOCK_KEY))

    async def holds(self) -> bool:
        return bool(await self.conn.fetchval(HOLDS_SQL, FEED_LOCK_KEY))

    async def release(self):
        try:
            await self.conn.execute("SELECT pg_advisory_unlock($1)",
                                    FEED_LOCK_KEY)
        except Exception:                                       # noqa: BLE001
            pass

    async def close(self):
        try:
            await asyncio.wait_for(self.conn.close(), CLOSE_TIMEOUT_S)
        except Exception:                                       # noqa: BLE001
            try:
                self.conn.terminate()
            except Exception:                                   # noqa: BLE001
                pass


class FeedOwner:
    def __init__(self, cache: F.FeedCache, *, sport_ids, streams=(
            "live", "prematch"), lease_factory: Callable, connect: Callable,
            key_env: str = PP.KEY_ENV, clock=time.time, sleep=asyncio.sleep,
            liveness_s: float = LIVENESS_S, standby_s: float = STANDBY_S):
        self.cache = cache
        self.sport_ids = sorted({int(s) for s in sport_ids})
        self.streams = list(streams)
        self.lease_factory, self.connect = lease_factory, connect
        self.key_env, self.clock, self.sleep = key_env, clock, sleep
        self.liveness_s, self.standby_s = liveness_s, standby_s
        self.stop_event = asyncio.Event()
        self.state = "STARTING"
        self.events: list = []            # bounded transition log
        self.refused: Optional[str] = None

    def _note(self, what, **kw):
        self.events.append(dict(at=round(self.clock(), 3), what=what, **kw))
        del self.events[:-50]

    def subscriptions(self):
        return [(s, sp) for s in self.streams for sp in self.sport_ids]

    def stop(self):
        self.stop_event.set()

    async def run(self):
        """Contend, own, lose, contend again -- until stop()."""
        attempt = 0
        while not self.stop_event.is_set():
            lease = None
            try:
                lease = await self.lease_factory()
                if not await lease.try_acquire():
                    self.state = "STANDBY"
                    await lease.close()
                    lease = None
                    await self._wait(self.standby_s)
                    continue
                self._note("LEASE_ACQUIRED")
                attempt = await self._own(lease, attempt)
            except Exception as exc:                            # noqa: BLE001
                self.cache.lost(R_LEASE_LOST)
                self._note("OWNER_ERROR", error=type(exc).__name__)
            finally:
                if self.cache.authority.granted:
                    self.cache.lost(R_STOPPED if self.stop_event.is_set()
                                    else R_LEASE_LOST)
                if lease is not None:
                    await lease.release()
                    await lease.close()
                    self._note("LEASE_RELEASED")
            if self.refused:
                self.state = "REFUSED_BY_PROVIDER"
                return
            if not self.stop_event.is_set():
                await self._wait(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                attempt += 1
        self.state = "STOPPED"

    async def _wait(self, s):
        try:
            await asyncio.wait_for(self.stop_event.wait(), s)
        except asyncio.TimeoutError:
            pass

    async def _own(self, lease, attempt) -> int:
        key = os.environ.get(self.key_env)
        if not key:
            self.refused = "KEY_NOT_PRESENT_IN_THIS_SERVICE"
            return attempt
        self.state = "CONNECTING"
        ws = await self.connect(PP.WS_URL, key)
        delivered = False
        try:
            epoch = self.cache.new_connection(self.subscriptions())
            self._note("EPOCH_GRANTED", epoch=epoch)
            await ws.send(json.dumps({"type": "subscribe",
                                      "streams": self.streams,
                                      "sport_ids": self.sport_ids}))
            self.state = "RESYNCHRONIZING"
            last_live = time.monotonic()
            last_rx = time.monotonic()
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now - last_live >= self.liveness_s:
                    try:
                        ok = await asyncio.wait_for(lease.holds(),
                                                    self.liveness_s)
                    except Exception:                           # noqa: BLE001
                        ok = False
                    if not ok:
                        # FIRST stop being an authority, THEN close
                        self.cache.lost(R_LEASE_LOST)
                        self._note("LEASE_LOST", epoch=epoch)
                        return 0
                    last_live = now
                if now - last_rx > SILENCE_S:
                    self.cache.lost(R_SILENCE)
                    self._note("PROVIDER_SILENT", epoch=epoch)
                    return attempt
                try:
                    raw = await asyncio.wait_for(ws.recv(), min(
                        self.liveness_s, 1.0))
                except asyncio.TimeoutError:
                    continue
                last_rx = time.monotonic()
                try:
                    msg = json.loads(raw)
                except Exception:                               # noqa: BLE001
                    continue
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                if isinstance(msg, dict) and msg.get("type") == "error":
                    code = str(msg.get("code") or msg.get("error"))
                    if code in STOP_ON:
                        self.refused = code
                        self.cache.lost(R_PROVIDER_REFUSED)
                        self._note("PROVIDER_REFUSED", code=code)
                        return attempt
                self.cache.apply(msg, epoch=epoch,
                                 received_ms=self.clock() * 1000.0)
                if not delivered:
                    delivered = True
                    attempt = 0       # backoff resets only on delivery
                if self.cache.authority.synced:
                    self.state = "OWNER_SYNCED"
            return attempt
        finally:
            self.cache.lost(R_SOCKET_CLOSED)
            try:
                await asyncio.wait_for(ws.close(), CLOSE_TIMEOUT_S)
            except Exception:                                   # noqa: BLE001
                pass
            self._note("SOCKET_CLOSED")

    def status(self) -> dict:
        return {"state": self.state, "refused": self.refused,
                "lease_key": FEED_LOCK_KEY, "sport_ids": self.sport_ids,
                "streams": self.streams, "transitions": self.events[-10:],
                "cache": self.cache.census()}
