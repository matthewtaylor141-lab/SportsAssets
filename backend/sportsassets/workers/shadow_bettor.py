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
from .. import shadow_bettor_sizing as szpol
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

# ── policy integrity, as a named state rather than a boolean ─────────
#
# Owner directive 2026-09-19 20:2xZ: a running code sha that differs
# from the frozen one is POLICY_CODE_DRIFT and DECISION_WRITING_ALLOWED
# is FALSE. V1 published this condition and carried on; V2 stops.
INTEGRITY_OK = "VERIFIED"
INTEGRITY_DRIFT = "POLICY_CODE_DRIFT"
INTEGRITY_DECLARATION_REFUSED = "POLICY_DECLARATION_REFUSED"
INTEGRITY_STORE_NOT_READY = "STORE_NOT_READY"
INTEGRITY_NOT_ESTABLISHED = "NOT_ESTABLISHED"


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


async def tick(pool, *, decision_writing_allowed: bool = True,
               integrity: str = INTEGRITY_OK) -> dict:
    """One cycle. OBSERVATION IS UNCONDITIONAL; the DECISION is
    not.

    Owner directive 2026-09-19 20:2xZ: "OBSERVE MARKET = YES /
    WRITE OPPORTUNITY = YES / WRITE DECISION = NO until policy
    integrity is restored. Do not lose prospective market
    observations because a decision policy fails integrity."

    So the gate sits around the decision write ONLY. A market
    observed during a drift is still evidence, and evidence
    that was never written cannot be recovered later.
    """
    stats = {"looked": 0, "opportunities": 0, "decisions": 0,
             "unreadable": 0, "failures": 0, "status": "ok",
             "decisionWritingAllowed": decision_writing_allowed,
             "policyIntegrity": integrity,
             "decisionsWithheld": 0}

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
            # THE FAIL-CLOSED GATE. The opportunity above is already
            # written -- that is the point of putting the gate here and
            # not at the top of the loop.
            if not decision_writing_allowed:
                stats["decisionsWithheld"] += 1
                continue
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
    # ── THE V2 BOOT ORDER, exactly as mandated ───────────────────────
    #
    #   STORE READY -> V2 DECLARATION VERIFIED -> V2 RUNNING CODE SHA
    #   VERIFIED -> V2 POLICY FROZEN -> DECISION WRITING ENABLED
    #
    # "There must be no interval where V2 code writes decisions as V1."
    # That is why decision_writing_allowed starts FALSE and is only ever
    # set true at the end of this sequence: there is no ordering of the
    # steps below that can leave it true by accident.
    frozen = None
    sized = None
    decision_writing_allowed = False
    integrity = INTEGRITY_NOT_ESTABLISHED
    integrity_why = "policy integrity has not been checked yet"

    if not ready["storeReady"]:
        integrity = INTEGRITY_STORE_NOT_READY
        integrity_why = "; ".join(ready["problems"]) or "store not ready"
    else:
        # STEP 2: THE DECLARATION. A REFUSED freeze means the rules
        # changed under a version already carrying rows.
        frozen = await store.freeze_policy(pool, policy=bpol.frozen_policy())
        if frozen["status"] == "REFUSED":
            integrity = INTEGRITY_DECLARATION_REFUSED
            integrity_why = frozen["why"]
            ready = dict(ready, storeReady=False,
                         problems=ready["problems"] + [frozen["why"]])
        else:
            # STEP 3: THE RUNNING CODE. On a fresh freeze the running
            # sha IS the frozen one by construction; on a later boot
            # freeze_policy compares them and reports the answer.
            matches = frozen.get("codeShaMatches")
            if frozen["status"] == "FROZEN":
                matches = True
            if matches is False:
                # FAIL CLOSED. Collection continues; decisions do not.
                integrity = INTEGRITY_DRIFT
                integrity_why = (
                    "running code sha %s does not match the frozen %s for "
                    "%s; decision writing is blocked until policy "
                    "integrity is restored"
                    % (bpol.POLICY_CODE_SHA[:16],
                       str(frozen.get("policyCodeSha"))[:16],
                       bpol.BETTOR_POLICY_VERSION))
                log.error("shadow_bettor: %s", integrity_why)
            else:
                # STEP 4/5: frozen and verified -> decisions enabled.
                integrity = INTEGRITY_OK
                integrity_why = "declaration and code sha both verified"
                decision_writing_allowed = True

        # THE $1,000 STANDARD, frozen before the first eligible entry.
        # A REFUSED sizing freeze does NOT stop collection: sizing is
        # consulted only once an entry is eligible, and none can be
        # while the action set is [NO_TRADE].
        sized = await store.freeze_sizing_policy(
            pool, policy=szpol.frozen_policy())
        if sized["status"] == "REFUSED":
            ready = dict(ready,
                         problems=ready["problems"] + [sized["why"]])

    boot = {"lane": "BETTOR_EV_SHADOW", "primary": True,
            "storeReady": ready["storeReady"],
            "problems": ready["problems"],
            "policy": bettor.POLICY_VERSION,
            "policyFreeze": (frozen or {}).get("status", "NOT_ATTEMPTED"),
            "policyIntegrity": integrity,
            "policyIntegrityWhy": integrity_why,
            "decisionWritingAllowed": decision_writing_allowed,
            "codeBoundary": bpol.CODE_BOUNDARY,
            "sizingFreeze": (sized or {}).get("status", "NOT_ATTEMPTED"),
            "sizingPolicy": szpol.SIZING_POLICY_VERSION,
            "standardNotionalUsd": szpol.STANDARD_BETTOR_SHADOW_NOTIONAL_USD,
            "policySha": bpol.POLICY_SHA[:16],
            "policyCodeSha": bpol.POLICY_CODE_SHA[:16],
            "codeShaMatches": (frozen or {}).get("codeShaMatches"),
            "universe": bettor.UNIVERSE_VERSION,
            "pBettor": "NOT_ESTABLISHED",
            "shadowMode": ready["shadowMode"],
            "capitalAtRisk": ready["capitalAtRisk"],
            "disclosure": ready["disclosure"]}
    log.info("shadow_bettor: %s", boot)

    # THE INTEGRITY VERDICT NEEDS A SOURCE OF ITS OWN, and the write
    # lives in the ops module rather than here. The append-only rule
    # refuses any upserting statement anywhere in the decision writer
    # or this worker, and it is right to: a boot marker is current
    # state,
    # not prospective evidence, and mixing the two in one file is how
    # the distinction erodes. Same reason the telemetry reads moved out
    # earlier -- move the code, never the rule.
    await ops.record_boot(pool, boot)


    if not ready["storeReady"]:
        while True:
            await heartbeat("shadow_bettor", "store_not_ready", boot)
            await asyncio.sleep(BACKOFF_S)

    beat_failures = 0
    while True:
        started = time.monotonic()
        try:
            stats = await tick(
                pool,
                decision_writing_allowed=decision_writing_allowed,
                integrity=integrity)
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
        # A HEARTBEAT FAILURE MUST BE DIAGNOSABLE.
        #
        # This was log.debug, below the configured level, and it hid a
        # TypeError on every beat for 62 minutes while COMMAND showed a
        # stale tick_failed and the decision loop ran perfectly. The
        # telemetry plane still may not take the decision plane down --
        # so this catches, but it catches LOUDLY, names the exception,
        # and records the failure in the database the same way a
        # decision failure is recorded.
        try:
            await heartbeat("shadow_bettor",
                            str(stats.get("status") or "ok"), stats)
            beat_failures = 0
        except Exception as exc:                               # noqa: BLE001
            beat_failures += 1
            log.error("shadow_bettor: HEARTBEAT WRITE FAILED (%d in a row): "
                      "%s: %s -- the decision loop is unaffected, but "
                      "COMMAND's telemetry plane is now stale",
                      beat_failures, type(exc).__name__, exc, exc_info=True)
            await _note(pool, "NOT_IDENTIFIED", exc, None, "HEARTBEAT")
        await asyncio.sleep(
            BACKOFF_S if stats.get("status") == "venue_unreadable"
            else TICK_S)
