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
    # ---- run 83.2 -------------------------------------------------------
    # Each read and confirmed to contain no order path before being added, as
    # the failure message above requires. None of them imports a venue client,
    # an executor or a signer; the only network call in the whole set is
    # cache.refresh_once's POST to the read-only /books endpoint.
    #
    # subject   -- os.environ only. Pure eligibility arithmetic, no imports
    #              beyond `os`.
    # slot      -- hashlib only. Derives an id string; touches nothing.
    # scheduler -- clock, config, slot, and cache (inside one function, to
    #              break the import cycle). A heap and a decision table; no I/O
    #              of any kind, which test_run832_scheduler.py asserts.
    # cache     -- clock, book, config. Holds book state and refreshes it with
    #              a POST to /books. READ-ONLY: it posts a list of token ids and
    #              parses ladders. No order fields, no auth, no signer.
    # capacity  -- config only. Arithmetic over measured rates; returns a
    #              report object and performs no action.
    "sportsassets.obs.subject",
    "sportsassets.obs.slot",
    "sportsassets.obs.scheduler",
    "sportsassets.obs.cache",
    "sportsassets.obs.capacity",
    # ---- run 83.3 -------------------------------------------------------
    # The two stream channels and the handshake client. Each read and confirmed
    # to contain no order path and no signer before being added here.
    #
    # streamstate -- clock and bisect. The state model and the 0 ms selection
    #                rule. No I/O of any kind.
    # pmus_stream -- clock, streamstate. A decoder and a dict of histories.
    #                CONTAINS NO SOCKET AND NO CREDENTIAL: the transport is
    #                deliberately not in this module, which is what lets the
    #                channel be tested with no connection.
    # clob_stream -- clock, streamstate. Same shape. Unauthenticated channel.
    # handshake   -- os.environ (broker URL + this collector's caller token,
    #                NEITHER of which is a venue credential) and logging. It can
    #                ask a separate process for header values already minted for
    #                GET /v1/ws/markets. It cannot sign, because it has no key
    #                and no signing primitive -- test_run833_capability_isolation
    #                asserts both by AST.
    "sportsassets.obs.streamstate",
    "sportsassets.obs.pmus_stream",
    "sportsassets.obs.clob_stream",
    "sportsassets.obs.handshake",
})

# Defence in depth. A module whose name matches one of these fragments is
# refused even if someone adds it to ALLOWED by mistake -- two independent
# mistakes would be needed to get an order path in here.
#
# THE LIST IS SPLIT, AND THE SPLIT IS NOT A WEAKENING. Run 83.3 added
# obs/clob_stream.py, which tripped the old single list on the fragment "clob".
# That was the check working: "clob" was put there to catch a CLOB ORDER CLIENT.
# But it cannot distinguish a venue-client module from an obs module legitimately
# NAMED AFTER the venue whose market data it decodes, and the honest fix is to
# say which fragments mean what.
#
# ORDER_FRAGMENTS name an ACTION and apply everywhere, obs included: an
# obs.order_client would still need two independent mistakes to get in.
# VENUE_SURFACE_FRAGMENTS name a VENUE and are meaningful only outside the obs
# package -- reaching sportsassets.clob_client or sportsassets.gateway from here
# is suspicious, while obs owning a clob_stream decoder is the design.
ORDER_FRAGMENTS: tuple[str, ...] = (
    "executor",
    "live_executor",
    "order",
    "mirror_live",
    "trade_client",
    "venue_client",
    "signer",
)
VENUE_SURFACE_FRAGMENTS: tuple[str, ...] = (
    "clob",
    "gateway",
    "pmus",
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
                 if any(frag in m.lower() for frag in ORDER_FRAGMENTS))
    assert not bad, (
        "the observability package reaches a module whose name matches a known "
        "order-egress pattern: " + ", ".join(bad)
    )


