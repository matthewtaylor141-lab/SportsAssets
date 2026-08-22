"""Desk unlock (owner directive 2026-08-22): password -> stateless 12h
HMAC token, admin token still accepted, guesses throttled. Tokens must
verify on any instance with no session store and die at expiry."""

import asyncio
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sportsassets.api import app as app_mod
from sportsassets.api.app import (DeskUnlockBody, desk_token_ok,
                                  desk_unlock, mint_desk_token,
                                  require_desk)
from sportsassets.config import settings


def test_mint_verify_round_trip():
    token, exp = mint_desk_token()
    assert desk_token_ok(token)
    assert exp == int(token.split(".")[0])
    assert exp > time.time() + 11 * 3600


def test_expired_token_refused():
    token, exp = mint_desk_token(now=time.time() - 13 * 3600)
    assert exp < time.time()
    assert not desk_token_ok(token)


def test_tampered_token_refused():
    token, _ = mint_desk_token()
    exp, _, sig = token.partition(".")
    # A pushed-out expiry with the old signature must not verify.
    assert not desk_token_ok(f"{int(exp) + 3600}.{sig}")
    assert not desk_token_ok(f"{exp}." + "0" * 64)
    assert not desk_token_ok("garbage")
    assert not desk_token_ok("")


def test_require_desk_accepts_token_and_admin_fallback():
    token, _ = mint_desk_token()
    require_desk(x_desk_token=token, x_admin_token="")     # no raise
    require_desk(x_desk_token="",                          # no raise
                 x_admin_token=settings().admin_token)
    with pytest.raises(HTTPException) as exc:
        require_desk(x_desk_token="", x_admin_token="")
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException):
        require_desk(x_desk_token="123.bad", x_admin_token="wrong")


def _req(ip: str) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"x-forwarded-for", ip.encode())],
                    "client": (ip, 1234), "query_string": b""})


def test_unlock_mints_a_working_token():
    r = asyncio.run(desk_unlock(
        _req("10.0.0.1"), DeskUnlockBody(password=settings().desk_password)))
    assert r["ok"] is True
    assert desk_token_ok(r["token"])
    assert r["expires_at"] > time.time()


def test_unlock_wrong_password_shape():
    r = asyncio.run(desk_unlock(
        _req("10.0.0.2"), DeskUnlockBody(password="nope")))
    assert r == {"ok": False, "error": "wrong password"}


def test_unlock_rate_limit_shape():
    ip = "10.9.9.9"
    for _ in range(10):
        r = asyncio.run(desk_unlock(_req(ip), DeskUnlockBody(password="x")))
        assert r["ok"] is False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(desk_unlock(_req(ip), DeskUnlockBody(password="x")))
    assert exc.value.status_code == 429
    # Another IP is unaffected — the throttle is per-IP.
    r = asyncio.run(desk_unlock(_req("10.9.9.10"),
                                DeskUnlockBody(password="x")))
    assert r["ok"] is False
    app_mod._UNLOCK_HITS.clear()
