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

from .. import pmus
from .. import pmx_institutional as pmx
from .. import institutional_book as ib
from .. import shadow_experiment_registry as reg
from .. import shadow_experiment_signals as sig
from .. import shadow_experimental_engine as eng
from .. import shadow_experimental_markouts as mk
from .. import shadow_experimental_store as xstore
from .. import shadow_experiments as xp
from .. import shadow_identity as ident
from .. import shadow_identity_resolver as xident
from .. import shadow_l2 as l2
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

LANE = xp.EXPERIMENTAL_LANE
REQUESTED_BY = "shadow_experimental"

TICK_S = 60.0
BACKOFF_S = 120.0

# How far back the eligible population is drawn from, and how many
# markets one tick may decide. THE WINDOW IS THE LANE'S OWN, not the
# collector's: at one sample a minute, half an hour is thirty samples
# and X1 reads the last five of them.
WINDOW_S = 1800
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


async def _persist(pool, sealed, execution, latency=None) -> bool:
    written = await xstore.record_decision(pool, sealed, execution,
                                           latency=latency)
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


# ── the lane's own sampler ───────────────────────────────────────────
#
# WHY THIS EXISTS, found in production rather than reasoned about. At
# 00:01Z the decision-grade collector's corrected rows read
# YES_CONTRACT_BOOK n=17 symbols=17 -- one sample per market per hour,
# because that lane selects with `ORDER BY updated_at DESC LIMIT 10`
# over a board that churns and so almost never revisits a market. X1's
# frozen rule is the signed drift over the last five CAPTURED SAMPLES
# against a 60-second horizon: on that feed it could never fire, and
# the lane would have sat at zero trades reporting healthy.
#
# THE RULE IS NOT TUNED TO FIT THE FEED. Lowering the sample minimum
# after seeing that the rule cannot fire is tuning a frozen experiment
# to manufacture activity (§4: "If changed later: X1_V2"), and widening
# the window would feed a 60-second momentum rule samples an hour
# apart. So the lane reads its own small focus set every tick instead,
# and writes to its own table -- never into the decision-grade lane's.
#
# VENUE LOAD IS BOUNDED AND SMALL. One paced quote read per focus
# market per tick: eight reads a minute, about 0.13 req/s against a
# gateway that 429'd above ~3 req/s. A run of unreadable books
# abandons the sweep rather than retrying into the limit.

FOCUS_SIZE = 8
FOCUS_HOLD_S = 1800
READ_PACING_S = 0.4
SAMPLE_MISS_ABANDON = 4


def _read_quote(slug: str) -> dict:
    """One paced quote read, off the event loop. Never raises."""
    pace(READ_PACING_S)
    try:
        return pmus.bbo_read(pmus._get_client(), slug)
    except Exception as exc:                                   # noqa: BLE001
        return {"bid": None, "ask": None, "state": None,
                "error": type(exc).__name__}


def observation_of(subject, quote, at, *, cadence_s=int(TICK_S)) -> dict:
    """One focus read -> one observation row. Pure.

    An unreadable book at a known instant is evidence and is written
    down as one: dropping it would leave a hole indistinguishable from
    a market nobody looked at, and the eligibility query filters on
    `readable` rather than on the row's absence.
    """
    leg = subject.get("outcomeLeg") or "yes"
    base = {"experimentalObservationId": xstore.observation_id(
                subject["symbol"], leg, at, cadence_s),
            "observedAt": at, "symbol": subject["symbol"],
            "outcomeLeg": leg, "eventId": subject.get("eventId"),
            "cadenceS": cadence_s, "venueState": quote.get("state")}

    if quote.get("error") or (quote.get("bid") is None
                              and quote.get("ask") is None):
        why = ("QUOTE_READ_FAILED_%s" % quote["error"]) if quote.get("error") \
            else ("VENUE_MARKET_STATE_%s" % quote["state"]
                  if quote.get("state") else "VENUE_RETURNED_NO_QUOTE")
        return dict(base, readable=False, whyUnreadable=why,
                    microstructure=l2.bind_leg(
                        {"status": l2.NOT_IDENTIFIED, "why": why}, leg))

    bid, ask = quote.get("bid"), quote.get("ask")
    mid = None if (bid is None or ask is None) else (bid + ask) / 2.0
    spread = None if (bid is None or ask is None) else (ask - bid)
    micro = {"status": l2.MEASURED, "bid": bid, "ask": ask, "mid": mid,
             "spread": spread,
             "spreadRelative": (spread / mid) if (spread is not None and mid)
             else None,
             # DEPTH IS NOT ESTABLISHED FROM A BBO and is not pretended
             # to be. The executable depth this lane walks is the
             # institutional book, fetched at arrival.
             "depth": l2.NOT_IDENTIFIED,
             "evidenceSource": xstore.EVIDENCE_SOURCE_EXPERIMENTAL}
    if bid is None or ask is None:
        micro["oneSided"] = True
    return dict(base, readable=True, whyUnreadable=None,
                microstructure=l2.bind_leg(micro, leg))


