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
        # ── ADDED 2026-09-26, AND HERE IS THE DECISION THIS FILE ASKS FOR ──
        #
        # `bettor_funded_execution` is the EV lane's funded path: a qualifying
        # external-valuation decision -> account binding -> owner-approved
        # limits -> authorization -> pmus.submit_fok. It is a genuine new
        # order route and this census went red when it was written, which is
        # what the file is for.
        #
        # THE CONTROLS IT GETS, asserted in
        # `test_the_funded_ev_route_declares_its_controls` below:
        #   * everything at the boundary, like every other route: the
        #     execution_gate `submit` authorization inside pmus.submit_fok,
        #     which denies by RAISING and is read at submission time;
        #   * FOUR additional disablements of its own, the first two being
        #     code constants that are currently False;
        #   * a per-order rail check against the OWNER-APPROVED effective
        #     limits before the adapter is reached at all -- which no other
        #     route on this list has, because no other route is funded by an
        #     owner-approved pilot set.
        #
        # It is the only route here that cannot reach the venue at all in the
        # shipped build.
        "sportsassets/bettor_funded_execution.py",
        # ── ADDED 2026-09-27: THE FUNDED SERVICING LANE ────────────────────
        #
        # `bettor_funded_management` sends two things and only two: an EXIT
        # (`pmus.submit_fok(..., sell=True)`) and a CANCEL. Both REDUCE
        # exposure and neither can open a position -- `submit_exit` refuses
        # anything larger than the residual inventory the book records, and it
        # never invents an exit price.
        #
        # WHY IT IS A SEPARATE ROUTE FROM THE ENTRY CONNECTOR, which is the
        # decision this census exists to make explicit: it has its OWN switch,
        # `FUNDED_EXIT_SUBMISSION_ENABLED`, because stopping new exposure must
        # never strand inventory. With the entry switch off and this one on the
        # lane can only wind down. Both are False in the shipped build, and the
        # settlement and status reconciliation beside them submit nothing at
        # all, so they are reads and are never gated.
        "sportsassets/bettor_funded_management.py",
        # ── ADDED 2026-10-02: THE 1:1,000 EXECUTION MIRROR ─────────────────
        #
        # `execmirror` trades a SEPARATE, freshly provisioned Polymarket US
        # account, owner-authorized for live execution at 1/1,000 of each new
        # paper order. It deliberately does NOT go through pmus: pmus holds
        # the funded account's credential and gate, and the mirror must never
        # be able to reach that account. THE CONTROLS IT GETS, asserted in
        # `test_the_execution_mirror_declares_its_controls` below: its own
        # credential names only; its own durable control row, OFF by
        # default; an emergency stop; the account fingerprint checked every
        # cycle; a per-order notional cap; and a cutover so nothing historical
        # is replayed. Its close_position call is the emergency-stop flatten.
        "sportsassets/execmirror.py",
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
    # `venue_sdk.py` NAMES THE CLASS WITHOUT CONSTRUCTING ONE. It exists to
    # report what the installed SDK does -- its pinned version, whether our
    # retry setting took, which methods it retries -- and it reads that from
    # `inspect.signature(PolymarketUS.__init__)`. Reading a constructor's
    # signature is not building a client and cannot send anything.
    #
    # THIS TEST WAS FAILING ON IT, and the failure was real in the sense that
    # the census had not been told about a module that names a venue class.
    # Allowing it silently would be the wrong repair, so it is allowed HERE and
    # then held to the same positive assertions as the read-only api modules
    # below: no submitting verb, and no actual construction.
    allowed = {"sportsassets/pmus.py", "sportsassets/live_executor.py",
               "sportsassets/api/pmus_account.py",
               "sportsassets/api/track_record.py",
               "sportsassets/venue_sdk.py",
               # the execution mirror's read-only account probe (asserted
               # below to hold no submitting verb) and the mirror lane
               # itself (its controls asserted in
               # test_the_execution_mirror_declares_its_controls)
               "sportsassets/execmirror_probe.py",
               "sportsassets/execmirror.py",
               # (210) the same-book probe's KEYLESS retail client: public
               # gateway only, every transport wrapped GET-only and gated
               # (institutional_same_book.install_read_only); held below to
               # the same no-submitting-verb assertion as the read-only
               # api modules
               "sportsassets/institutional_same_book.py"}
    offenders = {s[0] for s in sites} - allowed
    assert not offenders, (
        "venue client constructed outside the known adapters: %s"
        % sorted(offenders))

    for mod in ("sportsassets/api/pmus_account.py",
                "sportsassets/api/track_record.py",
                "sportsassets/execmirror_probe.py",
                "sportsassets/institutional_same_book.py"):
        src = open(os.path.join(BACKEND, mod)).read()
        for verb in ("post_order", "create_order", "orders.create",
                     "close_position"):
            assert verb not in src, "%s can submit: %s" % (mod, verb)

    # ── `venue_sdk` IS CHECKED ON ITS AST, NOT ITS TEXT ──────────────
    #
    # A text search is the right check for the two api modules: they have no
    # reason to name a submitting verb at all. It is the WRONG check here,
    # because this module's job is to DOCUMENT the installed SDK's surface --
    # its docstring lists `orders.{cancel,close_position,create,...}` and its
    # code reads `inspect.signature(PolymarketUS.__init__)`. A text search
    # cannot tell a documented method name from a called one, and the first
    # attempt at this repair failed on exactly that: it flagged a docstring.
    #
    # So the assertion is that no such NAME IS CALLED and no venue client is
    # CONSTRUCTED. Naming a thing is not doing it; calling it is.
    tree = ast.parse(open(os.path.join(BACKEND,
                                       "sportsassets/venue_sdk.py")).read())
    submitting = ("post_order", "create_order", "close_position",
                  "submit_fok", "preview")
    called = sorted({
        "%s:%d" % (getattr(n.func, "attr", None) or getattr(n.func, "id", ""),
                   n.lineno)
        for n in ast.walk(tree) if isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) in submitting
             or getattr(n.func, "id", None) in submitting)})
    assert not called, (
        "venue_sdk CALLS a submitting method: %s. It is a reporter" % called)
    built = sorted({
        "%s:%d" % (getattr(n.func, "id", ""), n.lineno)
        for n in ast.walk(tree) if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) in ("PolymarketUS", "AsyncPolymarketUS",
                                            "ClobClient")})
    assert not built, (
        "venue_sdk constructs a venue client at %s; it must only read the "
        "signature" % built)


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


