#!/usr/bin/env python3
"""THE GLOBAL VENUE-ACCESS DOMAIN, and an audit that stays true.

WHY. The rate ladder reported "ONE GLOBAL PACER, NO CONCURRENCY". That was true
inside the pilot process and false at the account level: `run85-phase2-capture`
had its own concurrency group, contacts the venue, and was running throughout
the ladder. The shared group covered four workflows; the repository has
eighteen that can reach the venue, spread across fourteen groups.

A list of five, written by hand, is how that gap survived. So this module does
not hold a hand-written list -- it DISCOVERS the venue-touching workflows from
the repository and fails when one appears outside the domain. A new collector
added next month breaks the audit test rather than silently contaminating the
next measurement.

WHAT IT CAN AND CANNOT ESTABLISH.

    BETTOR_COLLECTOR_ISOLATION = ESTABLISHED
        every KNOWN BETTOR venue-touching workflow is mutually excluded, and
        none was running when we started. Provable.

    NO_OTHER_CLIENT_ANYWHERE_IS_USING_THE_VENUE
        REFUSED_NOT_KNOWABLE. We do not see the venue's global traffic and are
        not going to pretend otherwise.

A KNOWN LIMIT OF GITHUB CONCURRENCY, RECORDED RATHER THAN DISCOVERED LATER.
With `cancel-in-progress: false` an IN-PROGRESS run is safe, but only ONE run
may be PENDING per group: when a newer run is queued, the older pending one is
CANCELLED. So a confirmation left waiting in the queue can be displaced by a
cron firing. The mitigation is to dispatch only into an idle domain so the run
starts immediately, and to verify it actually started rather than assuming.

This module contacts nothing. It reads files and compares strings.
"""
from __future__ import annotations

import re
from pathlib import Path

NOT_IDENTIFIED = "NOT_IDENTIFIED"
THIS_MODULE_CONTACTS_NOTHING = True

GLOBAL_DOMAIN = "pmus-public-read-global"
QUEUE_NOT_CANCEL = "cancel-in-progress: false"
WHY_QUEUE = ("an evidence run must not be killed mid-collection by a scheduled "
             "job; the loser waits its turn")

# THE PMUS VENUE, named once and narrowly.
#
# `polymarket.com` hosts -- data-api, lb-api, user-pnl-api -- are a DIFFERENT
# service behind a different limiter. Sweeping them in would drag ops tooling
# and the whale census into the evidence domain and serialise them behind
# research runs for no gain. The limiter this programme is measuring lives at
# gateway.polymarket.us.
VENUE_HOST_PATTERNS = (r"gateway\.polymarket\.us", r"GATEWAY_BASE")
NOT_THE_SAME_LIMITER = ("data-api.polymarket.com", "lb-api.polymarket.com",
                        "user-pnl-api.polymarket.com", "api.render.com")

# Our own API reaches PMUS on our behalf, so a workflow that drives it puts
# load on the venue by a path this domain does not control. Reported as a named
# residual rather than pulled into the domain -- serialising production ops
# behind a research run would be a worse failure than the one being fixed.
INDIRECT_HOST_PATTERNS = (r"sportsassets-api\.onrender\.com",)

PENDING_RUN_MAY_BE_SUPERSEDED = "YES"
WHY_PENDING_IS_NOT_SAFE = (
    "GitHub keeps at most one PENDING run per concurrency group; a newer "
    "queued run cancels the older pending one, so a waiting evidence run can "
    "be displaced by a cron firing")
MITIGATION = "DISPATCH_ONLY_INTO_AN_IDLE_DOMAIN_AND_VERIFY_THE_RUN_STARTED"

# ---------------------------------------------------------------------------
# WHAT THE IDLE WINDOW IS FOR: A CLEAN START, NOT A CONTAINER
# ---------------------------------------------------------------------------
#
# The confirmation does NOT have to finish before the next cron boundary. It
# has to START clean. Once it is RUNNING, the shared domain with
# cancel-in-progress: false makes later compliant collectors QUEUE behind it
# rather than overlap it -- which is the whole point of the domain.
#
# So the two checks answer different questions and must not share a rule:
#
#   AT START   any known direct collector that is RUNNING **or PENDING** blocks
#              us. Pending blocks because we would then be the second pending
#              run, and GitHub displaces one of those.
#   DURING     only a collector that is actually RUNNING is a confound. A
#              pending collector waiting behind us contacts nothing, and
#              treating it as contamination would fail a clean experiment for
#              behaving exactly as designed.
GATE_SEMANTICS_AT_START = "RUNNING_OR_PENDING_BLOCKS"
GATE_SEMANTICS_DURING_RUN = "ONLY_RUNNING_IS_A_CONFOUND"
WHY_PENDING_IS_NOT_A_CONFOUND = (
    "a queued collector held behind us by the domain makes no venue request; "
    "it is the domain working, not a contamination")
