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
hidden, the state the update states (else the plane's refdata record's, the
stream's own rule) OPEN, not crossed. A CURRENT book counts for the plane's
bound from OUR RECEIPT, recorded into the refresher with origin SNAPSHOT
(G in the freshness window); it never enters the stream's books, is never
a PRIORITY_PMX_BOOKS parity book, never a decision input. A symbol not
returned before the call ended is counted (SNAPSHOT_REFRESH_SYMBOL_NOT_
RETURNED) and left to the REST refresh. Switch: UMP_SNAPSHOT_REFRESH=off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .. import institutional_stream as IS
from . import active_refresh as AR

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


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG, "on")).strip().lower() not in (
        "off", "0", "false", "no")


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


def judge_update(u: dict, *, asked: set, refdata_state=None) -> dict:
    """PURE. One decoded snapshot update -> {outcome, venue_ts, levels}.
    CURRENT only for a symbol asked for, with the venue's clock, not hidden,
    OPEN (its own state, else the refdata record's), not crossed."""
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
    state = u.get("state") or refdata_state
    if not state:
        return dict(out, outcome=AR.R_REFRESH_STATE_UNKNOWN)
    if str(state) not in IS.OPEN_STATES:
        return dict(out, outcome=AR.R_REFRESH_NOT_OPEN,
                    state=str(state)[:40])
    if bids and offers and max(int(p) for p, _q in bids) >= min(
            int(p) for p, _q in offers):
        return dict(out, outcome=AR.R_REFRESH_CROSSED)
    return dict(out, outcome=CURRENT)


def _refdata_state(mgr, s, *, now: float, bound: float):
    """The state the stream itself would fall back to for `s` -- its last
    update's, else the plane's refdata record's (institutional_stream
    current(): evidence.market.state) -- or None when the books do not hold
    it. Read through the books' public read, never their internals."""
    try:
        r = mgr.current(s, now=now, max_snapshot_age_s=bound) or {}
        return ((r.get("evidence") or {}).get("market") or {}).get("state")
    except Exception:                                           # noqa: BLE001
        return None


class SnapshotRefresh:
    """The snapshot refresh's state: when the next call may be made, the
    hold after a failure, and the totals."""

    def __init__(self, *, clock=time.time):
        self._clock = clock
        self.last_call_at = None
        self.hold_until = 0.0
        self.hold_why = None
        self.last: dict = {}
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
        runs; each result recorded into the refresher. None when not due."""
        now = clock()
        ok, why = self.due(now)
        if not ok or ref is None or mgr is None:
            return None
        due, _counts = ref.plan(mgr, now=now, bound=bound,
                                lead_s=SNAPSHOT_LEAD_S)
        syms = due[:MAX_SYMBOLS_PER_CALL]
        if not syms:
            # nothing due: no call, and the minute is not spent
            return None
        self.last_call_at = now
        try:
            token = token_fn()
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
                             refdata_state=_refdata_state(
                                 mgr, u.get("symbol"), now=at, bound=bound))
            o = j["outcome"]
            t["by_outcome"][o] = t["by_outcome"].get(o, 0) + 1
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
                           for k, v in self.totals.items()}}


def off_digest(why: str = R_SNAPSHOT_OFF) -> dict:
    return {"version": VERSION, "enabled": False, "why": why}
