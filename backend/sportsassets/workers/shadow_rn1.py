"""Worker: STEP 2 of the prospective RN1_SHADOW ledger.

Sightings are written on the ingestion path, at arrival, with no venue
call. This loop picks them up, gets the book BETTOR would have had,
and writes the DECISION.

MEASUREMENT ONLY. SHADOW_MODE is true, REAL_ORDER_SUBMISSION is
disabled, CAPITAL_AT_RISK is 0 and mirror_live stays false. This worker
never places, cancels or touches an order, and it has no code path that
could: the only writes it makes are inserts into the shadow tables.

THE PREFLIGHT RUNS BEFORE THE FIRST ROW, EVERY BOOT. store_ready()
checks the append-only triggers, the lane column being NOT NULL with no
default, the foreign key that makes an unfrozen policy unwritable, the
RN1 idempotency index and the shadow_mode/capital_at_risk constraint --
against the live catalog, not against intent. A boot that cannot verify
them writes no decisions and beats the reason, so the blocker is
visible instead of the ledger being quietly empty.

VENUE LOAD IS BOUNDED, because this shares a process and a gateway with
the money path and the venue 429'd a board walk above ~3 req/s. One
paced quote read per observation, at most MAX_READS_PER_TICK of them,
and a tick that cannot read abandons and backs off rather than
retrying into the limit.

AN OBSERVATION THAT AGES OUT NEVER GETS A DECISION. A decision written
twenty minutes after the sighting is not prospective in any sense that
matters, and back-filling one would put a stale book in a row claiming
to be a live one. The gap stays a gap -- which is the truthful record
of a tick that did not keep up.

Kill: SHADOW_RN1_DECIDE=off.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from .. import pmus
from .. import shadow_policy as pol
from .. import shadow_rn1
from .. import shadow_store as store
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

TICK_S = 5.0
BACKOFF_S = 60.0
READ_PACING_S = 0.35
MAX_READS_PER_TICK = 12
# How stale a sighting may be and still get a decision.
DECISION_WINDOW_S = 300
# A run of unreadable books abandons the tick: the venue is having a
# bad minute and hammering it is how the mirror earned its 429s.
MISS_ABANDON = 3

EVIDENCE_SOURCE = "PMUS_BBO"

# named reasons a book could not be had, so COMMAND shows a blocker
# rather than an empty panel
W_NO_US_MARKET = "US_MARKET_NOT_RESOLVED"
W_NO_QUOTE = "VENUE_RETURNED_NO_QUOTE"
W_VENUE_STATE = "VENUE_MARKET_STATE_%s"
W_READ_FAILED = "QUOTE_READ_FAILED_%s"


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


# ── the cheapest honest mapping ──────────────────────────────────────
#
# RN1's fills carry a condition_id and a token. The venue's book is
# keyed by a US slug. The full resolver is a subsystem of its own, and
# calling it here would both duplicate it and spend venue budget the
# money path needs -- so this reads the mapping the mirror has ALREADY
# established and persisted for that market. Where the mirror has never
# opened a book, the market is recorded as unresolved BY NAME rather
# than guessed at: an honest gap in the ledger is evidence; an invented
# slug is a wrong book on a row that claims to be prospective.

_SLUG_SQL = """
    SELECT us_market_slug, long_asset
      FROM mirror_books
     WHERE condition_id = $1
     ORDER BY opened_at DESC
     LIMIT 1