PENDING_SUPERSESSION_IS = "AN_OPS_ISSUE_FOR_THAT_JOB_NOT_A_CONFIRMATION_FAULT"

# During the transition, runs created BEFORE the group change keep their old
# group, so the domain does not hold them. The pre-start audit therefore scans
# known venue-touching workflows BY NAME against live runs -- never group
# membership, which would have missed run85-phase2-capture entirely.
AUDIT_SCANS = "KNOWN_WORKFLOW_NAMES_AGAINST_LIVE_RUNS_NOT_GROUP_MEMBERSHIP"

ABSOLUTE_VENUE_ISOLATION = "REFUSED_NOT_KNOWABLE"
WHY_ABSOLUTE_IS_REFUSED = (
    "we observe our own workflows, not the venue's global traffic")

# ---------------------------------------------------------------------------
# TWO CLASSES OF BETTOR TRAFFIC, AND ONLY ONE OF THEM IS CONTROLLED
# ---------------------------------------------------------------------------
#
#   A  DIRECT GITHUB PMUS COLLECTORS -- the 18 discovered workflows. Mutually
#      excluded by pmus-public-read-global, and observable run by run.
#   B  INDIRECT PMUS TRAFFIC -- reaches the venue through our own API and the
#      production path. Not controlled by a GitHub concurrency group, and its
#      load contribution is not instrumented.
#
# So the isolation claim is scoped to class A and says so in its own name. A
# single "BETTOR_COLLECTOR_ISOLATION = ESTABLISHED" would quietly assert class
# B as well, which nothing here establishes.
COMBINED_ISOLATION_LABEL = "REFUSED_SCOPE_NOT_ESTABLISHED_FOR_INDIRECT_TRAFFIC"
INDIRECT_LOAD_ISOLATION = "NOT_ESTABLISHED"
INDIRECT_CONFOUND_MAGNITUDE = NOT_IDENTIFIED
WHY_INDIRECT_IS_UNCONTROLLED = (
    "it reaches PMUS through our own API, which no GitHub concurrency group "
    "gates, and no instrumentation counts those requests")
ABSENCE_IS_NOT_ZERO = (
    "not observing indirect traffic is not evidence that there was none; "
    "INDIRECT_LOAD stays NOT_IDENTIFIED until something actually counts it")

# GitHub reports a waiting run under several names; all of them occupy the
# domain, and "idle" means none of them is present.
ACTIVE_STATES = ("in_progress",)
WAITING_STATES = ("queued", "pending", "requested", "waiting")


# The scan walks every module in research/ and every workflow; the suite runs
# it many times and the workflow runs the suite before venue contact, so the
# result is memoised per root. Nothing here changes during a process.
_CACHE = {}


def _py_files(root):
    return sorted(Path(root, "research").rglob("*.py"))


def venue_capable_modules(root):
    """Modules that can reach the venue: they name the host, or import one
    that does. Iterated to a fixed point, so a two-hop import is caught."""
    root = Path(root)
    key = ("modules", str(root))
    if key in _CACHE:
        return _CACHE[key]
    src = {}
    for p in _py_files(root):
        try:
            src[p.stem] = p.read_text(errors="ignore")
        except OSError:                                        # noqa: PERF203
            continue
    direct = {n for n, t in src.items()
              if any(re.search(pat, t) for pat in VENUE_HOST_PATTERNS)}
    capable = set(direct)
    changed = True
    while changed:
        changed = False
        for name, text in src.items():
            if name in capable:
                continue
            for dep in capable:
                if re.search(r"\b(import|from)\s+%s\b" % re.escape(dep), text):
                    capable.add(name)
                    changed = True
                    break
    _CACHE[key] = (capable, direct)
    return capable, direct


def workflow_groups(root):
    """Every workflow's concurrency group, as written."""
    out = {}
    for w in sorted(Path(root, ".github/workflows").glob("*.yml")):
        text = w.read_text(errors="ignore")
        m = re.search(r"^concurrency:\s*\n(?:\s+.*\n)*?\s+group:\s*(.+)$",
                      text, re.M)
        out[w.stem] = (m.group(1).strip() if m else None)
    return out


