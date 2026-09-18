"""The install step and the suite's real dependencies must agree.

THE DEFECT THIS PINS.
For months four workflows installed two packages and then ran a suite that
imports four. Nothing connected the pin list to the code, so nothing noticed.
Run 35366130658 noticed, by acquiring execution and failing GATE 1 with
`33 failed, 2541 passed` -- thirty-three ModuleNotFoundErrors for numpy, and
twenty more waiting behind it for scipy.

This is the same shape as every other defect this session: a rule declared in
one place and unenforced where the work actually happens. So the pin list is
enforced here, from the code, by the suite itself.

WHAT IS ASSERTED.
  1. Every workflow that runs `pytest -q` over THIS WHOLE DIRECTORY installs
     from requirements-test.txt and does not carry its own inline pin list.
  2. Every third-party module imported unconditionally by this directory --
     established by reading the source, not by trusting a list -- is pinned in
     requirements-test.txt.
  3. Every pin is an exact `==` version. No ranges, no "latest".
  4. The two deliberate exclusions are still deliberate: scikit-learn and
     PyYAML are absent from the pins AND their absence is still safe, because
     every sklearn import sits behind an ImportError handler that records a
     named refusal, and yaml is imported only by the offline rehearsal
     harness, which the runner does not execute.

WHAT IS NOT ASSERTED.
  That the pinned VERSIONS are right. Only a clean-room install can show
  that, and requirements-test.txt records the run that did.
"""

from __future__ import annotations

import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REQUIREMENTS = HERE / "requirements-test.txt"

# The standard library of the interpreter that runs the suite. Using the
# interpreter's own answer rather than a hand-kept list is the point: a
# hand-kept list is exactly the thing that went stale.
STDLIB = set(sys.stdlib_module_names)

# First-party modules are not only the ones in THIS directory. collect.py,
# inventory.py and maker_fill.py each put a sibling beta48 directory on
# sys.path and import from it (`sys.path.insert(0, ... / "forward")`), so
# eligibility, fees_v2, fill_model_v2 and fwd_collect are ours, not PyPI's.
# The set is read off the tree rather than typed out, for the same reason
# the pin list is read off a file: a typed list is what goes stale.
BETA48 = HERE.parent
LOCAL = {p.stem for p in BETA48.rglob("*.py")} | {
    p.name for p in BETA48.rglob("*") if p.is_dir() and (p / "__init__.py").exists()
}

# The rehearsal harnesses are not executed by `pytest -q` on the runner. Their
# imports do not belong in the runner's dependency set.
NOT_RUN_ON_THE_RUNNER = {"rehearse_startup_gate.py", "rehearse_dispatch.py"}


def pins():
    out = {}
    for raw in REQUIREMENTS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            name, sep, version = line.partition("==")
            out[name.strip().lower()] = (sep, version.strip())
    return out


def _import_names(tree):
    """(module, is_guarded) for every import in the tree.

    Guarded means lexically inside a `try:` whose handler catches ImportError
    -- the shape an optional dependency must have to be optional.
    """
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches = any(
            (h.type is None)
            or (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "Exception"))
            or (isinstance(h.type, ast.Tuple) and any(
                isinstance(e, ast.Name) and e.id in ("ImportError", "Exception")
                for e in h.type.elts))
            for h in node.handlers)
        if catches:
            for stmt in node.body:
                for sub in ast.walk(stmt):
                    guarded.add(id(sub))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # relative import: first-party
                continue
            names = [node.module or ""]
        else:
            continue
        for n in names:
            top = n.split(".")[0]
            if top:
                found.append((top, id(node) in guarded))
    return found


def third_party_imports():
    """{module: {"required": bool, "files": set}} for this directory."""
    seen = {}
    for path in sorted(HERE.glob("*.py")):
        if path.name in NOT_RUN_ON_THE_RUNNER:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:                     # pragma: no cover - none today
            continue
        for module, is_guarded in _import_names(tree):
            if module in STDLIB or module in LOCAL or module.startswith("_"):
                continue
            row = seen.setdefault(module, {"required": False, "files": set()})
            row["files"].add(path.name)
            if not is_guarded:
                row["required"] = True
    return seen


def full_suite_workflows():
    """Workflow files that run pytest over this whole directory."""
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text()
        if "working-directory: research/beta48/shadow" in text and \
                "run: python -m pytest -q" in text:
            out.append(path)
    return out


