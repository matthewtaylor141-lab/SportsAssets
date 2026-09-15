"""PMUS_FAST_STREAM_PATH -- the decoder and state machine. No transport here.

THE FILE CONTAINS NO SOCKET AND NO CREDENTIAL. Everything below is a pure
function of (frame, local receive instant). That is not tidiness: it is what
lets the whole channel be exercised against recorded and synthetic frames with
no connection, which is the only kind of testing Run 83.3 is permitted. The
transport adapter lives in transport.py and is not imported by this module.

WHAT THE VENUE SENDS (SDK_SOURCE, polymarket_us 0.1.2, websocket/types.py):

    {"marketData": {"marketSlug": str,
                    "bids":   [{"px": str, "qty": str}, ...],
                    "offers": [{"px": str, "qty": str}, ...],
                    "state": ..., "stats": ..., "transactTime": ...}}

NO HASH. NO SEQUENCE NUMBER. Those two absences decide almost everything here:

  * continuity can never be VERIFIED from the message stream alone, because
    there is no field a receiver could use to notice a gap. It is pinned to
    STREAM_CONTINUITY_UNVERIFIED and no code path sets it otherwise.
  * PMUS_FULL_REPLACEMENT_UNCONFIRMED. The SDK type holds arrays, and an array
    is not evidence that the array is the entire book. It would be an easy and
    invisible error to treat `bids` as a full ladder, compute Q_A over it, and
    publish a depth-weighted price that is really a partial-depth price. So
    every state this module builds is TOP_OF_BOOK_ONLY, and streamstate.vwap
    REFUSES rather than returning a number.

    `DEPTH_AUTHORITY` below is the single switch that would change after a
    passive test establishes the semantics. It is deliberately one constant in
    one place so that promoting it is a reviewable edit and not a scattering.

`transactTime` is retained verbatim as a STRING in venue_timestamp_raw. It is
provenance. It never selects the 0 ms state -- that is streamstate's local
monotonic rule -- and nothing subtracts it from a local reading.
"""
from __future__ import annotations

from typing import Any

from . import clock
from .config import OFFSETS
from .streamstate import (
    RETENTION_HEADROOM_S,
    Continuity,
    DepthAuthority,
    StateValidity,
    StreamChannel,
    StreamState,
    TokenStateHistory,
)

CHANNEL = StreamChannel.PMUS_FAST_STREAM_PATH

# Retention horizon for this channel: the whole pre-registered ladder plus
# headroom. Derived from OFFSETS rather than written as a number, so an
# amendment to the ladder cannot leave the buffer too short for its own
# longest slot.
RETAIN_HORIZON_S = max(t for _, t in OFFSETS) + RETENTION_HEADROOM_S


# The one place PMUS depth authority is decided. Promoting this to
# FULL_REPLACEMENT_CONFIRMED requires passive observation establishing that every
# marketData message is a complete replacement -- not an SDK type signature, and
# not the absence of a message that looks like a delta.
DEPTH_AUTHORITY = DepthAuthority.TOP_OF_BOOK_ONLY
FULL_REPLACEMENT_STATUS = "PMUS_FULL_REPLACEMENT_UNCONFIRMED"

# Subscription is BY MARKET SLUG on this channel (SDK: subscribe_market_data
# takes market_slugs). CLOB subscribes by token id. Those identifier spaces are
# NOT interchangeable and the mapping gate governs any comparison between them.
SUBSCRIBE_BY = "marketSlug"


def _levels(raw: Any) -> list[tuple[float, float]]:
    """Parse a PMUS side into (price, size) pairs, skipping malformed entries.

    PMUS names the fields px/qty; the CLOB HTTP path names them price/size. Both
    spellings are accepted here rather than in a shared helper, because a shared
    parser is exactly how one venue's field naming quietly becomes an assumption
    about the other's.
    """
    out: list[tuple[float, float]] = []
    for lvl in raw or []:
        try:
            if isinstance(lvl, dict):
                px = lvl.get("px", lvl.get("price"))
                qty = lvl.get("qty", lvl.get("size"))
            else:
                px, qty = lvl[0], lvl[1]
            if px is None or qty is None:
                continue
            out.append((float(px), float(qty)))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return out


