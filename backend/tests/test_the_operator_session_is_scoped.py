"""THE BROWSER OPERATES THE DESK WITHOUT A SERVICE CREDENTIAL.

WHY THIS FILE EXISTS. The desk's first control wiring asked the operator to
paste the ADMIN token into the page. That is the entire admin API living in
a browser tab, readable by any script on the page and sittable in a history
entry -- and it is the same credential that approves funded limits. The
scoped operator session replaces it: a short-lived, HttpOnly, path-scoped
cookie that opens the desk's control actions and NOTHING ELSE.

WHAT IS ASSERTED HERE, and why each one matters:

  * the scope is INSIDE the signed material, so a read (desk) token can
    never satisfy a control challenge and a control token can never satisfy
    an admin gate. Two credentials that differ only by which handler looks
    at them are one credential.
  * a read session is refused for a WRITE by name, with its reads intact.
  * no credential at all is 401, not a silent 404.
  * an UNCONFIGURED operator password refuses by name. A default operator
    password would be worse than having no control sign-in at all.
  * the token is never in a response body, and the SHIPPED page carries no
    admin-token field and no service credential.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from sportsassets.api import app as A
from sportsassets.api import desk_page as DP


# ── 1 · THE TWO SCOPES ARE NOT INTERCHANGEABLE ──────────────────────

def test_a_control_token_verifies_and_a_desk_token_does_not(monkeypatch):
    """The scope is signed, so the two token families cannot cross over."""
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())

    ctl, exp = A.mint_control_token()
    assert ctl.startswith(A.CONTROL_SCOPE + ".")
    assert exp > time.time()
    assert A.control_token_ok(ctl) is True

    desk, _ = A.mint_desk_token()
    # A READ token presented at the control gate: refused. Not by a name
    # check -- the signature itself does not match a control challenge.
    assert A.control_token_ok(desk) is False
    # And the control token is not a read token either.
    assert A.desk_token_ok(ctl) is False


def test_a_control_token_is_not_the_admin_token(monkeypatch):
    """`require_admin` must not accept it. Otherwise the scoping is a
    label: the browser's cookie would open funded-limit approval."""
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    ctl, _ = A.mint_control_token()
    assert ctl != "service-credential"
    with pytest.raises(HTTPException) as e:
        A.require_admin(x_admin_token=ctl)
    assert e.value.status_code in (401, 403)


def test_a_control_token_expires(monkeypatch):
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    ctl, exp = A.mint_control_token(now=1_000_000)
    assert A.control_token_ok(ctl, now=1_000_000 + 10) is True
    assert A.control_token_ok(ctl, now=exp) is False
    assert A.control_token_ok(ctl, now=exp + 1) is False
    assert A.CONTROL_TOKEN_TTL_S <= 3600.0, "a control session is short"


def test_a_tampered_control_token_fails(monkeypatch):
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    ctl, exp = A.mint_control_token()
    scope, _, sig = ctl.split(".")
    # A later expiry with the original signature: refused.
    assert A.control_token_ok("%s.%d.%s" % (scope, exp + 6000, sig)) is False
    # The right shape, a wrong signature: refused.
    assert A.control_token_ok("%s.%d.%s" % (scope, exp, "0" * 64)) is False
    # A control token signed under another key: refused.
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "a-different-key"})())
    assert A.control_token_ok(ctl) is False


# ── 2 · THE GATE'S THREE ANSWERS ────────────────────────────────────

def test_the_operator_cookie_opens_a_control_and_names_its_role(monkeypatch):
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    ctl, _ = A.mint_control_token()
    assert A.require_command_control(x_admin_token="", x_desk_token="",
                                           bt_control=ctl, bt_command="") == "operator"


def test_ops_tooling_may_still_use_the_service_credential(monkeypatch):
    """CI and ops hold the token server-side already; refusing it there
    would only push them to invent a second path."""
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    assert A.require_command_control(
        x_admin_token="service-credential", x_desk_token="",
        bt_control="", bt_command="") == "admin"


def test_a_read_session_is_refused_for_a_write_by_name(monkeypatch):
    """403, naming the refusal and saying the reads still work. A viewer of
    the numbers must not thereby be able to halt the lane."""
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    desk, _ = A.mint_desk_token()
    with pytest.raises(HTTPException) as e:
        A.require_command_control(x_admin_token="", x_desk_token="",
                                  bt_control="", bt_command=desk)
    assert e.value.status_code == 403
    d = e.value.detail
    assert d["reason"] == "CONTROL_REQUIRES_AN_OPERATOR_SESSION"
    assert d["reads_still_work"] is True
    assert d["no_service_credential_is_needed_in_the_browser"] is True
    # The same refusal for the header form of the read credential.
    with pytest.raises(HTTPException) as e2:
        A.require_command_control(x_admin_token="", x_desk_token=desk,
                                  bt_control="", bt_command="")
    assert e2.value.status_code == 403


def test_no_credential_at_all_is_401_and_says_what_to_do(monkeypatch):
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    with pytest.raises(HTTPException) as e:
        A.require_command_control(x_admin_token="", x_desk_token="",
                                  bt_control="", bt_command="")
    assert e.value.status_code == 401
    assert e.value.detail["reason"] == "OPERATOR_SESSION_REQUIRED"
    assert "session/control" in e.value.detail["what"]


