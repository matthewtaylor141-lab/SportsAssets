#!/usr/bin/env python3
"""OFFLINE REHEARSAL OF THE ACTUAL WORKFLOW STARTUP COMMAND.

WHY A REHEARSAL AND NOT ONLY UNIT TESTS.

`test_startup_census.py` calls `startup_census(...)` -- the function. The job
does not call a function. It runs a `run:` block. The defect this whole
correction exists to fix was exactly that gap: `run_census.py` was tested and
correct, and the command the job ran never touched it. Testing the repaired
caller as a Python function and then trusting that the YAML invokes it would
repeat the same mistake one level up.

So this rehearsal does not paraphrase the command. It READS the `run:` text
out of `.github/workflows/beta48-substantive-capture.yml`, for the step named
below, and executes THAT TEXT with bash -- same working directory, same
argument string, same exit-code semantics. If somebody edits the YAML step,
this rehearsal exercises the edit.

HOW THE VENUE IS KEPT OUT OF IT.

Nothing here contacts GitHub or the venue. The subprocess gets a PYTHONPATH
shim whose `sitecustomize` replaces `httpx.get` with a fixture server driven
by a scenario file. No socket is opened. Every decision -- discovery, walk,
census, completeness, the isolation policy -- is made by the real code.

WHAT IS PROVED, PER MANAGEMENT'S FOUR NAMED REQUIREMENTS.

  A  CLEAN            a quiet domain certifies, exit 0
  B  ACTIVE           a running conflicting collector blocks venue access
  C  INCOMPLETE       unfinished pagination blocks certification
  D  MALFORMED        a malformed response cannot read as an idle domain
  E  UNNAMED          an unnamed occupying row cannot read as an idle domain
  F  QUEUED FOLLOWER  reported by name and NOT blocking, once the slot is
                      proven held
  G  NO OWNERSHIP     our own run absent from the census: nothing may be
                      excused on the strength of merely running code
  H  SELF PENDING     a queued self is standing in the queue, not holding it

F IS THE ONE THAT IS EASY TO GET WRONG IN THE FLATTERING DIRECTION, so it
asserts all of it: the follower is named, nothing is executing beside us, the
ADMISSION verdict is still NOT_ESTABLISHED (that rule did not move), ownership
is proven, and only then does the post-acquisition verdict pass. G and H are
the guards on that relaxation -- both are jobs that would have excused a
waiter without holding anything.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORKFLOW = ROOT / ".github/workflows/beta48-substantive-capture.yml"
STEP_NAME = "Prove BETTOR collector isolation at start"

SELF_RUN = "99999999999"
SELF_WF = "beta48-substantive-capture"
REPO = "matthewtaylor141-lab/SportsAssets"

SHIM = '''
import json, os, re

SCEN = json.load(open(os.environ["REHEARSAL_SCENARIO"]))
LOG = os.environ["REHEARSAL_REQUEST_LOG"]


class _Resp(object):
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def _page_for(wf, page):
    spec = SCEN["WORKFLOWS"].get(wf, SCEN["DEFAULT"])
    return spec, spec.get("PAGES", {}).get(str(page))


def _get(url, params=None, headers=None, timeout=None):
    with open(LOG, "a") as fh:
        fh.write(json.dumps({"url": url, "params": dict(params or {})}) + "\\n")
    m = re.search(r"/actions/workflows/(?P<wf>.+?)\\.yml/runs$", url)
    assert m, "UNMOCKED_URL: %s" % url
    wf = m.group("wf")
    page = int((params or {}).get("page", 1))
    spec, body = _page_for(wf, page)
    if body is None:
        body = {"total_count": spec.get("TOTAL", 0), "workflow_runs": []}
    if body.get("__STATUS__"):
        return _Resp(body["__STATUS__"], {})
    if body.get("__MALFORMED__"):
        return _Resp(200, {"unexpected": "shape"})
    return _Resp(200, {"total_count": body.get("total_count",
                                               spec.get("TOTAL", 0)),
                       "workflow_runs": body.get("workflow_runs", [])})


import httpx
httpx.get = _get
'''


def _run(wf_name, status, run_id, **extra):
    r = {"id": run_id, "name": wf_name, "status": status,
         "conclusion": "success" if status == "completed" else None,
         "created_at": "2026-09-18T12:00:00Z",
         "run_started_at": "2026-09-18T12:00:00Z",
         "updated_at": "2026-09-18T12:00:00Z"}
    r.update(extra)
    return r


def step_command():
    """The `run:` text of the startup gate step, from the workflow file."""
    import yaml
    doc = yaml.safe_load(WORKFLOW.read_text())
    for job in doc["jobs"].values():
        for step in job.get("steps", ()):
            if step.get("name") == STEP_NAME:
                return step["run"], step.get("working-directory")
    raise SystemExit("STEP_NOT_FOUND: %s" % STEP_NAME)


def invoke(command, scenario, seg):
    """Run the workflow's own command text offline. Returns (rc, report)."""
    tmp = Path(tempfile.mkdtemp(prefix="startup_gate_"))
    (tmp / "sitecustomize.py").write_text(SHIM)
    scen_path = tmp / "scenario.json"
    scen_path.write_text(json.dumps(scenario))
    log = tmp / "requests.jsonl"
    log.write_text("")

    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(tmp) + os.pathsep + env.get("PYTHONPATH", ""),
        "REHEARSAL_SCENARIO": str(scen_path),
        "REHEARSAL_REQUEST_LOG": str(log),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_WORKFLOW": SELF_WF,
        "GH_TOKEN": "REHEARSAL_TOKEN_NOT_A_CREDENTIAL",
        "SEG": seg,
    })
    proc = subprocess.run(["bash", "-c", command], cwd=str(HERE), env=env,
                          capture_output=True, text=True)
    out = HERE / ("evidence/substantive_%s/isolation_at_start.json" % seg)
    report = json.loads(out.read_text()) if out.exists() else None
    requests = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    shutil.rmtree(tmp, ignore_errors=True)
    return proc, report, requests


