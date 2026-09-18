"""The census that feeds the clean-start gate must fail closed.

Reproduces every way the gate has been fed badly in this session.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

import os

import pytest

import run_census as C
import venue_domain


def _repo_root():
    d = os.path.dirname(os.path.abspath(__file__))
    while d != "/" and not os.path.isdir(
            os.path.join(d, ".github", "workflows")):
        d = os.path.dirname(d)
    return d


# audit(".") from THIS directory finds no workflows and returns an EMPTY
# inventory, and an empty inventory makes the gate read 0 conflicts.
KNOWN = venue_domain.audit(_repo_root())["KNOWN_VENUE_TOUCHING_WORKFLOWS"]

PER_PAGE = 100


def run(rid, name, status, **over):
    r = {"id": rid, "name": name, "status": status}
    r.update(over)
    return r


def walk(workflow, rows, per_page=PER_PAGE, total=None, errors=()):
    """A one-page walk that terminates: the page is short, so it is final."""
    return C.workflow_walk(workflow, [list(rows)], per_page,
                           total_count=total if total is not None
                           else len(rows), errors=errors)


def full_walks(rows_by_wf):
    return {wf: walk(wf, rows) for wf, rows in rows_by_wf.items()}


def inventory_walks(rows_by_wf):
    """Every workflow in the real inventory gets a walk; most are empty."""
    walks = {wf: walk(wf, []) for wf in KNOWN}
    walks.update(full_walks(rows_by_wf))
    return walks


# --- The inventory itself ------------------------------------------------

def test_the_discovered_inventory_is_not_empty():
    assert len(KNOWN) >= 19
    assert "run85-phase2-capture" in KNOWN
    assert "beta48-forward-capture" in KNOWN


def test_an_empty_inventory_fails_open_at_the_gate_so_the_census_refuses_it():
    """The fail-open the standing rule warns about, and the refusal."""
    fails_open = venue_domain.domain_idle(
        [run(1, "run85-phase2-capture", "in_progress")], [])
    assert fails_open["ACTIVE_DIRECT_CONFLICTS"] == 0      # reads idle

    c = C.census(full_walks({"run85-phase2-capture": [
        run(1, "run85-phase2-capture", "in_progress")]}), inventory=[])
    assert c["CENSUS_COMPLETE"] is False
    assert "EMPTY_WORKFLOW_INVENTORY" in c["WHY_NOT_COMPLETE"]
    assert C.may_certify(c)["MAY_CERTIFY_IDLE"] is False


def test_a_workflow_in_the_inventory_with_no_walk_is_refused():
    c = C.census(full_walks({"run85-phase2-capture": []}), inventory=KNOWN)
    assert c["CENSUS_COMPLETE"] is False
    assert "no walk for:" in c["WHY_NOT_COMPLETE"]
    assert "beta48-forward-capture" in c["WHY_NOT_COMPLETE"]


# --- Defect 3: the withdrawn timeout horizon -----------------------------

def test_the_timeout_completeness_path_is_gone():
    """timeout-minutes bounds job execution, not workflow age or queue wait."""
    assert not hasattr(C, "DOMAIN_MAX_TIMEOUT_MINUTES")
    assert "covers_minutes" not in C.census.__code__.co_varnames
    assert "TIMEOUT" not in C.census(
        inventory_walks({}), inventory=KNOWN)["COMPLETENESS_BASIS"]


def test_the_measured_counterexample_to_the_timeout_argument():
    """beta48-forward-capture 35224744353: created 13:03:43Z, job created
    16:16:18Z, job completed 16:49:39Z. Age 3h46m under a 120-minute
    timeout, because 3h12m38s of it was waiting for the group."""
    from datetime import datetime
    t = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    age = (t("2026-09-17T16:49:39Z") - t("2026-09-17T13:03:43Z"))
    runtime = (t("2026-09-17T16:49:39Z") - t("2026-09-17T16:16:21Z"))
    assert age.total_seconds() / 60 > 120          # exceeds its own timeout
    assert runtime.total_seconds() / 60 < 120      # while executing well under
    assert "3h12m38s" in C.A_TIMEOUT_DOES_NOT_BOUND_A_WORKFLOW_AGE


# --- Pagination exhaustion -----------------------------------------------

def test_a_full_final_page_leaves_pagination_unresolved():
    rows = [run(i, "run85-phase2-capture", "completed") for i in range(5)]
    w = C.workflow_walk("run85-phase2-capture", [rows], per_page=5,
                        total_count=5)
    assert w["WALK_COMPLETE"] is False
    assert w["PAGINATION_EXHAUSTED"] is False
    assert "pagination unresolved" in " ".join(w["PROBLEMS"])


def test_a_short_final_page_exhausts_the_walk():
    rows = [run(i, "run85-phase2-capture", "completed") for i in range(4)]
    w = C.workflow_walk("run85-phase2-capture", [rows], per_page=5,
                        total_count=4)
    assert w["WALK_COMPLETE"] is True
    assert w["PAGINATION_EXHAUSTED"] is True


def test_the_unique_ids_are_reconciled_with_the_api_count():
    rows = [run(1, "run85-phase2-capture", "completed")]
    w = C.workflow_walk("run85-phase2-capture", [rows], per_page=5,
                        total_count=9)
    assert w["WALK_COMPLETE"] is False
    assert "reconcile failed: 1 unique ids vs API total 9" in \
        " ".join(w["PROBLEMS"])


def test_a_failed_request_leaves_the_walk_incomplete():
    rows = [run(1, "run85-phase2-capture", "completed")]
    w = C.workflow_walk("run85-phase2-capture", [rows], per_page=5,
                        total_count=1, errors=("page 2: 502",))
    assert w["WALK_COMPLETE"] is False
    assert "retrieval errors" in " ".join(w["PROBLEMS"])


def test_an_oversized_page_is_an_inconsistent_traversal():
    rows = [run(i, "run85-phase2-capture", "completed") for i in range(7)]
    w = C.workflow_walk("run85-phase2-capture", [rows], per_page=5,
                        total_count=7)
    assert w["WALK_COMPLETE"] is False
    assert "inconsistent traversal" in " ".join(w["PROBLEMS"])


def test_no_pages_at_all_is_not_an_exhausted_walk():
    w = C.workflow_walk("run85-phase2-capture", [], per_page=5, total_count=0)
    assert w["WALK_COMPLETE"] is False
    assert "no pages retrieved" in " ".join(w["PROBLEMS"])


# --- What a census may certify -------------------------------------------

def test_a_complete_census_over_the_real_inventory_may_certify_idle():
    c = C.census(inventory_walks({}), inventory=KNOWN,
                 taken_at="2026-09-18T03:22:00Z")
    assert c["CENSUS_COMPLETE"] is True
    assert c["COMPLETENESS_BASIS"] == "VERIFIED_PAGINATION_EXHAUSTION"
    assert C.may_certify(c, age_s=5)["MAY_CERTIFY_IDLE"] is True
    g = venue_domain.domain_idle(list(c["GATE_INPUT"]), KNOWN)
    assert g["ACTIVE_DIRECT_CONFLICTS"] == 0
    assert g["PENDING_DIRECT_CONFLICTS"] == 0


def test_a_stale_census_may_not_certify_idle():
    c = C.census(inventory_walks({}), inventory=KNOWN)
    assert C.may_certify(c, age_s=C.MAX_CENSUS_AGE_S + 1)[
        "MAY_CERTIFY_IDLE"] is False


def test_an_incomplete_census_may_still_certify_blocked():
    """Blocking is monotone: a conflict observed cannot be un-observed."""
    partial = C.census(
        full_walks({"run85-phase2-capture": [
            run(1, "run85-phase2-capture", "in_progress")]}),
        inventory=KNOWN)
    assert partial["CENSUS_COMPLETE"] is False        # walks are missing
    m = C.may_certify(partial)
    assert m["MAY_CERTIFY_IDLE"] is False
    assert m["MAY_CERTIFY_BLOCKED"] is True
    g = venue_domain.domain_idle(list(partial["GATE_INPUT"]), KNOWN)
    assert g["ACTIVE_DIRECT_CONFLICTS"] == 1


def test_a_pending_run_is_an_occupying_run():
    assert "pending" in C.WAITING_STATES
    c = C.census(inventory_walks({
        "run85-phase2-capture": [run(1, "run85-phase2-capture",
                                     "in_progress")],
        "beta48-forward-capture": [run(2, "beta48-forward-capture",
                                       "pending")]}), inventory=KNOWN)
    assert c["CENSUS_COMPLETE"] is True
    g = venue_domain.domain_idle(list(c["GATE_INPUT"]), KNOWN)
    assert g["ACTIVE_DIRECT_CONFLICTS"] == 1
    assert g["PENDING_DIRECT_CONFLICTS"] == 1


def test_supplying_only_the_running_run_hides_the_pending_one():
    only_running = venue_domain.domain_idle(
        [run(1, "run85-phase2-capture", "in_progress")], KNOWN)
    assert only_running["PENDING_DIRECT_CONFLICTS"] == 0
    both = venue_domain.domain_idle(
        [run(1, "run85-phase2-capture", "in_progress"),
         run(2, "beta48-forward-capture", "pending")], KNOWN)
    assert both["PENDING_DIRECT_CONFLICTS"] == 1


def test_an_unrecognised_status_is_refused_rather_than_ignored():
    c = C.census(inventory_walks({
        "run85-phase2-capture": [run(1, "run85-phase2-capture",
                                     "some_new_state")]}), inventory=KNOWN)
    assert c["CENSUS_COMPLETE"] is False
    assert "some_new_state" in c["UNRECOGNISED_STATUSES"]


# --- Defect 2: normalising the jobs response ------------------------------

JOB = {"run_id": 42, "name": "capture",
       "created_at": "2026-09-18T03:22:00Z",
       "started_at": "2026-09-18T03:22:03Z"}


def test_a_bare_jobs_list_no_longer_raises():
    """acquired({"jobs": [job]}) called .get on a list and raised
    AttributeError."""
    got = C.acquired({"jobs": [JOB]}, expected_run_id=42)
    assert got["ACQUIRED"] is True
    assert got["RESPONSE_SHAPE"] == "GITHUB_JOBS_LIST"
    assert got["JOB_CREATED_AT"] == "2026-09-18T03:22:00Z"


def test_the_connector_wrapped_shape_is_supported():
    got = C.acquired({"jobs": {"total_count": 1, "jobs": [JOB]}},
                     expected_run_id=42)
    assert got["ACQUIRED"] is True
    assert got["RESPONSE_SHAPE"] == "CONNECTOR_WRAPPED_JOBS_LIST"


@pytest.mark.parametrize("payload", [
    None, [], "jobs", {"total_count": 1}, {"jobs": 7}, {"jobs": {"x": 1}},
])
def test_a_malformed_response_is_refused_not_read_as_empty(payload):
    got = C.acquired(payload, expected_run_id=42)
    assert got["ACQUIRED"] is False
    assert got["RESPONSE_STATUS"] == "MALFORMED"
    assert got["JOB_COUNT"] == C.NOT_IDENTIFIED     # not 0 -- unknown


def test_an_empty_jobs_list_is_parsed_and_is_not_acquisition():
    got = C.acquired({"jobs": {"total_count": 0, "jobs": []}},
                     expected_run_id=42)
    assert got["RESPONSE_STATUS"] == "PARSED"
    assert got["ACQUIRED"] is False
    assert got["JOB_COUNT"] == 0


def test_a_job_from_another_run_is_not_our_acquisition():
    other = dict(JOB, run_id=99)
    got = C.acquired({"jobs": [other]}, expected_run_id=42)
    assert got["ACQUIRED"] is False
    assert got["MATCHING_JOBS"] == 0
    assert "JOB_BELONGS_TO_RUN_99" in got["REJECTED_JOBS"]


def test_a_differently_named_job_is_not_the_capture_job():
    other = dict(JOB, name="lint")
    got = C.acquired({"jobs": [other]}, expected_run_id=42)
    assert got["ACQUIRED"] is False
    assert "JOB_NAMED_lint" in got["REJECTED_JOBS"]


def test_the_expected_capture_job_name_is_the_real_one():
    """Both venue collectors name their job `capture`; the dispatched
    substantive capture does too."""
    assert C.CAPTURE_JOB_NAME == "capture"


def test_a_non_mapping_job_record_is_rejected_not_indexed():
    got = C.acquired({"jobs": ["not-a-job", JOB]}, expected_run_id=42)
    assert got["ACQUIRED"] is True                  # the real one still counts
    assert "NON_MAPPING_JOB_RECORD" in got["REJECTED_JOBS"]


# --- An occupying run the gate cannot attribute to a domain member -------
#
# venue_domain.domain_idle() matches a run to the domain by its WORKFLOW
# NAME. A row with no name matches nothing, so a census that FOUND the run
# still hands the gate something it reads as an idle domain. Blocking is
# monotone only if the census refuses to certify such a census complete.

def test_an_unnamed_occupying_run_fails_open_at_the_gate():
    """The fail-open itself, before the refusal that now covers it."""
    nameless = {"id": 35274725330, "status": "in_progress"}
    fails_open = venue_domain.domain_idle([nameless], KNOWN)
    assert fails_open["ACTIVE_DIRECT_CONFLICTS"] == 0
    assert fails_open["DOMAIN_IDLE"] == "YES"          # over a live collector


def test_the_census_refuses_an_unnamed_occupying_run():
    walks = inventory_walks({"run85-phase2-capture": [
        {"id": 35274725330, "status": "in_progress"}]})
    c = C.census(walks, inventory=KNOWN)
    assert c["CENSUS_COMPLETE"] is False
    assert "cannot attribute" in c["WHY_NOT_COMPLETE"]
    assert "35274725330" in c["WHY_NOT_COMPLETE"]
    assert c["UNATTRIBUTABLE_OCCUPYING_RUNS"]
    assert C.may_certify(c, age_s=5)["MAY_CERTIFY_IDLE"] is False


def test_it_may_still_certify_blocked_over_an_unnamed_occupying_run():
    """Monotone: the run was observed, so BLOCKED survives the refusal."""
    walks = inventory_walks({"run85-phase2-capture": [
        {"id": 35274725330, "status": "in_progress"}]})
    v = C.may_certify(C.census(walks, inventory=KNOWN), age_s=5)
    assert v["MAY_CERTIFY_BLOCKED"] is True
    assert v["OBSERVED_CONFLICTS"] == 1


def test_an_occupying_row_naming_another_workflow_is_refused():
    walks = inventory_walks({"run85-phase2-capture": [
        run(35274725330, "beta48-forward-capture", "in_progress")]})
    c = C.census(walks, inventory=KNOWN)
    assert c["CENSUS_COMPLETE"] is False
    assert "walked under run85-phase2-capture" in c["WHY_NOT_COMPLETE"]


def test_a_completed_row_needs_no_name_because_it_occupies_nothing():
    """The check is on the OCCUPYING rows only -- history stays cheap."""
    walks = inventory_walks({"run85-phase2-capture": [
        {"id": 1, "status": "completed"}]})
    c = C.census(walks, inventory=KNOWN)
    assert c["CENSUS_COMPLETE"] is True
    assert c["UNATTRIBUTABLE_OCCUPYING_RUNS"] == ()


def test_every_waiting_state_is_covered_by_the_attribution_check():
    for state in C.WAITING_STATES + C.RUNNING_STATES:
        walks = inventory_walks({"beta48-forward-capture": [
            {"id": 99, "status": state}]})
        c = C.census(walks, inventory=KNOWN)
        assert c["CENSUS_COMPLETE"] is False, state
        assert "cannot attribute" in c["WHY_NOT_COMPLETE"], state
