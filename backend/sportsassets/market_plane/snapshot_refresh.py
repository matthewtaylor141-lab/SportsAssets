"""SNAPSHOT-ONLY gRPC REFRESH: ONE CALL RE-PROVES EVERY QUIET PRIORITY MEMBER
(RC6 lane D1, delivery). Read only; market data only; no order path.

THE VENUE'S OWN MECHANISM. The venue's official proto bundle
(vendor/pmx_proto/upstream/marketdatasubscription.proto, sha256 de43bd1c...)
defines on the market-data service a second RPC beside the stream this
plane holds: CreateMarketDataSubscription, server-streaming ("symbols are
fixed at subscription time"), whose request carries `snapshot_only` --
documented (/streaming-endpoints/market-data-stream, cited by
institutional_stream and research/p5_live_book_currency_review.md) as "If
True, receive only initial snapshot then close stream". One call naming the
quiet priority members returns each one's FULL BOOK as the venue holds it
now, and ends. The vendored stub already carries the method; the
subscribe-all stream is not touched (no unsubscribe, no reconnect, no
second subscribe on it).

WHY (RC6 lane D1). The REST GetOrderBook budget (12 a minute, the venue's
figure) holds at most 12 x 285 s / 60 = 57 quiet members current at once.
The refresh simulation on production's own distributions (tools/
freshness_refresh_sim.py: research-sql run 37870039455's stream gaps, 9-25 %
of members never re-sent) keeps 0.95 with REST alone only up to ~134
members; at the RC5 20:07Z denominator (186) it reaches 0.83-0.93 and at the
24 h maximum (306) 0.75-0.88. With one snapshot call a minute it is
0.978-0.9997 in every scenario, even when the venue returns half the symbols
asked. A connection heartbeat, a subscription ack or a sequence number
would not do this: none says anything about one book's content now
(research/p5_live_book_currency_review.md §2(b)-(d)); a fresh snapshot does.

THE BUDGET (documented, not the REST one): 20 concurrent gRPC streams PER
FIRM, pooled with orders, drop copy and the rest; 100 client messages a
second PER FIRM; 1,000 symbols per stream. This uses ONE stream for at most
CALL_DEADLINE_S, at most once every CALL_EVERY_S, ONE client message, at
most MAX_SYMBOLS_PER_CALL symbols. A failed call holds the next one
HOLD_S; a refusal of the call itself (UNIMPLEMENTED, PERMISSION_DENIED,
UNAUTHENTICATED, INVALID_ARGUMENT, RESOURCE_EXHAUSTED) holds it
REFUSED_HOLD_S -- the REST refresh carries on alone meanwhile, as before.

WHAT IS SENT, AND NOTHING ELSE. `snapshot_request` builds the one request:
an EXPLICIT, non-empty, de-duplicated symbol list (an EMPTY list is EVERY
instrument to the venue), snapshot_only=True, depth DEPTH, aggregated, no
skip-to-head; `outbound` refuses anything else before the wire.

WHAT COUNTS (`judge_update`): exactly the stream's and the REST refresh's
checks -- a symbol we asked for, the venue's transact_time, the book not
hidden, the state the update states OPEN (a fallback state -- the stream's
own last stated state, else the plane's refdata record's -- counts only
when it is the venue's word at the receipt: the stream's own state with
its newest update inside the bound, see fallback_is_the_venues_word), not
crossed. The REST judge requires the body's own state; a stateless snapshot
book is held to the venue's-word standard in BOTH directions. A CURRENT book counts for the plane's
bound from OUR RECEIPT, recorded into the refresher with origin SNAPSHOT
(G in the freshness window); it never enters the stream's books, is never
a PRIORITY_PMX_BOOKS parity book, never a decision input. A symbol not
returned before the call ended is counted (SNAPSHOT_REFRESH_SYMBOL_NOT_
RETURNED) and left to the REST refresh. Switch: UMP_SNAPSHOT_REFRESH=off.

WHOSE WORD A NOT-OPEN STATE IS (review of 785907f2). A snapshot book
usually carries no state: the vendored proto sends it "when the exchange
provides a non-default state" (marketdatasubscription.proto, MarketData
Update.state). The fallback the stream would use -- a refdata record or an
old stream state, of any age -- is not the venue's word about the market
NOW. So a not-open state makes the market NOT OPEN (ACTIVE_REFRESH_MARKET_
NOT_OPEN: EXTERNAL_UNAVAILABLE in the freshness window, the 900 s market
wait) only when it is (a) carried on the snapshot update itself, (b) a
TERMINAL state (the held-position rule: any age), or (c) the stream's own
state with the stream's newest update received inside the bound. Any other
not-open fallback is SNAPSHOT_REFRESH_STATELESS_BOOK_FALLBACK_STATE_NOT_
PROVEN: not current (the conservative judgement stands), SOFTWARE, counted
NOT_CURRENT in the window, retried after the 60 s of a failed read, and
it does not end an earlier current read. Every judgement records where its
state came from (state_from UPDATE / FALLBACK, with the fallback's source
and receipt instant).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .. import institutional_stream as IS
from . import active_refresh as AR
from . import freshness_window as FW

log = logging.getLogger(__name__)

VERSION = "PRIORITY_SNAPSHOT_REFRESH_V1"
ENV_FLAG = "UMP_SNAPSHOT_REFRESH"
RPC = "CreateMarketDataSubscription"
#: one call a minute at most
CALL_EVERY_S = 60.0
#: re-prove a refresh this close to the bound: more than one call interval,
#: so a quiet member is re-read before it lapses
SNAPSHOT_LEAD_S = 90.0
#: under the venue's documented 1,000 symbols per stream
MAX_SYMBOLS_PER_CALL = 250
CALL_DEADLINE_S = 10.0
DEPTH = IS.DEPTH
HOLD_S = 300.0
REFUSED_HOLD_S = 1800.0
REFUSED_CODES = ("UNIMPLEMENTED", "PERMISSION_DENIED", "UNAUTHENTICATED",
                 "INVALID_ARGUMENT", "RESOURCE_EXHAUSTED")

ORIGIN = "SNAPSHOT"
CURRENT = AR.CURRENT
R_SNAPSHOT_NOT_RETURNED = "SNAPSHOT_REFRESH_SYMBOL_NOT_RETURNED"
R_SNAPSHOT_BOOK_HIDDEN = "SNAPSHOT_REFRESH_BOOK_HIDDEN"
R_SNAPSHOT_NOT_ASKED = "SNAPSHOT_REFRESH_SYMBOL_NOT_ASKED"
R_SNAPSHOT_CALL_FAILED = "SNAPSHOT_REFRESH_CALL_FAILED"
R_SNAPSHOT_REFUSED = "SNAPSHOT_REFRESH_REFUSED_BY_THE_VENUE"
R_SNAPSHOT_HELD = "SNAPSHOT_REFRESH_HELD_AFTER_A_FAILED_CALL"
R_SNAPSHOT_NO_TOKEN = "SNAPSHOT_REFRESH_NO_BEARER_TOKEN"
R_SNAPSHOT_OFF = "SNAPSHOT_REFRESH_OFF_BY_SWITCH"
R_SNAPSHOT_UNAVAILABLE = "SNAPSHOT_REFRESH_TRANSPORT_UNAVAILABLE"
#: (review of 785907f2) a stateless snapshot book whose fallback state is
#: not open, the fallback neither terminal nor the stream's own state read
#: inside the bound: not current, and not the venue's word about the market
R_SNAPSHOT_FALLBACK_NOT_PROVEN = (
    "SNAPSHOT_REFRESH_STATELESS_BOOK_FALLBACK_STATE_NOT_PROVEN")
STATE_FROM_UPDATE, STATE_FROM_FALLBACK = "UPDATE", "FALLBACK"


def enabled(env=None) -> bool:
    """OFF unless UMP_SNAPSHOT_REFRESH is set on (final review of 1dff0d5f):
    the call is documented for the read:marketdata credential and spends no
    REST budget, but it opens a second market-data stream a minute against
    the firm's pooled 20 (the venue's recorded architecture for this plane
    is ONE market-data stream) and the venue's abuse-prevention rules name
    'polling for data available via streaming'; the credential also carries
    the primary stream. Turning it on is the owner's decision."""
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "off")).strip().lower() in (
        "on", "1", "true", "yes")


