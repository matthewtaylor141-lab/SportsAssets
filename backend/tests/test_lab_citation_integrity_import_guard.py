"""LAB-B IMPORT GUARD, BOTH DIRECTIONS.

1. The lab's citation-integrity modules import only the standard library and
   each other: nothing from an order, venue, execution, funded, paper or
   agent module (static AST check), and importing them loads no other
   sportsassets module at all (a fresh interpreter).
2. No decision path imports the lab: importing the investment / management /
   execution modules loads no sportsassets.lab module (a fresh interpreter),
   and the only modules that import the lab are the agent chat's answer
   pipeline (persona_chat, where the verifier must run before publication)
   and the lab's own read-only endpoint -- a new importer fails this test
   until it is justified here.
"""
from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
LAB_MODULES = ("citation_integrity.py", "citation_integrity_store.py")
#: the modules allowed to import the lab, and why
LAB_IMPORTERS = {
    "sportsassets/agents/persona_chat.py":
        "the agent chat's single publication choke point: the verifier "
        "runs there before an answer is stored or returned",
    "sportsassets/api/command_lab_citation.py":
        "the lab's own read-only endpoint",
}
DECISION_PATH = (
    "sportsassets.agents.paper_benchmark", "sportsassets.agents.paper_xavier",
    "sportsassets.live_parity", "sportsassets.canonical_intent",
    "sportsassets.canonical_components", "sportsassets.allie_capital",
    "sportsassets.decision_hooks", "sportsassets.execution_intent",
    "sportsassets.execmirror", "sportsassets.bettor_funded_execution",
    "sportsassets.bettor_entry_execution", "sportsassets.bettor_paper_ledger",
    "sportsassets.agents.eddie", "sportsassets.agents.karen",
)


def _imports(path: pathlib.Path) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [(0, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append((node.level, node.module))
            else:                       # `from . import x` imports module x
                out += [(node.level, a.name) for a in node.names]
    return out


def test_the_lab_imports_only_the_standard_library_and_itself():
    std = set(sys.stdlib_module_names) | {"__future__"}
    for name in LAB_MODULES:
        for level, mod in _imports(PKG / "lab" / name):
            if level == 0:
                assert mod.split(".")[0] in std, (name, mod)
            else:
                # relative: only a sibling inside the lab package
                assert level == 1 and mod in ("citation_integrity",
                                              "citation_integrity_store"), (
                    name, level, mod)


def _fresh(code: str) -> list:
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_importing_the_lab_loads_no_other_sportsassets_module():
    loaded = _fresh(
        "import sys, json\n"
        "import sportsassets.lab.citation_integrity\n"
        "import sportsassets.lab.citation_integrity_store\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('sportsassets'))))")
    assert set(loaded) == {"sportsassets", "sportsassets.lab",
                           "sportsassets.lab.citation_integrity",
                           "sportsassets.lab.citation_integrity_store"}, loaded


def test_no_decision_path_loads_the_lab():
    loaded = _fresh(
        "import sys, json, importlib\n"
        "for m in %r:\n"
        "    importlib.import_module(m)\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m.startswith('sportsassets.lab') or "
        "m.endswith('persona_chat'))))" % (DECISION_PATH,))
    assert loaded == [], loaded


def test_only_the_chat_pipeline_and_the_lab_endpoint_import_the_lab():
    importers = set()
    for path in PKG.rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if rel.startswith("sportsassets/lab/"):
            continue
        for level, mod in _imports(path):
            target = mod if level == 0 else None
            if level:
                # resolve a relative import against the module's package
                base = path.relative_to(ROOT).parent.parts
                base = base[:len(base) - (level - 1)]
                target = ".".join(base + ((mod,) if mod else ()))
            if target and (target == "sportsassets.lab" or
                           target.startswith("sportsassets.lab.")):
                importers.add(rel)
    assert importers == set(LAB_IMPORTERS), importers


def test_listed_as_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_lab_citation_integrity_import_guard.py" in \
        listed.split()
