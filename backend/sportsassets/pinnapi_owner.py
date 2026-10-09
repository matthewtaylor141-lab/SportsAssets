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
# The arm control row could not be read (timeout, or pool/DB error). Still
# fails closed, but named apart from a row that actually reads disarmed: on
# 2026-10-02 14:48:50Z a revocation was labelled FEED_DISARMED_BY_CONTROL_ROW
# while the row read true before and after it.
R_CONTROL_TIMEOUT = "FEED_CONTROL_READ_TIMED_OUT"
R_CONTROL_UNREADABLE = "FEED_CONTROL_UNREADABLE"
R_SOCKET_CLOSED = "FEED_SOCKET_CLOSED"
R_SILENCE = "FEED_PROVIDER_SILENT"
R_STOPPED = "FEED_OWNER_STOPPED"
R_PROVIDER_REFUSED = "FEED_PROVIDER_REFUSED"


#: ── THE WEDGED OWNER (P0 first-loss, production 2026-10-06) ─────────────
#:
#: MEASURED (research-sql run 37411912022). From 01:29:56Z the deciding
#: process had NO feed authority for 2.5 h while every other loop in it ran:
#: the heartbeat read state STARTING, epoch 0, transitions LEASE_ACQUIRED
#: (01:29:50) -> FEED_GUARD_CHECK_TIMED_OUT (01:29:56) and nothing after;
#: pg_locks showed the feed lease (7723901544120036) still granted to the
#: `pinnapi-feed-owner` session opened at 01:29:50, idle. Every candidate
#: read FEED_OWNERSHIP_NOT_HELD (94-102 rows/h, 0 before), no reactive
#: attempt was written after 01:59Z, and 38 provider events' first loss in
#: the 24 h census was FEED_OWNERSHIP_NOT_HELD.
#:
#: THE MECHANISM (reproduced on Python 3.12.3 / asyncpg 0.31 against a real
#: Postgres, tests/test_p0_first_loss_plumbing.py). The guard's `holds()`
#: timed out; asyncpg answers a cancelled query by sending a CancelRequest
#: on a NEW connection and awaiting the server's disconnect with no timeout
#: (connect_utils._cancel). While that is unanswered, the teardown's
#: `release()` waits on it and times out, then `close()` sets the protocol's
#: `closing` flag and awaits the same (now cancelled) future: a
#: CancelledError that is NOT a cancellation of the task. `close()` then
#: calls `_abort()`, which returns at once because `closing` is already set
#: -- the socket stays open and the lock stays held -- and the
#: CancelledError (a BaseException) escaped `except Exception`, out of
#: `run()`'s `finally`, ending the owner task for good. Nothing restarted
#: it, and its leaked session kept every later contender in STANDBY.
#:
#: THE REPAIR: a lease session whose state is in doubt (a guard that timed
#: out or raised, a failed unlock) is DISCARDED -- its transport aborted
#: synchronously, which ends the server session and frees the lock -- never
#: awaited; teardown never raises; a CancelledError that is not the task's
#: own cancellation is an owner error that re-contends, not an exit; and the
#: runtime restarts an owner task that ended without being stopped or
#: refused (pinnapi_feed_runtime.supervise). The 30 s rule, the lease, the
#: writer fence and the arm row are unchanged.
R_STRAY_CANCELLATION = "FEED_OWNER_STRAY_CANCELLATION"


