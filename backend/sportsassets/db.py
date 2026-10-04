"""asyncpg pool helpers. All services share this thin layer — no ORM."""

import asyncio
import logging

import asyncpg

from .config import settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# THE CONNECT IS SINGLE-FLIGHT (2026-09-05, the workers dying during the
# full-disk outage of 2026-09-04/05). get_pool() used to be
# check-then-set with the whole connect between the check and the set:
# every caller that found _pool None built its OWN asyncpg pool, and
# the last one to finish overwrote the global. The losers' pools --
# min_size=1, max_size=10 each -- were never closed, and workers/all.py
# boots by gathering _record_boot plus eighteen supervised loops at
# once, which is exactly that race on every boot and again on every
# 5-second restart cycle while the database refused connections. One
# lock, one create_pool in flight; everyone else waits on it and
# re-reads _pool once it is theirs -- a refusing database sees at most
# one connect at a time instead of nineteen, and the moment it is back
# the holder succeeds and every waiter gets that pool on its re-check.
#
# WAITERS SHARE THE LEADER'S VERDICT, NOT JUST ITS POOL (2026-09-05,
# the same day, from the review of the lock above). The first cut held
# the lock across the whole 10-attempt walk (1+2+4+8+15x6 = ~105 s) and
# a waiter that acquired it after the leader FAILED walked its OWN ~105
# s backoff: waiters shared a landing but not a failure, so under a
# dead database the queue behind the lock drained one caller per ~105
# s. In the API process every DB-backed request handler joins that
# queue -- 36-128 KB apiece, measured -- and hours of ordinary traffic
# against a dead database were hours of handlers piling up in memory
# waiting for a verdict already reached. _walk_gen is the walk counter:
# a caller notes it before queueing on the lock, and if it has moved by
# the time the lock is his, a walk ended while he waited and did not
# land (a landing would be in _pool), so that verdict is his too and he
# raises at once instead of walking again. A caller who arrives AFTER
# the failure reads the new count and becomes the next leader -- one
# walk per generation of arrivals, never one per caller. The cost of
# stamping nothing on a cancel (the else branch in get_pool says why):
# a leader cancelled mid-walk hands the walk to its first waiter --
# one walk per cancellation, never one per waiter.
#
# The lock is built lazily PER EVENT LOOP, never at module scope: an
# asyncio.Lock binds itself to the first loop that contends on it and
# raises "is bound to a different event loop" from the next one.
# asyncio.run() makes a fresh loop for every test, and a worker
# restarted through asyncio.run would too. The walk counter is plain
# module state: it is only ever compared, never awaited on.
_connect_lock: asyncio.Lock | None = None
_connect_lock_loop: asyncio.AbstractEventLoop | None = None
_walk_gen = 0

# The heartbeat write's ceiling (2026-09-05), on BOTH legs of the
# write: the acquire and the statement. Poller.run()'s beat is
# "logged when it cannot be written, never a raise" -- but a raise is
# not the only way a beat ends a loop: an unbounded write blocks for
# as long as the database takes, and against a database that ACCEPTS
# a connection and then stalls (the full disk of 2026-09-04/05 did
# exactly that before it started refusing outright) the beat would
# hold run() forever with nothing to log. The first cut passed this
# to pool.execute(timeout=), which bounds only the STATEMENT:
# asyncpg's Pool.execute is `async with self.acquire(): con.execute(
# ..., timeout=)` with no acquire timeout, so against a SATURATED
# pool -- size == max, idle == 0, the shape /healthz read at
# 2026-09-05 01:06Z, and what a stalled database turns a pool into
# once its statements pile up -- the beat blocked in acquire forever:
# no TimeoutError, no 'not written' line, run() stopped pacing (the
# containment re-review of the same day, reproduced on asyncpg 0.31
# against a saturated pool). So the ceiling now goes to
# pool.acquire(timeout=) AND to the connection's execute(timeout=);
# asyncpg raises TimeoutError at either, an Exception, so the
# caller's containment sees it like any other failed write. The third
# leg, honestly: a statement cut off at its ceiling is CANCELLED, and
# releasing the connection waits for the server to acknowledge that
# cancel. asyncpg reuses the acquire timeout as that release budget
# (Pool.release: "defaults to the timeout provided in the
# corresponding call to acquire") and terminates the connection when
# the budget runs out, so a cancel the server never acknowledges
# costs a third ceiling, not forever -- under pool.execute there was
# no acquire timeout to reuse and that wait was unbounded. Worst case
# for one beat is three ceilings, ~30 s, and it ENDS, in a
# TimeoutError the caller logs. Ten seconds is generous for a one-row
# upsert and short next to every poll pace that calls it.
HEARTBEAT_TIMEOUT_S = 10.0


