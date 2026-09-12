"""Mint one PMUS market-websocket handshake. The only operation this broker has.

THE PUBLIC SURFACE IS ONE FUNCTION THAT TAKES NO SIGNING PARAMETERS.

    mint_market_ws_headers(consumer=...) -> MintedHandshake

`consumer` is a label for the mint ledger. It never enters the signed message,
and test_broker_consumer_label_cannot_reach_the_signed_message proves it by
minting under a consumer string that IS a trading path and checking the canonical
message is unchanged.

WHY THE SIGNING IS REIMPLEMENTED HERE RATHER THAN IMPORTED. `polymarket_us`
ships the signer in the same distribution as `resources/orders.py` -- the order
client. Importing the SDK to get four lines of Ed25519 would put the order
client inside the one process that holds the secret, which is the single worst
place for it. So the broker depends on PyNaCl alone and the SDK is absent from
its image entirely.

That reimplementation is a divergence risk, and it is pinned rather than
trusted: test_broker_signing_matches_the_vendor_sdk_byte_for_byte runs both
implementations over the same inputs IN THE TEST ENVIRONMENT (where the SDK is
present) and compares the headers. If the vendor ever changes the scheme, that
test fails here instead of a handshake failing at the venue.
"""
from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass
from typing import Final

from nacl.signing import SigningKey

from . import capability

# Advisory. The venue's scheme carries a timestamp and NO NONCE, and the
# server's tolerance window is not documented anywhere we can reach, so we
# cannot state how long a minted header is actually accepted for. Every mint is
# therefore treated as SINGLE USE operationally: a failed connect asks for a new
# one rather than retrying the old. This number is what the collector uses to
# refuse an obviously stale handful, not a claim about the venue.
ADVISORY_MAX_AGE_MS: Final[int] = 2_000

ACCESS_KEY_HEADER: Final[str] = "X-PM-Access-Key"
TIMESTAMP_HEADER: Final[str] = "X-PM-Timestamp"
SIGNATURE_HEADER: Final[str] = "X-PM-Signature"


class BrokerNotConfigured(RuntimeError):
    """No credential is loaded. The broker fails closed rather than degrading."""


@dataclass(frozen=True)
class MintedHandshake:
    """One handshake's worth of authentication material, plus its provenance.

    `headers` is the only field carrying secret-derived data. __repr__ is
    overridden so that logging or an exception traceback that happens to include
    this object cannot print a signature -- security test 9 asserts exactly that.
    """

    mint_id: str
    capability_id: str
    minted_at_wall_ms: int
    advisory_max_age_ms: int
    consumer: str
    headers: dict[str, str]

    def __repr__(self) -> str:                       # pragma: no cover - trivial
        return (f"MintedHandshake(mint_id={self.mint_id!r}, "
                f"capability_id={self.capability_id!r}, "
                f"consumer={self.consumer!r}, headers=<redacted>)")

    __str__ = __repr__


def canonical_signing_message(timestamp_ms: str) -> str:
    """The exact bytes that get signed, as a function of the CLOCK ALONE.

    This is the containment, expressed as a signature: the only input is a
    timestamp. There is no argument by which a caller could substitute a method
    or a path, because the function has nowhere to put one.
    """
    return f"{timestamp_ms}{capability.METHOD}{capability.PATH}"


def _signing_key(secret_key_b64: str) -> SigningKey:
    """Decode the venue's base64 Ed25519 secret. Mirrors the vendor's handling.

    The vendor accepts either a 32-byte seed or a 64-byte expanded key and takes
    the first 32 bytes of the latter (polymarket_us/auth.py:32-34). Matching that
    exactly is what keeps this implementation interchangeable with the SDK's.
    """
    raw = base64.b64decode(secret_key_b64)
    if len(raw) == 64:
        raw = raw[:32]
    return SigningKey(raw)


def mint_market_ws_headers(*, key_id: str, secret_key_b64: str,
                           consumer: str) -> MintedHandshake:
    """Produce authentication for GET /v1/ws/markets and for nothing else.

    Note the shape of the signature: `key_id` and `secret_key_b64` are the
    CREDENTIAL, supplied by the process's own configuration, and `consumer` is a
    LABEL. There is no third kind of argument. A caller of the network surface
    supplies only the label.
    """
    if not key_id or not secret_key_b64:
        raise BrokerNotConfigured(
            "no PMUS credential loaded; the broker mints nothing")

    timestamp = str(int(time.time() * 1000))
    message = canonical_signing_message(timestamp)
    signature = _signing_key(secret_key_b64).sign(message.encode()).signature

    return MintedHandshake(
        mint_id=secrets.token_hex(8),
        capability_id=capability.CAPABILITY_ID,
        minted_at_wall_ms=int(timestamp),
        advisory_max_age_ms=ADVISORY_MAX_AGE_MS,
        consumer=consumer,
        headers={
            ACCESS_KEY_HEADER: key_id,
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: base64.b64encode(signature).decode(),
        },
    )


# There is deliberately no sign(), no sign_request(), no headers_for(method,
# path), and no way to reach _signing_key with a message of the caller's
# choosing. If one is ever added, test_broker_exposes_no_generic_signer fails.
__all__ = [
    "ADVISORY_MAX_AGE_MS",
    "ACCESS_KEY_HEADER",
    "TIMESTAMP_HEADER",
    "SIGNATURE_HEADER",
    "BrokerNotConfigured",
    "MintedHandshake",
    "canonical_signing_message",
    "mint_market_ws_headers",
]
