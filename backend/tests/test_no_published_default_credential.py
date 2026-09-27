"""THERE IS NO PUBLISHED DEFAULT CREDENTIAL, AND MISSING CONFIG FAILS CLOSED.

THE DEFECT THESE TESTS PIN, 2026-09-27. `config.Settings` carried two defaults
that are published in this repository:

    admin_token   = "change-me"
    desk_password = "bt"

A production readback then showed `DESK_PASSWORD` was **not set on the service**,
so the credential actually in force for every protected Command Centre read was
the published one. Anyone who read the file could mint a desk token, a wall token
and a COMMAND cookie.

AND I REPORTED IT AS "AN OWNER DECISION TO MAKE". That is wrong on its own terms.
The owner cannot decide their way out of a fallback the code offers; the code has
to stop offering it. It is an authentication defect.

`admin_token` was the worse of the two, and it is the one a "just set
DESK_PASSWORD" fix would have left standing:

  * `require_admin` compares it directly, so an unset environment granted the
    whole admin API to the string "change-me";
  * `require_command` accepts it for the `admin` role;
  * it is the HMAC signing key for every desk, wall and control token, so an
    empty key let a token FORGED with an empty key verify. Two of the three
    verifiers guarded against that. `control_token_ok` -- the one guarding
    WRITES -- did not, and no minter guarded at all.

Each of those is a separate test below, because each is a separate way in.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sportsassets import config
from sportsassets.api import app as A


@pytest.fixture(autouse=True)
def _fresh_settings():
    """`settings()` is lru_cached, so every test clears it before AND after.

    THE UNLOCK THROTTLE IS ALSO CLEARED. The three unlock endpoints share one
    guess-oracle bucket keyed by IP, which is a real and wanted property -- but
    every test here comes from the same client address, so without this the later
    tests get 429 instead of the refusal they are checking. The throttle has its
    own tests; these are about the credential.
    """
    config.settings.cache_clear()
    A._UNLOCK_HITS.clear()
    A._PING_HITS.clear()
    yield
    config.settings.cache_clear()
    A._UNLOCK_HITS.clear()
    A._PING_HITS.clear()


def _with(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    config.settings.cache_clear()


@pytest.fixture
def client():
    # NO LIFESPAN. `with TestClient(...)` runs the app's startup, which opens DB
    # pools and starts the warm loops -- these tests are about the auth gates and
    # must not need a database. Same construction the other API tests use.
    return TestClient(A.app, raise_server_exceptions=False)


# ── 1 · THE DEFAULTS ARE GONE FROM THE SOURCE ────────────────────────

def test_neither_credential_has_a_default_value():
    """The shipped default must be EMPTY, because empty is what every consumer
    refuses on. A non-empty default is a credential in the repository."""
    fields = config.Settings.model_fields
    assert fields["admin_token"].default == ""
    assert fields["desk_password"].default == ""
    # `operator_password` was already correct and must stay that way.
    assert fields["operator_password"].default == ""


def test_the_old_default_strings_appear_nowhere_as_EXECUTABLE_values():
    """A sentinel comparison is not a fix. `/api/admin/ping` used to read
    `expected != "change-me"`, which tolerated the published default as a working
    credential and merely declined to CALL it configured.

    THE SCAN IS OVER EXECUTABLE CODE, NOT PROSE, and this test's first version got
    that wrong -- it matched the comment in `app.py` that explains the defect, and
    the comment is exactly the record we want to keep. So the source is parsed and
    every string LITERAL is examined, which is what "appears as a value" means.
    """
    import ast
    import inspect

    found = []
    for mod in (A, config):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Docstrings are prose too, but a bare string statement is never
                # a credential comparison, so only non-docstring literals matter.
                if node.value.strip() in {"change-me", "bt"}:
                    found.append((mod.__name__, node.lineno, node.value))
    assert found == [], found


# ── 2 · THE DEFAULT FAILS, AND A REAL CREDENTIAL WORKS ───────────────
#
# Both halves matter. A build that refused everything would pass the first and
# be useless.

@pytest.mark.parametrize("path", ["/api/desk/unlock", "/api/wall/unlock",
                                  "/api/command/session"])
def test_the_published_default_password_is_REFUSED(monkeypatch, client, path):
    """With DESK_PASSWORD unset, the old default must not open anything."""
    _with(monkeypatch, DESK_PASSWORD=None, ADMIN_TOKEN="a-real-admin-token")
    r = client.post(path, json={"password": "bt"})
    assert r.status_code == 200          # the endpoint answers ...
    assert r.json().get("ok") is False   # ... and refuses
    assert "bt_command" not in r.cookies


@pytest.mark.parametrize("path", ["/api/desk/unlock", "/api/wall/unlock",
                                  "/api/command/session"])
def test_a_CONFIGURED_password_is_ACCEPTED(monkeypatch, client, path):
    """THE POSITIVE CONTROL. Without this the refusals above prove nothing."""
    _with(monkeypatch, DESK_PASSWORD="a-configured-desk-password",
          ADMIN_TOKEN="a-real-admin-token")
    r = client.post(path, json={"password": "a-configured-desk-password"})
    assert r.status_code == 200
    assert r.json().get("ok") is True, r.json()


def test_an_unset_desk_password_refuses_EVERY_password_including_empty(
        monkeypatch, client):
    """Not configured is not 'accepts anything' and not 'accepts empty'."""
    _with(monkeypatch, DESK_PASSWORD=None, ADMIN_TOKEN="a-real-admin-token")
    for guess in ("", "bt", "admin", "password", "a-configured-desk-password"):
        r = client.post("/api/command/session", json={"password": guess})
        assert r.json().get("ok") is False, guess


def test_an_unset_admin_token_refuses_the_old_default_and_everything_else(
        monkeypatch, client):
    """The admin route is the severe one: the default granted the whole admin
    API."""
    _with(monkeypatch, ADMIN_TOKEN=None)
    for guess in ("change-me", "", "admin"):
        r = client.get("/api/admin/funded-account-registry",
                       headers={"X-Admin-Token": guess})
        assert r.status_code == 401, guess


def test_a_configured_admin_token_is_accepted(monkeypatch, client):
    """The positive control for the admin gate."""
    _with(monkeypatch, ADMIN_TOKEN="a-real-admin-token")
    r = client.get("/api/admin/funded-account-registry",
                   headers={"X-Admin-Token": "a-real-admin-token"})
    assert r.status_code != 401


# ── 3 · NO TOKEN CAN BE MINTED OR VERIFIED WITHOUT A SIGNING KEY ─────

def test_no_session_token_can_be_MINTED_without_a_signing_key(monkeypatch):
    """Previously every minter signed with the EMPTY key and returned a token."""
    from fastapi import HTTPException

    _with(monkeypatch, ADMIN_TOKEN=None)
    for mint in (A.mint_desk_token, A.mint_wall_token, A.mint_control_token):
        with pytest.raises(HTTPException) as e:
            mint()
        assert e.value.status_code == 503, mint.__name__
        # 503 AND NOT 401: the caller's credential is not the problem.
        assert "not configured" in str(e.value.detail)


@pytest.mark.parametrize("verify,scope", [
    ("desk_token_ok", "desk"),
    ("wall_token_ok", "wall"),
    ("control_token_ok", "control"),
])
def test_a_token_FORGED_with_an_empty_key_never_verifies(monkeypatch, verify,
                                                        scope):
    """THE HOLE `control_token_ok` HAD. With an unset ADMIN_TOKEN the key is
    empty, so anybody could compute the HMAC -- and control tokens are WRITES."""
    import hashlib
    import hmac

    _with(monkeypatch, ADMIN_TOKEN=None)
    exp = int(time.time()) + 600
    if scope == "control":
        material = ("%s:%d" % (A.CONTROL_SCOPE, exp)).encode()
        sig = hmac.new(b"", material, hashlib.sha256).hexdigest()
        forged = "%s.%d.%s" % (A.CONTROL_SCOPE, exp, sig)
    else:
        sig = hmac.new(b"", ("%s:%d" % (scope, exp)).encode(),
                       hashlib.sha256).hexdigest()
        forged = "%d.%s" % (exp, sig)
    assert getattr(A, verify)(forged) is False


def test_a_genuine_token_DOES_verify_under_a_configured_key(monkeypatch):
    """The positive control for the guards above."""
    _with(monkeypatch, ADMIN_TOKEN="a-real-admin-token")
    tok, _ = A.mint_desk_token()
    assert A.desk_token_ok(tok) is True
    wtok, _ = A.mint_wall_token()
    assert A.wall_token_ok(wtok) is True
    ctok, _ = A.mint_control_token()
    assert A.control_token_ok(ctok) is True


# ── 4 · ROTATING THE KEY INVALIDATES EVERY EXISTING SESSION ──────────

def test_rotating_the_admin_token_invalidates_tokens_minted_under_the_old_one(
        monkeypatch):
    """THE SESSION-INVALIDATION MECHANISM, AND IT IS WHY THE FIX IS SAFE.

    Every desk, wall and control token is an HMAC keyed by `admin_token`, so
    changing that key revokes every session ever minted -- including any minted
    with the published default password during the exposure window. There is no
    session store to purge.
    """
    _with(monkeypatch, ADMIN_TOKEN="the-old-key")
    old_desk, _ = A.mint_desk_token()
    old_wall, _ = A.mint_wall_token()
    old_ctl, _ = A.mint_control_token()
    assert A.desk_token_ok(old_desk) and A.wall_token_ok(old_wall)
    assert A.control_token_ok(old_ctl)

    _with(monkeypatch, ADMIN_TOKEN="the-new-key")
    assert A.desk_token_ok(old_desk) is False
    assert A.wall_token_ok(old_wall) is False
    assert A.control_token_ok(old_ctl) is False


# ── 5 · THE THREE BOUNDARIES STILL HOLD ──────────────────────────────

def test_a_read_credential_cannot_CONTROL_and_a_control_token_cannot_READ(
        monkeypatch):
    """The scopes were separate before this change and must stay separate: a
    read token must not open a write, and a control token must not open a read.
    Fixing an authentication defect must not quietly merge two roles."""
    _with(monkeypatch, ADMIN_TOKEN="a-real-admin-token")
    desk, _ = A.mint_desk_token()
    ctl, _ = A.mint_control_token()

    # A READ token is not a CONTROL token.
    assert A.control_token_ok(desk) is False
    # A CONTROL token is not a READ token -- the scope is in the signed
    # material, so its signature never matches a read challenge.
    assert A.desk_token_ok(ctl) is False
    assert A.wall_token_ok(ctl) is False


def test_the_command_read_role_still_refuses_an_unconfigured_admin_token(
        monkeypatch, client):
    """`require_command` accepts the admin token for the `admin` role. With no
    configured token that path must be unreachable rather than open."""
    _with(monkeypatch, ADMIN_TOKEN=None, DESK_PASSWORD=None)
    r = client.get("/api/command/bettor/desk",
                   headers={"X-Admin-Token": "change-me"})
    assert r.status_code == 401


def test_a_desk_session_opens_a_command_read_when_properly_configured(
        monkeypatch, client):
    """END TO END, AND THE POSITIVE CONTROL FOR THE WHOLE FIX: a real password
    mints a real session, and that session alone opens a read.

    THE SESSION IS PRESENTED AS THE HEADER, NOT THE COOKIE, and that is a
    transport fact rather than a weaker test. The cookie is set `Secure`, so the
    test client -- which speaks plain HTTP to `testserver` -- will not send it
    back; asserting on it would be asserting about httpx. The cookie's
    ATTRIBUTES are checked separately below, and the token inside it is the same
    stateless HMAC either way.
    """
    _with(monkeypatch, DESK_PASSWORD="a-configured-desk-password",
          ADMIN_TOKEN="a-real-admin-token")
    r = client.post("/api/command/session",
                    json={"password": "a-configured-desk-password"})
    assert r.json().get("ok") is True
    # THE TOKEN IS NEVER RETURNED IN THE BODY.
    assert "token" not in r.json()

    # The Set-Cookie header carries the session, HttpOnly, Secure and scoped.
    raw = r.headers.get("set-cookie", "")
    assert A.COMMAND_COOKIE in raw
    assert "HttpOnly" in raw
    assert "Secure" in raw
    assert "Path=/api/command" in raw

    # And the session it minted does open a read.
    token = raw.split(A.COMMAND_COOKIE + "=", 1)[1].split(";", 1)[0]
    assert A.desk_token_ok(token) is True
    got = client.get("/api/command/shadow/health",
                     headers={"X-Desk-Token": token})
    assert got.status_code != 401


# ── 6 · THE POSTURE IS REPORTED, NOT SILENT ──────────────────────────

def test_credential_posture_names_what_is_unset_and_what_refuses(monkeypatch):
    _with(monkeypatch, ADMIN_TOKEN=None, DESK_PASSWORD=None,
          OPERATOR_PASSWORD=None)
    p = config.credential_posture()
    assert p["all_configured"] is False
    assert set(p["not_configured"]) == {"admin_token", "desk_password",
                                        "operator_password"}
    # Each one says what refuses in its absence, so the report is actionable.
    assert "refuses 401" in p["credentials"]["admin_token"]["if_absent"]
    assert "HMAC signing key" in p["credentials"]["admin_token"]["if_absent"]
    assert "COMMAND cookie" in p["credentials"]["desk_password"]["if_absent"]
    assert "control token" in p["credentials"]["operator_password"]["if_absent"]


def test_the_posture_returns_NO_secret_material(monkeypatch):
    """Not the value, not its length, not a fingerprint. A length is a hint
    about a password and a short secret's hash is brute-forceable."""
    _with(monkeypatch, ADMIN_TOKEN="super-secret-admin-token",
          DESK_PASSWORD="super-secret-desk-password")
    blob = repr(config.credential_posture())
    assert "super-secret" not in blob
    for row in config.credential_posture()["credentials"].values():
        assert set(row) == {"configured", "state", "if_absent"}
        assert isinstance(row["configured"], bool)


