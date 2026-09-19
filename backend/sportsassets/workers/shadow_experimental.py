"""Worker: BETTOR_EXPERIMENTAL_SHADOW. The prospective experiment loop.

Owner directive 2026-09-19 21:2xZ (two lanes), 22:4xZ (§10's order of
work) and 23:0xZ ("Start X1 prospective experimental collection using
this bridge as soon as the frozen eligibility requirements are
satisfied").

THIS LANE IS NOT THE DECISION-GRADE LANE AND NEVER BECOMES IT.
BETTOR_EV_SHADOW keeps its frozen framework and may remain at zero
trades forever; this loop exists to make candidate models commit to
falsifiable prospective trades so we can find out whether any of them
has edge. Nothing here promotes anything, and the registry it trades is
the one that was frozen before the first outcome existed.

THE TWO HALVES OF A TICK, AND WHY THEY ARE IN THIS ORDER:

  EXECUTE first. Seals written on earlier ticks are matched against the
  first institutional book observed AFTER their instant, and the
  arrival walk is reconstructed against that book. A seal whose
  evidence never arrives inside the window is closed as NOT_IDENTIFIED
  -- a recorded decision with no P&L-bearing trade, which is what §4
  requires and is a real finding about this latency regime.

  SEAL second. The newest eligible opportunity per market is decided by
  every ARMED experiment against the SAME population, the seal is
  written down before any arrival evidence for it exists, and an L2
  request is queued for the bridge to drain.

WHY THE SEAL IS PERSISTED RATHER THAN HELD. The bridge runs on a
schedule and answers minutes later. A seal kept in memory is lost on
restart, and the tempting repair -- decide now against a book we
already have -- is lookahead. Writing the seal first makes the ledger
prove the ordering: `sealed_at` precedes `received_timestamp` on every
executed row, or the row does not exist.

THE LATENCY REGIME IS ON EVERY ROW AND IS NOT AVERAGED AWAY. A book
fetched by a CI runner minutes after the decision is a different
execution environment from a persistent worker's. X1's horizon is 60
seconds and the bridge's cadence is ten minutes, so BRIDGE-ERA X1
RESULTS ARE A WEAK TEST OF X1 -- they measure the rule under a latency
it was not designed for. That is worth collecting and must never be
pooled with a persistent worker's results; the schema refuses to.

MEASUREMENT ONLY. No order path exists in this import graph.
REAL_ORDER_SUBMITTED false, CAPITAL_AT_RISK 0, on every row and by
CHECK.

Kill: SHADOW_EXPERIMENTAL=off.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from .. import shadow_experiment_registry as reg
from .. import shadow_experiment_signals as sig
from .. import shadow_experimental_engine as eng
from .. import shadow_experimental_markouts as mk
from .. import shadow_experimental_store as xstore
from .. import shadow_experiments as xp
from .. import shadow_identity as ident
from .. import shadow_l2 as l2
from ..db import get_pool, heartbeat

log = logging.getLogger(__name__)

LANE = xp.EXPERIMENTAL_LANE
REQUESTED_BY = "shadow_experimental"

TICK_S = 60.0
BACKOFF_S = 120.0

# How far back the eligible population is drawn from, and how many
# markets one tick may decide.
WINDOW_S = 3600
MAX_MARKETS_PER_TICK = 10
MAX_SEALS_PER_TICK = 40

# HOW LONG A SEAL WAITS FOR ITS ARRIVAL BOOK. The bridge fires every
# ten minutes; a seal that has seen no institutional observation after
# twice that has not been served, and pretending otherwise by walking
# an older book would reconstruct a fill at a price the decision
# already knew. It is closed NOT_IDENTIFIED instead.
SEAL_EXPIRY_S = 1200


def _off(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() in (
        "off", "0", "false", "no")


def _now():
    return datetime.now(tz=timezone.utc)


def binding_at_t0(sealed: dict) -> dict:
    """The identity binding as it stood WHEN THE DECISION WAS MADE.

    Reconstructed from the seal rather than re-derived at arrival, on
    purpose: a binding that changed between T0 and the walk must not
    silently admit -- or silently refuse -- a trade that was decided
    under the old one. A complement basket arrives here with no
    walkable basket, so §4 refuses it, which is the honest state of the
    NO side until the institutional venue confirms its siblings.
    """
    verdict = sealed.get("identityBindingStatus", ident.NOT_IDENTIFIED)
    return {"verdict": verdict,
            "executionEligible": verdict in ident.EXECUTION_ELIGIBLE,
            "identityBindingSha": sealed.get("identityBindingSha"),
            "institutional": {
                "symbol": sealed.get("institutionalInstrumentId")},
            "retailLeg": sealed.get("outcomeLeg"),
            "basketWalkable": False}


async def _persist(pool, sealed, execution) -> bool:
    written = await xstore.record_decision(pool, sealed, execution)
    if written:
        await xstore.open_position(pool, sealed, execution)
    return written


# ── the execute half ─────────────────────────────────────────────────


async def drain_seals(pool, *, now=None, expiry_s=SEAL_EXPIRY_S,
                      limit=MAX_SEALS_PER_TICK) -> dict:
    """Match open seals to the first book observed after them."""
    now = now or _now()
    stats = {"open": 0, "executed": 0, "expired": 0, "waiting": 0,
             "refused": 0, "filled": 0}
    for row in await xstore.open_seals(pool, limit=limit):
        stats["open"] += 1
        sealed = row["seal"]
        decision_id = row["experimentalDecisionId"]
        sealed_at = row["sealedAt"]

        try:
            evidence = await xstore.evidence_for(pool, row["symbol"],
                                                 after=sealed_at)
        except l2.ScalesRequired as exc:
            # Recorded but unpriceable. Not a fill, and not a zero.
            log.warning("shadow_experimental: %s", exc)
            evidence = None

        aged = (now - sealed_at) >= timedelta(seconds=expiry_s)
        if evidence is None and not aged:
            stats["waiting"] += 1
            continue

        try:
            execution = eng.execute(
                sealed, evidence=evidence,
                binding=binding_at_t0(sealed),
                arrival_at=(evidence or {}).get("l2ReceivedTimestamp"))
        except eng.EngineRefusal as exc:
            # A seal that does not re-derive is never executed. It is
            # left open and loud rather than quietly turned into a
            # trade under a hash nobody can reproduce.
            stats["refused"] += 1
            log.error("shadow_experimental: %s (%s)", exc, decision_id)
            continue

        await _persist(pool, sealed, execution)
        await xstore.close_seal(
            pool, decision_id,
            status=(xstore.EXECUTED_SEAL if evidence is not None
                    else xstore.EXPIRED_SEAL),
            l2_request_id=(evidence or {}).get("l2RequestId"), at=now)
        if evidence is None:
            stats["expired"] += 1
        else:
            stats["executed"] += 1
        if execution.get("positionId"):
            stats["filled"] += 1
            log.info("shadow_experimental: %s %s %s executed $%s of $%s "
                     "at vwap %s (%s)", sealed["experimentId"],
                     sealed["action"], sealed["marketId"],
                     execution["executedNotionalUsd"],
                     sealed["intendedNotionalUsd"], execution["vwap"],
                     execution["latencyRegime"])
    return stats


# ── the seal half ────────────────────────────────────────────────────


def bind_yes(instrument_record, retail_row) -> dict:
    """The identity binding for one market's YES leg, or a refusal verdict."""
    if not instrument_record or not retail_row:
        return {"verdict": ident.NOT_IDENTIFIED, "executionEligible": False,
                "why": ["no institutional instrument record has been "
                        "observed for this symbol"],
                "identityBindingSha": None, "institutional": {}}
    return ident.yes_leg_binding(
        ident.institutional_identity(instrument_record),
        ident.retail_identity(retail_row))


