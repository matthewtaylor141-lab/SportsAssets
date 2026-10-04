"""THE LAB CANNOT REACH THE ORDER PATH, AND NO DECISION PATH READS THE LAB.

Both directions, statically and at runtime:

  * no module of sportsassets/lab (nor the lab's endpoint module) imports an
    order, venue, execution, live or funded module -- by name in its source
    (every `import` / `from` statement, lazy ones included), and in a fresh
    interpreter that imports every lab module AND exercises the functions
    that import lazily, none of those modules is loaded;
  * no module outside the lab imports the lab, except the API application
    (which registers the read-only route), the lab's own endpoint module and
    the lab's offline scripts -- so no decision, adapter, execution or
    agent path can read a lab output;
  * the offline fast-lane harness imports the canonical components it times
    (read-only) but no order, venue, execution, live or funded module.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import warnings

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
LAB = PKG / "lab"
LAB_ENDPOINT = PKG / "api" / "command_lab_edge_decay.py"
LAB_SCRIPTS = [PKG / "scripts" / "lab_edge_decay.py",
               PKG / "scripts" / "lab_fastlane_measure.py"]
#: module-name stems a lab module may never import (order / venue /
#: execution / live / funded)
FORBIDDEN = ("execmirror", "execution_intent", "execution_gate",
             "live_executor", "live_parity", "live_book_currency",
             "live_book_evidence", "live_rule_artifacts", "live_approvals",
             "kalshi_orders", "kalshi_venue", "kalshi_account", "venue_",
             "bettor_funded", "bettor_entry_execution", "bettor_paper_ledger",
             "bettor_paper_simulator", "bettor_test_venue_executor",
             "submission_surface", "clob", "pmus", "pmx", "positions_sync",
             "order_state_truth", "actual_admission", "p5_runtime",
             "p5_c12_proof", "bettor_live_control", "bettor_live_store",
             "bettor_read_only_venue", "decision_hooks")


def _imports(path: pathlib.Path) -> set:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            for a in n.names:
                out.add(("%s.%s" % (mod, a.name)) if mod else a.name)
            out.add(mod)
    return out


def _stem_forbidden(name: str) -> str | None:
    """The forbidden stem a dotted module name contains, or None."""
    for part in name.split("."):
        if part.startswith("bettor_funded"):
            return "bettor_funded"
        for f in FORBIDDEN:
            if part == f or (f.endswith("_") and part.startswith(f)):
                return f
    return None


def _lab_files():
    return sorted(LAB.rglob("*.py")) + [LAB_ENDPOINT]


def test_no_lab_module_names_a_forbidden_module():
    bad = {}
    for f in _lab_files():
        hits = sorted(i for i in _imports(f) if _stem_forbidden(i))
        if hits:
            bad[str(f.relative_to(ROOT))] = hits
    assert not bad, bad


def test_importing_and_running_the_lab_loads_no_forbidden_module():
    code = (
        "import sys\n"
        "import sportsassets.lab.edge_decay as ED\n"
        "import sportsassets.lab.edge_decay_reads\n"
        "import sportsassets.lab.fastlane as FL\n"
        "import sportsassets.lab.pit\n"
        "import sportsassets.lab.stats\n"
        "import sportsassets.lab.reference.edge_decay\n"
        "lv = ED.to_levels([['0.50', '10']], 'LONG')\n"
        "ED.top_net_edge(lv, p=0.6, at=1791000000.0)\n"
        "FL.derive_graph(); FL.intent_assembly()\n"
        "FORB = %r\n"
        "hit = sorted(m for m in sys.modules if m.startswith('sportsassets.')"
        " and any(p == f or (f.endswith('_') and p.startswith(f)) or "
        "p.startswith('bettor_funded') for p in m.split('.')[1:] "
        "for f in FORB))\n"
        "print('LOADED:' + ','.join(hit))\n" % (FORBIDDEN,))
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    line = [x for x in out.stdout.splitlines() if x.startswith("LOADED:")][-1]
    assert line == "LOADED:", line


def test_no_decision_path_imports_the_lab():
    allowed = {LAB_ENDPOINT, PKG / "api" / "app.py", *LAB_SCRIPTS}
    offenders = []
    for f in PKG.rglob("*.py"):
        if LAB in f.parents or f in allowed:
            continue
        for i in _imports(f):
            parts = i.split(".")
            if "lab" in parts and (i.startswith("sportsassets.lab")
                                   or i.startswith(".lab")
                                   or i.startswith("..lab")
                                   or i == "lab" or parts[0] == "lab"):
                offenders.append((str(f.relative_to(ROOT)), i))
        src = f.read_text()
        if "sportsassets.lab" in src or "from .lab" in src or \
                "from ..lab" in src:
            offenders.append((str(f.relative_to(ROOT)), "TEXT_REFERENCE"))
    assert not offenders, offenders


def test_the_app_only_registers_the_read_only_router():
    src = (PKG / "api" / "app.py").read_text()
    lab_lines = [ln.strip() for ln in src.splitlines()
                 if not ln.strip().startswith("#") and "import" in ln
                 and "lab" in ln.lower()]
    # every lab import in the application is a read-only lab ROUTER module
    # (other lab tracks register theirs the same way)
    assert lab_lines and all(ln.startswith("from .command_lab_")
                             for ln in lab_lines), lab_lines
    assert ("from .command_lab_edge_decay import router as "
            "_command_lab_edge_decay_router") in lab_lines
    ep = LAB_ENDPOINT.read_text()
    assert "@router.get(" in ep
    for verb in ("@router.post(", "@router.put(", "@router.delete(",
                 "@router.patch("):
        assert verb not in ep
    assert "readonly=True" in ep and "statement_timeout" in ep


def test_the_fast_lane_harness_reaches_no_order_path():
    for f in LAB_SCRIPTS:
        hits = sorted(i for i in _imports(f) if _stem_forbidden(i))
        assert not hits, (f.name, hits)
    src = (PKG / "scripts" / "lab_fastlane_measure.py").read_text()
    assert "readonly=True" in src
    # the harness writes nothing but its one SHADOW snapshot
    assert src.count("INSERT INTO") == 1
    assert "INSERT INTO lab_edge_decay_snapshots" in src
