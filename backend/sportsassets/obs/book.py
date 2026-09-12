"""Read the order book, over plain HTTP, with no order-capable client.

WHY NOT THE VENUE SDK. The SDK object that can read a book is the same object
the egress functions in sportsassets/pmus.py hang off. Importing it here would
put an egress path one attribute lookup away from the collector and would force
the safety allow-list open. A read-only HTTP GET cannot reach an egress path no
matter what calls it, so the collector reads the book that way instead. This is
not a workaround: copy_probe.py already reads the book exactly this way, over
the same endpoint.

AND IT IS THE RIGHT ENDPOINT FOR COMPARABILITY. U2 -- the sealed population all
of 81A/81B/82 measured -- was built from copy_probes, whose depth ladders came
from this endpoint. A forward curve read anywhere else would not be the same
quantity. Reading here means the prospective and historical measurements are
apples to apples.

WHAT THE VENUE DOES NOT GIVE US. Nothing this repository reads from any book or
BBO response carries a venue timestamp or a sequence number -- not the CLOB book,
not the PMUS bbo_read, not the depth read. So venue_snapshot_ts and
venue_sequence are recorded as NULL, honestly, rather than filled with our own
receipt time. That absence is itself a finding for run 83I: there is no venue-side
clock to synchronise against on the market-data path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import clock


class ObservationChannel:
    """WHICH channel produced a snapshot. Named on every row, never inferred.

    LEGACY_COMPARABLE_BOOK_PATH -- the raw CLOB /book HTTP read. It exists so the
    forward curve is the SAME QUANTITY as U2, which 81A, 81B and 82 all measured
    from this endpoint's ladders.

    IT IS NOT the fastest path available, and it is NOT the earliest actionable
    market state. Those are three different ideas and this label is what keeps
    them apart: if a streaming channel is added later, the two can be COMPARED
    rather than pooled into one average belonging to neither.

    FAST_STREAM_PATH is declared here so the schema and the analysis already have
    a name for it. NOTHING IMPLEMENTS IT and nothing activates it in run 83.
    """

    LEGACY_COMPARABLE_BOOK_PATH = "LEGACY_COMPARABLE_BOOK_PATH"

    # RUN 83.2 PRIMARY. State BETTOR already holds locally at the scheduled
    # instant, kept fresh by batched POST /books. The scheduled sample makes no
    # request, which is what lets the 0-500 ms offsets exist at all: a request
    # started after receipt is INSIDE its own round trip (measured p50 170 ms).
    LOCAL_CACHE_BATCH_POLL_PATH = "LOCAL_CACHE_BATCH_POLL_PATH"

    # STILL DECLARED, STILL NOT IMPLEMENTED, AND DELIBERATELY NOT REUSED FOR THE
    # CACHE. Established 2026-09-12 from py-clob-client 0.34.6: the vendor SDK
    # contains NO websocket client -- zero files matching websocket/wss/ws_ --
    # so there is no pushed feed to subscribe to. The V2 primary channel is a
    # POLLED cache, and naming a polled cache FAST_STREAM_PATH would be the same
    # error as naming the legacy read FASTEST_AVAILABLE_BOOK_PATH, which this
    # module refuses two lines below. This constant waits for a real feed.
    FAST_STREAM_PATH = "FAST_STREAM_PATH"          # declared, not implemented


class Transport:
    HTTP_GET = "http_get"
    WEBSOCKET_STREAM = "websocket_stream"          # declared, not implemented


@dataclass
class BookSnapshot:
    """One book reading, with the timing of the read itself."""

    ok: bool
    request_start: clock.Instant
    response: clock.Instant | None = None
    observation_channel: str = ObservationChannel.LEGACY_COMPARABLE_BOOK_PATH
    transport: str = Transport.HTTP_GET
    feed_identity: str | None = None
    # A pushed channel fills this and leaves request/response null; a polled one
    # does the reverse. Neither is made to invent the other.
    stream_receive: clock.Instant | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    # Exact retained ladder, as (price, size) pairs of the venue's own strings
    # converted once. Ordering is left as the venue sent it; every consumer
    # sorts for itself, so nothing depends on the venue's ordering.
    asks: list[tuple[float, float]] = field(default_factory=list)
    bids: list[tuple[float, float]] = field(default_factory=list)
    venue_snapshot_ts: str | None = None   # always None today -- see the docstring
    venue_sequence: str | None = None      # always None today -- see the docstring
    provenance: str = "clob_book_http"
    error: str | None = None

    @property
    def depth_levels(self) -> int:
        return len(self.asks)


def _levels(raw) -> list[tuple[float, float]]:
    """Parse a side of the ladder, keeping exactly what the venue sent.

    A malformed level is SKIPPED rather than coerced to zero: a zero-size level
    would silently change a VWAP, whereas a missing level shortens the ladder and
    can only make a row DEPTH_EXHAUSTED, which is a state the analysis already
    handles honestly.
    """
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


async def read_book(http, base_url: str, token_id: str,
                    timeout_s: float = 5.0) -> BookSnapshot:
    """GET the book for one token. Never raises; failures come back as a snapshot.

    The request instant is taken BEFORE the call and the response instant
    immediately after, both monotonically, so the read's own duration is measured
    rather than assumed -- and so the snapshot's position on the forward curve is
    the response instant, not the moment we got round to writing the row.
    """
    feed = f"{base_url.rstrip('/')}/book"
    t0 = clock.now()
    try:
        resp = await http.get(feed,
                              params={"token_id": token_id}, timeout=timeout_s)
        t1 = clock.now()
        if resp.status_code != 200:
            return BookSnapshot(ok=False, request_start=t0, response=t1,
                                feed_identity=feed, error=f"http_{resp.status_code}")
        body = resp.json()
    except Exception as exc:                      # noqa: BLE001 -- never raise
        return BookSnapshot(ok=False, request_start=t0, response=clock.now(),
                            feed_identity=feed,
                            error=f"{type(exc).__name__}: {exc}"[:200])

    asks = _levels(body.get("asks"))
    bids = _levels(body.get("bids"))
    return BookSnapshot(
        ok=True,
        request_start=t0,
        response=t1,
        feed_identity=feed,
        # best ask is the LOWEST ask and best bid the HIGHEST bid, computed here
        # rather than trusting a position in the array -- run 81A's semantic gate
        # checked exactly this and it is cheap to keep checking.
        best_ask=min((p for p, _ in asks), default=None),
        best_bid=max((p for p, _ in bids), default=None),
        asks=asks,
        bids=bids,
        venue_snapshot_ts=None,
        venue_sequence=None,
    )
