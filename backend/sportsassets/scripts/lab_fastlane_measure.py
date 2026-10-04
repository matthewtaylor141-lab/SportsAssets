"""LAB-A FAST-LANE MEASUREMENT HARNESS (offline; NEVER in the API process).

Times the REAL canonical components (canonical_components: event start,
Eddie, Opportunity Score, Karen, Allie) on recorded completed-game ENTER
decisions, the way live_parity.canonical_decision calls them, and then runs
the SAME inputs concurrently on ONE exported database snapshot to measure
the concurrent alternative and to prove it computes the SAME outputs.

    python -m sportsassets.scripts.lab_fastlane_measure --dsn <dsn>
        [--limit 20] [--reps 3] [--record --recorded-by <name>]

  cold    the component cache is reset before the call (canonical_
          components.CACHE_S keeps Eddie's history, the settlement lags, the
          capital snapshot and Allie's hurdle for 300 s)
  warm    the immediately repeated call (the cached inputs hit)
  total   the real canonical_components.at_decision, timed whole; the
          per-component replica's outputs must EQUAL its outputs (else the
          measurement is refused as not the decision path's computation)
  concurrent  event start, then {eddie, karen}, then {opportunity, allie}
          (the layers fastlane.derive_graph derives), each on its own
          connection inside REPEATABLE READ READ ONLY transactions that share
          one pg_export_snapshot(); outputs compared with the sequential run

WHY OFFLINE: the harness resets and fills canonical_components' process-wide
cache; inside the API it would perturb the cache the live decision path
uses. Every transaction it opens is READ ONLY. `--record` appends ONE
COMPONENT_LATENCY row to lab_edge_decay_snapshots (migration 242,
SHADOW_RESEARCH_ONLY) -- the only write, in its own transaction.

The decision inputs are rebuilt from the recorded decision exactly as
live_parity.canonical_decision builds them (the `decision` dict and the
`book_row`); the replica-equals-at_decision check is what pins the rebuild.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

import asyncpg

from .. import canonical_components as CC
from ..lab import fastlane as FL

COMPONENTS = ("event_start", "eddie", "opportunity_score", "karen", "allie")
STRATEGY = "PINNACLE_COMPLETED_GAME_PAPER"


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


def _norm(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


async def decision_inputs(conn, decision_id: str) -> dict | None:
    """The `decision` dict and `book_row` live_parity.canonical_decision
    built for this recorded ENTER (read only)."""
    d = await conn.fetchrow(
        "SELECT d.*, s.config AS session_config FROM paper_decisions d "
        "  JOIN paper_sessions s ON s.session_id = d.session_id "
        " WHERE d.decision_id = $1", decision_id)
    if d is None or d["book_obs_id"] is None:
        return None
    econ = _j(d["economics"])
    acq = econ.get("acquisition") or {}
    cfg = _j(d["session_config"])
    sport = (econ.get("mapping_assumptions") or {}).get("sport_family")
    if sport is None and d["valuation_id"] is not None:
        sport = await conn.fetchval(
            "SELECT sport_family FROM external_valuations WHERE id = $1",
            d["valuation_id"])
    cost = acq.get("acquisition_cost_usd")
    at = d["decided_at"].timestamp()
    decision = {
        "decision_id": d["decision_id"], "candidate_id": d["decision_id"],
        "capacity_id": None, "decided_at": at, "strategy": d["strategy"],
        "verdict": d["verdict"], "league": sport, "sport": sport,
        "fixture": d["fixture"], "us_market_slug": d["us_market_slug"],
        "holding_side": d["holding_side"],
        "proposed_qty": None if d["proposed_qty"] is None
        else int(d["proposed_qty"]),
        "limit_price": None if d["limit_price"] is None
        else float(d["limit_price"]),
        "p_pinnacle": d["p_pinnacle"], "p_blended": None, "p_internal": None,
        "economics": {"acquisition": acq},
        "executable_opportunity_dollars": acq.get("expected_net_profit_usd"),
        "executable_capacity_usd": cost,
        "capital_required_usd": (None if cost is None else
                                 float(cost) + float(acq.get("fees_usd") or 0)),
        "depth_within_limit": econ.get("depth_within_limit"),
        "per_order_cap_usd": (cfg.get("risk") or {}).get("per_order_cap_usd"),
        "event_start_at": None, "status": "MEASURED"}
    b = await conn.fetchrow(
        "SELECT obs_id, observed_at, bids, offers FROM paper_book_observations"
        " WHERE obs_id = $1", d["book_obs_id"])
    book_row = None if b is None else {
        "obs_id": b["obs_id"], "observed_at": b["observed_at"].timestamp(),
        "bids": _j(b["bids"]) or [], "offers": _j(b["offers"]) or []}
    return {"decision": decision, "book_row": book_row, "now": at,
            "decision_id": decision_id}


async def _start(conn, decision):
    from ..lost_opportunity import reads as LR
    slug = decision.get("us_market_slug")

    async def start():
        got = await LR.event_starts(conn, [slug])
        return {"t": got.get(slug)}
    return await CC._bounded(conn, start)


def _with_start(decision, st):
    if (st or {}).get("t") is not None:
        return dict(decision, event_start_at=st["t"],
                    event_start_basis="us_premap.game_start")
    return decision


async def sequential(conn, inp: dict) -> tuple:
    """The at_decision order, component by component, timed."""
    t, out = {}, {}
    dec, now = inp["decision"], inp["now"]
    s = time.perf_counter()
    st = await _start(conn, dec) if dec.get("us_market_slug") else {}
    t["event_start"] = time.perf_counter() - s
    dec = _with_start(dec, st)
    s = time.perf_counter()
    out["eddie"] = await CC.eddie_at_decision(conn, decision=dec,
                                              book_row=inp["book_row"], now=now)
    t["eddie"] = time.perf_counter() - s
    s = time.perf_counter()
    out["opportunity_score"] = await CC.opportunity_at_decision(
        conn, decision=dec, eddie=out["eddie"], now=now)
    t["opportunity_score"] = time.perf_counter() - s
    s = time.perf_counter()
    out["karen"] = await CC.karen_at_decision(
        conn, slug=dec.get("us_market_slug"), strategy=dec.get("strategy"),
        now=now)
    t["karen"] = time.perf_counter() - s
    s = time.perf_counter()
    out["allie"] = await CC.allie_at_decision(conn, decision=dec,
                                              eddie=out["eddie"], now=now)
    t["allie"] = time.perf_counter() - s
    return out, t


async def concurrent(conns: list, inp: dict) -> tuple:
    """The derived layers on three connections sharing one snapshot."""
    c0, c1, c2 = conns
    dec, now = inp["decision"], inp["now"]
    t, out = {}, {}

    async def timed(name, coro):
        s = time.perf_counter()
        r = await coro
        t[name] = time.perf_counter() - s
        return r
    wall = time.perf_counter()
    st = await timed("event_start", _start(c0, dec)) \
        if dec.get("us_market_slug") else {}
    dec = _with_start(dec, st)
    out["eddie"], out["karen"] = await asyncio.gather(
        timed("eddie", CC.eddie_at_decision(c0, decision=dec,
                                            book_row=inp["book_row"],
                                            now=now)),
        timed("karen", CC.karen_at_decision(
            c1, slug=dec.get("us_market_slug"),
            strategy=dec.get("strategy"), now=now)))
    out["opportunity_score"], out["allie"] = await asyncio.gather(
        timed("opportunity_score", CC.opportunity_at_decision(
            c0, decision=dec, eddie=out["eddie"], now=now)),
        timed("allie", CC.allie_at_decision(c2, decision=dec,
                                            eddie=out["eddie"], now=now)))
    return out, t, time.perf_counter() - wall


async def _snapshot_txns(conns: list):
    """Open REPEATABLE READ READ ONLY transactions on every connection
    sharing the first one's exported snapshot."""
    txs = []
    tx0 = conns[0].transaction(isolation="repeatable_read", readonly=True)
    await tx0.start()
    txs.append(tx0)
    sid = await conns[0].fetchval("SELECT pg_export_snapshot()")
    for c in conns[1:]:
        tx = c.transaction(isolation="repeatable_read", readonly=True)
        await tx.start()
        await c.execute("SET TRANSACTION SNAPSHOT '%s'" % sid.replace("'", ""))
        txs.append(tx)
    return txs, sid


