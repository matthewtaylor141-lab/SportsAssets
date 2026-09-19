"""Worker: BETTOR_EV_SHADOW's own prospective collection. THE PRIMARY LANE.

Owner clarification 2026-09-19: "BETTOR EV ENGINE IS THE PRIMARY
PRODUCT." / "BETTOR_EV_SHADOW does NOT need P_BETTOR to begin
accumulating prospective evidence." / "Do not wait for RN1 to generate a
BETTOR observation."

THIS LOOP DOES NOT DEPEND ON RN1 IN ANY WAY. It does not read his
fills, his positions or his lane's tables, and it keeps collecting
while RN1 is idle -- or, as on 2026-09-19, while fill detection itself
has stopped. That independence is the point of the hierarchy: the
primary product must not be a passenger of the benchmark.

MEASUREMENT ONLY. SHADOW_MODE true, REAL_ORDER_SUBMISSION disabled,
CAPITAL_AT_RISK 0. No order path exists in the import graph and a test
fails the build if one appears.

WHAT IT WRITES, EVERY CYCLE, FOR EACH MARKET IT LOOKS AT:

  an OPPORTUNITY  -- the market state BETTOR actually had, the features
                     it could honestly compute, the selection rule that
                     put this market in front of it, and the evidence
                     source, all recorded before any outcome exists;
  a DECISION      -- NO_TRADE today, with every blocker named, because
                     no independently validated Action EV exists yet.

THE REFUSALS ARE THE DATASET. "Management should eventually see which
blockers prevent the most trades and whether those refusals saved
money." That question is only answerable if the refusals were written
down at the time, with the book that produced them.

VENUE LOAD IS BOUNDED. This shares a gateway with the money path and
the venue 429'd a board walk above ~3 req/s: one paced quote read per
market, at most MAX_READS_PER_TICK per cycle, and a run of unreadable
books abandons the tick rather than retrying into the limit.

Kill: SHADOW_BETTOR=off.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from .. import pmus
from .. import shadow_bettor as bettor
# Operational telemetry lives apart from the decision path: the
# decision module may not read the decision ledger at all.
from .. import shadow_bettor_ops as ops
from .. import shadow_bettor_policy as bpol
from .. import shadow_store as store
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

TICK_S = 60.0
BACKOFF_S = 120.0
READ_PACING_S = 0.4
MAX_READS_PER_TICK = 10
MISS_ABANDON = 4

# ONE ROW PER MARKET PER BUCKET. Without a bucket the dataset becomes a
# record of how often this loop ran rather than of what the market did.
CADENCE_S = 300

EVIDENCE_SOURCE = "PMUS_BBO"

W_NO_QUOTE = "VENUE_RETURNED_NO_QUOTE"
W_VENUE_STATE = "VENUE_MARKET_STATE_%s"
W_READ_FAILED = "QUOTE_READ_FAILED_%s"


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


def _read_quote(slug: str) -> dict:
    """One paced quote read, off the event loop. Never raises."""
    pace(READ_PACING_S)
    try:
        return pmus.bbo_read(pmus._get_client(), slug)
    except Exception as exc:                                   # noqa: BLE001
        return {"bid": None, "ask": None, "state": None,
                "error": type(exc).__name__}


def _market_state(subject: dict, quote: dict, captured_at) -> dict:
    """The book, or a NAMED unreadable state. Never a bid of zero."""
    common = dict(captured_at=captured_at, symbol=subject["symbol"],
                  outcome_leg=subject.get("outcomeLeg"),
                  evidence_source=EVIDENCE_SOURCE,
                  source_interval_s=CADENCE_S)
    if quote.get("error"):
        return store.market_state_record(
            readable=False, why_unreadable=W_READ_FAILED % quote["error"],
            **common)
    if quote.get("bid") is None and quote.get("ask") is None:
        state = quote.get("state")
        return store.market_state_record(
            readable=False,
            why_unreadable=(W_VENUE_STATE % state) if state else W_NO_QUOTE,
            **common)
    return store.market_state_record(
        readable=True, bid=quote.get("bid"), ask=quote.get("ask"),
        # NOT pretended to be depth. The frozen BETTOR blocker
        # INSUFFICIENT_DEPTH fires from exactly this absence, which is
        # the honest state of the evidence until institutional L2 is
        # established as the source.
        available_depth=None,
        l2_reference={"feed": "bbo", "depth": "NOT_IDENTIFIED",
                      "venueState": quote.get("state")},
        **common)


async def _note(pool, stage, exc, opportunity_id, symbol) -> None:
    """Record the failure. NEVER raise from here: a writer that could
    fail while writing down its own failure is the shape of bug this
    whole mechanism exists to end."""
    try:
        await ops.record_failure(
            stage=stage, error=exc, pool=pool,
            bettor_opportunity_id=opportunity_id, symbol=symbol)
    except Exception:                                          # noqa: BLE001
        log.debug("shadow_bettor: failure note failed", exc_info=True)


async def tick(pool) -> dict:
    stats = {"looked": 0, "opportunities": 0, "decisions": 0,
             "unreadable": 0, "failures": 0, "status": "ok"}

    # EVERY ORPHAN GETS A NAMED REASON, every cycle. This is the counter
    # whose absence let 31 opportunities sit beside zero decisions for an
    # hour with nothing anywhere saying so.
    try:
        stats["annotated"] = await ops.annotate_orphans(pool)
    except Exception as exc:                                   # noqa: BLE001
        stats["annotated"] = {"error": type(exc).__name__}
        log.warning("shadow_bettor: orphan sweep failed", exc_info=True)

    try:
        subjects = await bettor.universe(pool, limit=MAX_READS_PER_TICK)
    except Exception as exc:                                   # noqa: BLE001
        stats["status"] = "universe_unreadable"
        stats["failures"] += 1
        stats["lastError"] = "%s: %s" % (type(exc).__name__, exc)
        await _note(pool, "UNIVERSE_READ", exc, None, None)
        return stats
    if not subjects:
        stats["status"] = "no_universe"
        return stats

    misses = 0
    for subject in subjects:
        stats["looked"] += 1
        quote = await asyncio.to_thread(_read_quote, subject["symbol"])
        captured_at = datetime.now(tz=timezone.utc)
        state = _market_state(subject, quote, captured_at)
        if not state["readable"]:
            misses += 1
            stats["unreadable"] += 1

        opportunity = bettor.opportunity_record(
            symbol=subject["symbol"], observed_at=captured_at,
            outcome_leg=subject.get("outcomeLeg"),
            event_id=subject.get("eventId"),
            evidence_source=EVIDENCE_SOURCE,
            market_state=state if state["readable"] else None,
            cadence_s=CADENCE_S)
        # The market state row is written whether or not it was
        # readable: an unreadable book at a known instant is evidence,
        # and dropping it would leave a hole nobody could distinguish
        # from a market we never looked at.
        await store.record_market_state(state, pool=pool)
        opportunity["marketStateId"] = state["marketStateId"]

        # ONE MARKET'S FAILURE IS NOT THE TICK'S FAILURE. Before this,
        # any exception here abandoned the whole cycle and reported
        # tick_failed with the STORE-READINESS problems list -- which is
        # empty when the store is fine. Nine healthy markets were
        # discarded because the tenth raised, and the heartbeat said
        # nothing about why. Now the failure is written down in the
        # database's own words and the loop continues.
        try:
            _oid, was_new = await bettor.record_opportunity(opportunity,
                                                            pool=pool)
        except Exception as exc:                               # noqa: BLE001
            stats["failures"] += 1
            stats["lastError"] = "%s: %s" % (type(exc).__name__, exc)
            log.warning("shadow_bettor: opportunity write failed for %s",
                        subject["symbol"], exc_info=True)
            await _note(pool, "OPPORTUNITY_WRITE", exc,
                        opportunity.get("bettorOpportunityId"),
                        subject["symbol"])
            continue

        if was_new:
            stats["opportunities"] += 1
            try:
                _did, decided = await bettor.write_decision(
                    opportunity, state if state["readable"] else None,
                    pool=pool)
            except Exception as exc:                           # noqa: BLE001
                stats["failures"] += 1
                stats["lastError"] = "%s: %s" % (type(exc).__name__, exc)
                log.warning("shadow_bettor: decision write failed for %s",
                            subject["symbol"], exc_info=True)
                await _note(pool, "DECISION_WRITE", exc,
                            opportunity["bettorOpportunityId"],
                            subject["symbol"])
                continue
            if decided:
                stats["decisions"] += 1

        if misses >= MISS_ABANDON:
            stats["status"] = "venue_unreadable"
            break
    return stats


async def run() -> None:
    if _off("SHADOW_BETTOR"):
        log.info("shadow_bettor: collection off by switch")
        return
    pool = await get_pool()

    ready = await store.store_ready(pool)

    # FREEZE BETTOR'S OWN POLICY BEFORE ROW 1. The benchmark lane's
    # worker does the same for its own policy; this is BETTOR's, and
    # neither can satisfy the other. This is the step that was missing:
    # the
    # decision table's foreign key into shadow_policy_versions refused
    # every BETTOR decision for want of this row, 99 times, and the
    # constraint was right to. The freeze is idempotent -- ALREADY_FROZEN
    # on every later boot -- and REFUSED if the declaration ever changes
    # under this version name, which is the whole point of freezing.
    frozen = None
    if ready["storeReady"]:
        frozen = await store.freeze_policy(pool, policy=bpol.frozen_policy())
        if frozen["status"] == "REFUSED":
            ready = dict(ready, storeReady=False,
                         problems=ready["problems"] + [frozen["why"]])

    boot = {"lane": "BETTOR_EV_SHADOW", "primary": True,
            "storeReady": ready["storeReady"],
            "problems": ready["problems"],
            "policy": bettor.POLICY_VERSION,
            "policyFreeze": (frozen or {}).get("status", "NOT_ATTEMPTED"),
            "policySha": bpol.POLICY_SHA[:16],
            "policyCodeSha": bpol.POLICY_CODE_SHA[:16],
            "codeShaMatches": (frozen or {}).get("codeShaMatches"),
            "universe": bettor.UNIVERSE_VERSION,
            "pBettor": "NOT_ESTABLISHED",
            "shadowMode": ready["shadowMode"],
            "capitalAtRisk": ready["capitalAtRisk"],
            "disclosure": ready["disclosure"]}
    log.info("shadow_bettor: %s", boot)

    if not ready["storeReady"]:
        while True:
            await heartbeat("shadow_bettor", "store_not_ready", boot)
            await asyncio.sleep(BACKOFF_S)

    while True:
        started = time.monotonic()
        try:
            stats = await tick(pool)
        except Exception as exc:                               # noqa: BLE001
            log.warning("shadow_bettor: tick failed", exc_info=True)
            # THE ERROR TRAVELS WITH THE STATUS. `problems` below comes
            # from the STORE-READINESS check and is empty whenever the
            # schema is fine -- so a tick_failed heartbeat used to carry
            # a reassuring empty list and no cause at all.
            stats = {"status": "tick_failed",
                     "tickError": "%s: %s" % (type(exc).__name__, exc)}
            try:
                await ops.record_failure(stage="NOT_IDENTIFIED",
                                            error=exc, pool=pool)
            except Exception:                                  # noqa: BLE001
                log.debug("shadow_bettor: tick failure note failed")
        stats.update(boot)
        stats["tickS"] = round(time.monotonic() - started, 3)
        # READ BACK, NOT ASSUMED. COMMAND must not show LIVE merely
        # because opportunity collection works, so the component state
        # is derived from the orphan and failure counts in the database
        # rather than from the fact that this loop reached its end.
        try:
            stats["pipeline"] = await ops.pipeline_health(pool)
        except Exception:                                      # noqa: BLE001
            stats["pipeline"] = {"state": "NOT_IDENTIFIED"}
        try:
            await heartbeat("shadow_bettor",
                            str(stats.get("status") or "ok"), stats)
        except Exception:                                      # noqa: BLE001
            log.debug("shadow_bettor: heartbeat failed")
        await asyncio.sleep(
            BACKOFF_S if stats.get("status") == "venue_unreadable"
            else TICK_S)
