"""The risk gate must refuse a report whose tests never executed.

Owner requirement: "Run these against a migrated database. 'No local DSN'
is an execution dependency, not acceptance evidence. ... show that the
tests ran rather than skipped."

THE FAILURE THIS GUARDS. Every risk test is `@pg`-guarded and skips when
RN1X_TEST_DSN is unset. pytest then reports `N skipped` and exits 0, which
a CI job reads as green -- so the capital-critical tests would be cited as
evidence for a loss control while never having run. That is worse than not
having them: their existence is the claim.

AND THE GUARD ITSELF IS TESTED, which is the point of it being a module.
The first version was a heredoc inside the workflow; it could not be
tested, and it broke the workflow's YAML in a way whose error message
("Workflow does not have 'workflow_dispatch' trigger") named nothing about
the real cause.
"""

import pathlib

import pytest

from sportsassets import assert_tests_ran as G


#: A COUNTER, SO TWO REPORTS IN ONE TEST ARE TWO FILES. The first version
#: wrote every report to `j.xml`, so a test building both a passing and a
#: failing report got the second one twice and the "passing" assertion read
#: the failing content. Found by the test failing, which is the right way
#: round, but it is worth naming: a fixture that silently aliases its
#: outputs makes two assertions about one input look like two cases.
_n = [0]


def _xml(tmp_path, body):
    _n[0] += 1
    p = tmp_path / ("j%d.xml" % _n[0])
    p.write_text(body)
    return str(p)


ONE_SUITE = ('<testsuite name="pytest" tests="%d" skipped="%d" '
             'failures="%d" errors="%d"></testsuite>')
WRAPPED = ('<testsuites><testsuite name="a" tests="%d" skipped="%d" '
           'failures="0" errors="0"></testsuite></testsuites>')


def test_all_skipped_is_refused(tmp_path):
    """14 collected, 14 skipped, 0 failures -- pytest exits 0. Refuse it."""
    got = G.verdict(_xml(tmp_path, ONE_SUITE % (14, 14, 0, 0)))
    assert got["passed"] is False
    codes = {r["refusal"] for r in got["reasons"]}
    assert G.R_NONE_RAN in codes, got
    assert G.R_SOME_SKIPPED in codes, got
    assert got["ran"] == 0


def test_one_skip_among_passes_is_still_refused(tmp_path):
    """A partial skip is the subtler case and must fail too.

    13 of 14 running looks healthy in a summary line. The one that did not
    run is exactly the one nobody notices.
    """
    got = G.verdict(_xml(tmp_path, ONE_SUITE % (14, 1, 0, 0)))
    assert got["passed"] is False
    assert {r["refusal"] for r in got["reasons"]} == {G.R_SOME_SKIPPED}
    assert got["ran"] == 13


def test_a_clean_run_passes(tmp_path):
    got = G.verdict(_xml(tmp_path, ONE_SUITE % (14, 0, 0, 0)))
    assert got["passed"] is True, got
    assert got["ran"] == 14
    assert got["reasons"] == []


def test_failures_and_errors_are_refused_separately(tmp_path):
    got = G.verdict(_xml(tmp_path, ONE_SUITE % (14, 0, 2, 1)))
    assert got["passed"] is False
    r = [x for x in got["reasons"] if x["refusal"] == G.R_FAILED][0]
    assert r["failures"] == 2 and r["errors"] == 1


def test_a_missing_report_is_a_failure_not_an_absence_of_news(tmp_path):
    """No file means the run died before writing one."""
    got = G.verdict(str(tmp_path / "does-not-exist.xml"))
    assert got["passed"] is False
    assert got["refusal"] == G.R_NOT_A_REPORT
    assert "not an absence" in got["why"]


def test_an_unparsable_report_is_refused(tmp_path):
    got = G.verdict(_xml(tmp_path, "<testsuite tests='3'"))
    assert got["passed"] is False
    assert got["refusal"] == G.R_NOT_A_REPORT


def test_the_testsuites_wrapper_form_is_summed_not_read_off_the_root(tmp_path):
    """THE BUG THIS PREVENTS, and it would have fired the wrong refusal.

    pytest writes `<testsuites><testsuite .../></testsuites>` in some
    versions. The wrapper carries no `tests` attribute, so reading the
    root alone gives 0 collected -- and `ran == 0` would then refuse a
    perfectly good run, reporting "the DSN did not reach pytest" about a
    run where it did. A guard that cries wolf gets disabled.
    """
    got = G.verdict(_xml(tmp_path, WRAPPED % (14, 0)))
    assert got["passed"] is True, got
    assert got["collected"] == 14 and got["ran"] == 14
    assert got["suites"] == 1


def test_allow_skips_defaults_to_false(tmp_path):
    """The default must not agree with the bug.

    A default of `allow_skips=True` would make this module accept exactly
    the report it exists to reject, and every caller would have to
    remember to opt in. Defaults are where guards are quietly lost.
    """
    import inspect
    sig = inspect.signature(G.verdict)
    assert sig.parameters["allow_skips"].default is False
    # AND THE OPT-IN STILL WORKS, for suites where a skip is legitimate.
    p = _xml(tmp_path, ONE_SUITE % (14, 1, 0, 0))
    assert G.verdict(p, allow_skips=True)["passed"] is True
    assert G.verdict(p)["passed"] is False


def test_the_cli_exit_status_is_the_verdict(tmp_path):
    """0 only on a pass; the workflow's `set -e` depends on it."""
    ok = _xml(tmp_path, ONE_SUITE % (14, 0, 0, 0))
    bad = _xml(tmp_path, ONE_SUITE % (14, 14, 0, 0))
    assert G._main(["x", ok]) == 0
    assert G._main(["x", bad]) == 1
    assert G._main(["x"]) == 2


def test_the_workflow_calls_this_module_and_not_a_heredoc():
    """The gate must use the tested guard, not a second copy of the rule.

    A reimplementation in YAML is a second chance to get it wrong, and the
    YAML copy is the one that cannot be tested.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    wf = (root / ".github/workflows/command-verify.yml").read_text()
    assert "python3 -m sportsassets.assert_tests_ran /tmp/risk.xml" in wf
    # AND THE HEREDOC IS GONE. It broke the workflow's YAML parse.
    assert "<<'PYX'\nimport sys" not in wf
