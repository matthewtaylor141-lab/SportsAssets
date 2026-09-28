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

import re
import sys
import xml.etree.ElementTree as ET

#: ── IDENTITIES THAT ARE NOT STABLE ACROSS RUNS ─────────────────────────
#:
#: A REAL FINDING FROM THIS TOOL'S FIRST USE. Four identities appeared to
#: VANISH between the two runs. They had not: they are parametrized tests
#: whose parameter embeds `datetime.now()`, so the test id itself carries a
#: wall-clock instant --
#:
#:   ...test_it_fails_closed_on_every_bad_budget[{"started_at":
#:      "2026-09-28T12:31:27.279622+00:00", ...}-BUDGET_EXHAUSTED]
#:
#: Two runs started seconds apart therefore produce DIFFERENT NAMES FOR THE
#: SAME TEST, and any identity-based comparison reads that as one deletion
#: plus one addition. That defeats exactly the check this file exists to
#: make, so timestamps are normalised out of the identity before comparing.
#:
#: The underlying test hygiene is still worth fixing at source -- a test id
#: should not depend on the clock -- and normalising here does not fix it,
#: it only stops it corrupting the gate verdict. Reported, not silenced.
_TS = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def canon(ident: str) -> str:
    """A test identity with embedded wall-clock instants neutralised."""
    return _TS.sub("<TS>", ident)


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
            bad.add(canon("%s::%s" % (case.get("classname") or "",
                                      case.get("name") or "")))
    tot["collected"] = collected
    return bad, tot


def ids(path: str) -> set:
    """Every test identity in the file, passing or not."""
    root = ET.parse(path).getroot()
    return {canon("%s::%s" % (c.get("classname") or "", c.get("name") or ""))
            for c in root.iter("testcase")}


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    base_p, head_p = argv[1], argv[2]
    base, bt = read(base_p)
    head, ht = read(head_p)
    base_all, head_all = ids(base_p), ids(head_p)

    print("== TOTALS ==")
    print("%-10s %8s %8s" % ("", "BASE", "HEAD"))
    for k in ("collected", "tests", "failures", "errors", "skipped"):
        print("%-10s %8s %8s" % (k, bt[k], ht[k]))
    print("%-10s %8.1f %8.1f" % ("seconds", bt["time"], ht["time"]))
    print("%-10s %8d %8d" % ("bad ids", len(base), len(head)))

    # ── RECONCILE A COLLECTED-COUNT DIFFERENCE RATHER THAN REFUSING ────
    #
    # THE CORRECTION THIS IS. The first version printed UNINTERPRETABLE on
    # any collected-count difference. That is too strict and it fired on
    # its own first real use: HEAD collected 38 more tests than BASE
    # because HEAD ADDS 38 TESTS. Added tests do not invalidate a
    # comparison over identities -- a new test that fails shows up in
    # `head - base` exactly as a regression would, which is the behaviour
    # you want.
    #
    # So the difference is ACCOUNTED FOR instead: identities added,
    # identities removed, and whether every addition passed. The
    # comparison is only refused when identities VANISHED (a test that
    # existed and no longer does could be hiding a failure) or when the
    # delta cannot be explained by additions alone.
    added = sorted(head_all - base_all)
    removed = sorted(base_all - head_all)
    print("\n== COLLECTION RECONCILED ==")
    print("identities added in HEAD   : %d" % len(added))
    print("identities removed in HEAD : %d" % len(removed))
    failing_additions = sorted(set(added) & head)
    print("added identities FAILING   : %d" % len(failing_additions))
    for t in failing_additions:
        print("  ! %s" % t)
    if removed:
        print("\n!! %d IDENTITIES VANISHED. A test that existed in BASE and "
              "does not exist in HEAD cannot be compared, and a deletion can "
              "hide a failure. Each is listed; the comparison is refused "
              "until each is accounted for." % len(removed))
        for t in removed[:40]:
            print("  ? %s" % t)
    explained = (bt["collected"] + len(added) - len(removed) == ht["collected"])
    print("\ndelta explained by add/remove: %s" % ("yes" if explained else "NO"))

    new = sorted(head - base)
    fixed = sorted(base - head)
    print("\n== NEW IN HEAD (regressions) : %d ==" % len(new))
    for t in new:
        print("  + %s" % t)
    print("\n== RESOLVED BY HEAD : %d ==" % len(fixed))
    for t in fixed:
        print("  - %s" % t)

    print("\n== VERDICT ==")
    if removed or not explained:
        print("UNINTERPRETABLE -- %s." % ("identities vanished" if removed
                                          else "the collected delta is not "
                                               "explained by additions"))
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
