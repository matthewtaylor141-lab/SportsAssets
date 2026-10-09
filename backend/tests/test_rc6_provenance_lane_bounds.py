"""RC6 PROVENANCE OFFLOAD -- WHERE THE RE-HASH RUNS, HOW MUCH OF IT RUNS AT
ONCE, AND WHAT A DECISION SEES WHEN THE LANE CANNOT RUN IT.

The owner's directive (2026-10-09): "verify CPU offloading and bounded
concurrency under parallel PAPER decisions ... do not claim GREEN from
structural tests alone". The uploaded offload patch moved the re-hash to
`asyncio.to_thread` (the default executor: up to min(32, cores + 4) threads,
an unbounded queue) and named the risk itself: "for large concurrent
verifications, add bounded concurrency". The successor (ff85d723) runs it on
the API's ONE CPU-lane thread (`cpu_lane`, thread `api-cpu_0`). Pinned here,
behaviourally, through the PAPER decision's own wrapper and context
(paper_derek.bounded_decision -> _context -> research_model ->
bettor_funded_model.verify_provenance), each decision on its own connection:

  1. PLACEMENT. The training-set hash and the label parse run on the lane
     thread, never on the event loop's (uvloop, as production serves).
  2. PARALLEL PAPER DECISIONS, N = 16, 32, 64. Half the decisions read a
     ledger whose training set changed after the fit. Every decision ends
     verified or refused by name (none raises, none is lost); the changed
     half all refuse and the rest all verify; the re-hashes ran on ONE
     thread, one at a time, the process gained at most that one thread, the
     lane's queue never held more than one job per waiting decision and
     drained to zero, each verification ran exactly once; and the loop's
     longest gap (net of the collector's own pauses) stayed under 0.25 s,
     offloop's bound (a 2 s production hold at the measured slow-down).
  3. FAIL CLOSED WHEN SATURATED. A lane held by a long job: every decision
     cut by its deadline raises the decision deadline's TimeoutError (no
     model, verified or not, reaches it), its queued verification is dropped
     and NEVER runs later, the queue drains, and the next decision verifies.
     A decision cut while its own lane job is running gets no result either.
  4. FAIL CLOSED WHEN SHUT DOWN. Verifications queued when the lane is shut
     down raise to their decisions -- none is reported verified, none of
     their hashes ran.

The timing scenarios run in a fresh interpreter (as test_rc6_api_responsive_
offloop does): a loop-gap bound read late in a long session measures the
session's heap as much as the work.
ALL DATA SYNTHETIC; no database (each decision's reads are answered by an
in-process stand-in for the reads it makes; the CPU work is the code's own).
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import subprocess
import sys
import threading
import time

import pytest

LANE = "api-cpu"
LOOP_GAP_BOUND_S = 0.25
RECORDS = 2_000
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER STAND-IN (the two reads a research-model check makes)
# ═════════════════════════════════════════════════════════════════════

def _label_rows(n: int, seed: int = 7) -> list:
    """Research label rows, each vector stored with ITS OWN identity (as
    every production writer stores it: bettor_funded_model.feature_sha)."""
    from sportsassets import bettor_funded_model as FM
    rng = random.Random(seed)
    out = []
    for i in range(n):
        feats = {"acquisition_price": rng.random(),
                 "payout_is_complement": float(i % 2)}
        out.append({
            "observation_id": "obs:%06d" % i, "fixture": "fx:%d" % (i // 3),
            "features": json.dumps(feats),
            "feature_sha": FM.feature_sha(feats),
            "price": rng.random(),
            "price_basis": "EXECUTABLE_BOOK_CURRENCY_ESTABLISHED",
            "cohort": "EXECUTABLE_PRICE_CURRENT", "pinnacle_p": rng.random(),
            "record_purpose": "ENTRY_DECISION",
            "evidence_class": "PROSPECTIVE_LIVE",
            "recorded_epoch": 1.79e9 + i, "decided_epoch": 1.79e9 + i,
            "valuation_id": 1000 + i, "outcome": i % 2,
            "outcome_basis": "VENUE_SETTLEMENT_PRICE",
            "outcome_epoch": 1.79e9 + 7200 + i})
    return out


class _Conn:
    """One decision's connection: the registry read (either shape --
    production RC6's SELECT * or the successor's slim REGISTRY_SQL), the
    research label read, the mismatch diagnostic's stored-records read, the
    training set's change stamp (migration 365; this ledger never changes
    while a scenario runs, so its stamp is one constant); every other read
    answers empty."""

    STAMP = "stand-in-ledger:unchanged"

    def __init__(self, labels, model_row, prov):
        self.labels, self.model_row, self.prov = labels, model_row, prov

    async def fetch(self, sql, *a):
        if "derek_research_observations" in sql:
            return self.labels
        if "bettor_funded_models" in sql:
            row = dict(self.model_row)
            if "_provenance_slim" in sql:
                slim = {k: v for k, v in self.prov.items()
                        if k not in ("records", "decision_ids")}
                row.update(_provenance_slim=json.dumps(slim),
                           _provenance_ids_type="array",
                           _provenance_ids=list(self.prov["decision_ids"]),
                           _provenance_ids_raw=None)
            else:
                row["training_provenance"] = json.dumps(self.prov)
            return [row]
        return []

    async def fetchval(self, sql, *a):
        if "research_training_set_changes" in sql:
            return self.STAMP
        if "training_provenance->'records'" in sql:
            return json.dumps(self.prov["records"])
        return None

    async def fetchrow(self, sql, *a):
        return None


def _world(n_records: int = RECORDS) -> dict:
    """The model as fitted, the ledger as it was, and the ledger after one
    training label was corrected."""
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import derek_research as DR

    rows = _label_rows(n_records)

    class _Fixed:
        async def fetch(self, sql, *a):
            return rows
    lab = asyncio.run(DR.labelled_observations(_Fixed(), decision_ids=None))
    recs = FM._training_records(lab)
    prov = {"kind": FM.PROVENANCE_RECORDS,
            "decision_ids": [r["observation_id"] for r in rows],
            "records_sha": FM._records_sha(recs), "records": recs,
            "source": FM.SOURCE_RESEARCH_OBSERVATIONS,
            "weighting": "EVENT_BALANCED", "n_events": lab["n_events"]}
    changed = [dict(r) for r in rows]
    k = n_records // 2
    changed[k] = dict(changed[k], outcome=1 - changed[k]["outcome"])
    import datetime as _d
    now = _d.datetime.now(_d.timezone.utc)
    model_row = {"model_id": "rc6-prov-lane-model",
                 "model_key": FM.KEY_ENTRY_PAYOUT, "model_version": "l1",
                 "state": FM.STATE_CANDIDATE, "kernel": "k", "estimator": "e",
                 "features": ["acquisition_price"], "params": '{"w": [1]}',
                 "fit_through": now, "train_rows": n_records,
                 "train_base_rate": None, "evaluation": None,
                 "approved_at": None, "approved_by": None, "retired_at": None,
                 "retired_reason": None, "superseded_by": None,
                 "created_at": now - _d.timedelta(seconds=5),
                 "trained_through": None, "outcomes_available_through": None}
    return {"rows": rows, "changed": changed, "prov": prov,
            "model_row": model_row,
            "changed_id": rows[k]["observation_id"]}


# ═════════════════════════════════════════════════════════════════════
# INSTRUMENTS
# ═════════════════════════════════════════════════════════════════════

class _Hashes:
    """Every training-set hash: its thread and its span."""

    def __init__(self):
        from sportsassets import bettor_funded_model as FM
        self.FM, self.real = FM, FM._records_sha
        self.spans: list = []
        self.lock = threading.Lock()

        def spy(*a, **k):
            t0 = time.perf_counter()
            try:
                return self.real(*a, **k)
            finally:
                t = threading.current_thread()
                with self.lock:
                    self.spans.append((t0, time.perf_counter(), t.name,
                                       t.ident))
        FM._records_sha = spy

    def undo(self):
        self.FM._records_sha = self.real

    def overlaps(self) -> int:
        s = sorted(self.spans)
        return sum(1 for a, b in zip(s, s[1:]) if b[0] < a[1])


class _Clock:
    """A 2 ms ticker on the loop: every tick's lateness, net of the
    collector's pauses, with the lane's queue and the process's threads
    sampled each tick."""

    def __init__(self):
        self.lags: list = []
        self.gc: list = []
        self._began = None
        self.max_queued = 0
        self.max_threads = threading.active_count()
        self.done = asyncio.Event()

    def collector(self, phase, info):
        if phase == "start":
            self._began = time.perf_counter()
        elif self._began is not None:
            self.gc.append((self._began, time.perf_counter()))
            self._began = None

    def _collected(self, a, b) -> float:
        return sum(max(0.0, min(b, e) - max(a, s)) for s, e in self.gc)

    async def run(self):
        from sportsassets import cpu_lane as CPU
        last = time.perf_counter()
        while not self.done.is_set():
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            self.lags.append(max(0.0, now - last - 0.002
                                 - self._collected(last, now)))
            last = now
            self.max_queued = max(self.max_queued, CPU.status()["queued"])
            self.max_threads = max(self.max_threads,
                                   threading.active_count())


def _q(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 4)


def _uvloop_run(coro):
    try:
        import uvloop
        loop = uvloop.new_event_loop()
    except ImportError:                                         # pragma: no cover
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fresh(name: str, *args) -> dict:
    """Run scenario `name` in a fresh interpreter; return what it measured."""
    code = ("import json, sys; sys.path[:0] = [%r, %r]; "
            "import test_rc6_provenance_lane_bounds as T; "
            "print('RESULT ' + json.dumps(T.%s(*%r)))"
            % (BACKEND, os.path.join(BACKEND, "tests"), name, tuple(args)))
    proc = subprocess.run([sys.executable, "-c", code], cwd=BACKEND,
                          capture_output=True, text=True, timeout=900,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    got = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert got, proc.stderr[-3000:]
    return json.loads(got[-1][len("RESULT "):])


def _decision(conn, at: float):
    """One PAPER decision's model step, as decide_one takes it: its context
    (research model + provenance check), under the decision deadline."""
    from sportsassets.agents import paper_derek as PD

    async def make(ctx):
        d = await PD._context(conn, ctx)
        return {"model": d["model"]}
    return make


async def _decide(make, *, at: float, timeout_s: float) -> dict:
    from sportsassets.agents import paper_derek as PD
    t0 = time.perf_counter()
    try:
        rec = await PD.bounded_decision(make, {"now": at},
                                        timeout_s=timeout_s)
        m = rec["model"]
        out = {"outcome": "VERIFIED" if (m.get("ok") and m.get(
                   "provenance_verified")) else "REFUSED",
               "refusal": m.get("refusal"), "why": m.get("why"),
               "verified": bool(m.get("provenance_verified"))}
    except asyncio.TimeoutError:
        out = {"outcome": "DEADLINE", "verified": False}
    except BaseException as exc:                                # noqa: BLE001
        out = {"outcome": "RAISED:" + type(exc).__name__, "verified": False}
    out["s"] = time.perf_counter() - t0
    return out


# ═════════════════════════════════════════════════════════════════════
# 1 + 2 · PLACEMENT AND PARALLEL PAPER DECISIONS
# ═════════════════════════════════════════════════════════════════════

def scenario_parallel(n: int, n_records: int = RECORDS) -> dict:
    from sportsassets import cpu_lane as CPU
    from sportsassets.agents import paper_derek as PD

    w = _world(n_records)
    threads_before = threading.active_count()
    hashes = _Hashes()
    clock = _Clock()
    gc.callbacks.append(clock.collector)
    PD._CONTEXT_CACHE.clear()
    jobs_before = CPU.status()["jobs"]
    at = time.time()

    async def go():
        loop_tid = threading.get_ident()
        tick = asyncio.ensure_future(clock.run())
        await asyncio.sleep(0.02)
        conns = [_Conn(w["changed"] if i % 2 else w["rows"], w["model_row"],
                       w["prov"]) for i in range(n)]
        t0 = time.perf_counter()
        got = await asyncio.gather(*(
            _decide(_decision(c, at), at=at, timeout_s=600.0)
            for c in conns))
        wall = time.perf_counter() - t0
        await asyncio.sleep(0.02)
        clock.done.set()
        await tick
        return got, wall, loop_tid
    try:
        got, wall, loop_tid = _uvloop_run(go())
    finally:
        hashes.undo()
        gc.callbacks.remove(clock.collector)
    st = CPU.status()
    unchanged = [g for i, g in enumerate(got) if i % 2 == 0]
    changed = [g for i, g in enumerate(got) if i % 2 == 1]
    return {
        "n": n, "records": n_records, "wall_s": round(wall, 3),
        "outcomes": sorted({g["outcome"] for g in got}),
        "unchanged_verified": sum(1 for g in unchanged
                                  if g["outcome"] == "VERIFIED"),
        "changed_refused": sum(
            1 for g in changed if g["outcome"] == "REFUSED"
            and g["refusal"] == PD.R_MODEL_UNVERIFIED
            and g["why"] == "THE_TRAINING_RECORDS_DO_NOT_REPRODUCE"),
        "changed_verified": sum(1 for g in changed if g["verified"]),
        "hash_threads": sorted({s[2] for s in hashes.spans}),
        "hashes": len(hashes.spans),
        "hashes_on_loop": sum(1 for s in hashes.spans if s[3] == loop_tid),
        "hash_overlaps": hashes.overlaps(),
        "lane_jobs": st["jobs"] - jobs_before, "lane_queued_after": st[
            "queued"], "lane_running_after": st["running"],
        "lane_workers": st["workers"], "max_queued": clock.max_queued,
        "threads_before": threads_before, "max_threads": clock.max_threads,
        "loop_lag_p50_s": _q(clock.lags, .5),
        "loop_lag_p95_s": _q(clock.lags, .95),
        "loop_lag_max_s": _q(clock.lags, 1.0),
        "decision_p50_s": _q([g["s"] for g in got], .5),
        "decision_p95_s": _q([g["s"] for g in got], .95),
        "decision_max_s": _q([g["s"] for g in got], 1.0)}


@pytest.mark.parametrize("n", [16, 32, 64])
def test_parallel_paper_decisions_are_verified_or_refused_on_one_lane(n):
    got = _fresh("scenario_parallel", n)
    # every decision ended, verified or refused by name; none raised
    assert set(got["outcomes"]) <= {"VERIFIED", "REFUSED"}, got
    assert got["unchanged_verified"] == n // 2, got
    assert got["changed_refused"] == n // 2, got
    assert got["changed_verified"] == 0, got
    # one re-hash per decision, every one on the lane thread, one at a time
    assert got["hashes"] == n, got
    assert got["hash_threads"] == ["api-cpu_0"], got
    assert got["hashes_on_loop"] == 0, got
    assert got["hash_overlaps"] == 0, got
    # the label parse and the re-hash for each decision, plus the mismatch
    # diagnostic for each changed one: every job ran exactly once
    assert got["lane_jobs"] == 2 * n + n // 2, got
    # bounded: one worker; the queue held at most one job per waiting
    # decision and drained; the process gained at most the lane's thread
    assert got["lane_workers"] == 1
    assert got["max_queued"] <= n, got
    assert got["lane_queued_after"] == 0 and got["lane_running_after"] is None
    assert got["max_threads"] <= got["threads_before"] + 1, got
    assert got["loop_lag_max_s"] < LOOP_GAP_BOUND_S, got


# ═════════════════════════════════════════════════════════════════════
# 3 · FAIL CLOSED WHEN SATURATED
# ═════════════════════════════════════════════════════════════════════

def scenario_saturated(n: int = 16) -> dict:
    from sportsassets import cpu_lane as CPU
    from sportsassets.agents import paper_derek as PD

    w = _world(RECORDS)
    big = _world(28_303)
    hashes = _Hashes()
    PD._CONTEXT_CACHE.clear()
    gate = threading.Event()
    at = time.time()

    def hold():
        gate.wait(30)

    async def go():
        holding = asyncio.ensure_future(CPU.run(hold))
        await asyncio.sleep(0.05)
        jobs0 = CPU.status()["jobs"]
        cut = await asyncio.gather(*(
            _decide(_decision(_Conn(w["rows"], w["model_row"], w["prov"]),
                              at), at=at, timeout_s=0.3)
            for _ in range(n)))
        queued_after_cut = CPU.status()["queued"]
        gate.set()
        await holding
        await asyncio.sleep(0.1)
        drained = CPU.status()
        hashed_while_cut = len(hashes.spans)
        later = await _decide(_decision(_Conn(w["rows"], w["model_row"],
                                              w["prov"]), at),
                              at=at, timeout_s=600.0)
        # a deadline that lands while the decision's OWN lane job is running
        mid = await _decide(_decision(_Conn(big["rows"], big["model_row"],
                                            big["prov"]), at),
                            at=at, timeout_s=0.05)
        for _ in range(600):
            if CPU.status()["running"] is None and \
                    CPU.status()["queued"] == 0:
                break
            await asyncio.sleep(0.05)
        return {"cut": cut, "queued_after_cut": queued_after_cut,
                "drained_queued": drained["queued"],
                "jobs_while_cut": drained["jobs"] - jobs0,
                "hashed_while_cut": hashed_while_cut, "later": later,
                "mid": mid, "final": CPU.status()}
    try:
        got = _uvloop_run(go())
    finally:
        hashes.undo()
    return {"n": n, "cut_outcomes": sorted({c["outcome"] for c in got["cut"]}),
            "cut_verified": sum(1 for c in got["cut"] if c["verified"]),
            "queued_after_cut": got["queued_after_cut"],
            "drained_queued": got["drained_queued"],
            "jobs_while_cut": got["jobs_while_cut"],
            "hashed_while_cut": got["hashed_while_cut"],
            "later": got["later"]["outcome"], "mid": got["mid"]["outcome"],
            "mid_verified": got["mid"]["verified"],
            "final_queued": got["final"]["queued"],
            "final_running": got["final"]["running"]}


def test_a_saturated_lane_fails_closed_and_drops_what_it_never_ran():
    got = _fresh("scenario_saturated", 16)
    # every decision cut by its deadline: no model reached it
    assert got["cut_outcomes"] == ["DEADLINE"], got
    assert got["cut_verified"] == 0
    # their queued jobs were withdrawn with them and never ran: only the
    # job that held the lane ran
    assert got["queued_after_cut"] == 0, got
    assert got["drained_queued"] == 0
    assert got["jobs_while_cut"] == 1, got
    assert got["hashed_while_cut"] == 0, got
    # the lane recovered: the next decision verifies
    assert got["later"] == "VERIFIED", got
    # a deadline during the decision's own running lane job: no result
    assert got["mid"] == "DEADLINE" and got["mid_verified"] is False, got
    assert got["final_queued"] == 0 and got["final_running"] is None


# ═════════════════════════════════════════════════════════════════════
# 4 · FAIL CLOSED WHEN SHUT DOWN
# ═════════════════════════════════════════════════════════════════════

def scenario_shutdown(n: int = 8) -> dict:
    from sportsassets import cpu_lane as CPU
    from sportsassets.agents import paper_derek as PD

    w = _world(RECORDS)
    hashes = _Hashes()
    PD._CONTEXT_CACHE.clear()
    gate = threading.Event()
    at = time.time()

    def hold():
        gate.wait(30)

    async def go():
        holding = asyncio.ensure_future(CPU.run(hold))
        await asyncio.sleep(0.05)
        waiting = [asyncio.ensure_future(_decide(
            _decision(_Conn(w["rows"], w["model_row"], w["prov"]), at),
            at=at, timeout_s=600.0)) for _ in range(n)]
        await asyncio.sleep(0.2)
        queued = CPU.status()["queued"]
        CPU.shutdown()
        got = await asyncio.gather(*waiting)
        gate.set()
        await holding
        return queued, got
    try:
        queued, got = _uvloop_run(go())
    finally:
        hashes.undo()
    return {"n": n, "queued_at_shutdown": queued,
            "outcomes": sorted({g["outcome"] for g in got}),
            "verified": sum(1 for g in got if g["verified"]),
            "hashed": len(hashes.spans)}


def test_a_shut_down_lane_fails_closed():
    got = _fresh("scenario_shutdown", 8)
    assert got["queued_at_shutdown"] == 8, got
    # every queued verification raised to its decision; none verified and
    # none of their hashes ran
    assert got["verified"] == 0, got
    assert got["hashed"] == 0, got
    assert all(o.startswith("RAISED:") or o == "REFUSED"
               for o in got["outcomes"]), got


# ═════════════════════════════════════════════════════════════════════
# THE INSTRUMENTS THEMSELVES
# ═════════════════════════════════════════════════════════════════════

def test_the_stand_in_ledger_verifies_unchanged_and_refuses_changed():
    """In this process, no timing: the stand-in reads and the world they
    serve reproduce the fit (unchanged) and name the corrected label
    (changed) through verify_provenance itself."""
    from sportsassets import bettor_funded_model as FM
    w = _world(300)
    model = FM._row(dict(w["model_row"],
                         training_provenance=json.dumps(w["prov"])))
    ok = asyncio.run(FM.verify_provenance(
        _Conn(w["rows"], w["model_row"], w["prov"]), model))
    bad = asyncio.run(FM.verify_provenance(
        _Conn(w["changed"], w["model_row"], w["prov"]), model))
    assert ok["ok"] is True and len(ok["records"]) == 300
    assert bad["ok"] is False
    assert bad["refusal"] == FM.R_TRAINING_RECORDS_DO_NOT_REPRODUCE
    assert bad["changed_records"] == [w["changed_id"]]
