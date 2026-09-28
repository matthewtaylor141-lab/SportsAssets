#!/usr/bin/env python3
"""COMPARE TWO JUnit XMLs BY FAILURE IDENTITY, NOT BY COUNT.

WHY COUNTS ARE NOT ENOUGH, and this has bitten this repository before. Two
runs can report the same number of failures while a fix and a regression
cancel out. The only comparison that establishes "no new failures" is a
SET DIFFERENCE over test identities, in BOTH directions:

    in HEAD but not BASE  -> a regression this change introduced
    in BASE but not HEAD  -> something this change resolved

A matched gate also has to be run on the same instrument. This script
prints the totals so a mismatch in collected counts is visible rather
than averaged away: if the two runs did not collect the same tests, the
set difference is measuring two different suites and says nothing.

    python3 gate_diff.py BASE.xml HEAD.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def read(path: str):
    """(identities_of_failures, totals) for one JUnit XML."""
    root = ET.parse(path).getroot()
    suites = ([root] if root.tag == "testsuite"
              else list(root.iter("testsuite")))
    bad, collected = set(), 0
    tot = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for s in suites:
        for k in ("tests", "failures", "errors", "skipped"):
            tot[k] += int(s.get(k) or 0)
        tot["time"] += float(s.get("time") or 0.0)
    for case in root.iter("testcase"):
        collected += 1
        # A failure AND an error both mean the test did not pass. Counting
        # only <failure> would hide a collection error as a pass.
        if case.find("failure") is not None or case.find("error") is not None:
            ident = "%s::%s" % (case.get("classname") or "",
                                case.get("name") or "")
            bad.add(ident)
    tot["collected"] = collected
    return bad, tot


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    base_p, head_p = argv[1], argv[2]
    base, bt = read(base_p)
    head, ht = read(head_p)

    print("== TOTALS ==")
    print("%-10s %8s %8s" % ("", "BASE", "HEAD"))
    for k in ("collected", "tests", "failures", "errors", "skipped"):
        print("%-10s %8s %8s" % (k, bt[k], ht[k]))
    print("%-10s %8.1f %8.1f" % ("seconds", bt["time"], ht["time"]))
    print("%-10s %8d %8d" % ("bad ids", len(base), len(head)))

    if bt["collected"] != ht["collected"]:
        print("\n!! COLLECTED COUNTS DIFFER (%d vs %d). The two runs did not "
              "collect the same tests, so the set difference below compares "
              "two different suites. Treat it as UNINTERPRETABLE until the "
              "instrument matches." % (bt["collected"], ht["collected"]))

    new = sorted(head - base)
    fixed = sorted(base - head)
    print("\n== NEW IN HEAD (regressions) : %d ==" % len(new))
    for t in new:
        print("  + %s" % t)
    print("\n== RESOLVED BY HEAD : %d ==" % len(fixed))
    for t in fixed:
        print("  - %s" % t)

    print("\n== VERDICT ==")
    if bt["collected"] != ht["collected"]:
        print("UNINTERPRETABLE -- collected counts differ.")
        return 3
    if new:
        print("REGRESSION -- %d test(s) fail on HEAD that pass on BASE."
              % len(new))
        return 1
    print("NO NEW FAILURES -- the HEAD failure set is a subset of BASE.")
    if fixed:
        print("And %d were resolved." % len(fixed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
