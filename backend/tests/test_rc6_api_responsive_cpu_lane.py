"""RC6 api-responsive: the API process shares its GIL with the event loop on
purpose -- one CPU lane for pure compute, a short switch interval, the boot
heap out of the collector's walk.

THE EVIDENCE. Moving each named loop-holder off the loop (test_rc6_api_
responsive_offloop) moved its work into a thread -- and every such thread
competes with the loop for the GIL. Measured (LOCAL BENCHMARK, uvloop ticking
every 2 ms beside N pure-Python threads): the longest tick gap was 6 ms with
none, 19 ms with one, 73 ms with two, 197 ms with four and 309 ms with
eight. The RC5 API ran up to eight such jobs at once (intel's four, the
research labeller, Derek's model check, the coverage census, the blocker
census, the pair-observation labeller). And 39 of the 132 RC5 stalls >= 2 s
carried a watchdog overrun >= 0.1 s: the watchdog thread itself could not
get the GIL (render-ops logs 2026-10-08).

THE CONTRACT PINNED HERE:
  * every pure compute job the RC6 lane moved off the loop runs on the ONE
    CPU-lane thread (`api-cpu`), one at a time, in arrival order, with the
    caller's context, its errors raised to the caller;
  * the API's lifespan sets the GIL switch interval first (1 ms; the
    operator may set API_GIL_SWITCH_INTERVAL_S) and freezes the boot heap
    last, and its shutdown stops the lane and the desk parse child.
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
import gc
import inspect
import json
import sys
import textwrap
import threading
import time

import pytest

LANE = "api-cpu"


# ── the lane itself ──────────────────────────────────────────────────────

def test_a_job_runs_on_the_lane_thread_and_returns_its_value():
    from sportsassets import cpu_lane as CPU
    seen = []

    def job(a, *, b):
        seen.append(threading.current_thread().name)
        return a + b

    assert asyncio.run(CPU.run(job, 2, b=3)) == 5
    assert seen and seen[0].startswith(LANE)


def test_jobs_run_one_at_a_time_in_arrival_order():
    from sportsassets import cpu_lane as CPU
    spans = []
    lock = threading.Lock()

    def job(i):
        t0 = time.perf_counter()
        x = 0
        for k in range(200_000):
            x += k
        with lock:
            spans.append((i, t0, time.perf_counter(),
                          threading.current_thread().name))
        return i

    async def go():
        return await asyncio.gather(*(CPU.run(job, i) for i in range(6)))
    assert asyncio.run(go()) == list(range(6))
    assert [s[0] for s in spans] == list(range(6)), "arrival order"
    assert len({s[3] for s in spans}) == 1, "one thread"
    for (_a, _s0, e0, _n), (_b, s1, _e1, _m) in zip(spans, spans[1:]):
        assert s1 >= e0, "two lane jobs overlapped"


def test_the_callers_context_reaches_the_job_and_errors_reach_the_caller():
    from sportsassets import cpu_lane as CPU
    var = contextvars.ContextVar("lane_var", default="unset")

    def job():
        return var.get()

    def bad():
        raise ValueError("the job's own error")

    async def go():
        var.set("the caller's")
        got = await CPU.run(job)
        with pytest.raises(ValueError, match="the job's own error"):
            await CPU.run(bad)
        return got
    before = CPU.status()["failed"]
    assert asyncio.run(go()) == "the caller's"
    st = CPU.status()
    assert st["failed"] == before + 1
    assert st["running"] is None and st["queued"] == 0
    assert st["workers"] == 1


def test_a_job_cancelled_before_it_starts_leaves_no_count_behind():
    from sportsassets import cpu_lane as CPU
    gate = threading.Event()

    def blocker():
        gate.wait(5)

    def never():
        raise AssertionError("a cancelled job ran")

    async def go():
        first = asyncio.ensure_future(CPU.run(blocker))
        await asyncio.sleep(0.05)
        second = asyncio.ensure_future(CPU.run(never))
        await asyncio.sleep(0.05)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        gate.set()
        await first
    asyncio.run(go())
    assert CPU.status()["queued"] == 0


# ── the jobs the lane carries (each one's pure function, spied) ──────────

def _spy(monkeypatch, mod, name, seen):
    real = getattr(mod, name)

    def spy(*a, **k):
        seen.append(threading.current_thread().name)
        return real(*a, **k)
    monkeypatch.setattr(mod, name, spy)


class _Rows:
    def __init__(self, rows, needle=None):
        self.rows, self.needle = rows, needle

    async def fetch(self, sql, *a):
        return self.rows if self.needle is None or self.needle in sql else []

    async def fetchval(self, sql, *a):
        return True if "to_regclass" in sql else None


def test_the_coverage_census_classifies_on_the_lane(monkeypatch):
    from sportsassets.agents import coverage as COV
    seen = []
    _spy(monkeypatch, COV, "_classify_all", seen)
    rows = [{"market_slug": "aec-mlb-a-b-2026-10-10-%d" % i,
             "event_slug": "mlb-a-b-2026-10-10", "event_title": "A vs B",
             "question": "A vs B", "sports_type":
             "baseball_team_full_game_moneyline", "game_start": None,
             "updated_at": None} for i in range(50)]
    got = asyncio.run(COV.census(_Rows(rows, "FROM us_premap"),
                                 now=time.time()))
    assert got["ok"] is True
    assert seen and all(n.startswith(LANE) for n in seen), seen


def test_the_blocker_census_tallies_on_the_lane(monkeypatch):
    from sportsassets import bettor_capital_authority as CA
    seen = []
    _spy(monkeypatch, CA, "_blocker_tally", seen)
    rows = [{"strategy": "S", "stage": "DECISION", "refusal": "STALE_BOOK",
             "us_market_slug": "aec-x-%d" % i, "holding_side": "LONG",
             "fixture": "fx", "line": None, "scope": "FULL",
             "gross_edge_pp": 0.1, "edge_shortfall_pp": 0.1,
             "expected_fees_usd": 0.01, "slippage_usd": 0.0,
             "adverse_selection_usd": 0.0, "total_executable_ev_usd": 0.0,
             "refused_at": None} for i in range(20)]
    got = asyncio.run(CA.blocker_census(_Rows(rows), "paper_acct_main"))
    assert got["refused_entries"] == 20
    assert seen and all(n.startswith(LANE) for n in seen), seen


def _research(n=60):
    return [{"observation_id": "obs:%04d" % i, "fixture": "fx:%d" % (i // 3),
             "features": json.dumps({"acquisition_price": 0.4,
                                     "payout_is_complement": 0.0,
                                     "pinnacle_p": 0.5}),
             "feature_sha": "%016x" % i, "price": 0.4,
             "price_basis": "DISPLAYED", "cohort": "C0", "pinnacle_p": 0.5,
             "record_purpose": "CALIBRATION_ONLY",
             "evidence_class": "RESEARCH_OBSERVATION",
             "recorded_epoch": 1.79e9 + i, "decided_epoch": 1.79e9 + i,
             "valuation_id": 1000 + i, "outcome": i % 2,
             "outcome_basis": "VENUE_SETTLEMENT",
             "outcome_epoch": 1.79e9 + 7200 + i} for i in range(n)]


def test_the_research_labels_and_the_model_check_run_on_the_lane(
        monkeypatch):
    from sportsassets import bettor_funded_model as FM
    from sportsassets.agents import derek_research as DR
    rows = _research()
    lab = asyncio.run(DR.labelled_observations(_Rows(rows),
                                               decision_ids=None))
    recs = FM._training_records(lab)
    model = {"model_id": "m", "model_key": FM.KEY_ENTRY_PAYOUT,
             "training_provenance": {
                 "kind": FM.PROVENANCE_RECORDS,
                 "decision_ids": [r["observation_id"] for r in rows],
                 "records_sha": FM._records_sha(recs),
                 "source": FM.SOURCE_RESEARCH_OBSERVATIONS}}
    labels, checks = [], []
    _spy(monkeypatch, DR, "_label_rows", labels)
    _spy(monkeypatch, FM, "_reproduce", checks)
    got = asyncio.run(FM.verify_provenance(_Rows(rows), model))
    assert got["ok"] is True, got
    assert labels and all(n.startswith(LANE) for n in labels), labels
    assert checks and all(n.startswith(LANE) for n in checks), checks


def test_the_api_installs_the_lane_for_the_intel_cycle(monkeypatch):
    """Intel may import no executor (its closure is pinned shadow-only), so
    it leaves the loop through intel.common.offload -- asyncio.to_thread
    until the API's lifespan installs its CPU lane there."""
    from sportsassets.api import app as A
    from sportsassets.intel import calibration as CAL
    from sportsassets.intel import common as IC
    monkeypatch.setattr(IC, "offload", IC.offload)    # restored after
    seen = []
    _spy(monkeypatch, CAL, "build_records", seen)
    assert A._install_cpu_lane() == ["intel.common.offload"]
    asyncio.run(CAL.load_records(_Rows([], None), now=1.8e9))
    assert seen and all(n.startswith(LANE) for n in seen), seen


