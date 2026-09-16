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


def overlap_audit(window_start, window_end, runs, known, self_run_id=None):
    """Which known direct collectors EXECUTED during the evidence window.

    A run counts only if it actually reached a running/executing state --
    queued-and-never-started issued no request. An in-flight run with no end
    time is treated as still running, which overlaps anything that began
    before now.
    """
    ws, we = _iso(window_start), _iso(window_end)
    known = set(known or ())
    if ws is None or we is None:
        return {"DIRECT_CONFLICT_STARTED_DURING_RUN": NOT_IDENTIFIED,
                "WHY_NOT_IDENTIFIED": "the evidence window is not established",
                "DIRECT_OVERLAP_COUNT": NOT_IDENTIFIED,
                "DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW": []}

    overlaps, considered, oldest = [], 0, None
    for r in runs or ():
        rid = str(r.get("id", ""))
        created = _iso(r.get("created_at"))
        if oldest is None or (created and created < oldest):
            oldest = created
        if self_run_id is not None and rid == str(self_run_id):
            continue
        if r.get("name") not in known:
            continue
        considered += 1
        started = _iso(r.get("run_started_at")) or created
        if started is None:
            continue
        # never executed -> never venue load
        if r.get("status") in WAITING_STATES and r.get("conclusion") in (
                None, "cancelled", "skipped"):
            continue
        ended = _iso(r.get("updated_at")) if r.get("status") == "completed" else we
        if ended is None:
            ended = we
        if started <= we and ended >= ws:
            overlaps.append({"NAME": r.get("name"), "ID": rid,
                             "OTHER_RUN_START": started.isoformat(),
                             "OTHER_RUN_END": (ended.isoformat()
                                               if r.get("status") == "completed"
                                               else "STILL_RUNNING"),
                             "STATUS": r.get("status")})

    # Coverage: the fetched history must reach back past the window start, or
    # an older overlapping run could be sitting just off the end of the page.
    # PRECEDENCE MATTERS. A DETECTED overlap is a fact and outranks coverage:
    # thin history cannot turn something we saw into something unknown.
    # Coverage only gates the NEGATIVE -- concluding "nothing overlapped"
    # requires history reaching back past the window start.
    covered = bool(oldest and ws and oldest <= ws)
    if overlaps:
        verdict = "YES"
    elif covered:
        verdict = "NO"
    else:
        verdict = NOT_IDENTIFIED
    return {
        "EVIDENCE_WINDOW": [ws.isoformat(), we.isoformat()],
        "DIRECT_RUNS_OVERLAPPING_EVIDENCE_WINDOW": overlaps,
        "DIRECT_OVERLAP_COUNT": len(overlaps),
        "DIRECT_CONFLICT_STARTED_DURING_RUN": verdict,
        "DIRECT_RESEARCH_COLLECTOR_ISOLATION_THROUGHOUT_RUN": (
            "NO" if verdict == "YES" else
            ("YES" if verdict == "NO" else NOT_IDENTIFIED)),
        "RUN_HISTORY_COVERS_THE_WINDOW": "YES" if covered else "NO",
        "OLDEST_RUN_IN_HISTORY": oldest.isoformat() if oldest else NOT_IDENTIFIED,
        "KNOWN_DIRECT_RUNS_CONSIDERED": considered,
        "WHY_NOT_IDENTIFIED": (None if verdict != NOT_IDENTIFIED else
                               "the fetched run history does not reach back "
                               "past the window start, so an older "
                               "overlapping run cannot be excluded"),
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