def test_healthz_publishes_the_posture_without_authentication(monkeypatch,
                                                             client):
    """Visible from outside, so the gap cannot hide again. Publishing WHICH
    doors are locked is not a leak -- the 401 says the same thing."""
    _with(monkeypatch, ADMIN_TOKEN=None, DESK_PASSWORD=None)
    body = client.get("/healthz").json()
    assert body["auth_all_configured"] is False
    assert "admin_token" in body["auth_not_configured"]
    assert "desk_password" in body["auth_not_configured"]

    _with(monkeypatch, ADMIN_TOKEN="a-real-admin-token",
          DESK_PASSWORD="a-configured-desk-password",
          OPERATOR_PASSWORD="a-configured-operator-password")
    body = client.get("/healthz").json()
    assert body["auth_all_configured"] is True
    assert body["auth_not_configured"] == []


# ── 7 · THE SESSION EPOCH REVOKES WITHOUT ROTATING THE KEY ───────────

def test_bumping_the_session_epoch_invalidates_every_outstanding_token(
        monkeypatch):
    """THE MECHANISM THAT MAKES REVOCATION POSSIBLE WITHOUT BREAKING VERIFICATION.

    Rotating `admin_token` revokes every session -- and it is also the credential
    the authorized verification workflows present, held as a GitHub secret. So
    rotating it on the service to revoke sessions would break the route used to
    prove the revocation landed.

    The epoch is inside the signed material instead. It is a counter, not a
    secret, so it travels by the ordinary env route and is safe to print.
    """
    _with(monkeypatch, ADMIN_TOKEN="an-unchanged-admin-token", SESSION_EPOCH="1")
    desk, _ = A.mint_desk_token()
    wall, _ = A.mint_wall_token()
    ctl, _ = A.mint_control_token()
    assert A.desk_token_ok(desk) and A.wall_token_ok(wall)
    assert A.control_token_ok(ctl)

    # THE SIGNING KEY IS UNCHANGED. Only the epoch moves.
    _with(monkeypatch, ADMIN_TOKEN="an-unchanged-admin-token", SESSION_EPOCH="2")
    assert A.desk_token_ok(desk) is False
    assert A.wall_token_ok(wall) is False
    assert A.control_token_ok(ctl) is False

    # AND NEW SESSIONS STILL WORK -- a revocation that bricked sign-in would be
    # an outage, not a revocation.
    fresh, _ = A.mint_desk_token()
    assert A.desk_token_ok(fresh) is True
    fresh_ctl, _ = A.mint_control_token()
    assert A.control_token_ok(fresh_ctl) is True


def test_the_epoch_defaults_to_a_value_and_is_never_empty(monkeypatch):
    """An unset or blank epoch must not make the signed material ambiguous: two
    deployments reading "" and "1" differently would silently reject each other's
    tokens."""
    for value in (None, "", "   "):
        _with(monkeypatch, ADMIN_TOKEN="a-real-admin-token", SESSION_EPOCH=value)
        assert A._session_epoch() == "1"


def test_the_epoch_is_NOT_treated_as_a_secret(monkeypatch):
    """It is a counter. Treating it as a secret would push it into the private
    provisioning path, where a value nobody may read is a value nobody can bump."""
    from sportsassets import config

    assert "session_epoch" not in config.CREDENTIAL_CONSEQUENCES
    _with(monkeypatch, SESSION_EPOCH="7")
    assert "session_epoch" not in config.credential_posture()["credentials"]
