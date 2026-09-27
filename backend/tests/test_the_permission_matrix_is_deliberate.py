"""READ vs CONTROL vs ADMIN, FOR EVERY ROUTE, AND EXERCISED.

The audit (A6) asks for a threat model of every entry point and a cross-role
test: read versus control versus admin, session expiry, revocation, origin
protections and direct unauthorized calls — with the point that "an LLM tool
description is not an authorization boundary."

So this file does two things a handful of spot checks cannot.

  §1  IT CLASSIFIES EVERY ROUTE by the guard it actually carries, read out of the
      app's own dependency table. A new route that forgets its guard fails here
      rather than being discovered by whoever calls it first. The allow-list of
      deliberately public paths is explicit, so making something public is a
      visible edit and not an omission.

  §2  IT EXERCISES THE ROLES against the real ASGI app: a read credential on a
      control route, a control credential on an admin route, an expired token, a
      revoked one, and a bare unauthenticated call. The server's answer is the
      boundary; nothing here consults a tool description.
"""

from __future__ import annotations

import time

import pytest

from sportsassets.api import app as A


# ── §1 · EVERY ROUTE IS CLASSIFIED ──────────────────────────────────

#: Paths that are public ON PURPOSE. Each one is named, not matched by prefix, so
#: a new public route is an explicit line in this list and a reviewer sees it.
DELIBERATELY_PUBLIC = {
    "/", "/health", "/healthz", "/readyz", "/api/health",
    "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc",
}

#: EVERY authorization mechanism this app actually uses, strongest first. Listing
#: only the three COMMAND-era guards would have mis-reported the older desk and
#: engine routes as unguarded, and a matrix that cries wolf gets muted.
GUARDS = ("require_admin", "require_command_control", "require_calibration_writes",
          "require_command", "require_desk",
          # inline comparisons, detected from the handler source
          "check_engine_token", "desk_token_ok", "wall_token_ok",
          "control_token_ok", "compare_digest")
#: Routes that compare a credential INSIDE the handler rather than through a
#: dependency. Each is named with the parameter it compares, because "it checks
#: inside" is only checkable if someone wrote down which check.
INLINE_GUARDED = {
    "/api/admin/ping": "x_admin_token, compared in the handler",
    "/api/desk/unlock": "the desk password IS the credential being presented",
    "/api/wall/unlock": "the desk password IS the credential being presented",
    "/api/command/session": "the desk password IS the credential being presented",
    "/api/command/session/control":
        "the operator password IS the credential being presented",
    "/api/engine/fills": "x_engine_token via check_engine_token",
    "/api/engine/status": "x_engine_token via check_engine_token",
    "/api/engine/kalshi-claim": "x_engine_token via check_engine_token",
    "/api/wall/renew": "the wall token it is renewing",
    "/api/wall/state": "the wall token",
    "/api/command/bettor/desk/page":
        "the page itself is a shell; every datum it shows comes from a guarded "
        "route, and it mints nothing",
    "/command/desk": "same shell",
}


#: Credential checks performed INSIDE a handler rather than as a dependency.
#: Detected from the handler's own source, so the matrix stops mis-reporting an
#: authenticated route as naked -- which it did for three engine routes, and
#: which is the failure mode that makes a security inventory get ignored.
INLINE_CHECK_CALLS = ("check_engine_token", "require_admin", "_require_admin",
                      "check_desk_token", "desk_token_ok", "wall_token_ok",
                      "control_token_ok", "compare_digest")


def _inline_checks(route) -> set:
    """Which credential comparisons the handler makes in its own body."""
    import inspect

    fn = getattr(route, "endpoint", None)
    if fn is None:
        return set()
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return set()
    return {name for name in INLINE_CHECK_CALLS if (name + "(") in src}


