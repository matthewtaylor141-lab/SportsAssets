"""The census that feeds the clean-start gate must fail closed.

Reproduces both ways the gate was fed badly in this session.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

import os

import run_census as C
import venue_domain


def _repo_root():
    d = os.path.dirname(os.path.abspath(__file__))
    while d != "/" and not os.path.isdir(os.path.join(d, ".github", "workflows")):
        d = os.path.dirname(d)
    return d


# audit(".") from this directory finds no workflows and returns an EMPTY known
# list, and an empty known list makes the gate read 0 conflicts and fail open.
# The root is resolved explicitly and the list is asserted non-empty.
KNOWN = venue_domain.audit(_repo_root())["KNOWN_VENUE_TOUCHING_WORKFLOWS"]


def test_the_known_list_is_not_empty():
    """The fail-open the standing rule warns about: known=[] reads idle."""
    assert len(KNOWN) >= 19
    assert "run85-phase2-capture" in KNOWN
    assert "beta48-forward-capture" in KNOWN
    empty = venue_domain.domain_idle(
        [run(1, "run85-phase2-capture", "in_progress")], [])
    assert empty["ACTIVE_DIRECT_CONFLICTS"] == 0      # fails OPEN on known=[]


def run(rid, name, status):
    return {"id": rid, "name": name, "status": status}


def test_a_pending_run_is_an_occupying_run():
    """GitHub reports a concurrency-held run as `pending`, which the API's
    own status filter cannot express."""
    assert "pending" in C.WAITING_STATES
    c = C.census([[run(1, "run85-phase2-capture", "in_progress"),
                   run(2, "beta48-forward-capture", "pending")]], 2)
    assert c["CENSUS_COMPLETE"] is True
    assert len(c["GATE_INPUT"]) == 2


def test_the_gate_sees_the_pending_collector_when_the_census_is_complete():
    c = C.census([[run(1, "run85-phase2-capture", "in_progress"),
                   run(2, "beta48-forward-capture", "pending")]], 2)
    g = venue_domain.domain_idle(list(c["GATE_INPUT"]), KNOWN)
    assert g["ACTIVE_DIRECT_CONFLICTS"] == 1
    assert g["PENDING_DIRECT_CONFLICTS"] == 1
    assert g["DOMAIN_IDLE"] == "NO"


def test_supplying_only_the_running_run_hides_the_pending_one():
    """The defect, reproduced: the gate is right and the input was short."""
    g = venue_domain.domain_idle(
        [run(1, "run85-phase2-capture", "in_progress")], KNOWN)
    assert g["PENDING_DIRECT_CONFLICTS"] == 0     # reads clean, wrongly
    full = venue_domain.domain_idle(
        [run(1, "run85-phase2-capture", "in_progress"),
         run(2, "beta48-forward-capture", "pending")], KNOWN)
    assert full["PENDING_DIRECT_CONFLICTS"] == 1


def test_a_short_retrieval_is_refused():
    c = C.census([[run(1, "run85-phase2-capture", "in_progress")]], 4)
    assert c["CENSUS_COMPLETE"] is False
    assert "retrieved 1 of 4" in c["WHY_NOT_COMPLETE"]
    assert C.may_evaluate_gate(c)["MAY_EVALUATE_GATE"] is False


def test_a_retrieval_error_is_refused():
    c = C.census([[run(1, "beta48-forward-capture", "pending")]], 1,
                 errors=("page 2: 502",))
    assert c["CENSUS_COMPLETE"] is False
    assert C.may_evaluate_gate(c)["MAY_EVALUATE_GATE"] is False


def test_an_unrecognised_status_is_refused_rather_than_ignored():
    c = C.census([[run(1, "run85-phase2-capture", "some_new_state")]], 1)
    assert c["CENSUS_COMPLETE"] is False
    assert "some_new_state" in c["UNRECOGNISED_STATUSES"]


def test_an_empty_complete_census_may_evaluate_and_reads_idle():
    c = C.census([[]], 0)
    assert c["CENSUS_COMPLETE"] is True
    assert C.may_evaluate_gate(c)["MAY_EVALUATE_GATE"] is True
    g = venue_domain.domain_idle(list(c["GATE_INPUT"]), KNOWN)
    assert g["ACTIVE_DIRECT_CONFLICTS"] == 0
    assert g["PENDING_DIRECT_CONFLICTS"] == 0


def test_duplicate_rows_across_pages_are_collapsed_by_run_id():
    c = C.census([[run(1, "run85-phase2-capture", "in_progress")],
                  [run(1, "run85-phase2-capture", "in_progress")]], 1)
    assert c["RUNS_RETRIEVED"] == 1
    assert c["CENSUS_COMPLETE"] is True


def test_a_dispatch_acknowledgement_is_not_acquisition():
    assert C.acquired({"jobs": {"total_count": 0, "jobs": []}})[
        "ACQUIRED"] is False
    got = C.acquired({"jobs": {"total_count": 1, "jobs": [
        {"created_at": "2026-09-18T03:22:00Z",
         "started_at": "2026-09-18T03:22:03Z"}]}})
    assert got["ACQUIRED"] is True
    assert got["JOB_CREATED_AT"] == "2026-09-18T03:22:00Z"
