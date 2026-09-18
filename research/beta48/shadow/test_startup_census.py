#!/usr/bin/env python3
"""THE STARTUP GATE, EXERCISED THROUGH ITS CALLER.

run_census has always passed its own unit tests. The job never called it: the
workflow curled one `status=in_progress` page straight into venue_domain,
which does not import run_census. Testing run_census alone could not have
caught that, so every test here drives `startup_census.startup_census` -- the
command the workflow now runs -- with an injected fetcher.
"""
import os

import pytest

import run_census as RC
import startup_census as SC
import venue_domain as VD

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SELF = "999"


def _run(rid, name, status="completed"):
    return {"id": rid, "name": name, "status": status,
            "created_at": "2026-09-18T10:00:00Z"}


def fetcher(rows_by_wf, totals=None, errors=None, per_page=SC.PER_PAGE):
    """Serve full history per workflow, paginated like the Actions API."""
    totals, errors = totals or {}, errors or {}

    def fetch(wf, page, pp):
        if wf in errors:
            return [], None, errors[wf]
        rows = list(rows_by_wf.get(wf, []))
        start = (page - 1) * pp
        chunk = rows[start:start + pp]
        return chunk, totals.get(wf, len(rows)), None
    return fetch


@pytest.fixture(scope="module")
def inventory():
    return sorted(VD.venue_touching_workflows(ROOT))


def idle_rows(inventory):
    """Every workflow walked, every run terminal. A genuinely idle domain."""
    return {wf: [_run("t-%s" % wf, wf, "completed")] for wf in inventory}


# ---------------------------------------------------------------- the basics

def test_the_gate_runs_the_census_over_the_discovered_inventory(inventory):
    rep = SC.startup_census(ROOT, fetcher(idle_rows(inventory)), SELF)
    assert rep["STARTUP_CENSUS_OK"] is True
    assert rep["CENSUS_COMPLETE"] is True
    assert rep["COMPLETENESS_BASIS"] == "VERIFIED_PAGINATION_EXHAUSTION"
    assert rep["INVENTORY_SIZE"] == len(inventory) == 19
    assert set(rep["WALKS"]) == set(inventory)
    assert rep["DIRECT_RESEARCH_COLLECTOR_ISOLATION"] == "ESTABLISHED"


def test_the_retrieval_is_not_status_filtered(inventory):
    """The defect: status=in_progress hid every waiting row."""
    rep = SC.startup_census(ROOT, fetcher(idle_rows(inventory)), SELF)
    assert rep["RETRIEVAL_WAS_STATUS_FILTERED"] is False
    # And the census's own vocabulary still covers the waiting states.
    assert set(RC.WAITING_STATES) <= set(rep["OCCUPYING_STATES"])


# ----------------------------------------- 2.1 an active collector blocks

