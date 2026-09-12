"""How the collector obtains PMUS handshake material -- and how it gives it up.

THE COLLECTOR HAS NO SECRET-KEY CONFIGURATION FIELD. There is no env var read in
this module that could hold an Ed25519 secret, no parameter named secret_key,
and no signer. What the collector can obtain is one set of three header values,
minted by the broker for `GET /v1/ws/markets` and for nothing else. It cannot
ask for a different path because the broker's mint operation has no parameter
for one -- the restriction lives in the broker, not in this module's good
behaviour.

    UNDERLYING_CREDENTIAL_AUTHORITY = TRADING_CAPABLE_AT_CLIENT_LAYER
    COLLECTOR_SIGNING_CAPABILITY    = MARKET_WS_HANDSHAKE_ONLY

The second is an architectural capability restriction in OUR code. It is not a
venue-issued key scope, and it must never be described as one. The venue would
honour a correctly signed order from the underlying credential; what stops one
being signed is that this process cannot construct a signature at all.

SINGLE USE, BECAUSE THE SCHEME HAS NO NONCE. The venue signs
`timestamp + method + path` with no nonce, and the server's tolerance window is
not documented anywhere reachable, so we cannot say how long a header set stays
valid or how many times it would be accepted. Every mint is therefore treated as
single-use: a failed connect requests a NEW mint rather than retrying the old
one. `HandshakeMaterial.consume()` enforces it in code, so the discipline does
not depend on every caller remembering.

HOST BINDING IS ABSENT FROM THE SIGNATURE. A minted header is valid for
`GET /v1/ws/markets` on any host that accepts the key. Restricting the
destination to api.polymarket.us is a NETWORK POLICY obligation, discharged
outside this file. The two are recorded as separate controls because treating
them as one is how a firewall rule gets mistaken for a cryptographic guarantee.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

BROKER_MINT_PATH = "/mint"
CALLER_HEADER = "X-Broker-Caller"

UNDERLYING_CREDENTIAL_AUTHORITY = "TRADING_CAPABLE_AT_CLIENT_LAYER"
COLLECTOR_SIGNING_CAPABILITY = "MARKET_WS_HANDSHAKE_ONLY"
SIGNATURE_HOST_BINDING = "ABSENT"
HOST_RESTRICTION = "NETWORK_POLICY"

# The intended destination, stated so a deployment can be checked against it.
# Stating it here does not enforce it; the network policy does.
INTENDED_WS_URL = "wss://api.polymarket.us/v1/ws/markets"


class HandshakeUnavailable(RuntimeError):
    """The broker would not mint. The collector does not connect. No fallback."""


class HandshakeAlreadyConsumed(RuntimeError):
    """Someone tried to reuse minted material. Refused: mints are single use."""


@dataclass
class HandshakeMaterial:
    """Three header values with a one-shot latch and a redacting repr.

    The headers are held in a private field and handed out exactly once. After
    `consume()` the object retains its mint id -- which is safe to log and is
    what ties a connection attempt to a ledger row -- and nothing else.
    """

    mint_id: str
    capability_id: str
    minted_at_wall_ms: int
    advisory_max_age_ms: int
    _headers: dict[str, str] | None = field(repr=False, default=None)
    consumed: bool = False

    def consume(self) -> dict[str, str]:
        """Hand over the headers once, then forget them.

        Called immediately before the websocket handshake. The material is gone
        from this object before the connection attempt returns, so a later
        exception, log line or retry has nothing to leak or replay.
        """
        if self.consumed or self._headers is None:
            raise HandshakeAlreadyConsumed(
                f"mint {self.mint_id} has already been used; the venue's scheme "
                "carries no nonce and its tolerance window is unknown, so a "
                "second use is not attempted. Request a fresh mint."
            )
        headers = self._headers
        self._headers = None
        self.consumed = True
        return headers

    def discard(self) -> None:
        """Drop unused material -- on an aborted connect, or in a finally block."""
        self._headers = None
        self.consumed = True

    def __repr__(self) -> str:                       # pragma: no cover - trivial
        return (f"HandshakeMaterial(mint_id={self.mint_id!r}, "
                f"consumed={self.consumed}, headers=<redacted>)")

    __str__ = __repr__


def broker_url() -> str:
    """Where the broker lives. A broker URL, never a venue URL."""
    return os.environ.get("RN1_OBS_BROKER_URL", "").rstrip("/")


def caller_token() -> str:
    """This collector's identity to the broker. Not a venue credential."""
    return os.environ.get("RN1_OBS_BROKER_CALLER_TOKEN", "")


