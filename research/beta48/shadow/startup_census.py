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

WHAT IT DOES NOT DO.

It does not change the policy. `venue_domain.isolation` decides, exactly as
before: a RUNNING direct collector blocks; a WAITING one is the domain working
as designed and is reported, not counted as venue load. Nothing here relaxes a
verdict to obtain a pass -- the only way this command exits zero is a complete
census over a non-empty inventory with isolation ESTABLISHED.
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


def startup_census(root, fetch, self_run_id=None, taken_at=None):
    """Discovered inventory -> complete census -> the unchanged policy."""
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
        out["STARTUP_CENSUS_OK"] = False
        out["REFUSED_BECAUSE"] = "CENSUS_INCOMPLETE: " + cen["WHY_NOT_COMPLETE"]
        out["MAY_CERTIFY"] = RC.may_certify(cen, age_s=0)
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
    out["STARTUP_CENSUS_OK"] = (
        iso["DIRECT_RESEARCH_COLLECTOR_ISOLATION"] == "ESTABLISHED")
    if not out["STARTUP_CENSUS_OK"]:
        # NAME THE ACTUAL REASON. A start blocked by a WAITING member is a
        # displacement risk, not venue load, and saying "active: unknown"
        # when the active list is simply empty would misreport which of the
        # two it was.
        active = list(iso["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"])
        pending = list(iso["KNOWN_DIRECT_PMUS_COLLECTORS_PENDING"])
        parts = []
        if active:
            parts.append("RUNNING_DIRECT_COLLECTORS=%s" % active)
        if pending:
            parts.append("WAITING_DIRECT_COLLECTORS=%s (a START would risk "
                         "displacing them; they are not venue load)" % pending)
        if not parts:
            parts.append("NO_NAMED_CONFLICT_IDENTIFIED: see "
                         "UNATTRIBUTABLE_OCCUPYING_RUNS and OBSERVED_CONFLICTS")
        out["REFUSED_BECAUSE"] = (
            "DIRECT_RESEARCH_COLLECTOR_ISOLATION = NOT_ESTABLISHED: "
            + "; ".join(parts))
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
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not (a.repo and token):
        raise SystemExit("STARTUP_CENSUS_REFUSED: repo or token missing")

    rep = startup_census(a.root, _http_fetch(a.repo, token),
                         self_run_id=a.self_run_id)
    for k in ("DOMAIN_AUDIT", "INVENTORY_SIZE", "CENSUS_COMPLETE",
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
