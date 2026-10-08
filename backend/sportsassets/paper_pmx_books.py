"""THE PMX INSTITUTIONAL BOOK AS THE PAPER CONSUMERS' PRIMARY BOOK SOURCE.

PAPER BY NAME, AND BY THE REPOSITORY'S OWN GUARD. The consumers are the paper
market-data owner (paper_market_data: the paper pass, Derek, the benchmark,
the held-mark refresh) and the collector's paper / calibration book reads
(ext_pinnacle_loop.venue_quote under PMX_BOOK_BEFORE_REST_RULE). The `paper_`
prefix puts this module under tests/test_paper_records_cannot_reach_the_
funded_path: no funded module may import it, so no funded or actual decision
is ever priced from it (a stream book reaches those only under the owner's
P5 live-stream-book approval, elsewhere).

THE DEFECT (production, release 7fd4574e = RC4; pm-acceptance run
37738089957, read-only readbacks). The approved architecture names PMX
institutional gRPC the PRIMARY venue book source; production read none of it.

  * the deciding process (sportsassets-api) held its own PMX stream
    CONNECTED: 30 symbols requested, 30 acknowledged by the venue, 14
    resident L2 books current under the decision bound and 23 under the
    held-mark bound (paper_freshness.json market_data.streams.institutional;
    the completion readback, at another instant: 30 and 30), 138 refdata
    reads since boot, the same-book tap 85 AGREE_TOP_N of 227 samples;
  * yet every venue book a consumer used was a REST read: held marks
    REST 4 / INSTITUTIONAL_STREAM 0 (completion.json market_data.pmx_primary,
    why NO_HELD_MARK_FROM_THE_STREAM), the paper owner 243 REST dispatches
    and 19 HTTP 429s since boot, and the collector's per-candidate book read
    (`ext_pinnacle_loop.venue_quote`) always `_read_book_blocking`;
  * the dedicated plane's consumer bridge saw 115 priority PMX books against
    7 REST books in its window, top-of-book equal 7 of 7
    (market_plane.json consumer_parity) -- SHADOW, never a consumer;
  * and the SOFTWARE first-loss census after RC4 is dominated by that REST
    budget: QUOTE_STALE_ON_ARRIVAL 35 / h and PROBABILITY_DEADLINE_PASSED
    13 / h (candidates queued behind ~0.23 req/s of authenticated book reads
    answered 429 with Retry-After 7-10 s), BOOK_READ_DID_NOT_FINISH_INSIDE_
    THE_DECISION_DEADLINE 2 / h.

THE RULE. A consumer about to make a REST book read first asks
`consumer_read(slug, consumer=...)`. The resident PMX book stands in for the
REST read ONLY when, for THIS slug, every one of these holds:

  (a) EXACT IDENTITY: the PMX stream runs in this process and its identity
      mapper (institutional_api_stream.identity_mapper, on the refdata this
      process holds) maps the retail slug to the institutional symbol
      exactly: same registered contract (symbol == slug), the YES leg is the
      instrument's LONG side, price_transform IDENTITY, its own scales;
  (b) ACKNOWLEDGED: the venue acknowledged this symbol's subscription on the
      CURRENT connection (institutional_stream evidence `subscription`);
  (c) CURRENT: the stream's own `current()` answers ok under the DECISION
      bound (institutional_stream.MAX_SNAPSHOT_AGE_S, unchanged): running,
      connected, no gap, a full update on this connection, alive within
      MAX_SILENCE_S, venue clock present, open, not hidden, not crossed;
  (d) INSIDE THE EXISTING LIMIT: our receipt of the book is at most
      MAX_RECEIPT_AGE_S old -- = paper_market_data.CACHE_MAX_AGE_S =
      bettor_paper_guard.SHARED_BOOK_MAX_AGE_S (6 s; a test pins all three):
      the age at which the paper owner ALREADY answers a reader without a
      venue request. A PMX book is never admitted older than a shared REST
      read may be. A reader's `not_before_epoch` is honoured exactly as the
      cache honours it;
  (e) SAME BOOK: THIS symbol's own same-book evidence is SUPPORTED -- the
      window rule (institutional_same_book.same_book_by_symbol, exact
      samples at one venue instant) or a durable certificate whose
      fingerprint equals the identity in force now
      (paper_market_data.effective_same_book) -- loaded from the database
      within SAME_BOOK_EVIDENCE_MAX_AGE_S. Aggregate agreement never admits
      a symbol, and evidence that has not been loaded recently is absent.

Otherwise the consumer reads REST EXACTLY AS BEFORE. Every answer is counted
by consumer and by source (PMX_GRPC / REST) and every fallback by its reason
(`telemetry()`), so consumer use is demonstrable, not asserted.

WHAT THE READ IS. The REST read's own shape -- {"marketData": {bids, offers,
state, transactTime}, "observed_at"} -- built from the instrument's own
scales, with `observed_at` = OUR RECEIPT of the last full update (never
"now": nothing is made fresher than it is) and `transactTime` the venue's
clock (provenance, decided on by nobody, exactly as on a REST read). It is
the LONG instrument's book in the retail book's own price space (the retail
/v1/markets/{slug}/book is the same instrument's book); a SHORT side is the
complement computed by bettor_book_snapshot exactly as off a REST read.
Depth is the stream's DEPTH requested levels; deeper levels are absent,
never invented (a walk past them sees less depth, never more). It carries
`book_source: PMX_GRPC` so the recorded observation names its origin.

THE DEDICATED PLANE IS NOT A CONSUMER SOURCE HERE, BY ITS OWN PERSISTED SHAPE.
Its books live in another process; what it persists is one PRIORITY_PMX_BOOKS
event per snapshot carrying best bid / best offer, the venue clock and the
receipt -- no sizes, no depth -- at most once a minute, and the plane is
OOM-cycling (2026-10-08 since 05:16Z, ~20-60 min). No decision can size
against a top without a quantity, and a once-a-minute top is never inside
the 6 s limit above; so a stale, absent or restarted plane can never serve a
consumer, by construction, and every consumer falls back to REST exactly as
today. What WOULD make plane-held books consumable cross-process (an L2
publication with receipt, connection epoch and ack per priority member) is
named in the release notes, not improvised here.

READ ONLY, NO AUTHORITY. Nothing here sends a request: the stream, the
identity mapper and the evidence are read in memory and from the database.
No order, cancel, funding or live path is reachable; PinnAPI's probability
authority is not read or touched; no freshness rule, threshold or gate moves.
PMX_CONSUMER_BOOKS=off restores RC4 behaviour exactly (every read REST).
"""
from __future__ import annotations