async def measure(dsn: str, *, decision_ids: list | None = None,
                  limit: int = 20, reps: int = 3) -> dict:
    """Run the harness. Returns the COMPONENT_LATENCY payload."""
    conns = [await asyncpg.connect(dsn) for _ in range(3)]
    saved = dict(CC._CACHE)
    try:
        if decision_ids is None:
            decision_ids = [r["decision_id"] for r in await conns[0].fetch(
                "SELECT decision_id FROM paper_decisions WHERE strategy = $1 "
                "   AND verdict = 'ENTER' AND book_obs_id IS NOT NULL "
                " ORDER BY decided_at DESC LIMIT $2", STRATEGY, int(limit))]
        cold = {c: [] for c in COMPONENTS}
        warm = {c: [] for c in COMPONENTS}
        conc = {c: [] for c in COMPONENTS}
        totals, conc_walls, equal, compared, replica_ok = [], [], 0, 0, 0
        refused = []
        for did in decision_ids:
            inp = await decision_inputs(conns[0], did)
            if inp is None:
                refused.append({"decision_id": did, "why": "NO_INPUTS"})
                continue
            for _ in range(max(1, int(reps))):
                tx = conns[0].transaction(readonly=True)
                await tx.start()
                try:
                    CC.reset_cache()
                    s = time.perf_counter()
                    whole = await CC.at_decision(
                        conns[0], decision=inp["decision"],
                        book_row=inp["book_row"], cost_usd=None, p=None,
                        wire=None, now=inp["now"])
                    totals.append(time.perf_counter() - s)
                    CC.reset_cache()
                    seq_out, seq_t = await sequential(conns[0], inp)
                    _, warm_t = await sequential(conns[0], inp)
                finally:
                    await tx.rollback()
                same = all(_norm(seq_out[k]) == _norm(whole[k])
                           for k in ("eddie", "opportunity_score", "karen",
                                     "allie"))
                replica_ok += int(same)
                for k in COMPONENTS:
                    cold[k].append(seq_t[k])
                    warm[k].append(warm_t[k])
                txs, _sid = await _snapshot_txns(conns)
                try:
                    CC.reset_cache()
                    c_out, c_t, wall = await concurrent(conns, inp)
                finally:
                    for tx in txs:
                        await tx.rollback()
                conc_walls.append(wall)
                for k in COMPONENTS:
                    conc[k].append(c_t.get(k))
                compared += 1
                equal += int(all(_norm(c_out[k]) == _norm(seq_out[k])
                                 for k in ("eddie", "opportunity_score",
                                           "karen", "allie")))
        graph = FL.derive_graph()
        cs = FL.component_latency_summary(cold)
        ws = FL.component_latency_summary(warm)
        comps = {k: {"cold_p50_s": cs[k]["p50_s"], "cold_p90_s": cs[k]["p90_s"],
                     "cold_max_s": cs[k]["max_s"], "warm_p50_s": ws[k]["p50_s"],
                     "warm_p90_s": ws[k]["p90_s"], "n": cs[k]["n"]}
                 for k in COMPONENTS}
        est = FL.critical_path(graph.get("depends_on") or {},
                               {k: v["cold_p50_s"] for k, v in comps.items()})
        return {"version": FL.VERSION, "authority": FL.AUTHORITY,
                "decisions": len(decision_ids), "runs": compared,
                "refused": refused, "components": comps,
                "at_decision_total": FL.component_latency_summary(
                    {"total": totals})["total"],
                "concurrent_wall": FL.component_latency_summary(
                    {"wall": conc_walls})["wall"],
                "concurrent_component": FL.component_latency_summary(conc),
                "replica_equals_at_decision": {"equal": replica_ok,
                                               "of": compared},
                "concurrent_equals_sequential": {"equal": equal,
                                                 "of": compared},
                "critical_path_estimate": est, "graph": graph,
                "cache_s": CC.CACHE_S,
                "component_timeout_s": CC.COMPONENT_TIMEOUT_S,
                "method": ("cold = cache reset before the call; warm = the "
                           "repeated call; concurrent = derived layers on 3 "
                           "connections sharing one exported REPEATABLE READ "
                           "READ ONLY snapshot")}
    finally:
        CC._CACHE.clear()
        CC._CACHE.update(saved)
        for c in conns:
            await c.close()


