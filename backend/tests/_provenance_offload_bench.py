"""LOCAL MEASUREMENT, NEVER PRODUCTION EVIDENCE: the API event loop's lag and
the PAPER decision's model step, with the research model's provenance check
at production size, on any tree -- production's RC6 source, the uploaded
offload patch applied to it, the successor.

WHAT IT RUNS. The step every PAPER decision takes first
(paper_derek._context: the newest research model from the registry, its
provenance re-read and re-hashed, the void measure), through the TREE'S OWN
CODE, against a REAL Postgres through the tree's own asyncpg pool (max 10,
as production), on uvloop (production's loop), after the API module is
imported and its lifespan's process-level boot applied the tree's own way
(GIL switch interval, CPU lane, boot-heap freeze -- when the tree has them).
The registry holds ONE research model at production's size (2026-10-09
readback: 28,303 named records, a 16,095,248-character provenance), fitted
and registered by the production code (`setup`, run once, from this tree).

PHASES. idle (the loop alone), seq (decisions one after another), burst (N
decisions at once, each on its own pool connection, as concurrent valuation
hooks would), burst_keyed (N decisions of ONE session -- its context cache
key -- at once from a cold context: production starts up to 9 in one
second), arrivals (one session's decisions every --arrive-every s from a
cold context, the per-valuation hook's shape). A 2 ms ticker on the loop records every tick's lateness
(lag = the interval past 2 ms); the collector's pauses are timed apart.
Decision latency is each context step's wall time, waiting for a pool
connection included. Every decision must end VERIFIED, or the run says
INVALID.

USAGE (from backend/; the database must be your own scratch one):
    python tests/_provenance_offload_bench.py setup --dsn DSN [--records N]
    python tests/_provenance_offload_bench.py measure --dsn DSN \\
        --backend /path/to/<tree>/backend --label L [--seq 8] [--burst 16] \\
        [--burst-keyed 9] [--arrive-every 0.5 --arrive-for 15] [--json OUT]
    python tests/_provenance_offload_bench.py teardown --dsn DSN
`setup` refuses a database that already holds research observations or
entry-payout models (it would fit them too); `teardown` removes only what
`setup` wrote. Nothing here calls a venue or a provider.
ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time

PREFIX = "prov-bench-syn-"
MODEL_ID = PREFIX + "model"
ADMIN_TOKEN = "bench-" + "b" * 40


def _q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 4)


# ═════════════════════════════════════════════════════════════════════
# SETUP / TEARDOWN (this tree's production writers)
# ═════════════════════════════════════════════════════════════════════

async def setup(dsn: str, n: int) -> dict:
    import datetime as _dt

    import asyncpg

    from sportsassets import bettor_external_shadow as ext
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import derek_research as DR
    conn = await asyncpg.connect(dsn)
    try:
        if await conn.fetchval("SELECT count(*) FROM "
                               "derek_research_observations"):
            raise SystemExit("refused: derek_research_observations is not "
                             "empty (use your own scratch database)")
        if await conn.fetchval("SELECT count(*) FROM bettor_funded_models "
                               " WHERE model_key = $1", FM.KEY_ENTRY_PAYOUT):
            raise SystemExit("refused: entry-payout models exist here")
        start = time.time() - 4 * 86400.0
        t0 = time.perf_counter()
        await conn.execute(
            "INSERT INTO external_valuations (experiment_id, version, "
            " source_class, provider, book, devig_method, venue, "
            " condition_id, us_market_slug, contract_selection, "
            " sport_family, market, period, raw_odds, outcomes_priced, "
            " expected_outcomes, observed_at, received_at, probability, "
            " executable_price, cost_per_contract, decision, admissible, "
            " refusals, why, payout_event, buy_intent, ladder_side, "
            " record_purpose, decided_at, event_key) "
            "SELECT $1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
            " 'the-odds-api.com/v4','pinnacle','power','PMUS',"
            " $3 || g, $3 || g, 'HOME','baseball','h2h','FULL_GAME',"
            " '{}'::jsonb,2,2, to_timestamp($4 + 5 * g - 5),"
            " to_timestamp($4 + 5 * g - 4), 0.55,"
            " 0.05 + 0.009 * ((g * 37) % 100), 0.0175, 'NO_TRADE', false,"
            " ARRAY['SYNTHETIC_RESEARCH_RECORD'], 'synthetic bench evidence',"
            " 'HOME','ORDER_INTENT_BUY_LONG','ASK','ENTRY_DECISION',"
            " to_timestamp($4 + 5 * g), 'e-' || $3 || g "
            "  FROM generate_series(0, $2 - 1) g",
            ext.EXPERIMENT_ID, int(n), PREFIX, float(start))
        rows = await conn.fetch(DR._SELECT + " AND v.condition_id LIKE $2",
                                ext.EXPERIMENT_ID, PREFIX + "%")
        args = []
        for r in rows:
            obs, why = DR.observation_from_row(dict(r))
            if obs is None:
                raise SystemExit("not an observation: %s" % why)
            args.append(DR._insert_args(obs))
        await conn.executemany(DR.INSERT_SQL, args)
        await conn.execute(
            "UPDATE external_valuations SET outcome_known = TRUE, "
            " outcome = CASE WHEN id % 3 = 0 THEN 1 ELSE 0 END, "
            " outcome_at = decided_at + interval '1 hour', "
            " outcome_basis = 'VENUE_SETTLEMENT_PRICE' "
            " WHERE condition_id LIKE $1 AND outcome_known = FALSE",
            PREFIX + "%")
        through = _dt.datetime.fromtimestamp(time.time(), _dt.timezone.utc)
        fit = await FM.fit_from_records(
            conn, through=through, model_key=FM.KEY_ENTRY_PAYOUT,
            source=FM.SOURCE_RESEARCH_OBSERVATIONS,
            cohorts=[FM.COHORT_EXECUTABLE])
        if not fit.get("ok"):
            raise SystemExit("fit refused: %s" % fit.get("refusal"))
        reg = await FM.register(conn, model_id=MODEL_ID,
                                model_version=MODEL_ID, fitted=fit,
                                fit_through=through,
                                model_key=FM.KEY_ENTRY_PAYOUT)
        if not reg.get("ok"):
            raise SystemExit("registration refused: %s %s"
                             % (reg.get("refusal"), reg.get("why")))
        size = await conn.fetchrow(
            "SELECT jsonb_array_length(training_provenance->'decision_ids') "
            "       AS named, length(training_provenance::text) AS prov, "
            "       length((training_provenance->'records')::text) AS recs "
            "  FROM bettor_funded_models WHERE model_id = $1", MODEL_ID)
        return {"observations": len(args), "named_records": size["named"],
                "provenance_chars": size["prov"],
                "records_chars": size["recs"],
                "setup_s": round(time.perf_counter() - t0, 1)}
    finally:
        await conn.close()


async def teardown(dsn: str) -> dict:
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL session_replication_role = replica")
            m = await conn.execute("DELETE FROM bettor_funded_models "
                                   " WHERE model_id = $1", MODEL_ID)
            o = await conn.execute("DELETE FROM derek_research_observations "
                                   " WHERE condition_id LIKE $1",
                                   PREFIX + "%")
            v = await conn.execute("DELETE FROM external_valuations "
                                   " WHERE condition_id LIKE $1",
                                   PREFIX + "%")
        return {"models": m, "observations": o, "valuations": v}
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# MEASURE (the tree under --backend)
# ═════════════════════════════════════════════════════════════════════

class Holder(threading.Thread):
    """Names what the loop thread is running while a tick is overdue
    (> 50 ms): its innermost frames, sampled every 10 ms, counted."""

    def __init__(self, clock, loop_tid: int):
        super().__init__(daemon=True, name="bench-holder")
        self.clock, self.loop_tid = clock, loop_tid
        self.seen: dict = {}
        self.halt = threading.Event()

    def run(self):
        while not self.halt.wait(0.01):
            if time.perf_counter() - self.clock.last < 0.05:
                continue
            f = sys._current_frames().get(self.loop_tid)
            names = []
            while f is not None and len(names) < 3:
                names.append("%s:%s" % (os.path.basename(
                    f.f_code.co_filename), f.f_code.co_name))
                f = f.f_back
            key = " <- ".join(names) or "(no Python frame)"
            self.seen[key] = self.seen.get(key, 0) + 1

    def top(self, n=8) -> list:
        return sorted(self.seen.items(), key=lambda kv: -kv[1])[:n]


class Clock:
    def __init__(self):
        self.ticks: list = []        # (t, lag_s)
        self.gc: list = []           # (start, end)
        self._began = None
        self.stop = False
        self.last = time.perf_counter()

    def collector(self, phase, info):
        if phase == "start":
            self._began = time.perf_counter()
        elif self._began is not None:
            self.gc.append((self._began, time.perf_counter()))
            self._began = None

    async def run(self):
        last = time.perf_counter()
        while not self.stop:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            self.ticks.append((now, max(0.0, now - last - 0.002)))
            last = self.last = now

    def window(self, a: float, b: float) -> dict:
        lags = [lag for t, lag in self.ticks if a <= t <= b]
        gcs = [(s, e) for s, e in self.gc if a <= s <= b]
        return {"ticks": len(lags), "lag_p50_s": _q(lags, .5),
                "lag_p95_s": _q(lags, .95), "lag_p99_s": _q(lags, .99),
                "lag_max_s": _q(lags, 1.0),
                "holds_ge_100ms": sum(1 for x in lags if x >= 0.1),
                "holds_ge_250ms": sum(1 for x in lags if x >= 0.25),
                "holds_ge_500ms": sum(1 for x in lags if x >= 0.5),
                "gc_pauses": len(gcs),
                "gc_max_s": round(max((e - s for s, e in gcs),
                                      default=0.0), 4),
                "gc_total_s": round(sum(e - s for s, e in gcs), 3)}


async def _measure(args) -> dict:
    import gc
    import importlib

    from sportsassets import db
    from sportsassets.agents import paper_derek as PD
    from sportsassets.api import app as appmod
    for mod in ("ext_pinnacle_loop", "rn1x_shadow", "rn1x_learn_loop",
                "rn1x_model_loop"):
        try:
            importlib.import_module("sportsassets.workers." + mod)
        except Exception:                                       # noqa: BLE001
            pass
    switch = (appmod._share_the_gil() if hasattr(appmod, "_share_the_gil")
              else sys.getswitchinterval())
    lane = (appmod._install_cpu_lane()
            if hasattr(appmod, "_install_cpu_lane") else [])
    frozen = (appmod._freeze_boot_heap()
              if hasattr(appmod, "_freeze_boot_heap") else 0)
    pool = await db.get_pool()
    async with pool.acquire() as c:
        await c.fetchval("SELECT 1")
    clock = Clock()
    gc.callbacks.append(clock.collector)
    ticking = asyncio.ensure_future(clock.run())
    holder = Holder(clock, threading.get_ident())
    holder.start()
    out = {"label": args.label, "backend": args.backend,
           "machine_load_1m_at_start": round(os.getloadavg()[0], 2),
           "boot": {"gil_switch_interval_s": switch, "cpu_lane": lane,
                    "boot_heap_frozen": frozen},
           "phases": {}, "decisions": {}}

    async def one():
        t0 = time.perf_counter()
        async with pool.acquire() as c:
            d = await PD._context(c, {"now": time.time()})
        m = d["model"]
        ok = bool(m.get("ok") and m.get("provenance_verified")
                  and m.get("model_id") == MODEL_ID)
        return time.perf_counter() - t0, ok, m.get("refusal")

    a = time.perf_counter()
    await asyncio.sleep(args.idle)
    out["phases"]["idle"] = clock.window(a, time.perf_counter())
    # warm: one decision outside the windows (first imports, first plans)
    await one()
    a = time.perf_counter()
    seq = [await one() for _ in range(args.seq)]
    out["phases"]["seq"] = clock.window(a, time.perf_counter())
    await asyncio.sleep(0.5)
    a = time.perf_counter()
    burst = await asyncio.gather(*(one() for _ in range(args.burst)))
    b = time.perf_counter()
    out["phases"]["burst"] = clock.window(a, b)
    out["phases"]["burst"]["wall_s"] = round(b - a, 3)
    from sportsassets import bettor_funded_model as FM
    hashes = [0]
    real = FM._records_sha

    def counted(*a, **k):
        hashes[0] += 1
        return real(*a, **k)

    def cold():
        # the session's cache (and, on a tree that has them, its flights)
        # empty, as at a TTL expiry or a process start
        PD._CONTEXT_CACHE.clear()
        getattr(PD, "_CONTEXT_FLIGHTS", {}).clear()

    async def keyed(key):
        t0 = time.perf_counter()
        async with pool.acquire() as c:
            d = await PD._context(c, {"now": time.time(),
                                      "context_cache_key": key})
        m = d["model"]
        return (time.perf_counter() - t0,
                bool(m.get("ok") and m.get("provenance_verified")),
                m.get("refusal"))
    # KEYED BURST: N decisions of ONE session at once from a cold context
    # (production starts up to 9 decisions within one second): how many
    # verifications they cost and what each waits.
    burst_keyed = []
    if args.burst_keyed > 0:
        await asyncio.sleep(0.5)
        FM._records_sha = counted
        hashes[0] = 0
        cold()
        a = time.perf_counter()
        burst_keyed = await asyncio.gather(*(
            keyed(PREFIX + "burst-session")
            for _ in range(args.burst_keyed)))
        b = time.perf_counter()
        FM._records_sha = real
        out["phases"]["burst_keyed"] = clock.window(a, b)
        out["phases"]["burst_keyed"].update(wall_s=round(b - a, 3),
                                            verifications=hashes[0])
    # ARRIVALS: the per-valuation hook's own shape -- decisions of ONE
    # session (its context cache key, the tree's own TTL) arriving every
    # `arrive_every` s, the cache cold at the start as after each TTL
    # expiry. How many verifications one expiry costs, and what the
    # decisions arriving meanwhile wait.
    arrivals = []
    if args.arrive_for > 0:
        FM._records_sha = counted
        hashes[0] = 0
        cold()
        key = PREFIX + "session"
        await asyncio.sleep(0.5)
        a = time.perf_counter()
        tasks = []
        while time.perf_counter() - a < args.arrive_for:
            tasks.append(asyncio.ensure_future(keyed(key)))
            await asyncio.sleep(args.arrive_every)
        arrivals = await asyncio.gather(*tasks)
        b = time.perf_counter()
        FM._records_sha = real
        out["phases"]["arrivals"] = clock.window(a, b)
        out["phases"]["arrivals"].update(
            wall_s=round(b - a, 3), arrive_every_s=args.arrive_every,
            verifications=hashes[0])
    clock.stop = True
    await ticking
    holder.halt.set()
    gc.callbacks.remove(clock.collector)
    out["machine_load_1m_at_end"] = round(os.getloadavg()[0], 2)
    out["loop_holders_sampled"] = holder.top()
    for name, got in (("seq", seq), ("burst", burst),
                      ("burst_keyed", burst_keyed), ("arrivals", arrivals)):
        if not got:
            continue
        lat = [s for s, _ok, _r in got]
        out["decisions"][name] = {
            "n": len(got), "verified": sum(1 for _s, ok, _r in got if ok),
            "refusals": sorted({str(r) for _s, ok, r in got if not ok}),
            "p50_s": _q(lat, .5), "p95_s": _q(lat, .95),
            "max_s": _q(lat, 1.0),
            "over_8s_decision_deadline": sum(1 for s in lat if s > 8.0)}
    out["valid"] = all(d["verified"] == d["n"]
                       for d in out["decisions"].values())
    try:
        from sportsassets import cpu_lane as _lane
        out["cpu_lane"] = _lane.status()
    except ImportError:
        out["cpu_lane"] = None                  # a tree without the lane
    await db.close_pool()
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("action", choices=("setup", "measure", "teardown"))
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--backend", default=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    p.add_argument("--label", default="tree")
    p.add_argument("--records", type=int, default=28_303)
    p.add_argument("--idle", type=float, default=2.0)
    p.add_argument("--seq", type=int, default=8)
    p.add_argument("--burst", type=int, default=16)
    p.add_argument("--burst-keyed", type=int, default=0)
    p.add_argument("--arrive-every", type=float, default=0.5)
    p.add_argument("--arrive-for", type=float, default=15.0)
    p.add_argument("--json", default=None)
    args = p.parse_args(argv)
    if not args.dsn.startswith("postgres"):
        print("refused: --dsn (a real Postgres) is required")
        return 2
    args.backend = os.path.abspath(args.backend)
    os.environ["DATABASE_URL"] = args.dsn
    os.environ.setdefault("ADMIN_TOKEN", ADMIN_TOKEN)
    sys.path.insert(0, args.backend)
    os.chdir(args.backend)
    if args.action == "setup":
        got = asyncio.run(setup(args.dsn, args.records))
    elif args.action == "teardown":
        got = asyncio.run(teardown(args.dsn))
    else:
        try:
            import uvloop
            loop = uvloop.new_event_loop()
        except ImportError:                                     # pragma: no cover
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            got = loop.run_until_complete(_measure(args))
        finally:
            loop.close()
        got["threads_at_exit"] = sorted(t.name for t in threading.enumerate())
    text = json.dumps(got, indent=1, default=str)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