class SnapshotRefused(IS.OutboundRefused):
    """A snapshot request this plane does not send."""


def outbound(req):
    """THE GATE every snapshot request passes: snapshot_only set, an
    explicit non-empty list of non-empty symbols within the cap, aggregated,
    no skip-to-head. A test double without the fields passes through."""
    if not hasattr(req, "snapshot_only"):
        return req
    syms = list(req.symbols)
    if not req.snapshot_only:
        raise SnapshotRefused("refused: a continuous subscription is not "
                              "this refresh's (snapshot_only must be set)")
    if not syms:
        raise SnapshotRefused("refused: an empty symbol list is EVERY "
                              "instrument to the venue")
    if not all(str(s).strip() for s in syms):
        raise SnapshotRefused("refused: a request naming an empty symbol")
    if len(syms) > MAX_SYMBOLS_PER_CALL:
        raise SnapshotRefused("refused: more than %d symbols"
                              % MAX_SYMBOLS_PER_CALL)
    if getattr(req, "unaggregated", False) or getattr(
            req, "slow_consumer_skip_to_head", False):
        raise SnapshotRefused("refused: only the aggregated book, never "
                              "skip-to-head")
    return req


def snapshot_request(pb2, symbols):
    """THE ONE BUILDER: CreateMarketDataSubscriptionRequest(symbols=explicit,
    snapshot_only=True, depth=DEPTH)."""
    syms = sorted({str(s).strip() for s in symbols or () if str(
        s or "").strip()})
    return outbound(pb2.CreateMarketDataSubscriptionRequest(
        symbols=syms, depth=DEPTH, snapshot_only=True))


