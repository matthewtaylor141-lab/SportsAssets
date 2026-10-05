"""THE CAPITAL-CRITICAL CI VERDICT (tools/ci_verdict.py) AND ITS WORKFLOW.

Owner, program section 21: "Unexpected failures = REJECT"; quarantine entries
must be machine-readable with test, owner, reason, date, expiry, issue. Every
REJECT path the tool names is driven here from a real junit document, and the
one ACCEPT path only when nothing is wrong. The workflow is pinned to the
production image's environment and to the exact pushed SHA.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND / "tools"))
import ci_verdict as V  # noqa: E402

TODAY = "2026-10-04"
SHA = "a" * 40
ISSUE = "https://github.com/matthewtaylor141-lab/sportsassets/issues/1"


def _case(file, name, classes=(), kind=None, skip_type="pytest.skip"):
    mod = file[:-3].replace("/", ".")
    classname = ".".join([mod, *classes])
    inner = ""
    if kind == "failure":
        inner = '<failure message="boom">trace</failure>'
    elif kind == "error":
        inner = '<error message="setup failed">trace</error>'
    elif kind == "skipped":
        inner = '<skipped type="%s" message="skip"/>' % skip_type
    elif kind == "failure+error":
        inner = '<failure message="boom"/><error message="teardown"/>'
    return ('<testcase classname="%s" name="%s" file="%s" line="1" time="0.1">%s</testcase>'
            % (classname, name, file, inner))


def _junit(tmp_path, cases):
    p = tmp_path / "junit.xml"
    p.write_text('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
                 + "".join(cases) + "</testsuite></testsuites>")
    return str(p)


def _entry(node, **over):
    e = {"test": node, "owner": "R30 engineering",
         "reason": "fails on the venue fixture that the next stream rebuilds",
         "date": "2026-10-04", "expiry": "2026-10-18", "issue": ISSUE}
    e.update(over)
    return e


def _files(tmp_path, quarantine=(), critical=("tests/test_crit.py",)):
    q = tmp_path / "quarantine.json"
    q.write_text(json.dumps(list(quarantine)))
    c = tmp_path / "critical.txt"
    c.write_text("# the capital-critical list\n" + "\n".join(critical) + "\n")
    return str(q), str(c)


def _run(tmp_path, cases, quarantine=(), critical=("tests/test_crit.py",), today=TODAY):
    junit = _junit(tmp_path, cases)
    q, c = _files(tmp_path, quarantine, critical)
    out = tmp_path / "verdict.json"
    rc = V.main(["--junit", junit, "--quarantine", q, "--critical", c,
                 "--sha", SHA, "--out", str(out), "--today", today])
    return rc, json.loads(out.read_text())


GREEN = [_case("tests/test_crit.py", "test_a"), _case("tests/test_other.py", "test_b"),
         _case("tests/test_other.py", "test_c[x-1]", classes=("TestK",))]


# ── ACCEPT: only when nothing is wrong ──────────────────────────────

def test_a_clean_run_is_accepted_with_counts_and_the_sha(tmp_path):
    rc, v = _run(tmp_path, GREEN)
    assert rc == 0 and v["verdict"] == "ACCEPT" and v["reasons"] == []
    assert v["schema"] == "CI_VERDICT_V1" and v["sha"] == SHA
    assert v["counts"]["tests"] == 3 and v["counts"]["passed"] == 3
    assert v["critical"]["tests"] == 1 and v["critical"]["violations"] == []
    for key in ("sha", "counts", "unexpected", "quarantined", "expired", "critical"):
        assert key in v


def test_a_quarantined_failure_is_accepted_and_named(tmp_path):
    t = "tests/test_other.py::test_b"
    cases = [GREEN[0], _case("tests/test_other.py", "test_b", kind="failure")]
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t)])
    assert rc == 0 and v["verdict"] == "ACCEPT"
    assert v["quarantined"] == [{"test": t, "outcome": "failed", "owner": "R30 engineering",
                                 "expiry": "2026-10-18", "issue": ISSUE}]
    assert v["unexpected"] == []


# ── REJECT: unexpected failures ─────────────────────────────────────

@pytest.mark.parametrize("kind", ["failure", "error", "failure+error"])
def test_an_unquarantined_failure_or_error_rejects(tmp_path, kind):
    cases = GREEN + [_case("tests/test_other.py", "test_d", kind=kind)]
    rc, v = _run(tmp_path, cases)
    assert rc == 1 and v["verdict"] == "REJECT"
    assert v["unexpected"] == ["tests/test_other.py::test_d"]
    assert any(r.startswith("UNEXPECTED") for r in v["reasons"])


def test_a_collection_error_is_an_unexpected_error(tmp_path):
    coll = ('<testcase classname="" name="tests.test_broken" time="0">'
            '<error message="collection failure">ImportError</error></testcase>')
    rc, v = _run(tmp_path, GREEN + [coll])
    assert rc == 1 and v["unexpected"] == ["tests/test_broken.py"]


def test_zero_tests_collected_rejects(tmp_path):
    rc, v = _run(tmp_path, [], critical=())
    assert rc == 1 and v["counts"]["tests"] == 0
    assert any(r.startswith("NOTHING_COLLECTED") for r in v["reasons"])


def test_a_missing_or_unreadable_report_rejects(tmp_path):
    q, c = _files(tmp_path)
    out = tmp_path / "v.json"
    rc = V.main(["--junit", str(tmp_path / "absent.xml"), "--quarantine", q, "--critical", c,
                 "--sha", SHA, "--out", str(out), "--today", TODAY])
    assert rc == 1 and json.loads(out.read_text())["reasons"][0].startswith("INVALID_REPORT")
    bad = tmp_path / "half.xml"
    bad.write_text("<testsuites><testsuite><testcase")
    rc = V.main(["--junit", str(bad), "--quarantine", q, "--critical", c,
                 "--sha", SHA, "--out", str(out), "--today", TODAY])
    assert rc == 1


def test_a_usage_error_is_never_an_accept(tmp_path):
    assert V.main(["--junit", "x"]) == 2
    q, c = _files(tmp_path)
    assert V.main(["--junit", _junit(tmp_path, GREEN), "--quarantine", q, "--critical", c,
                   "--sha", SHA, "--today", "04/10/2026"]) == 2


# ── REJECT: the quarantine itself ───────────────────────────────────

def test_an_expired_entry_rejects_even_though_the_test_fails(tmp_path):
    t = "tests/test_other.py::test_b"
    cases = [GREEN[0], _case("tests/test_other.py", "test_b", kind="failure")]
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t, date="2026-09-20", expiry="2026-10-03")])
    assert rc == 1 and v["expired"] == [{"test": t, "expiry": "2026-10-03", "issue": ISSUE}]
    # and the failure it covered is unexpected again
    assert v["unexpected"] == [t]
    # the expiry day itself is still valid
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t, date="2026-09-20", expiry="2026-10-04")])
    assert rc == 0, v["reasons"]


@pytest.mark.parametrize("over,needle", [
    ({"owner": ""}, "owner"),
    ({"reason": "flaky"}, "reason"),
    ({"date": "04/10/2026"}, "date is not an ISO date"),
    ({"expiry": "2026-13-01"}, "expiry is not an ISO date"),
    ({"expiry": "2026-10-19"}, "more than 14 days"),
    ({"date": "2026-10-05", "expiry": "2026-10-06"}, "in the future"),
    ({"expiry": "2026-10-01"}, "before date"),
    ({"issue": "https://example.com/1"}, "issue"),
    ({"issue": "https://github.com/o/r/pull/3"}, "issue"),
    ({"test": "test_b"}, "node id"),
    ({"extra": 1}, "unknown field"),
])
def test_a_malformed_entry_rejects(tmp_path, over, needle):
    t = "tests/test_other.py::test_b"
    cases = [GREEN[0], _case("tests/test_other.py", "test_b", kind="failure")]
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t, **over)])
    assert rc == 1 and v["malformed"], v
    assert any(needle in p for p in v["malformed"][0]["problems"]), v["malformed"]


def test_a_missing_field_a_duplicate_and_a_non_list_reject(tmp_path):
    t = "tests/test_other.py::test_b"
    cases = [GREEN[0], _case("tests/test_other.py", "test_b", kind="failure")]
    e = _entry(t)
    del e["issue"]
    rc, v = _run(tmp_path, cases, quarantine=[e])
    assert rc == 1 and "missing field(s) issue" in v["malformed"][0]["problems"][0]
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t), _entry(t)])
    assert rc == 1 and "duplicate" in v["malformed"][0]["problems"][0]
    junit = _junit(tmp_path, cases)
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"test": t}))
    _q, c = _files(tmp_path)
    assert V.main(["--junit", junit, "--quarantine", str(q), "--critical", c,
                   "--sha", SHA, "--today", TODAY]) == 1


def test_a_quarantined_test_that_now_passes_rejects_so_the_list_shrinks(tmp_path):
    t = "tests/test_other.py::test_b"
    rc, v = _run(tmp_path, GREEN, quarantine=[_entry(t)])
    assert rc == 1 and v["stale"] == [{"test": t, "why": "NOW_PASSED"}]


def test_a_quarantine_entry_for_a_test_that_did_not_run_rejects(tmp_path):
    rc, v = _run(tmp_path, GREEN, quarantine=[_entry("tests/test_gone.py::test_x")])
    assert rc == 1 and v["stale"][0]["why"] == "MATCHES_NO_TEST_IN_THIS_RUN"


def test_a_quarantined_test_that_was_skipped_rejects(tmp_path):
    t = "tests/test_other.py::test_b"
    cases = [GREEN[0], _case("tests/test_other.py", "test_b", kind="skipped")]
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t)])
    assert rc == 1 and v["stale"] == [{"test": t, "why": "NOW_SKIPPED"}]


# ── REJECT: the capital-critical tests ──────────────────────────────

@pytest.mark.parametrize("kind,skip_type,outcome", [
    ("failure", None, "failed"), ("error", None, "error"),
    ("skipped", "pytest.skip", "skipped"), ("skipped", "pytest.xfail", "xfailed")])
def test_a_critical_test_must_run_and_pass(tmp_path, kind, skip_type, outcome):
    cases = [_case("tests/test_crit.py", "test_a", kind=kind, skip_type=skip_type or "pytest.skip"),
             GREEN[1]]
    rc, v = _run(tmp_path, cases)
    assert rc == 1
    assert {"test": "tests/test_crit.py::test_a", "outcome": outcome,
            "why": "A_CAPITAL_CRITICAL_TEST_MUST_PASS"} in v["critical"]["violations"]


def test_a_critical_test_can_never_be_quarantined_even_with_a_valid_entry(tmp_path):
    t = "tests/test_crit.py::test_a"
    cases = [_case("tests/test_crit.py", "test_a", kind="failure"), GREEN[1]]
    rc, v = _run(tmp_path, cases, quarantine=[_entry(t)])
    assert rc == 1
    whys = {x["why"] for x in v["critical"]["violations"]}
    assert "A_CAPITAL_CRITICAL_TEST_CAN_NEVER_BE_QUARANTINED" in whys
    # an entry naming a critical test rejects even when that test passes
    rc, v = _run(tmp_path, GREEN, quarantine=[_entry(t)])
    assert rc == 1 and any(x["outcome"] == "quarantined" for x in v["critical"]["violations"])


def test_a_critical_file_that_collected_nothing_rejects(tmp_path):
    rc, v = _run(tmp_path, GREEN, critical=("tests/test_crit.py", "tests/test_missing.py"))
    assert rc == 1 and v["critical"]["empty_files"] == ["tests/test_missing.py"]


def test_a_critical_node_id_entry_matches_its_parametrisations(tmp_path):
    cases = [_case("tests/test_other.py", "test_c[x-1]", classes=("TestK",), kind="failure"),
             _case("tests/test_other.py", "test_c[x-2]", classes=("TestK",))]
    rc, v = _run(tmp_path, cases, critical=("tests/test_other.py::TestK::test_c",))
    assert rc == 1 and v["critical"]["tests"] == 2
    assert v["critical"]["violations"][0]["test"] == "tests/test_other.py::TestK::test_c[x-1]"


# ── node ids ────────────────────────────────────────────────────────

def test_node_ids_round_trip_through_junit_with_and_without_the_file_attribute(tmp_path):
    import xml.etree.ElementTree as ET
    c = ET.fromstring(_case("tests/test_other.py", "test_c[a::b c]", classes=("TestK", "TestIn")))
    assert V.testcase_node_id(c, None) == (
        "tests/test_other.py::TestK::TestIn::test_c[a::b c]", "tests/test_other.py")
    del c.attrib["file"]
    assert V.testcase_node_id(c, None)[0] == "tests/test_other.py::TestK::TestIn::test_c[a::b c]"
    # with a root, the longest existing module wins
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text("")
    assert V.testcase_node_id(c, str(tmp_path))[1] == "tests/test_other.py"
    assert V.split_node_id("tests/t.py::C::test_x[a::b]") == ("tests/t.py", ["C"], "test_x[a::b]")


# ── the tool and the repository's own files ─────────────────────────

def test_the_tool_imports_only_the_standard_library():
    tree = ast.parse((BACKEND / "tools" / "ci_verdict.py").read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
    mods.discard("__future__")
    assert mods and all(m in sys.stdlib_module_names for m in mods), mods


def test_the_repository_quarantine_is_well_formed_and_names_no_critical_test():
    entries = json.loads((BACKEND / "tests" / "quarantine.json").read_text())
    assert isinstance(entries, list)
    critical = V.read_critical(str(BACKEND / "tools" / "capital_critical_tests.txt"))
    import datetime as dt
    for e in entries:
        # well-formed as of the day it was written; expiry is the verdict's job
        assert V.validate_entry(e, V._iso_date(e.get("date")) or dt.date.max) == [], e
        path = V.split_node_id(e["test"])[0]
        assert V._critical_match(e["test"], path, critical) is None, e
        assert (BACKEND / path).is_file(), e


def test_the_critical_list_names_files_that_exist_and_carries_this_file():
    critical = V.read_critical(str(BACKEND / "tools" / "capital_critical_tests.txt"))
    assert len(critical) > 300
    for c in critical:
        assert (BACKEND / V.split_node_id(c)[0]).is_file(), c
    assert "tests/test_ci_verdict.py" in critical


# ── the workflow ────────────────────────────────────────────────────

def _wf(name):
    doc = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text())
    return doc, doc.get("on", doc.get(True))


def test_the_workflow_runs_on_the_exact_sha_in_the_images_environment():
    doc, on = _wf("capital-critical.yml")
    assert "workflow_dispatch" in on
    branches = on["push"]["branches"]
    for b in ("claude/release-api", "claude/r30-live-parity", "claude/r30a-*",
              "claude/r30b-*", "claude/r30c-*", "claude/r30d-*"):
        assert b in branches, b
    assert set(on["push"]["paths"]) >= {"backend/**", "tools/**",
                                        ".github/workflows/capital-critical.yml"}
    job = doc["jobs"]["suite"]
    assert job["services"]["postgres"]["image"] == "postgres:16"
    steps = job["steps"]
    co = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
    assert co["with"]["ref"] == "${{ github.sha }}" and co["with"]["fetch-depth"] == 0
    py = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    from sportsassets import runtime_manifest as RM
    assert py["with"]["python-version"] == RM.EXPECTED_PYTHON == "3.12.3"
    run = "\n".join(s.get("run", "") for s in steps)
    assert 'git rev-parse HEAD' in run and '"$GITHUB_SHA"' in run
    assert "-r requirements.lock -r requirements-test.lock" in run and "pip check" in run
    assert "RM.check()" in run
    assert "python -m sportsassets.scripts.migrate" in run
    assert "python -m pytest tests " in run and "--junitxml=" in run
    assert "-k " not in run and "--deselect" not in run and "--ignore" not in run
    suite = next(s for s in steps if s.get("name") == "Run the whole suite")
    assert suite["env"]["RN1X_TEST_DSN"] == "${{ env.CC_DSN }}"
    verdict = next(s for s in steps if "verdict" in s.get("name", "").lower()
                   and "run" in s)
    assert "tools/ci_verdict.py" in verdict["run"] and "tests/quarantine.json" in verdict["run"]
    assert "tools/capital_critical_tests.txt" in verdict["run"] and '--sha "$GITHUB_SHA"' in verdict["run"]
    up = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
    assert up["if"] == "always()" and "capital-critical-verdict.json" in up["with"]["path"]


def test_the_test_lock_moves_no_runtime_version():
    from sportsassets import runtime_manifest as RM
    lock = RM.read_lock(BACKEND / "requirements.lock")
    test_lock = RM.read_lock(BACKEND / "requirements-test.lock")
    assert test_lock and not set(test_lock) & set(lock)
    assert all(v and v[0].isdigit() for v in test_lock.values())


def test_backend_tests_stands_on_the_same_ground():
    doc, on = _wf("backend-tests.yml")
    assert on["push"]["paths"] == ["backend/**", ".github/workflows/backend-tests.yml"]
    job = doc["jobs"]["pytest"]
    assert job["services"]["postgres"]["image"] == "postgres:16"
    steps = job["steps"]
    py = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python"))
    assert py["with"]["python-version"] == "3.12.3"
    run = "\n".join(s.get("run", "") for s in steps)
    assert "-r requirements.lock -r requirements-test.lock" in run
    assert 'pip install --quiet -e ".[dev]"' not in run
    assert "python -m sportsassets.scripts.migrate" in run
    suite = next(s for s in steps if s.get("name") == "Run the suite")
    assert "RN1X_TEST_DSN" in suite["env"] and "exit $rc" in suite["run"]