def _dsn() -> str:
    # asyncpg accepts postgresql:// DSNs directly.
    return settings().database_url


def _lock() -> asyncio.Lock:
    """The connect lock for the RUNNING loop, built on first use."""
    global _connect_lock, _connect_lock_loop
    loop = asyncio.get_running_loop()
    if _connect_lock is None or _connect_lock_loop is not loop:
        _connect_lock = asyncio.Lock()
        _connect_lock_loop = loop
    return _connect_lock


async def get_pool() -> asyncpg.Pool:
    global _pool, _walk_gen
    if _pool is not None:
        return _pool
    # the walk we may end up queued behind (see _walk_gen above)
    gen = _walk_gen
    async with _lock():
        if _pool is not None:
            # the flight ahead of us landed while we waited
            return _pool
        if _walk_gen != gen:
            # a walk ended while we waited and it did not land: that
            # verdict is ours. Raising here, not walking again, is what
            # drains the queue behind a dead database at once instead
            # of one caller per ~105 s.
            raise RuntimeError("could not connect to Postgres")
        for attempt in range(10):
            try:
                pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=10)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                wait = min(2**attempt, 15)
                log.warning("DB connect failed (%s); retry in %ss", exc, wait)
                await asyncio.sleep(wait)
        else:
            # every attempt failed: _pool stays None, the lock is
            # released on the way out, and the walk is stamped so that
            # everyone queued behind it inherits this verdict. Stamped
            # HERE and not in a finally on purpose: a leader cancelled
            # mid-walk (a shutdown, or a caller's own ceiling -- the
            # 2026-09-03 /healthz did exactly that to get_pool) learned
            # nothing about the database, and its waiters must walk,
            # not inherit a failure nobody observed.
            _walk_gen += 1
            raise RuntimeError("could not connect to Postgres")
        _pool = pool
        return pool


def pool_stats() -> dict | None:
    """The live pool's own counters -- {"size", "idle", "max", "min"} --
    or None when this process has no pool.

    Synchronous on purpose (2026-09-05, after the night /healthz was
    read in anger): {"ok": true, "db_ok": false} on its own cannot tell
    a saturated pool from a slow database from a full disk, and each is
    fixed by a different hand. asyncpg's get_size/get_idle_size/
    get_max_size/get_min_size read numbers the pool already holds -- no
    acquire, no await, nothing a saturated pool can make wait -- so a
    probe that prints this can never hang the way the old SELECT 1 did.
    Informational, and it FAILS CLOSED TO NONE: counters that cannot be
    read (a pool mid-close, a stand-in without them) are None, never a
    raise out of the one check the platform restarts on. How to read
    it: size == max with idle == 0 is a saturated pool; idle > 0 next
    to db_ok false is the database itself being slow or dead."""
    pool = _pool
    if pool is None:
        return None
    try:
        return {"size": int(pool.get_size()),
                "idle": int(pool.get_idle_size()),
                "max": int(pool.get_max_size()),
                "min": int(pool.get_min_size())}
    except Exception:  # noqa: BLE001 -- informational; closed to None
        return None


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ── THE SINGLE-WRITER LOOPS' OWN SESSIONS (R30A, 2026-10-04) ────────────
#
# WHAT PRODUCTION SHOWED. research/r30a_runtime_evidence.sql (research-sql
# run 37226381750, 18:57Z) read pg_stat_activity + pg_locks for the API host:
# ten pooled backends -- the pool's max_size, every slot open -- and SIX of
# them were the session advisory locks of the single-writer loops, each
# connected since boot (5,989 s): execmirror 0x45584d31, the desk loop ...031,
# rn1x_shadow ...032, rn1x_learn ...033, ext_pinnacle ...034, rn1x_model ...035.
# A session lock must live on one session for the loop's whole life, so each
# of those loops did `async with pool.acquire()` once and never gave the slot
# back. Four slots were left for every HTTP request, the reactive evaluation
# and its audit, the feed heartbeat, the servicing pass, the research tick and
# the agents' runners. The API log of the same afternoon (render-ops logs,
# 15:47-18:47Z, runs 37225745383 / 37225748641 / 37225751870): `pinnapi
# reactive audit failed` 12 times -- 10 with the TimeoutError inside asyncpg
# Pool._acquire (waiting for a slot), 2 (16:00:53, 17:46:10) inside the
# INSERT itself, after a slot was acquired; `agent research tick failed:
# TimeoutError` 17 times (no traceback is logged for those); `pinnapi feed
# heartbeat failed` 8 times.
#
# THE POOL WAS ONE OF TWO CAUSES. The other: the API event loop itself was
# held 2-4 s at a time (the loop watchdog's ~60 recorded stalls, clustered in
# exactly the minutes of all three classes; render-ops run 37231102537), and
# a held loop expires every 2-3 s budget in the process -- it is the only
# explanation for the two failures INSIDE the INSERT, whose server-side
# execution never exceeded 12 ms (pg_stat_statements since 2026-10-01:
# 4,139 calls, mean 0.17 ms; research-sql run 37231484481). The stalls are
# fixed at their sources (pinnapi_feed.EventIndexedQuotes, the held refresh,
# the census; see tests/test_r30a_loop_stalls.py). This block fixes the
# pool half.
#
# THE FIX IS NOT A BIGGER POOL OR A LONGER TIMEOUT. A lock holder gets a
# connection of its own, opened here, outside the shared pool: the pool's ten
# slots go back to transient work, and the holders cost the same six backends
# they already cost (the database allows 403; 31 were in use). TCP keepalives
# make the server notice a partitioned holder within ~25 s and free its lock,
# exactly as pinnapi_owner.Lease does for the feed. No statement_timeout is
# set: the holders' own statements (an entry cycle, an outcome join) carry
# their own bounds.
#
# ONLY THE PROCESS'S OWN POOL IS BYPASSED. `pool is _pool` is the test: a pool
# this module built from DATABASE_URL. Anything else -- a test double, a pool
# a caller built itself -- keeps its own `acquire()`, so every caller that
# injects a pool keeps working unchanged.
LEASE_CONNECT_TIMEOUT_S = 10.0
LEASE_CLOSE_TIMEOUT_S = 5.0
LEASE_SERVER_SETTINGS = {"tcp_keepalives_idle": "10",
                         "tcp_keepalives_interval": "5",
                         "tcp_keepalives_count": "3"}