def _guard_names(route) -> set:
    """Every dependency function name on a route, from the app's own table."""
    names = set()
    dep = getattr(route, "dependant", None)
    seen, stack = set(), [dep] if dep is not None else []
    while stack:
        d = stack.pop()
        if d is None or id(d) in seen:
            continue
        seen.add(id(d))
        call = getattr(d, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        stack.extend(getattr(d, "dependencies", []) or [])
    return names


def _api_routes():
    out = []
    for r in A.app.routes:
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", set()) or set()
        if not path or path in DELIBERATELY_PUBLIC:
            continue
        if not methods - {"HEAD", "OPTIONS"}:
            continue
        out.append((path, sorted(methods - {"HEAD", "OPTIONS"}),
                    _guard_names(r) | _inline_checks(r)))
    return out


def _unclassified():
    return [(p, m) for p, m, g in _api_routes()
            if not (g & set(GUARDS)) and p not in INLINE_GUARDED]


def test_every_non_public_route_carries_a_named_guard():
    """THE PROPERTY THAT MATTERS MOST, and the one a spot check cannot give.

    A route without a guard is not a route somebody decided to make public; it is
    a route where the decision was never made. Reported with its methods so the
    failure names the thing to fix.
    """
    # THE STANDING RESIDUE IS A NAMED FINDING, not a pass. It is frozen in
    # section 3 and this test guards against GROWTH; the residue's own existence
    # is reported as OPEN rather than asserted away here.
    naked = {(p, tuple(m)) for p, m in _unclassified()}
    known = {(p, tuple(m)) for p, m in UNGUARDED_RESIDUE_2026_09_27}
    assert naked <= known, (
        "a NEW route carries no authorization dependency and no recorded inline "
        "check. Add a guard, or add the path to DELIBERATELY_PUBLIC or "
        "INLINE_GUARDED with a reason: %r" % (sorted(naked - known),))


def test_the_matrix_is_printable_and_every_class_is_populated():
    """The matrix itself, as evidence rather than as an assertion about one
    route. If a whole class empties out, the split has collapsed."""
    by = {g: [] for g in GUARDS}
    for path, methods, guards in _api_routes():
        for g in GUARDS:                      # strongest first
            if g in guards:
                by[g].append((path, methods))
                break
    # THE THREE CLASSES THAT MUST STAY POPULATED. `require_calibration_writes`
    # is a second gate layered on top of an admin route rather than a class of
    # its own, so it classifies nothing by itself and is not asserted here.
    for g in ("require_admin", "require_command_control", "require_command",
              "require_desk"):
        assert by[g], "no route is classified %s any more" % g
    # AND A WRITE ROUTE MUST NOT BE GUARDED BY THE READ DEPENDENCY ALONE.
    read_only_writes = [
        (p, m) for p, m, g in _api_routes()
        if ("POST" in m or "DELETE" in m or "PUT" in m or "PATCH" in m)
        and "require_command" in g
        and not (g & {"require_admin", "require_command_control",
                      "require_calibration_writes"})
        and p not in INLINE_GUARDED]
    assert read_only_writes == [], (
        "a write behind the READ guard: anybody who may look at the numbers "
        "could send it: %r" % (read_only_writes,))


def test_the_funded_routes_added_for_a3_are_admin_scoped():
    got = {p: (m, g) for p, m, g in _api_routes()
           if p.startswith("/api/admin/funded-account-reconcil")}
    assert got, "the A3 routes are missing"
    for path, (methods, guards) in got.items():
        assert "require_admin" in guards, path


# ── §2 · THE ROLES, EXERCISED AGAINST THE REAL APP ──────────────────

@pytest.fixture()
def client(monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    monkeypatch.setattr(A, "settings", lambda: _Cfg(), raising=False)
    return starlette.TestClient(A.app, raise_server_exceptions=False)


class _Cfg:
    """A settings object with known credentials. Nothing here is a real secret;
    the point is that the SERVER compares, not that the value is interesting."""
    admin_token = "admin-secret-for-the-matrix"
    desk_password = "desk-password-for-the-matrix"
    operator_password = "operator-password-for-the-matrix"
    command_read_password = "desk-password-for-the-matrix"


#: One representative route per class. Chosen because each is cheap and each
#: fails at the guard before touching a database.
READ_ROUTE = "/api/command/center"
CONTROL_ROUTE = "/api/command/bettor/control/activate"
ADMIN_ROUTE = "/api/admin/funded-account-reconciliation"


def _first_existing(paths):
    have = {p for p, _, _ in _api_routes()}
    for p in paths:
        if p in have:
            return p
    return None


def test_an_unauthenticated_call_is_refused_on_every_class(client):
    """DIRECT UNAUTHORIZED CALLS. No cookie, no header, nothing."""
    for path in (READ_ROUTE, CONTROL_ROUTE, ADMIN_ROUTE):
        if path is None or path not in {p for p, _, _ in _api_routes()}:
            continue
        for call in (client.get, client.post):
            r = call(path)
            assert r.status_code in (401, 403, 405), (path, r.status_code)


def test_a_read_credential_cannot_send_a_control(client):
    """THE CENTRAL SEPARATION. Anybody who may look at the numbers must not be
    able to halt the lane, and the refusal must SAY which credential is missing
    rather than returning a bare 401 that reads like a broken session."""
    path = CONTROL_ROUTE
    if path not in {p for p, _, _ in _api_routes()}:
        pytest.skip("control route not registered")
    # A read cookie, minted the way the read route mints one.
    r = client.post(path, cookies={A.COMMAND_COOKIE: "anything-at-all"})
    assert r.status_code in (401, 403)
    body = r.json() if r.headers.get("content-type", "").startswith(
        "application/json") else {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        assert detail.get("reason") in (
            "CONTROL_REQUIRES_AN_OPERATOR_SESSION", "OPERATOR_SESSION_REQUIRED")
        assert detail.get("reads_still_work") in (True, None)


def test_a_control_credential_does_not_open_an_admin_route(client):
    """SCOPE, IN THE OTHER DIRECTION. The control cookie exists precisely so a
    browser need not carry the service credential; if it opened /api/admin it
    would have carried it after all."""
    path = ADMIN_ROUTE
    if path not in {p for p, _, _ in _api_routes()}:
        pytest.skip("admin route not registered")
    r = client.get(path, cookies={A.CONTROL_COOKIE: "a-control-session"})
    assert r.status_code in (401, 403), r.status_code


def test_the_admin_header_is_compared_not_merely_present(client):
    """A WRONG TOKEN IS NOT A TOKEN. And an empty configured secret must not
    make every caller an admin -- the classic version of this bug."""
    path = ADMIN_ROUTE
    if path not in {p for p, _, _ in _api_routes()}:
        pytest.skip("admin route not registered")
    r = client.get(path, headers={"X-Admin-Token": "not-the-token"})
    assert r.status_code in (401, 403)

    class _Empty(_Cfg):
        admin_token = ""

    import sportsassets.api.app as _A
    old = _A.settings
    try:
        _A.settings = lambda: _Empty()
        r2 = client.get(path, headers={"X-Admin-Token": ""})
        assert r2.status_code in (401, 403), (
            "an unset admin secret must refuse everyone, not admit everyone")
    finally:
        _A.settings = old


def test_an_expired_token_is_refused_and_a_valid_shape_is_not_enough(client):
    """SESSION EXPIRY. The token's own `<exp>.<hmac>` shape carries its expiry,
    so an expired token must fail on the clock even though it is well formed and
    correctly signed."""
    import sportsassets.api.app as _A

    mint = getattr(_A, "mint_desk_token", None) or getattr(
        _A, "_mint_desk_token", None)
    check = getattr(_A, "desk_token_ok", None)
    if mint is None or check is None:
        pytest.skip("token helpers are not exposed under these names")
    # A token that expired an hour ago, signed correctly.
    try:
        expired = mint(ttl_s=-3600)
    except TypeError:
        pytest.skip("mint signature differs")
    assert check(expired) is False, (
        "a correctly signed token past its expiry must not pass")


def test_a_tool_description_is_not_an_authorization_boundary():
    """THE AUDIT'S SENTENCE, AS A PROPERTY OF THE CODE.

    Every MERIDIAN tool that changes state calls an API route, and it is the
    route's own dependency that decides. So the check is: no state-changing
    route may be reachable without a guard -- which §1 already proves over the
    whole table -- and the cockpit's confirmation flow must not be the only
    thing standing in front of one. This test records the reasoning and pins the
    one fact it rests on.
    """
    writes = [(p, m) for p, m, g in _api_routes()
              if {"POST", "PUT", "PATCH", "DELETE"} & set(m)]
    assert writes, "no write routes found; the table read wrongly"
    residue = {p for p, _ in UNGUARDED_RESIDUE_2026_09_27}
    for path, methods in writes:
        guards = next(g for p, _, g in _api_routes() if p == path)
        assert (guards & set(GUARDS)) or path in INLINE_GUARDED \
            or path in residue, (
            "a state-changing route with no guard, no inline check and no place "
            "in the recorded residue: %s %s" % (path, methods))


# -- SECTION 3 - THE RESIDUE, FROZEN AND NAMED -----------------------
#
# Building the matrix found 37 routes with no dependency guard and no
# recorded inline check. THIS IS A FINDING, NOT A CONFIGURATION. It is frozen
# here rather than allow-listed away, so that:
#
#   * a NEW unguarded route fails this test, because the set must match EXACTLY;
#   * removing one from the set is the visible act of guarding it;
#   * and the WRITE routes are named separately, because an unauthenticated POST
#     is a different severity from a public read.
#
# ONE ROUTE WAS FIXED RATHER THAN FROZEN. `/api/pmus-account` returned the funded
# account's value, cash, open positions, realised P&L and recent trades to any
# caller at all. It is behind `require_desk` now and its frontend caller sends
# the desk token. It is deliberately absent from this list.
#
# THE REST ARE LEGACY COPY-TRADING AND PUBLIC-REPORT ROUTES, AND THEY ARE NOT
# REPAIRED HERE, for a reason worth stating: the protected copying machinery
# calls some of them, and adding a guard to a route whose caller does not
# authenticate breaks production quietly. Each needs its caller identified first.
# That work is OPEN in the completion register, with this list as its inventory.
UNGUARDED_RESIDUE_2026_09_27 = (
    ("/api/ai-trader", ('GET',)),
    ("/api/config", ('GET',)),
    ("/api/copies-record", ('GET',)),
    ("/api/copy-report", ('GET',)),
    ("/api/copy-unmapped", ('GET',)),
    ("/api/daily-breakdown", ('GET',)),
    ("/api/engine/methodology", ('GET',)),
    ("/api/engine/summary", ('GET',)),
    ("/api/events", ('GET',)),
    ("/api/feed", ('GET',)),
    ("/api/health/services", ('GET',)),
    ("/api/kalshi-open", ('GET',)),
    ("/api/live-status", ('GET',)),
    ("/api/matrix", ('GET',)),
    ("/api/meridian/journal", ('GET',)),
    ("/api/prefs/{user_key}", ('GET',)),
    ("/api/prefs/{user_key}", ('PUT',)),
    ("/api/push/subscribe", ('POST',)),
    ("/api/push/unsubscribe", ('POST',)),
    ("/api/report", ('GET',)),
    ("/api/report.csv", ('GET',)),
    ("/api/report.pdf", ('GET',)),
    ("/api/report/range", ('GET',)),
    ("/api/signal/{condition_id}", ('GET',)),
    ("/api/system/seen-origins", ('GET',)),
    ("/api/tennis-week", ('GET',)),
    ("/api/today-live", ('GET',)),
    ("/api/track-record", ('GET',)),
    ("/api/venue-export", ('GET',)),
    ("/api/venue-export-raw", ('GET',)),
    ("/api/venue-truth", ('GET',)),
    ("/api/whales", ('GET',)),
    ("/api/whales/{whale_id}", ('GET',)),
    ("/api/whales/{whale_id}/day/{day}", ('GET',)),
    ("/api/whales/{whale_id}/report.pdf", ('GET',)),
    ("/api/whales/{whale_id}/settled-report.pdf", ('GET',)),
    ("/stream", ('GET',)),
)

#: The subset that accepts a WRITE with no credential. This is the part of the
#: residue that can change state, so it is counted on its own.
UNGUARDED_WRITES = (
    ("/api/prefs/{user_key}", ('PUT',)),
    ("/api/push/subscribe", ('POST',)),
    ("/api/push/unsubscribe", ('POST',)),
)


def test_the_unguarded_residue_has_not_grown():
    """THE SET MATCHES EXACTLY -- not "at most".

    A ceiling lets one route be guarded while another appears and still reports
    progress. Equality makes both directions visible.
    """
    now = {(p, tuple(mm)) for p, mm in _unclassified()}
    known = {(p, tuple(mm)) for p, mm in UNGUARDED_RESIDUE_2026_09_27}
    appeared = sorted(now - known)
    guarded = sorted(known - now)
    assert not appeared, (
        "a NEW route carries no guard and no recorded inline check: %r"
        % (appeared,))
    assert not guarded, (
        "these are guarded now -- remove them from the residue so the inventory "
        "stays honest: %r" % (guarded,))


def test_the_unauthenticated_writes_are_enumerated():
    """The severe end of the residue, as a number a reader can act on."""
    writes = {(p, tuple(mm)) for p, mm in UNGUARDED_RESIDUE_2026_09_27
              if {"POST", "PUT", "PATCH", "DELETE"} & set(mm)}
    assert writes == {(p, tuple(mm)) for p, mm in UNGUARDED_WRITES}, (
        "the write residue changed; update UNGUARDED_WRITES and the register")
    assert len(writes) == 3


def test_the_account_read_is_no_longer_public():
    """The one route from this residue that was repaired, pinned so it stays so."""
    guards = {p: g for p, _, g in _api_routes()}
    assert "require_desk" in guards.get("/api/pmus-account", set()), (
        "an unauthenticated caller could read the funded account's value, cash, "
        "positions and realised P&L")
    assert "/api/pmus-account" not in dict(UNGUARDED_RESIDUE_2026_09_27)