async def sample_focus(pool, *, now=None, size=FOCUS_SIZE) -> dict:
    """One paced read per focus market, written to this lane's table."""
    now = now or _now()
    stats = {"focus": 0, "written": 0, "unreadable": 0, "held": 0}
    subjects = await xstore.focus_set(pool, size=size, hold_s=FOCUS_HOLD_S)
    if not subjects:
        stats["status"] = "no_focus_set"
        return stats

    misses = 0
    for subject in subjects:
        stats["focus"] += 1
        stats["held"] += 1 if subject.get("held") else 0
        quote = await asyncio.to_thread(_read_quote, subject["symbol"])
        obs = observation_of(subject, quote, _now())
        if not obs["readable"]:
            misses += 1
            stats["unreadable"] += 1
        if await xstore.record_observation(pool, obs):
            stats["written"] += 1
        if misses >= SAMPLE_MISS_ABANDON:
            # A RUN OF UNREADABLE BOOKS IS THE VENUE, NOT THE MARKET.
            # Abandoning is cheaper than retrying into a rate limit.
            stats["status"] = "venue_unreadable"
            break
    return stats


# ── the seal half ────────────────────────────────────────────────────


def bind_yes(instrument_record, retail_row) -> dict:
    """The identity binding for one market's YES leg, or a refusal verdict.

    THE FALLBACK, not the primary path any more. Owner directive
    2026-09-20 §2: the binding is resolved for the focus universe
    BEFORE the signal needs it, by the loop that holds the credential,
    and `binding_for` reads that. This function stays for the case
    where no pre-bound row exists yet -- a market bound only seconds
    after it joined the focus set -- and its honest answer there is
    still a refusal.
    """
    if not instrument_record or not retail_row:
        return {"verdict": ident.NOT_IDENTIFIED, "executionEligible": False,
                "why": ["no institutional instrument record has been "
                        "observed for this symbol"],
                "identityBindingSha": None, "institutional": {}}
    return ident.yes_leg_binding(
        ident.institutional_identity(instrument_record),
        ident.retail_identity(retail_row))