class lease_session:                                       # noqa: N801
    """`async with lease_session(pool, name="ext_pinnacle") as conn:` -- ONE
    session for a single-writer loop's whole life (see above)."""

    def __init__(self, pool, *, name: str):
        self.pool, self.name = pool, str(name)
        self._cm = None
        self._conn = None
        self.dedicated = False

    async def __aenter__(self):
        if self.pool is None or self.pool is not _pool:
            self._cm = self.pool.acquire()
            return await self._cm.__aenter__()
        self.dedicated = True
        self._conn = await asyncpg.connect(
            _dsn(), timeout=LEASE_CONNECT_TIMEOUT_S,
            server_settings=dict(LEASE_SERVER_SETTINGS, application_name=(
                "sportsassets-lease:" + self.name)[:63]))
        return self._conn

    async def __aexit__(self, *exc):
        if self._cm is not None:
            return await self._cm.__aexit__(*exc)
        conn, self._conn = self._conn, None
        if conn is None:
            return False
        try:
            # closing the session releases every session lock it held
            await asyncio.wait_for(conn.close(), LEASE_CLOSE_TIMEOUT_S)
        except asyncio.CancelledError:
            conn.terminate()        # the socket goes now; the server frees
            raise                   # the locks when it sees the close
        except Exception:                                  # noqa: BLE001
            conn.terminate()
        return False


def advisory_key_parts(key: int) -> tuple[int, int]:
    """pg_locks spells a bigint advisory key as (classid, objid) = (high 32
    bits, low 32 bits) with objsubid 1."""
    k = int(key) & 0xFFFFFFFFFFFFFFFF
    return (k >> 32) & 0xFFFFFFFF, k & 0xFFFFFFFF


ADVISORY_HELD_SQL = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
    "   AND granted AND objsubid = 1 AND pid = pg_backend_pid() "
    "   AND classid = $1::bigint::oid AND objid = $2::bigint::oid)")


ADVISORY_HELD_BY_SQL = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
    "   AND granted AND objsubid = 1 AND pid = $3 "
    "   AND classid = $1::bigint::oid AND objid = $2::bigint::oid)")