def venue_touching_workflows(root):
    """Workflows that can reach the venue -- by naming the host directly, or
    by invoking a venue-capable module. Discovered, never assumed."""
    root = Path(root)
    capable, _ = venue_capable_modules(root)
    found = {}
    for w in sorted(Path(root, ".github/workflows").glob("*.yml")):
        text = w.read_text(errors="ignore")
        why = []
        if any(re.search(pat, text) for pat in VENUE_HOST_PATTERNS):
            why.append("NAMES_THE_VENUE_HOST")
        # An INVOCATION, not a word. `collect` appears in ops prose; only
        # `collect.py` or `-m collect` actually runs venue-capable code. The
        # loose form swept in render-ops and the whale census, neither of
        # which touches the PMUS gateway at all.
        mods = sorted(m for m in capable
                      if re.search(r"(\b%s\.py\b|-m\s+%s\b)"
                                   % (re.escape(m), re.escape(m)), text))
        if mods:
            why.append("INVOKES_VENUE_CAPABLE_MODULE:" + ",".join(mods[:4]))
        if why:
            found[w.stem] = why
    return found


def indirect_venue_workflows(root):
    """Workflows that reach PMUS through our own API rather than directly.

    Named, not domained: they are real load on the venue, and they are also
    production ops. Putting them in the evidence domain would serialise the
    desk behind a research run.
    """
    out = {}
    for w in sorted(Path(root, ".github/workflows").glob("*.yml")):
        text = w.read_text(errors="ignore")
        if any(re.search(p, text) for p in INDIRECT_HOST_PATTERNS) and not any(
                re.search(p, text) for p in VENUE_HOST_PATTERNS):
            out[w.stem] = "REACHES_PMUS_VIA_OUR_OWN_API"
    return out


def audit(root="."):
    """Is every venue-touching workflow inside the one global domain?"""
    root = Path(root)
    key = ("audit", str(root))
    if key in _CACHE:
        return _CACHE[key]
    touching = venue_touching_workflows(root)
    indirect = indirect_venue_workflows(root)
    groups = workflow_groups(root)
    outside = {n: (groups.get(n) or "NONE") for n in touching
               if groups.get(n) != GLOBAL_DOMAIN}
    ok = not outside
    rep = {
        "GLOBAL_VENUE_CONCURRENCY_DOMAIN": GLOBAL_DOMAIN,
        "KNOWN_VENUE_TOUCHING_WORKFLOWS": sorted(touching),
        "VENUE_TOUCHING_COUNT": len(touching),
        "WHY_EACH_IS_VENUE_TOUCHING": touching,
        "WORKFLOWS_OUTSIDE_THE_DOMAIN": outside,
        "DOMAIN_AUDIT": "PASS" if ok else "FAIL",
        "DISCOVERED_NOT_ASSUMED": True,
        "INDIRECT_VENUE_WORKFLOWS": sorted(indirect),
        "INDIRECT_COLLECTOR_ISOLATION": "NOT_ESTABLISHED",
        "WHY_INDIRECT_IS_NOT_DOMAINED": (
            "they reach PMUS through our own API and are production ops; "
            "serialising the desk behind a research run would be a worse "
            "failure than the one being fixed"),
        "HOSTS_DELIBERATELY_EXCLUDED": list(NOT_THE_SAME_LIMITER),
        "ABSOLUTE_VENUE_ISOLATION": ABSOLUTE_VENUE_ISOLATION,
        "WHY_ABSOLUTE_IS_REFUSED": WHY_ABSOLUTE_IS_REFUSED,
        "PENDING_RUN_MAY_BE_SUPERSEDED": PENDING_RUN_MAY_BE_SUPERSEDED,
        "WHY_PENDING_IS_NOT_SAFE": WHY_PENDING_IS_NOT_SAFE,
        "MITIGATION": MITIGATION,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }
    _CACHE[key] = rep
    return rep


