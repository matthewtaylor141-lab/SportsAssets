"""THE SHADOW INTELLIGENCE RUNNER: one bounded, failure-isolated cycle every
CYCLE_S in the API process.

ARMED FROM api/app.py's lifespan. It is ON by default because it writes only
its own intel_* tables (plus an Audrey finding on a risk recompute
disagreement) and has no venue, order, sizing, limit or probability
authority; INTEL_SHADOW in {off, 0, false, no} is a kill switch that keeps
it from starting at all. Without migration 208 it idles.

BOUNDED. Every read has a lookback window and a row LIMIT; every component
runs inside its own savepoint with `SET LOCAL statement_timeout` and an
asyncio timeout; the pure computation of the heavier components runs off
the event loop (common.offload: the API's CPU lane); history in the intel_* tables is pruned
after store.RETENTION_DAYS.

FAILURE-ISOLATED. A component that raises or times out rolls back its own
savepoint, is recorded FAILED / TIMEOUT in intel_runs, and the components
after it run with that input marked unmeasured. Nothing propagates to the
API: the loop logs and sleeps.

ONE RUNNER AT A TIME. Each cycle takes a session advisory lock
(LOCK_KEY) on its connection and releases it before returning the
connection to the pool; an instance that does not get the lock skips the
cycle.

ORDER: CALIBRATION -> ATTRIBUTION -> RISK -> AUDREY_RISK -> REGIME ->
SIZING -> ALLOCATOR (each later one reads the earlier ones' results).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import allocator as AL
from . import attribution as AT
from . import calibration as CAL
from . import common as C
from . import regime as RG
from . import risk as RK
from . import sizing as SZ
from . import store as ST

log = logging.getLogger(__name__)

VERSION = "INTEL_RUNNER_V1"
ENV_KILL = "INTEL_SHADOW"
LOCK_KEY = 0x494E5431              # 'INT1'
CYCLE_S = 600.0
FIRST_DELAY_S = 120.0
COMPONENT_TIMEOUT_S = 90.0
STATEMENT_TIMEOUT_MS = 20000


def enabled() -> bool:
    return str(os.environ.get(ENV_KILL, "on")).strip().lower() not in (
        "off", "0", "false", "no")


async def _component(conn, run_id, name, fn, *, summary_of=None):
    """Run one component in its own savepoint; record the outcome."""
    started = time.time()
    value, status, error = None, "OK", None
    try:
        async with asyncio.timeout(COMPONENT_TIMEOUT_S):
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                value = await fn()
    except TimeoutError:
        status, error = "TIMEOUT", "component exceeded %ss" % (
            COMPONENT_TIMEOUT_S)
        value = None
    except Exception as exc:                                   # noqa: BLE001
        status, error = "FAILED", "%s: %s" % (type(exc).__name__,
                                              str(exc)[:300])
        value = None
        log.warning("intel shadow component %s failed", name, exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component=name, started_at=started,
                finished_at=time.time(), status=status, error=error,
                summary=(summary_of(value) if (summary_of and value
                                               is not None) else {}))
    except Exception:                                          # noqa: BLE001
        log.warning("intel shadow could not record %s", name, exc_info=True)
    return value, status


async def run_cycle(conn, *, now=None, account_id=C.PAPER_ACCOUNT,
                    experiment_id=None, slug_prefix=None,
                    include_actual=True) -> dict:
    """One full shadow cycle on `conn`. Returns what each component did."""
    if not await ST.tables_ready(conn):
        return {"ran": False, "why": "MIGRATION_208_NOT_APPLIED"}
    now = float(time.time() if now is None else now)
    run_id = ST.new_run_id()
    t0 = time.time()
    status: dict = {}

    async def calibration():
        recs = await CAL.load_records(conn, now=now, account_id=account_id,
                                      experiment_id=experiment_id)
        # OFF THE LOOP (RC6): `independent` walks every record and
        # `overlay_plan` can fit a Newton-Raphson overlay over thousands of
        # pairs; both are pure (see load_records for the production stalls)
        ind = await C.offload(CAL.independent, recs)
        ovs = await ST.overlays(conn)
        acts = await C.offload(CAL.overlay_plan, ind, ovs, now=now)
        await ST.apply_overlay_actions(conn, acts, now=now,
                                       protocol=CAL.PROTOCOL,
                                       method=CAL.OVERLAY_METHOD)
        ovs = await ST.overlays(conn)
        rep = await C.offload(CAL.report, ind, now=now, overlays=ovs)
        rep["records_read"] = len(recs)
        rep["overlay_actions"] = [{k: v for k, v in a.items()
                                   if k != "result"} for a in acts]
        await ST.save_calibration_segments(conn, run_id=run_id, now=now,
                                           segments=rep["segments"])
        await ST.save_snapshot(conn, run_id=run_id, component="CALIBRATION",
                               payload=rep, now=now, version=CAL.VERSION)
        return rep

    cal, status["CALIBRATION"] = await _component(
        conn, run_id, "CALIBRATION", calibration,
        summary_of=lambda r: {"segments": len(r["segments"]),
                              "records_read": r["records_read"]})

    async def attribution():
        rows = await AT.load_paper(conn, now=now, account_id=account_id)
        if include_actual:
            rows += await AT.load_actual(conn, now=now)
        await ST.upsert_attribution(conn, run_id=run_id, now=now, rows=rows)
        summ = C.envelope(version=AT.VERSION, computed_at=now,
                          summary=AT.summarize(rows), rows=len(rows))
        await ST.save_snapshot(conn, run_id=run_id, component="ATTRIBUTION",
                               payload=summ, now=now, version=AT.VERSION)
        return rows

    attr, status["ATTRIBUTION"] = await _component(
        conn, run_id, "ATTRIBUTION", attribution,
        summary_of=lambda r: {"rows": len(r)})

    async def risk():
        paper = await RK.paper_report(conn, now=now, account_id=account_id)
        await ST.save_snapshot(conn, run_id=run_id, component="RISK",
                               book="PAPER", payload=paper, now=now,
                               version=RK.VERSION)
        actual = None
        if include_actual:
            actual = await RK.actual_report(conn, now=now)
            await ST.save_snapshot(conn, run_id=run_id, component="RISK",
                                   book="ACTUAL", payload=actual, now=now,
                                   version=RK.VERSION)
        return {"PAPER": paper, "ACTUAL": actual}

    rk, status["RISK"] = await _component(
        conn, run_id, "RISK", risk,
        summary_of=lambda r: {b: (None if r[b] is None else {
            "open_positions": r[b]["open_positions"],
            "gross_exposure_usd": r[b]["gross_exposure_usd"]})
            for b in r})
    risk_paper = (rk or {}).get("PAPER")
    risk_actual = (rk or {}).get("ACTUAL")

    async def audrey():
        from ..agents import audrey_intel_risk as AR

        out = {}
        for book, rep in (("PAPER", risk_paper), ("ACTUAL", risk_actual)):
            if rep is None:
                continue
            out[book] = await AR.check(conn, run_id=run_id, book=book,
                                       primary=rep, now=now,
                                       account_id=account_id)
        return out

    if rk is not None:
        aud, status["AUDREY_RISK"] = await _component(
            conn, run_id, "AUDREY_RISK", audrey,
            summary_of=lambda r: {b: {"agrees": v["agrees"],
                                      "finding_id": v["finding_id"]}
                                  for b, v in r.items()})
    else:
        status["AUDREY_RISK"] = "SKIPPED"

    async def regime():
        rep = await RG.load_and_detect(
            conn, now=now, cal_report=cal, attributions=attr,
            account_id=account_id, experiment_id=experiment_id,
            slug_prefix=slug_prefix)
        await ST.save_regime(conn, run_id=run_id, now=now, regime=rep)
        await ST.save_snapshot(conn, run_id=run_id, component="REGIME",
                               payload=rep, now=now, version=RG.VERSION)
        return rep

    rg, status["REGIME"] = await _component(
        conn, run_id, "REGIME", regime,
        summary_of=lambda r: {"recommendation": r["recommendation"]})

    async def sizing():
        rows = await SZ.load_and_size(
            conn, now=now, cal_report=cal, attributions=attr,
            risk_paper=risk_paper,
            regime=(rg or {}).get("recommendation"), account_id=account_id)
        await ST.upsert_sizing(conn, run_id=run_id, now=now, rows=rows)
        await ST.save_snapshot(
            conn, run_id=run_id, component="SIZING", now=now,
            version=SZ.VERSION, payload=C.envelope(
                version=SZ.VERSION, computed_at=now, rows=len(rows),
                applied=False,
                shadow_usd_total=C.rnd(sum(r["shadow_usd"] or 0.0
                                           for r in rows)),
                regime=(rg or {}).get("recommendation")))
        return rows

    sz, status["SIZING"] = await _component(
        conn, run_id, "SIZING", sizing,
        summary_of=lambda r: {"rows": len(r)})

    async def allocator():
        rep = await AL.load_and_allocate(
            conn, now=now, sizing_rows=sz or [], risk_paper=risk_paper,
            risk_actual=risk_actual, regime=rg, cal_report=cal,
            account_id=account_id)
        if sz is None:
            rep["inputs"]["unmeasured"]["sizing"] = (
                "SIZING_FAILED_THIS_CYCLE_NO_NEW_DECISION_CANDIDATES")
        await ST.save_allocations(conn, run_id=run_id, now=now,
                                  ranked=rep["allocation"])
        await ST.save_snapshot(conn, run_id=run_id, component="ALLOCATOR",
                               payload=rep, now=now, version=AL.VERSION)
        return rep

    al, status["ALLOCATOR"] = await _component(
        conn, run_id, "ALLOCATOR", allocator,
        summary_of=lambda r: {"candidates": r["candidates"],
                              "allocated_usd": r["allocated_usd"]})

    try:
        async with conn.transaction():
            await ST.prune(conn, now=now)
    except Exception:                                          # noqa: BLE001
        log.warning("intel shadow prune failed", exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component="CYCLE", started_at=t0,
                finished_at=time.time(),
                status="OK" if all(v in ("OK", "SKIPPED")
                                   for v in status.values()) else "FAILED",
                summary={"components": status, "version": VERSION},
                version=VERSION)
    except Exception:                                          # noqa: BLE001
        log.warning("intel shadow could not record the cycle", exc_info=True)
    return {"ran": True, "run_id": run_id, "components": status,
            "label": C.LABEL}


async def _one(pool) -> dict:
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   LOCK_KEY):
            return {"ran": False, "why": "STANDBY_ANOTHER_RUNNER_HOLDS_LOCK"}
        try:
            return await run_cycle(conn)
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                log.warning("intel shadow unlock failed", exc_info=True)


async def run(get_pool) -> None:
    """The scheduled loop. Never raises into the API."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            pool = await get_pool()
            got = await _one(pool)
            log.info("intel shadow cycle: %s", {k: got.get(k) for k in (
                "ran", "run_id", "why", "components")})
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.warning("intel shadow cycle failed", exc_info=True)
        await asyncio.sleep(CYCLE_S)