def binding_for(symbol, leg, *, bound, instruments, retail) -> dict:
    """The binding this decision is taken under. PRE-BOUND FIRST.

    WHY THE PRECOMPUTED ROW WINS. §2 asks the hot path to already know
    whether a market is execution eligible, and the pre-bound row was
    resolved against refdata the credentialed worker fetched DIRECTLY.
    The fallback below resolves against `bettor_l2_evidence`'s
    instrument_record, which only the GitHub bridge ever wrote -- the
    exact lookup that stamped 13 prospective BUY decisions
    NOT_IDENTIFIED while every component reported healthy.
    """
    leg = (leg or "yes").lower()
    row = (bound or {}).get((symbol, leg))
    if row:
        return xident.binding_for_hot_path(row)
    return bind_yes(instruments.get(symbol), retail.get((symbol, leg)))


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

    by_symbol = await xstore.eligible_observations(pool, window_s=window_s)
    if not by_symbol:
        stats["status"] = "no_eligible_population"
        return stats

    instruments = await xstore.instrument_records(pool, list(by_symbol))
    retail = await xstore.retail_rows(pool, list(by_symbol))
    bound = await xstore.bound_identities(pool, list(by_symbol))

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
        pool, [o["experimentalObservationId"] for o in latest.values()])

    fresh = {s: o for s, o in latest.items()
             if any((e["experimentId"], o["experimentalObservationId"])
                    not in already for e in armed)}
    stats["eligible"] = len(fresh)
    if not fresh:
        stats["status"] = "already_sealed"
        return stats

    population_id = eng.population_id(
        [o["experimentalObservationId"] for o in fresh.values()],
        now.isoformat(), xstore.ELIGIBILITY_RULE_SHA)
    await xstore.record_population(pool, population_id=population_id,
                                   sealed_at=now,
                                   opportunity_count=len(fresh))

    series_of = {symbol: s for _at, symbol, _rows, s in subjects}
    # WHICH MARKETS HAVE A CURRENT BOOK IN MEMORY, read once for the
    # whole pass. A market without one falls back to the bridge rather
    # than being skipped: the decision is still worth sealing, and its
    # arrival simply comes from the slower path.
    books = {s: ib.STORE.current(s, at=now) for s in fresh}
    stats["booksCurrent"] = len(
        [1 for b in books.values()
         if b.get("FRESHNESS_STATUS") == ib.CURRENT])
    for symbol, opportunity in fresh.items():
        direct = books.get(symbol, {}).get(
            "FRESHNESS_STATUS") == ib.CURRENT
        # §2: THE BINDING IS ALREADY KNOWN. The market-data loop
        # resolved it against refdata it fetched directly; the hot path
        # reads that row rather than re-deriving identity between a
        # signal and its execution.
        binding = binding_for(
            symbol, opportunity.get("outcomeLeg"),
            bound=bound, instruments=instruments, retail=retail)
        if not binding.get("executionEligible"):
            stats["notIdentified"] += 1
        for experiment in armed:
            if (experiment["experimentId"],
                    opportunity["experimentalObservationId"]) in already:
                continue
            # §7's hot path: the model runs between two instants that
            # are both recorded, and nothing slow sits between them.
            #
            # THE SEAL'S CLOCK IS THE TICK'S DECLARED INSTANT, not the
            # instant the model happened to start. The decision id and
            # the population id are both derived from it, and a seal
            # has to re-derive byte-for-byte before it may execute; a
            # clock read fresh inside the loop would make the same tick
            # irreproducible. §8 keeps the two apart anyway --
            # DECISION_TIMESTAMP and MODELED_SEND_TIMESTAMP are named
            # separately there -- so nothing is lost by being exact
            # about which is which.
            model_start = _now()
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
            model_end = _now()

            decision_id = eng.decision_id(sealed)
            # THE SEAL IS WRITTEN BEFORE THE ARRIVAL, still. §7 asks for
            # evidence persisted immediately after the T0 decision, and
            # the ordering it proves -- decided, then executed -- is the
            # same property the bridge era needed. One small insert,
            # taken during the modeled-latency wait rather than before
            # the model.
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
                await _persist(pool, sealed, execution,
                               latency=latency_block(
                                   observation=opportunity,
                                   sealed_at=now,
                                   model_start=model_start,
                                   model_end=model_end,
                                   modeled_arrival=None,
                                   book_row=books.get(symbol),
                                   persisted_at=_now()))
                await xstore.close_seal(pool, decision_id,
                                        status=xstore.EXECUTED_SEAL, at=now)
                stats["noTrade"] += 1
                continue

            if direct:
                await _execute_direct(pool, sealed, binding,
                                      decision_id=decision_id,
                                      observation=opportunity,
                                      sealed_at=now,
                                      model_start=model_start,
                                      model_end=model_end,
                                      stats=stats)
                continue

            # THE BRIDGE PATH REMAINS, unused while the direct one is
            # available and deliberately not deleted: it is the only
            # market-data path if the worker's own credential ever
            # stops authenticating, and a fallback removed the day the
            # primary worked is a fallback nobody has when it matters.
            request_id = await xstore.request_l2(
                pool, symbol=symbol, requested_by=REQUESTED_BY,
                purpose=xstore.PURPOSE_ARRIVAL,
                identity_binding_sha=binding.get("identityBindingSha"),
                at=now)
            await xstore.attach_request(pool, decision_id, request_id)
            requested.add(request_id)
    stats["requested"] = len(requested)
    return stats


