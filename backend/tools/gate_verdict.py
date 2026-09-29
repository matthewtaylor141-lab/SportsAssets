"""THE GATE VERDICT, FROM STRUCTURED REPORTS, WITH VALIDITY BEFORE COMPARISON.

Replaces the console-regex comparator. Five acceptance gaps were reproduced
against that one (A empty log, B collection error, C undercounted summary,
D parameterised ids with spaces collapsing, E discarded runner exit status), and
every one of them turned an invalid or regressed run into "NO NEW FAILURE
IDENTITY". The lesson is not that the regex was wrong; it is that a human
summary is the wrong authority for an acceptance decision.

THREE OUTCOMES, NEVER TWO. The previous tool answered one question -- are there
new node ids -- so "the run did not happen" and "the run was clean" shared an
answer. They are separated here:

    INVALID         the artifact or the run cannot support a comparison
    NEW_FAILURES    a completed run with identities the baseline lacks
    ACCEPTED        a completed run whose failures the baseline already had

Only ACCEPTED exits 0.

THE BASELINE FAILS 367 TESTS, so `exitstatus == 1` is EXPECTED for a completed
run and must be handled deliberately rather than falling through to success. Any
other status, a collection error, a missing completion record, or a manifest that
does not reconcile is INVALID.
"""

from __future__ import annotations

import json
import sys

# pytest's own exit codes.
EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_INTERRUPTED = 2
EXIT_INTERNAL_ERROR = 3
EXIT_USAGE_ERROR = 4
EXIT_NO_TESTS_COLLECTED = 5

#: A completed run may only exit 0 (nothing failed) or 1 (tests failed). Every
#: other status means the run itself did not do what a gate needs.
COMPLETED_STATUSES = (EXIT_OK, EXIT_TESTS_FAILED)

#: Outcomes that count as a failure identity. `xfailed` does NOT: an expected
#: failure is a recorded expectation, and promoting it to a failure would make
#: every xfail a permanent new-failure. `xpassed` also does not -- it is a
#: signal worth reporting, and it is reported, but it is not a regression.
FAILING = ("failed",)

#: Reported separately because each is a proof that did not run.
NOT_RUN = ("skipped",)

#: EVERY OUTCOME THE REPORTER CAN WRITE. A node carrying anything else was not
#: produced by `gate_report` as written, so the report is not trusted at all.
KNOWN_OUTCOMES = ("passed", "failed", "skipped", "xfailed", "xpassed")

#: THE ONE OUTCOME A CRITICAL PROOF MAY HAVE, and the three phases it must have
#: passed. The matched baseline excuses failures elsewhere; for the critical
#: list nothing but a complete pass is a proof:
#:
#:   failed     REJECTED  the property does not hold.
#:   xfailed    REJECTED  the test DECLARES the property does not hold. An
#:                        expected failure is a recorded expectation of failure,
#:                        and a proof that is expected to fail proves nothing.
#:   xpassed    REJECTED  DEFINED HERE, deliberately: the test still declares
#:                        the property does not hold, and a pass that
#:                        contradicts its own marker is not accepted as a proof
#:                        until the marker is removed and the test passes as an
#:                        ordinary test. (Outside the critical list an xpass is
#:                        reported and is not a regression.)
#:   skipped    INVALID   the proof did not run.
#:   anything else, or a passed node missing its setup, call or teardown
#:              record    INVALID   the record is incomplete or not ours.
CRITICAL_PASS = "passed"
CRITICAL_PHASES = ("setup", "call", "teardown")


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, "the report file does not exist: %s" % path
    except json.JSONDecodeError as exc:
        return None, ("the report is not valid JSON (%s). A truncated or "
                      "half-written artifact is not a clean run" % exc)
    if not isinstance(doc, dict):
        return None, "the report is not an object"
    if doc.get("schema") != "GATE_REPORT_V1":
        return None, ("the report carries schema %r, not GATE_REPORT_V1"
                      % doc.get("schema"))
    return doc, None


