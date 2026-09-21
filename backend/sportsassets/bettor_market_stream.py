"""BETTOR's own market stream. Push-latency books, with eligibility.

WHY THIS IS A SEPARATE MODULE FROM `edge/venues/pmus_stream.py`.

That streamer is a live, protected research path serving the mirror. It
is also correct FOR WHAT IT DOES: it is an accelerator over a REST
fallback, explicitly "fail-soft ... never a dependency", and a 90-second
book is fine for an engine that would otherwise poll. BETTOR's decision
bound is TEN SECONDS and it has no REST fallback behind it, so the same
cache serving the same data would be wrong here for reasons that are
not wrong there. Changing it under the mirror to suit BETTOR is how a
frozen path stops being frozen.

So this is a second reader over the same transport, and the two never
share a cache.

FOUR CORRECTIONS, each verified against the installed SDK (0.1.2) rather
than assumed:

  1. SUB_BATCH 200 -> 100. VERIFIED against the documentation, which
     says: "You can subscribe to a maximum of 100 markets per
     subscription. Use multiple subscriptions if you need more."
     `subscribe_market_data` puts the whole list in one `marketSlugs`
     array, so the batch size IS the subscription size, and 200 was
     double the ceiling on every request the streamer ever sent.

  2. THE SOURCE CLOCK AND THE MARKET STATE WERE THROWN AWAY.
     `polymarket_us.websocket.types._MarketDataPayload` declares
     `marketSlug, bids, offers, state, stats, transactTime`. The old
     cache kept `{k: md[k] for k in ("bids","offers","asks")}` -- which
     also asks for `asks`, a key the payload does not have, so the
     guess is visible in the code. Dropping `transactTime` leaves only
     OUR receipt clock, and an engine with one clock cannot tell a slow
     venue from a slow consumer. Dropping `state` means a HALTED market
     looks exactly like a quiet one: on 2026-09-05 the venue was halted
     venue-wide for five hours with `bestBid: null` on every market.

  3. NO 90-SECOND DEFAULT. There is no default age at all here.
     `book_at()` requires the caller's bound, because the one number
     that decides whether a book may be traded on is not a default.

  4. A DISCONNECT INVALIDATES EVERY BOOK. The old cache kept serving
     entries across a drop, so a reconnect that took 40 seconds served
     40-second-old books as live for the rest of the 90-second window.
     Here a disconnect stamps an epoch; every book cached in a previous
     epoch is INELIGIBLE regardless of age, and stays ineligible until
     that market sends a fresh full book on the current connection.

FIFTH, AND IT WAS NOT ON THE LIST: SUBSCRIPTION WAS FIRE-AND-FORGET.
The old code added slugs to `_subscribed` in `ensure()` -- before the
request was even sent -- and never looked at the result. There is no
positive acknowledgment in this protocol: `MarketMessage` is
`MarketData | MarketDataLite | Trade | Heartbeat | WebSocketErrorMessage`
and carries no "subscribed" reply. Confirmation is therefore the
ARRIVAL OF DATA for a slug, and refusal is an `error` message carrying
the `requestId` of the batch. Both are tracked, so `subscribed` stops
being a claim: a slug is REQUESTED when its batch is sent, CONFIRMED
when its first book arrives, and FAILED when its batch's requestId
comes back as an error.

BOOK REPLACEMENT SEMANTICS ARE TREATED AS UNVERIFIED. A
`SUBSCRIPTION_TYPE_MARKET_DATA` message carries whole `bids`/`offers`
arrays and the SDK gives no delta type, which is consistent with full
replacement -- and "consistent with" is not "verified". This module
REPLACES on every message, which is the only safe reading of the two:
if the venue were sending deltas, replacing would show as a visibly
thin book rather than as a silently wrong one. `describe()` records the
status as ASSUMED_FULL_REPLACEMENT so it cannot be promoted by habit.

NO ORDER PATH. This module subscribes to market data and trades. It
imports no order function and holds no position state.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

STREAM_VERSION = "BETTOR_MARKET_STREAM_V1"

# The documented per-subscription ceiling. `subscribe_market_data` sends
# the whole list in one `marketSlugs` array, so this is the batch size.
SUB_BATCH = 100
# A bound on the universe this reader will hold at once. BETTOR's
# universe is selected for liquidity and activity, not swept.
MAX_SUBSCRIPTIONS = 600

# Book eligibility, reported rather than silently applied.
ELIGIBLE = "ELIGIBLE"
NO_BOOK = "NO_BOOK_RECEIVED"
STALE_SOURCE = "STALE_AT_VENUE_CLOCK"
STALE_RECEIPT = "STALE_AT_RECEIPT_CLOCK"
DISCONNECTED = "INVALIDATED_BY_DISCONNECT"
NOT_OPEN = "MARKET_NOT_OPEN"
NO_SOURCE_TS = "NO_VENUE_SOURCE_TIMESTAMP"

# Subscription lifecycle. "Subscribed" was a claim; these are facts.
REQUESTED = "REQUESTED"
CONFIRMED = "CONFIRMED_BY_DATA"
FAILED = "FAILED_BY_ERROR"

BOOK_REPLACEMENT = "ASSUMED_FULL_REPLACEMENT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketStream:
    """Latest full book per slug, with the venue's clock and state.

    Thread-safe. The socket runs on its own thread; every accessor takes
    the lock and returns plain data.
    """

    def __init__(self, key_id: str, secret_key: str, *,
                 on_book=None, on_trade=None, autostart: bool = False) -> None:
        self._key_id = key_id
        self._secret_key = secret_key
        self._lock = threading.Lock()
        # slug -> book record. Every record carries BOTH clocks and the
        # epoch it arrived in.
        self._books: dict[str, dict] = {}
        self._subs: dict[str, dict] = {}       # slug -> {state, request_id}
        self._req_slugs: dict[str, list] = {}  # requestId -> slugs
        self._pending: list = []
        self._trades: list = []
        # NAMED `_book_cb` / `_trade_cb`, not `_on_book` / `_on_trade`:
        # the handlers are methods with those names and an attribute
        # would shadow them, so `ws.on("trade", self._on_trade)` would
        # have bound the user's callback in place of the handler.
        self._book_cb = on_book
        self._trade_cb = on_trade

        self.connected = False
        # THE EPOCH IS THE INVALIDATION MECHANISM. It increments on every
        # successful connect, so a book cached before a drop can never be
        # mistaken for one cached after it.
        self.epoch = 0
        self.updates = 0
        self.trade_count = 0
        self.reconnects = 0
        self.errors: list = []
        self.connected_since: str | None = None
        self.first_connected_at: str | None = None
        self._stop = False
        self._thread = None
        if autostart:
            self.start()

    # ── control ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="bettor-market-stream")
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def subscribe(self, slugs) -> dict:
        """Queue slugs. They are REQUESTED, not subscribed, until data."""
        with self._lock:
            room = MAX_SUBSCRIPTIONS - len(self._subs)
            fresh = []
            for s in slugs:
                if s and s not in self._subs and s not in fresh:
                    fresh.append(s)
            dropped = max(0, len(fresh) - max(room, 0))
            fresh = fresh[:max(room, 0)]
            for s in fresh:
                self._subs[s] = {"state": REQUESTED, "request_id": None,
                                 "requested_at": _now_iso()}
            self._pending.extend(fresh)
        return {"queued": len(fresh), "dropped_over_cap": dropped,
                "cap": MAX_SUBSCRIPTIONS}

    def prune(self, keep) -> int:
        """Drop every slug outside `keep`. Returns how many went.

        Without this the subscription set only ever grows: ended games
        hold their last book forever, dead slugs are resubscribed on
        every reconnect, and because the cap is measured against the
        LIFETIME count, new markets eventually stop streaming
        altogether.

        A straggler update for a pruned slug just re-caches one entry
        that the next prune removes again -- and it cannot be decided
        on, because `decide_slug` only looks at slugs the worker
        marked dirty from a live subscription.
        """
        keep = set(keep)
        with self._lock:
            gone = [s for s in self._subs if s not in keep]
            for s in gone:
                self._subs.pop(s, None)
                self._books.pop(s, None)
            self._pending = [s for s in self._pending if s in keep]
        return len(gone)

    # ── reads ────────────────────────────────────────────────────────

    def book_at(self, slug: str, *, decided_at=None,
                max_source_age_s: float,
                max_receipt_age_s: float) -> dict:
        """The book for `slug` and WHETHER IT MAY BE TRADED ON.

        Both bounds are REQUIRED. The old reader defaulted to 90 s,
        which is the number a caller is least able to supply by
        accident and most likely to inherit by accident.

        Returns {"eligible": bool, "reason": str, "book": dict|None,
        "source_ts", "received_at", "decided_at", ages}. It never
        returns a bare book: a book without its eligibility is exactly
        what a caller will use without checking.
        """
        now = time.time()
        d_iso = decided_at or _now_iso()
        with self._lock:
            rec = self._books.get(slug)
            epoch, connected = self.epoch, self.connected
        out = {"slug": slug, "decided_at": d_iso, "book": None,
               "source_ts": None, "received_at": None,
               "source_age_s": None, "receipt_age_s": None,
               "venue_state": None, "epoch": epoch, "connected": connected}
        if rec is None:
            return dict(out, eligible=False, reason=NO_BOOK)

        out.update({"book": rec["book"], "source_ts": rec["source_ts"],
                    "received_at": rec["received_at_iso"],
                    "venue_state": rec["state"],
                    "receipt_age_s": round(now - rec["received_at"], 4)})

        # 4. A BOOK FROM A PREVIOUS CONNECTION IS NOT A LIVE BOOK.
        if rec["epoch"] != epoch or not connected:
            return dict(out, eligible=False, reason=DISCONNECTED)
        if rec["source_ts"] is None:
            return dict(out, eligible=False, reason=NO_SOURCE_TS)

        src = _parse_ts(rec["source_ts"])
        if src is None:
            return dict(out, eligible=False, reason=NO_SOURCE_TS)
        dec = _parse_ts(d_iso)
        age = (dec - src).total_seconds() if dec else None
        out["source_age_s"] = round(age, 4) if age is not None else None

        if rec["state"] not in (None, "MARKET_STATE_OPEN"):
            return dict(out, eligible=False, reason=NOT_OPEN)
        if age is None or age > max_source_age_s or age < 0:
            return dict(out, eligible=False, reason=STALE_SOURCE)
        if out["receipt_age_s"] > max_receipt_age_s:
            return dict(out, eligible=False, reason=STALE_RECEIPT)
        return dict(out, eligible=True, reason=ELIGIBLE)

    def _subscription_report_locked(self) -> dict:
        """CALLER MUST HOLD `self._lock`.

        Split out because `stats()` took the lock and then called
        `subscription_report()`, which took it again -- and
        `threading.Lock` is not reentrant, so `stats()` deadlocked the
        first time the harness called it. An RLock would have hidden
        that; naming which methods hold the lock does not.
        """
        by: dict = {}
        for s in self._subs.values():
            by[s["state"]] = by.get(s["state"], 0) + 1
        return {"requested": len(self._subs), "by_state": by,
                "confirmed": by.get(CONFIRMED, 0),
                "failed": by.get(FAILED, 0),
                "pending_send": len(self._pending)}

    def subscription_report(self) -> dict:
        with self._lock:
            return self._subscription_report_locked()

    def drain_trades(self) -> list:
        """Trades since the last drain. Cleared, so nothing double-counts."""
        with self._lock:
            out, self._trades = self._trades, []
        return out

    def stats(self) -> dict:
        with self._lock:
            return {"stream": STREAM_VERSION, "connected": self.connected,
                    "epoch": self.epoch, "books_cached": len(self._books),
                    "updates": self.updates, "trades": self.trade_count,
                    "reconnects": self.reconnects,
                    "errors": list(self.errors[-10:]),
                    "connected_since": self.connected_since,
                    "first_connected_at": self.first_connected_at,
                    "subscriptions": self._subscription_report_locked()}

    # ── socket thread ────────────────────────────────────────────────

    def _on_market_data(self, message: dict) -> None:
        md = (message or {}).get("marketData") or {}
        slug = md.get("marketSlug")
        if not slug:
            return
        now = time.time()
        rec = {
            # 2. BOTH CLOCKS AND THE STATE, which the old cache dropped.
            "source_ts": md.get("transactTime"),
            "received_at": now,
            "received_at_iso": _now_iso(),
            "state": md.get("state"),
            "stats": md.get("stats") or {},
            # `offers`, not `asks`. The payload has no `asks` key; the
            # old reader asked for one.
            "book": {"bids": md.get("bids") or [],
                     "offers": md.get("offers") or []},
            "epoch": self.epoch,
            "replacement": BOOK_REPLACEMENT,
        }
        with self._lock:
            self._books[slug] = rec
            self.updates += 1
            sub = self._subs.get(slug)
            if sub is not None and sub["state"] != CONFIRMED:
                # 5. DATA IS THE ONLY CONFIRMATION THIS PROTOCOL OFFERS.
                sub["state"] = CONFIRMED
                sub["confirmed_at"] = rec["received_at_iso"]
            cb = self._book_cb
        if cb:
            try:
                cb(slug, rec)
            except Exception as exc:  # noqa: BLE001 -- never stop the feed
                log.debug("book listener failed: %s", exc)

    def _on_trade(self, message: dict) -> None:
        t = (message or {}).get("trade") or {}
        slug = t.get("marketSlug")
        if not slug:
            return
        rec = {"slug": slug, "price": _amount(t.get("price")),
               "quantity": _amount(t.get("quantity")),
               "trade_time": t.get("tradeTime"),
               "received_at": _now_iso(),
               # Which side was passive. This is the one public signal
               # about maker participation, and it is why trades are
               # subscribed at all.
               "maker_side": (t.get("maker") or {}).get("side"),
               "taker_side": (t.get("taker") or {}).get("side"),
               "epoch": self.epoch}
        with self._lock:
            self._trades.append(rec)
            self.trade_count += 1
            cb = self._trade_cb
        if cb:
            try:
                cb(rec)
            except Exception as exc:  # noqa: BLE001
                log.debug("trade listener failed: %s", exc)

    def _on_error(self, err=None) -> None:
        """An error naming a requestId FAILS that batch's slugs.

        The old reader logged errors at debug and moved on, so a
        subscription the venue refused was indistinguishable from one
        that simply had no trades.
        """
        rid = getattr(err, "request_id", None) or getattr(err, "requestId", None)
        with self._lock:
            self.errors.append({"at": _now_iso(), "error": str(err),
                                "request_id": rid})
            for slug in self._req_slugs.get(rid, []):
                sub = self._subs.get(slug)
                if sub is not None and sub["state"] != CONFIRMED:
                    sub["state"] = FAILED
                    sub["error"] = str(err)

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # noqa: BLE001
            log.error("bettor market stream died: %s", exc)
            with self._lock:
                self.connected = False
                self.errors.append({"at": _now_iso(), "fatal": str(exc)})

    async def _main(self) -> None:
        from polymarket_us.websocket.markets import MarketsWebSocket

        backoff, seq = 1.0, 0
        while not self._stop:
            open_flag = {"v": False}
            ws = None
            try:
                ws = MarketsWebSocket(key_id=self._key_id,
                                      secret_key=self._secret_key)
                ws.on("market_data", self._on_market_data)
                ws.on("trade", self._on_trade)
                ws.on("error", self._on_error)
                ws.on("close", lambda *a: open_flag.update(v=False))
                await ws.connect()
                open_flag["v"] = True
                with self._lock:
                    # NEW EPOCH. Everything cached before this instant is
                    # now ineligible, whatever its age.
                    self.epoch += 1
                    self.connected = True
                    self.connected_since = _now_iso()
                    if self.first_connected_at is None:
                        self.first_connected_at = self.connected_since
                    # Every known slug goes back to REQUESTED: a
                    # subscription does not survive a socket.
                    for s in self._subs.values():
                        s["state"] = REQUESTED
                    self._pending = list(self._subs)
                backoff = 1.0
                while open_flag["v"] and not self._stop:
                    with self._lock:
                        batch = self._pending[:SUB_BATCH]
                        self._pending = self._pending[SUB_BATCH:]
                    if not batch:
                        await asyncio.sleep(0.25)
                        continue
                    seq += 1
                    for kind, rid in (("book", "bk-%d" % seq),
                                      ("trade", "tr-%d" % seq)):
                        with self._lock:
                            self._req_slugs[rid] = list(batch)
                            if kind == "book":
                                for s in batch:
                                    self._subs[s]["request_id"] = rid
                        if kind == "book":
                            await ws.subscribe_market_data(rid, batch)
                        else:
                            await ws.subscribe_trades(rid, batch)
            except Exception as exc:  # noqa: BLE001 -- reconnect
                log.warning("bettor market stream disconnected: %s", exc)
                with self._lock:
                    self.errors.append({"at": _now_iso(), "error": str(exc)})
            finally:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:  # noqa: BLE001
                        pass
            with self._lock:
                self.connected = False
                self.connected_since = None
                self.reconnects += 1
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _amount(a):
    """An Amount's value as a string, or None. `{value, currency}`."""
    if isinstance(a, dict):
        return a.get("value")
    return str(a) if a is not None else None