def configured() -> bool:
    return bool(broker_url() and caller_token())


def _parse_mint_response(payload: dict) -> HandshakeMaterial:
    headers = payload.get("headers")
    if not isinstance(headers, dict) or not headers:
        raise HandshakeUnavailable("broker returned no handshake headers")
    return HandshakeMaterial(
        mint_id=str(payload.get("mint_id", "unknown")),
        capability_id=str(payload.get("capability_id", "unknown")),
        minted_at_wall_ms=int(payload.get("minted_at_wall_ms", 0)),
        advisory_max_age_ms=int(payload.get("advisory_max_age_ms", 0)),
        _headers={str(k): str(v) for k, v in headers.items()},
    )


async def request_handshake(http, *, consumer: str) -> HandshakeMaterial:
    """Ask the broker for one handshake. Never raises with credential text.

    `consumer` is a label for the broker's mint ledger. It is deliberately the
    ONLY thing this function sends, because it is the only thing the broker
    accepts -- a body naming a method or a path is refused there with
    SIGNING_PARAMETER_REFUSED.
    """
    base = broker_url()
    token = caller_token()
    if not base or not token:
        raise HandshakeUnavailable(
            "RN1_OBS_BROKER_URL / RN1_OBS_BROKER_CALLER_TOKEN are unset; the "
            "collector has no way to obtain a handshake and does not connect")

    try:
        resp = await http.post(f"{base}{BROKER_MINT_PATH}",
                               json={"consumer": consumer},
                               headers={CALLER_HEADER: token},
                               timeout=5.0)
    except Exception as exc:                       # noqa: BLE001 - never raise raw
        raise HandshakeUnavailable(
            f"broker unreachable: {type(exc).__name__}") from None

    if resp.status_code != 200:
        # The broker's error codes are capability words (BROKER_NOT_CONFIGURED,
        # CALLER_IDENTITY_REJECTED, SIGNING_PARAMETER_REFUSED). Safe to log --
        # none of them is derived from a credential.
        raise HandshakeUnavailable(f"broker refused: http_{resp.status_code}")

    material = _parse_mint_response(resp.json())
    log.info("handshake minted mint_id=%s capability=%s",
             material.mint_id, material.capability_id)
    return material


async def connect_with_fresh_mint(http, connect, *, consumer: str,
                                  attempts: int = 3):
    """Mint, connect, and on failure MINT AGAIN -- never retry the old material.

    `connect` is injected rather than imported so this function is exercisable
    with no socket: Run 83.3 tests it against a double that fails and then
    succeeds, and asserts that two DISTINCT mints were requested. That is the
    difference between a policy in a docstring and a property of the code.

    The finally block is the one that matters. Whatever happens -- success,
    refusal, an exception from deep inside a websocket library -- the material is
    discarded before this frame unwinds. On success it has already been consumed
    by the handshake; on failure it is dropped unused. There is no path on which
    a header set outlives its one connection attempt.
    """
    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        material = await request_handshake(http, consumer=consumer)
        try:
            headers = material.consume()
            return await connect(headers)
        except Exception as exc:                   # noqa: BLE001 - retry policy
            last = exc
            log.warning("handshake attempt %d/%d failed (mint_id=%s): %s",
                        attempt, attempts, material.mint_id, type(exc).__name__)
        finally:
            material.discard()
    raise HandshakeUnavailable(
        f"no connection after {attempts} freshly minted handshakes: "
        f"{type(last).__name__ if last else 'unknown'}")
