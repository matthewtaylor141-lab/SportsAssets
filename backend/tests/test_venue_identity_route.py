"""The identity gate: focused checks on the one thing it must not do.

WHAT THIS GUARDS. `account_identity()` answers "is the authenticated
account the one we think it is?" That answer is only worth anything if
the value it compares against comes from somewhere the venue response
cannot reach. A check that pulls `expected` out of the same payload it
is checking matches trivially and proves nothing -- it is the classic
check that cannot fail.

So these tests pin three properties:

  1. The reference is OUR CONFIGURATION, not the response and not a
     caller-supplied parameter.
  2. No identifier, reference or credential is ever returned -- only a
     verdict and a field NAME.
  3. An unset reference is a BLOCKER (`no_expected`), never a pass.

Run:  python -m pytest backend/tests/test_venue_identity_route.py
"""

import ast
import inspect

import pytest

from sportsassets.api import reconcile_read as rr


# ── 1. the reference cannot come from the response ───────────────────

def test_no_expected_when_nothing_is_configured():
    """An unset reference must BLOCK, not wave through."""
    out = rr.account_identity(None)
    assert out["verdict"] in ("no_expected", "no_identity", "unreadable"), out
    assert out["verdict"] != "match", "an unset reference must never match"


def test_match_requires_the_configured_value(monkeypatch):
    """A real match is reported, and only the FIELD NAME comes back."""
    monkeypatch.setattr(rr, "_client", lambda: _FakeClient(
        {"balances": [{"accountId": "ACCT-TRUSTED-1", "cash": "123.45"}]}))
    out = rr.account_identity("ACCT-TRUSTED-1")
    assert out["verdict"] == "match"
    assert out.get("field") == "accountId"
    assert "ACCT-TRUSTED-1" not in _flat(out)


def test_a_different_account_is_a_mismatch_and_leaks_nothing(monkeypatch):
    monkeypatch.setattr(rr, "_client", lambda: _FakeClient(
        {"balances": [{"accountId": "ACCT-SOMEONE-ELSE"}]}))
    out = rr.account_identity("ACCT-TRUSTED-1")
    assert out["verdict"] == "mismatch"
    flat = _flat(out)
    assert "ACCT-SOMEONE-ELSE" not in flat, "the other account leaked"
    assert "ACCT-TRUSTED-1" not in flat, "our reference leaked"


def test_absent_identity_field_is_a_blocker_not_a_pass(monkeypatch):
    """The venue exposing nothing to compare is the blocker we already
    hit on /api/desk/accounts. It must stay a blocker."""
    monkeypatch.setattr(rr, "_client", lambda: _FakeClient(
        {"balances": [{"cash": "123.45", "currency": "USD"}]}))
    out = rr.account_identity("ACCT-TRUSTED-1")
    assert out["verdict"] == "no_identity"
    assert out["verdict"] != "match"


def test_an_unreadable_call_is_not_an_empty_account(monkeypatch):
    class Boom:
        @property
        def account(self):
            raise RuntimeError("venue down")

    monkeypatch.setattr(rr, "_client", lambda: Boom())
    out = rr.account_identity("ACCT-TRUSTED-1")
    assert out["verdict"] == "unreadable"


# ── 2. the route wires the reference to CONFIG, not to a caller ──────

def test_the_route_takes_no_caller_supplied_reference():
    """If the route ever grows a query parameter for `expected`, a
    caller could hand it whatever makes the check pass."""
    import sportsassets.api.app as app_mod

    fn = getattr(app_mod, "api_venue_identity", None)
    assert fn is not None, "the identity route is not defined"
    params = list(inspect.signature(fn).parameters)
    assert params == [], (
        "the identity route must take no parameters; got %s" % params)


def test_the_route_reads_the_reference_from_settings():
    """The comparison value must come from configuration."""
    import sportsassets.api.app as app_mod

    src = inspect.getsource(app_mod.api_venue_identity)
    tree = ast.parse(src.strip())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "pmus_account_ref" in attrs, (
        "the route does not read pmus_account_ref from settings")
    # and it must not be reaching into the venue response for it
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for banned in ("request", "body", "payload", "resp"):
        assert banned not in names, (
            "the route reads '%s'; the reference must come from "
            "configuration only" % banned)


def test_the_reference_setting_defaults_to_empty():
    """Defaulting to a value would make every deployment claim a match
    it has not earned."""
    from sportsassets.config import Settings

    assert Settings.model_fields["pmus_account_ref"].default == ""


# ── helpers ──────────────────────────────────────────────────────────

class _FakeAccount:
    def __init__(self, payload):
        self._p = payload

    def balances(self):
        return self._p


class _FakeClient:
    def __init__(self, payload):
        self.account = _FakeAccount(payload)


def _flat(obj) -> str:
    return repr(obj)