# ---------------------------------------------------------------- 1. workflows

def test_the_workflows_that_run_this_suite_are_the_ones_we_think_they_are():
    names = {p.name for p in full_suite_workflows()}
    assert names == {
        "beta48-substantive-capture.yml",
        "beta48-rate-confirm.yml",
        "beta48-rate-pilot.yml",
        "beta48-shadow-tick.yml",
    }, names


def test_every_full_suite_workflow_installs_from_the_pinned_file():
    for path in full_suite_workflows():
        text = path.read_text()
        assert "-r research/beta48/shadow/requirements-test.txt" in text, path.name


def test_no_full_suite_workflow_carries_its_own_inline_pin_list():
    # The inline list is what drifted. A workflow may not name a package
    # version in a pip install line at all; the file is the only source.
    for path in full_suite_workflows():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if "pip install" in line:
                assert "==" not in line, "%s:%d inline pin: %s" % (
                    path.name, i, line.strip())


def test_every_full_suite_workflow_proves_the_environment_before_the_boundary():
    for path in full_suite_workflows():
        text = path.read_text()
        assert "prove_environment.py" in text, path.name
        assert text.index("prove_environment.py") < text.index("run: python -m pytest -q"), \
            "%s proves the environment after the boundary" % path.name


# ------------------------------------------------------------- 2. completeness

def test_every_required_third_party_import_is_pinned():
    required = {m for m, row in third_party_imports().items() if row["required"]}
    missing = sorted(m for m in required if m not in pins())
    assert not missing, "imported unconditionally but never installed: %s" % missing


def test_the_required_set_is_exactly_the_four_we_proved_in_a_clean_room():
    required = {m for m, row in third_party_imports().items() if row["required"]}
    assert required == {"httpx", "pytest", "numpy", "scipy"}, sorted(required)


def test_nothing_is_pinned_that_the_suite_does_not_import():
    imported = set(third_party_imports())
    extra = sorted(p for p in pins() if p not in imported)
    assert not extra, "pinned but unimported: %s" % extra


# -------------------------------------------------------------------- 3. shape

def test_every_pin_is_an_exact_version():
    for name, (sep, version) in pins().items():
        assert sep == "==", "%s is not pinned exactly" % name
        assert version, "%s has no version" % name


# ------------------------------------------------- 4. the deliberate omissions

def test_sklearn_is_absent_and_its_absence_is_still_named_not_silent():
    rows = third_party_imports()
    assert "sklearn" not in pins()
    assert "sklearn" in rows, "sklearn import vanished; revisit requirements-test.txt"
    assert not rows["sklearn"]["required"], \
        "sklearn is now imported unconditionally and must be pinned"
    # The handler must record a refusal, not fall through to a fitted-looking
    # result. This is the 'unavailable must never render as zero' rule.
    for name in sorted(rows["sklearn"]["files"]):
        assert "SKLEARN_NOT_AVAILABLE" in (HERE / name).read_text(), name


def test_yaml_is_only_ever_needed_by_the_harness_the_runner_does_not_execute():
    rows = third_party_imports()
    assert "yaml" not in pins()
    # third_party_imports() already excludes the rehearsal harnesses, so yaml
    # appearing here at all would mean the suite itself now needs it.
    assert "yaml" not in rows, rows.get("yaml")
    assert "import yaml" in (HERE / "rehearse_startup_gate.py").read_text()


def test_the_environment_prover_refuses_an_unpinned_line():
    import prove_environment as pe
    p, refused = pe.parse_requirements("httpx==0.27.2\nnumpy>=2\n# a comment\n")
    assert p == [("httpx", "0.27.2")]
    assert refused == ["numpy>=2"]


def test_the_environment_prover_calls_an_absent_package_absent():
    import prove_environment as pe
    found = pe.prove([("a-package-that-is-not-installed", "1.0")])
    assert found[0]["STATUS"] == "ABSENT"


def test_the_environment_prover_refuses_a_version_that_is_not_the_pinned_one():
    import prove_environment as pe
    import httpx
    assert pe.prove([("httpx", httpx.__version__)])[0]["STATUS"] == "OK"
    assert pe.prove([("httpx", "0.0.0-not-this")])[0]["STATUS"] == "VERSION_MISMATCH"
