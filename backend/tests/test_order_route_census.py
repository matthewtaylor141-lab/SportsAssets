"""Every order-capable route, enumerated from the source each run.

WHY IT IS A TEST AND NOT A DOCUMENT. The last census was written by hand
and was wrong within a day: it recorded six routes and missed that
_execute_manual_sell honoured no stop control at all. A list of order
paths maintained by remembering to update it is a list that silently
goes stale, and the thing it goes stale about is what can spend money.

So the inventory is derived by walking the AST for calls to the two
functions that reach the venue, and the test fails when the set changes.
A new order route cannot be added to this repository without this file
turning red and someone deciding, in the open, which controls it gets.
"""

import ast
import os

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(BACKEND, "sportsassets")

# The two calls that actually send an order to the venue. cancel_order
# is not here: it reduces exposure and is deliberately ungated.
SUBMITTING = ("submit_fok", "close_position")
CANCELLING = ("cancel_order",)


def _py_files():
    for root, _dirs, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def _call_sites(names):
    """(module, function, call) for every call to one of `names`."""
    out = []
    for path in _py_files():
        try:
            tree = ast.parse(open(path).read())
        except SyntaxError:                                # pragma: no cover
            continue
        rel = os.path.relpath(path, BACKEND)
        stack = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                # pmus.submit_fok(...) and asyncio.to_thread(pmus.submit_fok, ...)
                for sub in ast.walk(node):
                    nm = getattr(sub, "attr", None) or getattr(sub, "id", None)
                    if nm in names:
                        out.append((rel, stack[-1] if stack else "<module>",
                                    nm, node.lineno))
                        break
                self.generic_visit(node)

            def visit_Attribute(self, node):
                # A STORED REFERENCE IS STILL A ROUTE. calibration_adapter
                # does `self._submit = submit_fn or pmus.submit_fok` and
                # calls it later through the attribute, so a walker that
                # only looks at call sites does not see it -- and the
                # first version of this census did not.
                if node.attr in names:
                    out.append((rel, stack[-1] if stack else "<module>",
                                node.attr, node.lineno))
                self.generic_visit(node)

        V().visit(tree)
    return out


def test_every_submitting_call_site_is_accounted_for():
    """The inventory. If this fails, an order path was added, moved or
    removed -- decide what controls it gets before making it pass."""
    sites = _call_sites(SUBMITTING)
    modules = sorted({s[0] for s in sites})
    expected = {
        "sportsassets/pmus.py",                    # the boundary itself
        "sportsassets/live_executor.py",           # copy, manual x3, exit
        "sportsassets/workers/mirror_live.py",     # probe, reserved place
        "sportsassets/workers/underdog.py",        # enter, cashout, exit
        "sportsassets/calibration_adapter.py",     # stored reference
    }
    assert set(modules) == expected, (
        "order-capable modules changed.\n  now: %s\n  was: %s"
        % (sorted(modules), sorted(expected)))


def test_the_boundary_is_gated_and_it_is_the_only_thing_that_needs_to_be():
    """Every route reaches the venue through these two functions, so
    gating them gates all of them -- including routes not yet written."""
    import inspect

    from sportsassets import pmus
    for fn in (pmus.submit_fok, pmus.close_position):
        assert "_gate.authorize" in inspect.getsource(fn), fn.__name__


def test_cancellation_is_not_gated_anywhere():
    """Stated as a decision and checked, so it is not 'fixed' later for
    consistency. A paused system must still be able to pull its resting
    orders."""
    import inspect

    from sportsassets import pmus
    assert "_gate.authorize" not in inspect.getsource(pmus.cancel_order)


def test_the_control_matrix_covers_every_lane():
    """Global controls bind every route; copy controls bind everything
    that is not explicitly an operator lane."""
    from sportsassets import execution_gate as gate
    assert set(gate.CONTROLS) == {"global", "copy"}
    # the manual desk is the only lane exempt from the copy breakers
    assert gate.GLOBAL_ONLY_LANES == frozenset({"manual"})
    # and it is still bound by everything global
    assert len(gate.CONTROLS["global"]) >= 2


@pytest.mark.parametrize("module,entry,lane", [
    ("sportsassets/live_executor.py", "execute_manual", "manual"),
    ("sportsassets/live_executor.py", "execute_manual_limit", "manual"),
    ("sportsassets/live_executor.py", "execute_manual_sell", "manual"),
    ("sportsassets/live_executor.py", "maybe_execute", "copy"),
    ("sportsassets/live_executor.py", "mirror_exit", "whale_exit"),
    ("sportsassets/live_executor.py", "execute_copy", "copy"),
])
def test_each_public_route_declares_its_lane(module, entry, lane):
    """Declaring the lane does not loosen anything -- undeclared already
    gets the strictest treatment -- but it is what makes the logs say
    which route was refused."""
    import inspect

    from sportsassets import live_executor as le
    fn = getattr(le, entry)
    src = inspect.getsource(fn)
    assert ('"%s"' % lane) in src, "%s does not declare lane %s" % (entry,
                                                                    lane)


def test_no_order_path_bypasses_the_adapter():
    """A route that built its own venue client would miss the gate
    entirely. _get_client is the only constructor and it lives behind
    the gated functions."""
    sites = _call_sites(("ClobClient", "PolymarketUS"))
    # live_executor builds a py_clob_client for the OTHER venue
    # (polymarket-clob) in _submit_fok. That is a genuine second
    # submission path and it is gated separately -- see the test below.
    # The two api modules build read-only clients: neither contains a
    # post_order, create_order or close_position call, asserted here
    # rather than assumed.
    allowed = {"sportsassets/pmus.py", "sportsassets/live_executor.py",
               "sportsassets/api/pmus_account.py",
               "sportsassets/api/track_record.py"}
    offenders = {s[0] for s in sites} - allowed
    assert not offenders, (
        "venue client constructed outside the known adapters: %s"
        % sorted(offenders))

    for mod in ("sportsassets/api/pmus_account.py",
                "sportsassets/api/track_record.py"):
        src = open(os.path.join(BACKEND, mod)).read()
        for verb in ("post_order", "create_order", "orders.create",
                     "close_position"):
            assert verb not in src, "%s can submit: %s" % (mod, verb)


def test_the_clob_submission_path_is_gated_too():
    """THE ROUTE NO HAND-WRITTEN CENSUS EVER LISTED.

    live_executor._submit_fok does not go through pmus.submit_fok. It
    constructs its own py_clob_client and calls post_order directly, so
    it is a complete second path to a DIFFERENT venue -- and gating the
    pmus boundary did nothing for it. Found by walking the AST for venue
    clients outside the adapter, one day after a hand-written inventory
    of six routes missed it."""
    import inspect

    from sportsassets import live_executor as le
    src = inspect.getsource(le._submit_fok)
    assert "_gate.authorize" in src
    # Match the CALL, not the word: the comment above the gate explains
    # what post_order is, and searching for the bare token finds the
    # prose first and reports the gate as misplaced.
    assert src.index("_gate.authorize") < src.index("client.post_order(")
    assert src.index("_gate.authorize") < src.index("client.create_order(")
