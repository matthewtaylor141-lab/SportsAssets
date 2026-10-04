"""THE PROFITABILITY RUNNER: one bounded, failure-isolated cycle every
CYCLE_S in the API process (modelled on intel/runner.py).

ARMED FROM api/app.py's lifespan. ON by default because it writes only its
own append-only pos_* tables and has no venue, order, sizing, limit,
threshold or capital authority; POS_ECON in {off, 0, false, no} is the kill
switch that keeps it from starting at all. Without migration 216 it idles.

BOUNDED. Every read has a lookback window and a row LIMIT (reads.py); every
component runs inside its own savepoint with `SET LOCAL statement_timeout`
and an asyncio timeout; the pure computation of the heavier components runs
off the event loop (asyncio.to_thread). Writes are revisions only when
content changed, so the tables grow with events, not with cycles.

FAILURE-ISOLATED. A component that raises or times out rolls back its own
savepoint, is recorded FAILED / TIMEOUT in pos_runs, and the components
after it run with that input absent (and say so). Nothing propagates to the
API: the loop logs and sleeps.

ONE RUNNER AT A TIME: a session advisory lock (LOCK_KEY) per cycle; an
instance that does not get it skips the cycle.

ORDER: CAPACITY -> ECONOMICS -> WAREHOUSE -> CAPITAL -> NORTH_STAR ->
FORECAST (the warehouse references capacity rows; capital, metrics and the
forecast read the economics; the forecast reads the capacity aggregate).

PRODUCTION CONFIDENCE IS INVESTMENT-ONLY (migration 227). Every position
carries its group's durable sleeve (reads.attach_sleeve); NORTH_STAR writes
one row per (book, sleeve, strategy, metric), FORECAST one forecast per
(book, sleeve) -- each over its own rows, scored against its own sleeve.
The CAPACITY snapshot's top level is the PRODUCTION capacity (INVESTMENT
strategies, books within the executable freshness standard, INVESTMENT-only
rates); the research aggregate rides beside it under `research`. CAPITAL
keeps the shared-cash accounting lines and reports profit per sleeve.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import time

from .. import bettor_fee_schedule as FS
from . import capacity as CP
from . import common as C
from . import economics as EC
from . import forecast as FC
from . import metrics as MT
from . import reads as R
from . import store as ST
from . import warehouse as WH

log = logging.getLogger(__name__)

VERSION = "POS_RUNNER_V1"
ENV_KILL = "POS_ECON"
LOCK_KEY = 0x504F5331              # 'POS1'
CYCLE_S = 3600.0
FIRST_DELAY_S = 300.0
COMPONENT_TIMEOUT_S = 120.0
STATEMENT_TIMEOUT_MS = 20000
FEE_BASIS = ("PUBLISHED_POLYMARKET_US_TAKER_SCHEDULE_FOR_THE_DECISION_DATE_"
             "UNVERIFIED_AS_APPLIED")


def enabled() -> bool:
    return str(os.environ.get(ENV_KILL, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def fee_fn_for(epoch_s):
    """fee per contract at a price, by the published taker schedule of the
    decision's date; None when no schedule covers the date."""
    if epoch_s is None:
        return None
    try:
        sched = FS.for_date(_dt.datetime.fromtimestamp(
            float(epoch_s), _dt.timezone.utc).date().isoformat())
    except Exception:                                          # noqa: BLE001
        return None
    return lambda px: float(sched.exact(sched.theta_taker, 1, px))


async def _component(conn, run_id, name, fn, *, summary_of=None):
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
        log.warning("profitability component %s failed", name, exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component=name, started_at=started,
                finished_at=time.time(), status=status, error=error,
                summary=(summary_of(value) if (summary_of and value
                                               is not None) else {}))
    except Exception:                                          # noqa: BLE001
        log.warning("profitability could not record %s", name, exc_info=True)
    return value, status