#: ── WHO CLOSED THE SOCKET (RC6, production 2026-10-08) ──────────────────
#:
#: MEASURED (research-sql run 37839436591, the heartbeat's transitions and
#: pg_locks). After the API restart at 18:39:27Z the owner logged three
#: unrequested closes inside EVICTION_WINDOW_S -- the third at 19:00:51Z
#: (epoch 3 lived 179 s, epoch 4 309 s) -- set refused =
#: FEED_EVICTION_LOOP_SUSPECTED and returned for good. From then on no
#: backend held the feed lease (7723901544120036), loop health read
#: pinnapi_feed.heartbeat UNHEALTHY (WRITER_LOCK_HELD_BY_NO_BACKEND), every
#: held read and every PinnAPI-only candidate answered
#: FEED_OWNERSHIP_NOT_HELD (324 rows in the 19:00 hour, 174 in the next 26
#: min) and 0 of 3 held PAPER positions had a current packet. Each close was
#: counted as "another holder of the account key evicting us", yet nothing
#: recorded WHO closed it: `except Exception` kept no close code and no side.
#:
#: THE DEFECT. PinnAPI's eviction is the PROVIDER closing the older socket
#: when a newer one for the same key registers (docs: "opening a new socket
#: will close any previous one for the same key"). A close THIS client
#: initiated is never that. The websockets library fails the connection
#: itself with 1011 "keepalive ping timeout" when its ping is not answered
#: within ping_timeout -- which is what an event loop that cannot run for
#: that long produces even though the provider answered (the API's loop
#: watchdog recorded stalls at 18:52:21Z and 18:55:04-18:56:07Z, 19 s and
#: 4-36 s before the first two closes) -- and with 1009 when a frame
#: exceeds max_size. Counting those as evictions turned a stall of our own
#: into the loss of the feed for the rest of the process's life.
#:
#: THE REPAIR. Every unrequested close is classified from the library's own
#: record of the closing handshake (ConnectionClosed.rcvd / .sent /
#: .rcvd_then_sent; `close_of`): SERVER (the provider's close frame came
#: first), CLIENT (ours came first, or only ours exists), NO_CLOSE_FRAME
#: (the connection ended with neither -- 1006) or UNKNOWN (not a websockets
#: close at all). A CLIENT close is named R_CLIENT_CLOSED and reconnects
#: with the ordinary backoff on a new epoch (resynchronized like any other).
#: SERVER, NO_CLOSE_FRAME and UNKNOWN count toward EVICTIONS_MAX exactly as
#: before -- an eviction may come with or without a close frame, so the
#: owner still never fights. The side, the code and a bounded reason ride on
#: the transition and on status(), so the next census names the cause. The
#: lease, the writer fence, the arm row, EVICTIONS_MAX / EVICTION_WINDOW_S
#: and the 30 s rule are unchanged.
R_CLIENT_CLOSED = "FEED_SOCKET_CLOSED_BY_THIS_CLIENT"
CLOSE_SERVER = "SERVER"
CLOSE_CLIENT = "CLIENT"
CLOSE_NO_FRAME = "NO_CLOSE_FRAME"
CLOSE_UNKNOWN = "UNKNOWN"
#: the sides an eviction by another holder of the key can look like
EVICTION_CONSISTENT = (CLOSE_SERVER, CLOSE_NO_FRAME, CLOSE_UNKNOWN)
#: RFC 6455 7.1.5: no close frame was received or sent
ABNORMAL_CLOSURE = 1006
CLOSE_REASON_MAX = 64
#:
#: AN AMBIGUOUS RECORD IS EVICTION-CONSISTENT (RC6 integration review). When
#: the library recorded BOTH close frames but not which came first
#: (rcvd_then_sent None), the side cannot be read. The xavier cut called that
#: CLIENT -- never counted toward EVICTIONS_MAX, so a provider close frame
#: that may have been an eviction could be fought indefinitely. It is
#: UNKNOWN now (EVICTION_CONSISTENT), carrying the PROVIDER's frame's code
#: and reason, which is what an eviction would say.
#:
#: THE CLOSE'S OWN FACTS, IN THE SAME RECORD (rc6/pipeline-reds abf4ea43).
#: Beside the side, code and reason: how long since the socket's last
#: received frame (`rx_gap_s`: a long gap points at our event loop or the
#: network, not at a newer holder) and how long the socket had lived
#: (`socket_age_s`), both on the monotonic clock. One record per close --
#: the transition's `close`, `last_unrequested_close`, the heartbeat -- never
#: a second, parallel one. No header, key or payload is read.


def _gap(now, then) -> Optional[float]:
    try:
        if now is None or then is None:
            return None
        return round(max(0.0, float(now) - float(then)), 3)
    except (TypeError, ValueError):
        return None


def _close_frame(frame) -> dict:
    """{code, reason} of one websockets Close frame; the reason is the
    peer's own short text, bounded and printable only."""
    try:
        code = int(getattr(frame, "code"))
    except (TypeError, ValueError, AttributeError):
        code = None
    reason = getattr(frame, "reason", None)
    if reason is not None:
        reason = "".join(ch for ch in str(reason)[:CLOSE_REASON_MAX]
                         if ch.isprintable())
    return {"code": code, "reason": reason}