def isolation(active_runs, known, self_run_id=None):
    """DIRECT_RESEARCH_COLLECTOR_ISOLATION, scoped to class A and named so.

    ACTIVE and PENDING conflicts are counted SEPARATELY. "Idle" cannot mean
    in_progress == 0: GitHub allows a run to sit pending in the group, and a
    newer queued run displaces an older pending one, so a domain with a waiting
    member is not a domain we may start an experiment in.
    """
    known = set(known or ())
    active, waiting = [], []
    for r in active_runs or ():
        rid = str(r.get("id", ""))
        if self_run_id is not None and rid == str(self_run_id):
            continue
        if r.get("name") not in known:
            continue
        st = r.get("status")
        row = {"NAME": r.get("name"), "ID": rid, "STATUS": st}
        if st in ACTIVE_STATES:
            active.append(row)
        elif st in WAITING_STATES:
            waiting.append(row)
    clear = not active and not waiting
    return {
        "ACTIVE_DIRECT_CONFLICTS": len(active),
        "PENDING_DIRECT_CONFLICTS": len(waiting),
        "KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE": [r["NAME"] for r in active],
        "KNOWN_DIRECT_PMUS_COLLECTORS_PENDING": [r["NAME"] for r in waiting],
        "AUDIT_SCANS": AUDIT_SCANS,
        "ACTIVE_CONFLICTING_WORKFLOWS": len(active) + len(waiting),
        "CONFLICTS": active + waiting,
        "DIRECT_RESEARCH_COLLECTOR_ISOLATION": ("ESTABLISHED" if clear
                                                else "NOT_ESTABLISHED"),
        "NO_OTHER_KNOWN_BETTOR_GITHUB_COLLECTOR_RUNNING": ("YES" if clear
                                                           else "NO"),

        # class B, uncontrolled and unmeasured -- carried beside every class A
        # verdict so the two are never read as one.
        "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION": INDIRECT_LOAD_ISOLATION,
        "INDIRECT_CONFOUND_MAGNITUDE": INDIRECT_CONFOUND_MAGNITUDE,
        "WHY_INDIRECT_IS_UNCONTROLLED": WHY_INDIRECT_IS_UNCONTROLLED,
        "ABSENCE_IS_NOT_ZERO": ABSENCE_IS_NOT_ZERO,
        "BETTOR_COLLECTOR_ISOLATION": COMBINED_ISOLATION_LABEL,
        "ALL_BETTOR_PMUS_TRAFFIC_ISOLATED": "NO",

        "NO_OTHER_CLIENT_ANYWHERE_IS_USING_THE_VENUE": ABSOLUTE_VENUE_ISOLATION,
        "WHY_ABSOLUTE_IS_REFUSED": WHY_ABSOLUTE_IS_REFUSED,
        "KNOWN_VENUE_TOUCHING_WORKFLOWS": sorted(known),
    }


def domain_idle(active_runs, known, self_run_id=None):
    """May an experiment be dispatched? Both counts must be zero."""
    i = isolation(active_runs, known, self_run_id)
    idle = (i["ACTIVE_DIRECT_CONFLICTS"] == 0
            and i["PENDING_DIRECT_CONFLICTS"] == 0)
    i["DOMAIN_IDLE"] = "YES" if idle else "NO"
    i["WHY_NOT_IDLE"] = (None if idle else
                         "a member of the domain is %s; dispatching now would "
                         "leave the experiment pending, where a newer queued "
                         "run can displace it"
                         % ("running" if i["ACTIVE_DIRECT_CONFLICTS"]
                            else "waiting"))
    return i


def during_run_conflict(active_runs, known, self_run_id=None):
    """Did a known direct collector RUN while we were reading?

    Only RUNNING counts. A collector sitting pending behind us in the domain
    made no request, and failing the confirmation for that would punish the
    domain for working.
    """
    i = isolation(active_runs, known, self_run_id)
    started = i["ACTIVE_DIRECT_CONFLICTS"] > 0
    return {
        "DIRECT_CONFLICT_STARTED_DURING_RUN": "YES" if started else "NO",
        "DIRECT_CONFLICTS_RUNNING_DURING_RUN":
            i["KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE"],
        "PENDING_COLLECTORS_DURING_RUN":
            i["KNOWN_DIRECT_PMUS_COLLECTORS_PENDING"],
        "PENDING_IS_NOT_A_CONFOUND": True,
        "WHY_PENDING_IS_NOT_A_CONFOUND": WHY_PENDING_IS_NOT_A_CONFOUND,
        "PENDING_SUPERSESSION_IS": PENDING_SUPERSESSION_IS,
        "GATE_SEMANTICS_DURING_RUN": GATE_SEMANTICS_DURING_RUN,
        "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION": INDIRECT_LOAD_ISOLATION,
        "INDIRECT_CONFOUND_MAGNITUDE": INDIRECT_CONFOUND_MAGNITUDE,
    }


# ---------------------------------------------------------------------------
# A SNAPSHOT AT THE END IS NOT AN AUDIT OF THE INTERVAL
# ---------------------------------------------------------------------------
#
#   confirmation starts    21:35
#   other collector starts 21:40
#   other collector ends   21:47
#   confirmation ends      21:55
#
# At 21:55 nothing is running, so an end-of-run snapshot reports a clean run --
# and the experiment was contaminated for seven minutes. The verdict therefore
# comes from RUN-HISTORY OVERLAP against the evidence window. The snapshot
# stays for operational visibility and is not the proof.
EVIDENCE_VERDICT_FROM = "RUN_HISTORY_OVERLAP_NOT_END_OF_RUN_SNAPSHOT"
WHY_SNAPSHOT_IS_NOT_PROOF = (
    "a collector that started and finished inside the window is invisible at "
    "the end, and it was venue load the whole time it ran")
QUEUED_WITHOUT_EXECUTION_IS_NOT_LOAD = (
    "a run that never reached a running state issued no request")