def test_the_funded_ev_route_declares_its_controls():
    """THE DECISION THE CENSUS DEMANDS, WRITTEN AS ASSERTIONS.

    `bettor_funded_execution` was added to the inventory above. A new order
    route is only allowed onto that list once someone says which controls bind
    it, so this test is that statement -- and it fails if any of them is
    removed later.
    """
    import inspect

    from sportsassets import bettor_entry_execution as EX
    from sportsassets import bettor_funded_activation as FA
    from sportsassets import bettor_funded_execution as FX
    from sportsassets import pmus

    src = inspect.getsource(FX)

    # 1 · THE BOUNDARY'S OWN GATE, unchanged and not bypassed. This route
    #     reaches the venue only through submit_fok, which authorizes.
    assert "_gate.authorize" in inspect.getsource(pmus.submit_fok)
    assert "submit_fok" in FX.ADAPTER_SURFACE
    assert FX.ADAPTER_MODULE == "sportsassets.pmus"
    # IT CONSTRUCTS NO CLIENT OF ITS OWN -- checked on the parsed module, not
    # on its text. The module NAMES `_get_client` in its docstring (as the
    # chain it goes through) and in `describe()["transport_seam_for_tests"]`
    # (so a test knows where to cut). Naming a seam is not using it, and a
    # textual check here would forbid the documentation rather than the call.
    called = {getattr(n.func, "attr", None) or getattr(n.func, "id", None)
              for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    for banned in ("ClobClient", "PolymarketUS", "_get_client"):
        assert banned not in called, banned

    # 2 · FUNDED CLASS ONLY, and disjoint from the test-venue executor
    assert FX.ALLOWED_VENUE_CLASSES == (FA.VENUE_FUNDED,)

    # 3 · ITS OWN FOUR DISABLEMENTS, two of them code constants that are off
    d = {x["what"]: x for x in FX.disablements()}
    assert len(d) == 4
    assert d["FUNDED_SUBMISSION_ENABLED"]["value"] is False
    assert d["REAL_ORDER_SUBMISSION_ENABLED"]["value"] is False
    assert "execution_gate" in d
    assert "PMUS_KEY_ID / PMUS_SECRET_KEY" in d
    for x in d.values():
        assert x.get("cleared_by"), x

    # 4 · THE AUTHORIZATION GATE IS CONSULTED, and its answer is required
    assert "authorize_submission" in src
    assert "authorization_consumed" in src
    assert FX.R_NOT_AUTHORIZED in (FX.describe()["refusals"])

    # 5 · A PER-ORDER RAIL CHECK AGAINST THE OWNER-APPROVED SET, which is
    #     unique to this route on the census
    assert "MAX_MARKET_EXPOSURE" in src
    assert FX.R_OVER_RAIL in FX.describe()["refusals"]
    assert "effective_limits" in src

    # 6 · AND IT WRITES NO ROWS, so it cannot record an order it did not send
    assert "INSERT INTO" not in src.upper()
    assert EX.REAL_ORDER_SUBMISSION_ENABLED is False


def test_the_execution_mirror_declares_its_controls():
    """The decision the census asks for, for the 1:1,000 mirror lane."""
    src = open(os.path.join(PKG, "execmirror.py")).read()
    # its own credential, never the funded one, and never through pmus
    assert "PMUS_KEY_ID" not in src and "pmus_key_id" not in src
    assert "from . import pmus" not in src and "import pmus\n" not in src
    from sportsassets import execmirror_probe as EP
    assert (EP.KEY_ID_ENV, EP.SECRET_ENV) == ("PMUS_EXECMIRROR_KEY_ID",
                                             "PMUS_EXECMIRROR_SECRET_KEY")
    # its own control row, off by default, with a stop and a cap
    mig = open(os.path.join(BACKEND, "migrations",
                            "192_execution_mirror.sql")).read()
    assert "enabled             boolean     NOT NULL DEFAULT false" in mig
    assert "stopped" in mig and "max_order_usd" in mig and "cutover_at" in mig
    # every cycle: disabled -> nothing; another account -> halt; stop -> stop
    assert "if not ctl.get(\"enabled\"):" in src
    assert "HALTED_ACCOUNT_CHANGED" in src and "emergency_stop" in src
    assert "ABOVE_ORDER_CAP" in src and "cutover_at" in src
    # the flatten (close_position) is reachable only from the emergency stop
    tree = ast.parse(src)
    owners = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = ast.unparse(fn)
            if "close_position" in body or ".close," in body:
                owners.append(fn.name)
    assert sorted(set(owners)) == ["close", "emergency_stop"], owners


def test_the_p5_focus_universe_and_c12_proof_paths_are_not_order_routes():
    """cand24 added the stream's focus universe, its worker path and the C12
    decision-time proof. None is an order route: no submitting or cancelling
    call, no stored reference to one, no `.place(`."""
    new = {"sportsassets/institutional_focus_universe.py",
           "sportsassets/p5_c12_proof.py",
           "sportsassets/institutional_same_book.py",
           "sportsassets/institutional_api_stream.py",
           "sportsassets/workers/institutional_md.py",
           "sportsassets/p5_runtime.py"}
    sites = _call_sites(SUBMITTING + CANCELLING + ("place",))
    assert not [s for s in sites if s[0] in new], [
        s for s in sites if s[0] in new]