async def _execute_direct(pool, sealed, binding, *, decision_id,
                          observation, sealed_at, model_start, model_end,
                          stats) -> None:
    """Wait out the frozen modeled latency, then walk the CURRENT book.

    §9: the wait is real. The book at the decision instant and the book
    250ms later are different objects on a moving market, and awarding
    the shadow the earlier one would be handing it an execution nobody
    could have got.

    THE ARRIVAL IS MEASURED FROM MODEL_END, the instant the decision
    actually existed in this process, and not from the seal's declared
    tick instant. They are close in production and they are not the
    same thing, and taking the earlier of the two would quietly refund
    the model's own compute time out of the modeled latency -- which is
    the one number §9 exists to stop anyone shrinking.
    """
    modeled_arrival = ib.modeled_arrival(model_end, reg.ARRIVAL_LATENCY_MS)
    remaining = (modeled_arrival - _now()).total_seconds()
    if remaining > 0:
        await asyncio.sleep(remaining)

    symbol = sealed["marketId"]
    book_row = ib.STORE.executable(symbol, at=modeled_arrival)
    if book_row is None:
        # STALE OR ABSENT. Not a fill at an old price, and not a zero.
        current = ib.STORE.current(symbol, at=modeled_arrival)
        execution = eng.execute(sealed, evidence=None, binding=binding,
                                arrival_at=modeled_arrival)
        execution = dict(execution,
                         why=current.get("why") or execution.get("why"))
        await _persist(pool, sealed, execution,
                       latency=latency_block(
                           observation=observation, sealed_at=sealed_at,
                           model_start=model_start, model_end=model_end,
                           modeled_arrival=modeled_arrival,
                           book_row=current, persisted_at=_now()))
        await xstore.close_seal(pool, decision_id,
                                status=xstore.EXPIRED_SEAL, at=_now())
        stats["bookNotCurrent"] = stats.get("bookNotCurrent", 0) + 1
        return

    # THE EXACT BOOK WALKED IS WRITTEN DOWN, so the fill can be
    # re-derived from the levels it came from rather than believed.
    try:
        await xstore.record_direct_evidence(pool, book_row)
    except Exception:                                          # noqa: BLE001
        log.debug("shadow_experimental: direct evidence write failed",
                  exc_info=True)
    evidence_id = xstore.direct_evidence_id(
        symbol, book_row["BETTOR_RECEIVED_TIMESTAMP"], book_row["BOOK_SHA"])

    execution = eng.execute(
        sealed, evidence=evidence_from_memory(book_row, evidence_id),
        binding=binding, arrival_at=modeled_arrival)
    await _persist(pool, sealed, execution,
                   latency=latency_block(
                       observation=observation, sealed_at=sealed_at,
                       model_start=model_start, model_end=model_end,
                       modeled_arrival=modeled_arrival,
                       book_row=book_row, persisted_at=_now()))
    await xstore.close_seal(pool, decision_id,
                            status=xstore.EXECUTED_SEAL, at=_now())
    stats["direct"] = stats.get("direct", 0) + 1
    if execution.get("positionId"):
        stats["filled"] = stats.get("filled", 0) + 1
        log.info("shadow_experimental: DIRECT %s %s %s executed $%s of $%s "
                 "at vwap %s (book %sms old, modeled arrival %sms)",
                 sealed["experimentId"], sealed["action"], symbol,
                 execution["executedNotionalUsd"],
                 sealed["intendedNotionalUsd"], execution["vwap"],
                 book_row.get("bookAgeMs"), reg.ARRIVAL_LATENCY_MS)


# ── §5/§7/§9: the DIRECT hot path ────────────────────────────────────
#
# CURRENT IN-MEMORY BOOK -> CURRENT FEATURES -> X1 -> ACTION -> MODELED
# ARRIVAL -> EXECUTION RECONSTRUCTION, in one tick, with no GitHub
# Action anywhere in it.
#
# §9 IS THE CLAUSE THAT COSTS SOMETHING AND IS KEPT ANYWAY. Having the
# book in memory does not mean BETTOR would have executed at the
# decision instant, so the walk is against the book CURRENT AT MODELED
# ARRIVAL -- the loop actually waits out the frozen latency and re-reads
# the store, rather than walking the book the decision was made on. That
# is the difference between reconstructing an execution and awarding
# oneself a free one, and on a moving book it is not a small one.
#
# A STALE BOOK IS NOT EXECUTABLE EVIDENCE (§6). The store returns
# freshness with every read and `executable()` returns nothing at all
# when the book has aged past the frozen limit; the decision is then
# recorded NOT_IDENTIFIED with the book's real age, never filled at a
# price whose provenance has gone quiet.

DIRECT = "DIRECT_INSTITUTIONAL_WORKER"
MODELED_BASIS = "MODELED_EXECUTION_LATENCY"


def _ms(a, b):
    """Milliseconds from a to b, or None if either instant is absent."""
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000, 3)