def _iso(ts):
    from datetime import datetime
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:                                          # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# A RUNNING WORKFLOW IS NOT PROVEN VENUE CONTACT
# ---------------------------------------------------------------------------
#
# A job can be RUNNING while it checks out, installs, runs tests, proves
# provenance, sleeps, uploads artifacts or post-processes -- issuing no PMUS
# request at all. So a RUNNING-interval overlap is a POSSIBILITY, not an
# observation, and the two get different names:
#
#   LEVEL A  the other run sealed FIRST_VENUE_GET_TIME / LAST_VENUE_GET_TIME.
#            Overlap of that interval is CONFIRMED_DIRECT_REQUEST_OVERLAP.
#   LEVEL B  no request timestamps, only a job interval.
#            POSSIBLE_DIRECT_WORKFLOW_OVERLAP, and CONFIRMED stays
#            NOT_IDENTIFIED. We do not invent the missing timestamps.
#
# Level B still fails the confirmation -- conservatively -- but it is never
# reported as observed PMUS traffic.
CONSERVATIVE_PROXY = "CONSERVATIVE_WORKFLOW_INTERVAL_PROXY"
WHY_RUNNING_IS_NOT_CONTACT = (
    "a job is RUNNING through checkout, tests, provenance and upload, none of "
    "which touches the venue")
DO_NOT_SYNTHESIZE_GET_TIMESTAMPS = True

# ---------------------------------------------------------------------------
# A JOB TIMEOUT BOUNDS EXECUTION, NOT QUEUE AGE
# ---------------------------------------------------------------------------
#
# The previous revision used the longest job timeout (340 min) as a created-at
# lookback, reasoning that a run cannot outlive its own timeout. True, and it
# proves nothing, because a run's life is:
#
#     CREATED -> QUEUED (for an unbounded time) -> STARTS -> runs <= timeout
#
# The timeout bounds only the last leg. A run CREATED at 03:00 that waited in
# the queue until 18:05 and ran to 18:10 sits inside a 18:00-18:20 window while
# its created_at is fifteen hours older than any finite cutoff. So:
#
#     RUN_CREATED_AT < WINDOW_START - MAX_TIMEOUT
#         does NOT imply
#     RUN_EXECUTION_CANNOT_OVERLAP_THE_WINDOW
#
# Two consequences, and both are implemented below rather than noted:
#
#   OVERLAP is computed from the ACTUAL JOB EXECUTION INTERVAL
#   (run_started_at .. completion), never from creation time.
#
#   NEGATIVE COVERAGE has no finite created-at shortcut. A workflow's history
#   covers the window only when it is EXHAUSTED. Anything less is
#   NOT_ESTABLISHED -- not "NO", and certainly not YES.
TIMEOUT_BOUNDS = "EXECUTION_NOT_QUEUE_AGE"
WHY_CREATED_AT_LOOKBACK_IS_REFUSED = (
    "a run may sit QUEUED for an unbounded time before it starts, so a run "
    "created long before the window can begin executing inside it; a finite "
    "created-at cutoff is not a proof about execution")
OVERLAP_INTERVAL = "ACTUAL_JOB_EXECUTION_INTERVAL"
NOT_THE_OVERLAP_INTERVAL = "RUN_CREATION_INTERVAL"
COVERAGE_ROUTE_A = "API_HISTORY_EXHAUSTED"
COVERAGE_ROUTE_B = "PROOF_NO_OLDER_RUN_CAN_EXECUTE_INTO_THE_WINDOW"
COVERAGE_ROUTE_B_STATUS = "NOT_ESTABLISHED_QUEUE_DELAY_IS_UNBOUNDED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

# THE AUDIT IS TWO-STAGE, so evidence quality costs no bandwidth.
#   STAGE 1  run/job metadata only -> which runs' EXECUTION intervals overlap.
#   STAGE 2  for those candidates ALONE, look for sealed request times.
# Downloading every artifact of every old run would buy nothing: a run whose
# job never overlapped cannot have made a request inside the window.
AUDIT_STAGES = ("STAGE_1_JOB_EXECUTION_INTERVALS",
                "STAGE_2_REQUEST_TIMES_FOR_CANDIDATES_ONLY")


def max_job_timeout_s(root):
    """The longest job timeout among known venue-touching workflows.

    THIS IS NOT A COVERAGE DEVICE. It bounds how long a job may RUN once it has
    started, which is what our own dispatch check uses to decide whether a job
    can outlast its evidence floor. It says nothing about how long a run may
    have WAITED first, so it may not be turned into a created-at cutoff --
    see WHY_CREATED_AT_LOOKBACK_IS_REFUSED.
    """
    import re as _re
    best = 0
    for n in venue_touching_workflows(root):
        txt = Path(root, ".github/workflows", "%s.yml" % n).read_text(
            errors="ignore")
        for m in _re.finditer(r"timeout-minutes:\s*(\d+)", txt):
            best = max(best, int(m.group(1)))
    return (best or 360) * 60