def _parse_ts(x):
    """An AWARE datetime, or None. A naive stamp is refused."""
    if x is None:
        return None
    t = str(x).strip()
    if not t or t == "NOT_IDENTIFIED":
        return None
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    if "." in t:
        head, rest = t.split(".", 1)
        digits = ""
        for ch in rest:
            if ch.isdigit():
                digits += ch
            else:
                break
        t = "%s.%s%s" % (head, digits[:6].ljust(6, "0"), rest[len(digits):])
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo and dt.tzinfo.utcoffset(dt) is not None else None


def to_observation(slug: str, at: dict, *, outcome_leg: str) -> dict:
    """A `book_at()` result -> the adapter's input row.

    ONE NORMALIZER FOR EVERY SOURCE. The stream, the REST reader and the
    captured rows all go through `bettor_observation_adapter.normalize`,
    so the top-of-book-versus-cumulative correction lives in one place.

    The stream's ladder levels carry `px`/`qty` and NO level index --
    unlike the capture's `multi_level_depth`, which marks each one. The
    adapter falls back to array order when no level is marked, and here
    that fallback is CORRECT BY DOCUMENTATION rather than by luck:
    "Order book levels are sorted best-to-worst (highest bid first,
    lowest ask first)." The index is written out explicitly so nothing
    downstream has to know that.

    `qty` "may contain decimals for partial-contract markets", so it is
    carried as a string and parsed as a float, never as an int.
    """
    book = at.get("book") or {}
    bids = [{"level": i, "qty": _amount(l.get("qty")) or str(l.get("qty")),
             "price": _amount(l.get("px"))}
            for i, l in enumerate(book.get("bids") or [])
            if isinstance(l, dict)]
    asks = [{"level": i, "qty": _amount(l.get("qty")) or str(l.get("qty")),
             "price": _amount(l.get("px"))}
            for i, l in enumerate(book.get("offers") or [])
            if isinstance(l, dict)]
    row = {
        "observation_id": "%s@%s" % (slug, at.get("source_ts") or "NO_TS"),
        "market_id": slug, "instrument_id": slug, "outcome_leg": outcome_leg,
        "book_source_ts": at.get("source_ts") or "NOT_IDENTIFIED",
        "book_received_ts": at.get("received_at"),
        "observed_at": at.get("received_at"),
        "venue_state": at.get("venue_state"),
        "book_readability_status": "READABLE",
        "yes_bid": bids[0]["price"] if bids else "NOT_IDENTIFIED",
        "yes_ask": asks[0]["price"] if asks else "NOT_IDENTIFIED",
        # A separate instrument and a separate read. Deriving 1 - own
        # asserts the identity Class C falsified on 3,732 observations.
        "no_bid": "NOT_IDENTIFIED", "no_ask": "NOT_IDENTIFIED",
        "stream": STREAM_VERSION,
    }
    if bids or asks:
        row["multi_level_depth"] = {"bid": bids, "ask": asks,
                                    "levels": max(len(bids), len(asks))}
        row["yes_depth"] = {
            "bid": "%.4f" % sum(float(b["qty"] or 0) for b in bids),
            "ask": "%.4f" % sum(float(a["qty"] or 0) for a in asks),
            "levelsCaptured": max(len(bids), len(asks)),
            "isCumulative": "the sum ACROSS levels, not the touch",
        }
    if at.get("source_age_s") is not None:
        row["book_age_s"] = "%.6f" % at["source_age_s"]
    return row