"""


async def _us_slug(pool, market_id: str | None):
    if not market_id:
        return None, None
    row = await pool.fetchrow(_SLUG_SQL, market_id)
    if row is None:
        return None, None
    return row["us_market_slug"], row["long_asset"]


def _read_quote(slug: str) -> dict:
    """One paced quote read, off the event loop. Never raises."""
    pace(READ_PACING_S)
    try:
        return pmus.bbo_read(pmus._get_client(), slug)
    except Exception as exc:                                   # noqa: BLE001
        return {"bid": None, "ask": None, "state": None,
                "error": type(exc).__name__}


def _market_state(observation: dict, slug, quote, captured_at) -> dict:
    """Turn a quote read into the market state row, UNREADABLE included.

    An unreadable book is a state with a name, never a bid of zero. The
    venue was halted venue-wide for five hours on 2026-09-05 and
    answered 200 with null quotes on every market; a ledger that booked
    those as zeros would have priced five hours of decisions against a
    market that was not trading.
    """
    symbol = slug or observation.get("symbol")
    common = dict(captured_at=captured_at, symbol=symbol or "UNRESOLVED",
                  outcome_leg=observation.get("outcomeLeg"),
                  evidence_source=EVIDENCE_SOURCE,
                  source_interval_s=TICK_S)
    if slug is None:
        return store.market_state_record(
            readable=False, why_unreadable=W_NO_US_MARKET, **common)
    if quote.get("error"):
        return store.market_state_record(
            readable=False,
            why_unreadable=W_READ_FAILED % quote["error"], **common)
    if quote.get("bid") is None and quote.get("ask") is None:
        state = quote.get("state")
        return store.market_state_record(
            readable=False,
            why_unreadable=(W_VENUE_STATE % state) if state else W_NO_QUOTE,
            **common)
    return store.market_state_record(
        readable=True, bid=quote.get("bid"), ask=quote.get("ask"),
        # DEPTH IS NOT ESTABLISHED FROM A BBO, and it is not pretended
        # to be. The frozen sizing policy refuses a size it cannot cap
        # against observed depth, so these decisions record
        # OBSERVED_DEPTH_NOT_ESTABLISHED until an L2 feed exists --
        # which is the honest state of the evidence today, written down
        # rather than papered over with the top-of-book quantity.
        available_depth=None,
        l2_reference={"feed": "bbo", "depth": "NOT_IDENTIFIED",
                      "venueState": quote.get("state")},
        **common)


async def tick(pool) -> dict:
    """One pass. Returns the census the heartbeat carries."""
    stats = {"pending": 0, "decided": 0, "reads": 0, "misses": 0,
             "blocked": 0, "status": "ok"}
    pending = await shadow_rn1.pending_observations(
        pool, within_s=DECISION_WINDOW_S, limit=MAX_READS_PER_TICK)
    stats["pending"] = len(pending)
    misses = 0
    for observation in pending:
        slug, _long_asset = await _us_slug(pool, observation.get("marketId"))
        quote = {"bid": None, "ask": None, "state": None, "error": None}
        if slug is not None:
            quote = await asyncio.to_thread(_read_quote, slug)
            stats["reads"] += 1
        captured_at = datetime.now(tz=timezone.utc)
        state = _market_state(observation, slug, quote, captured_at)
        if not state["readable"]:
            misses += 1
            stats["misses"] += 1
        _decision_id, was_new = await shadow_rn1.write_decision(
            observation, state, pool=pool)
        if was_new:
            stats["decided"] += 1
        if not state["readable"]:
            stats["blocked"] += 1
        if misses >= MISS_ABANDON:
            stats["status"] = "venue_unreadable"
            break
    return stats


async def run() -> None:
    if _off("SHADOW_RN1_DECIDE"):
        log.info("shadow_rn1: decision loop off by switch")
        return
    pool = await get_pool()

    ready = await store.store_ready(pool)
    frozen = None
    if ready["storeReady"]:
        frozen = await store.freeze_policy(pool)
        if frozen["status"] == "REFUSED":
            ready = dict(ready, storeReady=False,
                         problems=ready["problems"] + [frozen["why"]])
    boot = {"storeReady": ready["storeReady"], "problems": ready["problems"],
            "policy": pol.RN1_SHADOW_POLICY_VERSION,
            "policyFreeze": (frozen or {}).get("status", "NOT_ATTEMPTED"),
            "shadowMode": ready["shadowMode"],
            "capitalAtRisk": ready["capitalAtRisk"],
            "disclosure": ready["disclosure"]}
    log.info("shadow_rn1: %s", boot)

    if not ready["storeReady"]:
        # NO ROWS UNTIL THE GUARANTEES ARE REAL. The blocker is beaten
        # on a slow cadence so an operator sees WHY the ledger is empty
        # instead of an empty ledger with no explanation.
        while True:
            await heartbeat("shadow_rn1", "store_not_ready", boot)
            await asyncio.sleep(BACKOFF_S)

    while True:
        started = time.monotonic()
        try:
            stats = await tick(pool)
        except Exception:                                      # noqa: BLE001
            log.warning("shadow_rn1: tick failed", exc_info=True)
            stats = {"status": "tick_failed"}
        stats.update(boot)
        stats["tickS"] = round(time.monotonic() - started, 3)
        try:
            await heartbeat("shadow_rn1", str(stats.get("status") or "ok"),
                            stats)
        except Exception:                                      # noqa: BLE001
            log.debug("shadow_rn1: heartbeat failed")
        await asyncio.sleep(
            BACKOFF_S if stats.get("status") == "venue_unreadable"
            else TICK_S)
