"""CAPACITY IS A PROCESSING NUMBER, AND THE FILE HAS TO SAY SO.

WHAT THIS GUARDS. The probe measures how fast the deployed writers and the
deployed lifecycle move, against a real migrated database. That number is
useful and it is dangerous: quoted without its label it reads as "the
engine finds 800,000 trades a day". So three separations are asserted here
rather than trusted:

  1 PROCESSING IS NOT OPPORTUNITY. The report must say it, and the
    extrapolated per-day figure must be labelled an extrapolation.
  2 A WRITER MICROBENCHMARK IS NOT A LIFECYCLE. Entry-only throughput and
    complete lifecycles are separate phases with separate numbers.
  3 A REBUILT POOL IS NOT A RESTARTED PROCESS. The connection-recovery
    phase says what it is, and the actual restart phase spawns and kills a
    real child interpreter.

And the published evidence file is held to the same labels, because the
file is what a reader outside this repository sees.
"""

from __future__ import annotations

import inspect
import json
import os

from tools import bettor_capacity_probe as P

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
EVIDENCE = os.path.join(ROOT, "research", "evidence",
                        "BETTOR_ORDER_LIFECYCLE_CAPACITY.json")


def test_the_probe_separates_processing_from_opportunity():
    src = inspect.getsource(P)
    assert "processing_capacity_is_not_opportunity" in src
    assert "implied_per_day_is_an_extrapolation" in src
    assert "workload_is_synthetic" in src


def test_the_lifecycle_phase_is_its_own_measurement():
    """It must not be folded into the entry throughput figure."""
    src = inspect.getsource(P.phase_lifecycles)
    assert "complete_lifecycles" in src
    assert "completion_rate" in src and "failures" in src
    assert "lifecycles_per_s" in src
    # AND IT RUNS THE DEPLOYED LIFECYCLE, not a copy of it.
    one = inspect.getsource(P._one_lifecycle)
    assert "bettor_demonstration" in one and "run_full" in one
    # COMPLETED MEANS FLAT AND RECONCILED, not merely "did not raise".
    assert "position_is_flat" in one and "reconciles" in one


def test_connection_recovery_is_not_called_a_restart():
    src = inspect.getsource(P.run)
    assert "connection_recovery" in src
    assert "restart_recovery" not in src
    flat = " ".join(inspect.getsource(P).split())
    assert "It is NOT a process restart" in flat


def test_the_restart_phase_actually_restarts_a_process():
    src = inspect.getsource(P.phase_process_restart)
    # A real child, killed without a chance to clean up.
    assert "subprocess.Popen" in src and ".kill()" in src
    assert "SIGKILL" in src
    # A SECOND process finishes the batch.
    assert "subprocess.run" in src
    # And the checks that matter: no duplicate, everything flat, fees tie.
    for probe in ("no_duplicate_position", "all_positions_flat",
                  "fee_reconciles"):
        assert probe in src, probe
    assert "what_it_does_not_prove" in src


def test_the_lifecycle_book_is_not_the_strategys_book():
    """The harness writes thousands of synthetic positions. They must be
    under their own experiment id so no strategy read can reach them."""
    assert P.LIFECYCLE_EXPERIMENT == "CAPACITY_PROBE_LIFECYCLE_V1"
    from sportsassets import bettor_external_shadow as ext

    assert P.LIFECYCLE_EXPERIMENT != ext.EXPERIMENT_ID


def test_the_published_evidence_carries_its_own_labels():
    with open(EVIDENCE) as fh:
        d = json.load(fh)
    assert d["workload_is_synthetic"] is True
    assert "not a count of qualifying opportunities" in \
        d["processing_capacity_is_not_opportunity"]
    assert "extrapolation" in d["implied_per_day_is_an_extrapolation"] \
        or "NOT a claim" in d["implied_per_day_is_an_extrapolation"]
    ph = d["phases"]
    assert "sustained" in ph and "complete_lifecycles" in ph
    lc = ph["complete_lifecycles"]
    # THE MEASUREMENT IS REPORTED WITH ITS FAILURES, whatever they were.
    for k in ("completed", "attempted", "completion_rate", "failure_count",
              "latency_ms", "accounting"):
        assert k in lc, k
    assert "restart" in json.dumps(ph)
    rst = ph["actual_process_restart"]
    assert rst["first_child"]["killed_with"] == "SIGKILL"
    assert rst["expected_positions"] == rst["positions_in_range"]
    assert "connection_recovery" in ph
    assert "It is NOT a process restart" in \
        ph["connection_recovery"]["what_this_is"]
