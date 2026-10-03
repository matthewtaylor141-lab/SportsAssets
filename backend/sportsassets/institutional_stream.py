"""RESIDENT INSTITUTIONAL BOOKS FROM THE PMX gRPC MARKET-DATA STREAM.

WHAT THE VENUE DOCUMENTS (captured 2026-09-16 from docs.polymarket.us, sha256
in research/beta48/capability/evidence; cited by path):

  * /streaming-endpoints/grpc-overview -- production target
    `grpc-api.prod.polymarketexchange.com:443`, TLS; bearer token in the
    `authorization` metadata; 20 concurrent streams per firm; client-to-server
    100 msg/s per firm.
  * /streaming-endpoints/market-data-stream -- service
    `polymarket.v1.MarketDataSubscriptionAPI`, rpc `BiDirectionalStreamMarketData`
    (subscribe / unsubscribe / keepalive commands; `depth`, `unaggregated`,
    `snapshot_only` read from the FIRST request only). Responses: `heartbeat`,
    `update` (MarketDataUpdate: symbol, bids, offers [BookEntry px, qty int64],
    optional state, optional stats, transact_time "server timestamp of update",
    book_hidden), `subscription_ack`, `subscription_error` (INVALID_SYMBOL,
    ALREADY_SUBSCRIBED, NOT_SUBSCRIBED). Needs only `read:marketdata`; NO
    participant id. An idle bidi stream is cut by the load balancer after
    3600 s without client traffic -- send a KeepAliveCommand every 30-60 min.
    An EMPTY symbol list subscribes to ALL instruments, so this client never
    opens a stream until it has at least one symbol to ask for.
  * /trader-guide/market-data -- "Each market data message is a complete
    snapshot. Replace your local book state with each update." Updates are sent
    on every change AND at regular intervals.

WHAT IT DOES NOT DOCUMENT, and how that is handled rather than guessed:

  * NO SEQUENCE NUMBER on MarketDataUpdate. Gaps are therefore detected the
    ways that exist: (a) the connection ending -- every book held is GAP until
    a full update for that market arrives on a LATER connection; (b) the
    venue's own `transact_time` moving BACKWARDS for a market on one
    connection -- GAP until an update at or past the high-water mark; (c) the
    connection going silent past the liveness bound -- STALE. Because every
    update is a full replacement, the next update IS the authoritative
    resnapshot; no merge is ever attempted. `venue_sequence` is reported as
    NOT_PROVIDED_BY_VENUE, never as a number we made up.
  * The generated Python modules come from the venue's proto bundle (a Google
    Drive download linked from /streaming-endpoints/proto-reference); neither
    it nor `grpcio` is in this image today. The transport imports them lazily
    and, when absent, the stream reports TRANSPORT_UNAVAILABLE and every
    `current()` refuses -- it fails closed, it does not fall back.

THE DECISION PATH READS ONE FUNCTION: `current(symbol)`. It returns the book
WITH its currency evidence (connection identity, market identity, snapshot
state, gap state, venue timestamp, local receipt timestamp, lag, depth, market
state) or a precise named refusal. It never answers ok for a reason it cannot
show. Keyed by the INSTITUTIONAL symbol; binding a retail slug to it is
`shadow_identity_resolver`'s job, not this module's.

NO ORDER PATH. Market data only; no order, cancel, position or funding call
exists in this file.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

from . import bettor_stream_currency as sc
from . import market_data_identity as mdi
from . import shadow_l2 as l2

log = logging.getLogger(__name__)

VERSION = "INSTITUTIONAL_STREAM_V1"
ENV_FLAG = "INSTITUTIONAL_MD_STREAM"
ENV_ON = ("on", "1", "true", "yes")

GRPC_TARGET = mdi.PMX_INTERFACE["grpc_target"]
DEPTH = 10
#: Documented: an idle bidi stream is reset after 3600 s; send every 30-60 min.
KEEPALIVE_S = 1800.0
#: Documented: 1000 symbols per stream. This lane holds a small focus set.
MAX_SYMBOLS = 200

#: No new numbers: the lane's own liveness and snapshot bounds.
MAX_SILENCE_S = sc.MAX_SILENCE_S
MAX_SNAPSHOT_AGE_S = sc.MAX_SNAPSHOT_AGE_S

#: A stream silent this long (no update, heartbeat or ack) is cancelled and
#: reconnected. Updates are documented to arrive "at regular intervals (even if
#: no changes)"; their interval is not documented, so this is generous.
WATCHDOG_S = 4 * MAX_SILENCE_S

RECONNECT_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
MAX_CONSECUTIVE_FAILURES = 8
RESTART_AFTER_GAVE_UP_S = 900.0
RESTART_AFTER_REFUSAL_S = 1800.0

VENUE_SEQUENCE = "NOT_PROVIDED_BY_VENUE"
OPEN_STATES = ("INSTRUMENT_STATE_OPEN",)

# ── stream states ─────────────────────────────────────────────────────
S_NOT_STARTED = "NOT_STARTED"
S_DISABLED = "DISABLED_BY_CONFIGURATION"
S_CREDENTIAL = "CREDENTIAL_REFUSED_BY_IDENTITY_GUARD"
S_TRANSPORT_UNAVAILABLE = "TRANSPORT_UNAVAILABLE"
S_IDLE = "IDLE_NO_SYMBOLS_REQUESTED"
S_CONNECTING = "CONNECTING"
S_CONNECTED = "CONNECTED"
S_RECONNECTING = "RECONNECTING"
S_REFUSED = "REFUSED_BY_VENUE"
S_GAVE_UP = "GAVE_UP"
S_STOPPED = "STOPPED"
RUNNING = (S_IDLE, S_CONNECTING, S_CONNECTED, S_RECONNECTING, S_REFUSED,
           S_GAVE_UP)

# ── refusals `current()` names ────────────────────────────────────────
R_NOT_RUNNING = "INSTITUTIONAL_STREAM_NOT_RUNNING_IN_THIS_PROCESS"
R_REFUSED_KEY = "VENUE_REFUSED_THE_STREAM_CREDENTIAL"
R_GAVE_UP = "RECONNECT_BOUND_EXHAUSTED"
R_NOT_REQUESTED = "SYMBOL_NOT_REQUESTED"
R_REFUSED_SYMBOL = "VENUE_REFUSED_THIS_SYMBOL"
R_NO_SCALE = "INSTRUMENT_SCALES_UNKNOWN"
R_NO_CONNECTION = "NO_CONNECTION_OPEN"
R_GAP_CONNECTION = "GAP_CONNECTION_LOST_AWAITING_FRESH_SNAPSHOT"
R_GAP_CLOCK = "GAP_VENUE_CLOCK_WENT_BACKWARDS_AWAITING_FRESH_SNAPSHOT"
R_SNAPSHOT_PENDING = "AWAITING_FIRST_SNAPSHOT_ON_THIS_CONNECTION"
R_NO_VENUE_TS = "UPDATE_CARRIES_NO_VENUE_TIMESTAMP"
R_SILENT = "CONNECTION_SILENT_PAST_THE_LIVENESS_BOUND"
R_SNAPSHOT_OLD = "SNAPSHOT_OLDER_THAN_THE_BOUND"
R_BOOK_HIDDEN = "VENUE_REPORTS_BOOK_HIDDEN"
R_NOT_OPEN = "MARKET_NOT_OPEN"
R_STATE_UNKNOWN = "MARKET_STATE_UNKNOWN"
R_CROSSED = "BOOK_CROSSED"
R_CURRENT = None

_BENIGN_SUBSCRIPTION_ERRORS = ("ALREADY_SUBSCRIBED", "NOT_SUBSCRIBED")


def _utc(ts) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return None


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


class ResidentBooks:
    """Per-symbol resident books and the connection they arrived on.

    Thread-safe: the transport calls in from its stream thread; the decision
    path reads from anywhere. Every public read takes the lock and returns
    plain data. Nothing here raises into either caller.
    """

    def __init__(self, *, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.state = S_NOT_STARTED
        self.state_why = "constructed; start_default() has not run in this process"
        self._conn_seq = 0
        self._conn_id = None
        self._connected = False
        self._connected_at = None
        self._last_life_at = None
        self._messages = 0
        self._stray = 0
        self._refusal = None
        self._markets: dict = {}
        self._instruments: dict = {}
        self._errors: list = []

    # ── configuration the caller supplies ─────────────────────────────

    def set_state(self, state: str, why: str) -> None:
        with self._lock:
            self.state, self.state_why = state, why

    def set_instrument(self, symbol: str, record) -> None:
        """The venue's refdata record for this symbol: ITS OWN scales (never a
        default -- the venue's own sample defaults to 1000 and warns against
        it) and its state as of refdata."""
        from . import pmx_institutional as pmx
        ps, qs = pmx.scales_of(record)
        rec = record if isinstance(record, dict) else {}
        with self._lock:
            self._instruments[str(symbol)] = {
                "price_scale": ps, "qty_scale": qs,
                "refdata_state": rec.get("state"),
                "product_id": rec.get("productId"),
                "at": self._clock()}

    def want(self, symbols) -> list:
        """Symbols the decision path needs. Returns the ones that are new."""
        at = self._clock()
        fresh = []
        with self._lock:
            for s in symbols or ():
                s = str(s or "").strip()
                if not s:
                    continue
                if s not in self._markets:
                    if len(self._markets) >= MAX_SYMBOLS:
                        continue
                    self._markets[s] = _new_market(at)
                    fresh.append(s)
                self._markets[s]["wanted_at"] = at
        return fresh

    def wanted(self) -> list:
        with self._lock:
            return sorted(self._markets)

    # ── transport events ──────────────────────────────────────────────

    def on_connected(self, conn_id=None) -> int:
        now = self._clock()
        with self._lock:
            self._conn_seq += 1
            self._conn_id = str(conn_id or uuid.uuid4())
            self._connected = True
            self._connected_at = now
            self._last_life_at = now
            self._refusal = None
            for m in self._markets.values():
                m["hw"] = None          # a venue clock is per connection
                m["acked_seq"] = None
            self.state, self.state_why = S_CONNECTED, "stream open"
            return self._conn_seq

    def silence_s(self) -> float | None:
        with self._lock:
            life = self._last_life_at if self._connected else None
        return None if life is None else self._clock() - life

    def on_heartbeat(self) -> None:
        with self._lock:
            self._last_life_at = self._clock()
            self._messages += 1

    def on_ack(self, added=(), removed=(), active=()) -> None:
        with self._lock:
            self._last_life_at = self._clock()
            self._messages += 1
            for s in added or ():
                m = self._markets.get(s)
                if m is not None:
                    m["acked_seq"] = self._conn_seq

    def on_subscription_error(self, code, message, symbols) -> None:
        now = self._clock()
        code = str(code or "")
        with self._lock:
            self._last_life_at = now
            self._messages += 1
            self._errors.append({"at": now, "code": code,
                                 "message": str(message or "")[:200],
                                 "symbols": len(symbols or ())})
            del self._errors[:-20]
            if code in _BENIGN_SUBSCRIPTION_ERRORS:
                return
            for s in symbols or ():
                m = self._markets.get(s)
                if m is not None:
                    m["refused"] = {"code": code,
                                    "message": str(message or "")[:200],
                                    "at": now}

    def on_update(self, u: dict, *, received_at=None) -> None:
        """ONE MarketDataUpdate (already decoded to plain data) -> a FULL
        replacement of that symbol's book, unless it is out of order."""
        now = float(received_at if received_at is not None else self._clock())
        sym = str((u or {}).get("symbol") or "")
        ts = _utc((u or {}).get("transact_time"))
        with self._lock:
            self._last_life_at = now
            self._messages += 1
            m = self._markets.get(sym)
            if m is None or not self._connected:
                self._stray += 1
                return
            hw = m.get("hw")
            if ts is not None and hw is not None and ts < hw:
                m["gap"] = {"reason": R_GAP_CLOCK, "at": now,
                            "seq": self._conn_seq, "high_water": _iso(hw),
                            "received_venue_ts": _iso(ts)}
                m["regressions"] += 1
                return
            if ts is not None:
                m["hw"] = ts if hw is None else max(hw, ts)
            gap = m.get("gap")
            m.update({
                "book_seq": self._conn_seq, "received_at": now,
                "venue_ts": ts,
                "bids": [(int(p), int(q)) for p, q in (u.get("bids") or [])],
                "offers": [(int(p), int(q))
                           for p, q in (u.get("offers") or [])],
                "book_hidden": bool(u.get("book_hidden")),
                "updates": m["updates"] + 1})
            if u.get("state"):
                m["stream_state"] = str(u.get("state"))
            # THE FULL UPDATE IS THE RESNAPSHOT. It closes a connection gap
            # from an EARLIER connection, and a clock gap only when it carries
            # a venue timestamp that restores the order (checked above).
            if gap and ((gap["reason"] == R_GAP_CONNECTION
                         and gap["seq"] < self._conn_seq)
                        or (gap["reason"] == R_GAP_CLOCK and ts is not None)):
                m["gap"] = None
                m["resnapshots"] += 1

    def on_disconnected(self, why: str = "") -> None:
        now = self._clock()
        with self._lock:
            self._connected = False
            for m in self._markets.values():
                if m.get("book_seq") is not None or m.get("gap"):
                    m["gap"] = {"reason": R_GAP_CONNECTION, "at": now,
                                "seq": self._conn_seq}
            self.state, self.state_why = S_RECONNECTING, \
                "stream ended: %s" % str(why)[:160]

    def on_refused(self, code: str) -> None:
        now = self._clock()
        with self._lock:
            self._connected = False
            self._refusal = {"code": str(code), "at": now}
            for m in self._markets.values():
                if m.get("book_seq") is not None:
                    m["gap"] = {"reason": R_GAP_CONNECTION, "at": now,
                                "seq": self._conn_seq}
            self.state, self.state_why = S_REFUSED, \
                "venue refused the stream: %s" % code

    def on_gave_up(self, failures: int) -> None:
        with self._lock:
            self._connected = False
            self.state, self.state_why = S_GAVE_UP, \
                "%d consecutive attempts delivered nothing" % failures

    # ── the read the decision path makes ──────────────────────────────

    def current(self, symbol, *, now=None) -> dict:
        at = float(now if now is not None else self._clock())
        sym = str(symbol or "")
        with self._lock:
            st, why_st = self.state, self.state_why
            m = dict(self._markets.get(sym) or {})
            known = sym in self._markets
            inst = dict(self._instruments.get(sym) or {})
            seq, cid = self._conn_seq, self._conn_id
            connected, life = self._connected, self._last_life_at
            conn_at, refusal = self._connected_at, dict(self._refusal or {})
        silence = None if life is None else round(at - life, 3)
        age = (None if m.get("received_at") is None
               else round(at - m["received_at"], 3))
        venue_ts, recv = m.get("venue_ts"), m.get("received_at")
        lag_ms = (None if venue_ts is None or recv is None
                  else round((recv - venue_ts.timestamp()) * 1000.0, 1))
        state = m.get("stream_state") or inst.get("refdata_state")
        state_src = ("STREAM" if m.get("stream_state") else
                     "REFDATA" if inst.get("refdata_state") else None)
        evidence = {
            "version": VERSION,
            "stream_state": st,
            "connection": {"seq": seq, "id": cid, "connected": connected,
                           "connected_at": conn_at, "silence_s": silence,
                           "target": GRPC_TARGET},
            "market": {"symbol": sym, "price_scale": inst.get("price_scale"),
                       "qty_scale": inst.get("qty_scale"),
                       "product_id": inst.get("product_id"),
                       "state": state, "state_source": state_src},
            "snapshot": {"book_seq": m.get("book_seq"),
                         "on_current_connection":
                             (m.get("book_seq") is not None
                              and m.get("book_seq") == seq),
                         "updates": m.get("updates", 0),
                         "resnapshots": m.get("resnapshots", 0),
                         "venue_ts": _iso(venue_ts),
                         "received_at": recv,
                         "lag_ms": lag_ms, "age_s": age},
            "gap": m.get("gap"),
            "regressions": m.get("regressions", 0),
            "depth": {"bids": len(m.get("bids") or ()),
                      "offers": len(m.get("offers") or ()),
                      "requested": DEPTH},
            "venue_sequence": VENUE_SEQUENCE,
            "bounds": {"max_silence_s": MAX_SILENCE_S,
                       "max_snapshot_age_s": MAX_SNAPSHOT_AGE_S},
        }

        def refuse(name, why):
            return {"ok": False, "symbol": sym, "refusal": name, "why": why,
                    "book": None, "evidence": evidence}

        if st not in RUNNING:
            return refuse(R_NOT_RUNNING, "the institutional stream is %s in "
                          "this process (%s)" % (st, why_st))
        if st == S_REFUSED:
            return refuse(R_REFUSED_KEY, "the venue refused the stream "
                          "credential (%s)" % refusal.get("code"))
        if st == S_GAVE_UP:
            return refuse(R_GAVE_UP, why_st)
        if not known:
            return refuse(R_NOT_REQUESTED, "nobody has asked the stream for "
                          "this symbol")
        if m.get("refused"):
            return refuse(R_REFUSED_SYMBOL, "the venue answered this symbol's "
                          "subscribe with %s" % m["refused"]["code"])
        if not (inst.get("price_scale") and inst.get("qty_scale")):
            return refuse(R_NO_SCALE, "no refdata scales for this instrument; "
                          "a book is never priced with an assumed scale")
        gap = m.get("gap")
        if not connected:
            if gap or m.get("book_seq") is not None:
                return refuse(R_GAP_CONNECTION, "the connection ended; the "
                              "book held before it is discarded, not aged")
            return refuse(R_NO_CONNECTION, "no stream connection is open")
        if gap and gap["reason"] == R_GAP_CLOCK:
            return refuse(R_GAP_CLOCK, "the venue's transact_time went "
                          "backwards for this symbol; awaiting an update at "
                          "or past the high-water mark")
        if m.get("book_seq") != seq:
            if gap:
                return refuse(R_GAP_CONNECTION, "reconnected; no fresh "
                              "snapshot for this symbol on the new connection "
                              "yet")
            return refuse(R_SNAPSHOT_PENDING, "subscribed; no full update on "
                          "this connection yet")
        if venue_ts is None:
            return refuse(R_NO_VENUE_TS, "the update carried no transact_time; "
                          "its currency cannot be shown")
        if silence is None or silence > MAX_SILENCE_S:
            return refuse(R_SILENT, "the stream has not proven itself alive "
                          "within %.0f s" % MAX_SILENCE_S)
        if age is None or age > MAX_SNAPSHOT_AGE_S:
            return refuse(R_SNAPSHOT_OLD, "the last full update for this "
                          "symbol is older than %.0f s" % MAX_SNAPSHOT_AGE_S)
        if m.get("book_hidden"):
            return refuse(R_BOOK_HIDDEN, "the venue marks this book hidden")
        if not state:
            return refuse(R_STATE_UNKNOWN, "neither the stream nor refdata "
                          "gave this instrument's state")
        if str(state) not in OPEN_STATES:
            return refuse(R_NOT_OPEN, "instrument state is %s" % state)
        ps, qs = inst["price_scale"], inst["qty_scale"]
        bids = [{"px": p, "qty": q, "price": l2._scaled(p, ps),
                 "size": l2._scaled(q, qs)} for p, q in m.get("bids") or ()]
        offers = [{"px": p, "qty": q, "price": l2._scaled(p, ps),
                   "size": l2._scaled(q, qs)} for p, q in m.get("offers") or ()]
        if bids and offers and max(b["px"] for b in bids) >= \
                min(o["px"] for o in offers):
            return refuse(R_CROSSED, "best bid is at or through best offer")
        return {"ok": True, "symbol": sym, "refusal": R_CURRENT,
                "why": "full update %.1f s old on connection %s, alive %.1f s "
                       "ago" % (age, seq, silence),
                "book": {"bids": bids, "offers": offers,
                         "best_bid": bids[0]["price"] if bids else None,
                         "best_offer": offers[0]["price"] if offers else None},
                "evidence": evidence}

    def digest(self, *, now=None) -> dict:
        at = float(now if now is not None else self._clock())
        with self._lock:
            syms = list(self._markets)
            base = {"version": VERSION, "state": self.state,
                    "why": self.state_why, "connection_seq": self._conn_seq,
                    "connected": self._connected, "messages": self._messages,
                    "stray_updates": self._stray,
                    "venue_refusal": dict(self._refusal or {}) or None,
                    "subscription_errors": list(self._errors[-5:])}
        by: dict = {}
        for s in syms:
            r = self.current(s, now=at)
            k = "OK" if r["ok"] else r["refusal"]
            by[k] = by.get(k, 0) + 1
        return dict(base, symbols=len(syms), by_refusal=by)


