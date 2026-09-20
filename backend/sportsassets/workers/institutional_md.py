"""Worker: the PERSISTENT INSTITUTIONAL MARKET-DATA process.

Owner directive 2026-09-20 00:1xZ §5/§6: institutional market data →
a persistent BETTOR market-data process → an in-memory L2 book →
incremental features → the experimental model. "Do not put GitHub
Actions in this critical path."

WHAT THIS LOOP IS. It holds the production credential installed on this
service, bootstraps instrument metadata once per symbol, and then keeps
the current institutional book for a small focus set in memory. The
experimental loop reads that memory; it does not make a REST call to
decide.

THE MECHANISM IS NAMED HONESTLY. §6 says to use the venue's documented
persistent market-data mechanism WHERE VERIFIED. It is not verified:
the documentation spells the gRPC host four ways and none is confirmed
for production, and this lane has never opened one. So REST bootstraps
and REST maintains at a tight cadence, the mechanism is recorded as
REST_POLL_MAINTAINED_IN_MEMORY, and the stream target is recorded as
NOT_IDENTIFIED. Calling a poll a subscription would be a claim about
the venue that nobody here has checked.

FRESHNESS IS THE POINT OF A PERSISTENT PROCESS, and it is measured
rather than assumed: every book carries the venue's own transactTime,
this process's receive instant, and the lag between them. A book older
than the frozen limit is STALE, and the execution path refuses it.

NO ORDER PATH. `pmx_institutional` has an allow-list of exactly three
reads and no insert, cancel, replace, preview or funding path in its
source; a test asserts their absence. Even if the token carries
write:orders, ORDER_SUBMISSION_IMPLEMENTATION is NONE -- a scope the
venue granted is not a capability this process has.

Kill: INSTITUTIONAL_MD=off.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from .. import institutional_book as ib
from .. import pmx_institutional as pmx
from .. import shadow_experimental_store as xstore
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

SERVICE = "institutional_md"

# THE MAINTENANCE CADENCE. Fast enough that a book is CURRENT under the
# 5s freshness limit for most of its life, slow enough to stay far
# below the ~3 req/s the gateway 429'd at: eight symbols per sweep at
# 2s is about 4 reads/s at the worst... so the sweep is paced.
SWEEP_S = 2.0
READ_PACING_S = 0.15
REFDATA_REFRESH_S = 3600
BOOTSTRAP_BACKOFF_S = 30.0
AUTH_BACKOFF_S = 60.0

# How many instruments this process maintains. The focus set is chosen
# by the experimental lane; this loop only keeps what that lane is
# already sampling, so the two never drift apart.
MAX_INSTRUMENTS = 8

# HOW OFTEN A BOOK IS WRITTEN DOWN as evidence. The in-memory book
# updates every sweep; persisting every sweep would write thousands of
# rows an hour for books nothing decided on. A book that a decision
# actually walked is persisted by the decision path itself; this is the
# background trail, and it is deliberately sparse.
EVIDENCE_EVERY_S = 60.0


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


def _now():
    return datetime.now(tz=timezone.utc)


def bootstrap_instrument(client, symbol) -> dict:
    """Refdata for one symbol: identity and ITS OWN scales.

    Returns a named result either way. A symbol the institutional venue
    does not list is a real answer about coverage, not an error.
    """
    row = client.read("instruments", symbol)
    body = row.get("body") or {}
    rows = body.get("instruments") or []
    rec = rows[0] if rows and isinstance(rows[0], dict) else None
    ps, qs = pmx.scales_of(rec)
    return {"symbol": symbol, "status": row.get("status"),
            "record": rec, "priceScale": ps, "qtyScale": qs,
            "listed": rec is not None, "priceable": bool(ps and qs),
            "ms": row.get("ms"),
            "transportError": row.get("transportError")}


def sweep_once(client, store, symbols) -> dict:
    """One paced pass over the focus set. Runs OFF the event loop."""
    from ..venue_pace import pace

    stats = {"read": 0, "stored": 0, "failed": 0, "notListed": 0,
             "venueMs": []}
    for symbol in symbols[:MAX_INSTRUMENTS]:
        inst = store.instrument(symbol)
        if inst is None:
            pace(READ_PACING_S)
            boot = bootstrap_instrument(client, symbol)
            store.put_instrument(symbol, boot["record"],
                                 price_scale=boot["priceScale"],
                                 qty_scale=boot["qtyScale"])
            if not boot["priceable"]:
                stats["notListed"] += 1
                continue
            inst = store.instrument(symbol)

        if not inst.get("priceable"):
            stats["notListed"] += 1
            continue

        pace(READ_PACING_S)
        row = client.read("book", symbol)
        stats["read"] += 1
        if row.get("status") != 200 or not isinstance(row.get("body"), dict):
            stats["failed"] += 1
            continue
        stored = store.put_book(symbol, row["body"],
                                received_at=_now(),
                                venue_request_ms=row.get("ms"),
                                request_id=row.get("requestId"))
        if stored is not None:
            stats["stored"] += 1
            if row.get("ms") is not None:
                stats["venueMs"].append(row["ms"])
    return stats


async def persist_trail(pool, store, symbols) -> int:
    """The sparse background trail. Append-only, deduped by book sha.

    A book a DECISION walked is persisted by the decision path with its
    decision; this is the record that the process was reading the venue
    at all, and it is deliberately thin.
    """
    written = 0
    for symbol in symbols[:MAX_INSTRUMENTS]:
        row = store.current(symbol)
        if row.get("FRESHNESS_STATUS") == ib.ABSENT:
            continue
        try:
            if await xstore.record_direct_evidence(pool, row):
                written += 1
        except Exception:                                      # noqa: BLE001
            log.debug("institutional_md: trail write failed", exc_info=True)
    return written


async def run() -> None:
    if _off("INSTITUTIONAL_MD"):
        log.info("institutional_md: off by switch")
        return

    seen = pmx.presence()
    boot = {"service": SERVICE,
            "marketDataMechanism": pmx.MARKET_DATA_MECHANISM,
            "streamTarget": pmx.STREAM_TARGET,
            "evidenceEnvironment": pmx.EVIDENCE_ENVIRONMENT,
            "orderSubmissionImplementation":
                pmx.ORDER_SUBMISSION_IMPLEMENTATION,
            "freshnessLimitS": ib.FRESHNESS_LIMIT_S,
            "presence": seen}
    log.info("institutional_md: %s", boot)

    if seen["verdict"]:
        # PRESENCE ONLY. Which names are missing, never anything about
        # what the present ones contain.
        while True:
            await heartbeat(SERVICE, "credential_missing", boot)
            await asyncio.sleep(AUTH_BACKOFF_S)

    pool = await get_pool()
    client = pmx.Institutional()
    store = ib.STORE

    # §1/§3: prove the worker's own credential once, at boot, and write
    # the verdict where COMMAND and the logs can both read it.
    verdict = await asyncio.to_thread(pmx.verify, client, "")
    boot = dict(boot, auth=verdict)
    log.info("institutional_md: auth %s scopes=%s readL2=%s",
             verdict.get("AUTH_STATUS"), verdict.get("TOKEN_SCOPES"),
             verdict.get("READ_L2_PERMISSION"))
    await heartbeat(SERVICE, str(verdict.get("AUTH_STATUS")), boot)

    last_evidence = 0.0
    beats = 0
    while True:
        started = time.monotonic()
        stats = {"service": SERVICE}
        try:
            symbols = [s["symbol"] for s in await xstore.focus_set(
                pool, size=MAX_INSTRUMENTS)]
        except Exception as exc:                               # noqa: BLE001
            symbols = []
            stats["focusError"] = "%s: %s" % (type(exc).__name__, exc)

        if not symbols:
            stats["status"] = "no_focus_set"
        else:
            try:
                stats.update(await asyncio.to_thread(
                    sweep_once, client, store, symbols))
                stats["status"] = "ok"
            except Exception as exc:                           # noqa: BLE001
                stats["status"] = "sweep_failed"
                stats["sweepError"] = "%s: %s" % (type(exc).__name__, exc)
                log.warning("institutional_md: sweep failed", exc_info=True)

            if time.monotonic() - last_evidence >= EVIDENCE_EVERY_S:
                last_evidence = time.monotonic()
                try:
                    stats["trailRows"] = await persist_trail(
                        pool, store, symbols)
                except Exception:                              # noqa: BLE001
                    log.debug("institutional_md: trail failed", exc_info=True)

        venue_ms = stats.pop("venueMs", []) or []
        if venue_ms:
            ordered = sorted(venue_ms)
            stats["venueMsP50"] = ordered[len(ordered) // 2]
            stats["venueMsMax"] = ordered[-1]
        stats.update(store.snapshot())
        stats["symbols"] = len(symbols)
        stats["sweepS"] = round(time.monotonic() - started, 3)
        stats["authStatus"] = verdict.get("AUTH_STATUS")
        stats["marketDataMechanism"] = pmx.MARKET_DATA_MECHANISM
        stats["orderSubmissionImplementation"] = \
            pmx.ORDER_SUBMISSION_IMPLEMENTATION

        # THE HEARTBEAT IS NOT ON THE HOT PATH, so it is throttled:
        # §7 keeps reporting out of the decision path, and a beat every
        # sweep would be thirty writes a minute saying the same thing.
        beats += 1
        if beats % 15 == 1:
            try:
                await heartbeat(SERVICE, str(stats.get("status") or "ok"),
                                stats)
            except Exception as exc:                           # noqa: BLE001
                log.error("institutional_md: heartbeat failed: %s: %s",
                          type(exc).__name__, exc)
        await asyncio.sleep(
            BOOTSTRAP_BACKOFF_S if stats.get("status") in (
                "no_focus_set", "sweep_failed") else SWEEP_S)
