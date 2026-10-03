"""THE KALSHI SUBMIT PATH IS REACHABLE FROM NOWHERE IT SHOULD NOT BE.

The Kalshi building blocks are deliberately NOT wired into any runner.
These AST checks keep it that way until a reviewed change says otherwise:

  * only the Kalshi modules themselves import `kalshi_venue` (the module
    holding the client, its `submit` and `cancel`) or `kalshi_orders`;
  * no paper, funded, execution-mirror, worker, agent or API module imports
    any Kalshi module;
  * the Kalshi modules import no paper or funded module, no edge-engine
    code, and no network library at module level (requests is imported
    lazily inside the real transport only);
  * nothing outside kalshi_venue calls `.submit(` on anything named like a
    Kalshi client.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
KALSHI = {"kalshi_venue.py", "kalshi_account.py", "kalshi_mapping.py",
          "kalshi_orders.py", "kalshi_linkage.py", "venue_selection.py"}
KALSHI_MODS = {m[:-3] for m in KALSHI}


def _imports(tree) -> list[str]:
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    return mods


def _top_level_imports(tree) -> list[str]:
    mods = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    return mods


def test_all_kalshi_modules_exist():
    for name in KALSHI:
        assert (ROOT / name).exists(), name


def test_only_kalshi_modules_import_the_kalshi_modules():
    offenders = []
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        if rel in KALSHI:
            continue
        for m in _imports(ast.parse(p.read_text())):
            leaf = (m or "").split(".")[-1]
            if leaf in KALSHI_MODS:
                offenders.append((rel, m))
    assert offenders == [], offenders


def test_the_kalshi_modules_import_no_paper_funded_or_edge_engine_code():
    for name in KALSHI:
        tree = ast.parse((ROOT / name).read_text())
        for m in _imports(tree):
            low = (m or "").lower()
            assert "paper" not in low and "funded" not in low, (name, m)
            assert not low.startswith("edge"), (name, m)
            assert "pmus" not in low and "polymarket" not in low, (name, m)
        for m in _top_level_imports(tree):
            assert m.split(".")[0] not in ("requests", "httpx", "aiohttp",
                                           "urllib3", "websockets"), (name, m)


def test_only_kalshi_venue_holds_a_submit_call():
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        if rel == "kalshi_venue.py":
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "submit":
                owner = ast.unparse(node.func.value).lower()
                assert "kalshi" not in owner, (rel, ast.unparse(node))


def test_the_submit_path_is_behind_the_gate_in_source():
    """kalshi_venue.KalshiClient.submit calls submission_gate before it
    sends; a refactor that reorders this fails here as well as in the
    behavioural tests."""
    tree = ast.parse((ROOT / "kalshi_venue.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "KalshiClient")
    sub = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name == "submit")
    calls = [ast.unparse(n.func) for n in ast.walk(sub) if isinstance(n, ast.Call)]
    assert calls.index("submission_gate") < calls.index("self._send")