def _new_market(at: float) -> dict:
    return {"wanted_at": at, "acked_seq": None, "book_seq": None,
            "received_at": None, "venue_ts": None, "hw": None, "gap": None,
            "refused": None, "bids": [], "offers": [], "book_hidden": False,
            "stream_state": None, "updates": 0, "resnapshots": 0,
            "regressions": 0}


# ── decoding a documented MarketDataUpdate ────────────────────────────

def _has(msg, field: str) -> bool:
    try:
        return bool(msg.HasField(field))
    except (ValueError, AttributeError):
        return getattr(msg, field, None) not in (None, "", 0)


def decode_update(u, refdata_pb2=None) -> dict:
    """A generated MarketDataUpdate -> plain data, by the DOCUMENTED field
    names only (symbol, bids, offers, state, transact_time, book_hidden)."""
    state = None
    if _has(u, "state"):
        raw = u.state
        try:
            state = (refdata_pb2.InstrumentState.Name(raw) if refdata_pb2
                     else str(raw))
        except Exception:                                     # noqa: BLE001
            state = str(raw)
    ts = None
    if _has(u, "transact_time"):
        t = u.transact_time
        secs, nanos = int(getattr(t, "seconds", 0)), int(getattr(t, "nanos", 0))
        if secs or nanos:
            ts = datetime.fromtimestamp(secs + nanos / 1e9, tz=timezone.utc)
    return {"symbol": str(u.symbol),
            "bids": [(int(e.px), int(e.qty)) for e in u.bids],
            "offers": [(int(e.px), int(e.qty)) for e in u.offers],
            "state": state, "transact_time": ts,
            "book_hidden": bool(getattr(u, "book_hidden", False))}


