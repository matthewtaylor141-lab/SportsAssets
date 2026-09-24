"""CAN THE EXTERNAL SHADOW PATH PLACE A FUNDED ORDER? Verified, not asserted.

THE CORRECTION THIS FILE EXISTS FOR. I previously offered
`external_valuations.order_submitted` having a CHECK that it is FALSE as
evidence that no order could be submitted. That is not evidence. A CHECK
constrains what a row may say; it says nothing about whether a network
request left the process. A funded order could be placed and simply never
recorded, and the CHECK would be satisfied.

AND THE IMPORT CLOSURE IS NOT CLEAN. The loop reads the venue book through
`pmus.book_read`, and `sportsassets.pmus` ALSO defines `submit_fok`. So a
funded-order function is one attribute access away from the shadow loop's
own imports. "No submit appears in this module's source" is therefore a
weaker claim than it sounds, and stating it alone would be the same
mistake in a new place.

WHAT ACTUALLY STOPS IT, in the order a request would meet it:

  1. NO CALL SITE. The loop never calls a submit function. Checked over
     the module's real source, including the transitive helper it uses.
  2. THE VENUE-BOUNDARY GATE. `pmus.submit_fok` calls
     `execution_gate.authorize("submit", ...)` as its FIRST act, before
     the client is even constructed, and denial RAISES. The gate is read
     at the moment of submission and never carried in by a caller, so
     queued or retried work cannot present a stale token. A lane that
     never declared itself is treated as 'unknown' and still gets the
     copy controls -- the gate's own comment says the undeclared route
     must not be the one that escapes.
  3. A FAIL-CLOSED READ. With no pool bound, the gate cannot read the
     kill switch and decides on an empty snapshot rather than proceeding.

These are tested below. (2) is the load-bearing one, and it is a property
of the venue boundary rather than of this experiment -- which is the point:
the control does not depend on the new code being well behaved.
"""

from __future__ import annotations

import inspect

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets.workers import ext_pinnacle_loop as loop

FUNDED_CALLS = (
    "submit_fok", "close_position", "post_order", "create_order",
    "place_order", "cancel_order", "submit(", "OrderArgs",
)


def _sources():
    """The loop's own source plus the helpers it actually defines."""
    return {"ext_pinnacle_loop": inspect.getsource(loop),
            "bettor_external_shadow": inspect.getsource(ext)}


def test_no_funded_call_site_exists_in_the_shadow_path():
    for name, src in _sources().items():
        for bad in FUNDED_CALLS:
            assert bad not in src, "%s reaches %s" % (name, bad)


def test_the_venue_module_is_reached_only_through_its_reader():
    """`pmus` IS imported -- for `book_read`. This pins that the only
    attributes taken off it are the reader and its client, because the same
    module also defines `submit_fok`.

    Read with the AST rather than by string search: a comment mentioning
    `pmus.submit_fok` would fool a grep in either direction, and what
    matters is the attribute the code actually accesses.
    """
    import ast

    tree = ast.parse(inspect.getsource(loop))
    used = {n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name) and n.value.id == "pmus"}
    assert used == {"book_read", "_get_client"}, (
        "only the reader and its client may be taken off pmus, got %r"
        % (sorted(used),))


def test_the_submit_function_authorizes_before_anything_else():
    """The control is at the VENUE boundary, so it holds for any caller,
    including one written later that forgets to be careful.

    The first executable statement is found with the AST, so a docstring
    of any shape cannot be mistaken for code.
    """
    import ast
    import textwrap

    from sportsassets import pmus

    fn = ast.parse(textwrap.dedent(
        inspect.getsource(pmus.submit_fok))).body[0]
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and \
            isinstance(body[0].value, ast.Constant) and \
            isinstance(body[0].value.value, str):
        body = body[1:]                      # the docstring
    assert body, "submit_fok has no body"
    first = body[0]
    src = ast.dump(first)
    assert "authorize" in src, (
        "the authorization must be the first executable statement, got:\n%s"
        % ast.unparse(first))
    assert "'submit'" in ast.unparse(first) or '"submit"' in ast.unparse(first)


def test_denial_raises_rather_than_returning_a_value():
    """A route cannot proceed by ignoring a return value."""
    from sportsassets import execution_gate as gate

    assert issubclass(gate.Denied, Exception)
    src = inspect.getsource(gate._decide)
    assert "raise Denied(" in src


def test_an_undeclared_lane_still_gets_the_controls():
    from sportsassets import execution_gate as gate

    src = inspect.getsource(gate.authorize)
    assert "unknown" in src, (
        "an undeclared lane must not be the one that escapes")


def test_the_gate_without_a_pool_does_not_default_to_yes():
    """Fail-closed. With nothing bound, there is no kill switch to read,
    and the answer must not be permission."""
    from sportsassets import execution_gate as gate

    src = inspect.getsource(gate.authorize_async)
    # With no pool, an EMPTY Snapshot is built and handed to the same
    # _decide as any other read -- it does not short-circuit to a yes.
    assert "if pool is None:" in src
    assert "snap = Snapshot()" in src
    assert "return _decide(snap, operation, lane, slug)" in src
    # And an empty snapshot is not permissive: a fresh Snapshot must not
    # report a venue it never read.
    empty = gate.Snapshot()
    assert not getattr(empty, "copy_enabled", False)
    assert not getattr(empty, "live_enabled", False)


def test_the_record_cannot_claim_a_submitted_order_either():
    """The CHECK is kept -- as a BACKSTOP, which is all it ever was. It
    catches a writer that lies; it does not stop a request."""
    rec = ext.evaluate(
        contract={"venue": "V", "condition_id": "c", "selection": "A",
                  "sport_family": "soccer", "market": "h2h",
                  "period": "FULL_GAME", "line": None,
                  "settlement_rule": "R", "event_key": "e"},
        quote={"book": "pinnacle",
               "outcomes": {"A": 1.5, "B": 3.0, "C": 4.0},
               "observed_at": 1000.0, "received_at": 1000.0,
               "event_key": "e", "period": "FULL_GAME", "line": None,
               "settlement_rule": "R"},
        market_state={"ask": 0.05, "depth": 500.0, "readable": True},
        execution_estimate={"p_fill": 0.9, "basis": "TEST",
                            "crossing": True},
        size=1.0, risk={"permitted": True}, fee_fn=lambda qty, price: 0.0,
        now=1001.0, outcome_books=4, armed=True)
    assert rec["order_submitted"] is False
    assert rec["shadow_only"] is True
    # And there is no code path that sets it True.
    assert "order_submitted\"] = True" not in inspect.getsource(ext)
    assert "order_submitted=True" not in inspect.getsource(ext)


def test_p_fill_is_never_defaulted_into_certainty():
    """The loop supplies p_fill=None with a named basis. If it ever
    supplied a number, every modelled fill would silently become a
    prediction rather than an acknowledged unknown."""
    src = inspect.getsource(loop)
    assert '"p_fill": None' in src
    assert "P_FILL_NOT_IDENTIFIED" in src