def test_an_active_conflicting_collector_blocks_venue_access(inventory):
    rows = idle_rows(inventory)
    rows["run85-phase2-capture"] = [
        _run("r-live", "run85-phase2-capture", "in_progress")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["CENSUS_COMPLETE"] is True          # the census is fine...
    assert rep["STARTUP_CENSUS_OK"] is False       # ...the domain is not
    assert rep["DIRECT_RESEARCH_COLLECTOR_ISOLATION"] == "NOT_ESTABLISHED"
    assert "run85-phase2-capture" in \
        rep["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"]
    assert "NOT_ESTABLISHED" in rep["REFUSED_BECAUSE"]


# ------------------------------------- 2.2 incomplete pagination blocks

def test_incomplete_pagination_blocks_certification(inventory):
    """A full final page means there may be another. Unresolved, not idle."""
    wf = sorted(inventory)[0]
    rows = idle_rows(inventory)
    rows[wf] = [_run("f-%d" % i, wf) for i in range(SC.PER_PAGE)]
    # Declare MORE than we will serve, so the walk cannot reconcile either.
    rep = SC.startup_census(
        ROOT, fetcher(rows, totals={wf: SC.PER_PAGE * 3}), SELF)
    assert rep["CENSUS_COMPLETE"] is False
    assert rep["STARTUP_CENSUS_OK"] is False
    assert rep["REFUSED_BECAUSE"].startswith("CENSUS_INCOMPLETE")
    assert rep["COMPLETENESS_BASIS"] == RC.NOT_IDENTIFIED
    assert rep["PER_WORKFLOW_WALKS"][wf]["WALK_COMPLETE"] is False
    # An incomplete census may not certify IDLE.
    assert rep["MAY_CERTIFY"]["MAY_CERTIFY_IDLE"] is False


def test_a_retrieval_error_blocks_certification(inventory):
    wf = sorted(inventory)[0]
    rep = SC.startup_census(
        ROOT, fetcher(idle_rows(inventory), errors={wf: "HTTP_502"}), SELF)
    assert rep["CENSUS_COMPLETE"] is False
    assert rep["STARTUP_CENSUS_OK"] is False
    assert "HTTP_502" in str(rep["PER_WORKFLOW_WALKS"][wf]["PROBLEMS"])


def test_a_malformed_response_blocks_and_is_not_an_empty_one(inventory):
    wf = sorted(inventory)[0]
    rep = SC.startup_census(
        ROOT, fetcher(idle_rows(inventory),
                      errors={wf: "MALFORMED_RESPONSE"}), SELF)
    assert rep["STARTUP_CENSUS_OK"] is False
    assert "MALFORMED_RESPONSE" in str(rep["PER_WORKFLOW_WALKS"][wf]["PROBLEMS"])


# --------------------------- 2.3 malformed / unnamed rows cannot look idle

def test_an_unnamed_occupying_row_cannot_look_idle(inventory):
    rows = idle_rows(inventory)
    wf = sorted(inventory)[0]
    rows[wf] = [{"id": "x-1", "status": "in_progress"}]     # no name at all
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["CENSUS_COMPLETE"] is False
    assert rep["STARTUP_CENSUS_OK"] is False
    assert any("no name" in u for u in rep["UNATTRIBUTABLE_OCCUPYING_RUNS"])


def test_a_row_naming_another_workflow_cannot_look_idle(inventory):
    rows = idle_rows(inventory)
    a, b = sorted(inventory)[0], sorted(inventory)[1]
    rows[a] = [_run("x-2", b, "in_progress")]     # walked under a, names b
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["STARTUP_CENSUS_OK"] is False
    assert any("names" in u for u in rep["UNATTRIBUTABLE_OCCUPYING_RUNS"])


def test_a_row_outside_the_inventory_cannot_look_idle(inventory):
    rows = idle_rows(inventory)
    wf = sorted(inventory)[0]
    rows[wf] = [{"id": "x-3", "name": "some-other-workflow",
                 "status": "in_progress"}]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["STARTUP_CENSUS_OK"] is False
    assert rep["UNATTRIBUTABLE_OCCUPYING_RUNS"]


def test_an_unrecognised_status_cannot_look_idle(inventory):
    rows = idle_rows(inventory)
    wf = sorted(inventory)[0]
    rows[wf] = [_run("x-4", wf, "sleepwalking")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["STARTUP_CENSUS_OK"] is False
    assert "sleepwalking" in rep["UNRECOGNISED_STATUSES"]


def test_an_empty_inventory_is_refused_by_name(tmp_path):
    """domain_idle fails OPEN on an empty `known` list, so the runtime path
    must refuse before it ever gets there."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "research").mkdir()
    rep = SC.startup_census(str(tmp_path), fetcher({}), SELF)
    assert rep["STARTUP_CENSUS_OK"] is False
    assert rep["REFUSED_BECAUSE"] == "EMPTY_DISCOVERED_WORKFLOW_INVENTORY"
    assert rep["INVENTORY_SIZE"] == 0


# ------------------- 2.4 queued followers reported under the approved policy

def test_a_queued_follower_is_reported_and_blocks_for_the_stated_reason(
        inventory):
    """THE APPROVED POLICY, UNCHANGED AND NOT BENT TO PASS.

    `venue_domain.isolation` sets clear = not active and not waiting: a
    WAITING domain member blocks a start. The reason on the record is
    displacement, NOT venue load -- "a newer queued run displaces an older
    pending one, so a domain with a waiting member is not a domain we may
    start an experiment in".

    Both halves matter here:
      * the waiting rows must be VISIBLE at all (status=in_progress hid them);
      * they are counted as PENDING_DIRECT_CONFLICTS, never folded into
        ACTIVE_DIRECT_CONFLICTS, so a compliant waiter is never reported as
        actual venue load.
    """
    rows = idle_rows(inventory)
    rows["beta48-forward-capture"] = [
        _run("q-1", "beta48-forward-capture", "queued")]
    rows["beta48-shadow-tick"] = [
        _run("q-2", "beta48-shadow-tick", "pending")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)

    assert rep["CENSUS_COMPLETE"] is True
    assert rep["GATE_POPULATION"] == 2            # both are VISIBLE
    assert rep["QUEUED_FOLLOWERS"] == ("beta48-forward-capture",
                                       "beta48-shadow-tick")
    assert rep["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"] == []
    assert sorted(rep["KNOWN_DIRECT_PMUS_COLLECTORS_PENDING"]) == [
        "beta48-forward-capture", "beta48-shadow-tick"]
    # Counted apart from active load -- they are not venue traffic...
    assert rep["ACTIVE_DIRECT_CONFLICTS"] == 0
    assert rep["PENDING_DIRECT_CONFLICTS"] == 2
    # ...and under the approved policy a waiting member still blocks a START.
    assert rep["DIRECT_RESEARCH_COLLECTOR_ISOLATION"] == "NOT_ESTABLISHED"
    assert rep["STARTUP_CENSUS_OK"] is False
    # THE REFUSAL MUST NAME THE RIGHT CAUSE. Reporting "active: unknown"
    # when the active list is simply empty would describe a displacement
    # block as though the venue were being read.
    why = rep["REFUSED_BECAUSE"]
    assert "WAITING_DIRECT_COLLECTORS" in why
    assert "RUNNING_DIRECT_COLLECTORS" not in why
    assert "beta48-forward-capture" in why


def test_a_waiting_row_is_never_relabelled_as_active_load(inventory):
    rows = idle_rows(inventory)
    rows["venue-probe"] = [_run("q-3", "venue-probe", "waiting")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert "venue-probe" not in rep["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"]
    assert rep["ACTIVE_DIRECT_CONFLICTS"] == 0      # not actual venue load
    assert rep["PENDING_DIRECT_CONFLICTS"] == 1     # but seen, and named
    assert rep["QUEUED_FOLLOWERS"] == ("venue-probe",)


def test_running_beats_waiting_when_both_are_present(inventory):
    rows = idle_rows(inventory)
    rows["venue-probe"] = [_run("q-4", "venue-probe", "queued")]
    rows["run85-phase2a"] = [_run("r-5", "run85-phase2a", "in_progress")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["STARTUP_CENSUS_OK"] is False
    assert rep["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"] == ["run85-phase2a"]
    assert rep["KNOWN_DIRECT_PMUS_COLLECTORS_PENDING"] == ["venue-probe"]


def test_this_run_itself_is_excluded_where_the_gate_requires_it(inventory):
    """The dispatched run holds the slot; it is not its own confound."""
    rows = idle_rows(inventory)
    rows["beta48-substantive-capture"] = [
        _run(SELF, "beta48-substantive-capture", "in_progress")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    assert rep["STARTUP_CENSUS_OK"] is True
    assert rep["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"] == []
    assert SELF not in rep["QUEUED_FOLLOWERS"]


# ------------------------------------------------- the policy is unchanged

def test_the_gate_applies_venue_domains_own_isolation_verdict(inventory):
    """No second opinion: the same function, on the complete population."""
    rows = idle_rows(inventory)
    rows["run85-phase2b"] = [_run("r-6", "run85-phase2b", "in_progress")]
    rep = SC.startup_census(ROOT, fetcher(rows), SELF)
    direct = VD.isolation(list(rep["GATE_INPUT"]), sorted(inventory), SELF)
    for k in ("ACTIVE_DIRECT_CONFLICTS", "PENDING_DIRECT_CONFLICTS",
              "DIRECT_RESEARCH_COLLECTOR_ISOLATION",
              "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION"):
        assert rep[k] == direct[k]