import os
import threading
import time
from decimal import Decimal

VERSION = "PMX_CONSUMER_BOOKS_V1"
ENV_FLAG = "PMX_CONSUMER_BOOKS"

SOURCE_PMX = "PMX_GRPC"
SOURCE_REST = "REST"
SOURCES = (SOURCE_PMX, SOURCE_REST)

#: (d) = paper_market_data.CACHE_MAX_AGE_S = bettor_paper_guard.
#: SHARED_BOOK_MAX_AGE_S (a test pins the three equal). Never wider.
MAX_RECEIPT_AGE_S = 6.0
#: A venue clock this far ahead of our clock is not a clock we can read
#: (the held-mark rule's own tolerance, paper_market_data.
#: institutional_held_book: source age >= -5 s).
VENUE_CLOCK_FUTURE_TOLERANCE_S = 5.0
#: (e) per-symbol same-book evidence is re-read at most this often...
SAME_BOOK_REFRESH_S = 60.0
#: ...and evidence loaded longer ago than this is ABSENT (a loader that
#: stopped never leaves an old SUPPORTED standing).
SAME_BOOK_EVIDENCE_MAX_AGE_S = 600.0
#: the symbols one evidence load asks about (the stream's own bound)
SAME_BOOK_MAX_SYMBOLS = 200

#: the consumers, by name (the counters' keys)
C_PAPER_OWNER = "PAPER_MARKET_DATA_OWNER"
C_COLLECTOR = "COLLECTOR_VENUE_QUOTE"

