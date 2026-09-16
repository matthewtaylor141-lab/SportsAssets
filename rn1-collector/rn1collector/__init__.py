"""Standalone RN1 observability collector.

Holds no PMUS secret, no general signer, no order client, no private-websocket
implementation and no wallet material. It obtains one set of handshake headers
from the signing broker, uses them once, and discards them.

    UNDERLYING_CREDENTIAL_AUTHORITY = TRADING_CAPABLE_AT_CLIENT_LAYER
    COLLECTOR_SIGNING_CAPABILITY    = MARKET_WS_HANDSHAKE_ONLY

The second is an architectural restriction in this code. It is NOT a
venue-issued key scope, and the credential must never be called read-only
unless the venue actually enforces that.
"""
from __future__ import annotations

__all__ = ["transport", "runner"]