def close_of(exc, *, now: Optional[float] = None,
             last_rx: Optional[float] = None,
             opened: Optional[float] = None) -> dict:
    """WHO CLOSED THE SOCKET, from the exception `recv()` raised, and the
    close's own facts. Pure; never raises. {"initiator", "code", "reason",
    "error", "rx_gap_s", "socket_age_s"}: initiator is SERVER, CLIENT,
    NO_CLOSE_FRAME or UNKNOWN (see R_CLIENT_CLOSED); the two gaps are
    seconds on the caller's monotonic clock (`now` - `last_rx`, `now` -
    `opened`), None when not given."""
    out = {"error": type(exc).__name__,
           "rx_gap_s": _gap(now, last_rx), "socket_age_s": _gap(now, opened)}
    try:
        if not (hasattr(exc, "rcvd") and hasattr(exc, "sent")):
            return dict(out, initiator=CLOSE_UNKNOWN, code=None, reason=None)
        rcvd, sent = exc.rcvd, exc.sent
        then = getattr(exc, "rcvd_then_sent", None)
        if rcvd is not None and sent is not None and then is None:
            # both frames, order unknown: never assumed ours
            return dict(out, initiator=CLOSE_UNKNOWN, **_close_frame(rcvd))
        if rcvd is not None and (sent is None or then is True):
            return dict(out, initiator=CLOSE_SERVER, **_close_frame(rcvd))
        if sent is not None:
            return dict(out, initiator=CLOSE_CLIENT, **_close_frame(sent))
        return dict(out, initiator=CLOSE_NO_FRAME, code=ABNORMAL_CLOSURE,
                    reason=None)
    except Exception:                                           # noqa: BLE001
        return dict(out, initiator=CLOSE_UNKNOWN, code=None, reason=None)


#: ── A CLOSE OF OUR OWN THAT KEEPS COMING WAITS LONGER EACH TIME (RC6) ────
#:
#: THE DEFECT (integration review of 412c4962). A CLIENT close reconnects
#: with the ordinary BACKOFF, and that backoff resets on every delivered
#: epoch (`attempt = 0`, "backoff resets only on delivery"). A CLIENT close
#: that repeats AFTER delivery -- a provider frame that keeps exceeding our
#: frame cap (1009), an event loop that keeps stalling past the keepalive
#: (1011) -- therefore reconnected every BACKOFF[0] (1 s) forever, each time
#: a new epoch that drops the cache and reloads every subscribed snapshot,
#: and nothing on the heartbeat said so.
#:
#: THE REPAIR: CLIENT closes are counted in a rolling CLIENT_CLOSE_WINDOW_S
#: of CONNECTED time: the seconds this owner's sockets were actually open
#: (from the completed handshake to the socket's end), summed over epochs.
#: Nothing else is on that clock -- not a slow connect, not lease, standby
#: or arm-row time, not a backoff wait whether it ran its scheduled length,
#: longer, or was cut short -- so none of them can thin or pad the window
#: (integration review of 0a40533b, which subtracted the SCHEDULED waits
#: from the monotonic clock and so counted all of those as connected).
#: The first CLIENT_CLOSES_FREE inside it reconnect as
#: before; the level is the number of closes in the window beyond those,
#: and the owner waits client_close_wait_s(level) -- 5 s, doubling, at most
#: 300 s -- before it contends again. Delivery does NOT reset it. Counting
#: connected time is what keeps the two cases apart (integration review):
#: a storm (a close about 1 s after every reconnect) keeps filling the
#: window however long the waits grow, so it climbs to and holds 300 s; a
#: close every ~240 s of streaming (the cadence of the 2026-10-08 keepalive
#: 1011s, three in 491 s) never holds more than 3 in the window, so it
#: waits a steady 5 s instead of climbing to 300 s and keeping the feed
#: down most of the time. The window empties after CLIENT_CLOSE_WINDOW_S of
#: connected time with no CLIENT close. The wait is the owner's state (CLIENT_CLOSE_BACKOFF), the authority's reason
#: (R_CLIENT_CLOSE_BACKOFF), a transition, and `client_close_backoff` on
#: status() and so on the heartbeat. It is never a refusal and never counts
#: toward EVICTIONS_MAX; a stop ends it at once. The lease is released while
#: it waits, as in every backoff. EVICTIONS_MAX, EVICTION_WINDOW_S,
#: SILENCE_S and the 30 s rule are unchanged.
CLIENT_CLOSE_WINDOW_S = 600.0
CLIENT_CLOSES_FREE = 2
CLIENT_CLOSE_BACKOFF_BASE_S = 5.0
CLIENT_CLOSE_BACKOFF_MAX_S = 300.0
#: the owner's state and the transition while it waits
CLIENT_CLOSE_BACKOFF = "CLIENT_CLOSE_BACKOFF"
#: the authority's revocation reason while it waits
R_CLIENT_CLOSE_BACKOFF = "FEED_CLIENT_CLOSE_BACKOFF"


