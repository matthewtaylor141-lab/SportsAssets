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

ABSOLUTE_VENUE_ISOLATION = "REFUSED_NOT_KNOWABLE"
WHY_ABSOLUTE_IS_REFUSED = (
    "we observe our own workflows, not the venue's global traffic")


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
    """BETTOR_COLLECTOR_ISOLATION from a list of currently active runs.

    `active_runs` is [{"name":…, "id":…, "status":…}, …] as the Actions API
    reports it. Our own run never counts against itself; anything else on the
    known list does.
    """
    known = set(known or ())
    conflicts = []
    for r in active_runs or ():
        rid = str(r.get("id", ""))
        if self_run_id is not None and rid == str(self_run_id):
            continue
        if r.get("status") not in ("in_progress", "queued", "pending",
                                   "requested", "waiting"):
            continue
        if r.get("name") in known:
            conflicts.append({"NAME": r.get("name"), "ID": rid,
                              "STATUS": r.get("status")})
    clear = not conflicts
    return {
        "ACTIVE_CONFLICTING_WORKFLOWS": len(conflicts),
        "CONFLICTS": conflicts,
        "BETTOR_COLLECTOR_ISOLATION": ("ESTABLISHED" if clear
                                       else "NOT_ESTABLISHED"),
        "NO_OTHER_KNOWN_BETTOR_GITHUB_COLLECTOR_RUNNING": ("YES" if clear
                                                           else "NO"),
        "NO_OTHER_CLIENT_ANYWHERE_IS_USING_THE_VENUE": ABSOLUTE_VENUE_ISOLATION,
        "WHY_ABSOLUTE_IS_REFUSED": WHY_ABSOLUTE_IS_REFUSED,
        "KNOWN_VENUE_TOUCHING_WORKFLOWS": sorted(known),
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
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = audit(a.root)
    print(render(rep))
    if a.active_runs:
        raw = json.loads(Path(a.active_runs).read_text())
        runs = raw.get("workflow_runs", raw) if isinstance(raw, dict) else raw
        iso = isolation(runs, rep["KNOWN_VENUE_TOUCHING_WORKFLOWS"],
                        a.self_run_id)
        rep.update(iso)
        for k in ("ACTIVE_CONFLICTING_WORKFLOWS", "BETTOR_COLLECTOR_ISOLATION"):
            print("%-38s = %s" % (k, rep[k]))
        for c in iso["CONFLICTS"]:
            print("%-38s = %s" % ("CONFLICT", c))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True,
                                          default=str))
    if rep["DOMAIN_AUDIT"] != "PASS":
        raise SystemExit("DOMAIN_AUDIT = FAIL")
    if a.active_runs and rep["BETTOR_COLLECTOR_ISOLATION"] != "ESTABLISHED":
        raise SystemExit("BETTOR_COLLECTOR_ISOLATION = NOT_ESTABLISHED")


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
