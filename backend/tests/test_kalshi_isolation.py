"""THE KALSHI SUBMIT PATH IS REACHABLE FROM NOWHERE IT SHOULD NOT BE.

The Kalshi building blocks were deliberately NOT wired into any runner.
These AST checks keep it that way until a reviewed change says otherwise:

  * only the Kalshi modules themselves import `kalshi_venue` (the module
    holding the client, its `submit` and `cancel`) or `kalshi_orders`;
  * no paper, funded, execution-mirror, worker, agent or API module imports
    any Kalshi module -- with ONE reviewed exception (rc6.3 kalshi-shadow,
    below);
  * the Kalshi modules import no paper or funded module, no edge-engine
    code, and no network library at module level (requests is imported
    lazily inside the real transport only);
  * nothing outside kalshi_venue calls `.submit(` on anything named like a
    Kalshi client.

THE ONE REVIEWED EXCEPTION (rc6.3 kalshi-shadow, Issue #6 gates 2/3/6). The
Kalshi SHADOW planner (kalshi_shadow.py, a member of the Kalshi set) is
armed by the API lifespan: api/app.py may import `kalshi_shadow` -- that
module and no other Kalshi module, only inside `lifespan`, and only to
start `_KSHADOW.run(_cap_pool)`. Everything else stays as it was, and the
exception is fenced by the checks added with it:

  * kalshi_shadow never imports kalshi_venue (the client) directly: it
    reads the account only through kalshi_account.read_only_client, whose
    transport refuses every method but GET (GetOnlyTransport);
  * kalshi_shadow names no order / cancel / send primitive at all (no
    attribute or name submit, cancel, _send, place, submit_fok,
    cancel_order, post_order, create_order, close_position, KalshiClient,
    RequestsTransport), and no module outside kalshi_venue / kalshi_account
    constructs a KalshiClient;
  * the only KalshiClient kalshi_account constructs is read_only_client's,
    over GetOnlyTransport;
  * nothing a shared-worker loop starts reaches kalshi_shadow (the workers
    must never reach kalshi_orders: tests/test_workers_hold_no_venue_write).
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
KALSHI = {"kalshi_venue.py", "kalshi_account.py", "kalshi_mapping.py",
          "kalshi_orders.py", "kalshi_linkage.py", "venue_selection.py",
          "kalshi_shadow.py"}
KALSHI_MODS = {m[:-3] for m in KALSHI}
#: (rc6.3 kalshi-shadow) THE reviewed importer -> the Kalshi modules it may
#: import. Nothing else outside the Kalshi set imports a Kalshi module.
REVIEWED_IMPORTERS = {"api/app.py": {"kalshi_shadow"}}
#: names the SHADOW planner may never call or reference
SHADOW_FORBIDDEN = {"submit", "cancel", "_send", "place", "submit_fok",
                    "cancel_order", "post_order", "create_order",
                    "close_position", "KalshiClient", "RequestsTransport",
                    "cancel_request", "_tx", "_auth"}


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
        allowed = REVIEWED_IMPORTERS.get(rel, set())
        for m in _imports(ast.parse(p.read_text())):
            leaf = (m or "").split(".")[-1]
            if leaf in KALSHI_MODS and leaf not in allowed:
                offenders.append((rel, m))
    assert offenders == [], offenders


def test_the_one_reviewed_importer_imports_the_planner_inside_lifespan_only():
    """api/app.py imports kalshi_shadow exactly once, inside `lifespan`,
    and uses it only to start its run loop on the API's pool getter."""
    tree = ast.parse((ROOT / "api" / "app.py").read_text())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
                a.name in KALSHI_MODS for a in node.names):
            hits.append(node)
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(
                ".")[-1] in KALSHI_MODS:
            hits.append(node)
        if isinstance(node, ast.Import) and any(
                a.name.split(".")[-1] in KALSHI_MODS for a in node.names):
            hits.append(node)
    assert len(hits) == 1, [ast.unparse(h) for h in hits]
    imp = hits[0]
    assert [(a.name, a.asname) for a in imp.names] == [("kalshi_shadow",
                                                        "_KSHADOW")]
    life = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "lifespan")
    assert any(n is imp for n in ast.walk(life)), "imported outside lifespan"
    uses = [ast.unparse(n) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "_KSHADOW"]
    assert uses == ["_KSHADOW.run"], uses
    calls = [ast.unparse(n) for n in ast.walk(life) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "_KSHADOW.run"]
    assert calls == ["_KSHADOW.run(_cap_pool)"], calls


def test_the_shadow_planner_names_no_order_cancel_or_send_primitive():
    tree = ast.parse((ROOT / "kalshi_shadow.py").read_text())
    named = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            named.add(node.attr)
        elif isinstance(node, ast.Name):
            named.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            named.add(node.name)
    assert not named & SHADOW_FORBIDDEN, sorted(named & SHADOW_FORBIDDEN)
    mods = {(m or "").split(".")[-1] for m in _imports(tree)}
    assert "kalshi_venue" not in mods, "the planner imports the client module"
    # and no attribute path through another module to the client module
    # (kalshi_account / kalshi_orders bind it as `KV`)
    assert "KV" not in named and "kalshi_venue" not in named


def test_only_the_venue_and_the_read_only_account_construct_a_client():
    """No module outside kalshi_venue / kalshi_account builds a KalshiClient,
    and kalshi_account builds exactly one: read_only_client's, over the
    GET-only transport."""
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        if rel in ("kalshi_venue.py", "kalshi_account.py"):
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and ast.unparse(
                    node.func).split(".")[-1] == "KalshiClient":
                raise AssertionError((rel, ast.unparse(node)))
    tree = ast.parse((ROOT / "kalshi_account.py").read_text())
    built = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and ast.unparse(
                    node.func).split(".")[-1] == "KalshiClient":
                built.append((fn.name, ast.unparse(node.args[0])))
    assert built == [("read_only_client", "GetOnlyTransport(transport)")], \
        built


def test_no_shared_worker_loop_reaches_the_shadow_planner():
    """The planner runs in the API process: the shared workers must never
    reach kalshi_orders, and so never the planner (static import closure
    from the module of every loop workers/all.py starts, the same roots as
    tests/test_workers_hold_no_venue_write)."""
    import sys
    import types
    sys.modules.setdefault("pywebpush", types.SimpleNamespace(
        webpush=None, WebPushException=Exception))
    from sportsassets.workers import all as all_mod
    pkg = ROOT.parent
    mods = {}
    for p in ROOT.rglob("*.py"):
        name = ".".join(p.relative_to(pkg).with_suffix("").parts)
        mods[name[:-9] if name.endswith(".__init__") else name] = p

    def deps(name):
        p = mods[name]
        is_pkg = p.name == "__init__.py"
        out = set()
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom):
                if n.level:
                    base = name.split(".") if is_pkg else name.split(".")[:-1]
                    base = base[:len(base) - (n.level - 1)]
                    pre = ".".join(base + ([n.module] if n.module else []))
                elif (n.module or "").startswith("sportsassets"):
                    pre = n.module
                else:
                    continue
                out |= {m for m in [pre] + [pre + "." + a.name
                                            for a in n.names] if m in mods}
            elif isinstance(n, ast.Import):
                out |= {a.name for a in n.names if a.name in mods}
        return out

    roots = [fn.__module__ for name, fn in all_mod.startable_loops()
             if name != "memory"]                # defined in all.py itself
    assert roots and all(r in mods for r in roots), roots
    seen, stack = set(), list(roots)
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack += list(deps(x))
    assert "sportsassets.kalshi_shadow" not in seen


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