# ---------------------------------------------------------------- scenarios

QUIET = {"TOTAL": 2, "PAGES": {"1": {"total_count": 2, "workflow_runs": [
    _run("x", "completed", 1), _run("x", "completed", 2)]}}}


def quiet_for(wf):
    return {"TOTAL": 2, "PAGES": {"1": {"total_count": 2, "workflow_runs": [
        _run(wf, "completed", 1), _run(wf, "completed", 2)]}}}


def owning(extra_rows=()):
    """OUR OWN RUN, EXECUTING. The post-acquisition rule excuses waiters
    only once ownership of the slot is proven, so every scenario that
    expects the relaxed verdict must actually show the job running."""
    rows = [_run(SELF_WF, "in_progress", int(SELF_RUN))] + list(extra_rows)
    return {"TOTAL": len(rows),
            "PAGES": {"1": {"total_count": len(rows), "workflow_runs": rows}}}


def owning_pending():
    """Our own run QUEUED. It holds nothing, so it may excuse nothing."""
    rows = [_run(SELF_WF, "queued", int(SELF_RUN))]
    return {"TOTAL": 1,
            "PAGES": {"1": {"total_count": 1, "workflow_runs": rows}}}


def scenario(overrides=None, own=True):
    wf = dict(overrides or {})
    if own and SELF_WF not in wf:
        wf[SELF_WF] = owning()
    return {"DEFAULT": QUIET, "WORKFLOWS": wf}


COLLECTOR = "beta48-substantive-capture"