def evidence_from_memory(row: dict, evidence_id=None) -> dict:
    """An in-memory book, in the shape the engine's walk expects.

    `observedArrivalLatencyMs` is deliberately absent: there is no
    transport round trip between BETTOR and the venue at this instant,
    because the book was already here. The latency that DOES apply is
    the modeled execution latency, and it travels in its own column
    under its own name so the two can never be read as one number.
    """
    book = dict(row.get("book") or {})
    book.update({
        "l2EvidenceId": evidence_id,
        "l2BookSha": row.get("BOOK_SHA"),
        "latencyRegime": DIRECT,
        "bridgeLatencyMs": None,
        "l2SourceTimestamp": row.get("SOURCE_TIMESTAMP"),
        "l2ReceivedTimestamp": row.get("BETTOR_RECEIVED_TIMESTAMP"),
        "priceScale": row.get("priceScale"),
        "qtyScale": row.get("qtyScale"),
    })
    return book


def latency_block(*, observation, sealed_at, model_start, model_end,
                  modeled_arrival, book_row, persisted_at=None) -> dict:
    """§8, every instant and every interval derived from two of them.

    "Do not collapse these into one latency number." One number cannot
    tell a slow venue from a slow model from a slow persist, and the
    three have completely different remedies.
    """
    received = (book_row or {}).get("BETTOR_RECEIVED_TIMESTAMP")
    source_ts = (book_row or {}).get("SOURCE_TIMESTAMP")
    feature_asof = (observation or {}).get("observedAt")
    lag = (book_row or {}).get("MARKET_DATA_LAG_MS")
    return {
        "venueSourceTimestamp": source_ts,
        "bettorReceivedTimestamp": received,
        "featuresSealedTimestamp": feature_asof,
        "modelStartTimestamp": model_start,
        "modelEndTimestamp": model_end,
        "decisionTimestamp": sealed_at,
        "modeledSendTimestamp": sealed_at,
        "modeledArrivalTimestamp": modeled_arrival,
        "persistedTimestamp": persisted_at,
        # the intervals
        "marketDataLagMs": lag,
        "featureComputeMs": _ms(feature_asof, model_start),
        "modelComputeMs": _ms(model_start, model_end),
        "sourceToDecisionMs": (None if lag is None
                               else round(lag + (_ms(received, sealed_at)
                                                 or 0.0), 3)),
        "modeledExecutionLatencyMs": _ms(sealed_at, modeled_arrival),
        "sourceToModeledArrivalMs": (
            None if lag is None
            else round(lag + (_ms(received, modeled_arrival) or 0.0), 3)),
        "bookFreshnessStatus": (book_row or {}).get("FRESHNESS_STATUS"),
        "bookAgeMs": (book_row or {}).get("bookAgeMs"),
        # §9: the word, on the row, always.
        "executionLatencyBasis": MODELED_BASIS,
        "evidenceEnvironment": DIRECT,
    }


# ── §12: the markouts, appended as later facts ───────────────────────

# The bridge's own cadence. A request is bucketed to it so one fetch
# answers one bucket instead of sixty ticks writing sixty rows.
BRIDGE_CADENCE_S = 600

# How long after the last horizon a markout request is still worth
# queueing. Past this the miss is recorded and the symbol is dropped:
# a book fetched half an hour after a 300-second target is not that
# markout under any tolerance, and asking for it forever would load
# the venue to produce rows nothing can use.
MARKOUT_REQUEST_GRACE_S = 1800


