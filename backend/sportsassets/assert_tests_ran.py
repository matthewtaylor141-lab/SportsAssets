"""A SKIP IS NOT A PASS. Refuse a JUnit report whose tests did not execute.

── WHY THIS EXISTS ──────────────────────────────────────────────────
Every capital-critical risk test is guarded by `@pg`, which skips when
`RN1X_TEST_DSN` is unset. A typo in a workflow env block, a service
container that never came up, or a renamed variable makes them ALL skip --
and pytest reports `N skipped` with exit status 0, which a CI job reads as
green. The tests would then be cited as evidence for a loss control while
never having run once.

That is worse than not having the tests at all: their existence is the
claim, and the claim would be false.

── AND WHY IT IS A MODULE RATHER THAN A HEREDOC ─────────────────────
It was a heredoc first, and the heredoc broke the workflow. A `<<'PYX'`
body at column 0 inside a YAML block scalar ends the scalar early; GitHub
then cannot parse the file and reports `Workflow does not have
'workflow_dispatch' trigger`, which names nothing about indentation and
sent me looking for a missing trigger that was present all along. Local
`yaml.safe_load` had accepted it.

A module has none of that failure surface, and -- the better reason -- it
can be tested. A guard that cannot itself be tested is the wrong shape for
something whose whole job is to catch a silent failure.
"""

import xml.etree.ElementTree as ET

R_NOT_A_REPORT = "JUNIT_REPORT_UNREADABLE"
R_NONE_RAN = "NOT_ONE_TEST_EXECUTED"
R_SOME_SKIPPED = "TESTS_WERE_SKIPPED_AND_A_SKIP_IS_NOT_A_PASS"
R_FAILED = "TESTS_FAILED_OR_ERRORED"


def tally(path: str) -> dict:
    """Collected / ran / skipped / failed, summed over every suite.

    A JUnit file may hold one `<testsuite>` or a `<testsuites>` wrapper
    around several. Reading only the root's attributes gives zero on the
    wrapper form -- which would report "0 collected" as though nothing had
    been asked for, and a `ran == 0` refusal would then fire on a perfectly
    good run. So both shapes are summed.
    """
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": R_NOT_A_REPORT,
                "path": path, "error": "%s: %s" % (type(exc).__name__, exc),
                "why": ("no report means the run did not get far enough to "
                        "write one, which is a failure and not an absence "
                        "of news")}
    suites = root.findall(".//testsuite") or [root]
    out = {"collected": 0, "skipped": 0, "failures": 0, "errors": 0}
    for s in suites:
        for k, attr in (("collected", "tests"), ("skipped", "skipped"),
                        ("failures", "failures"), ("errors", "errors")):
            try:
                out[k] += int(s.get(attr) or 0)
            except (TypeError, ValueError):
                pass
    out["ran"] = out["collected"] - out["skipped"]
    out["suites"] = len(suites)
    return dict(out, ok=True)


def verdict(path: str, *, allow_skips: bool = False) -> dict:
    """PASS only when tests actually executed and none of them failed.

    `allow_skips` exists for suites where a skip is legitimate (a test
    guarded on an optional dependency). It defaults to FALSE, because the
    caller that matters here is the risk gate, and there a skip is the
    exact failure being guarded against. A default of True would have made
    this module agree with the bug.
    """
    t = tally(path)
    if not t.get("ok"):
        return dict(t, passed=False)
    reasons = []
    if t["ran"] == 0:
        reasons.append({
            "refusal": R_NONE_RAN,
            "why": ("not one test executed. The DSN did not reach pytest, so "
                    "these tests are not evidence of anything -- and pytest "
                    "still exits 0, which a job reads as green")})
    if t["skipped"] and not allow_skips:
        reasons.append({
            "refusal": R_SOME_SKIPPED,
            "count": t["skipped"],
            "why": ("a skip is not a pass. A capital-critical test that did "
                    "not run must not be counted as one that passed")})
    if t["failures"] or t["errors"]:
        reasons.append({
            "refusal": R_FAILED,
            "failures": t["failures"], "errors": t["errors"],
            "why": "tests that ran did not pass"})
    return dict(t, passed=not reasons, reasons=reasons)


def _main(argv):
    import json

    if len(argv) < 2:
        print("usage: assert_tests_ran <junit.xml> [--allow-skips]")
        return 2
    allow = "--allow-skips" in argv[2:]
    got = verdict(argv[1], allow_skips=allow)
    print(json.dumps(got, indent=2))
    if got.get("passed"):
        print("OK: %d test(s) ran against the migrated database"
              % got.get("ran", 0))
        return 0
    for r in got.get("reasons") or [{"refusal": got.get("refusal")}]:
        print("REFUSED: %s -- %s" % (r.get("refusal"), r.get("why", "")))
    return 1


if __name__ == "__main__":                                  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))
