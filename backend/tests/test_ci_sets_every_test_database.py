"""Every database a test suite needs is given to it by the required gates.

backend-tests and capital-critical give the suite a real Postgres through
RN1X_TEST_DSN, but four suites (bettor_live_store, bettor_canary,
bettor_incentive_release, bettor_socket_allowance: 44 tests) read their
database from BETTOR_TEST_PG_DSN, which neither workflow set. They skipped
"BETTOR_TEST_PG_DSN names no server" in every gate through release 732cc0c6
(backend-tests 37875925931 and capital-critical 37875926135: 49 skipped, 44
of them these), so the required gates reported them as run-and-skipped
rather than run. Against a real Postgres all 166 tests in those files pass.

Pinned here: every environment variable that a test module reads WITHOUT a
default and uses to decide whether it can run (a skipif over a DSN) is set
in the suite step of BOTH required gates, to the gate's own database.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "backend" / "tests"
GATES = {
    "backend-tests": (ROOT / ".github" / "workflows" / "backend-tests.yml", "BT_DSN"),
    "capital-critical": (ROOT / ".github" / "workflows" / "capital-critical.yml", "CC_DSN"),
}
# os.environ.get("X_DSN") with no default, or os.environ["X_DSN"]
NO_DEFAULT = re.compile(r"""os\.environ(?:\.get\(\s*["']([A-Z0-9_]*DSN)["']\s*\)|\[["']([A-Z0-9_]*DSN)["']\])""")
# variables a test sets for itself before reading (not a gate's to give)
SELF_SET = {"BOOT_DSN"}


def _no_default_dsn_vars() -> set[str]:
    found: set[str] = set()
    for f in TESTS.rglob("*.py"):
        if f.resolve() == Path(__file__).resolve():
            continue
        for a, b in NO_DEFAULT.findall(f.read_text(encoding="utf-8")):
            found.add(a or b)
    return found - SELF_SET


def _suite_env(wf: Path) -> str:
    text = wf.read_text(encoding="utf-8")
    m = re.search(r"\n      - name: Run the (?:whole )?suite\n(.*?)\n        run: \|", text, re.S)
    assert m, wf
    return m.group(1)


def test_the_bettor_suites_get_their_database_in_both_gates():
    for name, (wf, dsn) in GATES.items():
        env = _suite_env(wf)
        assert "BETTOR_TEST_PG_DSN: ${{ env.%s }}" % dsn in env, name
        assert "RN1X_TEST_DSN: ${{ env.%s }}" % dsn in env, name


def test_every_no_default_test_database_variable_is_set_by_both_gates():
    needed = _no_default_dsn_vars()
    assert {"RN1X_TEST_DSN", "BETTOR_TEST_PG_DSN"} <= needed, needed
    for name, (wf, dsn) in GATES.items():
        env = _suite_env(wf)
        missing = sorted(v for v in needed if "%s: ${{ env.%s }}" % (v, dsn) not in env)
        assert not missing, (name, missing)


#: a module-level DSN taken from DATABASE_URL alone (no RN1X_TEST_DSN):
#: neither required gate sets DATABASE_URL in its suite step (only for the
#: migration runner), so such a module skipped in every gate. capital-
#: critical 37927450586 on 5d83e0de: test_rc6_api_responsive_offloop::
#: test_the_registry_read_leaves_the_stored_records_behind SKIPPED, a
#: capital-critical violation; the healthz and harness modules likewise.
DATABASE_URL_ONLY = re.compile(
    r"""^\w*DSN\w*\s*=\s*\(?\s*os\.environ\.get\(\s*["']DATABASE_URL["']""",
    re.M)


def test_no_test_module_takes_its_database_from_database_url_alone():
    bad = []
    for f in TESTS.rglob("*.py"):
        if f.resolve() == Path(__file__).resolve():
            continue
        for m in DATABASE_URL_ONLY.finditer(f.read_text(encoding="utf-8")):
            line = m.string[m.start():m.string.find("\n", m.start())]
            if "RN1X_TEST_DSN" not in line:
                bad.append("%s: %s" % (f.name, line.strip()))
    assert not bad, bad