SCENARIOS = {
    "A_CLEAN": (
        scenario(), 0,
        "a quiet domain certifies and the job may proceed"),
    "B_ACTIVE_COLLECTOR_BLOCKS": (
        scenario({"run85-phase2-capture": {"TOTAL": 1, "PAGES": {"1": {
            "total_count": 1, "workflow_runs": [
                _run("run85-phase2-capture", "in_progress", 35300000001)]}}}}),
        1, "another EXECUTING collector blocks venue access, slot or no slot"),
    "C_INCOMPLETE_PAGINATION_BLOCKS": (
        scenario({COLLECTOR: {"TOTAL": 250, "PAGES": {
            "1": {"total_count": 250,
                  "workflow_runs": [_run(COLLECTOR, "completed", 1000 + i)
                                    for i in range(100)]},
            "2": {"__STATUS__": 502}}}}), 1,
        "unfinished pagination blocks CERTIFICATION, not merely the verdict"),
    "D_MALFORMED_CANNOT_LOOK_IDLE": (
        scenario({COLLECTOR: {"TOTAL": 0,
                              "PAGES": {"1": {"__MALFORMED__": True}}}}), 1,
        "a malformed body is unreadable, and unreadable is not empty"),
    "E_UNNAMED_OCCUPYING_ROW_BLOCKS": (
        scenario({"venue-probe": {"TOTAL": 1, "PAGES": {"1": {
            "total_count": 1, "workflow_runs": [
                dict(_run("venue-probe", "in_progress", 35300000002),
                     name="")]}}}}),
        1, "an occupying row we cannot attribute cannot read as idle"),
    "F_QUEUED_FOLLOWER_REPORTED_NOT_BLOCKING": (
        scenario({"beta48-forward-capture": {"TOTAL": 1, "PAGES": {"1": {
            "total_count": 1, "workflow_runs": [
                _run("beta48-forward-capture", "queued", 35300000003)]}}}}), 0,
        "with the slot PROVEN held, a queued follower is named and reported; "
        "it cannot be displaced by a run already executing and issues no "
        "venue request while it waits"),
    "G_OWNERSHIP_NOT_PROVEN_BLOCKS": (
        scenario({}, own=False), 1,
        "our own run is absent from the census, so the slot is not proven "
        "held and no follower may be excused on the strength of it"),
    "H_SELF_PENDING_CANNOT_EXCUSE_ITS_OWN_QUEUE": (
        scenario({COLLECTOR: owning_pending(),
                  "beta48-forward-capture": {"TOTAL": 1, "PAGES": {"1": {
                      "total_count": 1, "workflow_runs": [
                          _run("beta48-forward-capture", "queued",
                               35300000004)]}}}}), 1,
        "a PENDING self is standing in the queue, not holding it"),
}


