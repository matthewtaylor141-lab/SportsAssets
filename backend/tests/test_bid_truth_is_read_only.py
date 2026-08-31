"""The bid-truth endpoint is an INSTRUMENT, not a trading path.

It exists because mirror_exit prices every partial sale off pmus.slug_bid,
slug_bid returns None on this venue's whole shared-identifier family, and
the obvious fix is the dangerous one: markets.bbo carries no side dimension,
so pricing a SHORT position off a LONG-side bid sets an IOC sell floor at
the complement's price — roughly a 75% giveaway on our own position.

So the endpoint reads. It must never acquire the ability to write, and the
day someone adds an order call to it is the day a diagnostic becomes a
money path without a review. Pinned here rather than trusted to the
docstring, which is the same mistake in prose form.
"""

import ast
import inspect

from sportsassets.api import app as app_mod


def _src():
    return inspect.getsource(app_mod.api_bid_truth)


def _node():
    """The function's AST, found by name in the module.

    Not by string-slicing the source — the first version of this test did
    that, produced a fragment starting mid-signature, and failed on a
    SyntaxError that said nothing about the endpoint. A test that breaks
    on its own parsing teaches people to delete the test.
    """
    tree = ast.parse(inspect.getsource(app_mod))
    for n in ast.walk(tree):
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) \
                and n.name == "api_bid_truth":
            return n
    raise AssertionError("api_bid_truth not found in the module")


def _code_only():
    """Source with the docstring removed.

    The prose explains WHY the dangerous fix is dangerous, so it names
    mirror_exit and submit_fok on purpose. Checking raw text would flag
    the explanation and miss nothing real — so strip it and check code.
    """
    n = _node()
    body = n.body[1:] if (n.body and isinstance(n.body[0], ast.Expr)
                          and isinstance(n.body[0].value, ast.Constant)
                          and isinstance(n.body[0].value.value, str)) else n.body
    return "\n".join(ast.unparse(b) for b in body)


class TestItCannotTrade:
    def test_it_calls_no_order_placing_function(self):
        # submit_fok and close_position are the ONLY two lines in this
        # codebase that send an order (live_executor:1152-1158). Neither
        # may ever appear in this function's CODE.
        code = _code_only()
        for banned in ("submit_fok", "close_position", "cancel_order",
                       "place_order", "mirror_exit"):
            assert banned not in code, f"{banned} in a read-only endpoint"

    def test_it_writes_nothing_to_the_database(self):
        code = _code_only()
        for banned in ("INSERT ", "UPDATE ", "DELETE ", "pool.execute"):
            assert banned not in code, f"{banned} in a read-only endpoint"

    def test_only_read_methods_on_the_venue_client(self):
        # Checked by AST attribute name rather than substring, so a
        # rename cannot slip a writer past a grep.
        attrs = {n.attr for n in ast.walk(_node())
                 if isinstance(n, ast.Attribute)}
        suspicious = {a for a in attrs
                      if a.endswith("order") or a.startswith("submit")
                      or a.startswith("place") or a.startswith("cancel")}
        assert not suspicious, f"order-shaped calls: {suspicious}"
        assert "retrieve_by_slug" in attrs or "slug_bid" in attrs, \
            "it should still be reading the venue at all"

    def test_it_is_admin_gated(self):
        # Everything under /api/admin carries require_admin; a bid probe
        # that leaked our open positions to the public web would be a
        # position disclosure, not a diagnostic.
        whole = inspect.getsource(app_mod)
        i = whole.index('@app.get("/api/admin/bid-truth"')
        head = whole[i:i + 220]
        assert "require_admin" in head


class TestItAnswersTheQuestionItWasBuiltFor:
    def test_it_reports_the_side_attribution(self):
        src = _src()
        # The whole point: say which leg the venue's bid belongs to, and
        # which leg WE hold, so the two can be compared.
        assert "attrib" in src
        assert "our_side_is_long" in src
        assert "bestBid" in src and "bestAsk" in src

    def test_it_reads_slugs_we_actually_hold(self):
        # A bid read on a market we do not hold cannot answer "what would
        # we sell into" — that is what the public SDK already failed at.
        src = _src()
        assert "status = 'filled'" in src
        assert "live_orders" in src

    def test_it_shows_what_the_broken_function_returns_today(self):
        # slug_bid returning None beside a book that HAS a bid is the
        # defect stated as a measurement rather than an argument.
        assert "slug_bid_today" in _src()
