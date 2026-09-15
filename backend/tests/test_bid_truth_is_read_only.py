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
import builtins
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


class TestItActuallyRuns:
    """The gap that let a NameError reach production.

    Every other test in this file asserts what the endpoint must NOT do.
    None of them asserted that it RUNS, so `ORDER_INTENT_SQL` — which
    app.py imports locally in each caller and not at module level — was
    referenced without an import, raised only when the route was hit, and
    the first deploy answered BIDTRUTHHTTP code=500.

    A prohibition-only test suite is a suite that cannot fail on the most
    common way a new endpoint breaks.
    """

    def test_every_name_it_references_is_actually_bound(self):
        # Walk the function's own Load-context names and confirm each one
        # resolves: a local binding (assignment, import, arg, or
        # comprehension target) or a module global. An unbound one is a
        # NameError waiting for the first request.
        node = _node()
        bound = {a.arg for a in node.args.args}
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                bound.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    bound.add((al.asname or al.name).split(".")[0])
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
        used = {n.id for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        unbound = {u for u in used
                   if u not in bound
                   and not hasattr(app_mod, u)
                   and not hasattr(builtins, u)}
        assert not unbound, (
            f"unbound at runtime -> NameError on first request: {unbound}")

    def test_the_intent_sql_is_imported_not_assumed(self):
        # The specific one that shipped broken. app.py has no top-level
        # ORDER_INTENT_SQL; 5636, 7156 and 7310 each import it locally.
        #
        # Compared by AST position, not by str.index — the first textual
        # occurrence IS the import statement, so a naive index() check
        # measures the import against itself and always fails. (It did.)
        assert not hasattr(app_mod, "ORDER_INTENT_SQL"), \
            "if this becomes a module global, this test is obsolete"
        node = _node()
        imports = [n.lineno for n in ast.walk(node)
                   if isinstance(n, ast.ImportFrom)
                   and any(a.name == "ORDER_INTENT_SQL" for a in n.names)]
        assert imports, "ORDER_INTENT_SQL is used but never imported here"
        uses = [n.lineno for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "ORDER_INTENT_SQL" in n.value]
        uses += [n.lineno for n in ast.walk(node)
                 if isinstance(n, ast.Name) and n.id == "ORDER_INTENT_SQL"]
        assert uses, "expected the SQL to reference it"
        assert min(imports) < max(uses), "referenced before it is imported"


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


class TestItCanActuallyAnswerItsOwnQuestion:
    """The endpoint exists to say whether slug_bid prices these rows.

    It called `slug_bid(us)` with the slug alone, so on the
    shared-identifier family it always returned None — correctly, since
    a slug cannot select a side. The consequence is that the one
    instrument aimed at this question read `slug_bid_today=null` on
    every row whether the resolver was broken or working. Run
    33404785410 shows exactly that: six rn1 rows, all null, on a build
    where the resolver had already been fixed.
    """

    def test_it_passes_the_leg_it_already_knows(self):
        src = _code_only()
        i = src.index("pmus.slug_bid")
        call = src[i:i + 80]
        assert "_leg" in call, (
            "slug_bid is still called with the slug alone, so it must "
            "refuse on every shared-identifier row")

    def test_the_leg_comes_from_the_rows_recorded_intent(self):
        src = _code_only()
        assert "BUY_LONG" in src and "BUY_SHORT" in src

    def test_both_legs_are_reported_side_by_side(self):
        """One number cannot show a swap; two can."""
        src = _code_only()
        assert "bid_if_long" in src and "bid_if_short" in src