def validate(doc, label):
    """Every reason this run cannot be compared. Empty list means it can."""
    bad = []
    if not doc.get("session_complete"):
        bad.append("%s: the session never finished -- there is no completion "
                   "record, so the node list is whatever had run when it "
                   "stopped" % label)
    if doc.get("interrupted"):
        bad.append("%s: the run was interrupted (%s)"
                   % (label, doc["interrupted"]))
    st = doc.get("exitstatus")
    if st not in COMPLETED_STATUSES:
        bad.append("%s: pytest exited %r. Only 0 and 1 are completed runs; %r "
                   "means the run itself did not do what a gate needs"
                   % (label, st, st))
    if doc.get("collect_errors"):
        ids = [e.get("nodeid") for e in doc["collect_errors"]]
        bad.append("%s: %d collection error(s) %s. Tests that could not be "
                   "collected did not run, and a collection failure has no "
                   "`::` so an identity comparison cannot see it"
                   % (label, len(ids), ids[:5]))
    if not doc.get("collected"):
        bad.append("%s: nothing was collected" % label)
    # ── THE MANIFEST MUST RECONCILE, PER PYTEST'S OWN SEMANTICS ──────
    #
    # Not "summary count == len(set)", which is the comparison that let an
    # undercounted log through. Every collected test must have produced phase
    # records, unless it was deselected. A collected test with no phases at all
    # did not run and nobody noticed.
    collected = set(doc.get("collected") or ())
    executed = set((doc.get("nodes") or {}).keys())
    deselected = set(doc.get("deselected") or ())
    missing = collected - executed - deselected
    if missing:
        bad.append("%s: %d collected test(s) produced no phase record, so they "
                   "did not run: %s"
                   % (label, len(missing), sorted(missing)[:5]))
    stray = executed - collected
    if stray:
        bad.append("%s: %d executed node(s) were not in the collected "
                   "manifest: %s" % (label, len(stray), sorted(stray)[:5]))
    unknown = sorted(n for n, v in (doc.get("nodes") or {}).items()
                     if v.get("outcome") not in KNOWN_OUTCOMES
                     or any(o not in KNOWN_OUTCOMES
                            for o in (v.get("phases") or {}).values()))
    if unknown:
        bad.append("%s: %d node(s) carry an outcome the reporter does not "
                   "write (%s), so the report is not ours or is damaged"
                   % (label, len(unknown), unknown[:5]))
    st_ok = st == EXIT_TESTS_FAILED
    fails = failing(doc)
    if st_ok and not fails:
        bad.append("%s: pytest exited 1 (tests failed) but the report records "
                   "no failing node. The two disagree about what happened"
                   % label)
    if st == EXIT_OK and fails:
        bad.append("%s: pytest exited 0 but the report records %d failing "
                   "node(s)" % (label, len(fails)))
    return bad


def failing(doc):
    return {n for n, v in (doc.get("nodes") or {}).items()
            if v.get("outcome") in FAILING}


def skipped(doc):
    return {n for n, v in (doc.get("nodes") or {}).items()
            if v.get("outcome") in NOT_RUN}


def xpassed(doc):
    return {n for n, v in (doc.get("nodes") or {}).items()
            if v.get("outcome") == "xpassed"}