MAX_JOB_TIMEOUT_IS_NOT_A_COVERAGE_PROOF = True


def overlap_audit(window_start, window_end, runs, known, self_run_id=None,
                  venue_windows=None, more_pages=None, pages_fetched=None):
    """Which known direct collectors EXECUTED during the evidence window, and
    how well we know what they did while they ran.

    STAGE 1 uses each run's ACTUAL JOB EXECUTION INTERVAL -- not its creation
    time, which a queue of unbounded length separates from execution. STAGE 2
    consults `venue_windows` (run id -> (first_get, last_get)) for the stage-1
    candidates ALONE; those give LEVEL A. A candidate with no sealed request
    times stays LEVEL B and is labelled a conservative proxy.

    `more_pages` maps workflow name (or "*") -> whether the API had more runs to
    give. Negative coverage requires exhaustion; there is no created-at
    shortcut, because queue delay is unbounded.
    """
    venue_windows = venue_windows or {}
    more_pages = more_pages or {}
    pages_fetched = pages_fetched or {}
    ws, we = _iso(window_start), _iso(window_end)
    known = sorted(set(known or ()))
    if ws is None or we is None:
        return {"DIRECT_CONFLICT_STARTED_DURING_RUN": NOT_IDENTIFIED,
                "WHY_NOT_IDENTIFIED": "the evidence window is not established",
                "CONFIRMED_DIRECT_REQUEST_OVERLAP": NOT_IDENTIFIED,
                "POSSIBLE_DIRECT_WORKFLOW_OVERLAP": NOT_IDENTIFIED,
                "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN":
                    NOT_IDENTIFIED,
                "DIRECT_OVERLAP_COUNT": NOT_IDENTIFIED,
                "DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW": []}

    # ---------------- STAGE 1: job execution intervals ----------------
    candidates, considered = [], 0
    for r in runs or ():
        rid = str(r.get("id", ""))
        if self_run_id is not None and rid == str(self_run_id):
            continue
        if r.get("name") not in known:
            continue
        considered += 1
        status = r.get("status")
        created = _iso(r.get("created_at"))
        # NEVER EXECUTED -> never venue load. A run that sat in the queue and
        # was cancelled issued no request.
        if status in WAITING_STATES and r.get("conclusion") in (
                None, "cancelled", "skipped"):
            continue
        job_start = _iso(r.get("run_started_at")) or _iso(r.get("started_at"))
        src = "JOB_EXECUTION_INTERVAL"
        if job_start is None:
            # No execution start recorded. Falling back to creation time makes
            # the interval WIDER, never narrower, so it can only over-report an
            # overlap -- the safe direction. It is labelled, not hidden.
            job_start = created
            src = "RUN_CREATION_TIME_AS_START_PROXY_WIDER_NOT_NARROWER"
        if job_start is None:
            continue
        if status == "completed":
            job_end = _iso(r.get("updated_at")) or we
        else:
            job_end = we                       # still running: open-ended
        if job_start <= we and job_end >= ws:
            candidates.append((r, rid, job_start, job_end, src, status))

    # ---------------- STAGE 2: request times, candidates only ----------------
    confirmed, possible, cleared = [], [], []
    for r, rid, job_start, job_end, src, status in candidates:
        vw = venue_windows.get(rid) or venue_windows.get(r.get("id"))
        a0 = a1 = None
        if vw:
            a0, a1 = _iso(vw[0]), _iso(vw[1])
        if a0 is not None and a1 is not None:
            row = {"NAME": r.get("name"), "ID": rid,
                   "EVIDENCE_LEVEL": "A_SEALED_VENUE_REQUEST_TIMES",
                   "REQUEST_TIME_EVIDENCE": "SEALED",
                   "JOB_EXECUTION_INTERVAL": [job_start.isoformat(),
                                              job_end.isoformat()],
                   "OTHER_FIRST_VENUE_GET_TIME": a0.isoformat(),
                   "OTHER_LAST_VENUE_GET_TIME": a1.isoformat(),
                   "STATUS": status}
            # Level A is evidence in BOTH directions: sealed times outside our
            # window CLEAR a job whose interval overlapped it.
            (confirmed if (a0 <= we and a1 >= ws) else cleared).append(row)
            continue
        possible.append({
            "NAME": r.get("name"), "ID": rid,
            "EVIDENCE_LEVEL": "B_" + CONSERVATIVE_PROXY,
            "REQUEST_TIME_EVIDENCE": "ABSENT",
            "JOB_EXECUTION_INTERVAL": [
                job_start.isoformat(),
                job_end.isoformat() if status == "completed" else
                "STILL_RUNNING"],
            "JOB_INTERVAL_SOURCE": src,
            "VENUE_REQUEST_TIMES": NOT_IDENTIFIED,
            "STATUS": status})

    # ---------------- COVERAGE, PER WORKFLOW ----------------
    # Exhaustion is the only route this can actually prove. Route B would need
    # a bound on queue delay, and there is none, so it is named and refused
    # rather than approximated by a created-at cutoff that would read as proof.
    cov_rows, coverage_complete = [], True
    for name in known:
        rows = [r for r in (runs or ()) if r.get("name") == name]
        created = sorted(c for c in (_iso(r.get("created_at")) for r in rows)
                         if c is not None)
        starts = sorted(s for s in (_iso(r.get("run_started_at")) for r in rows)
                        if s is not None)
        mp = more_pages.get(name, more_pages.get("*", None))
        pf = pages_fetched.get(name, pages_fetched.get("*", NOT_IDENTIFIED))
        covers = "YES" if mp is False else NOT_ESTABLISHED
        if covers != "YES":
            coverage_complete = False
        cov_rows.append({
            "WORKFLOW_NAME": name,
            "HISTORY_PAGES_FETCHED": pf,
            "RUNS_FETCHED": len(rows),
            "EARLIEST_RUN_TIME_FETCHED": (created[0].isoformat() if created
                                          else NOT_IDENTIFIED),
            "LATEST_RUN_TIME_FETCHED": (created[-1].isoformat() if created
                                        else NOT_IDENTIFIED),
            "EARLIEST_JOB_START_FETCHED": (starts[0].isoformat() if starts
                                           else NOT_IDENTIFIED),
            "MORE_PAGES_AVAILABLE": (NOT_IDENTIFIED if mp is None
                                     else ("YES" if mp else "NO")),
            "HISTORY_EXHAUSTED": ("YES" if mp is False else
                                  (NOT_IDENTIFIED if mp is None else "NO")),
            "COVERS_EVIDENCE_WINDOW": covers,
            "COVERAGE_ROUTE": (COVERAGE_ROUTE_A if covers == "YES"
                               else COVERAGE_ROUTE_B_STATUS),
        })

    # PRECEDENCE. A DETECTED overlap -- confirmed OR possible -- is a fact and
    # outranks coverage: thin history cannot turn something we saw into
    # something unknown. Coverage gates only the NEGATIVE.
    if confirmed:
        verdict, iso_verdict = "YES", "NO_CONFIRMED"
    elif possible:
        verdict, iso_verdict = "POSSIBLE", "NOT_ESTABLISHED_POSSIBLE_OVERLAP"
    elif coverage_complete:
        verdict, iso_verdict = "NO", "YES"
    else:
        verdict, iso_verdict = NOT_IDENTIFIED, NOT_IDENTIFIED

    why = None
    if verdict == NOT_IDENTIFIED:
        why = ("the run history of at least one known direct collector is not "
               "exhausted, and an older-CREATED run may have waited in the "
               "queue and then executed inside the window, so it cannot be "
               "excluded")
    elif verdict == "POSSIBLE":
        why = ("the other run's job EXECUTION interval overlaps but it sealed "
               "no venue request times, so contact is possible and not "
               "observed")

    return {
        "EVIDENCE_WINDOW": [ws.isoformat(), we.isoformat()],
        "DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW": confirmed + possible,
        "DIRECT_OVERLAP_COUNT": len(confirmed) + len(possible),
        "CONFIRMED_DIRECT_REQUEST_OVERLAP": (
            "YES" if confirmed else ("NO" if coverage_complete and not possible
                                     else NOT_IDENTIFIED)),
        "CONFIRMED_DIRECT_REQUEST_OVERLAP_COUNT": len(confirmed),
        "POSSIBLE_DIRECT_WORKFLOW_OVERLAP": "YES" if possible else "NO",
        "POSSIBLE_DIRECT_WORKFLOW_OVERLAP_COUNT": len(possible),
        "DIRECT_CONFLICT_STARTED_DURING_RUN": verdict,
        "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN": iso_verdict,

        # the two stages, and what each one cost
        "AUDIT_STAGES": list(AUDIT_STAGES),
        "STAGE_1_JOB_INTERVAL_CANDIDATES": len(candidates),
        "STAGE_2_CANDIDATES_WITH_SEALED_REQUEST_TIMES": len(confirmed) + len(
            cleared),
        "STAGE_2_CANDIDATES_CLEARED_BY_REQUEST_TIMES": cleared,
        "OVERLAP_INTERVAL": OVERLAP_INTERVAL,
        "NOT_THE_OVERLAP_INTERVAL": NOT_THE_OVERLAP_INTERVAL,

        "RUNNING_IS_NOT_PROVEN_VENUE_CONTACT": WHY_RUNNING_IS_NOT_CONTACT,
        "LEVEL_B_LABEL": CONSERVATIVE_PROXY,
        "DO_NOT_SYNTHESIZE_GET_TIMESTAMPS": DO_NOT_SYNTHESIZE_GET_TIMESTAMPS,

        "DIRECT_RUN_HISTORY_COVERAGE": cov_rows,
        "DIRECT_RUN_HISTORY_COVERAGE_COMPLETE": (
            "YES" if coverage_complete else "NO"),
        "COVERAGE_REQUIRES": COVERAGE_ROUTE_A,
        "COVERAGE_ROUTE_B": COVERAGE_ROUTE_B,
        "COVERAGE_ROUTE_B_STATUS": COVERAGE_ROUTE_B_STATUS,
        "TIMEOUT_BOUNDS": TIMEOUT_BOUNDS,
        "WHY_CREATED_AT_LOOKBACK_IS_REFUSED": WHY_CREATED_AT_LOOKBACK_IS_REFUSED,
        "RUN_HISTORY_COVERS_THE_WINDOW": "YES" if coverage_complete else "NO",

        "KNOWN_DIRECT_RUNS_CONSIDERED": considered,
        "WHY_NOT_IDENTIFIED": why,
        "COVERAGE_GATES_ONLY_THE_NEGATIVE": True,
        "EVIDENCE_VERDICT_FROM": EVIDENCE_VERDICT_FROM,
        "WHY_SNAPSHOT_IS_NOT_PROOF": WHY_SNAPSHOT_IS_NOT_PROOF,
        "QUEUED_WITHOUT_EXECUTION_IS_NOT_LOAD":
            QUEUED_WITHOUT_EXECUTION_IS_NOT_LOAD,
        "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION": INDIRECT_LOAD_ISOLATION,
    }


