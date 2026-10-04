"""THE TWIN / PROFITABILITY RESEARCH RUNNER: one bounded, failure-isolated
cycle every CYCLE_S in the API process.

ARMED FROM api/app.py's lifespan. It is ON by default because it writes only
its own twin_* tables and has NO authority -- no venue, order, sizing,
limit, control or activation effect; a kill-switch criterion records
RECOMMEND_PAUSE and nothing else. POS_TWIN in {off, 0, false, no} is the
kill switch that keeps it from starting at all. Without migration 219 it
idles.

BOUNDED. Every read has a window (WINDOW_DAYS) and a row LIMIT; each
component runs in its own savepoint with `SET LOCAL statement_timeout` and
an asyncio timeout; the pure replay runs off the event loop
(asyncio.to_thread). Append-only growth is bounded by content
de-duplication (an unchanged input set is not re-inserted).

FAILURE-ISOLATED. A component that raises or times out rolls back its own
savepoint and is recorded FAILED / TIMEOUT in twin_runs; the components
after it run with that input marked unmeasured. Nothing propagates to the
API: the loop logs and sleeps.

ONE RUNNER AT A TIME: a session advisory lock (LOCK_KEY) per cycle.

ORDER: TWIN -> TRANSFER -> SCORECARDS -> LADDER -> KILL_SWITCHES -> EVALS.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import common as C
from . import engine as E
from . import evals as EV
from . import killswitch as KS
from . import ladder as LD
from . import reads as R
from . import scenarios as SC
from . import scorecards as SCD
from . import store as ST
from . import transfer as TR

log = logging.getLogger(__name__)

VERSION = "TWIN_RUNNER_V1"
ENV_KILL = "POS_TWIN"
LOCK_KEY = 0x54574E31              # 'TWN1'
CYCLE_S = 6 * 3600.0                # research cadence: four cycles a day
FIRST_DELAY_S = 300.0
COMPONENT_TIMEOUT_S = 120.0
STATEMENT_TIMEOUT_MS = 20000
WINDOW_DAYS = 60.0


def enabled() -> bool:
    return str(os.environ.get(ENV_KILL, "on")).strip().lower() not in (
        "off", "0", "false", "no")


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
        log.warning("twin component %s failed", name, exc_info=True)
    try:
        async with conn.transaction():
            await ST.record_component(
                conn, run_id=run_id, component=name, started_at=started,
                finished_at=time.time(), status=status, error=error,
                summary=(summary_of(value) if (summary_of and value
                                               is not None) else {}))
    except Exception:                                          # noqa: BLE001
        log.warning("twin could not record %s", name, exc_info=True)
    return value, status


# ═════════════════════════════════════════════════════════════════════
# THE RECORDED STREAM
# ═════════════════════════════════════════════════════════════════════

def _book_instants(positions: list, karen: list) -> list:
    items = []
    for p in positions:
        if p["entry_at"] is None:
            continue
        items.append((p["slug"], p["entry_at"]))
        th = p.get("thesis")
        if th and th.get("at") is not None:
            items.append((p["slug"], max(p["entry_at"], th["at"])))
        ids = {p["decision_id"], p["group_id"]}
        for k in karen:
            refs = {str(x.get("id")) for x in (C.jload(k.get(
                "evidence_refs")) or []) if isinstance(x, dict)}
            if k.get("target_id") in ids or refs & ids:
                items.append((p["slug"], k["at"]))
    return items


async def build_streams(conn, *, now, since, account_id,
                        include_actual=True) -> dict:
    raw = await R.paper_raw(conn, since=since, until=now,
                            account_id=account_id)
    act = (await R.actual_raw(conn, since=since, until=now)
           if include_actual else None)
    vids = [d.get("valuation_id") for d in raw["decisions"]]
    slugs = [d.get("us_market_slug") for d in raw["decisions"]]
    gids = list(raw["groups"].values())
    if act:
        vids += [i.get("valuation_id") for i in act["intents"]]
        slugs += [i.get("us_market_slug") for i in act["intents"]]
        gids += [i.get("group_id") for i in act["intents"]]
    vals = await R.valuations(conn, vids)
    prem = await R.premap(conn, slugs)
    theses = await R.theses(conn, gids)
    paper = E.build_paper(raw, vals=vals, prem=prem, theses=theses)
    actual = (E.build_actual(act, vals=vals, prem=prem, theses=theses)
              if act else {"opps": [], "positions": [], "oracle": {}})
    karen = await R.karen_blocks(conn, since=since, until=now)
    regimes = await R.regimes(conn, since=since, until=now)
    dids = [o["subject_id"] for o in paper["opps"] + actual["opps"]]
    allocs = await R.allocations(conn, dids, since=since, until=now)
    eddie, ew = await R.iface(conn, "EDDIE", since=since, until=now)
    scout, sw = await R.iface(conn, "SCOUT", since=since, until=now)
    books = await R.point_books(conn, _book_instants(
        paper["positions"] + actual["positions"], karen))
    why = {"EDDIE": ew, "SCOUT": sw}
    out = {}
    for basis, b in (("PAPER", paper), ("ACTUAL", actual)):
        if basis == "ACTUAL" and not include_actual:
            continue
        out[basis] = E.Stream(
            basis=basis, opps=b["opps"], positions=b["positions"],
            oracle=b["oracle"], books=books, regimes=regimes,
            allocations=allocs, karen=karen, eddie=eddie, scout=scout,
            iface_why=why, window=(since, now))
    return {"streams": out, "regimes": regimes, "eddie": (eddie, ew),
            "scout": (scout, sw)}


def _replay(streams: dict, scenarios: list) -> list:
    out = []
    for basis in sorted(streams):
        for sc in scenarios:
            out.append((sc, basis, E.run_scenario(streams[basis], sc)))
    return out


def _summary(sc: dict, body: dict) -> dict:
    w, b = body.get("world") or {}, body.get("baseline") or {}
    pick = ("label", "subjects", "scored", "unscored", "total_pnl_usd",
            "mean_pnl_usd", "ci", "capital_usd", "return_on_capital",
            "max_drawdown_usd", "pnl_per_capital_hour", "unmeasured")
    return {"scenario_id": sc["scenario_id"],
            "scenario_key": sc["scenario_key"], "version": sc["version"],
            "world_kind": sc["world"], "basis_book": body["basis_book"],
            "result_id": ST.result_id(body), "status": body["status"],
            "unavailable_reason": body["unavailable_reason"],
            "input_sha256": body["input_sha256"],
            "output_sha256": body["output_sha256"],
            "baseline": {k: b.get(k) for k in pick} if b else None,
            "world": {k: w.get(k) for k in pick} if w else None,
            "comparison": body.get("comparison")}


# ═════════════════════════════════════════════════════════════════════
# THE CYCLE
# ═════════════════════════════════════════════════════════════════════

async def run_cycle(conn, *, now=None, account_id=C.PAPER_ACCOUNT,
                    include_actual=True, window_days=WINDOW_DAYS) -> dict:
    if not await ST.tables_ready(conn):
        return {"ran": False, "why": "MIGRATION_219_NOT_APPLIED"}
    now = float(time.time() if now is None else now)
    since = now - float(window_days) * 86400.0
    run_id = ST.new_run_id()
    t0 = time.time()
    status: dict = {}
    ctx: dict = {}

    async def twin():
        got = await build_streams(conn, now=now, since=since,
                                  account_id=account_id,
                                  include_actual=include_actual)
        cat = SC.catalog()
        await ST.register_scenarios(conn, cat, now=now,
                                    engine_version=E.VERSION)
        done = await asyncio.to_thread(_replay, got["streams"], cat)
        results, traces, summaries, inserted = {}, {}, [], 0
        for sc, basis, r in done:
            rid = await ST.save_result(conn, run_id=run_id, now=now,
                                       body=r["body"], traces=r["traces"])
            inserted += rid is not None
            results.setdefault(sc["scenario_key"], {})[basis] = r["body"]
            traces[(sc["scenario_key"], basis)] = r["rows"]
            summaries.append(_summary(sc, r["body"]))
        payload = C.envelope(
            version=E.VERSION, computed_at=now, run_id=run_id,
            window={"start": since, "end": now},
            research_only=True, production_truth=False,
            summed_across_books=False,
            inputs={b: s.input_sha() for b, s in got["streams"].items()},
            interfaces={"EDDIE": got["eddie"][1], "SCOUT": got["scout"][1]},
            scenarios=summaries)
        await ST.save_snapshot(conn, run_id=run_id, component="TWIN",
                               payload=payload, now=now, version=E.VERSION)
        ctx.update(streams=got["streams"], regimes=got["regimes"],
                   eddie=got["eddie"], scout=got["scout"], results=results,
                   traces=traces)
        return {"results": len(done), "inserted": inserted}

    tw, status["TWIN"] = await _component(conn, run_id, "TWIN", twin,
                                          summary_of=lambda r: r)
    streams = ctx.get("streams") or {}
    actual_positions = (streams.get("ACTUAL").positions
                        if streams.get("ACTUAL") else [])

    async def transfer():
        spec = await ST.register_spec(conn, kind="TRANSFER_CRITERIA",
                                      version=1, spec=TR.CRITERIA_SPEC,
                                      now=now)
        tests = await TR.existing_tests(conn)
        start = min([since] + [t["declared_at"] for t in tests])
        slug_sport = {o["slug"]: o["sport"] for s in streams.values()
                      for o in s.opps if o.get("slug")}
        obs = await TR.load(conn, now=now, actual_positions=actual_positions,
                            slug_sport=slug_sport, since=start)
        plans = TR.plan_registrations(
            obs, existing={(t["dimension"], t["target_sport"])
                           for t in tests}, now=now,
            spec_id=spec["spec_id"])
        registered = 0
        for t in plans:
            registered += await ST.register_transfer_test(conn, t)
        tests = await TR.existing_tests(conn)
        evs = []
        for t in tests:
            ev = TR.evaluate(t, obs)
            await ST.save_transfer_evaluation(conn, run_id=run_id, now=now,
                                              ev=ev)
            evs.append(dict(ev, dimension=t["dimension"],
                            source_sport=t["source_sport"],
                            target_sport=t["target_sport"],
                            hypothesis=t["hypothesis"],
                            source_value=t["source_value"],
                            declared_at=t["declared_at"]))
        counts = {}
        for e in evs:
            counts[e["classification"]] = counts.get(e["classification"],
                                                     0) + 1
        payload = C.envelope(
            version=TR.VERSION, computed_at=now, run_id=run_id,
            criteria_spec_id=spec["spec_id"], transfer_assumed=False,
            observations={d: len(v) for d, v in obs.items()},
            sports={d: sorted({r["sport"] for r in v})
                    for d, v in obs.items()},
            tests=evs, classifications=counts,
            dimensions_without_a_source=[
                d for d in TR.DIMENSIONS
                if not any(t["dimension"] == d for t in tests)])
        await ST.save_snapshot(conn, run_id=run_id, component="TRANSFER",
                               payload=payload, now=now, version=TR.VERSION)
        return {"registered": registered, "tests": len(tests),
                "classifications": counts}

    _, status["TRANSFER"] = await _component(conn, run_id, "TRANSFER",
                                             transfer, summary_of=lambda r: r)

    async def scorecards():
        await ST.register_spec(conn, kind="SCORECARD_RULES", version=1,
                               spec=SCD.RULES, now=now)
        db = await SCD.load(conn, now=now, since=since)
        ed, ew = ctx.get("eddie") or (None, "TWIN_COMPONENT_FAILED")
        sc_, sw = ctx.get("scout") or (None, "TWIN_COMPONENT_FAILED")
        rows = SCD.compute(streams=streams, results=ctx.get("results") or {},
                           traces=ctx.get("traces") or {}, db=db,
                           eddie_rows=ed, eddie_why=ew, scout_rows=sc_,
                           scout_why=sw)
        await ST.save_scorecards(conn, run_id=run_id, now=now, rows=rows)
        by: dict = {}
        for r in rows:
            by.setdefault(r["agent"], []).append(r)
        await ST.save_snapshot(
            conn, run_id=run_id, component="SCORECARDS", now=now,
            version=SCD.VERSION, payload=C.envelope(
                version=SCD.VERSION, computed_at=now, run_id=run_id,
                economic_contribution_only=True, summed_across_books=False,
                twin_available=bool(streams), agents=by))
        return {"metrics": len(rows), "measured": sum(
            1 for r in rows if r["status"] == "MEASURED")}

    _, status["SCORECARDS"] = await _component(
        conn, run_id, "SCORECARDS", scorecards, summary_of=lambda r: r)

    async def ladder():
        cs = await ST.register_spec(conn, kind="LADDER_CRITERIA", version=1,
                                    spec=LD.LADDER_SPEC, now=now)
        cf = await ST.register_spec(conn, kind="CONFIDENCE_RULES", version=1,
                                    spec=LD.CONFIDENCE_SPEC, now=now)
        paper = streams.get("PAPER")
        pp = paper.positions if paper else []
        closed = await _closed_rows(conn, pp, actual_positions)
        out = LD.compute(history=LD.historical(pp), paper_closed=closed[
            "PAPER"], actual_closed=closed["ACTUAL"],
            actual_positions=actual_positions,
            regimes=ctx.get("regimes") or [], now=now)
        out["closed_rows_source"] = closed["source"]
        out.update(criteria_spec_id=cs["spec_id"],
                   criteria_sha256=cs["spec_sha256"],
                   confidence_spec_id=cf["spec_id"])
        if not streams:
            out["twin_unavailable"] = "TWIN_COMPONENT_FAILED_THIS_CYCLE"
        await ST.save_ladder(conn, run_id=run_id, now=now, ladder=out)
        await ST.save_snapshot(conn, run_id=run_id, component="LADDER",
                               now=now, version=LD.VERSION,
                               payload=C.envelope(version=LD.VERSION,
                                                  computed_at=now,
                                                  run_id=run_id, **out))
        return {"level": out["level"],
                "confidence": out["confidence"]["status"]}

    _, status["LADDER"] = await _component(conn, run_id, "LADDER", ladder,
                                           summary_of=lambda r: r)

    async def kill():
        spec = await ST.register_spec(conn, kind="KILL_SWITCH_CRITERIA",
                                      version=1, spec=KS.SPEC, now=now)
        trows, twhy = await R.iface(conn, "TOURNAMENT", since=since,
                                    until=now)
        regimes = ctx.get("regimes")
        if regimes is None:
            regimes = await R.regimes(conn, since=since, until=now)
        got = KS.evaluate(streams=streams, regimes=regimes,
                          tournament_rows=trows, tournament_why=twhy,
                          spec_id=spec["spec_id"],
                          spec_sha=spec["spec_sha256"])
        made = await ST.save_kill_recommendations(
            conn, run_id=run_id, now=now, recs=got["recommendations"])
        await ST.save_snapshot(
            conn, run_id=run_id, component="KILL_SWITCHES", now=now,
            version=KS.VERSION, payload=C.envelope(
                version=KS.VERSION, computed_at=now, run_id=run_id,
                criteria_spec_id=spec["spec_id"],
                effect="RECOMMEND_PAUSE_RECORD_ONLY",
                stops_capital=False, activates_capital=False,
                evaluations=got["evaluations"],
                recommendations=got["recommendations"],
                twin_available=bool(streams)))
        return {"evaluations": len(got["evaluations"]),
                "triggered": len(got["recommendations"]),
                "new_recommendations": len(made)}

    _, status["KILL_SWITCHES"] = await _component(
        conn, run_id, "KILL_SWITCHES", kill, summary_of=lambda r: r)

    async def evals():
        await ST.register_spec(conn, kind="EVAL_RUBRIC", version=1,
                               spec=EV.RUBRIC, now=now)
        allpos = [p for s in streams.values() for p in s.positions]
        db = await EV.load(conn, since=since, until=now,
                           account_id=account_id,
                           group_ids=[p["group_id"] for p in allpos])
        ed, ew = ctx.get("eddie") or (None, "TWIN_COMPONENT_FAILED")
        sc_, sw = ctx.get("scout") or (None, "TWIN_COMPONENT_FAILED")
        ok = await EV.decisions_exist(conn, [r["decision_id"] for r in (
            ed or []) + (sc_ or [])])
        got = EV.compute(db=db, positions=allpos, eddie_rows=ed,
                         eddie_why=ew, scout_rows=sc_, scout_why=sw,
                         decision_ok=ok)
        await ST.save_evals(conn, run_id=run_id, now=now, rows=got["rows"])
        await ST.save_snapshot(
            conn, run_id=run_id, component="EVALS", now=now,
            version=EV.VERSION, payload=C.envelope(
                version=EV.VERSION, computed_at=now, run_id=run_id,
                judges="persisted records, never prose", rows=got["rows"],
                macro=got["macro"]))
        return {"rows": len(got["rows"]),
                "origins": got["macro"]["origins"]}

    _, status["EVALS"] = await _component(conn, run_id, "EVALS", evals,
                                          summary_of=lambda r: r)
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
        log.warning("twin could not record the cycle", exc_info=True)
    return {"ran": True, "run_id": run_id, "components": status,
            "label": C.LABEL}


async def _closed_rows(conn, paper_positions, actual_positions) -> dict:
    """Closed positions per book: Audrey's postmortems when migration 209
    is applied (the accounting record), enriched by group with the twin's
    cost / sport / probability; else the twin's own closed positions."""
    by = {p["group_id"]: p for p in paper_positions + actual_positions}
    if not await R.regclass(conn, "position_postmortems"):
        return {"PAPER": LD.closed_rows(paper_positions),
                "ACTUAL": LD.closed_rows(actual_positions),
                "source": "TWIN_POSITIONS_MIGRATION_209_ABSENT"}
    out = {"PAPER": [], "ACTUAL": [], "source": "POSITION_POSTMORTEMS"}
    rows = await conn.fetch(
        "SELECT book, position_key, group_id, us_market_slug, fixture, "
        "       realized_pnl_usd::float8 AS realized_pnl_usd, "
        "       extract(epoch FROM opened_at)::float8 AS opened_at, "
        "       extract(epoch FROM closed_at)::float8 AS closed_at "
        "  FROM position_postmortems ORDER BY closed_at LIMIT %d"
        % R.MAX_ROWS)
    for r in rows:
        d = dict(r)
        d["key"] = d.pop("position_key")
        p = by.get(d["group_id"]) or {}
        d.update(cost_usd=p.get("cost_usd"), sport=p.get("sport") or
                 "unknown", decided_at=p.get("decided_at"), p=p.get("p"),
                 payoff=p.get("payoff"), slippage_pc=p.get("slippage_pc"))
        out[d["book"]].append(d)
    return out


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
                log.warning("twin unlock failed", exc_info=True)


async def run(get_pool) -> None:
    """The scheduled loop. Never raises into the API."""
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        if not enabled():
            await asyncio.sleep(CYCLE_S)
            continue
        try:
            pool = await get_pool()
            got = await _one(pool)
            log.info("twin research cycle: %s", {k: got.get(k) for k in (
                "ran", "run_id", "why", "components")})
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.warning("twin research cycle failed", exc_info=True)
        await asyncio.sleep(CYCLE_S)