def test_the_pair_observation_labeller_runs_on_the_lane(monkeypatch):
    from sportsassets import bettor_pair_observations as PO
    seen = []
    _spy(monkeypatch, PO, "labelled_rows", seen)

    async def _yes(conn):
        return True
    monkeypatch.setattr(PO, "has_schema", _yes)
    asyncio.run(PO.labelled(_Rows([])))
    assert seen and all(n.startswith(LANE) for n in seen), seen


def test_every_moved_job_names_the_lane_not_a_bare_thread():
    """Source pin beside the behaviour above: no pure job the RC6 lane moved
    off the loop is back on asyncio.to_thread (the default executor, where
    eight of them competed with the loop at once)."""
    from sportsassets import bettor_capital_authority as CA
    from sportsassets import bettor_funded_model as FM
    from sportsassets import bettor_pair_observations as PO
    from sportsassets.agents import coverage as COV
    from sportsassets.agents import derek_research as DR
    from sportsassets.intel import attribution as AT
    from sportsassets.intel import calibration as CAL
    from sportsassets.intel import regime as RG
    from sportsassets.intel import risk as RK
    from sportsassets.intel import runner as RN
    for fn, jobs in (
            (FM.verify_provenance, ("_reproduce", "_changed_records")),
            (DR.labelled_observations, ("_label_rows",)),
            (COV.census, ("_classify_all",)),
            (CA.blocker_census, ("_blocker_tally",)),
            (CAL.load_records, ("build_records",)),
            (AT.load_paper, ("paper_rows",)),
            (AT.load_actual, ("actual_rows",)),
            (RG.load_and_detect, ("detect",)),
            (RN.run_cycle, ("CAL.independent", "CAL.overlay_plan",
                            "CAL.report")),
            (RK._enrich, ("classify_all",)),
            (RK.paper_report, ("aggregate_paper", "paper_equity", "report")),
            (RK.actual_report, ("aggregate_actual", "mirror_equity",
                                "report")),
            (PO.labelled, ("labelled_rows",)),
            (PO.labelled_conditional, ("conditional_rows",))):
        src = inspect.getsource(fn)
        assert "to_thread" not in src, fn.__qualname__
        intel = fn.__module__.startswith("sportsassets.intel")
        call = "C.offload(" if intel else ".run("
        for job in jobs:
            assert (call + job + ",") in src or (call + job + ")") in src, \
                (fn.__qualname__, job)