def describe() -> dict:
    return {
        "stream": STREAM_VERSION,
        "sub_batch": SUB_BATCH,
        "max_subscriptions": MAX_SUBSCRIPTIONS,
        "submits_orders": False,
        "separate_from": ("edge/venues/pmus_stream.py, which is a "
                          "protected research path and is not modified"),
        "corrections": {
            "sub_batch": "200 -> 100, the documented per-request ceiling",
            "clocks": ("transactTime and state were discarded; both are "
                       "kept, and an engine with one clock cannot tell a "
                       "slow venue from a slow consumer"),
            "no_default_age": ("book_at() requires both bounds; the one "
                               "number deciding tradeability is not a "
                               "default"),
            "disconnect": ("a connect increments an epoch and every book "
                           "from an earlier epoch is INELIGIBLE whatever "
                           "its age, until that market sends a fresh "
                           "book on the current connection"),
            "subscription": ("was fire-and-forget and optimistic. This "
                             "protocol has no positive acknowledgment: "
                             "MarketMessage carries no 'subscribed' "
                             "reply, so confirmation is the ARRIVAL OF "
                             "DATA and refusal is an error naming the "
                             "batch's requestId"),
        },
        "verified_against": ["polymarket_us 0.1.2 websocket/types.py",
                             "docs.polymarket.us/api-reference/websocket/"
                             "markets, fetched 2026-09-21"],
        "documented_limits": {
            "max_markets_per_subscription": 100,
            "level_order": "best-to-worst; highest bid first, lowest ask "
                           "first (so array index IS the level)",
            "qty": "may contain decimals for partial-contract markets",
        },
        "debouncing_not_used": (
            "the protocol accepts `responsesDebounced: true` to batch "
            "updates, and the SDK's subscribe_market_data does not send "
            "it -- it writes only requestId, subscriptionType and "
            "marketSlugs. Using it would need a raw send(). Left off: "
            "debounced updates arrive on the venue's interval rather "
            "than on the book's change, which is the opposite of what a "
            "10-second decision bound wants"),
        "payload_fields": ["marketSlug", "bids", "offers", "state", "stats",
                           "transactTime"],
        "book_replacement": BOOK_REPLACEMENT,
        "book_replacement_note": (
            "whole bids/offers arrays with no delta type in the SDK is "
            "CONSISTENT WITH full replacement and is not verification. "
            "Replacing is the safe reading of the two: if the venue sent "
            "deltas, replacing shows a visibly thin book rather than a "
            "silently wrong one"),
        "eligibility_reasons": [ELIGIBLE, NO_BOOK, STALE_SOURCE,
                                STALE_RECEIPT, DISCONNECTED, NOT_OPEN,
                                NO_SOURCE_TS],
    }