# ── why a PMX book did not serve (each falls back to REST, counted) ───
R_OFF = "PMX_CONSUMER_BOOKS_SWITCHED_OFF"
R_FOREIGN = "PMX_CONSUMER_NOT_A_POLYMARKET_US_SLUG"
R_NOT_RUNNING = "PMX_STREAM_NOT_RUNNING_IN_THIS_PROCESS"
R_IDENTITY = "PMX_IDENTITY_NOT_PROVEN_EXACT"
R_SYMBOL = "PMX_SYMBOL_IS_NOT_THE_RETAIL_SLUG"
R_ORIENTATION = "PMX_ORIENTATION_NOT_ESTABLISHED"
R_NOT_ACKED = "PMX_SUBSCRIPTION_NOT_ACKNOWLEDGED_ON_THIS_CONNECTION"
#: the stream's own refusal is carried after the colon (institutional_stream
#: R_*), e.g. PMX_STREAM_REFUSED:SNAPSHOT_OLDER_THAN_THE_BOUND
R_STREAM = "PMX_STREAM_REFUSED"
R_RECEIPT_OLD = "PMX_BOOK_RECEIPT_OLDER_THAN_THE_CONSUMER_LIMIT"
R_NOT_BEFORE = "PMX_BOOK_RECEIVED_BEFORE_THE_READERS_NOT_BEFORE_INSTANT"
R_VENUE_CLOCK = "PMX_VENUE_CLOCK_UNKNOWN_OR_AHEAD_OF_OURS"
R_SAME_BOOK = "PMX_SAME_BOOK_NOT_PROVEN_FOR_THIS_SYMBOL"
R_SAME_BOOK_UNLOADED = "PMX_SAME_BOOK_EVIDENCE_NOT_LOADED_RECENTLY"
R_LEVELS = "PMX_BOOK_LEVELS_UNREADABLE"
R_RAISED = "PMX_SOURCE_RAISED"


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


def _iso_epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")
                                      ).timestamp()
    except (TypeError, ValueError):
        return None


def _wire(levels) -> list:
    """Exact (price, size) -> the retail book's wire level shape."""
    return [{"px": {"value": format(p.normalize(), "f")},
             "qty": format(q.normalize(), "f")}
            for p, q in levels if isinstance(p, Decimal)]


