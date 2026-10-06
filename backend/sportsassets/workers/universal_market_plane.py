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
  * capacity is configured, never assumed: UMP_MAX_STREAMS x UMP_MAX_PER_STREAM
    (PMX documents 1,000 symbols per stream; the account's concurrent-stream
    allowance is not documented here). Overflow is named with the exact
    shards required for the whole subscribable universe.
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


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


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
        " WHERE active AND refdata IS NOT NULL "
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
                   "populate": {}, "catalogue": {}, "refdata": {}}
    while True:
        try:
            pool = await get_pool()
            now = time.time()
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
                    limit=REFDATA_PER_PASS * 8)) if mgr is not None else []
            sync = state.get("sync") or {}
            boot = {"attempted": 0, "stored": 0, "unlisted": 0, "failed": 0}
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
                    rec = (got or {}).get("record")
                    async with pool.acquire() as c:
                        if rec is None:
                            await R.save_unlisted(c, s_, at=now)
                            boot["unlisted"] += 1
                            continue
                        await R.save_refdata(c, s_, rec, at=now)
                    boot["stored"] += 1
                if len(attempted) > 200000:
                    attempted.clear()
                state["refdata"] = boot
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
                "fresh": len(fresh)})
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            if mgr is not None:
                mgr.stop()
            raise
        except Exception as exc:                                # noqa: BLE001
            log.exception("universal market plane pass failed")
            try:
                await heartbeat(SERVICE, "error",
                                {"error": type(exc).__name__})
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(10)


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
        "       count(*) AS total FROM market_plane_registry"))
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
    rad = RADAR.audit(
        venue_active=max(0, venue_active), registry_active=reg_active,
        subscribed=subscribed, fresh=len(fresh), coverage=cov,
        catalogue_complete=bool((state.get("catalogue") or {}).get(
            "complete")),
        shard_complete=bool(plan.get("complete", mgr is not None)),
        latency=lat if lat.get("n") else None)
    extra = []
    if mgr is None:
        extra.append("PMX_STREAMS_NOT_ARMED:%s" % arming.get("why"))
    if venue_active < 0:
        extra.append("VENUE_CATALOGUE_UNREADABLE")
    by = cov.get("by_state") or {}
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
        "coverage": {k: v for k, v in cov.items() if k != "rows"},
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
