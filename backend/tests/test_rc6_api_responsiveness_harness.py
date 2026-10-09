"""RC6 api-responsive: the representative-workload responsiveness harness
(tools/api_responsiveness_harness.py) -- its verdict, and the tree's.

The harness serves the real app on uvloop against the real Postgres while
the loop-holders production named run at production size, saturates the
pool, slows the venue and the database, then lets them recover. Run against
the pre-RC6 trees it FAILS (2026-10-09, this machine at load 4-8):

  1c874c1f (RC5 as deployed)  /healthz unanswered within 10 s x3, the loop
                              held 5.4 s (reactive name scans), 4.8 s (the
                              coverage census), 4.5 s (pair observations)
  412c4962 (RC6 base)         /healthz unanswered within 10 s x2, the loop
                              held 6.0 s (the coverage census), 4.0 s (the
                              blocker census), 3.9 s (reactive name scans)

and this tree PASSES (3 of 3 runs: the loop's longest hold 0.27-0.36 s,
the API's share of the slowest /healthz 0.24-0.31 s).

The first test pins the verdict's arithmetic on made-up samples (no
database); the second runs the harness itself on this tree, shortened.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DSN = os.environ.get("DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not DSN.startswith("postgres"),
                              reason="needs DATABASE_URL (a real Postgres)")


def _harness():
    sys.path.insert(0, str(BACKEND))
    from tools import api_responsiveness_harness as H
    return H


class _Sampler:
    def __init__(self, max_gap, gaps=()):
        self.max_gap, self.gaps, self.n = max_gap, list(gaps), 1000


class _Gc:
    pauses: list = []
    full: list = []

    def within(self, a, b):
        return 0.0


def _report(healthz, *, max_gap=0.1, reads=None):
    H = _harness()
    marks = H.Marks()
    for n in ("intel", "pair_obs", "desk_sweep", "pinnapi_resync",
              "derek_context", "census", "blockers", "reactive", "trim"):
        marks.add(n, 0.0, 1.0)
    cli = types.SimpleNamespace(
        healthz=healthz,
        reads=reads if reads is not None else [
            {"phase": "C", "t": 100.0, "path": p, "s": 0.05, "status": 200}
            for p in H.READ_PATHS])
    phases = {"A": 0.0, "B": 30.0, "C": 60.0, "C_settled": 60.0}
    return H._report(types.SimpleNamespace(), {}, cli, _Sampler(max_gap),
                     marks, {"ok": 1, "failed": 0, "s": [0.01]}, phases,
                     _Gc())


def _hz(phase, s, *, probe="OK", probe_s=0.01, t=100.0, db_ok=True):
    return {"phase": phase, "t": t, "s": s, "status": 200, "db_ok": db_ok,
            "db_probe": probe, "db_probe_s": probe_s}


def test_the_verdict_counts_the_apis_share_and_bounds_the_probe_apart():
    H = _harness()
    good = [_hz("A", 0.2), _hz("B", 0.05, probe=None, probe_s=0.0,
                                db_ok=False), _hz("C", 0.02, t=100.0)]
    assert _report(good)["verdict"] == "PASS"
    # a probe that waited its ceiling for a loaded database: the database's
    # time, not the API's -- so long as the API's share stays in bound
    slow_db = good + [_hz("B", 2.3, probe="TIMEOUT", probe_s=2.05,
                          db_ok=False)]
    got = _report(slow_db)
    assert got["verdict"] == "PASS", got["reasons"]
    assert got["healthz_api_share_max_s"] == pytest.approx(0.25)
    # the API's own share over the bound fails
    late = good + [_hz("A", 1.2, probe_s=0.3)]
    assert any(r.startswith("HEALTHZ_OVER_BOUND") for r in
               _report(late)["reasons"])
    # a tree that reports no probe time has its whole answer counted
    old = [dict(_hz("A", 0.9), db_probe=None, db_probe_s=None)] + good[1:]
    assert any(r.startswith("HEALTHZ_OVER_BOUND") for r in
               _report(old)["reasons"])
    # a probe past its own ceiling fails, whatever the share
    stuck = good + [_hz("B", 3.0, probe="TIMEOUT", probe_s=2.9,
                        db_ok=False)]
    assert any(r.startswith("PROBE_PAST_ITS_CEILING") for r in
               _report(stuck)["reasons"])
    # the platform's deadline and an unanswered check always fail
    over = good + [_hz("A", 5.5, probe_s=2.0)]
    assert any(r.startswith("HEALTHZ_OVER_THE_5S_DEADLINE") for r in
               _report(over)["reasons"])
    lost = good + [{"phase": "A", "t": 1.0, "s": 10.0, "status": None,
                    "error": "ReadTimeout"}]
    assert any(r.startswith("HEALTHZ_UNANSWERED") for r in
               _report(lost)["reasons"])
    # the loop's longest hold
    assert any(r.startswith("LOOP_HELD") for r in
               _report(good, max_gap=H.LOOP_HOLD_BOUND_S + 0.01)["reasons"])
    # no recovery: db_ok false after the dependencies cleared
    stale = good[:2] + [_hz("C", 0.02, t=100.0, db_ok=False)]
    assert "NO_RECOVERY:db_ok" in _report(stale)["reasons"]


@needs_pg
def test_this_tree_passes_the_harness():
    out = BACKEND / ".harness_report.json"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tools.api_responsiveness_harness",
             "--phase-a", "10", "--phase-b", "8", "--phase-c", "8",
             "--json", str(out)],
            cwd=str(BACKEND), capture_output=True, text=True, timeout=420,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        report = json.loads(out.read_text())
    finally:
        if out.exists():
            out.unlink()
    tree = report["tree"]
    assert tree["desk_child_parse"] and tree["snapshot_off_loop"]
    assert tree["health_probe_not_queued"] and tree["reactive_batch_index"]
    assert tree["cpu_lane"] and tree["boot_heap_frozen"] > 0
    assert tree["gil_switch_interval_s"] == pytest.approx(0.001)
    for name, w in report["workloads"].items():
        assert w["runs"] >= 1 and not w["errors"], (name, w)
    # phase B saturated the pool and /healthz said so without queueing
    assert report["phases"]["B"]["healthz"]["db_probe"].get(
        "POOL_SATURATED_NOT_QUEUED", 0) >= 1
    assert report["verdict"] == "PASS", (report["reasons"],
                                         report["loop"]["top"][:4],
                                         report.get("slowest_healthz"),
                                         proc.stdout[-400:])
    assert proc.returncode == 0