def arbitrate(slug: str, *, identity, current, same_book, now: float,
              max_receipt_age_s: float = MAX_RECEIPT_AGE_S,
              not_before_epoch=None) -> dict:
    """PURE. {"ok": True, "read": <REST-shaped read>} or {"ok": False,
    "refusal", "why"} for ONE slug. `identity` is the identity mapper's
    answer for (slug, YES); `current` the stream's DECISION-bound
    `current(symbol)`; `same_book` THIS symbol's effective evidence
    ({"status", "detail"}, None when absent); `max_receipt_age_s` the
    consumer's existing limit, never above MAX_RECEIPT_AGE_S."""
    from . import institutional_same_book as SB

    def refuse(name, why):
        return {"ok": False, "refusal": name, "why": why}

    limit = min(float(max_receipt_age_s), MAX_RECEIPT_AGE_S)
    ident = identity if isinstance(identity, dict) else None
    if not ident or ident.get("status") != "EXACT":
        return refuse(R_IDENTITY, "no exact retail->institutional identity "
                      "for %r on the refdata this process holds" % slug)
    sym = ident.get("symbol") or ident.get("institutional_symbol")
    if sym != slug:
        return refuse(R_SYMBOL, "identity names %r, not %r" % (sym, slug))
    if ident.get("institutional_side") != "LONG" or \
            ident.get("price_transform") != "IDENTITY":
        return refuse(R_ORIENTATION, "side %r / transform %r: not shown to "
                      "be the retail long-side book in its own price space"
                      % (ident.get("institutional_side"),
                         ident.get("price_transform")))
    cur = current if isinstance(current, dict) else {}
    if not cur.get("ok") or cur.get("symbol") not in (None, slug):
        return refuse("%s:%s" % (R_STREAM, cur.get("refusal") or "NO_ANSWER"),
                      "stream current(): %s" % (cur.get("why") or "no answer"))
    ev = cur.get("evidence") or {}
    sub = ev.get("subscription") or {}
    if sub.get("acked_on_current_connection") is not True:
        return refuse(R_NOT_ACKED, "the venue has not acknowledged %s on "
                      "connection %s" % (slug, (ev.get("connection") or {})
                                         .get("seq")))
    snap = ev.get("snapshot") or {}
    recv = snap.get("received_at")
    vts = _iso_epoch(snap.get("venue_ts"))
    try:
        receipt_age = float(now) - float(recv)
    except (TypeError, ValueError):
        return refuse(R_RECEIPT_OLD, "receipt instant unknown")
    if not (0.0 <= receipt_age <= limit):
        return refuse(R_RECEIPT_OLD, "received %.1f s ago; the consumer's "
                      "limit is %.0f s" % (receipt_age, limit))
    if not_before_epoch is not None and float(recv) < float(not_before_epoch):
        return refuse(R_NOT_BEFORE, "received %.3f, the reader needs a book "
                      "received at or after %.3f" % (float(recv),
                                                     float(not_before_epoch)))
    if vts is None or vts - float(now) > VENUE_CLOCK_FUTURE_TOLERANCE_S:
        return refuse(R_VENUE_CLOCK, "venue clock %r" % snap.get("venue_ts"))
    sbk = same_book if isinstance(same_book, dict) else {}
    if sbk.get("status") != "SUPPORTED":
        return refuse(sbk.get("refusal") or R_SAME_BOOK,
                      "same-book evidence for %s is %s (%s)"
                      % (slug, sbk.get("status") or "ABSENT",
                         sbk.get("detail")))
    lv = SB.stream_levels(cur)
    if lv is None:
        return refuse(R_LEVELS, "levels not scaled by the instrument's own "
                      "scales")
    mkt = ev.get("market") or {}
    conn = ev.get("connection") or {}
    return {"ok": True, "read": {
        "marketData": {"bids": _wire(lv["bids"]),
                       "offers": _wire(lv["offers"]),
                       "state": mkt.get("state"),
                       "transactTime": snap.get("venue_ts")},
        # OUR receipt of the last full update: never now
        "observed_at": float(recv),
        "error": None,
        "feed": SOURCE_PMX, "book_source": SOURCE_PMX,
        "pmx": {"version": VERSION, "symbol": slug,
                "receipt_age_s": round(receipt_age, 3),
                "venue_clock_age_s": round(float(now) - vts, 3),
                "venue_clock_decides_nothing": True,
                "limit_s": limit,
                "connection_seq": conn.get("seq"),
                "connection_id": conn.get("id"),
                "acked_seq": sub.get("acked_seq"),
                "depth_levels": ev.get("depth"),
                "identity_version": ident.get("version"),
                "same_book": sbk.get("detail"),
                "orientation": {"book": "LONG_INSTRUMENT",
                                "price_transform": "IDENTITY"}}}}


# ═════════════════════════════════════════════════════════════════════
# (e) PER-SYMBOL SAME-BOOK EVIDENCE, LOADED BY AN ASYNC CALLER
# ═════════════════════════════════════════════════════════════════════

