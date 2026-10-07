"""Universal Market Plane supervisor (workers process, PARALLEL / SHADOW).

durable registry (from the complete venue catalogue) -> priority-ordered,
stable PMX shards -> canonical books -> coverage, certification, latency and
Radar, recorded append-only. No orders, no sizing, no authority.

PARALLEL FIRST (package APPLY.md): the legacy market-data lanes keep running
and keep serving every decision and held mark. This supervisor builds the
universal plane beside them and records its evidence so parity can be shown
before any reader is cut over.

INTEGRATION FIXES OVER THE DELIVERED SUPERVISOR (each pinned by a test):

  * the PMX streams are armed under the SAME conditions as the existing
    stream: INSTITUTIONAL_MD_STREAM on, the market-data identity guard passes
    (a refused credential opens no stream), the transport available, and the
    token-invalidation hook handed to every transport;
  * shard books are armed RUNNING (a fresh ResidentBooks refuses every read);
  * only contracts whose own refdata is held are subscribed (scales known),
    in priority order (open positions, candidates, core families soonest);
    an unlisted contract is recorded and re-asked after UNLISTED_RETRY_S, not
    every pass;
  * DEDICATED READ-ONLY RUNTIME (owner decision, 2026-10-06): ~74,500 active
    markets are NOT to be solved by raising UMP_MAX_STREAMS inside the shared
    workers process. The same supervisor runs standalone as its own
    read-only service: `python -m sportsassets.workers.universal_market_plane`
    with UMP_RUNTIME=DEDICATED_READ_ONLY (and UNIVERSAL_MARKET_PLANE=off on the
    shared workers so exactly one runtime holds the shards). Until the venue
    grants more institutional capacity the priority order binds: held
    positions / working management, imminent decisions, near-term core
    sports, the remaining active sports, futures / the long tail; overflow is
    always named with the streams full coverage would need;
  * capacity is configured, never assumed: UMP_MAX_STREAMS x UMP_MAX_PER_STREAM
    (PMX documents 1,000 symbols per stream; the account's concurrent-stream
    allowance is not documented here). Overflow is named with the exact
    shards required for the whole subscribable universe.

SETTLEMENT RULE REGISTRY (integration): the coverage pass also computes each
contract's settlement state from evidence (market_plane.settlement); the
snapshot carries the settlement counts, the bounded venue x sport x league x
family breakdown, the NOT_PROVEN -> PROVEN delta and the rules-text counts;
and a Kalshi SPORTS catalogue step (kalshi_catalogue: credential-free,
GET-only, cursor-complete, TRUNCATED by name) runs every KALSHI_EVERY_S in a
background thread, kill switch KALSHI_CATALOGUE=off. Radar still audits the
PMUS universe; Kalshi rows are counted beside it. No order module is
imported or reachable.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from ..db import get_pool, heartbeat
from ..market_plane import certification as CERT
from ..market_plane import freshness as FR
from ..market_plane import populate as POP
from ..market_plane import radar as RADAR
from ..market_plane import registry as R
from ..market_plane import refdata_progress as RP
from ..market_plane import rules as RULES
from ..market_plane.sharded_stream import Manager

log = logging.getLogger(__name__)
SERVICE = "universal_market_plane"
ENV_FLAG = "UNIVERSAL_MARKET_PLANE"

INTERVAL_S = 2.0
POPULATE_EVERY_S = 10.0
FULL_POPULATE_EVERY_S = 1800.0
ASSIGN_EVERY_S = 30.0
COVERAGE_EVERY_S = 120.0
CERTIFY_EVERY_S = 300.0
SNAPSHOT_EVERY_S = 60.0
REFDATA_PER_PASS = 8
REFDATA_PACING_S = 0.15
REFDATA_RETRY_S = 300.0
UNLISTED_RETRY_S = 6 * 3600.0
#: the held-mark SLA: a canonical book is "fresh" for coverage under the same
#: 300 s rule the held marks use (never wider)
FRESH_SLA_S = 300.0
DEFAULT_MAX_STREAMS = 4
DEFAULT_MAX_PER_STREAM = 1000
#: (settlement rule registry) the Kalshi SPORTS catalogue step: a
#: credential-free, GET-only, cursor-complete walk (kalshi_catalogue) every
#: KALSHI_EVERY_S, run in a background thread so the plane's pass never waits
#: on it, persisted into the registry (venue KALSHI) and market_plane_rules.
#: Kill switch KALSHI_CATALOGUE=off. No order path is imported or reachable.
KALSHI_ENV_FLAG = "KALSHI_CATALOGUE"
KALSHI_EVERY_S = 1800.0


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def kalshi_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KALSHI_ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


async def kalshi_step(pool, task, *, now: float, last: float, env=None,
                      walk=None) -> tuple:
    """ONE scheduling decision for the Kalshi catalogue: start a walk in a
    worker thread when due (and none is running), or persist a finished one.
    Returns (task, last_started, report-or-None). Never raises."""
    from .. import kalshi_catalogue as KC
    report = None
    if task is not None and task.done():
        try:
            res = task.result()
            async with pool.acquire() as c:
                persisted = await POP.populate_kalshi(c, res, now=now)
            report = dict(KC.summary(res), persisted=persisted,
                          finished_at=now)
        except Exception as exc:                                # noqa: BLE001
            report = {"error": type(exc).__name__, "finished_at": now,
                      "complete": False}
        task = None
    if task is None and kalshi_enabled(env) and now - last >= KALSHI_EVERY_S:
        task = asyncio.ensure_future(asyncio.to_thread(walk or KC.walk))
        last = now
    return task, last, report


def caps(env=None) -> tuple:
    env = os.environ if env is None else env

    def _i(k, d):
        try:
            v = int(str(env.get(k, d)))
            return v if v > 0 else d
        except (TypeError, ValueError):
            return d
    return (_i("UMP_MAX_STREAMS", DEFAULT_MAX_STREAMS),
            min(1000, _i("UMP_MAX_PER_STREAM", DEFAULT_MAX_PER_STREAM)))


def stream_arming(env=None, *, available=None) -> dict:
    """The same arming rule as institutional_stream.start_default: off unless
    INSTITUTIONAL_MD_STREAM is on; refused when the identity guard refuses the
    credential; unavailable without the transport. NEVER RAISES."""
    env = os.environ if env is None else env
    try:
        from .. import institutional_stream as IS
        from .. import market_data_identity as mdi
        if not IS.enabled(env):
            return {"armed": False, "why": "%s_IS_NOT_ON" % IS.ENV_FLAG}
        refusal = mdi.guard(mdi.PMX, env=env)
        if refusal is not None:
            return {"armed": False, "why": refusal}
        ok, why = (available() if available else IS.transport_available())
        if not ok:
            return {"armed": False, "why": "TRANSPORT_UNAVAILABLE: %s" % why}
        return {"armed": True, "why": None}
    except Exception as exc:                                    # noqa: BLE001
        return {"armed": False, "why": "ARMING_RAISED:%s" % type(exc).__name__}


def fresh_symbols(mgr, *, now: float) -> set:
    if mgr is None:
        return set()
    out = set()
    for s in mgr.subscribed():
        try:
            r = mgr.current(s, now=now, max_snapshot_age_s=FRESH_SLA_S)
        except Exception:                                       # noqa: BLE001
            continue
        if (r or {}).get("ok"):
            out.add(s)
    return out


def latency_report(mgr, *, now: float) -> dict:
    """The latency decomposition over the shards' recent updates: venue age
    (the venue's own cadence) apart from transport (venue clock -> our
    receipt) and processing (receipt -> canonical book replaced). The book is
    readable in this process the instant it is replaced, so distribution
    inside the process is zero by construction; cross-process distribution is
    not measured until a reader is cut over (named)."""
    samples = []
    for vts, recv, norm in (mgr.latency_samples() if mgr is not None else ()):
        d = FR.decompose(venue_at=vts, received_at=recv, normalized_at=norm,
                         distributed_at=norm, now=now)
        samples.append(d)
    rep = FR.summarize(samples)
    ages = [s["venue_age_ms"] for s in samples if s.get("venue_age_ms")
            is not None]
    rep["venue_age_ms"] = {"p50": FR.percentile(ages, .5),
                           "p95": FR.percentile(ages, .95), "n": len(ages),
                           "note": "the venue's own publishing cadence; not "
                                   "a BETTOR latency"}
    rep["distribution_scope"] = ("IN_PROCESS (book readable on replacement); "
                                 "CROSS_PROCESS_NOT_CUT_OVER")
    return rep


async def venue_active_count(conn, *, now: float) -> int:
    """Active SPORTS contracts the venue catalogue lists (non-sports leagues
    excluded by name)."""
    try:
        return int(await conn.fetchval(
            "SELECT count(DISTINCT market_slug) FROM us_premap "
            " WHERE market_slug IS NOT NULL "
            "   AND listing_state = ANY($1::text[]) "
            "   AND updated_at > to_timestamp($2) "
            "   AND NOT (lower(split_part(coalesce(event_slug,''),'-',2)) "
            "            = ANY($3::text[]))",
            list(POP.ACTIVE_LISTING_STATES), now - POP.ACTIVE_HORIZON_S,
            sorted(POP.O.NON_SPORTS_LEAGUES)) or 0)
    except Exception:                                           # noqa: BLE001
        return -1


async def certify(conn, mgr) -> dict:
    """DURABLE SAME-BOOK CERTIFICATION: the strict window evidence (30
    comparable samples at >= 95% agreement, same venue instant, exact
    identity -- unchanged) for each subscribed contract, persisted keyed by
    its identity fingerprint. A contradiction or a fall below the rule
    overwrites the certificate (not SUPPORTED); a changed fingerprint never
    reuses it."""
    from .. import institutional_same_book as SB
    out = {"evaluated": 0, "supported": 0, "accumulating": 0,
           "contradicted": 0}
    rows = await conn.fetch(
        "SELECT contract_id, refdata FROM market_plane_registry "
        " WHERE venue='POLYMARKET_US' AND active AND refdata IS NOT NULL "
        "   AND coalesce(refdata->>'unlisted','false') <> 'true' "
        "   AND subscription_shard IS NOT NULL")
    recs = {}
    for r in rows:
        rd = POP._jsonish(r["refdata"]) or {}
        recs[r["contract_id"]] = rd
    syms = sorted(recs)
    for i in range(0, len(syms), 500):
        chunk = syms[i:i + 500]
        ev = await SB.same_book_by_symbol(conn, chunk)
        for s in chunk:
            e = ev.get(s) or {"status": "UNTESTED", "detail": {}}
            det = e.get("detail") or {}
            rd = recs[s]
            ident = CERT.identity_for(
                s, price_scale=rd.get("priceScale") or rd.get("price_scale"),
                qty_scale=rd.get("fractionalQtyScale") or rd.get("qty_scale"))
            comparable = int(det.get("comparable") or 0)
            agree = int(det.get("agree_top_n") or 0) + int(
                det.get("agree_touch_only") or 0)
            cert = CERT.verdict(comparable=comparable, agreeing=agree,
                                identity=ident)
            supported = e.get("status") == "SUPPORTED" and \
                cert["status"] == "SUPPORTED"
            cert["status"] = "SUPPORTED" if supported else "ACCUMULATING"
            cert["window_status"] = e.get("status")
            cert["identity"] = ident
            await R.save_certification(conn, s, cert)
            out["evaluated"] += 1
            out["supported"] += int(supported)
            out["accumulating"] += int(not supported)
            out["contradicted"] += int(e.get("status") == "CONTRADICTED")
    return out


async def run() -> None:
    if not enabled():
        log.info("universal_market_plane: off by switch (%s)", ENV_FLAG)
        return
    max_streams, max_per = caps()
    arming = stream_arming()
    mgr, client = None, None
    if arming["armed"]:
        from .. import pmx_institutional as PMX
        client = PMX.Institutional(env=os.environ)
        mgr = Manager(token_fn=client.token, max_per_stream=max_per,
                      max_streams=max_streams,
                      invalidate_token=client.invalidate_token)
    log.info("universal_market_plane: streams %s (%s) caps=%dx%d",
             "ARMED" if mgr else "NOT_ARMED", arming["why"], max_streams,
             max_per)
    watermark = 0.0
    last = {"populate": 0.0, "full": 0.0, "assign": 0.0, "coverage": 0.0,
            "certify": 0.0, "snapshot": 0.0}
    seen_receipts: dict = {}
    attempted: dict = {}
    state: dict = {"plan": {}, "coverage": {}, "certification": {},
                   "populate": {}, "catalogue": {}, "refdata": {},
                   "kalshi": {"enabled": kalshi_enabled()}}
    kalshi_task, kalshi_last = None, 0.0
    while True:
        try:
            pool = await get_pool()
            now = time.time()
            kalshi_task, kalshi_last, krep = await kalshi_step(
                pool, kalshi_task, now=now, last=kalshi_last)
            if krep is not None:
                state["kalshi"] = dict(krep, enabled=kalshi_enabled())
            async with pool.acquire() as c:
                cat = await POP.catalogue_completeness(c)
                state["catalogue"] = cat
                ids = {k: v.get("receipt_id")
                       for k, v in (cat.get("lanes") or {}).items()}
                new_receipt = ids != seen_receipts
                full = (now - last["full"] >= FULL_POPULATE_EVERY_S
                        or (new_receipt and ids.get("full") !=
                            seen_receipts.get("full")))
                if full or now - last["populate"] >= POPULATE_EVERY_S:
                    pop = await POP.populate(c, since=watermark, now=now,
                                             full=full)
                    watermark = max(watermark, pop.get("watermark") or 0.0)
                    state["populate"] = pop
                    last["populate"] = now
                    if full:
                        last["full"] = now
                        seen_receipts = ids
                assigned_rows = None
                if mgr is not None and now - last["assign"] >= ASSIGN_EVERY_S:
                    state["plan"] = await R.assign_missing_shards(
                        c, max_per_stream=max_per, max_streams=max_streams)
                    last["assign"] = now
                    assigned_rows = await R.assigned_contracts(c)
                due_rows = (await R.refdata_due(
                    c, now=now, unlisted_retry_s=UNLISTED_RETRY_S,
                    limit=REFDATA_PER_PASS * 8,
                    excluded=RP.cooling_ids(attempted, now=now,
                                             retry_s=REFDATA_RETRY_S))) if mgr is not None else []
            sync = state.get("sync") or {}
            boot = {"attempted": 0, "stored": 0, "unlisted": 0, "failed": 0, "failures_by_reason": {}}
            if mgr is not None:
                if assigned_rows is not None:
                    assignments = {r["contract_id"]: r["subscription_shard"]
                                   for r in assigned_rows}
                    instruments = {r["contract_id"]: POP._jsonish(
                        r["refdata"]) for r in assigned_rows}
                    sync = mgr.sync(assignments, instruments)
                    state["sync"] = sync
                # bounded refdata catch-up, priority order; a failed read is
                # retried after REFDATA_RETRY_S, an unlisted one after
                # UNLISTED_RETRY_S (refdata_due filters by refdata_at)
                due = [s_ for s_ in due_rows
                       if now - attempted.get(s_, 0.0) >= REFDATA_RETRY_S
                       ][:REFDATA_PER_PASS]
                from .institutional_md import bootstrap_instrument
                for s_ in due:
                    attempted[s_] = now
                    boot["attempted"] += 1
                    try:
                        got = await asyncio.to_thread(bootstrap_instrument,
                                                      client, s_)
                    except Exception:                           # noqa: BLE001
                        boot["failed"] += 1
                        continue
                    finally:
                        await asyncio.sleep(REFDATA_PACING_S)
                    checked = RP.classify_bootstrap(s_, got)
                    if checked["state"] == "RETRY":
                        boot["failed"] += 1
                        why = checked["why"]
                        boot["failures_by_reason"][why] = boot["failures_by_reason"].get(why, 0) + 1
                        continue
                    async with pool.acquire() as c:
                        if checked["state"] == "UNLISTED":
                            await R.save_unlisted(c, s_, at=time.time())
                            boot["unlisted"] += 1
                            continue
                        await R.save_refdata(c, s_, checked["record"], at=time.time())
                    boot["stored"] += 1
                if len(attempted) > 200000:
                    attempted.clear()
                state["refdata"] = boot
            now = time.time()  # source ages checked AFTER the catch-up work
            fresh = fresh_symbols(mgr, now=now)
            async with pool.acquire() as c:
                if now - last["coverage"] >= COVERAGE_EVERY_S:
                    state["coverage"] = await POP.coverage_pass(
                        c, fresh_symbols=fresh, now=now)
                    last["coverage"] = now
                if mgr is not None and \
                        now - last["certify"] >= CERTIFY_EVERY_S:
                    state["certification"] = await certify(c, mgr)
                    last["certify"] = now
                if now - last["snapshot"] >= SNAPSHOT_EVERY_S:
                    snap = await snapshot(c, mgr, state, now=now,
                                          arming=arming, fresh=fresh,
                                          caps=(max_streams, max_per))
                    await R.record_event(
                        c, "SNAPSHOT", "snapshot:%d" % int(now // 60), snap)
                    state["last_snapshot"] = snap
                    last["snapshot"] = now
            status = "ok" if (state.get("last_snapshot") or {}).get(
                "radar", {}).get("green") else "degraded"
            await heartbeat(SERVICE, status, {
                "arming": arming, "plan": state.get("plan"),
                "sync": sync, "refdata": boot,
                "populate": {k: v for k, v in (state.get("populate") or {})
                             .items() if k != "excluded"},
                "kalshi": {k: (state.get("kalshi") or {}).get(k) for k in (
                    "enabled", "complete", "stopped", "markets", "requests",
                    "error")},
                "fresh": len(fresh)})
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            if mgr is not None:
                mgr.stop()
            if kalshi_task is not None:
                kalshi_task.cancel()
            raise
        except Exception as exc:                                # noqa: BLE001
            log.exception("universal market plane pass failed")
            try:
                await heartbeat(SERVICE, "error",
                                {"error": type(exc).__name__})
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(10)


async def freshness_denominators(conn, cov: dict, reg: dict, plan: dict, *,
                                 subscribed: int, fresh: int,
                                 now: float) -> dict:
    """THE TWO DENOMINATORS, never blended (owner, 2026-10-06).

      priority_universe   open PAPER positions + evaluated candidates (the
                          capital-required markets): current via the PMX
                          stream or the REST recovery read inside the 300 s
                          SLA, over all of them (external named apart)
      held_positions      the management truth: bettor_paper_freshness over
                          the account's open positions (markable = all but
                          EXTERNAL_UNAVAILABLE with venue evidence)
      total_universe      every active sports contract: subscription
                          eligible, streamed, current via fallback, stale,
                          overflow, external -- nothing excluded to raise it
    """
    tiers = (cov or {}).get("freshness_tiers") or {}
    pr = tiers.get("PRIORITY") or {}
    al = tiers.get("ALL") or {}

    def rate(t):
        den = int(t.get("total") or 0) - int(t.get(
            "EXTERNAL_DATA_UNAVAILABLE") or 0)
        cur = int(t.get("PMX_GRPC") or 0) + int(t.get("REST_RECOVERY") or 0)
        return None if den <= 0 else round(cur / den, 4)
    # THE HELD-POSITION DENOMINATOR IS READ BY THE API, NOT HERE: this
    # worker is a started loop and must not import the paper ledger (whose
    # import graph reaches venue-write layers -- test_workers_hold_no_venue_
    # write / test_paper_records_cannot_reach_the_funded_path). GET
    # /api/command/market-plane fills it from bettor_paper_freshness.read.
    held = {"status": "READ_BY_THE_API", "rate": None,
            "source": "GET /api/command/market-plane (bettor_paper_freshness"
                      ".read: FRESH + QUIET_VALID / markable; 300 s SLA)"}
    total_active = int(reg.get("active") or 0)
    streamed_fresh = int(fresh)
    via_fallback = int(al.get("REST_RECOVERY") or 0)
    ext = int(al.get("EXTERNAL_DATA_UNAVAILABLE") or 0)
    current = int(al.get("PMX_GRPC") or 0) + via_fallback
    return {
        "priority_universe": {
            "denominator": int(pr.get("total") or 0),
            "current_pmx_stream": int(pr.get("PMX_GRPC") or 0),
            "current_rest_fallback": int(pr.get("REST_RECOVERY") or 0),
            "not_current": int(pr.get("NONE") or 0),
            "external_unavailable": int(pr.get(
                "EXTERNAL_DATA_UNAVAILABLE") or 0),
            "rate": rate(pr), "target": 0.95,
            "members": "OPEN_PAPER_POSITION + EVALUATED_CANDIDATE (6 h)"},
        "held_positions": held,
        "total_universe": {
            "active_contracts": total_active,
            "subscription_eligible": int(reg.get("pmx_listed") or 0),
            "pmx_unlisted": int(reg.get("pmx_unlisted") or 0),
            "refdata_pending": int(reg.get("refdata_pending") or 0),
            "streamed": int(subscribed),
            "streamed_current": streamed_fresh,
            "current_via_fallback": via_fallback,
            "stale_or_unread": max(0, total_active - current - ext),
            "overflow": plan.get("overflow_count"),
            "external_unavailable": ext,
            "rate": (round(current / max(1, total_active - ext), 4)
                     if total_active > ext else None)}}


async def snapshot(conn, mgr, state: dict, *, now: float, arming: dict,
                   fresh: set, caps: tuple) -> dict:
    """ONE append-only market-plane snapshot: universe, registry, coverage,
    subscription plan, sources, freshness, latency, certification, catalogue
    completeness and Radar. Read by GET /api/command/market-plane."""
    venue_active = await venue_active_count(conn, now=now)
    reg = dict(await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE active) AS active, "
        "       count(*) FILTER (WHERE active AND sport IS NOT NULL) "
        "           AS active_sports, "
        "       count(*) FILTER (WHERE active AND required_reason = "
        "                        'VENUE_ACTIVE') AS venue_listed, "
        "       count(*) FILTER (WHERE active AND required_reason IN "
        "           ('OPEN_PAPER_POSITION','EVALUATED_CANDIDATE')) "
        "           AS required, "
        "       count(*) FILTER (WHERE active AND refdata IS NOT NULL AND "
        "           coalesce(refdata->>'unlisted','false') <> 'true') "
        "           AS pmx_listed, "
        "       count(*) FILTER (WHERE active AND "
        "           refdata->>'unlisted' = 'true') AS pmx_unlisted, "
        "       count(*) FILTER (WHERE active AND refdata IS NULL) "
        "           AS refdata_pending, "
        "       count(*) FILTER (WHERE active AND subscription_shard IS NOT "
        "           NULL) AS assigned, "
        "       count(*) AS total FROM market_plane_registry "
        # the PMUS universe (the streams, the catalogue and Radar are its);
        # Kalshi rows are counted on their own below
        " WHERE venue = 'POLYMARKET_US'"))
    kalshi_reg = dict(await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE active) AS active, count(*) AS total "
        "  FROM market_plane_registry WHERE venue = 'KALSHI'"))
    rules = await RULES.rules_counts(conn)
    cert = dict(await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'SUPPORTED') AS supported, "
        "       count(*) FILTER (WHERE status = 'ACCUMULATING') "
        "           AS accumulating, count(*) AS total "
        "  FROM market_plane_certification"))
    cov = state.get("coverage") or {}
    plan = state.get("plan") or {}
    lat = latency_report(mgr, now=now)
    subscribed = len(mgr.subscribed()) if mgr is not None else 0
    reg_active = int(reg.get("active") or 0)
    # Radar audits the PMUS universe: its coverage counts only (a Kalshi row
    # is a named ontology gap of another venue, reported beside it)
    pm_cov = ({"by_state": (cov.get("by_venue") or {})["POLYMARKET_US"]}
              if "POLYMARKET_US" in (cov.get("by_venue") or {}) else cov)
    rad = RADAR.audit(
        venue_active=max(0, venue_active), registry_active=reg_active,
        subscribed=subscribed, fresh=len(fresh), coverage=pm_cov,
        catalogue_complete=bool((state.get("catalogue") or {}).get(
            "complete")),
        shard_complete=bool(plan.get("complete", mgr is not None)),
        latency=lat if lat.get("n") else None)
    extra = []
    if mgr is None:
        extra.append("PMX_STREAMS_NOT_ARMED:%s" % arming.get("why"))
    if venue_active < 0:
        extra.append("VENUE_CATALOGUE_UNREADABLE")
    by = pm_cov.get("by_state") or {}
    sbs = ((cov.get("settlement") or {}).get("by_venue") or {}).get(
        "POLYMARKET_US") or {}
    for k in ("SETTLEMENT_RULE_EVIDENCE_CONFLICT",
              "EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE"):
        if sbs.get(k):
            extra.append("%s:%d" % (k, sbs[k]))
    if by.get("MAPPED_BUT_SETTLEMENT_NOT_PROVEN"):
        extra.append("SETTLEMENT_PROOF_MISSING:%d"
                     % by["MAPPED_BUT_SETTLEMENT_NOT_PROVEN"])
    if by.get("MAPPED_BUT_NO_FAIR_VALUE_SOURCE"):
        extra.append("FAIR_VALUE_SOURCE_MISSING:%d"
                     % by["MAPPED_BUT_NO_FAIR_VALUE_SOURCE"])
    stale = max(0, subscribed - len(fresh))
    if stale:
        extra.append("SUBSCRIBED_BUT_NOT_FRESH:%d" % stale)
    if reg_active < max(0, venue_active):
        extra.append("AGENT_VISIBLE_BELOW_VENUE:%d"
                     % (max(0, venue_active) - reg_active))
    rad["extra_findings"] = extra
    max_streams, max_per = caps
    # CAPACITY, explicit (owner): streams, symbol capacity, subscribed,
    # overflow and the streams the whole subscribable universe would need
    rad["capacity"] = {
        "streams_open": len(mgr.shard_digest()) if mgr is not None else 0,
        "max_streams": max_streams, "max_per_stream": max_per,
        "symbol_capacity": max_streams * max_per,
        "subscribed": subscribed,
        "subscribable": plan.get("subscribable"),
        "overflow": plan.get("overflow_count"),
        "streams_required_for_full_coverage": plan.get(
            "shards_required_for_all"),
        "runtime": os.environ.get("UMP_RUNTIME", "SHARED_WORKERS")}
    freshness = await freshness_denominators(conn, cov, reg, plan,
                                             subscribed=subscribed,
                                             fresh=len(fresh), now=now)
    rad["capacity"].update(RP.capacity_view(
        active=reg_active, subscribable=int(reg.get("pmx_listed") or 0),
        pending=int(reg.get("refdata_pending") or 0),
        subscribed=subscribed, max_streams=max_streams,
        max_per_stream=max_per))
    rad["freshness"] = {k: (v.get("rate") if isinstance(v, dict) else None)
                        for k, v in freshness.items()}
    return {
        "computed_at": now, "version": "UNIVERSAL_MARKET_PLANE_SNAPSHOT_V1",
        "authority": R.AUTHORITY,
        "universe": {"venue_active_sports_contracts": venue_active,
                     "registry": reg,
                     "represented": reg_active,
                     "coverage_pct": (round(100.0 * min(reg_active,
                                                        venue_active)
                                            / venue_active, 3)
                                      if venue_active > 0 else None)},
        "coverage": {k: v for k, v in cov.items() if k not in (
            "rows", "settlement")},
        # (settlement rule registry) the settlement state of every active
        # contract, its venue x sport x league x family breakdown (bounded),
        # this pass's NOT_PROVEN -> PROVEN delta, and the rules-text counts
        "settlement": cov.get("settlement"),
        "rules": rules,
        "kalshi": dict(state.get("kalshi") or {}, registry=kalshi_reg),
        "subscription": {"armed": mgr is not None,
                         "arming_why": arming.get("why"),
                         "configured_capacity": {"max_streams": max_streams,
                                                 "max_per_stream": max_per,
                                                 "symbols": max_streams
                                                 * max_per},
                         "plan": plan,
                         "shards": mgr.shard_digest() if mgr else [],
                         "subscribed": subscribed, "fresh": len(fresh),
                         "stale_subscribed": stale},
        "sources": cov.get("source_counts"),
        "freshness": freshness,
        "latency": lat, "certification": dict(
            cert, last_pass=state.get("certification")),
        "catalogue": state.get("catalogue"),
        "populate": {k: v for k, v in (state.get("populate") or {}).items()},
        "refdata": state.get("refdata"),
        "radar": rad}


async def main():
    await run()


if __name__ == "__main__":
    asyncio.run(main())
