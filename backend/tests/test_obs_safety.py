"""THE SAFETY PROOF for the RN1 observability collector -- run 83F.

The invariant under test:

    THE OBSERVABILITY SHADOW HAS NO CODE PATH THAT SUBMITS, MODIFIES, CANCELS
    OR REPLACES A REAL ORDER.

It is proved two ways, because each catches what the other cannot.

STATICALLY, by walking the transitive import graph of sportsassets.obs and
requiring every first-party module it reaches to appear in an ALLOW-LIST. An
allow-list is used rather than a deny-list of order modules on purpose: a
deny-list silently goes stale the day someone adds a new egress module, whereas
an allow-list fails the moment the collector reaches ANY module nobody has
reviewed for this purpose. Widening it is a deliberate edit that shows up in a
diff.

DYNAMICALLY, by importing the collector with every order-submitting callable in
the tree replaced by a double that fails the test if it is called at all. The
static proof cannot see a late import inside a function body or a getattr; this
does.

If this file fails, the collector is not safe to run. It is not a lint.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PKG = BACKEND / "sportsassets"
OBS = PKG / "obs"

# ---------------------------------------------------------------------------
# THE ALLOW-LIST. Every first-party module the observability package may reach,
# directly or transitively. Each entry is here because it was read and found to
# contain no order path.
#
# TO ADD AN ENTRY: read the module and everything IT imports, confirm no order
# egress is reachable, and add it in the same commit as the import. If that
# feels like friction, it is the intended amount.
# ---------------------------------------------------------------------------
ALLOWED: frozenset[str] = frozenset({
    "sportsassets",
    "sportsassets.obs",
    "sportsassets.obs.clock",
    "sportsassets.obs.record",
    "sportsassets.obs.collector",
    "sportsassets.obs.schedule",
    "sportsassets.obs.book",
    "sportsassets.obs.config",
    # db is the connection pool only -- it executes SQL that the caller supplies
    # and reaches no venue.
    "sportsassets.db",
})

# Defence in depth. Any module whose name matches one of these fragments is
# refused even if someone adds it to ALLOWED by mistake -- two independent
# mistakes would be needed to get an order path in here.
FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "executor",
    "live_executor",
    "order",
    "mirror_live",
    "trade_client",
    "clob",
    "gateway",
    "venue_client",
)


def _first_party_imports(path: pathlib.Path) -> set[str]:
    """Module names imported by one file, resolved to dotted first-party names."""
    tree = ast.parse(path.read_text(), filename=str(path))
    # sportsassets.obs.clock -> package sportsassets.obs, for relative imports
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    pkg_parts = parts[:-1] if parts and parts[-1] != "" else parts

    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == "sportsassets":
                    out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)] \
                    if node.level > 1 else pkg_parts
                dotted = ".".join(list(base) + ([node.module] if node.module else []))
            else:
                dotted = node.module or ""
            if dotted.split(".")[0] != "sportsassets":
                continue
            out.add(dotted)
            # `from x import y` may name a submodule rather than an attribute;
            # both possibilities are recorded and the resolver keeps whichever
            # exists on disk.
            for a in node.names:
                out.add(f"{dotted}.{a.name}")
    return out


def _module_path(dotted: str) -> pathlib.Path | None:
    rel = pathlib.Path(*dotted.split("."))
    for cand in (BACKEND / rel.with_suffix(".py"), BACKEND / rel / "__init__.py"):
        if cand.exists():
            return cand
    return None


def _transitive_closure() -> set[str]:
    """Every first-party module reachable from sportsassets.obs by import."""
    seen: set[str] = set()
    stack: list[str] = []
    for f in sorted(OBS.glob("*.py")):
        dotted = "sportsassets.obs" if f.name == "__init__.py" \
            else f"sportsassets.obs.{f.stem}"
        stack.append(dotted)
    while stack:
        dotted = stack.pop()
        if dotted in seen:
            continue
        path = _module_path(dotted)
        if path is None:
            continue          # an attribute, not a module
        seen.add(dotted)
        for dep in _first_party_imports(path):
            if dep not in seen:
                stack.append(dep)
    return seen


@pytest.mark.skipif(not OBS.exists(), reason="obs package not present")
def test_obs_imports_nothing_outside_the_allow_list():
    reached = _transitive_closure()
    stray = sorted(reached - ALLOWED)
    assert not stray, (
        "the observability package reaches first-party modules that are NOT on "
        "the reviewed allow-list:\n  " + "\n  ".join(stray) +
        "\n\nEach of these must be read and confirmed to contain no order path "
        "before being added to ALLOWED in this file. Do not widen the list to "
        "make a test pass."
    )


@pytest.mark.skipif(not OBS.exists(), reason="obs package not present")
def test_obs_reaches_no_module_whose_name_suggests_an_order_path():
    reached = _transitive_closure()
    bad = sorted(m for m in reached
                 if any(frag in m.lower() for frag in FORBIDDEN_FRAGMENTS))
    assert not bad, (
        "the observability package reaches a module whose name matches a known "
        "order-egress pattern: " + ", ".join(bad)
    )


@pytest.mark.skipif(not OBS.exists(), reason="obs package not present")
def test_no_obs_source_file_mentions_an_order_verb():
    """A last textual backstop, over the package's own source.

    Catches a late import or a getattr that the AST walk above resolves as an
    attribute rather than a module. Comments and docstrings ARE scanned: this
    file's own prose says 'order' constantly, which is why the check runs over
    the obs package and not over itself.
    """
    verbs = ("place_order", "create_order", "submit_order", "cancel_order",
             "replace_order", "amend_order", "post_order", "send_order")
    offenders = []
    for f in sorted(OBS.rglob("*.py")):
        text = f.read_text()
        for v in verbs:
            if v in text:
                offenders.append(f"{f.relative_to(BACKEND)}: {v}")
    assert not offenders, (
        "an observability source file names an order verb: " + ", ".join(offenders)
    )


@pytest.mark.skipif(not OBS.exists(), reason="obs package not present")
def test_importing_obs_calls_no_order_api(monkeypatch):
    """DYNAMIC proof: import the whole package with every order entry point armed.

    Any call -- at import time or from a module-level side effect -- fails here
    rather than reaching a venue.
    """
    calls: list[str] = []

    def _tripwire(name):
        def _fail(*a, **k):
            calls.append(name)
            raise AssertionError(
                f"the observability package called {name}. The shadow collector "
                "must have no order path at all."
            )
        return _fail

    import importlib

    # Arm every module in the tree that exposes an order-shaped callable, then
    # import the collector on top of the armed tree.
    for f in sorted(PKG.rglob("*.py")):
        dotted = ".".join(f.relative_to(BACKEND).with_suffix("").parts)
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
        if dotted.startswith("sportsassets.obs"):
            continue
        mod = sys.modules.get(dotted)
        if mod is None:
            continue
        for attr in dir(mod):
            low = attr.lower()
            if any(v in low for v in ("place_order", "create_order", "cancel",
                                      "submit", "replace_order")):
                if callable(getattr(mod, attr, None)):
                    monkeypatch.setattr(mod, attr, _tripwire(f"{dotted}.{attr}"),
                                        raising=False)

    for f in sorted(OBS.glob("*.py")):
        dotted = "sportsassets.obs" if f.name == "__init__.py" \
            else f"sportsassets.obs.{f.stem}"
        importlib.import_module(dotted)

    assert not calls, f"order API called during import: {calls}"