def test_a_wrong_admin_token_does_not_pass_as_a_read_session(monkeypatch):
    monkeypatch.setattr(A, "settings", lambda: type(
        "S", (), {"admin_token": "service-credential"})())
    with pytest.raises(HTTPException) as e:
        A.require_command_control(x_admin_token="guess", x_desk_token="",
                                  bt_control="", bt_command="")
    assert e.value.status_code == 401


# ── 3 · THE SIGN-IN ROUTE'S OWN CONTRACT ────────────────────────────

def test_the_control_sign_in_route_is_not_admin_gated():
    """It must be reachable by a browser holding nothing -- that is the
    whole point -- so it carries no `require_admin` dependency."""
    route = next(r for r in A.app.routes
                 if getattr(r, "path", "") == "/api/command/session/control")
    deps = [getattr(d.dependency, "__name__", "")
            for d in getattr(route, "dependencies", [])]
    assert "require_admin" not in deps
    assert "require_command_control" not in deps


class _FakeRequest:
    """Enough of a Request for the shared unlock throttle to key on."""

    headers = {"x-forwarded-for": "203.0.113.9"}
    client = None


class _FakeResponse:
    def __init__(self):
        self.cookies = {}

    def set_cookie(self, name, value, **kw):
        self.cookies[name] = (value, kw)


def test_an_unconfigured_operator_password_refuses_by_name():
    """A DEFAULT would be worse than no control sign-in at all."""
    import asyncio

    class _S:
        admin_token = "service-credential"
        operator_password = ""

    real = A.settings
    A.settings = lambda: _S()
    try:
        with pytest.raises(HTTPException) as e:
            asyncio.run(A.command_session_control(
                request=_FakeRequest(), response=_FakeResponse(),
                body=type("B", (), {"password": "anything"})()))
        assert e.value.status_code == 503
        assert e.value.detail["reason"] == "OPERATOR_PASSWORD_NOT_CONFIGURED"
        assert "OPERATOR_PASSWORD" in str(e.value.detail)
    finally:
        A.settings = real
        A._UNLOCK_HITS.clear()


def test_a_wrong_operator_password_mints_nothing():
    """The refusal must not set the cookie, and must not distinguish itself
    from a right password by anything but its status."""
    import asyncio

    class _S:
        admin_token = "service-credential"
        operator_password = "the-real-one"

    real = A.settings
    A.settings = lambda: _S()
    try:
        resp = _FakeResponse()
        with pytest.raises(HTTPException) as e:
            asyncio.run(A.command_session_control(
                request=_FakeRequest(), response=resp,
                body=type("B", (), {"password": "not-it"})()))
        assert e.value.status_code == 401
        assert e.value.detail["reason"] == "OPERATOR_PASSWORD_NOT_ACCEPTED"
        assert e.value.detail["cookie_set"] is False
        assert resp.cookies == {}

        # And the right password mints the SCOPED cookie, HttpOnly, on the
        # command path, with no token in the body.
        ok = asyncio.run(A.command_session_control(
            request=_FakeRequest(), response=resp,
            body=type("B", (), {"password": "the-real-one"})()))
        assert ok["ok"] is True
        assert ok["scope"] == A.CONTROL_SCOPE
        assert ok["token_in_body"] is False
        assert "token" not in ok
        val, kw = resp.cookies[A.CONTROL_COOKIE]
        assert kw["httponly"] is True and kw["secure"] is True
        assert kw["path"] == "/api/command"
        assert A.control_token_ok(val) is True
        # It opens a control and it is NOT the admin credential.
        assert A.require_command_control(
            x_admin_token="", x_desk_token="",
            bt_control=val, bt_command="") == "operator"
        with pytest.raises(HTTPException):
            A.require_admin(x_admin_token=val)
    finally:
        A.settings = real
        A._UNLOCK_HITS.clear()


def test_the_operator_password_is_read_from_the_service_environment():
    """It lives in the settings object, so it is an environment value on the
    service and never a literal in this repository."""
    from sportsassets import config as CFG

    src = __import__("inspect").getsource(CFG)
    assert "operator_password" in src
    # It is a settings field, so it is read from the environment, and its
    # DEFAULT is empty -- an unset password refuses rather than opening.
    assert CFG.Settings.model_fields["operator_password"].default == ""


# ── 4 · NOTHING SENSITIVE IS SHIPPED IN THE PAGE ────────────────────

def test_the_shipped_page_has_no_admin_credential_and_no_token_field():
    html = DP.DESK_PAGE_HTML
    assert "X-Admin-Token" not in html
    assert "x-admin-token" not in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    # The operator signs in with a PASSWORD field, and the cookie carries
    # the authorisation from there on.
    assert 'id="oppass"' in html
    assert 'type="password"' in html
    assert "/api/command/session/control" in html
    # No token is ever put in a URL by the page.
    assert "token=" not in html


def test_the_control_session_response_never_carries_the_token():
    """The cookie is HttpOnly; a token echoed in the body would put it back
    within reach of any script on the page."""
    import inspect

    src = inspect.getsource(A.command_session_control)
    assert '"token_in_body": False' in src
    assert "httponly=True" in src
    assert "secure=True" in src
    assert 'path="/api/command"' in src
    # The returned dict names the scope and what it does NOT open.
    assert "does_not_open" in src
    assert "/api/admin" in src
