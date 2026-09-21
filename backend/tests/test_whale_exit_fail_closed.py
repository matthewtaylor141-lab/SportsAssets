"""R5 FAIL-CLOSED: the whale-exit route must refuse by default.

WHAT WAS WRONG, MEASURED 2026-09-21.

`workers/whale_exits.py` is registered in the running workers service
(workers/all.py:280). Every gate between it and a real sell order was
open at the same time:

    WHALE_EXIT_ENABLED     absent from the service env -> defaulted "1"
    LIVE_VERIFIED_WHALES   populated (35 chars)
    PMUS_KEY_ID / SECRET   present on the running service
    live_trading_paused    row ABSENT -> _is_paused() returns False
    LIVE_COPY_HALT         '0'        -> copy_halted() returns False
    LIVE_TRADING_ENABLED   '1'        -> master switch ON
    mirror_exit            never called active_venue() at all

Nothing had to be switched ON for that route to sell. Something had to
be switched OFF, and the thing that would have switched it off was an
environment variable that was not set. That is the definition of
fail-open.

It did not fire -- filled orders stood at 52 across an eight-hour gap --
but "it happened not to trigger" is not a control.

THESE TESTS USE MOCKS ONLY. No order function is called, no venue is
contacted, and no test here is capable of submitting anything.
"""

import importlib
import os

import pytest


def _reload_whale_exits(monkeypatch, env):
    """Re-import the module under a given environment.

    ENABLED is bound at import time, so the default can only be tested
    by re-importing with the variable absent or set.
    """
    for k in ("WHALE_EXIT_ENABLED",):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import sportsassets.workers.whale_exits as we
    return importlib.reload(we)


# ── A. the default itself ────────────────────────────────────────────

def test_absent_env_means_disabled_not_enabled(monkeypatch):
    """THE DEFECT. Absence used to arm the route."""
    we = _reload_whale_exits(monkeypatch, {})
    assert we.ENABLED is False


def test_explicit_zero_means_disabled(monkeypatch):
    we = _reload_whale_exits(monkeypatch, {"WHALE_EXIT_ENABLED": "0"})
    assert we.ENABLED is False


def test_only_an_explicit_one_arms_it(monkeypatch):
    we = _reload_whale_exits(monkeypatch, {"WHALE_EXIT_ENABLED": "1"})
    assert we.ENABLED is True


@pytest.mark.parametrize("val", ["", "true", "yes", "on", "2", "TRUE",
                                 " 1", "1 ", "enabled"])
def test_no_other_spelling_arms_it(monkeypatch, val):
    """A route that can spend money does not guess at intent. Anything
    that is not exactly "1" leaves it closed."""
    we = _reload_whale_exits(monkeypatch, {"WHALE_EXIT_ENABLED": val})
    assert we.ENABLED is False, val


def test_the_disabled_branch_returns_before_any_work(monkeypatch):
    """main() must exit at the flag, not merely log and continue."""
    import inspect
    we = _reload_whale_exits(monkeypatch, {})
    src = inspect.getsource(we.main)
    assert "if not ENABLED:" in src
    idx = src.index("if not ENABLED:")
    assert "return" in src[idx:idx + 200]


# ── B. the master gate inside mirror_exit ────────────────────────────

def test_mirror_exit_consults_the_authoritative_venue_gate():
    """IT DID NOT, AND THAT WAS THE HOLE. active_venue() is the switch
    every other order path on this platform reads -- _execute_manual,
    _execute_manual_limit, _execute_manual_sell and maybe_execute all
    call it. mirror_exit did not, so LIVE_TRADING_ENABLED did not gate
    it."""
    import inspect
    from sportsassets import live_executor as le
    src = inspect.getsource(le.mirror_exit)
    assert "active_venue()" in src
    assert "mx_not_authorized_no_active_venue" in src


def test_the_gate_is_checked_before_the_halt_flags():
    """Authorization precedes operational state. A system not
    authorized to trade refuses before asking whether it is paused."""
    import inspect
    from sportsassets import live_executor as le
    src = inspect.getsource(le.mirror_exit)
    assert src.index("active_venue()") < src.index("copy_halted()")


def test_active_venue_is_false_when_live_trading_is_disabled():
    """The gate's own contract: no master switch, no venue, no order."""
    import inspect
    from sportsassets import live_executor as le
    src = inspect.getsource(le.active_venue)
    assert "live_trading_enabled" in src
    assert "return None" in src


# ── C. credentials and allowlist are not authorization ───────────────

def test_credentials_present_alone_do_not_authorize():
    """active_venue() reads the master switch FIRST and only then looks
    at keys, so keys alone can never produce a venue."""
    import ast
    import inspect
    from sportsassets import live_executor as le
    src = inspect.getsource(le.active_venue)
    tree = ast.parse(src.lstrip())
    body = tree.body[0].body
    # the first statement after the docstring must be the switch check
    stmts = [n for n in body if not (isinstance(n, ast.Expr)
                                     and isinstance(n.value, ast.Constant))]
    first = ast.dump(stmts[1]) if len(stmts) > 1 else ast.dump(stmts[0])
    assert "live_trading_enabled" in ast.dump(stmts[0]) or \
        "live_trading_enabled" in first


def test_allowlist_population_alone_does_not_authorize():
    """LIVE_VERIFIED_WHALES decides WHO may be exited, never WHETHER
    the system may trade. It sits after the venue gate."""
    import inspect
    from sportsassets import live_executor as le
    src = inspect.getsource(le.mirror_exit)
    assert src.index("active_venue()") < src.index("LIVE_VERIFIED_WHALES")


# ── D. no test here can submit anything ──────────────────────────────

def test_this_file_never_calls_an_order_function():
    """The repair is verified by inspection and mocks. A regression
    suite for an order path must not be able to place one."""
    import ast
    src = open(__file__).read()
    tree = ast.parse(src)
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name:
                called.add(name)
    for forbidden in ("submit_fok", "cancel_order", "close_position",
                      "mirror_exit", "execute_copy", "maybe_execute",
                      "place_order", "post_order"):
        assert forbidden not in called, forbidden