async def advisory_held_by(conn, key: int, pid: int) -> bool:
    """CHILD FENCING: is the session advisory lock `key` still granted to
    the writer's backend `pid`? Asked on ANY connection (a child's own, from
    the pool) -- the writer's session itself is busy with the writer's own
    work and cannot be shared. The pid is the writer's fencing token: a
    re-contended lock belongs to a new backend, so a child of the old hold
    reads False. Raises when the connection cannot answer."""
    hi, lo = advisory_key_parts(key)
    return await conn.fetchval(ADVISORY_HELD_BY_SQL, hi, lo, int(pid)) is True


async def advisory_held(conn, key: int) -> bool:
    """FENCING: does THIS session still hold the session advisory lock `key`?

    A single-writer loop asks before each cycle: a session that died, or
    one that no longer holds its key, must stop writing and contend again
    -- not loop forever on a dead connection while the children it started
    write without the lock. Raises when the session cannot answer (a dead
    connection is not a held lock); the caller treats a raise as lost, and
    bounds the wait itself (asyncio.timeout)."""
    hi, lo = advisory_key_parts(key)
    return await conn.fetchval(ADVISORY_HELD_SQL, hi, lo) is True


def _heartbeat_default(o):
    """What JSON cannot carry, as text: a datetime / date as ISO-8601
    (bettor_state's tick stats carry its observation `bucket` as a
    datetime), anything else as its str. Never raises."""
    import datetime as _dt
    if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
        return o.isoformat()
    return str(o)


def heartbeat_json(detail) -> str:
    """THE ONE SERIALIZATION OF A HEARTBEAT DETAIL. A bare json.dumps raised
    `TypeError: Object of type datetime is not JSON serializable` on every
    bettor_state tick, so that loop's heartbeat (and the accounting it
    carries) never reached the database."""
    import json
    return json.dumps(detail or {}, default=_heartbeat_default)


async def heartbeat(service: str, status: str = "ok", detail: dict | None = None,
                    con=None) -> None:
    """Record a service heartbeat (used by health checks and admin dashboard).

    `con` LETS A CALLER THAT ALREADY HOLDS A CONNECTION SKIP THE ACQUIRE.

    THE FAILURE THIS IS FOR (2026-09-24, acceptance run 27). The four rn1x
    loops each hold ONE session-scoped connection for their whole life --
    they must, because the advisory lock that makes them the single writer
    dies with the session -- out of a pool whose max_size is 10. Six slots
    are left for request traffic AND for this function. Under a desk sweep
    the pool saturates, this acquire hits HEARTBEAT_TIMEOUT_S, and the
    cycle summary is lost:

        rn1x_shadow.py:783 in run -> heartbeat(SERVICE, ...)
        db.py:191 in heartbeat -> pool.acquire(timeout=HEARTBEAT_TIMEOUT_S)
        TimeoutError

    Seven of those in three hours, six of them BEFORE the release that
    exposed it, so this is chronic and not new. The cost is not only a
    missing heartbeat: `flow` -- the fetched/deferred/refused/written
    reconciliation -- travels in that record, so a starved pool erases the
    accounting and the lane reads as though it examined nothing.

    `ext_pinnacle_loop` and `rn1x_model_loop` already write their
    heartbeats on the connection they hold. This gives the shared helper
    the same option, so the loop that has a connection does not queue for
    a second one.

    The detail is serialized by `heartbeat_json` (datetimes as ISO-8601).
    """
    if con is not None:
        await con.execute(
            """
            INSERT INTO service_heartbeats (service, status, detail, beat_at)
            VALUES ($1, $2, $3::jsonb, now())
            ON CONFLICT (service) DO UPDATE
                SET status = EXCLUDED.status, detail = EXCLUDED.detail,
                    beat_at = now()
            """,
            service, status, heartbeat_json(detail),
            timeout=HEARTBEAT_TIMEOUT_S)
        return

    pool = await get_pool()
    # bounded on both legs (see HEARTBEAT_TIMEOUT_S): the acquire, so a
    # saturated pool ends this with a TimeoutError instead of holding
    # it in the queue, and the statement, so a stalled database does
    # the same. Never pool.execute(timeout=): that bounds only the
    # statement and sat in acquire forever on 2026-09-05.
    async with pool.acquire(timeout=HEARTBEAT_TIMEOUT_S) as con:
        await con.execute(
            """
            INSERT INTO service_heartbeats (service, status, detail, beat_at)
            VALUES ($1, $2, $3::jsonb, now())
            ON CONFLICT (service) DO UPDATE
                SET status = EXCLUDED.status, detail = EXCLUDED.detail, beat_at = now()
            """,
            service,
            status,
            heartbeat_json(detail),
            timeout=HEARTBEAT_TIMEOUT_S,
        )
