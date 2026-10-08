"""FAILED X-Admin-Token ATTEMPTS ARE THROTTLED (2026-10-08).

WHY. A read-only preview run (frontend-preview 37781022031) showed that the
credential production accepts as `X-Admin-Token` is two characters long.
`require_admin` and `require_command` compare that header directly against
`settings().admin_token`, which is also the HMAC key every desk, wall and
control token is signed with, and nothing limited how many wrong values a
caller could try. Only the owner may rotate the key (owner directive: no key
rotation without explicit authorization). That is the fix, and it has been
reported as an owner blocker. This module is the code-side mitigation that
changes no credential: it slows guessing.

WHAT. Before a request with a non-empty `X-Admin-Token` is handled:
  * the CORRECT token is never throttled (constant-time compare first), so
    the workflows that hold the real credential keep working during an
    attack on it;
  * a WRONG token is recorded against the calling client, and once a client
    has PER_CLIENT_LIMIT wrong attempts inside WINDOW_S, or all clients
    together have GLOBAL_LIMIT, further wrong attempts are refused 429
    ADMIN_TOKEN_ATTEMPTS_THROTTLED without reaching any route.
The client is the RIGHTMOST X-Forwarded-For hop: Render's edge appends the
connecting address, so the rightmost value is the one a caller cannot choose
(the leftmost is whatever the caller sent, and rotating it would reset a
leftmost-keyed budget). The global ceiling covers a caller who spreads guesses
over many real addresses.

It returns and logs no secret material: not the value, not its length.
Throttling cannot make a two-character secret safe; it only stretches the
time a guess takes. Rotation is the fix.
"""
from __future__ import annotations

import hmac
import logging
import time

log = logging.getLogger(__name__)

WINDOW_S = 600.0
PER_CLIENT_LIMIT = 10
GLOBAL_LIMIT = 60
R_THROTTLED = "ADMIN_TOKEN_ATTEMPTS_THROTTLED"
#: the map of clients is bounded: stale clients are dropped past this size
MAX_CLIENTS = 2000
#: a signing key shorter than this is reported in the private log at boot
MIN_SIGNING_KEY_CHARS = 32

_FAILS: dict[str, list[float]] = {}
_GLOBAL: list[float] = []


def client_key(headers, client_host: str | None) -> str:
    hops = [h.strip() for h in (headers.get("x-forwarded-for") or "")
            .split(",") if h.strip()]
    return hops[-1] if hops else (client_host or "?")


def _recent(ts: list[float], now: float) -> list[float]:
    return [t for t in ts if now - t < WINDOW_S]


def token_ok(supplied: str, expected: str) -> bool:
    supplied, expected = (supplied or "").strip(), (expected or "").strip()
    return bool(expected) and hmac.compare_digest(supplied, expected)


def blocked(key: str, now: float) -> bool:
    mine = _recent(_FAILS.get(key, []), now)
    _FAILS[key] = mine
    _GLOBAL[:] = _recent(_GLOBAL, now)
    return len(mine) >= PER_CLIENT_LIMIT or len(_GLOBAL) >= GLOBAL_LIMIT


def record_failure(key: str, now: float) -> None:
    _FAILS.setdefault(key, []).append(now)
    _GLOBAL.append(now)
    if len(_FAILS) > MAX_CLIENTS:
        for k in [k for k, v in _FAILS.items()
                  if not v or now - v[-1] > WINDOW_S][:MAX_CLIENTS // 2]:
            _FAILS.pop(k, None)


def check(headers, client_host: str | None, expected: str,
          now: float | None = None) -> str | None:
    """None to let the request through, or R_THROTTLED to refuse it."""
    supplied = headers.get("x-admin-token") or ""
    if not supplied.strip():
        return None
    if token_ok(supplied, expected):
        return None
    now = time.time() if now is None else now
    key = client_key(headers, client_host)
    if blocked(key, now):
        return R_THROTTLED
    record_failure(key, now)
    return None


def warn_if_weak(expected: str) -> bool:
    """Boot-time PRIVATE log line when the signing key is short; never the
    value or its length, and never published on a public route."""
    weak = bool((expected or "").strip()) and \
        len((expected or "").strip()) < MIN_SIGNING_KEY_CHARS
    if weak:
        log.warning("admin_token is below the %d-character minimum for an HMAC "
                    "signing key and admin credential; the owner should rotate "
                    "it (failed attempts are throttled meanwhile)",
                    MIN_SIGNING_KEY_CHARS)
    return weak


def reset() -> None:
    _FAILS.clear()
    _GLOBAL.clear()