def _bridge_bucket(at):
    epoch = int(at.timestamp()) // BRIDGE_CADENCE_S * BRIDGE_CADENCE_S
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _markout_window_open(subject, now, taken) -> bool:
    """Is any horizon on this position still worth fetching a book for?"""
    longest = max(s for _h, s in mk.HORIZONS)
    if now > subject["decisionTimestamp"] + timedelta(
            seconds=longest + MARKOUT_REQUEST_GRACE_S):
        return False
    return any((subject["experimentalDecisionId"], h) not in taken
               for h, _s in mk.HORIZONS)




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
             "notYetMature": 0, "requested": 0}
    subjects = await xstore.markout_subjects(pool, window_s=window_s,
                                             limit=limit)
    if not subjects:
        return stats
    taken = await xstore.markouts_taken(
        pool, [s["experimentalDecisionId"] for s in subjects])
    requested: set = set()

    for subject in subjects:
        stats["subjects"] += 1
        # THE BRIDGE FETCHES ONLY WHAT IS ASKED FOR. Without this the
        # only institutional book that ever exists for a symbol is its
        # arrival, and every markout would be measured against the book
        # the position was opened on -- which is not a markout at all,
        # it is the entry price wearing a later label. So while a
        # position still has an unresolved horizon, a request is queued
        # for the bridge to serve.
        #
        # ONE REQUEST PER SYMBOL PER BRIDGE CYCLE, not per tick: the
        # request id is derived from (symbol, purpose, instant), so
        # bucketing the instant to the bridge's own cadence makes the
        # insert idempotent instead of writing sixty rows an hour that
        # one fetch would answer.
        if _markout_window_open(subject, now, taken):
            key = (subject["symbol"], _bridge_bucket(now))
            if key not in requested:
                requested.add(key)
                await xstore.request_l2(
                    pool, symbol=subject["symbol"],
                    requested_by=REQUESTED_BY,
                    purpose=xstore.PURPOSE_MARKOUT,
                    at=_bridge_bucket(now))
                stats["requested"] += 1

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
            # SCORING MUST NOT BE ABLE TO STOP DECIDING (found in
            # production 2026-09-20 01:23Z). take_markouts runs BEFORE
            # seal_population in the tick, so one rejected markout
            # insert -- a CHECK that had not been widened for the
            # direct regime -- took down the whole experiment for
            # twenty-five minutes: no markouts AND no new decisions,
            # while the sampler kept writing observations and the lane
            # looked alive from outside.
            #
            # A MARKOUT IS A LATER FACT ABOUT A TRADE THAT ALREADY
            # HAPPENED. Failing to write one must cost that markout and
            # nothing else. The failure is counted and logged rather
            # than swallowed silently, and the horizon stays unmarked
            # so the next tick retries it.
            try:
                await xstore.record_markout(
                    pool, subject["experimentalDecisionId"],
                    subject["positionId"], out)
            except Exception as exc:                           # noqa: BLE001
                stats["writeFailed"] = stats.get("writeFailed", 0) + 1
                stats["writeError"] = "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:160])
                log.error("shadow_experimental: markout write failed for "
                          "%s %s: %s", subject["experimentalDecisionId"],
                          horizon, exc, exc_info=True)
                continue
            stats["observed" if out["status"] == mk.OBSERVED
                  else "notIdentified"] += 1
    return stats


async def tick(pool, *, now=None) -> dict:
    now = now or _now()
    stats = {"lane": LANE, "status": "ok"}
    # SAMPLE FIRST. The newest observation is what the seal half
    # decides on, so reading before sealing keeps the decision as close
    # to the book it was made on as this loop can manage.
    stats["sample"] = await sample_focus(pool, now=now)
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
                # §7: THE DIRECT WORKER IS NOW THE PRIMARY REGIME, and
                # the status line has to say so. It was a hardcoded
                # "GITHUB_BRIDGE" while the rows it was describing were
                # already being written DIRECT_INSTITUTIONAL_WORKER --
                # a boot line that contradicted its own ledger, and
                # exactly the sort of thing COMMAND would go on to
                # misclassify. The primary is named, the fallback is
                # named as a fallback, and neither is inferred.
                primaryLatencyRegime=DIRECT,
                fallbackLatencyRegime=xstore.REGIME_BRIDGE,
                marketDataMechanism=pmx.MARKET_DATA_MECHANISM,
                streamTarget=pmx.STREAM_TARGET,
                sealExpiryS=SEAL_EXPIRY_S,
                disclosure=xp.EXPERIMENTAL_DISCLOSURE)
    log.info("shadow_experimental: %s", boot)

    # IT RE-CHECKS, AND THAT MATTERS HERE. The API service runs the
    # migrations on ITS boot; this worker is a different service and
    # the two deploy in no guaranteed order. A wait loop that only
    # heartbeated would leave the lane dark until somebody restarted
    # it by hand -- minutes after the ALTER it was waiting for had
    # landed, and with nothing saying so.
    while not ready["storeReady"]:
        await heartbeat("shadow_experimental", "store_not_ready", boot)
        await asyncio.sleep(BACKOFF_S)
        try:
            ready = await xstore.store_ready(pool)
        except Exception as exc:                               # noqa: BLE001
            log.warning("shadow_experimental: store check failed: %s", exc)
            continue
        boot = dict(boot, storeReady=ready["storeReady"],
                    problems=ready["problems"])
        if ready["storeReady"]:
            # The registry is written down the moment the store can
            # take it, not at the next restart.
            frozen = await xstore.freeze_experiments(pool, reg.EXPERIMENTS)
            boot = dict(boot, experimentsWritten=frozen["written"])
            log.info("shadow_experimental: store became ready; %s", boot)

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