async def seal_population(pool, *, now=None, window_s=WINDOW_S,
                          max_markets=MAX_MARKETS_PER_TICK) -> dict:
    """Decide every ARMED experiment on one eligible population."""
    now = now or _now()
    stats = {"markets": 0, "eligible": 0, "sealed": 0, "requested": 0,
             "noTrade": 0, "tooFewSamples": 0, "notIdentified": 0,
             "refused": 0}
    # ONE BOOK REQUEST SERVES EVERY EXPERIMENT ON THAT MARKET AT THAT
    # INSTANT. The request id is derived from (symbol, purpose, instant),
    # so X1 and its control queue the same row -- which is what makes
    # them share an arrival rather than two books minutes apart. The
    # counter reports ROWS, not calls, or it would claim venue load
    # this lane never creates.
    requested: set = set()

    by_symbol = await xstore.eligible_opportunities(pool, window_s=window_s)
    if not by_symbol:
        stats["status"] = "no_eligible_population"
        return stats

    instruments = await xstore.instrument_records(pool, list(by_symbol))
    retail = await xstore.retail_rows(pool, list(by_symbol))

    # THE SUBJECTS, chosen before any signal is computed. Newest market
    # first is a selection rule that cannot know which way a signal
    # points, which is the property that matters: a population chosen
    # by what the model would say is not a population.
    subjects = []
    for symbol, rows in by_symbol.items():
        series = xstore.mid_series_of(rows)
        if len(series) < sig.M1_MIN_SAMPLES:
            stats["tooFewSamples"] += 1
            continue
        subjects.append((rows[-1]["observedAt"], symbol, rows, series))
    subjects.sort(key=lambda s: s[0], reverse=True)
    subjects = subjects[:max_markets]
    stats["markets"] = len(subjects)
    if not subjects:
        stats["status"] = "no_eligible_population"
        return stats

    armed = reg.armed()
    latest = {symbol: rows[-1] for _at, symbol, rows, _s in subjects}
    already = await xstore.sealed_already(
        pool, [o["bettorOpportunityId"] for o in latest.values()])

    fresh = {s: o for s, o in latest.items()
             if any((e["experimentId"], o["bettorOpportunityId"])
                    not in already for e in armed)}
    stats["eligible"] = len(fresh)
    if not fresh:
        stats["status"] = "already_sealed"
        return stats

    population_id = eng.population_id(
        [o["bettorOpportunityId"] for o in fresh.values()],
        now.isoformat(), xstore.ELIGIBILITY_RULE_SHA)
    await xstore.record_population(pool, population_id=population_id,
                                   sealed_at=now,
                                   opportunity_count=len(fresh))

    series_of = {symbol: s for _at, symbol, _rows, s in subjects}
    for symbol, opportunity in fresh.items():
        binding = bind_yes(instruments.get(symbol), retail.get(
            (symbol, (opportunity.get("outcomeLeg") or "").lower())))
        if not binding.get("executionEligible"):
            stats["notIdentified"] += 1
        for experiment in armed:
            if (experiment["experimentId"],
                    opportunity["bettorOpportunityId"]) in already:
                continue
            try:
                sealed = eng.seal(experiment=experiment,
                                  opportunity=opportunity,
                                  mid_series=series_of[symbol],
                                  binding=binding,
                                  eligible_population_id=population_id,
                                  at=now)
            except eng.EngineRefusal as exc:
                stats["refused"] += 1
                log.warning("shadow_experimental: seal refused for %s: %s",
                            symbol, exc)
                continue

            decision_id = eng.decision_id(sealed)
            if not await xstore.record_seal(pool, sealed,
                                            decision_id=decision_id):
                continue
            stats["sealed"] += 1

            if sealed["action"] == eng.NO_TRADE:
                # A REFUSAL IS A DECISION AND IS RECORDED AS ONE. There
                # is no arrival to wait for, so it is closed on the
                # same tick it was sealed.
                execution = eng.execute(sealed, evidence=None,
                                        binding=binding, arrival_at=None)
                await _persist(pool, sealed, execution)
                await xstore.close_seal(pool, decision_id,
                                        status=xstore.EXECUTED_SEAL, at=now)
                stats["noTrade"] += 1
                continue

            request_id = await xstore.request_l2(
                pool, symbol=symbol, requested_by=REQUESTED_BY,
                purpose=xstore.PURPOSE_ARRIVAL,
                identity_binding_sha=binding.get("identityBindingSha"),
                at=now)
            await xstore.attach_request(pool, decision_id, request_id)
            requested.add(request_id)
    stats["requested"] = len(requested)
    return stats