# ── the gRPC transport ────────────────────────────────────────────────

def load_generated():
    """(grpc, pb2, pb2_grpc, refdata_pb2) or raise ImportError naming what is
    missing. The modules are the venue's proto bundle compiled with
    grpc_tools.protoc, imported under the documented package `polymarket.v1`."""
    import grpc
    from polymarket.v1 import marketdatasubscription_pb2 as pb2
    from polymarket.v1 import marketdatasubscription_pb2_grpc as pb2_grpc
    try:
        from polymarket.v1 import refdata_pb2
    except ImportError:
        refdata_pb2 = None
    return grpc, pb2, pb2_grpc, refdata_pb2


def transport_available() -> tuple:
    try:
        load_generated()
        return True, None
    except ImportError as exc:
        return False, ("%s -- grpcio and the venue's compiled proto bundle "
                       "(polymarket.v1) must be in the image" % exc)


_REFUSAL_CODES = ("UNAUTHENTICATED", "PERMISSION_DENIED")


def _status_name(exc) -> str | None:
    """grpc.RpcError.code().name, or None for anything else."""
    try:
        return getattr(exc.code(), "name", None)
    except Exception:                                         # noqa: BLE001
        return None


class GrpcBidiTransport:
    """One supervised BiDirectionalStreamMarketData stream feeding a
    ResidentBooks. Bounded reconnects; a venue refusal stops it (one fresh
    try per RESTART_AFTER_REFUSAL_S); never logs the token."""

    def __init__(self, books: ResidentBooks, token_fn, *, target=GRPC_TARGET,
                 depth=DEPTH, modules=None, clock=time.time,
                 sleep=None):
        self.books = books
        self._token_fn = token_fn
        self.target = target
        self.depth = int(depth)
        self._mods = modules
        self._clock = clock
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        # Interruptible by stop() unless a test injects its own sleep.
        self._sleep = sleep or self._stop.wait
        self._subscribed: set = set()
        self._connected = False
        self.consecutive_failures = 0
        self.attempts = 0
        self._thread = None

    def mods(self):
        if self._mods is None:
            self._mods = load_generated()
        return self._mods

    # requests ---------------------------------------------------------

    def subscribe(self, symbols) -> None:
        """Queue a subscribe for symbols not already on the stream."""
        new = [s for s in symbols or () if s and s not in self._subscribed]
        if not new:
            return
        _g, pb2, _pg, _r = self.mods()
        self._q.put(pb2.BiDirectionalStreamMarketDataRequest(
            subscribe=pb2.SubscribeCommand(symbols=list(new))))
        self._subscribed.update(new)

    def _requests(self, first):
        """The client-to-server half: the first request carries the options;
        a KeepAliveCommand goes out after KEEPALIVE_S without traffic."""
        _g, pb2, _pg, _r = self.mods()
        last = self._clock()
        yield first
        while not self._stop.is_set():
            try:
                req = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._clock() - last >= KEEPALIVE_S:
                    last = self._clock()
                    yield pb2.BiDirectionalStreamMarketDataRequest(
                        keepalive=pb2.KeepAliveCommand())
                continue
            if req is None:
                return
            last = self._clock()
            yield req

    # one connection ---------------------------------------------------

    def run_once(self) -> str:
        """Open, consume until the stream ends. Returns 'ended', 'refused',
        'idle' or 'error'. Delivering anything resets the failure count."""
        grpc, pb2, pb2_grpc, refdata = self.mods()
        # Fresh request queue BEFORE the wanted set is read: a symbol asked for
        # in between lands in this queue (a duplicate subscribe is answered
        # ALREADY_SUBSCRIBED, which is benign) instead of being lost.
        self._q = queue.Queue()
        self._subscribed = set()
        symbols = self.books.wanted()
        if not symbols:
            # NEVER an empty subscribe: the venue reads it as ALL instruments.
            self.books.set_state(S_IDLE, "no symbols requested")
            return "idle"
        token = self._token_fn()
        if not token:
            self.books.on_refused("TOKEN_NOT_ISSUED")
            return "refused"
        self.attempts += 1
        self.books.set_state(S_CONNECTING, "attempt %d" % self.attempts)
        self._subscribed.update(symbols)
        first = pb2.BiDirectionalStreamMarketDataRequest(
            subscribe=pb2.SubscribeCommand(symbols=list(symbols)),
            depth=self.depth)
        channel = grpc.secure_channel(self.target,
                                      grpc.ssl_channel_credentials())
        delivered = 0
        watchdog = None
        try:
            stub = pb2_grpc.MarketDataSubscriptionAPIStub(channel)
            responses = stub.BiDirectionalStreamMarketData(
                self._requests(first),
                metadata=[("authorization", "Bearer %s" % token)])
            self.books.on_connected("grpc-%s" % uuid.uuid4().hex[:12])
            watchdog = self._watchdog(responses)
            for resp in responses:
                delivered += 1
                if delivered == 1:
                    self.consecutive_failures = 0
                self._dispatch(resp, refdata)
                if self._stop.is_set():
                    break
            self.books.on_disconnected("stream completed by the server")
            return "ended" if delivered else "error"
        except Exception as exc:                              # noqa: BLE001
            code = _status_name(exc)
            if code in _REFUSAL_CODES:
                self.books.on_refused(code)
                return "refused"
            self.books.on_disconnected("%s %s" % (type(exc).__name__,
                                                  code or ""))
            return "error" if not delivered else "ended"
        finally:
            if watchdog is not None:
                watchdog.set()
            try:
                channel.close()
            except Exception:                                 # noqa: BLE001
                pass

    def _watchdog(self, call):
        """A SILENT STREAM IS ENDED, not trusted. `current()` already refuses
        past MAX_SILENCE_S; past WATCHDOG_S the call is cancelled so the
        supervisor reconnects instead of blocking on a dead socket."""
        done = threading.Event()

        def watch():
            while not done.wait(1.0) and not self._stop.is_set():
                silence = self.books.silence_s()
                if silence is not None and silence > WATCHDOG_S:
                    try:
                        call.cancel()
                    except Exception:                         # noqa: BLE001
                        pass
                    return
        threading.Thread(target=watch, daemon=True,
                         name="institutional-md-watchdog").start()
        return done

    def _dispatch(self, resp, refdata) -> None:
        if _has(resp, "update"):
            self.books.on_update(decode_update(resp.update, refdata),
                                 received_at=self._clock())
        elif _has(resp, "heartbeat"):
            self.books.on_heartbeat()
        elif _has(resp, "subscription_ack"):
            a = resp.subscription_ack
            self.books.on_ack(list(a.symbols_added), list(a.symbols_removed),
                              list(a.active_symbols))
        elif _has(resp, "subscription_error"):
            e = resp.subscription_error
            self.books.on_subscription_error(e.error_code, e.message,
                                             list(e.symbols))

    # the supervised loop ---------------------------------------------

    def run(self) -> None:
        while not self._stop.is_set():
            got = self.run_once()
            if got == "idle":
                self._sleep(1.0)
                continue
            if got == "refused":
                self._sleep(RESTART_AFTER_REFUSAL_S)
                continue
            self.consecutive_failures += 1 if got == "error" else 0
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self.books.on_gave_up(self.consecutive_failures)
                self._sleep(RESTART_AFTER_GAVE_UP_S)
                self.consecutive_failures = 0
                continue
            i = min(self.consecutive_failures, len(RECONNECT_BACKOFF_S) - 1)
            self._sleep(RECONNECT_BACKOFF_S[i])

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, daemon=True,
                                        name="institutional-md-stream")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)