def client_close_wait_s(level: int) -> float:
    """Seconds to wait before reconnecting at a CLIENT-close backoff level:
    0 at level 0, then 5, 10, 20 ... capped at 300. Pure."""
    n = int(level or 0)
    if n <= 0:
        return 0.0
    return float(min(CLIENT_CLOSE_BACKOFF_MAX_S,
                     CLIENT_CLOSE_BACKOFF_BASE_S * 2 ** min(n - 1, 16)))


def _task_is_being_cancelled() -> bool:
    """True when the CURRENT task itself was asked to cancel (shutdown),
    as opposed to a CancelledError raised from a driver future."""
    t = asyncio.current_task()
    try:
        return bool(t is not None and t.cancelling())
    except AttributeError:                       # Python < 3.11
        return True


class Lease:
    """The advisory lock on its own connection."""

    def __init__(self, conn):
        self.conn = conn
        #: set when this session's state is no longer known (a guard check
        #: that timed out or raised, an unlock that failed): it is then
        #: discarded, never awaited (see R_STRAY_CANCELLATION above)
        self.in_doubt = False
        self.discarded = False

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

    def discard(self) -> None:
        """END THE SESSION NOW, synchronously, awaiting nothing: the server
        frees the session's advisory lock when its socket closes. Used for
        a session in doubt. `terminate()` alone is a no-op once a graceful
        close has set the protocol's `closing` flag, so the transport is
        aborted directly as well. Never raises."""
        self.discarded = True
        conn = self.conn
        try:
            conn.terminate()
        except Exception:                                       # noqa: BLE001
            pass
        tr = getattr(conn, "_transport", None)
        try:
            if tr is not None and not tr.is_closing():
                tr.abort()
        except Exception:                                       # noqa: BLE001
            pass

    async def release(self):
        """Unlock, bounded. Never raises except the task's own
        cancellation; any failure marks the session in doubt."""
        if self.in_doubt or self.discarded:
            return
        try:
            await asyncio.wait_for(self.conn.execute(
                "SELECT pg_advisory_unlock($1)", FEED_LOCK_KEY),
                CLOSE_TIMEOUT_S)
        except asyncio.CancelledError:
            self.in_doubt = True
            if _task_is_being_cancelled():
                self.discard()
                raise
        except Exception:                                       # noqa: BLE001
            self.in_doubt = True

    async def close(self):
        """Close, bounded; a session in doubt (or one whose graceful close
        fails in any way) is discarded. Never raises except the task's own
        cancellation, and then only after the session is discarded."""
        if self.discarded:
            return
        if self.in_doubt:
            self.discard()
            return
        try:
            await asyncio.wait_for(self.conn.close(), CLOSE_TIMEOUT_S)
        except asyncio.CancelledError:
            self.discard()
            if _task_is_being_cancelled():
                raise
        except Exception:                                       # noqa: BLE001
            self.discard()


def _mark_in_doubt(lease) -> None:
    try:
        lease.in_doubt = True
    except Exception:                                           # noqa: BLE001
        pass