# ── the lifespan: the switch interval first, the freeze last ─────────────

def _lifespan_body():
    from sportsassets.api import app as A
    tree = ast.parse(textwrap.dedent(inspect.getsource(A.lifespan)))
    fn = tree.body[0]
    return A, fn


def _calls(node) -> list:
    return [ast.unparse(n.func) for n in ast.walk(node)
            if isinstance(n, ast.Call)]


def test_the_lifespan_shares_the_gil_first_and_freezes_the_heap_last():
    A, fn = _lifespan_body()
    first = fn.body[0]
    assert "_share_the_gil" in _calls(first), ast.unparse(first)[:200]
    assert "_install_cpu_lane" in _calls(fn.body[1])
    # the statement that yields is the last; the freeze is right before it
    yield_at = next(i for i, st in enumerate(fn.body)
                    if any(isinstance(n, ast.Yield) for n in ast.walk(st)))
    assert "_freeze_boot_heap" in _calls(fn.body[yield_at - 1])
    final = fn.body[yield_at]
    assert isinstance(final, ast.Try) and final.finalbody
    ends = [c for st in final.finalbody for c in _calls(st)]
    assert "_CPU_LANE.shutdown" in ends
    assert "_PMUS_PARSE.shutdown_desk_parse" in ends


def test_the_switch_interval_and_its_override(monkeypatch):
    from sportsassets.api import app as A
    was = sys.getswitchinterval()
    try:
        monkeypatch.delenv("API_GIL_SWITCH_INTERVAL_S", raising=False)
        assert A._share_the_gil() == pytest.approx(0.001)
        assert sys.getswitchinterval() == pytest.approx(0.001)
        monkeypatch.setenv("API_GIL_SWITCH_INTERVAL_S", "0.005")
        assert A._share_the_gil() == pytest.approx(0.005)
        for junk in ("abc", "0", "5", "-1"):
            monkeypatch.setenv("API_GIL_SWITCH_INTERVAL_S", junk)
            assert A._share_the_gil() == pytest.approx(0.001), junk
    finally:
        sys.setswitchinterval(was)


def test_the_boot_freeze_takes_the_heap_out_of_every_collection():
    from sportsassets.api import app as A
    try:
        n = A._freeze_boot_heap()
        assert n == gc.get_freeze_count() and n > 10_000
        # what is built after the freeze is still collected
        class _Cycle:
            pass
        a, b = _Cycle(), _Cycle()
        a.other, b.other = b, a
        del a, b
        assert gc.collect() >= 2
    finally:
        gc.unfreeze()
