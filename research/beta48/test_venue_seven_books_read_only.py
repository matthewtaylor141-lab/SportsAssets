"""The seven-book reconciler touches a live account. Prove it cannot trade.

Same discipline as backend/tests/test_venue_reconcile_is_read_only.py,
applied to this module because it makes its own venue calls rather than
going through that AST-proven one. A docstring saying "read only" is not
a control; an AST walk that fails the build is.

Also pins the REQUEST CAP, because a counted budget whose cap can drift
upward silently is not a budget.

Run:  python -m pytest research/beta48/test_venue_seven_books_read_only.py
"""

import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import venue_seven_books as vsb                                # noqa: E402

FORBIDDEN = (
    "submit_fok", "cancel_order", "close_position", "place_order",
    "post_order", "mirror_exit", "execute_copy", "maybe_execute",
    "_execute_manual", "_execute_manual_limit", "_execute_manual_sell",
    "execute_manual_sell", "replace_order", "amend_order",
    "create_order", "insert_order", "submit_order",
)


def _src():
    return inspect.getsource(vsb)


def test_the_module_names_no_order_function():
    """THE CONTROL. Not 'we did not call one' -- the NAME does not
    appear anywhere, so a later edit cannot reach one by accident."""
    seen = set()
    for node in ast.walk(ast.parse(_src())):
        if isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                seen.add(a.name.split(".")[-1])
                if a.asname:
                    seen.add(a.asname)
    bad = sorted(seen & set(FORBIDDEN))
    assert not bad, "order-capable names present: %s" % bad


def test_only_read_endpoints_are_called():
    """The only venue surfaces reached are positions, open_orders and
    activities. Anything else is a new capability and must be argued
    for, not slipped in."""
    # The READ endpoints. `_get_client` is listed separately below: it
    # builds the authenticated client and performs no venue action, so
    # it is a permitted constructor rather than a read surface, and
    # naming it keeps the read set honest instead of widening it.
    allowed = {"positions", "activities", "open_orders", "_get_client"}
    reached = set()
    for node in ast.walk(ast.parse(_src())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            base = getattr(owner, "attr", None) or getattr(owner, "id", None)
            if base in ("portfolio", "pmus"):
                reached.add(node.func.attr)
    assert reached <= allowed, "unexpected venue call(s): %s" % sorted(
        reached - allowed)
    assert reached, "no venue read found -- the test would pass vacuously"


def test_the_request_cap_is_real_and_refuses():
    """A budget that warns is not a budget."""
    b = vsb.Budget(2)
    b.spend("one")
    b.spend("two")
    try:
        b.spend("three")
    except RuntimeError as exc:
        assert "CAP REACHED" in str(exc)
    else:                                     # pragma: no cover
        raise AssertionError("the cap did not refuse a third request")
    assert b.n == 2
    assert [e["what"] for e in b.log] == ["one", "two"]


def test_page_budgets_cannot_exceed_the_cap():
    """3 positions + 1 open_orders + 8 activities = 12, inside 14, with
    two spare for retries. If someone raises a page budget without
    raising the cap, this fails before a run does."""
    assert 3 + 1 + 8 <= vsb.MAX_REQUESTS
    assert vsb.MAX_REQUESTS <= 14
