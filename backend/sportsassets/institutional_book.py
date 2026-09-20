"""THE CURRENT INSTITUTIONAL BOOK, held in memory.

Owner directive 2026-09-20 §6: "Do not perform a new REST request for
every model decision if a valid current in-memory institutional book
exists." §6 also names what every book state must carry, and this
module is the thing that carries it.

FRESHNESS IS A STATE, NOT A COMMENT. "A stale book is not executable
evidence." So freshness is computed from the book's OWN received
timestamp against the instant it is being used, it is returned with
every read, and the execution path refuses a book that is not CURRENT
rather than walking it and hoping. A book that has gone quiet looks
exactly like a book that is genuinely unchanged, and only the clock
tells them apart.

TWO TIMESTAMPS, KEPT APART, ALWAYS:

  SOURCE_TIMESTAMP    the venue's own `transactTime` -- when the venue
                      says the book was that shape;
  RECEIVED_TIMESTAMP  when this process had it.

The difference between them is MARKET_DATA_LAG and is a real
measurement of the path. Collapsing them into one "timestamp" would
destroy the only number that says how far behind the venue we are.

THIS MODULE OPENS NOTHING. It is a store with a clock. The worker that
fills it is the one that holds the credential.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

from . import shadow_l2 as l2

CURRENT = "CURRENT"
STALE = "STALE"
ABSENT = "ABSENT"

# HOW OLD A BOOK MAY BE AND STILL BE CALLED CURRENT. A frozen literal,
# not a tunable: a threshold widened after seeing how often books miss
# it would be a threshold chosen to make the lane look live.
FRESHNESS_LIMIT_S = 5.0

# What every row this store produces says about where it came from.
EVIDENCE_ENVIRONMENT = "DIRECT_INSTITUTIONAL_WORKER"


def _now():
    return datetime.now(tz=timezone.utc)


def book_sha(bids, offers) -> str:
    """Over the VENUE'S RAW LEVELS, so it joins to the evidence row."""
    raw = json.dumps({"bids": bids or [], "offers": offers or []},
                     sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def freshness_of(received_at, *, at=None, limit_s=FRESHNESS_LIMIT_S) -> str:
    if received_at is None:
        return ABSENT
    age = ((at or _now()) - received_at).total_seconds()
    return CURRENT if age <= limit_s else STALE


class BookStore:
    """Per-instrument current books, and the scales that price them.

    THREAD-SAFE BY A PLAIN LOCK. The market-data loop writes from a
    worker thread (the venue client is synchronous and runs under
    asyncio.to_thread); the decision loop reads from the event loop.
    A dict is not a synchronisation primitive and a half-written book
    would price a trade.
    """

    def __init__(self, freshness_limit_s=FRESHNESS_LIMIT_S):
        self._lock = threading.Lock()
        self._books: dict = {}
        self._instruments: dict = {}
        self.freshness_limit_s = float(freshness_limit_s)

    # ── refdata ──────────────────────────────────────────────────────

    def put_instrument(self, symbol, record, *, price_scale, qty_scale):
        """The venue's own instrument record and ITS scales.

        A record without both scales is kept but marked unpriceable:
        knowing the venue lists an instrument is worth something even
        when its book cannot yet be converted, and it is a different
        fact from never having asked.
        """
        with self._lock:
            self._instruments[symbol] = {
                "symbol": symbol, "record": record,
                "priceScale": price_scale, "qtyScale": qty_scale,
                "priceable": bool(price_scale and qty_scale),
                "receivedAt": _now(),
            }

    def instrument(self, symbol) -> dict | None:
        with self._lock:
            row = self._instruments.get(symbol)
            return dict(row) if row else None

    def priceable_symbols(self) -> list:
        with self._lock:
            return sorted(s for s, r in self._instruments.items()
                          if r.get("priceable"))

    # ── books ────────────────────────────────────────────────────────

    def put_book(self, symbol, response, *, received_at=None,
                 venue_request_ms=None, request_id=None):
        """One /v1/orderbook/{symbol} response -> the current book.

        Returns the stored row, or None when the instrument's scales
        are not known: a book cannot be converted without them, and
        this module will not assume 100. Storing a raw, unconvertible
        book as if it were a priced one is how a $49 fill becomes
        $4,900 on an instrument whose scale is 1000.
        """
        inst = self.instrument(symbol)
        if not inst or not inst.get("priceable"):
            return None
        received = received_at or _now()
        raw_bids = (response or {}).get(l2.SIDE_BIDS) or []
        raw_offers = (response or {}).get(l2.SIDE_OFFERS) or []
        try:
            book = l2.book_from(
                response, price_scale=inst["priceScale"],
                qty_scale=inst["qtyScale"], request_id=request_id,
                received_at=received)
        except l2.L2Refusal:
            return None

        source_ts = (response or {}).get(l2.F_TRANSACT_TIME)
        row = {
            # §6's required fields, every one of them
            "INSTRUMENT_ID": symbol,
            "SOURCE_TIMESTAMP": source_ts,
            "BETTOR_RECEIVED_TIMESTAMP": received,
            "BOOK_SHA": book_sha(raw_bids, raw_offers),
            "BIDS": raw_bids,
            "OFFERS": raw_offers,
            # the parsed book the walk consumes
            "book": book,
            "priceScale": inst["priceScale"],
            "qtyScale": inst["qtyScale"],
            "venueState": (response or {}).get("state"),
            "venueRequestMs": venue_request_ms,
            "requestId": request_id,
            "evidenceEnvironment": EVIDENCE_ENVIRONMENT,
            # MARKET_DATA_LAG: the venue's own instant against ours.
            # NOT_IDENTIFIED where the venue supplied no timestamp,
            # never zero -- zero would claim a measurement.
            "MARKET_DATA_LAG_MS": _lag_ms(source_ts, received),
        }
        with self._lock:
            self._books[symbol] = row
        return dict(row)

    def current(self, symbol, *, at=None) -> dict:
        """The book, with its FRESHNESS_STATUS computed at `at`.

        Always returns a dict. An absent book is ABSENT, not None, so
        a caller that forgot to check cannot accidentally treat "no
        book" as "empty book".
        """
        with self._lock:
            row = self._books.get(symbol)
            row = dict(row) if row else None
        if row is None:
            return {"INSTRUMENT_ID": symbol, "FRESHNESS_STATUS": ABSENT,
                    "book": None,
                    "why": "no institutional book has been received for "
                           "this instrument"}
        at = at or _now()
        row["FRESHNESS_STATUS"] = freshness_of(
            row["BETTOR_RECEIVED_TIMESTAMP"], at=at,
            limit_s=self.freshness_limit_s)
        row["bookAgeMs"] = round(
            (at - row["BETTOR_RECEIVED_TIMESTAMP"]).total_seconds() * 1000, 1)
        if row["FRESHNESS_STATUS"] == STALE:
            row["why"] = (
                "the current institutional book is %.1fs old against a "
                "%.1fs freshness limit; a stale book is not executable "
                "evidence" % (row["bookAgeMs"] / 1000.0,
                              self.freshness_limit_s))
        return row

    def executable(self, symbol, *, at=None) -> dict | None:
        """The book ONLY if it is current. Otherwise nothing."""
        row = self.current(symbol, at=at)
        return row if row.get("FRESHNESS_STATUS") == CURRENT else None

    def snapshot(self) -> dict:
        with self._lock:
            return {"instruments": len(self._instruments),
                    "books": len(self._books),
                    "priceable": len([1 for r in self._instruments.values()
                                      if r.get("priceable")])}


def _lag_ms(source_ts, received):
    if not source_ts or source_ts == l2.NOT_IDENTIFIED:
        return None
    try:
        venue = datetime.fromisoformat(str(source_ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if venue.tzinfo is None:
        venue = venue.replace(tzinfo=timezone.utc)
    return round((received - venue).total_seconds() * 1000, 1)


# THE PROCESS-WIDE STORE. The market-data loop and the experimental
# loop are two tasks in one worker process, which is exactly why the
# book can be held in memory at all -- and why the store is a module
# singleton rather than something passed around.
STORE = BookStore()


def modeled_arrival(decision_at, latency_ms) -> datetime:
    """§9: the decision instant plus the FROZEN MODELED latency.

    Having L2 in memory does not mean BETTOR would have executed at the
    decision instant. This is the declared regime and it is called
    MODELED everywhere it appears; it is never represented as observed
    production execution latency, because none has been measured.
    """
    return decision_at + timedelta(milliseconds=float(latency_ms))