def main(argv):
    if len(argv) < 3:
        print("usage: gate_verdict.py BASELINE.json HEAD.json "
              "[--critical FILE]")
        return 2
    base_path, head_path = argv[1], argv[2]
    critical = []
    if "--critical" in argv:
        cp = argv[argv.index("--critical") + 1]
        with open(cp, encoding="utf-8") as fh:
            critical = [ln.strip() for ln in fh
                        if ln.strip() and not ln.startswith("#")]

    base, be = load(base_path)
    head, he = load(head_path)
    problems = [e for e in (be, he) if e]
    if base is not None:
        problems += validate(base, "BASELINE")
    if head is not None:
        problems += validate(head, "HEAD")

    if problems:
        print("VERDICT: INVALID -- this is not acceptance evidence.")
        for p in problems:
            print("  ! %s" % p)
        print()
        print("An invalid run is NOT 'no new failures'. Nothing is compared.")
        return 2

    bf, hf = failing(base), failing(head)
    new, fixed = sorted(hf - bf), sorted(bf - hf)

    for label, doc in (("BASELINE", base), ("HEAD", head)):
        print("%s  exitstatus=%s  collected=%d executed=%d deselected=%d"
              % (label, doc["exitstatus"], doc["collected_count"],
                 doc["executed_count"], len(doc.get("deselected") or ())))
        print("  counts: %s" % json.dumps(doc.get("counts") or {},
                                          sort_keys=True))
        print("  commit-ish argv: %s" % (doc["environment"].get("argv"),))
        print("  database: %s   python %s   pytest %s"
              % (doc["environment"].get("dsn_database"),
                 doc["environment"].get("python"),
                 doc["environment"].get("pytest")))
    print()

    # ── A CRITICAL PROOF THAT DID NOT RUN IS NOT A PASSED PROOF ──────
    crit_bad = []
    hnodes = head.get("nodes") or {}
    hdesel = set(head.get("deselected") or ())
    hcoll = set(head.get("collected") or ())
    # A FILE-LEVEL ENTRY (no `::`) names every test that file collects, so the
    # list can be declared and committed before the run it judges instead of
    # being generated from it. A listed file that collected NOTHING is itself
    # a critical proof that did not run.
    expanded = []
    for c in critical:
        if "::" in c:
            expanded.append(c)
            continue
        mine = sorted(n for n in (hcoll | hdesel) if n.split("::", 1)[0] == c)
        if not mine:
            crit_bad.append("%s collected no test at all" % c)
        expanded.extend(mine)
    critical = expanded
    # ── A CRITICAL PROOF IS ACCEPTED ONLY AS A COMPLETE PASS ─────────
    #
    # THE GAPS THIS CLOSES. The first version checked only that a critical test
    # RAN, so one failing in both runs was "pre-existing" and ACCEPTED. The
    # second rejected `failed` and nothing else, so an `xfailed` critical test
    # -- a test declaring that its property does NOT hold -- was ACCEPTED with
    # exit 0 (reproduced by independent review). The rule is now positive: a
    # critical node must be `passed` with setup, call and teardown each
    # recorded as passed. See CRITICAL_PASS for every other outcome.
    crit_failed = []
    for c in critical:
        v = hnodes.get(c)
        if c in hdesel:
            crit_bad.append("%s was DESELECTED" % c)
        elif c not in hcoll:
            crit_bad.append("%s was not COLLECTED" % c)
        elif v is None:
            crit_bad.append("%s produced no phase record" % c)
        elif v.get("outcome") in NOT_RUN:
            crit_bad.append("%s was SKIPPED" % c)
        elif v.get("outcome") in FAILING:
            crit_failed.append("%s FAILED" % c)
        elif v.get("outcome") == "xfailed":
            crit_failed.append("%s is XFAILED -- it declares its property does "
                               "not hold" % c)
        elif v.get("outcome") == "xpassed":
            crit_failed.append("%s XPASSED -- it still carries an expected-"
                               "failure marker, so its pass is not accepted "
                               "as a proof" % c)
        elif v.get("outcome") != CRITICAL_PASS:
            crit_bad.append("%s has outcome %r, which is not a pass"
                            % (c, v.get("outcome")))
        else:
            ph = v.get("phases") or {}
            short = [w for w in CRITICAL_PHASES if ph.get(w) != CRITICAL_PASS]
            if short:
                crit_bad.append("%s is recorded as passed without a passing "
                                "%s phase, so the record is incomplete"
                                % (c, "/".join(short)))
    crit_failed.sort()

    print("NEW FAILURES (in HEAD, not in the baseline): %d" % len(new))
    for n in new:
        v = hnodes.get(n, {})
        print("  + %s   [%s in %s]" % (n, v.get("outcome"),
                                       v.get("decided_by")))
    print()
    print("NO LONGER FAILING (in the baseline, not in HEAD): %d" % len(fixed))
    for n in fixed:
        print("  - %s" % n)
    print()
    print("COMMON (pre-existing, unchanged): %d" % len(bf & hf))
    hs, hx = skipped(head), xpassed(head)
    print("SKIPPED in HEAD: %d   XPASSED in HEAD: %d" % (len(hs), len(hx)))
    if hx:
        for n in sorted(hx)[:5]:
            print("  x %s  (expected to fail and did not -- worth reading, "
                  "not a regression)" % n)
    print()

    if crit_bad:
        print("VERDICT: INVALID -- a critical acceptance test did not execute.")
        for c in crit_bad:
            print("  ! %s" % c)
        return 2
    if crit_failed:
        print("VERDICT: NEW_FAILURES -- %d critical acceptance test(s) did not "
              "PASS. A critical proof must pass; the matched baseline does not "
              "excuse it." % len(crit_failed))
        for c in crit_failed:
            print("  ! %s" % c)
        return 1
    if new:
        print("VERDICT: NEW_FAILURES -- %d identity(ies) the baseline did not "
              "have. Each must be explained or fixed." % len(new))
        return 1
    print("VERDICT: ACCEPTED -- both runs completed, both manifests reconcile, "
          "and every failure in HEAD is one the matched baseline already had.")
    print("         This is NOT 'all tests green': %d baseline failures remain."
          % len(bf))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