def call(token: str, symbols, *, target=None, deadline_s=CALL_DEADLINE_S,
         channel_factory=None, modules=None, clock=time.time) -> dict:
    """ONE snapshot-only call (blocking: run it in a worker thread).
    {status, updates: [(decoded update, receipt instant)], heartbeats, ms}.
    status: ENDED (the venue closed it), COMPLETE (every symbol received;
    the call cancelled here), or the gRPC status name. Never raises a venue
    or transport problem; the bearer token travels in metadata only."""
    t0 = clock()
    try:
        grpc, pb2, pb2_grpc, refdata = modules or IS.load_generated()
    except Exception as exc:                                    # noqa: BLE001
        return {"status": R_SNAPSHOT_UNAVAILABLE, "updates": [],
                "error": type(exc).__name__, "ms": 0.0}
    req = snapshot_request(pb2, symbols)
    asked = set(req.symbols)
    target = target or IS.GRPC_TARGET
    channel = (channel_factory(target) if channel_factory is not None else
               grpc.secure_channel(target, grpc.ssl_channel_credentials(),
                                   options=list(IS.CHANNEL_OPTIONS)))
    got, seen, hb, status = [], set(), 0, "ENDED"
    stream = None
    try:
        stub = pb2_grpc.MarketDataSubscriptionAPIStub(channel)
        stream = stub.CreateMarketDataSubscription(
            req, metadata=[("authorization", "Bearer %s" % token)],
            timeout=float(deadline_s))
        for resp in stream:
            if IS._has(resp, "update"):
                u = IS.decode_update(resp.update, refdata)
                got.append((u, clock()))
                seen.add(u.get("symbol"))
                if asked <= seen:
                    status = "COMPLETE"
                    break
            elif IS._has(resp, "heartbeat"):
                hb += 1
    except Exception as exc:                                    # noqa: BLE001
        status = IS._status_name(exc) or "TRANSPORT_%s" % type(
            exc).__name__
    finally:
        if stream is not None and status == "COMPLETE":
            try:
                stream.cancel()
            except Exception:                                   # noqa: BLE001
                pass
        try:
            channel.close()
        except Exception:                                       # noqa: BLE001
            pass
    return {"status": status, "updates": got, "heartbeats": hb,
            "ms": round((clock() - t0) * 1000.0, 1)}