class Evidence:
    """Per-symbol same-book evidence this process loaded from the database
    (window + durable certificates), with the instant of the load. The
    consumers read it synchronously (from worker threads); the loaders
    (`refresh_evidence`) run on the event loop with a connection."""

    def __init__(self, *, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.window: dict = {}
        self.durable: dict = {}
        self.loaded_at: dict = {}
        self.last_refresh_at = None
        self.last_refresh = None

    def put(self, symbols, window, durable, *, at=None) -> None:
        t = float(self._clock() if at is None else at)
        with self._lock:
            for s in symbols:
                self.window[s] = (window or {}).get(s)
                self.durable[s] = (durable or {}).get(s)
                self.loaded_at[s] = t
            # bounded: symbols not asked about for long are dropped
            for s in [s for s, at_ in self.loaded_at.items()
                      if t - at_ > SAME_BOOK_EVIDENCE_MAX_AGE_S]:
                self.window.pop(s, None)
                self.durable.pop(s, None)
                self.loaded_at.pop(s, None)

    def effective(self, symbol, ident, *, now=None) -> dict:
        """THIS symbol's effective evidence (paper_market_data.
        effective_same_book) or a named absence."""
        at = float(self._clock() if now is None else now)
        with self._lock:
            loaded = self.loaded_at.get(symbol)
            w = self.window.get(symbol)
            d = self.durable.get(symbol)
        if loaded is None or at - loaded > SAME_BOOK_EVIDENCE_MAX_AGE_S:
            return {"status": "ABSENT", "refusal": R_SAME_BOOK_UNLOADED,
                    "detail": {"loaded_at": loaded}}
        from .paper_market_data import effective_same_book
        got = effective_same_book(w, d, ident)
        return got if isinstance(got, dict) else {"status": "ABSENT",
                                                  "detail": None}

    def digest(self, *, now=None) -> dict:
        at = float(self._clock() if now is None else now)
        with self._lock:
            n = len(self.loaded_at)
            sup = sum(1 for s, w in self.window.items()
                      if (w or {}).get("status") == "SUPPORTED")
            dur = sum(1 for d in self.durable.values() if d)
            last = self.last_refresh_at
        return {"symbols_loaded": n, "window_supported": sup,
                "durable_certificates": dur,
                "last_refresh_age_s": (None if last is None
                                       else round(at - last, 1)),
                "last_refresh": self.last_refresh,
                "max_age_s": SAME_BOOK_EVIDENCE_MAX_AGE_S}


EVIDENCE = Evidence()


async def refresh_evidence(conn, *, symbols=None, now=None, force=False,
                           evidence=None) -> dict:
    """Load THESE symbols' same-book evidence (default: every symbol this
    process's stream holds) when the last load is older than
    SAME_BOOK_REFRESH_S. Never raises; a failed read leaves the previous
    load to age out (SAME_BOOK_EVIDENCE_MAX_AGE_S), never extends it."""
    ev = evidence if evidence is not None else EVIDENCE
    at = float(time.time() if now is None else now)
    if not force and ev.last_refresh_at is not None and \
            at - ev.last_refresh_at < SAME_BOOK_REFRESH_S:
        return {"refreshed": False, "why": "NOT_DUE"}
    try:
        if symbols is None:
            from . import institutional_stream as IS
            symbols = IS.BOOKS.wanted()
        syms = sorted({str(s) for s in symbols or () if s})[
            :SAME_BOOK_MAX_SYMBOLS]
        if not syms:
            ev.last_refresh_at = at
            ev.last_refresh = {"symbols": 0}
            return {"refreshed": True, "symbols": 0}
        from . import institutional_same_book as SB
        from .paper_market_data import durable_certifications
        window = await SB.same_book_by_symbol(conn, syms)
        durable = await durable_certifications(conn, syms)
        ev.put(syms, window, durable, at=at)
        ev.last_refresh_at = at
        ev.last_refresh = {
            "symbols": len(syms),
            "window_supported": sum(1 for v in window.values()
                                    if (v or {}).get("status")
                                    == "SUPPORTED"),
            "durable_certificates": len(durable)}
        return dict(ev.last_refresh, refreshed=True)
    except Exception as exc:                                   # noqa: BLE001
        return {"refreshed": False, "error": type(exc).__name__}


# ═════════════════════════════════════════════════════════════════════
# THE COUNTERS: WHICH SOURCE SERVED EACH CONSUMER READ
# ═════════════════════════════════════════════════════════════════════

class Counters:
    def __init__(self):
        self._lock = threading.Lock()
        self.by_consumer: dict = {}

    def note(self, consumer: str, source: str, refusal=None) -> None:
        with self._lock:
            c = self.by_consumer.setdefault(str(consumer), {
                SOURCE_PMX: 0, SOURCE_REST: 0, "fallback_reasons": {}})
            c[source] = c.get(source, 0) + 1
            if refusal:
                k = str(refusal)[:120]
                c["fallback_reasons"][k] = c["fallback_reasons"].get(k, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            by = {k: dict(v, fallback_reasons=dict(v["fallback_reasons"]))
                  for k, v in self.by_consumer.items()}
        tot = {SOURCE_PMX: 0, SOURCE_REST: 0}
        for v in by.values():
            for s in SOURCES:
                tot[s] += int(v.get(s) or 0)
        n = tot[SOURCE_PMX] + tot[SOURCE_REST]
        return {"by_consumer": by, "totals": tot,
                "pmx_share": (round(tot[SOURCE_PMX] / n, 4) if n else None)}


COUNTERS = Counters()


def note_rest(consumer: str, refusal: str | None) -> None:
    """A consumer read REST (the PMX book did not serve, for `refusal`)."""
    COUNTERS.note(consumer, SOURCE_REST, refusal)


def consumer_read(slug: str, *, consumer: str, now=None,
                  max_receipt_age_s: float = MAX_RECEIPT_AGE_S,
                  not_before_epoch=None, identity_fn=None, current_fn=None,
                  evidence=None, running_fn=None, request_fn=None,
                  count: bool = True, env=None) -> dict:
    """THE CONSUMERS' ONE QUESTION: may the resident PMX book serve this
    read? {"ok": True, "read"} or {"ok": False, "refusal", "why"}. A PMX
    answer is counted here; a refusal is the caller's REST read and is
    counted by the caller once it reads REST (`note_rest`), so an answer is
    counted exactly once. NEVER RAISES; reads no network."""
    at = float(time.time() if now is None else now)
    try:
        got = _consumer_read(slug, now=at,
                             max_receipt_age_s=max_receipt_age_s,
                             not_before_epoch=not_before_epoch,
                             identity_fn=identity_fn, current_fn=current_fn,
                             evidence=evidence, running_fn=running_fn,
                             env=env, request_fn=request_fn)
    except Exception as exc:                                   # noqa: BLE001
        got = {"ok": False, "refusal": "%s:%s" % (R_RAISED,
                                                  type(exc).__name__),
               "why": "the PMX source raised; REST reads instead"}
    if got.get("ok") and count:
        COUNTERS.note(consumer, SOURCE_PMX)
    return got


def _consumer_read(slug, *, now, max_receipt_age_s, not_before_epoch,
                   identity_fn, current_fn, evidence, running_fn, env,
                   request_fn=None):
    if not enabled(env):
        return {"ok": False, "refusal": R_OFF, "why": "%s=off" % ENV_FLAG}
    s = str(slug or "").strip()
    from .paper_market_data import is_foreign_ticker
    if not s or is_foreign_ticker(s):
        return {"ok": False, "refusal": R_FOREIGN,
                "why": "PMX institutional books are Polymarket US only"}
    if identity_fn is None or current_fn is None or running_fn is None:
        from . import institutional_api_stream as IAS
        from . import institutional_stream as IS
        identity_fn = identity_fn or IAS.identity_mapper
        current_fn = current_fn or IS.current
        running_fn = running_fn or IAS.running
        request_fn = request_fn or IAS.request
    if not running_fn():
        return {"ok": False, "refusal": R_NOT_RUNNING,
                "why": "the PMX stream does not run in this process"}
    # ASKING IS THE SUBSCRIBE REQUEST: every consumer read refreshes this
    # symbol's ask, so the stream's own task bootstraps / subscribes it and
    # keeps it while consumers keep reading it (institutional_api_stream
    # MAX_REQUESTED); never a venue call
    if request_fn is not None:
        request_fn(s)
    ident = identity_fn(s, "YES")
    cur = current_fn(s, now=now) if ident else None
    ev = evidence if evidence is not None else EVIDENCE
    sbk = ev.effective(s, ident, now=now) if ident else None
    return arbitrate(s, identity=ident, current=cur, same_book=sbk, now=now,
                     max_receipt_age_s=max_receipt_age_s,
                     not_before_epoch=not_before_epoch)


def telemetry(*, now=None) -> dict:
    """Which source served each consumer read in THIS process (since
    import), every fallback by its reason, and the same-book evidence the
    rule reads. Never raises."""
    try:
        return dict(COUNTERS.snapshot(), version=VERSION,
                    enabled=enabled(), max_receipt_age_s=MAX_RECEIPT_AGE_S,
                    scope="PROCESS_SINCE_IMPORT",
                    evidence=EVIDENCE.digest(now=now),
                    rule=("PMX book serves a consumer read only with exact "
                          "identity, a venue ack on this connection, the "
                          "stream's decision-bound current(), our receipt "
                          "<= %.0f s (the owner's shared-read age) and this "
                          "symbol's own same-book SUPPORTED; else REST "
                          "exactly as before" % MAX_RECEIPT_AGE_S))
    except Exception as exc:                                   # noqa: BLE001
        return {"version": VERSION, "error": type(exc).__name__}


def reset() -> None:
    """Tests only."""
    global EVIDENCE, COUNTERS
    EVIDENCE = Evidence()
    COUNTERS = Counters()