def render(a):
    L = ["%-38s = %s" % ("GLOBAL_VENUE_CONCURRENCY_DOMAIN",
                         a["GLOBAL_VENUE_CONCURRENCY_DOMAIN"]),
         "%-38s = %d" % ("VENUE_TOUCHING_COUNT", a["VENUE_TOUCHING_COUNT"])]
    for n in a["KNOWN_VENUE_TOUCHING_WORKFLOWS"]:
        L.append("    %s" % n)
    for n, g in sorted(a.get("WORKFLOWS_OUTSIDE_THE_DOMAIN", {}).items()):
        L.append("%-38s = %s (group %s)" % ("OUTSIDE_THE_DOMAIN", n, g))
    L.append("%-38s = %s" % ("DOMAIN_AUDIT", a["DOMAIN_AUDIT"]))
    return "\n".join(L)


def _cli():                                                   # pragma: no cover
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--active-runs", default=None,
                    help="JSON from the Actions API (workflow_runs list)")
    ap.add_argument("--self-run-id", default=None)
    ap.add_argument("--mode", choices=("start", "during"), default="start",
                    help="start: running OR pending blocks. "
                         "during: only a RUNNING collector is a confound.")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = audit(a.root)
    print(render(rep))
    if a.active_runs:
        raw = json.loads(Path(a.active_runs).read_text())
        runs = raw.get("workflow_runs", raw) if isinstance(raw, dict) else raw
        known = rep["KNOWN_VENUE_TOUCHING_WORKFLOWS"]
        iso = isolation(runs, known, a.self_run_id)
        rep.update(iso)
        if a.mode == "during":
            rep.update(during_run_conflict(runs, known, a.self_run_id))
        for k in ("ACTIVE_DIRECT_CONFLICTS", "PENDING_DIRECT_CONFLICTS",
                  "KNOWN_DIRECT_PMUS_COLLECTORS_ACTIVE",
                  "KNOWN_DIRECT_PMUS_COLLECTORS_PENDING",
                  "DIRECT_RESEARCH_COLLECTOR_ISOLATION",
                  "INDIRECT_BETTOR_PMUS_LOAD_ISOLATION"):
            print("%-38s = %s" % (k, rep[k]))
        if a.mode == "during":
            print("%-38s = %s" % ("DIRECT_CONFLICT_STARTED_DURING_RUN",
                                  rep["DIRECT_CONFLICT_STARTED_DURING_RUN"]))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True,
                                          default=str))
    if rep["DOMAIN_AUDIT"] != "PASS":
        raise SystemExit("DOMAIN_AUDIT = FAIL")
    # Only the START gate aborts. The DURING check records and returns.
    if (a.active_runs and a.mode == "start"
            and rep["DIRECT_RESEARCH_COLLECTOR_ISOLATION"] != "ESTABLISHED"):
        raise SystemExit("DIRECT_RESEARCH_COLLECTOR_ISOLATION = NOT_ESTABLISHED")


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
