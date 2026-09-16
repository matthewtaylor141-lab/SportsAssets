"""The broker's network surface: POST /mint, and nothing that signs to order.

DEFAULT DENY, FAIL CLOSED. Three independent ways this refuses:

  * no caller token configured      -> 503, mints nothing, ever
  * caller token absent or wrong    -> 401, constant-time comparison
  * body carries a signing-shaped key -> 400, REFUSED AND NAMED

The third is the interesting one. A caller cannot influence the signature
because `mint_market_ws_headers` has no parameter for it -- the refusal is not
what provides the security. It exists so that an ATTEMPT is a loud error in the
broker's log rather than a silently ignored field, because a silently ignored
`{"path": "/v1/orders"}` looks identical to a successfully constrained one and
we would rather see the difference.

WHY STDLIB http.server AND NOT A FRAMEWORK. The dependency footprint of this
process is its containment story. It is PyNaCl plus the standard library: no web
framework, no database driver, and -- most importantly -- NO HTTP CLIENT and no
`polymarket_us`. A process with no outbound HTTP capability cannot be turned
into one that talks to the venue, and a process without the SDK does not contain
an order client to be reached. Signing requires no network access at all.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import capability
from .ledger import MintLedger
from .mint import BrokerNotConfigured, mint_market_ws_headers

log = logging.getLogger("pmusbroker")

MINT_PATH = "/mint"
HEALTH_PATH = "/healthz"
CALLER_HEADER = "X-Broker-Caller"
MAX_BODY_BYTES = 4096

# Any of these in a request body is an attempt to steer the signature. None of
# them is consulted; their presence is refused so the attempt is visible.
SIGNING_SHAPED_KEYS = frozenset({
    "method", "path", "url", "host", "hostname", "scheme", "endpoint",
    "message", "payload", "body", "sign", "signature", "capability",
    "key_id", "secret", "secret_key",
})


class RateLimiter:
    """A token bucket over mint operations. Bounds a loop, not a patient caller.

    WHAT IT IS FOR. One handshake per connection, and a connection lasts as long
    as the socket does, so a healthy collector mints a handful of times an hour.
    A caller minting thirty times a minute is a retry loop that has lost its
    backoff, or something enumerating. Either way the answer is to stop signing.

    WHAT IT IS NOT FOR, and this belongs in the same comment so the limit is not
    mistaken for a defence it isn't: it does NOT stop a caller who holds the
    token and paces itself under the limit. Nothing here can. What bounds that
    caller is the capability -- everything it can ever obtain is a market-socket
    handshake -- not the rate.
    """

    def __init__(self, *, capacity: int = 30, per_seconds: float = 60.0) -> None:
        self.capacity = capacity
        self.per_seconds = per_seconds
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self.refused = 0

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self.capacity,
                self._tokens + (now - self._last) * self.capacity / self.per_seconds)
            self._last = now
            if self._tokens < 1.0:
                self.refused += 1
                return False
            self._tokens -= 1.0
            return True


def _rate_limit_settings() -> tuple[int, float]:
    try:
        cap = max(1, int(os.environ.get("PMUS_BROKER_MINTS_PER_MINUTE", "30")))
    except ValueError:
        cap = 30
    return cap, 60.0


def _caller_token() -> str:
    return os.environ.get("PMUS_BROKER_CALLER_TOKEN", "")


def _credential() -> tuple[str, str]:
    """The broker's own credential, from its own environment.

    Named PMUS_BROKER_* rather than PMUS_* on purpose: the backend production
    trading credential is not approved for Run 83 and must not be reachable by
    accidentally inheriting a familiar variable name.
    """
    return (os.environ.get("PMUS_BROKER_KEY_ID", ""),
            os.environ.get("PMUS_BROKER_SECRET_KEY", ""))


def authorize(caller_header_value: str | None) -> tuple[bool, str]:
    """Default-deny caller check. Returns (allowed, reason)."""
    expected = _caller_token()
    if not expected:
        return False, "BROKER_NOT_CONFIGURED"
    if not caller_header_value:
        return False, "CALLER_IDENTITY_ABSENT"
    if not hmac.compare_digest(caller_header_value, expected):
        return False, "CALLER_IDENTITY_REJECTED"
    return True, "CALLER_ALLOWED"


def screen_body(raw: bytes) -> tuple[bool, str, str]:
    """Refuse any body that tries to name what gets signed.

    Returns (accepted, reason, consumer_label).
    """
    if not raw:
        return True, "EMPTY_BODY", "unlabelled"
    if len(raw) > MAX_BODY_BYTES:
        return False, "BODY_TOO_LARGE", ""
    try:
        parsed: Any = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return False, "BODY_NOT_JSON", ""
    if not isinstance(parsed, dict):
        return False, "BODY_NOT_OBJECT", ""

    offending = sorted(k for k in parsed if k.lower() in SIGNING_SHAPED_KEYS)
    if offending:
        return False, f"SIGNING_PARAMETER_REFUSED:{','.join(offending)}", ""

    consumer = parsed.get("consumer")
    if consumer is None:
        return True, "NO_CONSUMER_LABEL", "unlabelled"
    if not isinstance(consumer, str):
        return False, "CONSUMER_NOT_A_STRING", ""
    # Truncated and stripped of control characters: it reaches a log line and a
    # ledger row, never the signed message, and a log line is not a place to let
    # an arbitrary caller write arbitrary bytes.
    clean = "".join(c for c in consumer if c.isprintable())[:120]
    return True, "ACCEPTED", clean or "unlabelled"


class MintHandler(BaseHTTPRequestHandler):
    server_version = "pmusbroker/1"
    ledger: MintLedger = MintLedger()
    limiter: RateLimiter = RateLimiter(capacity=_rate_limit_settings()[0],
                                       per_seconds=_rate_limit_settings()[1])

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:      # noqa: A003
        """Route through logging, and never echo a request header.

        BaseHTTPRequestHandler's default writes the request line to stderr. The
        request line is harmless here, but the default also makes it easy for a
        later edit to start echoing headers -- and the response headers are the
        one thing in this process that must never be logged.
        """
        log.info("broker %s", fmt % args)

    def do_GET(self) -> None:                            # noqa: N802
        if self.path == HEALTH_PATH:
            configured = all(_credential()) and bool(_caller_token())
            self._reply(200, {
                "ok": True,
                "capability_id": capability.CAPABILITY_ID,
                "configured": configured,
                "underlying_credential_authority":
                    capability.UNDERLYING_CREDENTIAL_AUTHORITY,
                "exposed_capability": capability.COLLECTOR_SIGNING_CAPABILITY,
                "signature_host_binding": capability.SIGNATURE_HOST_BINDING,
                "mints": self.ledger.stats(),
                "rate_limited": self.limiter.refused,
            })
            return
        self._reply(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:                           # noqa: N802
        if self.path != MINT_PATH:
            # There is exactly one POST route. Anything else -- including a
            # hopeful /v1/orders -- is a 404 from a process that would not know
            # how to sign it if it existed.
            self._reply(404, {"error": "NOT_FOUND"})
            return

        allowed, reason = authorize(self.headers.get(CALLER_HEADER))
        if not allowed:
            status = 503 if reason == "BROKER_NOT_CONFIGURED" else 401
            log.warning("mint refused: %s", reason)
            self._reply(status, {"error": reason})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply(400, {"error": "BAD_CONTENT_LENGTH"})
            return
        raw = self.rfile.read(min(length, MAX_BODY_BYTES + 1)) if length else b""

        ok, screen_reason, consumer = screen_body(raw)
        if not ok:
            log.warning("mint refused: %s", screen_reason)
            self._reply(400, {"error": screen_reason})
            return

        if not self.limiter.allow():
            # A healthy collector mints a handful of times an hour: one per
            # connection. This many means a retry loop that has lost its
            # backoff, and the right answer is to stop signing.
            log.warning("mint refused: RATE_LIMIT_EXCEEDED")
            self._reply(429, {"error": "RATE_LIMIT_EXCEEDED"})
            return

        key_id, secret = _credential()
        try:
            minted = mint_market_ws_headers(key_id=key_id, secret_key_b64=secret,
                                            consumer=consumer)
        except BrokerNotConfigured:
            log.warning("mint refused: BROKER_NOT_CONFIGURED")
            self._reply(503, {"error": "BROKER_NOT_CONFIGURED"})
            return
        except Exception:                          # noqa: BLE001 - fail closed
            # Deliberately no exception text in the response or the log: the
            # only values in scope at this point are credential-derived.
            log.error("mint failed: signing raised")
            self._reply(500, {"error": "MINT_FAILED"})
            return

        rec = self.ledger.record(minted)
        log.info("minted %s capability=%s consumer=%s",
                 rec.mint_id, rec.capability_id, rec.consumer)
        self._reply(200, {
            "mint_id": minted.mint_id,
            "capability_id": minted.capability_id,
            "minted_at_wall_ms": minted.minted_at_wall_ms,
            "advisory_max_age_ms": minted.advisory_max_age_ms,
            "single_use": True,
            "headers": minted.headers,
        })


def serve(bind_address: str = "127.0.0.1", port: int = 8787) -> None:  # pragma: no cover
    """Listen. `bind_address` is OUR listening interface, never a destination.

    Named `bind_address` rather than `host` deliberately. No public callable in
    this process may take a parameter that reads as a signing input, and
    test_the_broker_exposes_no_generic_signer enforces that by name -- bluntly,
    on purpose. It caught this function on its first run. The answer to a blunt
    check in the process that holds the trading-capable secret is to make the
    name unambiguous, not to teach the check about exceptions.
    """
    logging.basicConfig(level=logging.INFO)
    ThreadingHTTPServer((bind_address, port), MintHandler).serve_forever()


if __name__ == "__main__":                               # pragma: no cover
    serve(os.environ.get("PMUS_BROKER_BIND", "127.0.0.1"),
          int(os.environ.get("PMUS_BROKER_PORT", "8787")))
