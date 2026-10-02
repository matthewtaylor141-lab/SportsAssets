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

WRITER_SQL = """SELECT EXISTS (SELECT 1 FROM pg_locks
   WHERE locktype = 'advisory' AND granted AND pid = $2
     AND ((classid::bigint << 32) | objid::bigint) = $1)"""
EVICTIONS_MAX = 3            # unrequested closes after a healthy stream...
EVICTION_WINDOW_S = 600.0    # ...within this window -> stop, never fight
BIG_FRAME = 256 * 1024       # decoded off the event loop

R_WRITER_LOST = "FEED_DECIDER_WRITER_LOCK_NOT_HELD"
R_DISARMED = "FEED_DISARMED_BY_CONTROL_ROW"
R_EVICTION_LOOP = "FEED_EVICTION_LOOP_SUSPECTED"
R_LEASE_LOST = "FEED_LEASE_CONNECTION_LOST_OR_NOT_HELD"
# The guard query did not answer within liveness_s. Still fails closed (the
# lease cannot be confirmed), but named apart: on 2026-10-02 every "lease
# lost" was indistinguishable from a stalled event loop or a slow database.
R_GUARD_TIMEOUT = "FEED_GUARD_CHECK_TIMED_OUT"
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
        # keepalives: a partitioned holder is noticed by the server within
        # ~25 s and its lock freed; statement_timeout bounds every check
        return cls(await asyncpg.connect(dsn, server_settings={
            "application_name": "pinnapi-feed-owner",
            "tcp_keepalives_idle": "10", "tcp_keepalives_interval": "5",
            "tcp_keepalives_count": "3", "statement_timeout": "5000"}))

    async def try_acquire(self) -> bool:
        return bool(await self.conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", FEED_LOCK_KEY))

    async def holds(self) -> bool:
        return bool(await self.conn.fetchval(HOLDS_SQL, FEED_LOCK_KEY))

    async def writer_holds(self, writer_pid: int, writer_key: int) -> bool:
        """The decider's writer lock is still granted to the decider's
        backend: the feed exists only beside the process that decides."""
        return bool(await self.conn.fetchval(WRITER_SQL, writer_key,
                                             writer_pid))

    async def release(self):
        try:
            await asyncio.wait_for(self.conn.execute(
                "SELECT pg_advisory_unlock($1)", FEED_LOCK_KEY),
                CLOSE_TIMEOUT_S)
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
            liveness_s: float = LIVENESS_S, standby_s: float = STANDBY_S,
            writer_pid: Optional[int] = None, writer_key: Optional[int] = None,
            armed: Optional[Callable] = None):
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
        self.writer_pid, self.writer_key = writer_pid, writer_key
        self.armed = armed              # async () -> bool, fail-closed
        self.evictions: list = []

    def _note(self, what, **kw):
        self.events.append(dict(at=round(self.clock(), 3), what=what, **kw))
        del self.events[:-50]

    def subscriptions(self):
        return [(s, sp) for s in self.streams for sp in self.sport_ids]

    def stop(self):
        # Revoke synchronously: async cleanup may be waiting on a DB read
        # or socket operation. No consumer may use the cache in that gap.
        self.cache.lost(R_STOPPED)
        self.stop_event.set()

    async def run(self):
        """Contend, own, lose, contend again -- until stop()."""
        attempt = 0
        while not self.stop_event.is_set():
            lease = None
            if not await self._armed():
                self.state = "DISARMED"
                await self._wait(self.standby_s)
                continue
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
                self.state = ("WRITER_LOCK_LOST" if self.refused == R_WRITER_LOST
                              else "EVICTION_LOOP_SUSPECTED"
                              if self.refused == R_EVICTION_LOOP
                              else "REFUSED_BY_PROVIDER")
                return
            if not self.stop_event.is_set():
                await self._wait(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                attempt += 1
        self.state = "STOPPED"

    async def _armed(self) -> bool:
        if self.armed is None:
            return True
        try:
            # This callback uses the shared pool, unlike lease checks on
            # the dedicated connection. Pool exhaustion must not leave
            # an already-synced cache authoritative indefinitely.
            return (await asyncio.wait_for(self.armed(),
                                           self.liveness_s)) is True
        except Exception:                                       # noqa: BLE001
            return False                  # unreadable control -> disarmed

    async def _guards(self, lease):
        """(ok, reason): lease held, decider's writer lock held, still armed."""
        try:
            if not await asyncio.wait_for(lease.holds(), self.liveness_s):
                return False, R_LEASE_LOST
            if self.writer_pid is not None and not await asyncio.wait_for(
                    lease.writer_holds(self.writer_pid, self.writer_key),
                    self.liveness_s):
                return False, R_WRITER_LOST
        except asyncio.TimeoutError:
            return False, R_GUARD_TIMEOUT
        except Exception:                                       # noqa: BLE001
            return False, R_LEASE_LOST
        if not await self._armed():
            return False, R_DISARMED
        return True, None

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
        if not await self._can_open(lease):
            return attempt
        self.state = "CONNECTING"
        ws = await asyncio.wait_for(self.connect(PP.WS_URL, key),
                                    self.liveness_s)
        delivered = False
        try:
            # Ownership or the arm row may have changed during connect.
            # Verify again before granting even an unsynced cache epoch.
            if not await self._can_open(lease):
                return attempt
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
                    ok, why = await self._guards(lease)
                    if not ok:
                        # FIRST stop being an authority, THEN close
                        self.cache.lost(why)
                        self._note(why, epoch=epoch)
                        if why == R_WRITER_LOST:
                            self.refused = R_WRITER_LOST   # never re-contend
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
                except Exception:                               # noqa: BLE001
                    # a close we did not ask for after a healthy stream may
                    # be another holder of the account key evicting us
                    if delivered:
                        t = time.monotonic()
                        self.evictions = [x for x in self.evictions
                                          if t - x < EVICTION_WINDOW_S] + [t]
                        self._note("UNREQUESTED_CLOSE",
                                   recent=len(self.evictions))
                        if len(self.evictions) >= EVICTIONS_MAX:
                            self.refused = R_EVICTION_LOOP
                            self.cache.lost(R_EVICTION_LOOP)
                    return attempt
                rx_ms = self.clock() * 1000.0     # before decoding
                last_rx = time.monotonic()
                try:
                    msg = (await asyncio.to_thread(json.loads, raw)
                           if len(raw) > BIG_FRAME else json.loads(raw))
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
                self.cache.apply(msg, epoch=epoch, received_ms=rx_ms)
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

    async def _can_open(self, lease) -> bool:
        if self.stop_event.is_set():
            self.cache.lost(R_STOPPED)
            return False
        ok, reason = await self._guards(lease)
        if self.stop_event.is_set():
            self.cache.lost(R_STOPPED)
            return False
        if not ok:
            self.cache.lost(reason)
            self._note(reason)
            if reason == R_WRITER_LOST:
                self.refused = R_WRITER_LOST
        return ok

    def status(self) -> dict:
        return {"state": self.state, "refused": self.refused,
                "writer_pid": self.writer_pid,
                "recent_unrequested_closes": len(self.evictions),
                "lease_key": FEED_LOCK_KEY, "sport_ids": self.sport_ids,
                "streams": self.streams, "transitions": self.events[-10:],
                "cache": self.cache.census()}