def fallback_is_the_venues_word(fallback, *, at=None, bound=None) -> bool:
    """PURE. Whether a not-open FALLBACK state is the venue's own word about
    the market at `at` (module docstring): a TERMINAL state at any age (the
    held-position rule), or the stream's own state (state_source STREAM)
    with the stream's newest update received inside `bound` of `at`. A
    refdata record's transient state, or an old stream state, is not."""
    fb = fallback or {}
    st = str(fb.get("state") or "").upper()
    if not st:
        return False
    if st in FW.TERMINAL_STATES:
        return True
    rcv = fb.get("received_at")
    if fb.get("state_source") != "STREAM" or rcv is None or at is None \
            or bound is None:
        return False
    try:
        return 0.0 <= float(at) - float(rcv) <= float(bound)
    except (TypeError, ValueError):
        return False


def judge_update(u: dict, *, asked: set, fallback=None, refdata_state=None,
                 at=None, bound=None) -> dict:
    """PURE. One decoded snapshot update -> {outcome, venue_ts, levels,
    state_from, ...}. CURRENT only for a symbol asked for, with the venue's
    clock, not hidden, OPEN (its own state, else the stream's fallback
    `fallback` {state, state_source, received_at}; `refdata_state` alone is
    a REFDATA fallback), not crossed. A not-open state is the market's
    (R_REFRESH_NOT_OPEN) only when it is on the update or
    `fallback_is_the_venues_word` at the receipt instant `at`; any other
    not-open fallback is R_SNAPSHOT_FALLBACK_NOT_PROVEN."""
    sym = str((u or {}).get("symbol") or "")
    out = {"symbol": sym, "outcome": None, "venue_ts": None,
           "levels": None, "status": "SNAPSHOT"}
    if sym not in asked:
        return dict(out, outcome=R_SNAPSHOT_NOT_ASKED)
    bids, offers = list(u.get("bids") or ()), list(u.get("offers") or ())
    out["levels"] = [len(bids), len(offers)]
    ts = u.get("transact_time")
    vts = ts.timestamp() if hasattr(ts, "timestamp") else None
    if vts is None:
        return dict(out, outcome=AR.R_REFRESH_NO_VENUE_TS)
    out["venue_ts"] = vts
    if u.get("book_hidden"):
        return dict(out, outcome=R_SNAPSHOT_BOOK_HIDDEN)
    fb = dict(fallback or {})
    if not fb.get("state") and refdata_state:
        fb = {"state": refdata_state, "state_source": "REFDATA",
              "received_at": None}
    own = u.get("state")
    state = own or fb.get("state")
    if own:
        out["state_from"] = STATE_FROM_UPDATE
    elif state:
        out["state_from"] = STATE_FROM_FALLBACK
        out["fallback"] = {"state": str(state)[:40],
                           "state_source": fb.get("state_source"),
                           "received_at": fb.get("received_at")}
    if not state:
        return dict(out, outcome=AR.R_REFRESH_STATE_UNKNOWN)
    if str(state) not in IS.OPEN_STATES:
        if own or fallback_is_the_venues_word(fb, at=at, bound=bound):
            return dict(out, outcome=AR.R_REFRESH_NOT_OPEN,
                        state=str(state)[:40])
        return dict(out, outcome=R_SNAPSHOT_FALLBACK_NOT_PROVEN,
                    state=str(state)[:40])
    if not own and not fallback_is_the_venues_word(fb, at=at, bound=bound):
        # AN OPEN FALLBACK IS HELD TO THE SAME STANDARD (integration review
        # of f1496b80): the venue states OPEN on the update itself (OPEN is
        # not the proto's default; CLOSED, enum 0, is the one left off), so
        # a stateless book is CLOSED or unknown, never proof of OPEN. A
        # refdata record or a stream state older than the bound is not the
        # venue's word NOW: not current (N, kept in the denominator), never
        # an exclusion, and the REST read -- whose body states the market's
        # own state -- stays free to try it.
        return dict(out, outcome=R_SNAPSHOT_FALLBACK_NOT_PROVEN,
                    state=str(state)[:40])
    if bids and offers and max(int(p) for p, _q in bids) >= min(
            int(p) for p, _q in offers):
        return dict(out, outcome=AR.R_REFRESH_CROSSED)
    return dict(out, outcome=CURRENT)


