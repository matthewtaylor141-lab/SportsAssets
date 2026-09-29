"""NEGATIVE CONTROLS FOR THE ACCEPTANCE TOOLING ITSELF.

The gate decides whether a commit is release-acceptable. Five ways it could say
"NO NEW FAILURE IDENTITY" about a run that never happened were reproduced against
the console-regex version, each with the executable code:

  A  an EMPTY head log            -> exit 0, "NO NEW FAILURE IDENTITY"
  B  a pytest collection error     -> exit 0, although pytest exits 2
  C  summary says 5 failed, the parser recognises 1 -> accepted
  D  `test_case[case A]` and `test_case[case B]` collapse to ONE identity,
     because the regex stopped at whitespace, so a run failing only A compared
     against one failing only B returned ZERO new identities
  E  the runner ended on `echo` and `tail`, so simulated pytest exits 1-5 all
     became runner exit 0

Every one turns invalid or regressed evidence into a clean gate. A tool that can
do that is worse than no tool, because it is used to stop looking.

So the authority is now a STRUCTURED report written from pytest's own hooks, the
verdict has THREE outcomes rather than two, and these tests hold each gap shut.
They are pure: they build report documents and assert the verdict, so they run
anywhere and cannot themselves be skipped for want of a database.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
VERDICT = TOOLS / "gate_verdict.py"

#: The three outcomes. Only ACCEPTED is a pass.
RC_ACCEPTED = 0
RC_NEW_FAILURES = 1
RC_INVALID = 2


def _report(*, nodes=None, collected=None, exitstatus=1, complete=True,
            collect_errors=None, deselected=None, interrupted=None):
    """A GATE_REPORT_V1 document, with only the fields the verdict reads."""
    nodes = dict(nodes or {})
    full = {}
    for nid, spec in nodes.items():
        if isinstance(spec, str):
            spec = {"outcome": spec, "decided_by": "call",
                    "phases": {"call": spec}, "longrepr": None}
        full[nid] = spec
    coll = list(collected if collected is not None else full.keys())
    counts = {}
    for v in full.values():
        counts[v["outcome"]] = counts.get(v["outcome"], 0) + 1
    return {
        "schema": "GATE_REPORT_V1",
        "session_complete": complete,
        "interrupted": interrupted,
        "exitstatus": exitstatus,
        "environment": {"python": "3.11.0", "pytest": "9.1.1", "argv": [],
                        "dsn_database": "testdb"},
        "counts": counts,
        "collected_count": len(coll),
        "executed_count": len(full),
        "collected": coll,
        "deselected": list(deselected or ()),
        "collect_errors": list(collect_errors or ()),
        "nodes": full,
        "started_at": 1.0, "finished_at": 2.0,
    }


def _run(tmp_path, base, head, critical=None):
    bp, hp = tmp_path / "base.json", tmp_path / "head.json"
    for p, d in ((bp, base), (hp, head)):
        if d is None:
            continue
        if isinstance(d, str):          # raw bytes on disk: truncated, junk...
            p.write_text(d)
        else:
            p.write_text(json.dumps(d))
    argv = [sys.executable, str(VERDICT), str(bp), str(hp)]
    if critical is not None:
        cp = tmp_path / "critical.txt"
        cp.write_text("\n".join(critical))
        argv += ["--critical", str(cp)]
    r = subprocess.run(argv, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# ═════════════════════════════════════════════════════════════════════
# THE FIVE REPRODUCED GAPS
# ═════════════════════════════════════════════════════════════════════

def test_A_an_empty_head_artifact_is_invalid_not_clean(tmp_path):
    """GAP A. An empty head log compared as NO NEW FAILURE IDENTITY, exit 0."""
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}), "")
    assert rc == RC_INVALID, out
    assert "INVALID" in out
    assert "not acceptance evidence" in out


def test_A2_a_missing_head_artifact_is_invalid(tmp_path):
    bp = tmp_path / "base.json"
    bp.write_text(json.dumps(_report(nodes={"tests/a.py::t": "failed"})))
    r = subprocess.run(
        [sys.executable, str(VERDICT), str(bp), str(tmp_path / "nope.json")],
        capture_output=True, text=True)
    assert r.returncode == RC_INVALID
    assert "does not exist" in r.stdout


def test_A3_a_truncated_artifact_is_invalid(tmp_path):
    """A half-written report must not be read as a short but complete run."""
    good = _report(nodes={"tests/a.py::t": "failed"})
    rc, out = _run(tmp_path, good, json.dumps(good)[:120])
    assert rc == RC_INVALID, out
    assert "not valid JSON" in out or "truncated" in out


def test_B_a_collection_error_is_invalid(tmp_path):
    """GAP B. pytest exits 2 and runs nothing; the old tool compared it as 0.

    A collection failure has NO `::` -- it names a module that could not be
    imported -- so an identity comparison literally cannot see it. It has to be
    caught as a validity failure instead.
    """
    head = _report(
        nodes={}, collected=[], exitstatus=2,
        collect_errors=[{"nodeid": "tests/broken.py", "kind": "COLLECT_ERROR",
                         "longrepr": "ImportError: no module named x"}])
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}), head)
    assert rc == RC_INVALID, out
    assert "collection error" in out
    assert "tests/broken.py" in out


def test_C_a_manifest_that_does_not_reconcile_is_invalid(tmp_path):
    """GAP C. The old tool printed 'summary: 5 failed / node ids: 1' and
    accepted it.

    The fix is not comparing a count against a set length -- one test has three
    phases and can appear once while producing several outcomes. It is that every
    COLLECTED test must have produced a phase record unless it was deselected.
    Here two collected tests never ran.
    """
    head = _report(nodes={"tests/a.py::t": "failed"},
                   collected=["tests/a.py::t", "tests/a.py::u", "tests/a.py::v"])
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}), head)
    assert rc == RC_INVALID, out
    assert "produced no phase record" in out


def test_D_parameterised_ids_with_spaces_are_distinct_identities(tmp_path):
    """GAP D, AND IT IS THE WORST OF THE FIVE. The regex stopped at whitespace,
    so these two collapsed to `tests/test_param.py::test_case[case` and a run
    failing only B compared against one failing only A returned ZERO new
    identities -- a real regression, invisible."""
    a = "tests/test_param.py::test_case[case A]"
    b = "tests/test_param.py::test_case[case B]"
    rc, out = _run(tmp_path, _report(nodes={a: "failed"}),
                   _report(nodes={b: "failed"}))
    assert rc == RC_NEW_FAILURES, out
    assert b in out
    assert "NEW FAILURES (in HEAD, not in the baseline): 1" in out


def test_E_the_runner_preserves_pytest_exit_status(tmp_path):
    """GAP E. The runner ended on echo/tail, so exits 1-5 all became 0.

    Driven through the real script with a stub `python` on PATH that exits with
    a chosen code, so what is tested is the script's status handling rather than
    a copy of its logic.
    """
    runner = TOOLS / "run_gate.sh"
    assert runner.exists()
    co = tmp_path / "co"
    (co / "backend" / "tools").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(co)], check=True)
    (co / "seed").write_text("x")
    subprocess.run(["git", "-C", str(co), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(co), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "seed"], check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for rc in (1, 2, 3, 4, 5):
        (bindir / "python").write_text(
            "#!/bin/bash\necho 'simulated pytest'\nexit %d\n" % rc)
        (bindir / "python").chmod(0o755)
        env = {"PATH": "%s:/usr/bin:/bin" % bindir, "HOME": str(tmp_path)}
        r = subprocess.run(
            ["bash", str(runner), str(co), "nodb", str(tmp_path / ("p%d" % rc))],
            capture_output=True, text=True, env=env)
        assert r.returncode == rc, (
            "pytest exit %d became runner exit %d -- the status is being "
            "discarded again: %s" % (rc, r.returncode, r.stdout + r.stderr))


# ═════════════════════════════════════════════════════════════════════
# THE REST OF THE REQUIRED REGRESSIONS
# ═════════════════════════════════════════════════════════════════════

def test_an_incomplete_session_is_invalid(tmp_path):
    """No completion record means the node list is whatever had run when it
    stopped. That is not a short clean run."""
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}),
                   _report(nodes={"tests/a.py::t": "failed"}, complete=False))
    assert rc == RC_INVALID, out
    assert "never finished" in out


def test_an_interrupted_run_is_invalid(tmp_path):
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}),
                   _report(nodes={"tests/a.py::t": "failed"},
                           interrupted="KEYBOARD_INTERRUPT"))
    assert rc == RC_INVALID, out
    assert "interrupted" in out


def test_no_tests_collected_is_invalid(tmp_path):
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}),
                   _report(nodes={}, collected=[], exitstatus=5))
    assert rc == RC_INVALID, out
    assert "nothing was collected" in out or "exited 5" in out


def test_a_setup_failure_is_a_failure_identity(tmp_path):
    """A test that fails in SETUP never ran its body. It is still a failure and
    the phase that decided it is recorded, because 'the assertion passed' and
    'the test passed' are different statements."""
    head = _report(nodes={"tests/a.py::t": {
        "outcome": "failed", "decided_by": "setup",
        "phases": {"setup": "failed"}, "longrepr": "fixture blew up"}})
    # The baseline must itself be a VALID run -- an empty one is refused, and
    # correctly: that is the same check as gap A.
    base = _report(nodes={"tests/a.py::t": "passed"}, exitstatus=0)
    rc, out = _run(tmp_path, base, head)
    assert rc == RC_NEW_FAILURES, out
    assert "[failed in setup]" in out


def test_a_teardown_failure_is_a_failure_identity(tmp_path):
    """The console shows one line for a test whose call passed and whose
    teardown failed. Both phases are recorded and the teardown decides."""
    head = _report(nodes={"tests/a.py::t": {
        "outcome": "failed", "decided_by": "teardown",
        "phases": {"call": "passed", "teardown": "failed"},
        "longrepr": "connection left open"}})
    base = _report(nodes={"tests/a.py::t": "passed"}, exitstatus=0)
    rc, out = _run(tmp_path, base, head)
    assert rc == RC_NEW_FAILURES, out
    assert "[failed in teardown]" in out


def test_captured_error_logs_are_not_failures(tmp_path):
    """The defect that started this: with --tb=short pytest prints captured logs
    beginning with `ERROR    sportsassets...`, and the console parser counted
    three of them as new failing node ids. A structured report has no route for
    that at all -- a log line is not a node."""
    clean = _report(nodes={"tests/a.py::t": "failed"})
    rc, out = _run(tmp_path, clean, clean)
    assert rc == RC_ACCEPTED, out
    assert "ACCEPTED" in out


def test_one_failure_disappearing_while_another_appears_is_not_clean(tmp_path):
    """The comparison a count cannot make: same total, different identities."""
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}),
                   _report(nodes={"tests/a.py::u": "failed"}))
    assert rc == RC_NEW_FAILURES, out
    assert "tests/a.py::u" in out
    assert "NO LONGER FAILING (in the baseline, not in HEAD): 1" in out


def test_a_skipped_critical_test_is_invalid(tmp_path):
    """A skipped proof is not a passed proof."""
    head = _report(nodes={"tests/a.py::t": "failed",
                          "tests/crit.py::proof": "skipped"})
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}), head,
                   critical=["tests/crit.py::proof"])
    assert rc == RC_INVALID, out
    assert "SKIPPED" in out


def test_a_deselected_critical_test_is_invalid(tmp_path):
    head = _report(nodes={"tests/a.py::t": "failed"},
                   collected=["tests/a.py::t"],
                   deselected=["tests/crit.py::proof"])
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}), head,
                   critical=["tests/crit.py::proof"])
    assert rc == RC_INVALID, out
    assert "DESELECTED" in out


def test_an_uncollected_critical_test_is_invalid(tmp_path):
    head = _report(nodes={"tests/a.py::t": "failed"})
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}), head,
                   critical=["tests/crit.py::proof"])
    assert rc == RC_INVALID, out
    assert "not COLLECTED" in out


def test_a_critical_test_failing_in_both_runs_is_not_accepted(tmp_path):
    """A capital-critical proof that is red on every commit is not excused by
    the matched baseline: the critical list must PASS, not merely run."""
    head = _report(nodes={"tests/a.py::t": "failed",
                          "tests/crit.py::proof": "failed"})
    base = _report(nodes={"tests/a.py::t": "failed",
                          "tests/crit.py::proof": "failed"})
    rc, out = _run(tmp_path, base, head, critical=["tests/crit.py::proof"])
    assert rc == RC_NEW_FAILURES, out
    assert "critical acceptance test(s) FAILED" in out
    assert "tests/crit.py::proof" in out
    # AND WITHOUT THE CRITICAL LIST the same pair is a matched baseline
    rc, out = _run(tmp_path, base, head)
    assert rc == RC_ACCEPTED, out


def test_a_critical_file_means_every_test_it_collects(tmp_path):
    """A file-level entry covers each of its tests, and a listed file that
    collected nothing did not run."""
    base = _report(nodes={"tests/crit.py::a": "passed",
                          "tests/crit.py::b": "passed"}, exitstatus=0)
    head = _report(nodes={"tests/crit.py::a": "passed",
                          "tests/crit.py::b": "skipped"}, exitstatus=0)
    rc, out = _run(tmp_path, base, head, critical=["tests/crit.py"])
    assert rc == RC_INVALID, out
    assert "tests/crit.py::b was SKIPPED" in out
    head = _report(nodes={"tests/crit.py::a": "passed",
                          "tests/crit.py::b": "failed"})
    rc, out = _run(tmp_path, base, head, critical=["tests/crit.py"])
    assert rc == RC_NEW_FAILURES, out
    rc, out = _run(tmp_path, base, base, critical=["tests/gone.py"])
    assert rc == RC_INVALID, out
    assert "tests/gone.py collected no test at all" in out
    rc, out = _run(tmp_path, base, base, critical=["tests/crit.py"])
    assert rc == RC_ACCEPTED, out


def test_a_present_and_passing_critical_test_is_accepted(tmp_path):
    """The control: the critical check must not refuse everything."""
    head = _report(nodes={"tests/a.py::t": "failed",
                          "tests/crit.py::proof": "passed"})
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed",
                                            "tests/crit.py::proof": "passed"}),
                   head, critical=["tests/crit.py::proof"])
    assert rc == RC_ACCEPTED, out


def test_an_xfail_is_not_a_new_failure_and_an_xpass_is_reported(tmp_path):
    """An expected failure is a recorded expectation, not a regression -- and
    promoting it would make every xfail permanently 'new'. An UNEXPECTED pass is
    worth reading and is printed, but it is not a regression either."""
    head = _report(nodes={"tests/a.py::t": "failed",
                          "tests/a.py::x": "xfailed",
                          "tests/a.py::y": "xpassed"})
    base = _report(nodes={"tests/a.py::t": "failed",
                          "tests/a.py::x": "xfailed",
                          "tests/a.py::y": "xfailed"})
    rc, out = _run(tmp_path, base, head)
    assert rc == RC_ACCEPTED, out
    assert "XPASSED in HEAD: 1" in out


def test_legitimate_baseline_failures_are_accepted_and_not_called_green(
        tmp_path):
    """The real case: 367 pre-existing failures, none new. It is ACCEPTED, and
    the verdict says in the same breath that it is not 'all tests green'."""
    nodes = {"tests/f%d.py::t" % i: "failed" for i in range(367)}
    nodes["tests/ok.py::t"] = "passed"
    rc, out = _run(tmp_path, _report(nodes=nodes), _report(nodes=nodes))
    assert rc == RC_ACCEPTED, out
    assert "NOT 'all tests green'" in out
    assert "367 baseline failures remain" in out


def test_exit_zero_with_recorded_failures_is_invalid(tmp_path):
    """The report and pytest must agree about what happened."""
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}),
                   _report(nodes={"tests/a.py::t": "failed"}, exitstatus=0))
    assert rc == RC_INVALID, out
    assert "exited 0 but the report records" in out


def test_exit_one_with_no_recorded_failure_is_invalid(tmp_path):
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "passed"},
                                     exitstatus=0),
                   _report(nodes={"tests/a.py::t": "passed"}, exitstatus=1))
    assert rc == RC_INVALID, out
    assert "records no failing node" in out


def test_a_wrong_schema_is_invalid(tmp_path):
    rc, out = _run(tmp_path, _report(nodes={"tests/a.py::t": "failed"}),
                   json.dumps({"schema": "SOMETHING_ELSE"}))
    assert rc == RC_INVALID, out
    assert "GATE_REPORT_V1" in out


# ═════════════════════════════════════════════════════════════════════
# AND THE REPORTER ITSELF, AGAINST A REAL PYTEST RUN
# ═════════════════════════════════════════════════════════════════════

def test_the_reporter_records_a_real_run_including_params_and_collect_errors(
        tmp_path):
    """The plugin is exercised by running pytest on generated files, so what is
    asserted is what pytest's hooks actually produce rather than my model of
    them."""
    (tmp_path / "test_gen_ok.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('c', ['case A', 'case B'])\n"
        "def test_case(c):\n"
        "    assert c == 'case A'\n"
        "def test_plain():\n"
        "    pass\n"
        "@pytest.mark.skip('because')\n"
        "def test_skipped():\n"
        "    pass\n"
        "@pytest.mark.xfail(reason='known')\n"
        "def test_xf():\n"
        "    assert False\n")
    rep = tmp_path / "r.json"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:randomly",
         "-p", "tools.gate_report", "--tb=no",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True,
        cwd=str(TOOLS.parent),
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "GATE_REPORT_PATH": str(rep),
             "PYTHONPATH": str(TOOLS.parent),
             "HOME": str(tmp_path)})
    assert rep.exists(), r.stdout + r.stderr
    doc = json.loads(rep.read_text())
    assert doc["schema"] == "GATE_REPORT_V1"
    assert doc["session_complete"] is True
    assert doc["exitstatus"] == 1            # one real failure
    nodes = doc["nodes"]
    # THE TWO PARAMETERISED IDS SURVIVE INTACT, SPACES AND ALL.
    ids = [n for n in nodes if "test_case" in n]
    assert len(ids) == 2, ids
    assert any("case A" in i for i in ids) and any("case B" in i for i in ids)
    failed = [n for n, v in nodes.items() if v["outcome"] == "failed"]
    assert len(failed) == 1 and "case B" in failed[0], failed
    # SKIP AND XFAIL ARE KEPT DISTINCT FROM PASS.
    outs = {v["outcome"] for v in nodes.values()}
    assert "skipped" in outs and "xfailed" in outs and "passed" in outs


def test_the_reporter_records_a_collection_error_with_no_double_colon(tmp_path):
    (tmp_path / "test_gen_broken.py").write_text(
        "import a_module_that_does_not_exist_anywhere\n")
    rep = tmp_path / "r.json"
    subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "-q",
         "-p", "no:randomly", "-p", "tools.gate_report", "--tb=no",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=str(TOOLS.parent),
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "GATE_REPORT_PATH": str(rep),
             "PYTHONPATH": str(TOOLS.parent),
             "HOME": str(tmp_path)})
    assert rep.exists()
    doc = json.loads(rep.read_text())
    assert doc["collect_errors"], doc
    bad = doc["collect_errors"][0]["nodeid"]
    assert "::" not in bad, (
        "a collection error names a module, not a test -- which is exactly why "
        "an identity comparison requiring `::` cannot see it")
    assert doc["exitstatus"] == 2
