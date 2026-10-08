"""numpy IS A TEST TOOL, NEVER A RUNTIME DEPENDENCY (red-team closeout, DEFECT 4).

The revenue-reliability parity proof runs the vendored package as its oracle,
and the package imports numpy. numpy is therefore pinned in
requirements-test.lock, which CI installs beside requirements.lock -- and only
there: the image installs requirements.lock alone, and no production module
may import numpy (the production port exists precisely because the image has
none). These pins keep both halves true, and keep a skipped proof from ever
being reported as a passed one."""
from __future__ import annotations

import ast
import tomllib
import warnings
from pathlib import Path

from sportsassets import runtime_manifest as RM

BACKEND = Path(__file__).resolve().parents[1]
TEST_ONLY = ("numpy",)


def _parse(p: Path) -> ast.AST:
    with warnings.catch_warnings():
        # two production docstrings carry an invalid escape sequence (SyntaxWarning); not this pin's business
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(p.read_text(), str(p))


def test_numpy_is_pinned_exactly_in_the_test_lock_and_absent_from_the_runtime_lock():
    test_lock = RM.read_lock(BACKEND / "requirements-test.lock")
    lock = RM.read_lock(BACKEND / "requirements.lock")
    for name in TEST_ONLY:
        v = test_lock.get(name)
        assert v and v[0].isdigit() and all(p.isdigit() for p in v.split(".")), (name, v)
        assert name not in lock, name
    deps = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["dependencies"]
    assert not [d for d in deps if d.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip().lower()
                in TEST_ONLY]


def test_the_image_installs_the_runtime_lock_only():
    src = (BACKEND / "Dockerfile").read_text()
    assert "requirements.lock" in src
    assert "requirements-test.lock" not in src


def test_no_production_module_imports_numpy():
    bad = []
    for p in sorted((BACKEND / "sportsassets").rglob("*.py")):
        tree = _parse(p)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)
                  and getattr(node.func, "attr", getattr(node.func, "id", "")) in ("import_module", "__import__")):
                names = [node.args[0].value]
            for n in names:
                if n.split(".")[0] in TEST_ONLY:
                    bad.append("%s:%d %s" % (p.relative_to(BACKEND), node.lineno, n))
    assert not bad, bad


def test_the_installed_numpy_is_the_pinned_one():
    import numpy  # a missing numpy FAILS here; it is never skipped
    assert numpy.__version__ == RM.read_lock(BACKEND / "requirements-test.lock")["numpy"]


def test_the_parity_proof_and_this_pin_are_capital_critical():
    """08828d04 took the parity file off the list because its oracle skipped
    in CI; with numpy pinned it runs, and it stays on the list."""
    text = (BACKEND / "tools" / "capital_critical_tests.txt").read_text()
    critical = {ln.split("#", 1)[0].strip() for ln in text.splitlines()} - {""}
    for f in ("tests/test_revenue_reliability_parity.py", "tests/test_numpy_is_test_only.py",
              "tests/test_revenue_reliability_readback.py"):
        assert f in critical, f


def test_no_test_turns_a_skip_into_a_pass():
    """`except pytest.skip.Exception: return` reports a proof that did not run
    as PASSED -- invisible to ci_verdict / gate_verdict, which reject skips
    only when they are reported as skips."""
    bad = []
    for p in sorted((BACKEND / "tests").glob("*.py")):
        for node in ast.walk(_parse(p)):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                src = ast.unparse(node.type)
                if "skip.Exception" in src or "Skipped" in src:
                    bad.append("%s:%d" % (p.name, node.lineno))
    assert not bad, bad
