"""CLOB_FAST_STREAM_PATH -- decoder and state machine. Separate from PMUS on purpose.

SECONDARY SOURCE / COMPARABILITY CHANNEL. It is not the execution channel and
its numbers never stand in for PMUS's. The two modules share only
streamstate.py's vocabulary; neither imports the other, so a PMUS assumption has
no route into CLOB reconstruction or the reverse.

WHAT CLOB SENDS, AND HOW IT DIFFERS FROM PMUS:

    subscribe by `assets_ids` (TOKEN IDS)      PMUS subscribes by marketSlug
    `book`  -- a full book, with `hash`         PMUS: no hash
    `price_change` -- SEMANTICS UNRESOLVED      PMUS: no such message
    millisecond `timestamp`                     PMUS: `transactTime`
    no authentication                           PMUS: auth mandatory

THE UNRESOLVED MESSAGE, AND WHY THIS MODULE REFUSES TO GUESS. `price_change` is
ambiguous between two readings of the venue's material:

    (A) a LEVEL DELTA -- apply it to the ladder at that price
    (B) a BEST-BID/ASK NOTIFICATION -- the touch moved; the ladder below is
        unspecified

Under (A), ignoring the message leaves the book stale. Under (B), applying it as
a delta CORRUPTS the book with a level that was never a level. The two failure
modes are opposite, so there is no cautious middle guess -- which is why this
module applies neither. A price_change is RECORDED, counted in
`pending_unapplied_updates`, and the affected state is marked
DEPTH_PENDING_SEMANTICS. A sample taken against such a state says, truthfully,
"the book I hold is from the last full `book` frame and N messages have arrived
since that I am not entitled to interpret."

THE HASH CANNOT RESCUE THIS. No hash algorithm is published, so we cannot
recompute it over our reconstruction and check agreement. It proves that
something changed; it cannot prove that we changed the same way. The passive
harness resolves the question by comparing against the NEXT FULL `book` frame,
not against the hash.
"""
from __future__ import annotations

from typing import Any

from . import clock
from .streamstate import (
    Continuity,
    DepthAuthority,
    StateValidity,
    StreamChannel,
    StreamState,
    TokenStateHistory,
)

CHANNEL = StreamChannel.CLOB_FAST_STREAM_PATH

PRICE_CHANGE_SEMANTICS = "CLOB_PRICE_CHANGE_SEMANTICS_UNRESOLVED"
PRICE_CHANGE_READINGS = ("LEVEL_DELTA", "BEST_QUOTE_NOTIFICATION")

# Subscription identifier space. NOT interchangeable with PMUS's marketSlug.
SUBSCRIBE_BY = "assets_ids"

# A full `book` frame is the venue's own complete ladder, so depth over it is
# defined. That is a statement about the `book` message specifically and does
# NOT extend to a state that has since absorbed unapplied price_change frames.
BOOK_DEPTH_AUTHORITY = DepthAuthority.FULL_REPLACEMENT_CONFIRMED


