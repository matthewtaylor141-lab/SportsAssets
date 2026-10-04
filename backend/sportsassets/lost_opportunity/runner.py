"""THE LOST OPPORTUNITY COMPONENT: called by the profitability cycle
(profitability/runner.run_cycle) after FORECAST, on the same connection and
under the same advisory lock, every CYCLE_S. It has no loop of its own.

BOUNDED and FAILURE-ISOLATED, like the cycle's own components: LEDGER and
SCORES each run inside their own savepoint with `SET LOCAL
statement_timeout` and an asyncio timeout; a failure is recorded FAILED /
TIMEOUT in lol_runs and never propagates (the profitability components'
statuses are untouched). POS_LOL in {off, 0, false, no} is its kill switch;
without migration 220 it idles.

IDEMPOTENT: a decision already classified by this classifier version is
not read again (and the UNIQUE key refuses a duplicate anyway); a candidate
already scored at its capacity row is not re-scored.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from ..profitability import common as C
from . import classify as CL
from . import horizons as HZ
from . import reads as R
from . import score as SC
from . import store as ST

log = logging.getLogger(__name__)

VERSION = "LOL_RUNNER_V1"
ENV_KILL = "POS_LOL"
COMPONENT_TIMEOUT_S = 90.0
STATEMENT_TIMEOUT_MS = 20000


def enabled() -> bool:
    return str(os.environ.get(ENV_KILL, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def evidence_ids(dec: dict) -> list:
    """The decision-time record ids the classification read."""
    out = []
    if dec.get("decision_id"):
        out.append(str(dec["decision_id"]))
    if dec.get("coverage_row_id") is not None:
        out.append("ext_candidate_outcomes:%s" % dec["coverage_row_id"])
    if dec.get("valuation_id") is not None:
        out.append("external_valuations:%s" % dec["valuation_id"])
    if dec.get("book_obs_id") is not None:
        out.append("paper_book_observations:%s" % dec["book_obs_id"])
    mid = (dec.get("internal_model") or {}).get("model_id") \
        if isinstance(dec.get("internal_model"), dict) else None
    if mid:
        out.append("bettor_funded_models:%s" % mid)
    return out


def ledger_row(dec: dict) -> dict:
    """classify + hypothetical P&L -> one lol_ledger row (dict)."""
    got = CL.classify(dec)
    econ = got["economics"]
    st = dec["settlement"]
    hyp = CL.hypothetical_pnl(dec, econ, st)
    codes = CL.refusal_codes(dec)
    return {
        "decision_ref": dec["decision_ref"], "source": dec["source"],
        "classifier_version": CL.VERSION, "decided_at": dec["decided_at"],
        "strategy": dec.get("strategy"), "league": dec.get("league"),
        "league_basis": dec.get("league_basis"),
        "us_market_slug": dec.get("us_market_slug"),
        "holding_side": dec.get("holding_side"),
        "classification": got["classification"], "reason": got["reason"],
        "defect": got["defect"], "refusal": codes[0] if codes else None,
        "refusals": codes, "refusal_category": got["refusal_category"],
        "attribution": got["attribution"],
        "attribution_code": got["attribution_code"],
        "decision_evidence_ids": evidence_ids(dec),
        "decision_time_net_ev_usd": econ["net"],
        "decision_time_net_ev_basis": econ["basis"] or econ["why"],
        "decision_time_executable_price": econ["price"],
        "decision_time_qty": econ["qty"],
        "decision_time_fees_usd": econ["fees"],
        "decision_time_cost_usd": econ["cost"],
        "policy_min_gross_edge": econ["min_gross_edge"],
        "policy_min_net_ev_usd": econ["min_net_ev"],
        "settlement_evidence_id": st["settlement_id"],
        "settlement_basis": hyp["payout_basis"] or "PAPER_SETTLEMENT",
        "settled_at": st["settled_at"], "settlement_outcome": st["outcome"],
        "payout_per_contract": hyp["payout_per_contract"],
        "hypothetical_pnl_usd": hyp["value"],
        "hypothetical_pnl_why": hyp["why"],
        "detail": {"checks": got["checks"], "defects": got["defects"],
                   "categories": got["categories"],
                   "economics_positive": econ["positive"],
                   "economics_edge": econ["edge"],
                   "hypothetical_label": CL.HYPOTHETICAL,
                   "settlement_feeds_class": False,
                   "provider_event_id": dec.get("provider_event_id"),
                   "coverage_stage": dec.get("stage")}}


async def _component(conn, run_id, name, fn, *, pos_run_id=None):
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
    except Exception as exc:                                   # noqa: BLE001
        status, error = "FAILED", "%s: %s" % (type(exc).__name__,
                                              str(exc)[:300])
        value = None
        log.warning("lost opportunity component %s failed", name,
                    exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component=name, started_at=started,
                finished_at=time.time(), status=status, error=error,
                summary=value if isinstance(value, dict) else {},
                pos_run_id=pos_run_id)
    except Exception:                                          # noqa: BLE001
        log.warning("lost opportunity could not record %s", name,
                    exc_info=True)
    return value, status


async def run_component(conn, *, now=None, pos_run_id=None, econs=None,
                        capacity_agg=None, lookback_days=90.0) -> dict:
    """LEDGER, SCORES, then HORIZONS on `conn`. Never raises. `econs` are
    the cycle's freshly computed position economics (None when the
    cycle's ECONOMICS failed: HORIZONS is then SKIPPED)."""
    if not enabled():
        return {"ran": False, "why": "KILL_SWITCH_%s_OFF" % ENV_KILL}
    try:
        if not await ST.tables_ready(conn):
            return {"ran": False, "why": "MIGRATION_220_NOT_APPLIED"}
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "why": "READINESS_CHECK_FAILED: %s"
                % type(exc).__name__}
    now = float(time.time() if now is None else now)
    run_id = ST.new_run_id()
    status = {}

    async def ledger():
        decs = await R.settled_refusals(conn, now=now,
                                        classifier_version=CL.VERSION)
        decs += await R.coverage_refusals(conn, now=now,
                                          classifier_version=CL.VERSION)
        rows = [ledger_row(d) for d in decs]
        n = await ST.save_ledger(conn, run_id=run_id, now=now, rows=rows)
        by = {}
        for r in rows:
            by[r["classification"]] = by.get(r["classification"], 0) + 1
        return {"read": len(decs), "written": n, "by_class": by,
                "classifier_version": CL.VERSION}

    led, status["LEDGER"] = await _component(conn, run_id, "LEDGER", ledger,
                                             pos_run_id=pos_run_id)

    async def scores():
        cands = await R.score_candidates(conn, now=now, version=SC.VERSION)
        if not cands:
            return {"candidates": 0, "written": 0}
        since = min(c["decided_at"] for c in cands
                    if c["decided_at"] is not None) if any(
            c["decided_at"] is not None for c in cands) else now
        snaps = await R.snapshots_since(conn, since=since)
        lags = await R.lag_samples(conn, now=now)
        sctx = await R.score_context(conn, cands=cands, since=since)
        rows = []
        for c in cands:
            vid = c.get("valuation_id")
            val = sctx["valuations"].get(int(vid)) if vid is not None \
                else None
            cal = R.as_of(sctx["calibration"], c["decided_at"])
            reg = R.as_of(sctx["regimes"], c["decided_at"])
            ec = (sctx["edge_confidence"].get(int(vid)) if vid is not None
                  else None)
            ctx = {
                "calibration": None if cal is None else cal[1],
                "regime": None if reg is None else reg[1],
                "settlement_compatibility": (
                    (C.jload((val or {}).get("settlement_comparison"))
                     or {}).get("compatibility") if val else None),
                "edge_confidence": ec if ec is not None else {
                    "value": None, "why": sctx["edge_confidence_why"]
                    or "NO_EDGE_CONFIDENCE_FORECAST_FOR_THIS_VALUATION"}}
            cap = R.as_of(snaps["CAPITAL"], c["decided_at"])
            fpe = R.as_of(snaps["CAPACITY"], c["decided_at"])
            got = SC.score(
                c,
                fill_probability=None if fpe is None else fpe[1],
                fill_basis=("NO_CAPACITY_SNAPSHOT_AT_OR_BEFORE_DECISION"
                            if fpe is None else fpe[2]),
                idle_capital_usd=None if cap is None else cap[1],
                idle_capital_why=("NO_PAPER_CAPITAL_SNAPSHOT_AT_OR_BEFORE_"
                                  "DECISION" if cap is None else cap[2]),
                lag_samples=lags, ctx=ctx)
            got["detail"] = {
                "capital_snapshot_at": None if cap is None else cap[0],
                "capacity_snapshot_at": None if fpe is None else fpe[0],
                "as_of": "THE_DECISION_INSTANT",
                "score_basis": got.get("score_basis"),
                "capacity_ceiling_usd": C.num(c.get("capacity_ceiling_usd"))}
            rows.append(got)
        n = await ST.save_scores(conn, run_id=run_id, now=now, rows=rows,
                                 version=SC.VERSION)
        return {"candidates": len(cands), "written": n,
                "measured": sum(1 for r in rows
                                if r["status"] == C.MEASURED)}

    sc, status["SCORES"] = await _component(conn, run_id, "SCORES", scores,
                                            pos_run_id=pos_run_id)

    async def horizons():
        scored = 0
        for f in await R.unscored_horizons(conn, now=now):
            real, n = await R.realized_between(conn, book=f["book"],
                                               start=f["hs"], end=f["he"])
            await ST.save_horizon_score(conn, HZ.score(
                f, realized_pnl=real, realized_positions=n, now=now))
            scored += 1
        opp = await R.opportunity_counts(conn, now=now)
        caps = await R.capital_now(conn)
        agg = capacity_agg or {}
        daily = C.num(agg.get("daily_executable_opportunity_dollars"))
        fp = C.num(((agg.get("rates") or {}).get("fill_probability")
                    or {}).get("value"))
        issued = {}
        for book in C.BOOKS:
            for key, days in HZ.HORIZONS:
                sc_ = await R.horizon_scores(conn, book=book, horizon=key)
                fc = await asyncio.to_thread(
                    HZ.build, econs, book=book, horizon=key, days=days,
                    now=now, lookback_days=lookback_days, opportunity=opp,
                    capital=caps.get(book), capacity_daily=daily,
                    fill_probability=fp, scores=sc_)
                fid = await ST.save_horizon(conn, run_id=run_id, fc=fc)
                issued["%s:%s" % (book, key)] = {"status": fc["status"],
                                                 "forecast_id": fid}
        return {"scored": scored, "issued": issued}

    if econs is None:
        status["HORIZONS"] = "SKIPPED"
        hz = None
        try:
            async with conn.transaction():
                await ST.record_component(
                    conn, run_id=run_id, component="HORIZONS",
                    started_at=time.time(), finished_at=time.time(),
                    status="SKIPPED", error="NO_POSITION_ECONOMICS_THIS_CYCLE",
                    pos_run_id=pos_run_id)
        except Exception:                                      # noqa: BLE001
            log.warning("lost opportunity could not record HORIZONS",
                        exc_info=True)
    else:
        hz, status["HORIZONS"] = await _component(
            conn, run_id, "HORIZONS", horizons, pos_run_id=pos_run_id)
    return {"ran": True, "run_id": run_id, "components": status,
            "ledger": led, "scores": sc, "horizons": hz, "label": C.LABEL,
            "authority": C.AUTHORITY}
