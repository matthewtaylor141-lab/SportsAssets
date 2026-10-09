"""CAPITAL-CRITICAL (research path): THE RN1X MODEL PREPARE AND THE RN1X
LEARN EVALUATION RUN ON THE API'S CPU LANE, NOT ON ITS EVENT LOOP, AND
ANSWER WHAT THEY ANSWERED BEFORE -- AND NEITHER THEY NOR THE MODEL FIT KEEP
THE LANE'S OTHER JOBS WAITING.

THE EVIDENCE. RC6.1 production, 2026-10-09 (render-ops logs run
37949540216, the API loop watchdog):
  14:44:28Z  lag 1.2 s held by task rn1x_model_loop.py:run at
             rn1x_model_loop.py:215 <genexpr> <- prepare <- cycle (the
             dataset digest over the closed rows, after the dataset build
             over up to 40,000 fills -- all on the loop);
  14:47:40Z  lag 1.8 s held by task rn1x_learn_loop.py:run at
             bettor_rn1x_run.py:150 run <- bettor_rn1x_learn.py:260 evaluate
             <- rn1x_learn_loop.py:250 cycle (every settled position replayed
             once per arm and scenario, on the loop).

Pinned here:
  * `prepare` returns exactly what the pre-lane `prepare` returned
    (`_prepare_before` below is b3f1b0cd's, verbatim) on 40,000
    production-shaped fills, and `learn.evaluate` -- drained in one call or
    run through the lane in slices -- exactly what b3f1b0cd's `evaluate`
    (`_evaluate_before`, verbatim) returned;
  * the dataset build and every replay of the learn evaluation run on the
    CPU lane's thread (spies), and the cycle's results are the functions'
    own;
  * at production size the loop's longest gap stays under the watchdog's
    1.0 s lag threshold and is a small fraction of the work's own time;
  * THE LANE STAYS AVAILABLE. A Command read's real lane job (the capital
    authority's blocker tally) submitted while a production-size model fit
    or learn evaluation runs finishes in under LANE_WAIT_S -- an eighth of
    the paper hook's deadline -- where the fit, put on the lane, kept it
    queued past that deadline (this change's first version, 62842fc1, never
    merged; its review measured 38.97 s against 0.16 s),
    and the evaluation, run as one lane job, for the evaluation's whole
    length. The fit stays on its own thread (asyncio.to_thread, as on
    b3f1b0cd); the evaluation runs in slices (cpu_lane.run_steps).
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import hashlib
import json
import random
import threading
import time

import pytest

from sportsassets import cpu_lane
from sportsassets.bettor_rn1x_learn import (CHALLENGERS, VERSION, _metrics,
                                            _sum_rows)
from sportsassets import bettor_rn1x_run as runner
from sportsassets import learn_gate as gate_mod
from sportsassets.learn import dataset as ds
from sportsassets.workers import rn1x_learn_loop as RL
from sportsassets.workers import rn1x_model_loop as L
from sportsassets.workers.rn1x_model_loop import (FILLS_SQL, HORIZON_S,
                                                  LIVE_SOURCES)

try:
    from tests._lane_probe import (LANE_WAIT_S, command_reads_while,
                                   production_gil)
except ImportError:                                             # pragma: no cover
    from _lane_probe import LANE_WAIT_S, command_reads_while, production_gil

WATCHDOG_LAG_S = 1.0


# ═════════════════════════════════════════════════════════════════════
# the pre-lane prepare, VERBATIM from b3f1b0cd (renamed only)
# ═════════════════════════════════════════════════════════════════════

async def _prepare_before(conn, *, days: int = 30, limit: int = 40000,
                 now: float | None = None) -> dict:
    """The point-in-time dataset, split by whether the horizon has closed.

    `observation_end` is the instant the dataset stops knowing anything.
    Rows whose horizon extends past it have NO label -- they are the
    prediction set, not negatives.
    """
    at = float(now if now is not None else time.time())
    rows = [dict(r) for r in await conn.fetch(
        FILLS_SQL, list(LIVE_SOURCES), str(int(days)), int(limit))]
    built = ds.build(rows, horizon_s=HORIZON_S, observation_end=at)
    table = built["rows"] if isinstance(built, dict) else built

    # A DATASET ROW CARRIES NO TRADE ID AND NO ACCOUNT -- `account` comes
    # back None. The prediction ledger needs both: without the trade id
    # there is nothing to key a prediction to, and without the account
    # the outcome join cannot ask "did THIS cohort account complete".
    # So each row is mapped back to the source fill it was built from, on
    # (condition_id, outcome_index, event_ts), and a row that cannot be
    # matched is DROPPED rather than given a placeholder -- a prediction
    # keyed to account "" would join against nothing forever.
    index = {}
    for f in rows:
        index[(f["condition_id"], int(f["outcome_index"]),
               round(float(f["ts"]), 3))] = (int(f["id"]),
                                             int(f["whale_id"]))
    unmatched = 0
    matched = []
    for r in table:
        key = (r.get("condition_id"), int(r.get("outcome_index") or 0),
               round(float(r.get("event_ts") or 0.0), 3))
        hit = index.get(key)
        if hit is None:
            unmatched += 1
            continue
        r["_trade_id"], r["_whale_id"] = hit
        matched.append(r)
    table = matched
    closed, open_ = [], []
    for r in table:
        # `censored` IS THE DATASET'S OWN WORD for a horizon that has not
        # closed. A censored row has NO label: treating it as a negative
        # would teach the model that everything fails, which is the
        # classic way to build a confidently wrong classifier.
        if bool(r.get("censored")):
            open_.append(r)
        else:
            closed.append(r)
    sha = hashlib.sha256(json.dumps(
        [sorted((k, str(v)) for k, v in r.items() if not k.startswith("_"))
         for r in closed], sort_keys=True, default=str
    ).encode()).hexdigest()[:16]
    return {"observation_end": at, "horizon_s": HORIZON_S,
            "source_rows": len(rows), "built_rows": len(table),
            "closed": closed, "open": open_,
            "dataset_sha": sha,
            "n_closed": len(closed), "n_open": len(open_),
            # The dataset's OWN accounting, carried through so the two can
            # be compared rather than trusted.
            "dataset_target": built.get("target"),
            "dataset_version": built.get("version"),
            "dataset_n_rows": built.get("n_rows"),
            "dataset_n_censored": built.get("n_censored"),
            "dataset_n_positive": built.get("n_positive"),
            "dataset_base_rate_uncensored": built.get(
                "base_rate_uncensored"),
            "skipped": built.get("skipped"),
            "unmatched_to_source_fill": unmatched}


# ═════════════════════════════════════════════════════════════════════
# the pre-slice learn evaluation, VERBATIM from b3f1b0cd's
# bettor_rn1x_learn.evaluate (renamed only)
# ═════════════════════════════════════════════════════════════════════

def _evaluate_before(seeds, *, scenarios=None, fee_fn=None) -> dict:
    """Run the champion and every challenger over the SAME seeds.

    `seeds` is a list of dicts, each carrying the arguments one call to
    `bettor_rn1x_run.run` needs: rows, payouts, resolved_at,
    source_whale_id, condition_id, initial_inventory_verified.

    ONE EVIDENCE SET, ONE ASSIGNED INVENTORY, MANY POLICIES. Nothing here
    re-selects which positions a policy gets; that is the whole design.
    """
    scen = tuple(scenarios or gate_mod.SCENARIOS)
    arms = [{"name": "CHAMPION", "params": {}}] + [dict(c) for c in
                                                   CHALLENGERS]
    results = {}
    for arm in arms:
        per_scenario = []
        for qs in scen:
            rows = []
            for seed in seeds:
                out = runner.run(queue_share=qs, fee_fn=fee_fn,
                                 policy_params=arm["params"] or None,
                                 **seed)
                if not (out.get("steps", {}).get("SEED", {}) or {}).get("ok"):
                    # A refused seed is refused for EVERY arm identically
                    # (the refusal is about our inventory record, not the
                    # policy), so dropping it keeps the arms comparable.
                    continue
                rows.append(_metrics(out, qs))
            row = _sum_rows(rows)
            # A SCENARIO THAT PRODUCED NOTHING IS KEPT AS A HOLE, not
            # dropped. Dropping it renumbers every later scenario.
            per_scenario.append(row or {"queue_share": qs, "empty": True})
        results[arm["name"]] = per_scenario
    return {"version": VERSION, "scenarios": list(scen), "arms": results,
            # The number of positions every arm was run over -- the same
            # for all arms by construction, which is the point.
            "positions": max((r.get("positions", 0)
                              for r in results.get("CHAMPION", [])),
                             default=0),
            # The eligibility floor is a COUNT, so it is an int. A float
            # here would read "6.0 decided orders" on the receipt.
            "decided": int(sum(r.get("orders", 0)
                               for r in results.get("CHAMPION", [])))}


# ═════════════════════════════════════════════════════════════════════
# production-shaped fills (FILLS_SQL's columns, oldest first)
# ═════════════════════════════════════════════════════════════════════

def _fills(n=40000, seed=1, at=None):
    rng = random.Random(seed)
    at = time.time() if at is None else at
    t = at - 86400 * 29
    step = 86400 * 29 / n
    conds = ["0xc%05d" % i for i in range(max(1, n // 6))]
    out = []
    for i in range(n):
        t += step * rng.uniform(0.5, 1.5)
        out.append({"id": 10_000_000 + i,
                    "whale_id": rng.choice([11, 22, 33, 44, 55, 66]),
                    "condition_id": rng.choice(conds),
                    "outcome_index": rng.choice([0, 1, 1, 0, 2]),
                    "side": rng.choice(["BUY", "BUY", "BUY", "SELL"]),
                    "size": round(rng.uniform(1, 500), 2),
                    "price": round(rng.uniform(0.02, 0.98), 3),
                    "ts": t - rng.uniform(0, 3), "detected_at": t,
                    "source": rng.choice(["chain", "poll"])})
    # the newest hour: horizons still open (the prediction set)
    for j in range(300):
        out.append(dict(out[-1], id=20_000_000 + j,
                        condition_id="0xopen%d" % j, outcome_index=j % 2,
                        side="BUY", ts=at - 600 + j, detected_at=at - 599 + j))
    return out


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [dict(r) for r in self.rows]


@contextlib.contextmanager
def _collector_paused():
    """A full collection holds the GIL whichever thread triggers it -- in a
    long test session's heap, for longer than the work measured here (the
    API loop watchdog reports the collector's share apart) -- so it is
    paused while a loop gap is measured."""
    was = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


async def _with_ticker(coro):
    """(result, the loop's longest gap, how long `coro` took). A task stamps
    each turn it gets; it has run once before `coro` starts, and its last
    stamp is the turn it gets after `coro` returns -- so a coroutine that
    never yields shows as one gap as long as itself."""
    stamps = []
    done = asyncio.Event()

    async def tick():
        while True:
            stamps.append(time.monotonic())
            if done.is_set():
                return
            await asyncio.sleep(0.005)
    t = asyncio.create_task(tick())
    await asyncio.sleep(0)
    t0 = time.monotonic()
    try:
        out = await coro
    finally:
        took = time.monotonic() - t0
        done.set()
        await t
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return out, max(gaps or [0.0]), took


# ═════════════════════════════════════════════════════════════════════
# rn1x_model_loop.prepare
# ═════════════════════════════════════════════════════════════════════

def test_prepare_answers_exactly_what_it_answered_before():
    at = time.time()
    rows = _fills(at=at)
    want = asyncio.run(_prepare_before(_Conn(rows), now=at))
    conn = _Conn(rows)
    got = asyncio.run(L.prepare(conn, now=at))
    assert got == want
    assert got["dataset_sha"] == want["dataset_sha"]
    assert got["n_closed"] > 5000 and got["n_open"] > 100, got["n_open"]
    # the same read, with the same bound
    assert conn.calls == [(FILLS_SQL, (list(LIVE_SOURCES), "30", 40000))]


def test_prepare_builds_the_dataset_on_the_cpu_lane(monkeypatch):
    threads = []
    real = ds.build

    def spy(*a, **k):
        threads.append(threading.current_thread().name)
        return real(*a, **k)
    monkeypatch.setattr(ds, "build", spy)

    async def go():
        here = threading.current_thread().name
        out = await L.prepare(_Conn(_fills(n=2000)), now=time.time())
        return here, out
    here, out = asyncio.run(go())
    assert out["source_rows"] == 2300
    assert len(threads) == 1 and threads[0] != here
    assert threads[0].startswith(cpu_lane.THREAD_PREFIX), threads


def test_a_production_size_prepare_leaves_the_loop_responsive():
    at = time.time()
    rows = _fills(at=at)
    with _collector_paused():
        out, gap, took = asyncio.run(_with_ticker(L.prepare(_Conn(rows),
                                                            now=at)))
    print("MEASURED longest loop gap %.4f s; prepare took %.4f s"
          % (gap, took))
    assert out["source_rows"] == len(rows)
    assert gap < WATCHDOG_LAG_S, (gap, took)
    assert gap < 0.25 * took, (gap, took)


# ═════════════════════════════════════════════════════════════════════
# a Command read's lane job, timed while a long computation runs
# ═════════════════════════════════════════════════════════════════════

def test_a_lane_job_is_not_queued_behind_the_model_fit(monkeypatch):
    """A production-size fit (40,300 fills; 30-50 s of one call) runs
    through `cycle` while a Command read's lane job runs three times: each
    finishes in under LANE_WAIT_S, the fit still running. With the fit on
    the lane (this change's first version, 62842fc1) each was cut at the
    paper hook's 8 s deadline."""
    at = time.time()
    rows = _fills(at=at)
    started, done = threading.Event(), threading.Event()
    fit_threads = []
    real_fit = L.fit

    def fit(closed):
        fit_threads.append(threading.current_thread().name)
        started.set()
        try:
            return real_fit(closed)
        finally:
            done.set()

    async def ret(v):
        return v

    async def nothing(*a, **k):
        return {}
    monkeypatch.setattr(L, "fit", fit)
    monkeypatch.setattr(L, "_running", lambda conn: ret((True, None)))
    monkeypatch.setattr(L, "_table_ready", lambda conn: ret(True))
    monkeypatch.setattr(L, "_next_version", lambda conn, sha: ret("1.x"))
    for name in ("predict", "join_outcomes", "evaluate", "_heartbeat"):
        monkeypatch.setattr(L, name, nothing)

    async def go():
        cyc = asyncio.create_task(L.cycle(_Conn(rows), now=at))
        lat, during = await command_reads_while(started, done.is_set)
        return lat, during, await cyc
    with _collector_paused(), production_gil():
        lat, during, out = asyncio.run(go())
    print("MEASURED Command read lane job while the fit ran: %s s" % (lat,))
    assert all(x is not None and x < LANE_WAIT_S for x in lat), lat
    assert during, "the fit ended before the reads: nothing was measured"
    assert "fit" in out and out["prepare"]["source_rows"] == len(rows)
    assert len(fit_threads) == 1
    assert not fit_threads[0].startswith(cpu_lane.THREAD_PREFIX), fit_threads


# ═════════════════════════════════════════════════════════════════════
# rn1x_learn_loop: learn.evaluate and the dataset digest
# ═════════════════════════════════════════════════════════════════════

def _seeds(n=200, rows_per=30, seed=3):
    """Settled positions shaped like `_seeds`' output: each the condition's
    fills (the source account's entry and other accounts' fills), payouts,
    the resolution instant and the verified inventory flag."""
    rng = random.Random(seed)
    out = []
    t0 = 1_790_000_000.0
    for i in range(n):
        src = 7000 + i % 9
        cond = "0xlearn%04d" % i
        rows, t = [], t0 + i * 900
        for j in range(rows_per):
            t += rng.uniform(5, 120)
            mine = j == 0 or rng.random() < 0.3
            rows.append({"id": 50_000_000 + i * 1000 + j,
                         "whale_id": src if mine else 9000 + j % 5,
                         "condition_id": cond,
                         "outcome_index": 0 if j == 0 else rng.choice([0, 1]),
                         "side": "BUY" if (j == 0 or rng.random() < 0.8)
                         else "SELL",
                         "size": round(rng.uniform(5, 300), 2),
                         "price": round(rng.uniform(0.05, 0.95), 3),
                         "ts": t - 1.0, "detected_at": t})
        win = rng.choice([0, 1])
        out.append({"rows": rows, "payouts": {win: 1.0, 1 - win: 0.0},
                    "resolved_at": t + 3600.0, "source_whale_id": src,
                    "condition_id": cond,
                    "initial_inventory_verified": rng.random() < 0.9})
    return out


class _LearnConn:
    def __init__(self):
        self.inserts = []

    async def fetchval(self, sql, *a):
        if "count(*) FROM rn1x_outcomes" in sql:
            return 500
        return None                           # no prior evaluation

    async def execute(self, sql, *a):
        self.inserts.append(a)
        return "INSERT 0 1"


def _learn_fakes(monkeypatch, seeds):
    async def ret(v):
        return v
    monkeypatch.setattr(RL, "_running", lambda c: ret((True, "RUNNING")))
    monkeypatch.setattr(RL.store, "ready", lambda c: ret({"ok": True}))
    monkeypatch.setattr(RL, "_registry_ready", lambda c: ret(True))
    monkeypatch.setattr(RL, "_seeds", lambda c, limit: ret(seeds))
    monkeypatch.setattr(RL, "_already_recorded", lambda *a: ret(None))
    monkeypatch.setattr(RL, "_next_version", lambda c, name: ret(1))


def _drain(steps):
    while True:
        try:
            next(steps)
        except StopIteration as stop:
            return stop.value


def test_the_learn_evaluation_answers_exactly_what_it_answered_before():
    """learn.evaluate, its steps drained, and its steps run through the lane
    in slices are b3f1b0cd's evaluate on production-shaped seeds (refused
    seeds included), for the default grid and a narrower one."""
    seeds = _seeds(n=40)
    assert any(not s["initial_inventory_verified"] for s in seeds)
    for scen in (None, (0.25,)):
        want = _evaluate_before(seeds, scenarios=scen)
        assert RL.learn.evaluate(seeds, scenarios=scen) == want
        assert _drain(RL.learn.evaluate_steps(seeds, scenarios=scen)) == want

        async def sliced():
            return await cpu_lane.run_steps(
                RL.learn.evaluate_steps(seeds, scenarios=scen),
                slice_s=0.01)
        assert asyncio.run(sliced()) == want
    assert want["positions"] > 0 and want["decided"] > 0
    assert RL.learn.evaluate([]) == _evaluate_before([])


def test_the_learn_evaluation_runs_on_the_cpu_lane_and_is_its_own(
        monkeypatch):
    seeds = _seeds(n=12)
    _learn_fakes(monkeypatch, seeds)
    threads = []
    real_run, real_sha = runner.run, RL._dataset_sha
    real_eval = RL.learn.evaluate

    def spy_run(**k):
        threads.append(("replay", threading.current_thread().name))
        return real_run(**k)

    def spy_sha(s):
        threads.append(("sha", threading.current_thread().name))
        return real_sha(s)
    slices = []
    real_slice = cpu_lane._slice

    def spy_slice(steps, budget_s):
        slices.append(budget_s)
        return real_slice(steps, budget_s)
    monkeypatch.setattr(runner, "run", spy_run)
    monkeypatch.setattr(RL, "_dataset_sha", spy_sha)
    monkeypatch.setattr(cpu_lane, "_slice", spy_slice)
    conn = _LearnConn()

    async def go():
        return threading.current_thread().name, await RL.cycle(conn)
    here, out = asyncio.run(go())
    assert out["state"] == "EVALUATED", out
    replays = len(seeds) * (1 + len(CHALLENGERS)) * len(gate_mod.SCENARIOS)
    assert [w for w, _ in threads] == ["replay"] * replays + ["sha"]
    for _w, name in threads:
        assert name != here and name.startswith(cpu_lane.THREAD_PREFIX)
    assert slices and set(slices) == {cpu_lane.SLICE_S}
    monkeypatch.setattr(runner, "run", real_run)
    # the cycle's result is the functions' own
    ev = real_eval(seeds)
    sha = real_sha(seeds)
    assert ev == _evaluate_before(seeds)
    assert out["dataset_sha"] == sha[:12]
    assert out["decided"] == ev["decided"] and out["positions"] == len(seeds)
    assert [r["verdict"] for r in out["receipts"]] == [
        RL.learn.recommend(ev, n)["verdict"]
        for n in RL.learn.challenger_names()]
    # the register rows carry exactly the evaluation computed directly
    assert len(conn.inserts) == len(RL.learn.challenger_names())
    for a, name in zip(conn.inserts, RL.learn.challenger_names()):
        rec = RL.learn.recommend(ev, name)
        want = RL.learn.registry_row(name, ev, rec, dataset_sha=sha,
                                     code_sha="unknown", train_cutoff=0.0,
                                     version=1)
        assert a[6] == sha
        assert json.loads(a[11]) == json.loads(json.dumps(
            want["evaluation"], default=str))


def test_a_lane_job_is_not_queued_behind_the_learn_evaluation(monkeypatch):
    """A production-size evaluation (MAX_POSITIONS seeds x five arms x the
    grid: several seconds) runs through `cycle` while a Command read's lane
    job runs three times: each finishes in under LANE_WAIT_S, the
    evaluation still replaying. Run as one lane job (this change's first
    version, 62842fc1) it kept each waiting for the rest of the
    evaluation."""
    seeds = _seeds(n=RL.MAX_POSITIONS)
    _learn_fakes(monkeypatch, seeds)
    total = len(seeds) * (1 + len(CHALLENGERS)) * len(gate_mod.SCENARIOS)
    started, replays = threading.Event(), [0]
    real_run = runner.run

    def spy_run(**k):
        started.set()
        replays[0] += 1
        return real_run(**k)
    monkeypatch.setattr(runner, "run", spy_run)

    async def go():
        cyc = asyncio.create_task(RL.cycle(_LearnConn()))
        lat, during = await command_reads_while(
            started, lambda: replays[0] >= total)
        return lat, during, replays[0], await cyc
    with _collector_paused(), production_gil():
        lat, during, at_last, out = asyncio.run(go())
    print("MEASURED Command read lane job while the evaluation ran: %s s "
          "(%d of %d replays done after the last)" % (lat, at_last, total))
    assert all(x is not None and x < LANE_WAIT_S for x in lat), lat
    assert during and at_last < total, "nothing was measured"
    assert out["state"] == "EVALUATED" and out["positions"] == len(seeds)


def test_a_production_size_learn_evaluation_leaves_the_loop_responsive(
        monkeypatch):
    seeds = _seeds(n=RL.MAX_POSITIONS)
    _learn_fakes(monkeypatch, seeds)
    with _collector_paused():
        out, gap, took = asyncio.run(_with_ticker(RL.cycle(_LearnConn())))
    print("MEASURED longest loop gap %.4f s; the learn cycle took %.4f s"
          % (gap, took))
    assert out["state"] == "EVALUATED" and out["positions"] == len(seeds)
    assert gap < WATCHDOG_LAG_S, (gap, took)
    assert gap < 0.25 * took, (gap, took)


def test_the_digest_is_the_one_the_register_has_always_carried():
    seeds = _seeds(n=5)
    h = hashlib.sha256()
    for s in seeds:
        for r in s["rows"]:
            h.update(("%s:%s:%s:%s:%s\n" % (
                r["id"], r["outcome_index"], r["side"], r["size"],
                r["price"])).encode())
    assert _drain(RL._evaluate_and_digest(seeds))[1] == h.hexdigest()


# ═════════════════════════════════════════════════════════════════════
# cpu_lane.run_steps
# ═════════════════════════════════════════════════════════════════════

def _counting(n, cost_s=0.0, fail_at=None):
    total = 0
    for i in range(n):
        if cost_s:
            end = time.monotonic() + cost_s
            while time.monotonic() < end:
                pass
        if i == fail_at:
            raise ValueError("step %d" % i)
        total += i
        yield
    return total


def test_run_steps_returns_what_the_steps_return_in_bounded_slices(
        monkeypatch):
    durations = []
    real = cpu_lane._slice

    def timed(steps, budget_s):
        t = time.monotonic()
        try:
            return real(steps, budget_s)
        finally:
            durations.append(time.monotonic() - t)
    monkeypatch.setattr(cpu_lane, "_slice", timed)

    async def go():
        return await cpu_lane.run_steps(_counting(300, cost_s=0.002),
                                        slice_s=0.05)
    assert asyncio.run(go()) == sum(range(300))
    assert len(durations) >= 8, durations          # ~0.6 s in 0.05 s slices
    assert max(durations) < 0.05 + 0.1, durations  # a slice plus one step
    assert asyncio.run(cpu_lane.run_steps(_counting(0))) == 0
    st = cpu_lane.status()
    assert st["running"] is None and st["queued"] == 0


def test_run_steps_lets_a_queued_job_run_between_slices():
    order = []

    def quick():
        order.append("quick")
        return "quick"

    def steps():
        for i in range(40):
            end = time.monotonic() + 0.005
            while time.monotonic() < end:
                pass
            order.append("step")
            yield
        return "steps"

    async def go():
        long_ = asyncio.ensure_future(cpu_lane.run_steps(steps(),
                                                         slice_s=0.02))
        await asyncio.sleep(0.03)
        q = await cpu_lane.run(quick)
        return q, await long_
    assert asyncio.run(go()) == ("quick", "steps")
    i = order.index("quick")
    assert 0 < i < len(order) - 1, order           # between two slices


def test_run_steps_raises_what_a_step_raises_and_the_lane_carries_on():
    async def go():
        with pytest.raises(ValueError, match="step 7"):
            await cpu_lane.run_steps(_counting(20, fail_at=7), slice_s=0.0)
        return await cpu_lane.run(sum, [1, 2, 3])
    assert asyncio.run(go()) == 6

