"""THE PINNAPI FEED IN THE DECIDING PROCESS (C1: observe only).

Started by ext_pinnacle_loop.run ONLY after it holds its writer lock
(7723901544120034), with that connection's backend pid, so the feed exists
only beside the process that decides; the owner re-checks that pid still
holds the writer lock every liveness pass and stops for good if it does not.

ARMING (fail-closed): the ingestion_state row 'pinnapi_feed' must read
exactly true, re-read every liveness pass -- absent, unreadable or anything
else disarms (authority revoked, socket closed). Disarmed, the owner takes no
lease and opens no socket; it only re-reads the row. Env PINNAPI_FEED in
{off,0,false,no} is a kill switch that keeps the module from starting at all.
(It is a kill switch, not an arm: changing a Render env var redeploys the
service, and arming must not need a deploy.)

SCOPE: ingestion_state 'pinnapi_feed_scope' = {"sport_ids": [...],
"streams": [...]}; absent -> baseball only ([6], live + prematch). The soccer
prematch firehose was 1,291 events in one snapshot (bounded capture
2026-10-01), so wider scopes are an explicit choice.

C1 CHANGES NO DECISION: nothing here is read by Derek or Xavier yet. It
publishes a bounded heartbeat ('pinnapi_feed_last', overwritten, capped)
with the owner state, the census of the provider's current state by sport /
market type / phase, and the provider-stamp->receipt distribution.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

from . import pinnapi_feed as F
from . import pinnapi_owner as O
from . import pinnapi_probe as PP

log = logging.getLogger(__name__)

CONTROL_KEY = "pinnapi_feed"
SCOPE_KEY = "pinnapi_feed_scope"
HEARTBEAT_KEY = "pinnapi_feed_last"
HEARTBEAT_S = 30.0
HEARTBEAT_MAX_BYTES = 65536
ROW_READ_TIMEOUT_S = 5.0
DEFAULT_SCOPE = {"sport_ids": [6], "streams": ["live", "prematch"]}
ALLOWED_SPORTS = set(range(1, 13))

_STATE: dict = {"owner": None, "task": None, "beat": None, "pool": None,
                "census": None}
CENSUS_S = 60.0


def enabled() -> bool:
    """False only for an explicit kill switch; arming is the control row."""
    return (os.environ.get("PINNAPI_FEED") or "").strip().lower() not in (
        "off", "0", "false", "no")


async def _read_row(pool, key):
    # Scope is read during start_default, before the collector can continue.
    # Bound BOTH shared-pool acquisition and the query, including while the
    # control row is absent/off. Dedicated lease settings do not cover this.
    async with asyncio.timeout(ROW_READ_TIMEOUT_S):
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT value FROM ingestion_state WHERE key = $1", key)


def _jsonish(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                       # noqa: BLE001
            return v
    return v


async def armed(pool) -> bool:
    """True only for an explicit true; anything else (absent, unreadable,
    malformed) is disarmed."""
    try:
        return _jsonish(await _read_row(pool, CONTROL_KEY)) is True
    except Exception:                                           # noqa: BLE001
        return False


async def scope(pool) -> dict:
    try:
        v = _jsonish(await _read_row(pool, SCOPE_KEY))
    except Exception:                                           # noqa: BLE001
        v = None
    if not isinstance(v, dict):
        return dict(DEFAULT_SCOPE)
    sports = [int(x) for x in (v.get("sport_ids") or [])
              if str(x).isdigit() and int(x) in ALLOWED_SPORTS][:4]
    streams = [x for x in (v.get("streams") or []) if x in ("live",
                                                            "prematch")]
    return {"sport_ids": sports or DEFAULT_SCOPE["sport_ids"],
            "streams": streams or DEFAULT_SCOPE["streams"]}


def digest() -> dict:
    o = _STATE.get("owner")
    if o is None:
        return {"state": "NOT_STARTED", "enabled_env": enabled()}
    d = o.status()
    d["enabled_env"] = enabled()
    d["coverage_census"] = _STATE.get("census")
    d["c1_decision_effect"] = "NONE (observe only)"
    return d


def _capped(d: dict) -> str:
    s = json.dumps(d, default=str)
    if len(s) <= HEARTBEAT_MAX_BYTES:
        return s
    d = dict(d)
    cache = dict(d.get("cache") or {})
    cache["markets_by_sport_type_phase"] = "TRUNCATED_FOR_SIZE"
    d["cache"] = cache
    cen = dict(d.get("coverage_census") or {})
    if cen:
        cen["by_sport_family_phase_state"] = "TRUNCATED_FOR_SIZE"
        d["coverage_census"] = cen
    d["heartbeat_truncated"] = True
    s = json.dumps(d, default=str)
    if len(s) <= HEARTBEAT_MAX_BYTES:
        return s
    # Truncating serialized JSON can make the Postgres jsonb write fail.
    # Keep a complete, bounded envelope if another field is too large.
    # Default ensure_ascii=True also makes this a byte-size bound.
    return json.dumps({
        "state": str(d.get("state", "UNKNOWN"))[:128],
        "enabled_env": d.get("enabled_env") is True,
        "heartbeat_truncated": True,
        "reason": "HEARTBEAT_EXCEEDED_SIZE_CAP",
        "c1_decision_effect": "NONE (observe only)",
    })


async def _census_once(pool) -> dict:
    """The venue catalogue against the feed's current events: every contract
    one named state, reconciled to the catalogue total."""
    from . import pinnapi_census as C
    o = _STATE.get("owner")
    t0 = time.time()
    subscribed = set(o.sport_ids if o else [])
    async with pool.acquire() as c:
        rows = [dict(r) for r in await c.fetch(
            C.catalogue_sql(sport_ids=subscribed))]
        others = [(r["sports_type"], r["n"]) for r in await c.fetch(
            C.catalogue_totals_sql())]
    view = C.feed_event_view(o.cache) if o else {}
    out = C.census(rows, view, subscribed_sports=subscribed,
                   synced=bool(o and o.cache.authority.synced), now=t0,
                   others=others)
    out["computed_at"] = t0
    out["took_ms"] = round((time.time() - t0) * 1000)
    return out


async def _beat_loop(pool):
    last_census = 0.0
    while True:
        o = _STATE.get("owner")
        synced = bool(o and o.cache.authority.synced)
        if not synced:
            # nothing to match against: no catalogue read while unsynced
            _STATE["census"] = {"skipped": "FEED_NOT_SYNCED"}
            last_census = 0.0
        elif time.monotonic() - last_census >= CENSUS_S:
            last_census = time.monotonic()
            try:
                _STATE["census"] = await _census_once(pool)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                _STATE["census"] = {"error": type(exc).__name__,
                                    "detail": str(exc)[:200]}
        try:
            async with pool.acquire() as c:
                await c.execute(
                    "INSERT INTO ingestion_state (key, value) VALUES "
                    "($1, $2::jsonb) ON CONFLICT (key) DO UPDATE SET "
                    "value = EXCLUDED.value",
                    HEARTBEAT_KEY, _capped(dict(digest(),
                                                beat_at=time.time())))
        except asyncio.CancelledError:
            raise
        except Exception:                                       # noqa: BLE001
            log.warning("pinnapi feed heartbeat failed", exc_info=True)
        await asyncio.sleep(HEARTBEAT_S)


async def start_default(pool, *, writer_pid: int, writer_lock_key: int,
                        lease_factory=None, connect=None) -> dict:
    """Never raises. Returns what it did and why."""
    try:
        if not enabled():
            return {"state": "KILLED_BY_ENV_PINNAPI_FEED_OFF"}
        if _STATE.get("task") is not None:
            return {"state": "ALREADY_STARTED"}
        sc = await scope(pool)
        from . import db

        async def lf():
            return await O.Lease.open(db._dsn())
        owner = O.FeedOwner(
            F.FeedCache(), sport_ids=sc["sport_ids"], streams=sc["streams"],
            lease_factory=lease_factory or lf,
            connect=connect or PP._ws_connect,
            writer_pid=writer_pid, writer_key=writer_lock_key,
            armed=lambda: armed(pool))
        loop = asyncio.get_running_loop()
        _STATE.update(owner=owner, pool=pool,
                      task=loop.create_task(owner.run()),
                      beat=loop.create_task(_beat_loop(pool)))
        return {"state": "STARTED", "scope": sc}
    except Exception as exc:                                    # noqa: BLE001
        log.warning("pinnapi feed start failed", exc_info=True)
        return {"state": "START_FAILED", "error": type(exc).__name__}


async def shutdown_default(wait_s: float = 8.0) -> dict:
    """Stop -> (owner revokes, closes socket, releases lease) -> bounded."""
    o, t, b = _STATE.get("owner"), _STATE.get("task"), _STATE.get("beat")
    if o is None:
        return {"verdict": "NEVER_STARTED"}
    o.stop()
    verdict = "CLOSED"
    try:
        if t is not None:
            await asyncio.wait_for(asyncio.shield(t), wait_s)
    except asyncio.TimeoutError:
        verdict = "INCOMPLETE_CLOSE_TIMEOUT"
        t.cancel()
    except Exception:                                           # noqa: BLE001
        verdict = "CLOSED_WITH_ERROR"
    if b is not None:
        b.cancel()
    o.cache.lost(O.R_STOPPED)
    _STATE.update(owner=None, task=None, beat=None)
    return {"verdict": verdict}


def read(event_id, key, **kw) -> dict:
    """The one accessor (unused by decisions in C1)."""
    o = _STATE.get("owner")
    if o is None:
        return {"ok": False, "reason": F.R_NO_AUTHORITY}
    return o.cache.read(event_id, key, **kw)