# ── §12: the markouts, appended as later facts ───────────────────────


async def take_markouts(pool, *, now=None, window_s=86400,
                        limit=100) -> dict:
    """30S / 60S / 300S for every filled position, once each.

    NOTHING HERE TOUCHES THE DECISION IT MEASURES. Each markout is its
    own append, keyed back, so the T0 row stays readable exactly as it
    was sealed -- §12's requirement and the reason markouts have their
    own table.
    """
    now = now or _now()
    stats = {"subjects": 0, "observed": 0, "notIdentified": 0,
             "notYetMature": 0}
    subjects = await xstore.markout_subjects(pool, window_s=window_s,
                                             limit=limit)
    if not subjects:
        return stats
    taken = await xstore.markouts_taken(
        pool, [s["experimentalDecisionId"] for s in subjects])

    for subject in subjects:
        stats["subjects"] += 1
        for horizon, seconds in mk.HORIZONS:
            key = (subject["experimentalDecisionId"], horizon)
            if key in taken:
                continue
            target = mk.target_at(subject["decisionTimestamp"], seconds)
            if now < target:
                stats["notYetMature"] += 1
                continue
            try:
                book = await xstore.evidence_nearest(
                    pool, subject["symbol"], target=target)
            except l2.ScalesRequired:
                book = None
            out = mk.markout(
                horizon=horizon, horizon_s=seconds,
                decision_at=subject["decisionTimestamp"],
                position={"qty": subject["qty"], "vwap": subject["vwap"]},
                book=book,
                observed_at=(book or {}).get("l2ReceivedTimestamp"),
                now=now)
            if out["status"] == mk.NOT_YET_MATURE:
                stats["notYetMature"] += 1
                continue
            await xstore.record_markout(pool,
                                        subject["experimentalDecisionId"],
                                        subject["positionId"], out)
            stats["observed" if out["status"] == mk.OBSERVED
                  else "notIdentified"] += 1
    return stats