def _fallback(mgr, s, *, now: float, bound: float) -> dict:
    """The state the stream itself would fall back to for `s`, with where
    it came from: {state, state_source (STREAM: its own last stated state;
    REFDATA: the plane's refdata record), received_at (the stream's newest
    update's receipt)} -- {} when the books do not hold it. Read through
    the books' public read (institutional_stream current(): evidence.market
    and evidence.snapshot), never their internals."""
    try:
        r = mgr.current(s, now=now, max_snapshot_age_s=bound) or {}
    except Exception:                                           # noqa: BLE001
        return {}
    ev = r.get("evidence") or {}
    mk, sn = ev.get("market") or {}, ev.get("snapshot") or {}
    if not mk.get("state"):
        return {}
    return {"state": mk.get("state"), "state_source": mk.get("state_source"),
            "received_at": sn.get("received_at")}


class SnapshotRefresh:
    """The snapshot refresh's state: when the next call may be made, the
    hold after a failure, and the totals."""

    def __init__(self, *, clock=time.time):
        self._clock = clock
        self.last_call_at = None
        self.hold_until = 0.0
        self.hold_why = None
        self.last: dict = {}
        #: where each judged book's state came from, and whether a
        #: fallback was the venue's word (UPDATE / FALLBACK:<source>:IN_BOUND
        #: | OLD / NONE): how much of SNAPSHOT-current rests on what
        self.state_from: dict = {}
        self.totals = {"calls": 0, "symbols_asked": 0, "returned": 0,
                       "current": 0, "not_current": 0, "not_returned": 0,
                       "by_status": {}, "by_outcome": {}}

    def due(self, now: float) -> tuple:
        if now < self.hold_until:
            return False, R_SNAPSHOT_HELD
        if self.last_call_at is not None and \
                now - self.last_call_at < CALL_EVERY_S:
            return False, "NOT_DUE"
        return True, None

    def _hold(self, now, status):
        refused = status in REFUSED_CODES
        self.hold_until = now + (REFUSED_HOLD_S if refused else HOLD_S)
        self.hold_why = (R_SNAPSHOT_REFUSED if refused
                         else R_SNAPSHOT_CALL_FAILED) + ":" + str(status)
        log.warning("market plane snapshot refresh: %s; next call in %.0f s",
                    self.hold_why, self.hold_until - now)

    async def step(self, ref, mgr, *, token_fn, bound: float,
                   clock=time.time, caller=None) -> dict | None:
        """ONE CALL when due: every refresher member due within
        SNAPSHOT_LEAD_S of the bound (the refresher's own plan: snapshot
        currency refusals only, never a market refusal, none in flight), in
        its order, up to MAX_SYMBOLS_PER_CALL; in flight while the call
        runs; each result recorded into the refresher. None when not due.
        Never blocks the loop it is awaited on: the token is minted and the
        call made in worker threads (a mint may wait on the keeper's lock
        and an HTTP POST)."""
        now = clock()
        ok, why = self.due(now)
        if not ok or ref is None or mgr is None:
            return None
        due, _counts = ref.plan(mgr, now=now, bound=bound,
                                lead_s=SNAPSHOT_LEAD_S)
        # a member the snapshot could not prove is the REST read's for
        # RETRY_NOT_OPEN_S (final review of 1dff0d5f)
        due = [s for s in due if not ref.snapshot_unprovable(s, now=now)]
        syms = due[:MAX_SYMBOLS_PER_CALL]
        if not syms:
            # nothing due: no call, and the minute is not spent
            return None
        self.last_call_at = now
        # the token is minted OFF the loop: token_fn is the keeper's token(),
        # which takes a threading lock (held by the keeper thread during its
        # own mint) and, on an expired cache, mints with a synchronous HTTP
        # POST (connect 5 s + read 30 s); on the loop it would stop the pass,
        # the heartbeats, the window sampler and the Kalshi runtime with it
        try:
            token = await asyncio.to_thread(token_fn)
        except Exception:                                       # noqa: BLE001
            token = None
        if not token:
            self.hold_until, self.hold_why = now + CALL_EVERY_S, \
                R_SNAPSHOT_NO_TOKEN
            self.last = {"at": now, "due": len(due), "asked": 0,
                         "why": R_SNAPSHOT_NO_TOKEN}
            return dict(self.last)
        for s in syms:
            ref.inflight.add(s)
        try:
            res = await asyncio.to_thread(caller or call, token, syms)
        except asyncio.CancelledError:
            for s in syms:
                ref.inflight.discard(s)
            raise
        except Exception as exc:                                # noqa: BLE001
            res = {"status": "RAISED:%s" % type(exc).__name__,
                   "updates": []}
        for s in syms:
            ref.inflight.discard(s)
        t = self.totals
        st = str(res.get("status"))
        t["calls"] += 1
        t["symbols_asked"] += len(syms)
        t["by_status"][st] = t["by_status"].get(st, 0) + 1
        asked, returned = set(syms), set()
        current = 0
        for u, at in res.get("updates") or ():
            j = judge_update(u, asked=asked,
                             fallback=_fallback(mgr, u.get("symbol"), now=at,
                                                bound=bound),
                             at=at, bound=bound)
            o = j["outcome"]
            t["by_outcome"][o] = t["by_outcome"].get(o, 0) + 1
            fbj = j.get("fallback") or {}
            sf = ("UPDATE" if j.get("state_from") == STATE_FROM_UPDATE
                  else "NONE" if j.get("state_from") is None
                  else "FALLBACK:%s:%s" % (
                      fbj.get("state_source") or "?",
                      "IN_BOUND" if fallback_is_the_venues_word(
                          dict(fbj, state=fbj.get("state") or "x"),
                          at=at, bound=bound) else "OLD"))
            key = "%s|%s" % (sf, o)
            self.state_from[key] = self.state_from.get(key, 0) + 1
            if o == R_SNAPSHOT_NOT_ASKED:
                continue
            returned.add(j["symbol"])
            ref.record_snapshot(j["symbol"], j, at=at)
            current += int(o == CURRENT)
        missing = asked - returned
        t["returned"] += len(returned)
        t["current"] += current
        t["not_current"] += len(returned) - current
        t["not_returned"] += len(missing)
        if missing:
            t["by_outcome"][R_SNAPSHOT_NOT_RETURNED] = t["by_outcome"].get(
                R_SNAPSHOT_NOT_RETURNED, 0) + len(missing)
        if st not in ("ENDED", "COMPLETE") and not returned:
            self._hold(now, st)
        self.last = {"at": now, "due": len(due), "asked": len(syms),
                     "status": st, "returned": len(returned),
                     "current": current, "not_returned": len(missing),
                     "ms": res.get("ms")}
        return dict(self.last)

    def digest(self, *, now: float) -> dict:
        return {"version": VERSION, "enabled": True, "rpc": RPC,
                "request": {"snapshot_only": True, "depth": DEPTH,
                            "max_symbols": MAX_SYMBOLS_PER_CALL,
                            "every_s": CALL_EVERY_S,
                            "lead_s": SNAPSHOT_LEAD_S,
                            "deadline_s": CALL_DEADLINE_S},
                "budget_basis": ("the venue's gRPC budget, not REST: 20 "
                                 "concurrent streams and 100 client "
                                 "messages a second per firm; one stream "
                                 "for <= %.0f s, one message, a minute"
                                 % CALL_DEADLINE_S),
                "hold_s": round(max(0.0, self.hold_until - now), 1),
                "hold_why": self.hold_why if now < self.hold_until
                else None,
                "last": dict(self.last),
                "totals": {k: (dict(v) if isinstance(v, dict) else v)
                           for k, v in self.totals.items()},
                # 'STATE ORIGIN|OUTCOME' -> books: CURRENT appears only
                # under UPDATE or FALLBACK:STREAM:IN_BOUND
                "by_state_from": dict(self.state_from)}


def off_digest(why: str = R_SNAPSHOT_OFF) -> dict:
    return {"version": VERSION, "enabled": False, "why": why}