async def run_cycle(conn, *, now=None, account_id=C.PAPER_ACCOUNT,
                    include_actual=True,
                    lookback_days=R.LOOKBACK_DAYS) -> dict:
    """One full research cycle on `conn`. Returns what each component did."""
    if not await ST.tables_ready(conn):
        return {"ran": False, "why": "MIGRATION_216_NOT_APPLIED"}
    now = float(time.time() if now is None else now)
    run_id = ST.new_run_id()
    t0 = time.time()
    status: dict = {}

    # ── CAPACITY ──────────────────────────────────────────────────────
    # Two aggregates, never pooled (owner audit 2026-10-04): the
    # PRODUCTION-CONFIDENCE capacity (top level of the snapshot, read by the
    # forecast's ceiling and the Opportunity Score's fill rate) counts only
    # INVESTMENT candidates whose book meets the strategy's executable
    # freshness standard, with INVESTMENT-only rates; the RESEARCH capacity
    # (every strategy, books up to CP.MAX_BOOK_AGE_S) is kept beside it,
    # labelled research.
    async def capacity():
        cands = await R.capacity_candidates(conn, now=now,
                                            account_id=account_id)
        books = await R.capacity_books(conn, cands)
        rows = []
        for c in cands:
            c["fee_basis"] = FEE_BASIS
            a = CP.assess(c, books.get(c["candidate_id"]),
                          fee_fn=fee_fn_for(c.get("decided_at")))
            # (R30A review) every persisted candidate row names its scope:
            # book, the decision strategy's sleeve (the classifier map; a
            # decision has no position group), strategy, the deciding
            # policy version, and the decision's own recorded book -- what
            # its executable freshness is judged against
            sleeve = C.strategy_sleeve(c.get("strategy"))
            a["detail"] = dict(a.get("detail") or {}, scope={
                "book": "PAPER", "sleeve": sleeve,
                "sleeve_basis": "THE_DECISION_STRATEGY_CLASSIFIER_MAP",
                "strategy": c.get("strategy"),
                "policy_version": c.get("policy_version"),
                "confidence_scope": C.confidence_scope(sleeve),
                "decision_book_obs_id": c.get("book_obs_id"),
                "decision_book_age_s": c.get("decision_book_age_s")})
            rows.append(a)
        await ST.save_capacity(conn, run_id=run_id, now=now, rows=rows,
                               fee_basis=FEE_BASIS)
        rates = await R.capacity_rates(conn, now=now, account_id=account_id)
        prod_rates = await R.capacity_rates(
            conn, now=now, account_id=account_id,
            strategies=C.INVESTMENT_STRATEGIES)
        recent = await R.capacity_recent(conn, now=now)

        def pvs(rs):
            # the deciding policy versions of the candidates an aggregate
            # counts (R30A review: every aggregate names them)
            return sorted({str(r["policy_version"]) for r in rs
                           if r.get("policy_version")})
        research = CP.aggregate(recent, rates=rates, scope={
            "confidence_scope": C.RESEARCH_SCOPE, "sleeve": None,
            "book": "PAPER", "strategies": "EVERY_PAPER_STRATEGY",
            "policy_versions": pvs(recent),
            "book_freshness": "RESEARCH_BOUND_%ds" % int(CP.MAX_BOOK_AGE_S),
            "why_research": ("accepts book observations up to %ds from the "
                             "decision -- older than any entry rule allows "
                             "-- and pools every sleeve" % int(
                                 CP.MAX_BOOK_AGE_S))})
        prod_rows = [CP.executable_view(r) for r in recent
                     if C.strategy_sleeve(r.get("strategy")) == C.INVESTMENT]
        agg = CP.aggregate(prod_rows, rates=prod_rates, scope={
            "confidence_scope": C.PRODUCTION, "sleeve": C.INVESTMENT,
            "book": "PAPER", "strategies": list(C.INVESTMENT_STRATEGIES),
            "policy_versions": pvs(prod_rows),
            "book_freshness": {
                "rule": "THE_STRATEGY_EXECUTABLE_FRESHNESS_STANDARD",
                "bounds_s": {s: CP.executable_bound(s)
                             for s in C.INVESTMENT_STRATEGIES},
                "basis": "the entry rule's book-age bound "
                         "(paper_benchmark.BOOK_MAX_AGE_S; for Derek, who "
                         "has no book-age refusal, the conservative "
                         "standard); a decision's own book by the age it "
                         "recorded, any other book only at or before the "
                         "decision"}})
        agg["computed_at"] = now
        agg["lookback_hours"] = R.CAPACITY_LOOKBACK_H
        research["computed_at"] = now
        research["lookback_hours"] = R.CAPACITY_LOOKBACK_H
        agg["research"] = research
        await ST.save_snapshot(conn, run_id=run_id, component="CAPACITY",
                               book="NONE", payload=agg, now=now,
                               version=CP.VERSION)
        return {"assessed": len(rows), "aggregate": agg}

    cap, status["CAPACITY"] = await _component(
        conn, run_id, "CAPACITY", capacity,
        summary_of=lambda r: {"assessed": r["assessed"],
                              "measured_markets":
                                  r["aggregate"]["measured_markets"]})
    cap_agg = (cap or {}).get("aggregate") or {}

    # ── ECONOMICS ─────────────────────────────────────────────────────
    loaded: dict = {}

    async def economics():
        lag = await R.settlement_lag_samples(conn, now=now)
        paper = await R.paper_positions(conn, now=now, account_id=account_id,
                                        days=lookback_days)
        actual = (await R.actual_positions(conn, now=now, days=lookback_days)
                  if include_actual else {"positions": [], "decisions": {}})
        loaded.update(paper=paper, actual=actual, lag=lag)
        positions = paper["positions"] + actual["positions"]

        def compute():
            rows, cfs = [], []
            for p in positions:
                e = EC.compute_position(p, lag_samples=lag)
                rows.append(e)
                cf = EC.counterfactual_hold(p, lag_samples=lag)
                if cf is not None:
                    cfs.append(cf)
            return rows, cfs

        rows, cfs = await asyncio.to_thread(compute)
        loaded.update(econ=rows, cf=cfs)
        return {"positions": len(rows), "counterfactuals": len(cfs)}

    eco, status["ECONOMICS"] = await _component(
        conn, run_id, "ECONOMICS", economics, summary_of=lambda r: r)

    # ── WAREHOUSE (lineage) + persist the economics against it ────────
    async def warehouse():
        recs = []
        by_key = {(e["book"], e["position_key"]): e for e in loaded["econ"]}
        for part, acct in (("paper", account_id), ("actual", None)):
            blk = loaded[part]
            if not blk["positions"]:
                continue
            ctx = await R.lineage_context(
                conn, positions=blk["positions"],
                decisions=blk["decisions"], account_id=acct or account_id,
                now=now)
            for p in blk["positions"]:
                recs.append(WH.build(p, by_key.get((p["book"],
                                                    p["position_key"])),
                                     ctx=ctx, decisions=blk["decisions"]))
        lin = await ST.save_lineage(conn, run_id=run_id, now=now,
                                    records=recs)
        lids = await ST.latest_lineage_ids(
            conn, [r["position_key"] for r in recs])
        econ = await ST.save_economics(
            conn, run_id=run_id, now=now,
            rows=loaded["econ"] + loaded["cf"], lineage_ids=lids)
        summ = WH.summary(recs)
        summ["computed_at"] = now
        await ST.save_snapshot(conn, run_id=run_id, component="WAREHOUSE",
                               book="NONE", payload=summ, now=now,
                               version=WH.VERSION)
        return {"positions": len(recs), "lineage_revisions": lin,
                "economics_revisions": econ}

    if eco is not None:
        wh, status["WAREHOUSE"] = await _component(
            conn, run_id, "WAREHOUSE", warehouse, summary_of=lambda r: r)
    else:
        wh, status["WAREHOUSE"] = None, "SKIPPED"

    # ── CAPITAL (portfolio, per book; COUNTERFACTUAL on its own) ──────
    async def capital():
        out = {}
        caps = {"PAPER": await R.paper_capital(conn, account_id=account_id),
                "ACTUAL": (await R.actual_capital(conn)) if include_actual
                else None}
        for book in C.BOOKS:
            if caps[book] is None:
                continue
            econs = [e for e in loaded["econ"] if e["book"] == book]
            k = caps[book]
            rep = EC.portfolio(
                econs, book=book, now=now,
                account_capital=k.get("account_capital"),
                account_capital_basis=k.get("basis"),
                open_reservations_usd=k.get("open_reservations_usd"),
                reservations_why=k.get("reservations_why"),
                lag_samples=loaded["lag"])
            # the book's capital lines are ACCOUNTING (one shared cash
            # ledger, every sleeve); its PROFIT lines are reported per
            # sleeve beside them, and the production-confidence profit is
            # the INVESTMENT sleeve's alone
            rep["confidence_scope"] = ("ACCOUNTING_ALL_SLEEVES_NOT_"
                                       "PRODUCTION_CONFIDENCE")
            rep["by_sleeve"] = {
                s: EC.sleeve_profit(loaded["econ"], book=book, sleeve=s,
                                    now=now, lag_samples=loaded["lag"])
                for s in C.SLEEVES}
            rep["production_confidence"] = rep["by_sleeve"][C.INVESTMENT]
            rep["computed_at"] = now
            await ST.save_snapshot(conn, run_id=run_id, component="CAPITAL",
                                   book=book, payload=rep, now=now,
                                   version=EC.VERSION)
            out[book] = rep
        cf = EC.counterfactual_summary(loaded["cf"])
        cf["computed_at"] = now
        await ST.save_snapshot(conn, run_id=run_id, component="CAPITAL",
                               book="COUNTERFACTUAL", payload=cf, now=now,
                               version=EC.VERSION)
        out["COUNTERFACTUAL"] = cf
        return out

    if eco is not None:
        _cap, status["CAPITAL"] = await _component(
            conn, run_id, "CAPITAL", capital,
            summary_of=lambda r: {b: r[b].get("capital_locked_usd")
                                  for b in r if b != "COUNTERFACTUAL"})
    else:
        status["CAPITAL"] = "SKIPPED"

    # ── NORTH STAR ────────────────────────────────────────────────────
    # Per book AND per scope (migration 227): every sleeve's aggregate and
    # every strategy present, each computed over ITS OWN rows only. The
    # INVESTMENT rows are production confidence; nothing pools sleeves.
    async def north_star():
        prev = await R.previous_metrics(conn, now=now,
                                        min_age_h=MT.TREND_MIN_AGE_H)
        shas = await R.latest_metric_shas(conn)
        books = [b for b in C.BOOKS if include_actual or b == "PAPER"]

        def compute():
            ms = []
            for b in books:
                for sleeve, strategy in C.scopes(loaded["econ"], book=b):
                    ms += MT.compute(loaded["econ"], book=b, now=now,
                                     lookback_days=lookback_days,
                                     sleeve=sleeve, strategy=strategy)
            return ms

        ms = await asyncio.to_thread(compute)
        for m in ms:
            m["trend"] = MT.trend(m, prev.get(ST.metric_key(m)))
        n = await ST.save_metrics(conn, run_id=run_id, now=now, metrics=ms,
                                  latest_shas=shas, version=MT.VERSION)
        return {"metrics": len(ms), "written": n}

    if eco is not None:
        ns, status["NORTH_STAR"] = await _component(
            conn, run_id, "NORTH_STAR", north_star, summary_of=lambda r: r)
    else:
        ns, status["NORTH_STAR"] = None, "SKIPPED"

    # ── FORECAST: score what has ended, then issue today's ────────────
    # One forecast per book AND sleeve (migration 227), each validated by
    # its own scored history and scored against its own sleeve. The
    # capacity ceiling is the PRODUCTION (INVESTMENT, executable-freshness)
    # capacity, so only the INVESTMENT forecast carries it.
    async def forecast():
        scored = 0
        for f in await R.unscored_forecasts(conn, now=now):
            real, n = await R.realized_between(
                conn, book=f["book"], start=f["hs"], end=f["he"],
                sleeve=f.get("sleeve"), strategy=f.get("strategy"))
            sc = FC.score(f, realized_pnl=real, realized_positions=n,
                          now=now)
            sc["detail"] = {"horizon_start": f["hs"], "horizon_end": f["he"],
                            "realized_basis": (
                                "pos_economics_latest net profit of positions "
                                "released inside the horizon" + (
                                    "" if f.get("sleeve") is None else
                                    " whose group's durable sleeve is %s"
                                    % f["sleeve"])),
                            "scope": f.get("sleeve") or C.BOOK_WIDE_PRE_227}
            await ST.save_score(conn, sc)
            scored += 1
        issued = {}
        daily = cap_agg.get("daily_executable_opportunity_dollars")
        fp = ((cap_agg.get("rates") or {}).get("fill_probability")
              or {}).get("value")
        for book in [b for b in C.BOOKS if include_actual or b == "PAPER"]:
            for sleeve in C.SLEEVES:
                scores = await R.forecast_scores(conn, book, sleeve,
                                                 C.ALL_STRATEGIES)
                prod = sleeve == C.INVESTMENT and book == "PAPER"

                def compute(book=book, sleeve=sleeve, scores=scores,
                            prod=prod):
                    return FC.build(
                        loaded["econ"], book=book, now=now,
                        lookback_days=lookback_days,
                        capacity_daily=daily if prod else None,
                        fill_probability=fp if prod else None,
                        scores=scores, sleeve=sleeve,
                        strategy=C.ALL_STRATEGIES,
                        capacity_why=None if prod else (
                            "CAPACITY_IS_MEASURED_FOR_THE_PAPER_INVESTMENT_"
                            "SLEEVE_ONLY"))

                fc = await asyncio.to_thread(compute)
                issued["%s:%s" % (book, sleeve)] = {
                    "status": fc["status"],
                    "forecast_id": await ST.save_forecast(
                        conn, run_id=run_id, fc=fc)}
        return {"scored": scored, "issued": issued}

    if eco is not None:
        fc, status["FORECAST"] = await _component(
            conn, run_id, "FORECAST", forecast, summary_of=lambda r: r)
    else:
        fc, status["FORECAST"] = None, "SKIPPED"

    # ── LOST OPPORTUNITY LEDGER + OPPORTUNITY SCORES (migration 220) ──
    # Its own savepoints, timeouts and run log (lol_runs); a failure there
    # never changes a component status above. POS_LOL=off is its switch.
    lol = None
    try:
        from ..lost_opportunity import runner as _LOL
        lol = await _LOL.run_component(
            conn, now=now, pos_run_id=run_id,
            econs=loaded.get("econ") if eco is not None else None,
            capacity_agg=cap_agg, lookback_days=lookback_days)
    except Exception:                                          # noqa: BLE001
        log.warning("lost opportunity component failed", exc_info=True)

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
        log.warning("profitability could not record the cycle",
                    exc_info=True)
    return {"ran": True, "run_id": run_id, "components": status,
            "label": C.LABEL, "authority": C.AUTHORITY,
            "results": {"capacity": (cap or {}).get("assessed"),
                        "economics": eco, "warehouse": wh,
                        "north_star": ns, "forecast": fc},
            "lost_opportunity": lol}


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
                log.warning("profitability unlock failed", exc_info=True)


async def run(get_pool) -> None:
    """The scheduled loop. Never raises into the API."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            if enabled():
                pool = await get_pool()
                got = await _one(pool)
                log.info("profitability cycle: %s", {k: got.get(k) for k in (
                    "ran", "run_id", "why", "components")})
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.warning("profitability cycle failed", exc_info=True)
        await asyncio.sleep(CYCLE_S)