async def tick(pool, *, now=None) -> dict:
    now = now or _now()
    stats = {"lane": LANE, "status": "ok"}
    stats["drain"] = await drain_seals(pool, now=now)
    stats["markouts"] = await take_markouts(pool, now=now)
    stats["seal"] = await seal_population(pool, now=now)
    if stats["seal"].get("status"):
        stats["status"] = stats["seal"]["status"]
    return stats


# ── boot ─────────────────────────────────────────────────────────────


async def run() -> None:
    if _off("SHADOW_EXPERIMENTAL"):
        log.info("shadow_experimental: collection off by switch")
        return
    pool = await get_pool()

    # THE REGISTRY IS WRITTEN DOWN BEFORE THE FIRST SEAL, and a rule
    # edited under a live experiment is visible here rather than
    # discovered later: verify_all re-derives every declaration's hash
    # from the typed literals.
    checks = reg.verify_all()
    mismatched = [c["experimentId"] for c in checks if not c["matches"]]

    # A DEPLOYMENT WHOSE MIGRATION HAS NOT RUN COSTS A HEARTBEAT, not a
    # crash loop and not a half-written decision.
    ready = await xstore.store_ready(pool)
    frozen = ({"written": 0} if not ready["storeReady"]
              else await xstore.freeze_experiments(pool, reg.EXPERIMENTS))

    boot = dict(reg.registry_report(),
                lane=LANE,
                storeReady=ready["storeReady"],
                problems=ready["problems"],
                experimentsWritten=frozen["written"],
                eligibilityRule=xstore.ELIGIBILITY_RULE,
                eligibilityRuleSha=xstore.ELIGIBILITY_RULE_SHA,
                featureSourceRequired=l2.FEATURE_SOURCE_VERSION,
                latencyRegime="GITHUB_BRIDGE",
                sealExpiryS=SEAL_EXPIRY_S,
                disclosure=xp.EXPERIMENTAL_DISCLOSURE)
    log.info("shadow_experimental: %s", boot)

    if not ready["storeReady"]:
        while True:
            await heartbeat("shadow_experimental", "store_not_ready", boot)
            await asyncio.sleep(BACKOFF_S)

    if mismatched:
        # FAIL CLOSED. A declaration whose hash no longer re-derives
        # means a frozen rule was edited; sealing under it would file
        # the new rule's trades under the old rule's history.
        while True:
            await heartbeat("shadow_experimental", "registry_mismatch",
                            dict(boot, mismatched=mismatched))
            await asyncio.sleep(BACKOFF_S)

    while True:
        started = time.monotonic()
        try:
            stats = await tick(pool)
        except Exception as exc:                               # noqa: BLE001
            log.warning("shadow_experimental: tick failed", exc_info=True)
            stats = {"status": "tick_failed",
                     "tickError": "%s: %s" % (type(exc).__name__, exc)}
        stats.update(boot)
        stats["tickS"] = round(time.monotonic() - started, 3)
        try:
            await heartbeat("shadow_experimental",
                            str(stats.get("status") or "ok"), stats)
        except Exception as exc:                               # noqa: BLE001
            log.error("shadow_experimental: HEARTBEAT WRITE FAILED: %s: %s",
                      type(exc).__name__, exc, exc_info=True)
        await asyncio.sleep(TICK_S)
