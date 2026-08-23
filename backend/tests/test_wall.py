"""TV wall (2026-08-23): desk password -> stateless 7-day READ-ONLY
HMAC token, scope-separated from desk tokens ('wall:' vs 'desk:') so
neither kind ever verifies as the other; renewal only rolls a still-
valid wall token; and every mutating desk endpoint refuses the wall
role with a 403 before touching any money path."""

import asyncio
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sportsassets.api import app as app_mod
from sportsassets.api.app import (CashOutBody, DeskUnlockBody,
                                  ManualTradeBody, WallBroadcastBody,
                                  api_desk_cancel_manual_order,
                                  api_desk_cash_out, api_manual_trade,
                                  desk_token_ok, mint_desk_token,
                                  mint_wall_token, require_desk,
                                  wall_broadcast, wall_renew, wall_state,
                                  wall_token_ok, wall_unlock)
from sportsassets.config import settings


# ── Token scope discipline ───────────────────────────────────────────


def test_mint_verify_round_trip():
    token, exp = mint_wall_token()
    assert wall_token_ok(token)
    assert exp == int(token.split(".")[0])
    assert exp > time.time() + 6 * 24 * 3600     # ~7 days, not 12h


def test_expired_wall_token_refused():
    token, exp = mint_wall_token(now=time.time() - 8 * 24 * 3600)
    assert exp < time.time()
    assert not wall_token_ok(token)


def test_tampered_wall_token_refused():
    token, _ = mint_wall_token()
    exp, _, sig = token.partition(".")
    # A pushed-out expiry with the old signature must not verify.
    assert not wall_token_ok(f"{int(exp) + 3600}.{sig}")
    assert not wall_token_ok(f"{exp}." + "0" * 64)
    assert not wall_token_ok("garbage")
    assert not wall_token_ok("")


def test_scopes_never_cross():
    # A desk token is not a wall token and vice versa — even though
    # both are minted from the same key, the scope string separates
    # them cryptographically, not by convention.
    desk, _ = mint_desk_token()
    wall, _ = mint_wall_token()
    assert desk_token_ok(desk) and not wall_token_ok(desk)
    assert wall_token_ok(wall) and not desk_token_ok(wall)


def test_require_desk_roles():
    wall, _ = mint_wall_token()
    desk, _ = mint_desk_token()
    assert require_desk(x_desk_token=wall, x_admin_token="") == "wall"
    assert require_desk(x_desk_token=desk, x_admin_token="") == "desk"
    # Admin still wins outright, whatever rides the desk header.
    assert require_desk(x_desk_token=wall,
                        x_admin_token=settings().admin_token) == "admin"
    with pytest.raises(HTTPException) as exc:
        require_desk(x_desk_token="123.bad", x_admin_token="")
    assert exc.value.status_code == 401


# ── Unlock ───────────────────────────────────────────────────────────


def _req(ip: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"x-forwarded-for", ip.encode())],
                    "client": (ip, 1234), "query_string": b""})


def test_wall_unlock_mints_a_wall_token():
    r = asyncio.run(wall_unlock(
        _req("10.7.0.1"), DeskUnlockBody(password=settings().desk_password)))
    assert r["ok"] is True
    assert wall_token_ok(r["token"])
    assert not desk_token_ok(r["token"])        # wall unlock ≠ desk grant
    assert r["expires_at"] > time.time() + 6 * 24 * 3600
    app_mod._UNLOCK_HITS.clear()


def test_wall_unlock_wrong_password_shape():
    r = asyncio.run(wall_unlock(_req("10.7.0.2"),
                                DeskUnlockBody(password="nope")))
    assert r == {"ok": False, "error": "wrong password"}
    app_mod._UNLOCK_HITS.clear()


def test_wall_unlock_shares_the_desk_throttle_bucket():
    ip = "10.7.9.9"
    for _ in range(10):
        r = asyncio.run(wall_unlock(_req(ip), DeskUnlockBody(password="x")))
        assert r["ok"] is False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wall_unlock(_req(ip), DeskUnlockBody(password="x")))
    assert exc.value.status_code == 429
    app_mod._UNLOCK_HITS.clear()


# ── Renew ────────────────────────────────────────────────────────────


def test_renew_rolls_a_valid_wall_token():
    old, old_exp = mint_wall_token(now=time.time() - 4 * 24 * 3600)
    assert wall_token_ok(old)                   # 3 days left
    r = asyncio.run(wall_renew(x_desk_token=old))
    assert r["ok"] is True
    assert wall_token_ok(r["token"])
    assert r["expires_at"] > old_exp            # fresh 7 days, not the stub


def test_renew_refuses_desk_tokens_and_garbage():
    desk, _ = mint_desk_token()
    for bad in (desk, "garbage", "", "123.bad"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wall_renew(x_desk_token=bad))
        assert exc.value.status_code == 401


def test_renew_refuses_expired_wall_token():
    dead, _ = mint_wall_token(now=time.time() - 8 * 24 * 3600)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wall_renew(x_desk_token=dead))
    assert exc.value.status_code == 401


# ── State & broadcast ────────────────────────────────────────────────


@pytest.fixture()
def fresh_wall_state():
    saved = dict(app_mod._wall_state)
    app_mod._wall_state.update({"mode": "book", "from": None,
                                "to": None, "set_at": None})
    yield app_mod._wall_state
    app_mod._wall_state.clear()
    app_mod._wall_state.update(saved)


def test_state_defaults_to_book(fresh_wall_state):
    assert asyncio.run(wall_state()) == {
        "mode": "book", "from": None, "to": None, "set_at": None}


def test_broadcast_as_desk_flips_the_state(fresh_wall_state):
    body = WallBroadcastBody.model_validate(
        {"mode": "report", "from": "2026-08-01", "to": "2026-08-22"})
    r = asyncio.run(wall_broadcast(body, role="desk"))
    assert r["ok"] is True
    assert r["mode"] == "report"
    assert r["from"] == "2026-08-01" and r["to"] == "2026-08-22"
    assert r["set_at"] == pytest.approx(time.time(), abs=5)
    assert asyncio.run(wall_state())["mode"] == "report"


def test_broadcast_as_wall_is_403(fresh_wall_state):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wall_broadcast(WallBroadcastBody(mode="report"),
                                   role="wall"))
    assert exc.value.status_code == 403
    assert exc.value.detail == "wall is read-only"
    # ...and the refusal must not have leaked into shared state.
    assert asyncio.run(wall_state())["mode"] == "book"


def test_broadcast_bad_mode_rejected(fresh_wall_state):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wall_broadcast(WallBroadcastBody(mode="disco"),
                                   role="desk"))
    assert exc.value.status_code == 400
    assert asyncio.run(wall_state())["mode"] == "book"


# ── Wall role is read-only on every mutating desk endpoint ───────────
# Bodies are valid-shaped so the 403 is the gate speaking, not the
# validators — and the gate must fire before any money path runs (no
# DB pool, no venue HTTP: these calls would explode otherwise).


def _wall_403(coro):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 403
    assert exc.value.detail == "wall is read-only"


def test_manual_trade_is_403_for_wall():
    _wall_403(api_manual_trade(
        ManualTradeBody(usd=5.0, venue="kalshi", ticker="KXMLBGAME-A",
                        side="yes"),
        role="wall"))


def test_cash_out_is_403_for_wall():
    _wall_403(api_desk_cash_out(
        CashOutBody(venue="kalshi", ticker="KXMLBGAME-A", qty=1),
        role="wall"))


def test_cancel_manual_order_is_403_for_wall():
    _wall_403(api_desk_cancel_manual_order(1, role="wall"))