def _levels(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lvl in raw or []:
        try:
            if isinstance(lvl, dict):
                px, sz = lvl.get("price"), lvl.get("size")
            else:
                px, sz = lvl[0], lvl[1]
            if px is None or sz is None:
                continue
            out.append((float(px), float(sz)))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return out


def decode_book(message: dict, *, receive: clock.Instant,
                feed_session_id: str) -> StreamState | None:
    """A full `book` frame -> a StreamState with authoritative depth."""
    msg = message or {}
    token = msg.get("asset_id") or msg.get("assetId")
    if not token:
        return None
    bids = _levels(msg.get("bids"))
    asks = _levels(msg.get("asks"))
    ts = msg.get("timestamp")
    return StreamState(
        channel=CHANNEL,
        token_id=str(token),
        receive=receive,
        feed_session_id=feed_session_id,
        best_bid=max((p for p, _ in bids), default=None),
        best_ask=min((p for p, _ in asks), default=None),
        bids=bids,
        asks=asks,
        depth_authority=BOOK_DEPTH_AUTHORITY,
        validity=StateValidity.VALID,
        # A hash is not a sequence number. It shows this book differs from the
        # last one; it says nothing about whether a book in between was missed.
        continuity=Continuity.UNVERIFIED,
        venue_timestamp_raw=None if ts is None else str(ts),
        venue_book_hash=msg.get("hash"),
        venue_sequence=None,
        pending_unapplied_updates=0,
    )


def _restate(prev: StreamState, *, receive: clock.Instant,
             pending: int) -> StreamState:
    """Carry a book forward with an unapplied update counted against it.

    The PRICES ARE UNCHANGED and that is the point: we did not learn a new book,
    we learned that the book may have moved. The new receive instant makes the
    state selectable at a later anchor while `pending_unapplied_updates` and
    DEPTH_PENDING_SEMANTICS keep it honest about what it is.
    """
    return StreamState(
        channel=prev.channel, token_id=prev.token_id, receive=receive,
        feed_session_id=prev.feed_session_id,
        best_bid=prev.best_bid, best_ask=prev.best_ask,
        bids=prev.bids, asks=prev.asks,
        depth_authority=DepthAuthority.DEPTH_PENDING_SEMANTICS,
        validity=prev.validity,
        continuity=prev.continuity,
        venue_timestamp_raw=prev.venue_timestamp_raw,
        venue_book_hash=prev.venue_book_hash,
        venue_sequence=prev.venue_sequence,
        pending_unapplied_updates=pending,
    )


class ClobStreamState:
    """Per-token histories for the CLOB channel.

    `price_change` frames are retained raw in `unapplied` so the passive harness
    can resolve their semantics offline against the next full book. Nothing in
    this class interprets them.
    """

    def __init__(self, *, feed_session_id: str, retain: int = 256) -> None:
        self.feed_session_id = feed_session_id
        self.retain = retain
        self.histories: dict[str, TokenStateHistory] = {}
        self.unapplied: dict[str, list[dict]] = {}
        self.books = 0
        self.price_changes = 0
        self.reconnects = 0
        self.connected = False

    def history(self, token_id: str) -> TokenStateHistory | None:
        return self.histories.get(token_id)

    def _append(self, state: StreamState) -> StreamState:
        hist = self.histories.get(state.token_id)
        if hist is None:
            hist = TokenStateHistory(channel=CHANNEL, token_id=state.token_id,
                                     retain=self.retain)
            self.histories[state.token_id] = hist
        hist.append(state)
        return state

    def on_book(self, message: dict, *,
                receive: clock.Instant) -> StreamState | None:
        state = decode_book(message, receive=receive,
                            feed_session_id=self.feed_session_id)
        if state is None:
            return None
        # A full book supersedes everything unapplied: whatever those messages
        # meant, this ladder is the venue's own answer as of now.
        self.unapplied.pop(state.token_id, None)
        self.books += 1
        return self._append(state)

    def on_price_change(self, message: dict, *,
                        receive: clock.Instant) -> StreamState | None:
        """Record, count, and refuse to interpret. Returns the restated state."""
        msg = message or {}
        token = msg.get("asset_id") or msg.get("assetId")
        if not token:
            return None
        token = str(token)
        self.price_changes += 1
        self.unapplied.setdefault(token, []).append(msg)

        hist = self.histories.get(token)
        prev = hist.latest if hist else None
        if prev is None:
            # No book yet. There is nothing to restate and nothing to apply, so
            # the frame is kept for the harness and no state is invented.
            return None
        return self._append(_restate(prev, receive=receive,
                                     pending=len(self.unapplied[token])))

    def on_disconnect(self, *, receive: clock.Instant) -> int:
        self.connected = False
        self.reconnects += 1
        n = 0
        for hist in self.histories.values():
            hist._states = [                                # noqa: SLF001
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
            n += len(hist._states)                          # noqa: SLF001
        return n

    def stats(self) -> dict:
        return {
            "channel": CHANNEL,
            "connected": self.connected,
            "tokens": len(self.histories),
            "books": self.books,
            "price_changes": self.price_changes,
            "unapplied": sum(len(v) for v in self.unapplied.values()),
            "price_change_semantics": PRICE_CHANGE_SEMANTICS,
            "continuity": Continuity.UNVERIFIED,
        }
