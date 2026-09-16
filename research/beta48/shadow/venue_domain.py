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
from datetime import timedelta
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


def max_job_timeout_s(root):
    """The longest job timeout among known venue-touching workflows.

    Coverage needs this: a run that OVERLAPS our window may have been CREATED
    long before it. run85-phase2-capture is created at 16:27 and still running
    at 21:30 -- paging back only to the window start would have missed the very
    collision this exists to catch. A run cannot outlive its own timeout, so
    that is the lookback, read from the workflows rather than guessed.
    """
    import re as _re
    best = 0
    for n in venue_touching_workflows(root):
        txt = Path(root, ".github/workflows", "%s.yml" % n).read_text(
            errors="ignore")
        for m in _re.finditer(r"timeout-minutes:\s*(\d+)", txt):
            best = max(best, int(m.group(1)))
    return (best or 360) * 60


def overlap_audit(window_start, window_end, runs, known, self_run_id=None,
                  venue_windows=None, more_pages=None, lookback_s=None,
                  pages_fetched=None):
    """Which known direct collectors overlapped the evidence window, and HOW
    WELL WE KNOW IT.

    `venue_windows` maps run id -> (first_get, last_get) for runs that sealed
    their own request timestamps; those give LEVEL A. Everything else falls
    back to the job interval and is labelled a conservative proxy.
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

    confirmed, possible, considered = [], [], 0
    for r in runs or ():
        rid = str(r.get("id", ""))
        if self_run_id is not None and rid == str(self_run_id):
            continue
        if r.get("name") not in known:
            continue
        considered += 1
        created = _iso(r.get("created_at"))
        started = _iso(r.get("run_started_at")) or created
        if started is None:
            continue
        # never executed -> never venue load
        if r.get("status") in WAITING_STATES and r.get("conclusion") in (
                None, "cancelled", "skipped"):
            continue

        # LEVEL A. The other run sealed the times of its OWN first and last
        # venue GET. That is an observation of venue contact, so an overlap of
        # that interval is CONFIRMED.
        vw = venue_windows.get(rid) or venue_windows.get(r.get("id"))
        a0 = a1 = None
        if vw:
            a0, a1 = _iso(vw[0]), _iso(vw[1])
        if a0 is not None and a1 is not None:
            if a0 <= we and a1 >= ws:
                confirmed.append({
                    "NAME": r.get("name"), "ID": rid,
                    "EVIDENCE_LEVEL": "A_SEALED_VENUE_REQUEST_TIMES",
                    "OTHER_FIRST_VENUE_GET_TIME": a0.isoformat(),
                    "OTHER_LAST_VENUE_GET_TIME": a1.isoformat(),
                    "STATUS": r.get("status")})
            continue

        # LEVEL B. No request timestamps -- only a job interval. A job is
        # RUNNING through checkout, tests, provenance and upload, none of which
        # touches the venue, so this is a POSSIBILITY and is labelled one. We
        # do not manufacture the timestamps the other run never wrote.
        ended = (_iso(r.get("updated_at")) if r.get("status") == "completed"
                 else we)
        if ended is None:
            ended = we
        if started <= we and ended >= ws:
            possible.append({
                "NAME": r.get("name"), "ID": rid,
                "EVIDENCE_LEVEL": "B_" + CONSERVATIVE_PROXY,
                "OTHER_RUN_START": started.isoformat(),
                "OTHER_RUN_END": (ended.isoformat()
                                  if r.get("status") == "completed"
                                  else "STILL_RUNNING"),
                "VENUE_REQUEST_TIMES": NOT_IDENTIFIED,
                "STATUS": r.get("status")})

    # COVERAGE, PER WORKFLOW. One workflow's history reaching back far enough
    # says nothing about another's. And the anchor is NOT the window start: a
    # run that overlaps the window may have been CREATED hours before it --
    # run85-phase2-capture is created at 16:27 and still running at 21:30 --
    # so history must reach back a full job timeout before the window, or the
    # very collision this exists to catch sits just off the end of the page.
    lb = float(lookback_s) if lookback_s else 0.0
    anchor = ws - timedelta(seconds=lb)
    cov_rows, coverage_complete = [], True
    for name in known:
        times = [_iso(r.get("created_at")) for r in (runs or ())
                 if r.get("name") == name]
        times = sorted(t for t in times if t is not None)
        mp = more_pages.get(name, more_pages.get("*", None))
        pf = pages_fetched.get(name, pages_fetched.get("*", NOT_IDENTIFIED))
        earliest = times[0] if times else None
        if mp is False:
            covers = "YES"          # the API says there is nothing older
        elif earliest is not None and earliest <= anchor:
            covers = "YES"
        else:
            covers = "NO"
        if covers != "YES":
            coverage_complete = False
        cov_rows.append({
            "WORKFLOW_NAME": name,
            "HISTORY_PAGES_FETCHED": pf,
            "EARLIEST_RUN_TIME_FETCHED": (earliest.isoformat() if earliest
                                          else NOT_IDENTIFIED),
            "LATEST_RUN_TIME_FETCHED": (times[-1].isoformat() if times
                                        else NOT_IDENTIFIED),
            "MORE_PAGES_AVAILABLE": (NOT_IDENTIFIED if mp is None
                                     else ("YES" if mp else "NO")),
            "COVERS_EVIDENCE_WINDOW": covers,
        })

    # PRECEDENCE. A DETECTED overlap is a fact and outranks coverage: thin
    # history cannot turn something we saw into something unknown. Coverage
    # gates only the NEGATIVE -- concluding "nothing overlapped" needs history
    # that reaches back far enough to have seen it.
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
        why = ("the fetched run history does not reach back a full job timeout "
               "before the window start for every known direct collector, so "
               "an older overlapping run cannot be excluded")
    elif verdict == "POSSIBLE":
        why = ("the other run's job interval overlaps but it sealed no venue "
               "request times, so contact is possible and not observed")

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
        "RUNNING_IS_NOT_PROVEN_VENUE_CONTACT": WHY_RUNNING_IS_NOT_CONTACT,
        "LEVEL_B_LABEL": CONSERVATIVE_PROXY,
        "DO_NOT_SYNTHESIZE_GET_TIMESTAMPS": DO_NOT_SYNTHESIZE_GET_TIMESTAMPS,
        "DIRECT_RUN_HISTORY_COVERAGE": cov_rows,
        "DIRECT_RUN_HISTORY_COVERAGE_COMPLETE": (
            "YES" if coverage_complete else "NO"),
        "COVERAGE_LOOKBACK_S": lb,
        "COVERAGE_ANCHOR": anchor.isoformat(),
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