async def retire_lease(lease) -> None:
    """Unlock and close a lease, or discard it when its session is in doubt.
    Never raises except the task's own cancellation."""
    if lease is None:
        return
    await lease.release()
    await lease.close()


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
        #: every unrequested close by side (R_CLIENT_CLOSED) and the last one
        self.closes_by_initiator: dict = {}
        self.last_close: Optional[dict] = None
        #: CLIENT closes (monotonic) inside CLIENT_CLOSE_WINDOW_S, the
        #: backoff level they reached, the last one, and the wait they ask
        #: of run() (see CLIENT_CLOSE_BACKOFF). Never reset by delivery,
        #: nor by a stand-down's re-entry: they belong to this owner.
        self.client_closes: list = []
        #: THE CONNECTED CLOCK: seconds this owner's sockets were open
        #: (handshake complete to socket end), summed over every epoch; the
        #: CLIENT closes above are kept on it
        self.connected_s = 0.0
        self.client_close_level = 0
        self.client_close_wait = 0.0
        #: the last CLIENT-close backoff, for the heartbeat (wall clock:
        #: display only)
        self.client_close_backoff: Optional[dict] = None
        #: the lease currently held (or being acquired), so a supervisor can
        #: discard it if this owner's task ever ends without retiring it
        self.lease = None

    def _note(self, what, **kw):
        self.events.append(dict(at=round(self.clock(), 3), what=what, **kw))
        del self.events[:-50]

    def subscriptions(self):
        return [(s, sp) for s in self.streams for sp in self.sport_ids]

    def _client_close(self, c: float) -> float:
        """Count one CLIENT close at `c` on the CONNECTED clock (seconds
        this owner's sockets have been open, see connected_s); return the
        seconds to wait before reconnecting (0.0 below the threshold). The
        level is the number of CLIENT closes beyond CLIENT_CLOSES_FREE inside
        the last CLIENT_CLOSE_WINDOW_S of connected time, so a storm holds
        its level however long the waits grow and a sparse cadence stays at
        the first step. Pure in its inputs: no wall or monotonic read."""
        self.client_closes = [x for x in self.client_closes
                              if c - x < CLIENT_CLOSE_WINDOW_S] + [c]
        self.client_close_level = max(
            0, len(self.client_closes) - CLIENT_CLOSES_FREE)
        self.client_close_wait = client_close_wait_s(self.client_close_level)
        return self.client_close_wait

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
                self.lease = lease
                if not await lease.try_acquire():
                    self.state = "STANDBY"
                    await lease.close()
                    lease = self.lease = None
                    await self._wait(self.standby_s)
                    continue
                self._note("LEASE_ACQUIRED")
                self.state = "LEASE_HELD"
                attempt = await self._own(lease, attempt)
            except asyncio.CancelledError:
                if _task_is_being_cancelled():
                    raise
                # NOT a cancellation of this task: a driver future that was
                # cancelled under us (see R_STRAY_CANCELLATION). An owner
                # error -- authority revoked, session discarded, contend
                # again -- never an exit.
                self.cache.lost(R_STRAY_CANCELLATION)
                _mark_in_doubt(lease)
                self._note(R_STRAY_CANCELLATION)
            except Exception as exc:                            # noqa: BLE001
                self.cache.lost(R_LEASE_LOST)
                _mark_in_doubt(lease)
                self._note("OWNER_ERROR", error=type(exc).__name__)
            finally:
                if self.cache.authority.granted:
                    self.cache.lost(R_STOPPED if self.stop_event.is_set()
                                    else R_LEASE_LOST)
                if lease is not None:
                    doubt = bool(getattr(lease, "in_doubt", False))
                    await retire_lease(lease)
                    self.lease = None
                    self._note("LEASE_DISCARDED" if doubt or getattr(
                        lease, "in_doubt", False) else "LEASE_RELEASED")
            if self.refused:
                self.state = ("WRITER_LOCK_LOST" if self.refused == R_WRITER_LOST
                              else "EVICTION_LOOP_SUSPECTED"
                              if self.refused == R_EVICTION_LOOP
                              else "REFUSED_BY_PROVIDER")
                return
            if not self.stop_event.is_set():
                wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                cw, self.client_close_wait = self.client_close_wait, 0.0
                if cw > wait:
                    # A CLOSE OF OUR OWN THAT KEEPS COMING: named, never a
                    # refusal (see CLIENT_CLOSE_BACKOFF)
                    wait = cw
                    self._client_close_backoff_begins(cw)
                await self._wait(wait)
                if self.client_close_backoff is not None:
                    self.client_close_backoff["waiting"] = False
                attempt += 1
        self.state = "STOPPED"

    def _client_close_backoff_begins(self, wait: float) -> None:
        now = self.clock()
        self.state = CLIENT_CLOSE_BACKOFF
        self.cache.lost(R_CLIENT_CLOSE_BACKOFF)
        self.client_close_backoff = {
            "waiting": True, "wait_s": wait, "level": self.client_close_level,
            "closes_in_window": len(self.client_closes),
            "window_s": CLIENT_CLOSE_WINDOW_S,
            "window_clock": "CONNECTED_SOCKET_SECONDS",
            "connected_s": round(self.connected_s, 3),
            "since_at": round(now, 3), "until_at": round(now + wait, 3)}
        self._note(CLIENT_CLOSE_BACKOFF, wait_s=wait,
                   level=self.client_close_level,
                   closes_in_window=len(self.client_closes))
        log.warning("pinnapi feed: %d closes of our own inside %.0fs of "
                    "connected time; waiting %.0fs before reconnecting",
                    len(self.client_closes), CLIENT_CLOSE_WINDOW_S, wait)

    async def _arm_state(self):
        """None when armed, else the reason it is not (all fail closed)."""
        if self.armed is None:
            return None
        try:
            # This callback uses the shared pool, unlike lease checks on
            # the dedicated connection. Pool exhaustion must not leave
            # an already-synced cache authoritative indefinitely.
            got = await asyncio.wait_for(self.armed(), self.liveness_s)
        except asyncio.TimeoutError:
            return R_CONTROL_TIMEOUT
        except Exception:                                       # noqa: BLE001
            return R_CONTROL_UNREADABLE
        return None if got is True else R_DISARMED

    async def _armed(self) -> bool:
        return await self._arm_state() is None

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
            # the check's query may still be in flight (or its cancellation
            # unanswered): the session is in doubt and is discarded
            _mark_in_doubt(lease)
            return False, R_GUARD_TIMEOUT
        except Exception:                                       # noqa: BLE001
            _mark_in_doubt(lease)
            return False, R_LEASE_LOST
        why = await self._arm_state()
        if why is not None:
            return False, why
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
        opened = time.monotonic()           # the socket's age, for close_of
        closed_at = None                    # when recv reported the close
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
                except Exception as exc:                        # noqa: BLE001
                    t = closed_at = time.monotonic()
                    info = dict(close_of(exc, now=t, last_rx=last_rx,
                                         opened=opened), epoch=epoch,
                                delivered=delivered)
                    self.last_close = dict(info, at=round(self.clock(), 3))
                    side = info["initiator"]
                    self.closes_by_initiator[side] = \
                        self.closes_by_initiator.get(side, 0) + 1
                    if side == CLOSE_CLIENT:
                        # OURS (keepalive timeout, frame cap): never another
                        # holder of the key; reconnect like any lost socket
                        # -- after a growing wait when it keeps coming
                        self.cache.lost(R_CLIENT_CLOSED)
                        wait = self._client_close(
                            self.connected_s + max(0.0, t - opened))
                        self._note(R_CLIENT_CLOSED, close=info,
                                   closes_in_window=len(self.client_closes),
                                   backoff_s=wait)
                        return attempt
                    # a close we did not ask for after a healthy stream may
                    # be another holder of the account key evicting us
                    if delivered:
                        self.evictions = [x for x in self.evictions
                                          if t - x < EVICTION_WINDOW_S] + [t]
                        self._note("UNREQUESTED_CLOSE",
                                   recent=len(self.evictions), close=info)
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
            # the connected clock: this socket's open time, and nothing after
            # its end (the close handshake, the backoff that follows)
            self.connected_s += max(0.0, (closed_at if closed_at is not None
                                          else time.monotonic()) - opened)
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
                "unrequested_closes_by_initiator": dict(
                    self.closes_by_initiator),
                "last_unrequested_close": self.last_close,
                # the last CLIENT-close wait as it was recorded, and the
                # level of the last CLIENT close (the next one recounts the
                # connected window)
                "client_close_backoff": (
                    dict(self.client_close_backoff,
                         current_level=self.client_close_level)
                    if self.client_close_backoff else None),
                "lease_key": FEED_LOCK_KEY, "sport_ids": self.sport_ids,
                "streams": self.streams, "transitions": self.events[-10:],
                "cache": self.cache.census()}
