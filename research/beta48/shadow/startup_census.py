#!/usr/bin/env python3
"""THE STARTUP GATE. The repaired census, in the command the job actually runs.

THE INTEGRATION GAP THIS CLOSES.

The workflow's pre-GET isolation step did this:

    curl ".../actions/runs?status=in_progress&per_page=100" -o active.json
    python venue_domain.py --active-runs active.json --mode start

`venue_domain._cli` reads the rows it is handed. It does not import
`run_census` and never has. So the repaired census -- unfiltered retrieval per
discovered workflow, pagination walked to verified exhaustion, a required
non-empty inventory, reconciliation against the API's own totals, and the
unattributable-row check -- was never executed by the job. One status-filtered
page was, and the gate was applied to that.

Two consequences, both bad in the same direction:

  * `status=in_progress` HIDES every waiting row. queued / pending / requested
    / waiting are exactly the states a concurrency-held collector sits in, so
    the filter removed the population the gate exists to see.
  * A single page is not an enumeration. One page that happened to be short
    proved nothing about the rest, and nothing reconciled it against the
    API's own count.

This module is the caller. It runs the census machinery unchanged, refuses by
name on anything incomplete, and only then applies the UNCHANGED isolation
policy to the complete population.

TWO VERDICTS, KEPT APART.

`venue_domain.isolation` is unchanged and is reported as ADMISSION_VERDICT:
before the slot, a waiting member blocks a START, because GitHub keeps one
pending run per group and a newer arrival displaces an older waiter.

GATE 3 runs INSIDE the job, which means the slot is already held, and it
therefore asks the other question -- POST_ACQUISITION_ISOLATION. A waiting
follower cannot be displaced by a run that is already executing and issues no
venue request while it waits, so it is REPORTED rather than treated as
competing execution. That relaxation is conditional on OWNERSHIP BEING PROVEN:
our own run must appear in the complete census in an executing state and our
own workflow must be a member of the group. Another EXECUTING collector,
unknown ownership, an incomplete census, an unattributable occupying row or
incompatible group membership all still block every venue read, by name.

The exit code follows POST_ACQUISITION_ISOLATION, because that is the question
the gate's position in the job actually poses. Both verdicts are written to
the evidence file so neither can be read as the other.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import run_census as RC
import venue_domain as VD

NOT_IDENTIFIED = "NOT_IDENTIFIED"

PER_PAGE = 100
MAX_PAGES = 100          # runaway bound; hitting it leaves the walk unresolved

# The walk is UNFILTERED on purpose. See the module docstring.
A_STATUS_FILTER_HIDES_THE_QUEUE = (
    "status=in_progress removes queued/pending/requested/waiting -- the exact "
    "states a concurrency-held collector occupies. The census walks every run "
    "and lets the POLICY, not the query, decide what a waiting row means")

WAITING_IS_NOT_VENUE_LOAD = (
    "a compliant collector waiting on the concurrency group issues no request "
    "while it waits. It is reported as a queued follower and is not counted "
    "as active venue load. That is the approved policy, unchanged")


def runs_url(repo, wf_file):
    return ("https://api.github.com/repos/%s/actions/workflows/%s/runs"
            % (repo, wf_file))


def walk_workflow(fetch, wf, per_page=PER_PAGE, max_pages=MAX_PAGES):
    """Page one workflow's FULL run history and hand it to the census walker.

    `fetch(wf, page, per_page)` returns (rows, total_count, error). Injected so
    the whole gate can be rehearsed offline against fixtures.
    """
    pages, total, errors = [], None, []
    for page in range(1, max_pages + 1):
        try:
            rows, tc, err = fetch(wf, page, per_page)
        except Exception as e:                                # noqa: BLE001
            errors.append("%s page %d: %s: %s" % (wf, page, type(e).__name__, e))
            break
        if err:
            errors.append("%s page %d: %s" % (wf, page, err))
            break
        if tc is not None:
            total = tc
        rows = list(rows or ())
        pages.append(rows)
        if len(rows) < per_page:
            break                       # a short page is the end of THIS walk
    return RC.workflow_walk(wf, pages, per_page, total_count=total,
                            errors=errors)


ADMISSION_RULE = (
    "BEFORE the slot: clear = not active and not waiting. A waiting member "
    "blocks a START because GitHub keeps one pending run per group and a "
    "newer arrival displaces an older waiter. venue_domain.isolation, "
    "unchanged.")

POST_ACQUISITION_RULE = (
    "AFTER proven ownership of the slot: a verified same-group waiting "
    "follower is REPORTED, not treated as competing execution. It cannot "
    "displace a run that is already executing, and it issues no venue "
    "request while it waits. Another EXECUTING collector, unknown "
    "ownership, an incomplete census, an unattributable occupying row or "
    "incompatible group membership still block every venue read.")

OWNERSHIP_IS_PROVEN_NOT_ASSUMED = (
    "the job does not assume it holds the slot because it is running code. "
    "Its own run must appear in the complete census in an executing state, "
    "and its own workflow must be a member of the group -- otherwise the "
    "waiters it is about to excuse are not waiting on anything it holds.")

B_SELF_NOT_FOUND = "SELF_RUN_NOT_IN_CENSUS"
B_SELF_NOT_EXECUTING = "SELF_RUN_NOT_EXECUTING"
B_SELF_WORKFLOW_UNKNOWN = "SELF_WORKFLOW_NOT_IDENTIFIED"
B_SELF_OUTSIDE_GROUP = "SELF_WORKFLOW_OUTSIDE_THE_GROUP"
B_GROUP_INCOMPATIBLE = "INCOMPATIBLE_GROUP_MEMBERSHIP"
B_CENSUS = "CENSUS_INCOMPLETE"
B_UNATTRIBUTABLE = "UNATTRIBUTABLE_OCCUPYING_RUNS"
B_COMPETING = "COMPETING_EXECUTION"


def post_acquisition(cen, inventory, rep, self_run_id, self_workflow):
    """THE POST-ACQUISITION VERDICT, kept apart from admission.

    WHY THE TWO DIFFER. `venue_domain.isolation` answers "may this run
    START?" -- and there a waiting member blocks, because a newer
    arrival displaces an older pending one and starting would risk
    dropping a compliant collector. That rule is correct, unchanged, and
    still reported as ADMISSION_VERDICT.

    GATE 3 asks a different question. By the time it executes, the job
    IS running: it already holds the concurrency slot, so nothing it does
    can displace a waiter, and a waiter issues no venue request while it
    waits. Blocking on a queued follower there does not protect anything
    -- it just refuses a run that already won the slot, which is how the
    previous attempt ended up cancelled rather than collected.

    OWNERSHIP IS PROVEN, NOT ASSUMED. Excusing waiters is only sound if
    we really hold the slot they are waiting on, so this requires our own
    run to be in the complete census in an executing state AND our own
    workflow to be a member of the group. Everything else still blocks,
    by name.
    """
    blockers, notes = [], {}

    gate_input = list(cen.get("GATE_INPUT") or ())
    mine = [r for r in gate_input if str(r.get("id")) == str(self_run_id)]
    if self_run_id is None or not mine:
        blockers.append(B_SELF_NOT_FOUND)
    else:
        st = (mine[0].get("status") or "").lower()
        notes["SELF_RUN_STATUS"] = st
        if st not in RC.RUNNING_STATES:
            blockers.append(B_SELF_NOT_EXECUTING)

    # GROUP MEMBERSHIP, ours and everybody's.
    if not self_workflow:
        blockers.append(B_SELF_WORKFLOW_UNKNOWN)
    elif self_workflow not in set(inventory):
        blockers.append(B_SELF_OUTSIDE_GROUP)
    outside = dict(rep.get("WORKFLOWS_OUTSIDE_THE_DOMAIN") or {})
    if outside or rep.get("DOMAIN_AUDIT") != "PASS":
        blockers.append("%s: %s" % (B_GROUP_INCOMPATIBLE, sorted(outside)))

    if not cen.get("CENSUS_COMPLETE"):
        blockers.append("%s: %s" % (B_CENSUS, cen.get("WHY_NOT_COMPLETE")))
    if cen.get("UNATTRIBUTABLE_OCCUPYING_RUNS"):
        blockers.append("%s: %d" % (B_UNATTRIBUTABLE,
                                    len(cen["UNATTRIBUTABLE_OCCUPYING_RUNS"])))

    known = set(inventory)
    executing, waiting = [], []
    for r in gate_input:
        if str(r.get("id")) == str(self_run_id):
            continue
        if r.get("name") not in known:
            continue                      # counted by the unattributable check
        st = (r.get("status") or "").lower()
        row = {"NAME": r.get("name"), "ID": str(r.get("id")), "STATUS": st}
        if st in RC.RUNNING_STATES:
            executing.append(row)
        elif st in RC.WAITING_STATES:
            waiting.append(row)
    if executing:
        blockers.append("%s: %s" % (B_COMPETING,
                                    sorted(r["NAME"] for r in executing)))

    return {
        "POST_ACQUISITION_RULE": POST_ACQUISITION_RULE,
        "OWNERSHIP_IS_PROVEN_NOT_ASSUMED": OWNERSHIP_IS_PROVEN_NOT_ASSUMED,
        "SLOT_OWNERSHIP_PROVEN": not any(
            b.startswith(p) for b in blockers
            for p in (B_SELF_NOT_FOUND, B_SELF_NOT_EXECUTING,
                      B_SELF_WORKFLOW_UNKNOWN, B_SELF_OUTSIDE_GROUP,
                      B_GROUP_INCOMPATIBLE)),
        "COMPETING_EXECUTION": tuple(sorted(r["NAME"] for r in executing)),
        # REPORTED, NOT COUNTED AS LOAD. Kept as its own field so no
        # reader can mistake a follower for a collector at the venue.
        "WAITING_FOLLOWERS_REPORTED": tuple(sorted(r["NAME"] for r in waiting)),
        "WAITING_FOLLOWERS_ARE_NOT_VENUE_LOAD": WAITING_IS_NOT_VENUE_LOAD,
        "POST_ACQUISITION_BLOCKERS": tuple(blockers),
        "POST_ACQUISITION_ISOLATION": ("ESTABLISHED" if not blockers
                                       else "NOT_ESTABLISHED"),
        "SELF_RUN_ID": None if self_run_id is None else str(self_run_id),
        "SELF_WORKFLOW": self_workflow,
        **notes,
    }


def startup_census(root, fetch, self_run_id=None, taken_at=None,
                   self_workflow=None):
    """Discovered inventory -> complete census -> both verdicts."""
    rep = VD.audit(root)
    inventory = sorted(rep["KNOWN_VENUE_TOUCHING_WORKFLOWS"])

    # A NON-EMPTY DISCOVERED INVENTORY IS REQUIRED IN THE RUNTIME PATH, not
    # merely in a fixture. `domain_idle` fails OPEN on an empty `known` list,
    # so an inventory that silently came back empty would read as a clean
    # domain rather than as a broken discovery.
    if not inventory:
        return {
            "STARTUP_CENSUS_OK": False,
            "REFUSED_BECAUSE": "EMPTY_DISCOVERED_WORKFLOW_INVENTORY",
            "INVENTORY_SIZE": 0,
            "DOMAIN_AUDIT": rep.get("DOMAIN_AUDIT"),
            "AN_EMPTY_INVENTORY_READS_AS_AN_IDLE_DOMAIN":
                RC.AN_EMPTY_INVENTORY_READS_AS_AN_IDLE_DOMAIN,
        }

    walks = {wf: walk_workflow(fetch, wf) for wf in inventory}
    taken_at = taken_at or time.time()
    cen = RC.census(walks, inventory, taken_at=taken_at)

    out = {
        "STARTUP_CENSUS_COMMAND": "startup_census.py",
        "DOMAIN_AUDIT": rep.get("DOMAIN_AUDIT"),
        "INVENTORY_SIZE": len(inventory),
        "WORKFLOW_INVENTORY": tuple(inventory),
        "RETRIEVAL_WAS_STATUS_FILTERED": False,
        "A_STATUS_FILTER_HIDES_THE_QUEUE": A_STATUS_FILTER_HIDES_THE_QUEUE,
        "WAITING_IS_NOT_VENUE_LOAD": WAITING_IS_NOT_VENUE_LOAD,
        "PER_WORKFLOW_WALKS": {
            wf: {k: w[k] for k in ("WALK_COMPLETE", "UNIQUE_IDS",
                                   "API_TOTAL_COUNT", "PAGES_WALKED",
                                   "PAGINATION_EXHAUSTED", "PROBLEMS")}
            for wf, w in walks.items()},
    }
    out.update(cen)

    if not cen["CENSUS_COMPLETE"]:
        # BLOCKING IS MONOTONE. An incomplete census cannot certify IDLE, but
        # it can still certify BLOCKED, and that verdict is kept.
        #
        # THE POST-ACQUISITION BLOCK IS EMITTED ON THIS PATH TOO. An
        # evidence file whose shape changes with the outcome invites the
        # reader to mistake an absent field for a passing one, and a
        # refusal that omits POST_ACQUISITION_ISOLATION would be exactly
        # that. It is computed here as well, and an incomplete census is
        # one of its named blockers.
        out["STARTUP_CENSUS_OK"] = False
        out["REFUSED_BECAUSE"] = "CENSUS_INCOMPLETE: " + cen["WHY_NOT_COMPLETE"]
        out["MAY_CERTIFY"] = RC.may_certify(cen, age_s=0)
        out["ADMISSION_VERDICT"] = "NOT_ESTABLISHED"
        out["ADMISSION_RULE"] = ADMISSION_RULE
        out.update(post_acquisition(cen, inventory, rep, self_run_id,
                                    self_workflow))
        out["POST_ACQUISITION_ISOLATION"] = "NOT_ESTABLISHED"
        out["STARTUP_CENSUS_OK"] = False
        return out

    # THE COMPLETE POPULATION, THROUGH THE UNCHANGED POLICY.
    gate_input = list(cen["GATE_INPUT"])
    iso = VD.isolation(gate_input, inventory, self_run_id)
    out.update(iso)
    out["MAY_CERTIFY"] = RC.may_certify(cen, age_s=0)
    out["GATE_POPULATION"] = len(gate_input)
    out["QUEUED_FOLLOWERS"] = tuple(
        sorted(str(r.get("name")) for r in gate_input
               if (r.get("status") or "").lower() in RC.WAITING_STATES
               and str(r.get("id")) != str(self_run_id)))

    # ADMISSION AND POST-ACQUISITION ARE TWO DIFFERENT QUESTIONS, kept
    # apart and both reported. See post_acquisition() for why.
    out["ADMISSION_VERDICT"] = iso["DIRECT_RESEARCH_COLLECTOR_ISOLATION"]
    out["ADMISSION_RULE"] = ADMISSION_RULE
    post = post_acquisition(cen, inventory, rep, self_run_id, self_workflow)
    out.update(post)
    out["STARTUP_CENSUS_OK"] = post["POST_ACQUISITION_ISOLATION"] == "ESTABLISHED"
    if not out["STARTUP_CENSUS_OK"]:
        out["REFUSED_BECAUSE"] = ("POST_ACQUISITION_ISOLATION = "
                                  "NOT_ESTABLISHED: "
                                  + "; ".join(post["POST_ACQUISITION_BLOCKERS"]))
    return out


def _http_fetch(repo, token):                                 # pragma: no cover
    import httpx

    def fetch(wf, page, per_page):
        url = runs_url(repo, wf + ".yml")
        r = httpx.get(url, params={"per_page": per_page, "page": page},
                      headers={"Authorization": "Bearer %s" % token,
                               "Accept": "application/vnd.github+json"},
                      timeout=30.0)
        if r.status_code != 200:
            return [], None, "HTTP_%d" % r.status_code
        body = r.json()
        if not isinstance(body, dict) or "workflow_runs" not in body:
            return [], None, "MALFORMED_RESPONSE"
        return body["workflow_runs"], body.get("total_count"), None
    return fetch


def _cli():                                                   # pragma: no cover
    import argparse
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--self-run-id", default=None)
    # OUR OWN WORKFLOW, so slot ownership can be PROVEN rather than
    # assumed. GITHUB_WORKFLOW carries the workflow's `name:`; every
    # member of this group names itself after its file stem (checked),
    # so it matches the discovered inventory.
    ap.add_argument("--self-workflow",
                    default=os.environ.get("GITHUB_WORKFLOW"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not (a.repo and token):
        raise SystemExit("STARTUP_CENSUS_REFUSED: repo or token missing")

    rep = startup_census(a.root, _http_fetch(a.repo, token),
                         self_run_id=a.self_run_id,
                         self_workflow=a.self_workflow)
    for k in ("DOMAIN_AUDIT", "INVENTORY_SIZE", "CENSUS_COMPLETE",
              "ADMISSION_VERDICT", "SLOT_OWNERSHIP_PROVEN",
              "COMPETING_EXECUTION", "WAITING_FOLLOWERS_REPORTED",
              "POST_ACQUISITION_ISOLATION", "POST_ACQUISITION_BLOCKERS",
              "COMPLETENESS_BASIS", "RUNS_RETRIEVED", "GATE_POPULATION",
              "OBSERVED_CONFLICTS", "QUEUED_FOLLOWERS",
              "KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE",
              "KNOWN_DIRECT_PMUS_COLLECTORS_PENDING",
              "DIRECT_RESEARCH_COLLECTOR_ISOLATION",
              "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION",
              "UNATTRIBUTABLE_OCCUPYING_RUNS", "STARTUP_CENSUS_OK",
              "REFUSED_BECAUSE"):
        if k in rep:
            print("%-40s = %s" % (k, rep[k]))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True,
                                          default=str))
    if not rep.get("STARTUP_CENSUS_OK"):
        raise SystemExit("STARTUP_CENSUS_REFUSED: %s"
                         % rep.get("REFUSED_BECAUSE", NOT_IDENTIFIED))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