# ── the process's one stream ──────────────────────────────────────────

BOOKS = ResidentBooks()
_TRANSPORT: GrpcBidiTransport | None = None
_START: dict = {"state": S_NOT_STARTED,
                "why": "start_default() has not run in this process"}
_LOCK = threading.Lock()


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "")).strip().lower() in ENV_ON


def start_default(*, env=None, transport_factory=None, token_fn=None,
                  available=None) -> dict:
    """Arm the stream from configuration. NEVER RAISES. Off unless
    INSTITUTIONAL_MD_STREAM=on; refuses a credential the identity guard
    refuses; reports TRANSPORT_UNAVAILABLE when grpcio or the compiled protos
    are absent."""
    global _TRANSPORT
    env = os.environ if env is None else env
    try:
        with _LOCK:
            if _TRANSPORT is not None:
                return {"started": False, "state": BOOKS.state,
                        "why": "already started"}
        if not enabled(env):
            _START.update(state=S_DISABLED, why="%s is not on" % ENV_FLAG)
            BOOKS.set_state(S_DISABLED, _START["why"])
            return dict(_START, started=False)
        refusal = mdi.guard(mdi.PMX, env=env)
        if refusal is not None:
            _START.update(state=S_CREDENTIAL, why=refusal)
            BOOKS.set_state(S_CREDENTIAL, refusal)
            return dict(_START, started=False)
        ok, why = (available() if available else transport_available())
        if not ok:
            _START.update(state=S_TRANSPORT_UNAVAILABLE, why=why)
            BOOKS.set_state(S_TRANSPORT_UNAVAILABLE, why)
            return dict(_START, started=False)
        if token_fn is None:
            from . import pmx_institutional as pmx
            token_fn = pmx.Institutional(env=env).token
        factory = transport_factory or GrpcBidiTransport
        t = factory(BOOKS, token_fn)
        with _LOCK:
            _TRANSPORT = t
        BOOKS.set_state(S_IDLE, "started; waiting for symbols")
        t.start()
        _START.update(state=S_IDLE, why="started")
        return dict(_START, started=True)
    except Exception as exc:                                  # noqa: BLE001
        _START.update(state=S_STOPPED, why="start_default raised %s"
                      % type(exc).__name__)
        return dict(_START, started=False)