@pytest.mark.skipif(not OBS.exists(), reason="obs package not present")
def test_obs_reaches_no_venue_client_module_outside_its_own_package():
    """The second half of the split list.

    A venue-named module INSIDE obs is a decoder this package owns. A
    venue-named module anywhere else in sportsassets is a client that can talk
    to that venue, and the collector must not reach one.
    """
    reached = _transitive_closure()
    bad = sorted(m for m in reached
                 if not m.startswith("sportsassets.obs")
                 and any(frag in m.lower() for frag in VENUE_SURFACE_FRAGMENTS))
    assert not bad, (
        "the observability package reaches a venue-client module outside its "
        "own package: " + ", ".join(bad)
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


# ---------------------------------------------------------------- the spin
#
# THE DEFECT THIS PINS (2026-09-12, found by the run 83 deployment gate, not by
# a test -- which is the point). With the flag off, main() logged one line and
# RETURNED. workers/all.py's supervisor is written for loops that only ever end
# by raising, so it read the clean return as an anomaly: WARNING, sleep 5s,
# start again. The inert collector span twelve times a minute in production,
# about 17,000 warnings a day into the log every incident is diagnosed from.
#
# The original test asserted main() "does nothing", and returning immediately
# satisfies that reading perfectly. It never asked what the CALLER does next.
# These two do, against the supervisor's real contract rather than a docstring.

def test_the_inert_collector_does_not_return_because_the_supervisor_restarts_it():
    """With the flag off, main() must not complete -- it parks."""
    import asyncio as _asyncio

    from sportsassets.workers import rn1_observability

    async def drive():
        task = _asyncio.ensure_future(rn1_observability.main())
        # Generous next to a 5s restart delay: if main() returns at all it
        # returns immediately, so anything that is still pending here is parked.
        done, pending = await _asyncio.wait({task}, timeout=0.5)
        for p in pending:
            p.cancel()
            await _asyncio.gather(p, return_exceptions=True)
        return done

    finished = _asyncio.new_event_loop().run_until_complete(drive())
    assert not finished, (
        "rn1_observability.main() returned with the shadow flag off. "
        "workers/all.py logs a WARNING and restarts any loop that returns, so "
        "this is a 5-second spin in production, not an inert worker."
    )


def test_the_supervisor_really_does_restart_a_loop_that_returns():
    """Pin the contract the test above exists BECAUSE of.

    An earlier draft of this second test tried to prove the general property --
    "no registered loop can run to completion" -- by walking each loop's AST for
    an await. It PASSED ON THE BROKEN CODE: the defective main() still contained
    `await collector.run(...)` further down the function, on a branch the flag-off
    path never reaches. Proving the general property soundly means following the
    call into collector.run() to find the real `while True`, which this file is
    not the place for.

    So this pins the narrow thing that is true and checkable, read from source
    rather than imported (workers/all.py pulls in every worker): inside
    supervise's `while True`, a factory that simply RETURNS falls through to a
    warning and a sleep, and goes round again. There is no break and no return.
    If someone later teaches the supervisor to retire a loop that finishes, this
    test fails and the test above should be revisited with it.
    """
    import ast
    import pathlib

    all_py = (pathlib.Path(__file__).resolve().parents[1]
              / "sportsassets" / "workers" / "all.py")
    tree = ast.parse(all_py.read_text())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
               and n.name == "supervise"), None)
    assert fn is not None, "workers/all.py no longer defines supervise"

    forever = [s for s in fn.body
               if isinstance(s, ast.While)
               and isinstance(s.test, ast.Constant) and s.test.value is True]
    assert forever, "supervise's `while True` is gone; the restart contract changed"

    body = forever[0]
    escapes = [s for s in ast.walk(body)
               if isinstance(s, (ast.Break, ast.Return))]
    assert not escapes, (
        "supervise can now leave its restart loop, so a worker that returns may "
        "no longer respawn -- re-check why rn1_observability.main() parks."
    )

    awaits_factory = any(
        isinstance(s, ast.Expr) and isinstance(s.value, ast.Await)
        and isinstance(s.value.value, ast.Call)
        and isinstance(s.value.value.func, ast.Name)
        and s.value.value.func.id == "factory"
        for s in ast.walk(body))
    assert awaits_factory, "supervise no longer awaits factory() inside its loop"