def main():
    command, wd = step_command()
    print("=" * 78)
    print("REHEARSAL OF THE ACTUAL WORKFLOW STARTUP COMMAND")
    print("=" * 78)
    print("WORKFLOW_FILE            = %s" % WORKFLOW.relative_to(ROOT))
    print("STEP                     = %s" % STEP_NAME)
    print("WORKING_DIRECTORY        = %s" % wd)
    print("COMMAND_TEXT_SOURCE      = READ_FROM_THE_WORKFLOW_FILE")
    print("TRANSPORT                = MOCKED (httpx.get); no socket opened")
    print("DECISIONS                = REAL (audit -> walk -> census -> policy)")
    print("-" * 78)
    for line in command.strip().splitlines():
        print("  | %s" % line)
    print("-" * 78)

    failures = []
    # THE JOB'S OWN RUN ID IS $SEG, and the gate now proves ownership by
    # finding THAT id executing in the census. So the rehearsal must use
    # one id for both, or every scenario would arrive as a job that
    # cannot find itself -- which is a real blocker, and would have made
    # the whole rehearsal pass for the wrong reason.
    seg = SELF_RUN
    for name, (scen, want_rc, why) in sorted(SCENARIOS.items()):
        shutil.rmtree(HERE / ("evidence/substantive_%s" % seg),
                      ignore_errors=True)
        proc, rep, reqs = invoke(command, scen, seg)
        rc_ok = (proc.returncode != 0) == (want_rc != 0)
        detail = []
        if rep:
            detail.append("inventory=%s" % rep.get("INVENTORY_SIZE"))
            detail.append("census_complete=%s" % rep.get("CENSUS_COMPLETE"))
            detail.append("status_filtered=%s"
                          % rep.get("RETRIEVAL_WAS_STATUS_FILTERED"))
            if rep.get("REFUSED_BECAUSE"):
                detail.append("refused=%s" % rep["REFUSED_BECAUSE"][:64])
        extra_ok = True
        if name == "A_CLEAN":
            extra_ok = (rep and rep.get("STARTUP_CENSUS_OK") is True
                        and rep.get("CENSUS_COMPLETE") is True
                        and rep.get("COMPLETENESS_BASIS")
                        == "VERIFIED_PAGINATION_EXHAUSTION")
        if name == "B_ACTIVE_COLLECTOR_BLOCKS":
            comp = list((rep or {}).get("COMPETING_EXECUTION") or ())
            extra_ok = "run85-phase2-capture" in comp
            detail.append("competing=%s" % comp)
        if name == "C_INCOMPLETE_PAGINATION_BLOCKS":
            extra_ok = (rep and rep.get("CENSUS_COMPLETE") is False
                        and str(rep.get("REFUSED_BECAUSE", "")
                                ).startswith("CENSUS_INCOMPLETE"))
        if name == "D_MALFORMED_CANNOT_LOOK_IDLE":
            extra_ok = rep is not None and rep.get("CENSUS_COMPLETE") is False
        if name == "E_UNNAMED_OCCUPYING_ROW_BLOCKS":
            un = list((rep or {}).get("UNATTRIBUTABLE_OCCUPYING_RUNS") or ())
            extra_ok = bool(un) and not (rep or {}).get("STARTUP_CENSUS_OK")
            detail.append("unattributable=%d" % len(un))
        if name == "F_QUEUED_FOLLOWER_REPORTED_NOT_BLOCKING":
            foll = list((rep or {}).get("WAITING_FOLLOWERS_REPORTED") or ())
            comp = list((rep or {}).get("COMPETING_EXECUTION") or ())
            # BOTH VERDICTS, side by side: admission still says a waiter
            # makes a START unsafe; post-acquisition says it cannot
            # displace a run that already holds the slot.
            extra_ok = ("beta48-forward-capture" in foll and not comp
                        and (rep or {}).get("ADMISSION_VERDICT")
                        == "NOT_ESTABLISHED"
                        and (rep or {}).get("SLOT_OWNERSHIP_PROVEN") is True
                        and (rep or {}).get("POST_ACQUISITION_ISOLATION")
                        == "ESTABLISHED"
                        and (rep or {}).get("STARTUP_CENSUS_OK") is True)
            detail.append("followers=%s competing=%s admission=%s post=%s"
                          % (foll, comp, (rep or {}).get("ADMISSION_VERDICT"),
                             (rep or {}).get("POST_ACQUISITION_ISOLATION")))
        if name == "G_OWNERSHIP_NOT_PROVEN_BLOCKS":
            extra_ok = ((rep or {}).get("SLOT_OWNERSHIP_PROVEN") is False
                        and "SELF_RUN_NOT_IN_CENSUS"
                        in ((rep or {}).get("POST_ACQUISITION_BLOCKERS") or ()))
            detail.append("blockers=%s"
                          % list((rep or {}).get("POST_ACQUISITION_BLOCKERS")
                                 or ()))
        if name == "H_SELF_PENDING_CANNOT_EXCUSE_ITS_OWN_QUEUE":
            extra_ok = ("SELF_RUN_NOT_EXECUTING"
                        in ((rep or {}).get("POST_ACQUISITION_BLOCKERS") or ()))
            detail.append("self_status=%s" % (rep or {}).get("SELF_RUN_STATUS"))
        # NO SCENARIO MAY REACH THE VENUE, AND NONE MAY STATUS-FILTER.
        filt = [r for r in reqs if "status" in r.get("params", {})]
        no_filter = not filt
        ok = rc_ok and extra_ok and no_filter
        print("%-40s rc=%-3d %s" % (name, proc.returncode,
                                    "PASS" if ok else "FAIL"))
        print("    %s" % why)
        print("    requests=%d status_filtered_requests=%d %s"
              % (len(reqs), len(filt), " ".join(detail)))
        if not ok:
            failures.append((name, proc.returncode, proc.stdout[-800:],
                             proc.stderr[-800:]))
        shutil.rmtree(HERE / ("evidence/substantive_%s" % seg),
                      ignore_errors=True)

    print("=" * 78)
    for name, rc, so, se in failures:
        print("FAILURE %s rc=%s\n%s\n%s" % (name, rc, so, se))
    print("STARTUP_GATE_REHEARSAL = %s"
          % ("PASS" if not failures else "FAIL"))
    print("THE_COMMAND_REHEARSED_IS_THE_COMMAND_IN_THE_WORKFLOW = YES")
    print("VENUE_CONTACTED = NO")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