def want(symbols) -> dict:
    """Ask the stream for these institutional symbols. NEVER RAISES."""
    try:
        fresh = BOOKS.want(symbols)
        t = _TRANSPORT
        if t is not None and fresh:
            t.subscribe(fresh)
        return {"queued": len(fresh)}
    except Exception as exc:                                  # noqa: BLE001
        return {"queued": 0, "why": type(exc).__name__}


def set_instrument(symbol, record) -> None:
    try:
        BOOKS.set_instrument(symbol, record)
    except Exception:                                         # noqa: BLE001
        log.debug("institutional_stream: set_instrument failed", exc_info=True)


def current(symbol, *, now=None) -> dict:
    """THE DECISION PATH'S ONE READ. The book plus its currency evidence, or a
    precise refusal. NEVER RAISES."""
    try:
        return BOOKS.current(symbol, now=now)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "symbol": str(symbol or ""),
                "refusal": R_NOT_RUNNING, "book": None, "evidence": None,
                "why": "current() raised %s" % type(exc).__name__}


def digest() -> dict:
    try:
        return dict(BOOKS.digest(), start=dict(_START))
    except Exception as exc:                                  # noqa: BLE001
        return {"version": VERSION, "digest_failed": type(exc).__name__}


def reset() -> None:
    """Tests only."""
    global BOOKS, _TRANSPORT
    with _LOCK:
        if _TRANSPORT is not None:
            try:
                _TRANSPORT.stop()
            except Exception:                                 # noqa: BLE001
                pass
        _TRANSPORT = None
        BOOKS = ResidentBooks()
        _START.clear()
        _START.update(state=S_NOT_STARTED,
                      why="start_default() has not run in this process")