def decode_market_data(message: dict, *, receive: clock.Instant,
                       feed_session_id: str) -> StreamState | None:
    """One `marketData` frame -> one StreamState. Returns None if unusable.

    `receive` MUST be read by the caller at the moment the frame came off the
    socket, before any parsing. Reading it after parsing would fold this
    function's own cost into the state's age, which at the 0-500 ms offsets is
    not a rounding error.
    """
    md = (message or {}).get("marketData") or {}
    slug = md.get("marketSlug")
    if not slug:
        return None

    bids = _levels(md.get("bids"))
    # The SDK names the sell side `offers`; `asks` is accepted because the
    # repository's existing PMUS stream stores whichever of the two arrives.
    asks = _levels(md.get("offers") or md.get("asks"))

    transact = md.get("transactTime")
    return StreamState(
        channel=CHANNEL,
        token_id=str(slug),
        receive=receive,
        feed_session_id=feed_session_id,
        best_bid=max((p for p, _ in bids), default=None),
        best_ask=min((p for p, _ in asks), default=None),
        bids=bids,
        asks=asks,
        depth_authority=DEPTH_AUTHORITY,
        validity=StateValidity.VALID,
        continuity=Continuity.UNVERIFIED,
        venue_timestamp_raw=None if transact is None else str(transact),
        venue_book_hash=None,      # PMUS sends none
        venue_sequence=None,       # PMUS sends none
        pending_unapplied_updates=0,
    )


class PmusStreamState:
    """Per-token histories for the PMUS channel, plus the session's own state.

    ON DISCONNECT, EVERY RETAINED STATE IS INVALIDATED. The SDK ships no
    reconnect logic, so a drop is silent, and with no sequence number a receiver
    cannot tell a resumed stream from a gapped one. Carrying pre-disconnect
    states forward would let a sample be answered from a book that may have moved
    arbitrarily far while we were not listening.
    """

    def __init__(self, *, feed_session_id: str,
                 retain_horizon_s: float = RETAIN_HORIZON_S) -> None:
        self.feed_session_id = feed_session_id
        self.retain_horizon_s = retain_horizon_s
        self.histories: dict[str, TokenStateHistory] = {}
        self.frames = 0
        self.reconnects = 0
        self.invalidated = 0
        self.connected = False

    def history(self, token_id: str) -> TokenStateHistory | None:
        return self.histories.get(token_id)

    def on_frame(self, message: dict, *, receive: clock.Instant) -> StreamState | None:
        state = decode_market_data(message, receive=receive,
                                   feed_session_id=self.feed_session_id)
        if state is None:
            return None
        hist = self.histories.get(state.token_id)
        if hist is None:
            hist = TokenStateHistory(channel=CHANNEL, token_id=state.token_id,
                                     retain_horizon_s=self.retain_horizon_s)
            self.histories[state.token_id] = hist
        hist.append(state)
        self.frames += 1
        return state

    def on_open(self, *, feed_session_id: str) -> None:
        self.feed_session_id = feed_session_id
        self.connected = True

    def on_disconnect(self, *, receive: clock.Instant) -> int:
        """Mark every retained state INVALIDATED_BY_DISCONNECT. Returns the count.

        The states are not deleted: a sample scheduled before the drop still has
        a slot to resolve, and CACHE_INVALID with a named reason is a result. An
        empty history would have been recorded as CACHE_MISS, which would be a
        different and false statement about what happened.
        """
        self.connected = False
        self.reconnects += 1
        n = 0
        for hist in self.histories.values():
            replaced = [
                StreamState(
                    channel=s.channel, token_id=s.token_id, receive=s.receive,
                    feed_session_id=s.feed_session_id,
                    best_bid=s.best_bid, best_ask=s.best_ask,
                    bids=s.bids, asks=s.asks,
                    depth_authority=s.depth_authority,
                    validity=StateValidity.INVALIDATED_BY_DISCONNECT,
                    continuity=s.continuity,
                    venue_timestamp_raw=s.venue_timestamp_raw,
                    venue_book_hash=s.venue_book_hash,
                    venue_sequence=s.venue_sequence,
                    pending_unapplied_updates=s.pending_unapplied_updates,
                )
                for s in hist._states                       # noqa: SLF001
            ]
            hist._states = replaced                         # noqa: SLF001
            n += len(replaced)
        self.invalidated += n
        return n

    def stats(self) -> dict:
        return {
            "channel": CHANNEL,
            "connected": self.connected,
            "tokens": len(self.histories),
            "frames": self.frames,
            "reconnects": self.reconnects,
            "invalidated": self.invalidated,
            "depth_authority": DEPTH_AUTHORITY,
            "full_replacement_status": FULL_REPLACEMENT_STATUS,
            "continuity": Continuity.UNVERIFIED,
        }
