"""THE FIXED CAPABILITY. One constant set, no parameters, no callers' input.

This module is the whole of the broker's authority surface. Everything the
signature commits to is a module-level constant here, so the answer to "what can
this broker authorise?" is read rather than traced.

WHY CONSTANTS AND NOT ARGUMENTS. The venue's scheme signs
`timestamp + method + path` (polymarket_us/auth.py). A signer that accepts a
method and a path is therefore a general trading signer: the same key that signs
GET /v1/ws/markets signs POST /v1/orders, and only the argument differs. The
containment in this repository is that THE ARGUMENT DOES NOT EXIST. There is no
code path -- public, private, or reachable by keyword -- that puts a caller's
string into the signed message.

WHAT THIS IS NOT. It is NOT a venue-issued key scope. The underlying credential
remains TRADING_CAPABLE_AT_CLIENT_LAYER: anyone holding the secret can sign
anything, and this process holds the secret. What is restricted is the
CAPABILITY THIS PROCESS EXPOSES, which is an architectural property of our code
and nothing the venue enforces. Those two facts must never be stated as one.

HOST BINDING IS ABSENT. The signed message contains no hostname. A signature
minted here is valid for `GET /v1/ws/markets` on ANY host that accepts this
key. Restricting the destination to api.polymarket.us is therefore a NETWORK
POLICY obligation on the collector, not a property of this signature. See
SIGNATURE_HOST_BINDING / HOST_RESTRICTION in the module constants below.
"""
from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------- the capability
# Changing any of these four lines changes what this broker can authorise. They
# are the review surface; there is deliberately nothing else to review.
CAPABILITY_ID: Final[str] = "PMUS_MARKET_WS_HANDSHAKE"
METHOD: Final[str] = "GET"
PATH: Final[str] = "/v1/ws/markets"

# Advisory only -- the collector's network policy is what actually binds the
# destination. Recorded here so the intended endpoint is stated in one place.
INTENDED_HOST: Final[str] = "api.polymarket.us"
INTENDED_URL: Final[str] = "wss://api.polymarket.us/v1/ws/markets"

# ------------------------------------------------------------ the honest labels
UNDERLYING_CREDENTIAL_AUTHORITY: Final[str] = "TRADING_CAPABLE_AT_CLIENT_LAYER"
COLLECTOR_SIGNING_CAPABILITY: Final[str] = "MARKET_WS_HANDSHAKE_ONLY"
SIGNATURE_HOST_BINDING: Final[str] = "ABSENT"
HOST_RESTRICTION: Final[str] = "NETWORK_POLICY"

# Paths this broker must never be able to authorise. Present as data so the
# adversarial tests can assert against a list rather than a remembered example.
# This is NOT a deny-list the code consults -- the code cannot reach these paths
# because it accepts no path at all. It exists only so a test can prove that.
NEVER_MINTABLE: Final[tuple[str, ...]] = (
    "/v1/orders",
    "/v1/orders/open",
    "/v1/orders/open/cancel",
    "/v1/order/preview",
    "/v1/order/close-position",
    "/v1/account/balances",
    "/v1/ws/private",
)
