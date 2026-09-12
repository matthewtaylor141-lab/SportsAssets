"""The websocket transport. The ONLY module in the collector that opens a socket.

Kept apart from the decoders on purpose: sportsassets/obs/pmus_stream.py and
clob_stream.py are pure functions of (frame, local receive instant), so the
whole scientific path is exercisable with no connection. This file is the thin
edge that is not.

TWO THINGS IT GUARANTEES.

1. THE RECEIVE INSTANT IS READ BEFORE ANY PARSING. `clock.now()` is the first
   statement after a frame comes off the socket, before json.loads. At the
   0-500 ms offsets, folding a parse into the state's age is not a rounding
   error.

2. HANDSHAKE MATERIAL DIES WITH THE ATTEMPT. `connect_with_fresh_mint` mints,
   consumes once, and discards in a finally block; a failure mints again rather
   than replaying. That is enforced in obs/handshake.py, not here, so this
   module cannot opt out of it.

DESTINATION BINDING IS NOT DONE HERE AND CANNOT BE. The venue's signature
carries no hostname (SIGNATURE_HOST_BINDING = ABSENT), so a URL check in this
file is a courtesy, not a control: anything running in this process could open a
different socket. The real boundary is the network policy
(HOST_RESTRICTION = NETWORK_POLICY). `assert_expected_destination` exists to
catch a misconfiguration early and to make the intended destination greppable --
it is not a security boundary and is not described as one.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from sportsassets.obs import clock, handshake
from sportsassets.obs.streamstate import StreamChannel

log = logging.getLogger(__name__)

PMUS_WS_URL = "wss://api.polymarket.us/v1/ws/markets"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class UnexpectedDestination(RuntimeError):
    """The configured URL is not the one this channel is supposed to dial."""


def assert_expected_destination(url: str, *, channel: str) -> None:
    """Early misconfiguration check. NOT a security boundary -- see the module docstring."""
    parsed = urlparse(url)
    if parsed.scheme not in ("ws", "wss"):
        raise UnexpectedDestination(f"{channel}: not a websocket URL: {parsed.scheme}")
    if channel == StreamChannel.PMUS_FAST_STREAM_PATH:
        if parsed.path != "/v1/ws/markets":
            raise UnexpectedDestination(
                f"{channel}: path {parsed.path!r} is not the market socket. The "
                "minted handshake is signed for GET /v1/ws/markets and would not "
                "authenticate anything else -- but nothing here prevents the "
                "socket being opened, so the network policy must.")


async def open_pmus_market_socket(http, ws_connect, *, url: str,
                                  consumer: str = "rn1-collector"):
    """Mint a handshake, open the market socket, discard the material.

    `ws_connect` is injected so the offline end-to-end test drives the real code
    path against a local fake endpoint. There is no import of a websocket
    library in this function's own body for the same reason.
    """
    assert_expected_destination(url, channel=StreamChannel.PMUS_FAST_STREAM_PATH)

    async def _connect(headers: dict[str, str]):
        return await ws_connect(url, additional_headers=headers)

    return await handshake.connect_with_fresh_mint(http, _connect,
                                                   consumer=consumer)


async def pump(ws, on_frame) -> int:
    """Read frames, stamping the LOCAL receive instant before parsing.

    Returns the number of frames handled. A malformed frame is counted and
    skipped: the collector must not die because the venue sent something
    unexpected, and a decode failure is a fact about one message.
    """
    handled = 0
    async for raw in ws:
        receive = clock.now()               # FIRST. Before json.loads.
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            message = json.loads(raw)
        except ValueError:
            log.debug("undecodable frame skipped")
            continue
        on_frame(message, receive=receive)
        handled += 1
    return handled