async def record(conn, payload: dict, *, recorded_by: str,
                 source: str, code_sha: str | None) -> int:
    return await conn.fetchval(
        "INSERT INTO lab_edge_decay_snapshots (kind, version, evidence_status,"
        " source, code_sha, payload, recorded_by) VALUES ('COMPONENT_LATENCY',"
        " $1, 'TESTED', $2, $3, $4::jsonb, $5) RETURNING snapshot_id",
        FL.VERSION, source, code_sha, json.dumps(payload, default=str),
        recorded_by)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--recorded-by", default="")
    ap.add_argument("--source", default="LOCAL_PRODUCTION_SHAPED_ROWS")
    a = ap.parse_args(argv)
    if not a.dsn:
        print("no --dsn / DATABASE_URL")
        return 2

    async def go():
        out = await measure(a.dsn, limit=a.limit, reps=a.reps)
        if a.record:
            if not a.recorded_by:
                raise SystemExit("--record needs --recorded-by")
            c = await asyncpg.connect(a.dsn)
            try:
                out["snapshot_id"] = await record(
                    c, out, recorded_by=a.recorded_by, source=a.source,
                    code_sha=os.environ.get("GIT_SHA"))
            finally:
                await c.close()
        print(json.dumps(out, indent=1, default=str))
    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
