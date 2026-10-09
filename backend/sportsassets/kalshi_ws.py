"""KALSHI AUTHENTICATED WEBSOCKET ORDER BOOKS -- THE PRIMARY KALSHI BOOK
SOURCE (Kalshi rep production contract 2026-10-07; docs captured under
research/kalshi_canonical_venue/REP_PRODUCTION_CONTRACT_2026-10-07).

  connection   wss://external-api-ws.kalshi.com/trade-api/ws/v2, signed
               handshake: KALSHI-ACCESS-KEY / -SIGNATURE / -TIMESTAMP where
               the signature is RSA-PSS(SHA-256, MGF1-SHA256, salt = digest)
               -- or Ed25519 for an Ed25519 key -- over
               timestamp_ms + "GET" + "/trade-api/ws/v2"
  channel      orderbook_delta (market_tickers): an `orderbook_snapshot`
               first, then `orderbook_delta` updates; every message carries
               the subscription id (sid) and a per-sid sequence number (seq)
  levels       yes_dollars_fp / no_dollars_fp are BIDS ([price, count]);
               a delta is (price_dollars, delta_fp, side). Within one market
               NO ask = 1 - best YES bid and YES ask = 1 - best NO bid: one
               book, one liquidity pool (kalshi_market_data.book_from_
               orderbook reads the same shape)

CURRENT only after a complete snapshot on the CURRENT subscription. A seq
that is not last + 1 on its sid, a disconnect, an error on the sid: every
book of that sid goes GAP at once, its later deltas are ignored, the
markets are resubscribed, and each book returns to CURRENT only when its
fresh snapshot arrives. A pre-gap book is never routed.

SNAPSHOTS ARE IN THE SEQUENCE TOO (RC6 red-team, replay). Kalshi: seq is
"used for snapshot/delta consistency". A snapshot on a sid that is in
sequence must be last + 1 like a delta: a REPLAYED or out-of-order
snapshot (seq <= last) used to roll its book back to the older state while
it stayed CURRENT, and a snapshot that SKIPPED a seq (a lost message for
another market of the sid) used to advance the sid past the gap, so the
other market served a book missing that update as CURRENT. Either now gaps
the sid and is not applied (a replay: R_SNAPSHOT_OUT_OF_SEQUENCE; a lost
message: R_SEQ_GAP). A gapped sid is DEAD -- a later snapshot on it is
ignored (IGNORED_DEAD_SID) -- until the venue announces it again
(`subscribed`) or the connection ends; the resubscribe (a new
subscription) restores CURRENT.

OUR ACK STARTS THE SEQUENCE (RC6 acceptance model). The venue answers our
subscribe command (its id) with `subscribed` and then sends that
subscription's messages in order, the snapshot first, from seq 1. So a sid
acknowledged as ours is sequenced from the ack: its first message must be
seq 1 and every later one last + 1. A lost first snapshot, a delta ahead of
it, or a late / replayed snapshot of the same subscription gaps the sid like
any break (it used to be served CURRENT without the deltas already seen).
On a sid never acknowledged as ours on this connection (no ack, or an
announcement answering none of our commands) where the sequence starts is
unknown: a delta before its first snapshot is not applied and does not
start it, and that snapshot must follow the HIGHEST such seq (+ 1) or --
assumption S5, pinned by red-team scenario 5 -- carry seq 1.

Account limits: GET /trade-api/v2/account/limits is the authoritative
usage tier and read / write token buckets; REST recovery pacing derives
from the read bucket. The schema returns no WebSocket connection cap, so
none is assumed (this runtime opens ONE connection).

STRUCTURALLY READ-ONLY: this module and its runtime import no order,
cancel, funding, Small Live or capital code (tests/test_kalshi_ws_market_
data.py checks the import closure). The credential is used only to sign
the handshake and the limits read. That is OUR code's boundary, not the
key's: Kalshi documents no read-only key class, so the key itself is
account-wide (market_plane_guard is the process-level wall).

THE KEY (RC5, 2026-10-08): loaded and signed by kalshi_key, the one
implementation every Kalshi signer shares -- the value EXACTLY as
configured (production's Ed25519 PEM is 119 characters with its trailing
newline; it used to be .strip()ped here before loading), the type read from
the parsed key, Ed25519 or RSA-PSS by that type.
"""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal

from . import kalshi_key as KK

VERSION = "KALSHI_WS_ORDERBOOK_V1"
WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
REST_BASE = "https://external-api.kalshi.com"
LIMITS_PATH = "/trade-api/v2/account/limits"
CHANNEL = "orderbook_delta"
KEY_ID_ENV = "KALSHI_API_KEY_ID"
PRIVATE_KEY_PEM_ENV = "KALSHI_PRIVATE_KEY_PEM"
BOOK_BASIS = "KALSHI_WS_ORDERBOOK_DELTA_SNAPSHOT_THEN_SEQ"
DEFAULT_TOKEN_COST = 10
#: used ONLY when the account-limits read is unavailable: Basic tier read
#: budget (200 tokens/s) at the default cost, halved; labelled as such
FALLBACK_REST_RATE_PER_S = 10.0
SUBSCRIBE_CHUNK = 100
#: subscribe commands awaiting the venue's `subscribed` ack, kept at most
MAX_PENDING_SUBSCRIBES = 512
#: a session with nothing to read re-reads the wanted set this often (a
#: connection with no subscription receives nothing at all)
IDLE_RECHECK_S = 5.0
#: (RC6 plane hang, 2026-10-09) resubscribes of ONE market since a snapshot
#: last made its book CURRENT, at most, in one session; one past it ends
#: the session (Subscriber.ResubscribeStorm): every book GAP, a reconnect
#: after the run loop's backoff subscribes each wanted market once
MAX_RESUBSCRIBES_WITHOUT_RECOVERY = 2
#: the session's end when a market is resubscribed past that bound
R_RESUBSCRIBE_STORM = "KALSHI_WS_RESUBSCRIBE_STORM"

CURRENT = "CURRENT"
GAP = "GAP"
PENDING = "AWAITING_SNAPSHOT"
UNSUBSCRIBED = "UNSUBSCRIBED"

R_SEQ_GAP = "KALSHI_WS_SEQUENCE_GAP"
R_DISCONNECT = "KALSHI_WS_DISCONNECTED"
R_SUB_ERROR = "KALSHI_WS_SUBSCRIPTION_ERROR"
R_NO_CREDENTIAL = "KALSHI_WS_CREDENTIAL_NOT_PROVISIONED"
#: a snapshot whose seq is not last + 1 on an in-sequence sid (a replayed /
#: out-of-order snapshot, or one that skipped a lost message)
R_SNAPSHOT_OUT_OF_SEQUENCE = "KALSHI_WS_SNAPSHOT_OUT_OF_SEQUENCE"


# ── signing (the handshake and the limits read) ─────────────────────────

def load_private_key(pem):
    """Either documented Kalshi key type (Ed25519 or RSA) from the value
    AS CONFIGURED -- never stripped first; a paste repair (base64 of the
    PEM, escaped newlines) only when the value does not load as given.
    ValueError(R_NO_CREDENTIAL) when there is nothing; otherwise a
    kalshi_key.KeyRefused (a ValueError) naming why it is not a key."""
    if pem is None or not str(pem).strip():
        raise ValueError(R_NO_CREDENTIAL)
    return KK.load_private_key(pem)


def signing_message(ts_ms: str, method: str, path: str) -> bytes:
    """timestamp + METHOD + path without the query (kalshi_venue's rule)."""
    return (str(ts_ms) + str(method).upper() + str(path).split("?")[0]
            ).encode("utf-8")


def sign(private_key, ts_ms: str, method: str, path: str) -> str:
    """Ed25519 or RSA-PSS by the key's own type (kalshi_key.sign); any
    other key type is refused by name before anything is signed (it used
    to fall through to the RSA call and fail with a TypeError)."""
    return KK.sign(private_key, signing_message(ts_ms, method, path))


def auth_headers(key_id: str, private_key, method: str, path: str, *,
                 ts_ms: str | None = None) -> dict:
    ts = ts_ms or str(int(time.time() * 1000))
    return {"KALSHI-ACCESS-KEY": str(key_id),
            "KALSHI-ACCESS-SIGNATURE": sign(private_key, ts, method, path),
            "KALSHI-ACCESS-TIMESTAMP": ts}


def credential_present(env) -> bool:
    return bool(str(env.get(KEY_ID_ENV) or "").strip()
                and str(env.get(PRIVATE_KEY_PEM_ENV) or "").strip())


# ── account limits ──────────────────────────────────────────────────────

def parse_limits(body: dict, *, as_of: float) -> dict:
    """GET /account/limits -> the recorded readback. Nothing assumed: a
    field the venue does not return is NOT_RETURNED_BY_VENUE."""
    b = body or {}

    def bucket(x):
        x = x or {}
        return {"refill_rate": x.get("refill_rate"),
                "bucket_capacity": x.get("bucket_capacity")}
    return {"usage_tier": b.get("usage_tier"),
            "read": bucket(b.get("read")), "write": bucket(b.get("write")),
            "grants": list(b.get("grants") or []),
            "websocket_connection_limit": (
                b.get("websocket_connection_limit")
                or "NOT_RETURNED_BY_VENUE"),
            "as_of": as_of, "source": "GET " + LIMITS_PATH}


def pacing(limits: dict | None, *, cost: int = DEFAULT_TOKEN_COST) -> dict:
    """REST recovery requests per second from the ACTUAL read bucket; the
    labelled fallback only when the limits read is unavailable."""
    rr = ((limits or {}).get("read") or {}).get("refill_rate")
    try:
        rate = float(rr) / float(cost)
    except (TypeError, ValueError, ZeroDivisionError):
        rate = None
    if rate and rate > 0:
        return {"rest_requests_per_s": rate,
                "basis": "ACCOUNT_LIMITS_READ_BUCKET (refill %s / cost %d)"
                % (rr, cost)}
    return {"rest_requests_per_s": FALLBACK_REST_RATE_PER_S,
            "basis": "FALLBACK_NO_ACCOUNT_LIMITS_READ (not our account "
                     "limit; conservative public pacing)"}


# ── the books ────────────────────────────────────────────────────────────

def _d(x) -> Decimal:
    return Decimal(str(x))


class WsBooks:
    """Per-market books under per-sid sequence discipline.

    THE SEQUENCE IS THE SUBSCRIPTION'S, NOT THE BOOK'S (RC6, 2026-10-09).
    The venue numbers every message of a sid -- each market's
    `orderbook_snapshot` as well as every `orderbook_delta` -- with one
    sequence ("Used for snapshot/delta consistency", websockets/orderbook-
    updates). So:
      * a snapshot whose seq skips on a sid that is already sequenced means
        messages of that sid were lost: every book of the sid goes GAP (the
        snapshot used to be applied and the sid's sequence re-based, so a
        lost delta of another market on the sid stayed CURRENT);
      * every in-sequence message advances the sid, including a delta for a
        market whose book is not CURRENT on that sid (it used to be skipped
        without advancing, so the sid's next message looked like a gap);
      * a sid that has gapped is DEAD until the connection ends, or until
        the venue acknowledges a new subscription under the same number
        (it is being unsubscribed): nothing on it is applied, and a snapshot
        still in flight on it never makes a book CURRENT -- its market is
        resubscribed if, and only if, the gap did not already do so (the
        market was not bound to the sid) and no live sid serves or awaits
        it: one gap, one resubscribe per market;
      * a gap or an error on a sid gaps only the books that live on that sid
        (or were subscribed on it and still wait for a snapshot); a book
        already CURRENT on, or acknowledged on, a live sid is left alone;
      * only a dead sid is ever unsubscribed (Subscriber), so no book is
        current on a subscription the venue no longer feeds.
    `bind(sid, tickers)` (the subscriber, on the venue's `subscribed` ack)
    records which markets a sid carries before any snapshot arrives, so a
    market whose snapshot never came is resubscribed with its sid -- and,
    for the ack of a command of ours, starts the sid's sequence (the
    subscription's first message is seq 1)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.books: dict = {}       # ticker -> {yes, no, sid, state, ...}
        self.sid_seq: dict = {}     # sid -> last seq
        self.sid_markets: dict = {}  # sid -> set(tickers)
        self.ticker_sid: dict = {}
        self.dead_sids: set = set()
        self.stats = {"snapshots": 0, "deltas": 0, "gaps": 0,
                      "disconnects": 0, "ignored_after_gap": 0,
                      "errors": 0, "ignored_dead_sid": 0,
                      "snapshots_out_of_sequence": 0, "forgotten": 0,
                      "ignored_not_tracked": 0}
        self.resubscribe: set = set()
        #: (RC6 acceptance model) sid -> the HIGHEST seq of a delta seen on
        #: a sid not acknowledged as ours, before its first snapshot: not its
        #: sequence, but that snapshot must agree with it
        #: (_first_snapshot_after)
        self.sid_pre: dict = {}
        #: (RC6 acceptance model) bumped on every change to a book's levels
        #: or state; each book carries the value of its latest change
        #: ("rev"), so a writer can tell any two versions of a book apart
        #: (the worker's flush key)
        self.rev = 0
        self.connected = False
        #: (RC6) markets the runtime stopped tracking in THIS session (see
        #: `forget`); cleared with the session
        self.forgotten: set = set()

    def _book(self, t):
        return self.books.setdefault(t, {
            "yes": {}, "no": {}, "sid": None, "state": PENDING,
            "snapshot_at": None, "updated_at": None, "venue_ts_ms": None,
            "seq": None, "why": None, "rev": 0})

    def _touch(self, b, **change) -> None:
        """Apply a change to a book's levels or state, and stamp it."""
        self.rev += 1
        b.update(change, rev=self.rev)

    def want(self, tickers) -> None:
        for t in tickers:
            self.forgotten.discard(t)
            self._book(t)

    def forget(self, tickers) -> dict:
        """(RC6) Stop holding the books of markets no longer tracked:
        {sid: [tickers]} of the live subscriptions they were on.

        WHY. Nothing removed a book: the runtime's wanted set rolls (the
        fixtures starting within [now - 4 h, now + 36 h]) but every market
        it ever wanted kept its book -- and counted in its freshness, as
        CURRENT -- for the life of the process. Production 2026-10-09 03:11Z
        (research-sql rc6_api-responsive_kalshi_ws_growth.sql): the
        heartbeat held 235 books, 235/235 "current", while the wanted set
        was 185; 50 of the CURRENT books were markets no longer tracked.
        A message still in flight for a forgotten market keeps its sid's
        sequence (an unbroken seq stays unbroken, a jump is still a gap)
        and never brings the book back (on_message)."""
        out: dict = {}
        for t in tickers:
            if t not in self.books:
                continue
            self.books.pop(t, None)
            sid = self.ticker_sid.pop(t, None)
            if sid is not None:
                out.setdefault(sid, []).append(t)
            # bound (acknowledged) on any sid, live or dead: a later gap of
            # that sid must not bring the book back
            for ms in self.sid_markets.values():
                ms.discard(t)
            self.resubscribe.discard(t)
            self.forgotten.add(t)
            self.stats["forgotten"] += 1
        return {k: sorted(v) for k, v in out.items()}

    def _not_tracked(self, typ, sid, seq) -> str:
        """A message for a forgotten market: no book, but its sid's
        sequence is kept exactly as a tracked market's message keeps it --
        a snapshot too: one past a lost message, or a replay (seq <= last),
        gaps the sid like any snapshot out of sequence (review of the
        three-lane merge b4506ed9: re-basing the sid on it served a tracked
        book missing the lost update as CURRENT, and a replay re-applied a
        delta). On a DEAD sid (its sequence already broke) nothing is
        re-armed and nothing is resubscribed: its place in the sequence
        cannot be known until the venue announces the sid anew, and the
        market is no longer wanted."""
        if sid in self.dead_sids:
            self.stats["ignored_not_tracked"] += 1
            return "IGNORED_DEAD_SID"
        last = self.sid_seq.get(sid)
        if last is None:
            if typ != "orderbook_snapshot":
                self._pre_snapshot(sid, seq)
                self.stats["ignored_not_tracked"] += 1
                return "IGNORED_NOT_TRACKED"
            last = self._first_snapshot_after(sid, seq)
        if last is not None and seq != last + 1:
            if typ == "orderbook_snapshot":
                self.stats["snapshots_out_of_sequence"] += 1
                self._gap_sid(sid, R_SNAPSHOT_OUT_OF_SEQUENCE
                              if seq <= last else R_SEQ_GAP)
            else:
                self._gap_sid(sid, R_SEQ_GAP)
            return "GAP"
        self.stats["ignored_not_tracked"] += 1
        self.sid_seq[sid] = seq
        self.sid_pre.pop(sid, None)
        return "IGNORED_NOT_TRACKED"

    def _pre_snapshot(self, sid, seq) -> None:
        """A delta on a sid with no sequence yet (never acknowledged as
        ours, no snapshot): not applied, not its sequence; the HIGHEST such
        seq is kept -- a replayed older delta after a newer one used to
        lower the mark, so a snapshot older than a delta already received
        passed as its successor (RC6 acceptance model, F3)."""
        pre = self.sid_pre.get(sid)
        self.sid_pre[sid] = seq if pre is None else max(pre, seq)

    def _first_snapshot_after(self, sid, seq):
        """What the FIRST snapshot on a sid with no sequence is checked
        against (None: it starts the sequence). Only a sid never
        acknowledged as ours gets here (`bind` sequences ours from the ack).
        A delta before that snapshot is not applied and does not start the
        sequence -- a delta numbered from an older subscription is never
        applied (red-team scenario 5) -- but the snapshot must follow the
        highest such seq (+ 1). Assumption S5 (red-team scenario 5 pins it):
        a seq-1 snapshot starts a subscription whatever preceded it on a sid
        this connection never acknowledged as ours. Any other -- a replay or
        duplicate of what preceded it, or one past a lost message -- is out
        of sequence like any snapshot."""
        pre = self.sid_pre.get(sid)
        return None if pre is None or seq == 1 else pre

    def on_connected(self) -> None:
        self.connected = True

    def on_disconnected(self) -> None:
        """Every book GAP: a book held across a reconnect is never reused.
        (The next connection subscribes every wanted market afresh; the
        resubscribe set is the subscriber's to reset then.)"""
        self.connected = False
        self.stats["disconnects"] += 1
        for t, b in self.books.items():
            if b["state"] != UNSUBSCRIBED:
                self._touch(b, state=GAP, why=R_DISCONNECT, sid=None)
                self.resubscribe.add(t)
        self.sid_seq.clear()
        self.sid_pre.clear()
        self.sid_markets.clear()
        self.ticker_sid.clear()
        self.dead_sids.clear()
        # a new session subscribes only what is wanted
        self.forgotten.clear()

    def bind(self, sid, tickers, *, ours: bool = True) -> None:
        """The venue acknowledged a subscribe as `sid`: the markets it
        carries are known before their snapshots arrive. A market CURRENT
        on another sid keeps that sid.

        `ours`: the ack answers a subscribe command this connection sent
        (its id). Then the subscription's messages follow it in order from
        seq 1, so the sid is sequenced from here (last seq 0): a first
        message that is not seq 1 -- a lost or late first snapshot, a delta
        ahead of it -- gaps the sid like any break (RC6 acceptance model,
        F1). A sid that already has a sequence (a live subscription) is
        never reset by an ack; on one with only unsequenced deltas, those
        preceded the ack and are not this subscription's."""
        if sid is None:
            return
        self._revive(sid)
        if ours and sid not in self.sid_seq:
            self.sid_seq[sid] = 0
            self.sid_pre.pop(sid, None)
        # a market dropped while this subscribe awaited its ack is not
        # brought back by the ack (review of b4506ed9)
        tickers = [t for t in tickers if t not in self.forgotten]
        self.sid_markets.setdefault(sid, set()).update(tickers)
        for t in tickers:
            b = self._book(t)
            if b["state"] != CURRENT or b["sid"] is None:
                self.ticker_sid[t] = sid

    def _revive(self, sid) -> None:
        """The venue announced (`subscribed`) a sid number this connection
        already saw die -- the docs promise no unique sid: a NEW
        subscription under a reused number. One connection delivers in
        order, so every message of the old subscription preceded the
        announcement: its sequence and markets start afresh (left dead, the
        new subscription's snapshots were ignored until a reconnect). A
        live sid is never reset by an announcement."""
        if sid in self.dead_sids:
            self.dead_sids.discard(sid)
            self.sid_seq.pop(sid, None)
            self.sid_pre.pop(sid, None)
            self.sid_markets.pop(sid, None)

    def _current_elsewhere(self, b, sid) -> bool:
        return b["state"] == CURRENT and b["sid"] is not None \
            and b["sid"] != sid

    def _held_elsewhere(self, t, b, sid) -> bool:
        """The market is served -- CURRENT -- or awaited -- acknowledged,
        its snapshot still to come -- on a LIVE sid other than `sid`: that
        subscription delivers its book, so nothing on `sid` resubscribes
        it (a resubscribe would open a second live sid for it)."""
        if self._current_elsewhere(b, sid):
            return True
        s = self.ticker_sid.get(t)
        return s is not None and s != sid and s not in self.dead_sids

    def _gap_sid(self, sid, why) -> None:
        if sid in self.dead_sids:
            return
        self.stats["gaps"] += 1
        for t in self.sid_markets.get(sid, set()):
            b = self._book(t)
            if self._held_elsewhere(t, b, sid):
                continue
            self._touch(b, state=GAP, why=why)
            self.resubscribe.add(t)
        self.sid_seq[sid] = None
        self.dead_sids.add(sid)

    def on_message(self, m: dict, *, recv_at: float | None = None) -> str:
        """Apply one decoded WS message; returns what happened."""
        at = float(recv_at if recv_at is not None else self.clock())
        typ = m.get("type")
        sid = m.get("sid")
        if typ == "subscribed":
            # (the subscriber binds the ack first, which revives the sid with
            # its markets; standalone, the announcement alone revives it)
            self._revive((m.get("msg") or {}).get("sid", sid))
            return "SUBSCRIBED"
        if typ == "error":
            self.stats["errors"] += 1
            if sid is not None:
                self._gap_sid(sid, R_SUB_ERROR)
            return "ERROR"
        if typ not in ("orderbook_snapshot", "orderbook_delta"):
            return "IGNORED"
        msg = m.get("msg") or {}
        t = msg.get("market_ticker")
        seq = m.get("seq")
        if t is None or sid is None or seq is None:
            return "MALFORMED"
        if t in self.forgotten:
            return self._not_tracked(typ, sid, seq)
        if sid in self.dead_sids:
            # gapped and being unsubscribed: nothing on it is applied. A
            # snapshot still in flight on it resubscribes its market ONLY
            # when the gap could not: the market was not known on the sid
            # (no `subscribed` ack bound it). A market the ack bound was
            # resubscribed at the gap, and one served or awaited on a live
            # sid has its subscription; resubscribing either opened a
            # second live sid for it, and the unsubscribe that followed
            # named the LIVE sid its new ack had bound (review of 5f8b2de5).
            # Each market is handled once per dead sid.
            if typ != "orderbook_snapshot":
                self.stats["ignored_after_gap"] += 1
                return "IGNORED_NOT_CURRENT"
            self.stats["ignored_dead_sid"] += 1
            b = self._book(t)
            on_dead = self.sid_markets.setdefault(sid, set())
            if t not in on_dead and not self._held_elsewhere(t, b, sid):
                self._touch(b, state=GAP, why=R_SEQ_GAP)
                self.resubscribe.add(t)
            on_dead.add(t)
            return "IGNORED_DEAD_SID"
        last = self.sid_seq.get(sid)
        if typ == "orderbook_snapshot":
            if last is None:
                last = self._first_snapshot_after(sid, seq)
            if last is not None and seq != last + 1:
                # messages of this sid were lost before this snapshot (seq
                # past last + 1), or it is a replayed / out-of-order snapshot
                # (seq <= last, red-team scenario 4: applying it rolled a
                # CURRENT book back): never applied, every book of the sid
                # goes GAP, this market's too (its sid is about to be torn
                # down)
                self.stats["snapshots_out_of_sequence"] += 1
                self.sid_markets.setdefault(sid, set()).add(t)
                self._gap_sid(sid, R_SNAPSHOT_OUT_OF_SEQUENCE
                              if seq <= last else R_SEQ_GAP)
                return "GAP"
            b = self._book(t)
            yes = {_d(p): _d(q) for p, q in msg.get("yes_dollars_fp") or []}
            no = {_d(p): _d(q) for p, q in msg.get("no_dollars_fp") or []}
            self._touch(b, yes={p: q for p, q in yes.items() if q > 0},
                        no={p: q for p, q in no.items() if q > 0},
                        sid=sid, state=CURRENT, snapshot_at=at,
                        updated_at=at, venue_ts_ms=m.get("sending_ts_ms"),
                        seq=seq, why=None)
            self.sid_markets.setdefault(sid, set()).add(t)
            self.ticker_sid[t] = sid
            self.sid_seq[sid] = seq
            self.sid_pre.pop(sid, None)
            self.resubscribe.discard(t)
            self.stats["snapshots"] += 1
            return "SNAPSHOT"
        # delta: the sid's sequence first, then this market's book
        if last is None:
            # before the first snapshot of a sid never acknowledged as ours:
            # never applied, not its sequence, but remembered
            # (_first_snapshot_after)
            self._pre_snapshot(sid, seq)
            self.stats["ignored_after_gap"] += 1
            return "IGNORED_NOT_CURRENT"
        if seq != last + 1:
            self._gap_sid(sid, R_SEQ_GAP)
            return "GAP"
        self.sid_seq[sid] = seq
        b = self._book(t)
        if b["sid"] != sid or b["state"] != CURRENT:
            self.stats["ignored_after_gap"] += 1
            return "IGNORED_NOT_CURRENT"
        side = str(msg.get("side") or "").lower()
        if side not in ("yes", "no"):
            self._gap_sid(sid, R_SUB_ERROR)
            return "GAP"
        lv = b[side]
        p, dq = _d(msg.get("price_dollars")), _d(msg.get("delta_fp"))
        q = lv.get(p, Decimal(0)) + dq
        if q > 0:
            lv[p] = q
        else:
            lv.pop(p, None)
        self._touch(b, updated_at=at, seq=seq,
                    venue_ts_ms=msg.get("ts_ms") or m.get("sending_ts_ms"))
        self.stats["deltas"] += 1
        return "DELTA"

    def current(self, t: str) -> dict:
        b = self.books.get(t)
        if b is None:
            return {"ok": False, "state": UNSUBSCRIBED, "book": None}
        ok = self.connected and b["state"] == CURRENT and \
            b["sid"] is not None and self.sid_seq.get(b["sid"]) is not None
        return {"ok": ok, "state": b["state"] if ok or b["state"] != CURRENT
                else GAP, "why": b["why"],
                "book": self.book_of(t) if ok else None,
                "snapshot_at": b["snapshot_at"],
                "updated_at": b["updated_at"]}

    def book_of(self, t: str) -> dict:
        """The REST orderbook_fp shape (bids ascending), so the one reader
        (kalshi_market_data.book_from_orderbook) derives the asks."""
        b = self.books[t]

        def lv(x):
            return [[str(p), str(q)] for p, q in sorted(x.items())]
        return {"orderbook_fp": {"yes_dollars": lv(b["yes"]),
                                 "no_dollars": lv(b["no"])}}

    def counts(self) -> dict:
        st: dict = {}
        for b in self.books.values():
            st[b["state"]] = st.get(b["state"], 0) + 1
        return {"markets": len(self.books), "by_state": st,
                "current": sum(1 for t in self.books
                               if self.current(t)["ok"]),
                "connected": self.connected, **self.stats}


# ── commands ─────────────────────────────────────────────────────────────

class Commands:
    def __init__(self):
        self.n = 0

    def _id(self):
        self.n += 1
        return self.n

    def subscribe(self, tickers: list) -> dict:
        return {"id": self._id(), "cmd": "subscribe", "params": {
            "channels": [CHANNEL], "market_tickers": list(tickers)}}

    def unsubscribe(self, sids: list) -> dict:
        return {"id": self._id(), "cmd": "unsubscribe",
                "params": {"sids": list(sids)}}


def chunks(xs: list, n: int = SUBSCRIBE_CHUNK):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ── the runtime (one connection) ─────────────────────────────────────────

class ResubscribeStorm(RuntimeError):
    """A market was resubscribed more than MAX_RESUBSCRIBES_WITHOUT_RECOVERY
    times with no snapshot making its book CURRENT in between: the session's
    repairs are not converging, so the session ends (fail closed)."""

    code = R_RESUBSCRIBE_STORM


class Subscriber:
    """One authenticated connection: subscribe the wanted markets in
    chunks, apply messages, resubscribe on a gap, reconnect on a drop.
    `connect` is an async callable returning a socket with async send /
    recv / close (websockets in production, a fake in tests).

    THE REPAIRS ARE BOUNDED (RC6 plane hang, 2026-10-09). Production
    sportsassets-market-plane, RC6.1 (3d5af039), one connection, no
    disconnect: 4,716 gaps, 1,286,756 resubscribes, 1,440,903 snapshots on
    dead sids, every one of 397 books GAP (pm-acceptance 37954055578,
    venues.json KALSHI_HEALTH.mechanism.ws). Every snapshot of a market not
    bound to the dead sid it arrived on resubscribed that market at once, and
    the venue answered each resubscribe with a snapshot that again arrived on
    a sid the books had declared dead -- an open loop: each message sent a
    subscribe, the session never ran out of frames to read, and it held the
    plane's one event loop (no universal_market_plane pass completed after
    15:22:06Z; CPU 1.0, RSS +10 MB/min). Now each market's resubscribes are
    counted until a snapshot makes its book CURRENT; one past
    MAX_RESUBSCRIBES_WITHOUT_RECOVERY ends the session (ResubscribeStorm):
    every book GAP (on_disconnected), the run loop's backoff, a fresh
    connection that subscribes each wanted market once."""

    def __init__(self, connect, books: WsBooks, *, wanted,
                 clock=time.time, backoff=(1, 2, 5, 10, 30, 60),
                 idle_recheck_s: float | None = None):
        self.connect = connect
        self.books = books
        self.wanted = wanted            # callable -> list of tickers
        self.clock = clock
        self.backoff = backoff
        self.idle_recheck_s = (IDLE_RECHECK_S if idle_recheck_s is None
                               else idle_recheck_s)
        self.cmd = Commands()
        # the first command id of the current session: a `subscribed` whose
        # id lies in [first_id, cmd.n] answers a command of ours
        self.first_id = 1
        self.subscribed: set = set()
        self.unsubscribed_sids: set = set()
        # each subscribe's markets until the venue acknowledges it (the
        # `subscribed` message names the command id and the sid it opened)
        self.pending: dict = {}
        self.connections = 0
        self.resubscribes = 0
        self.last_error = None
        # market -> resubscribes since a snapshot last made it CURRENT (this
        # session); sessions ended by a storm, counted
        self.unrecovered: dict = {}
        self.storms = 0

    def forget(self, tickers) -> int:
        """(RC6) The markets no longer wanted: their books dropped
        (WsBooks.forget) and out of the tracked subscription. No command is
        sent (the socket carries subscribe / unsubscribe only): the venue
        keeps sending their messages until the session ends -- each one
        ignored, its sid's sequence kept -- and the next session subscribes
        the wanted set alone. Returns how many books were dropped."""
        gone = set(tickers)
        held = sum(1 for t in gone if t in self.books.books)
        self.books.forget(gone)
        self.subscribed.difference_update(gone)
        for t in gone:
            self.unrecovered.pop(t, None)
        # an unacknowledged subscribe no longer names them
        for k, ts in list(self.pending.items()):
            self.pending[k] = [t for t in ts if t not in gone]
        return held

    def _ours(self, cid) -> bool:
        """The `subscribed` answers a command this session sent (its id)."""
        return type(cid) is int and self.first_id <= cid <= self.cmd.n

    async def _subscribe(self, ws, tickers) -> None:
        """Every record of a command is made BEFORE its send: `send` can
        suspend (write backpressure), and the worker's prune runs then. A
        market it drops is taken out of `subscribed` and `pending` by
        `forget`, and no later chunk names it (it used to be re-added to
        `subscribed` after the send -- so, wanted again, it was never
        subscribed again in the session -- and named by the next chunk)."""
        for c in chunks(sorted(tickers)):
            c = [t for t in c if t not in self.books.forgotten]
            if not c:
                continue
            cmd = self.cmd.subscribe(c)
            self.pending[cmd["id"]] = list(c)
            while len(self.pending) > MAX_PENDING_SUBSCRIBES:
                self.pending.pop(next(iter(self.pending)))
            self.subscribed.update(c)
            await ws.send(json.dumps(cmd))

    def _needs_repair(self) -> bool:
        return bool(self.books.resubscribe
                    or self.books.dead_sids - self.unsubscribed_sids)

    async def _resubscribe_gapped(self, ws) -> None:
        """Unsubscribe each DEAD sid once, then resubscribe the markets the
        books named. Only a sid the books have gapped is ever unsubscribed:
        it used to be the sid each resubscribed market's ticker_sid pointed
        to, which after a `subscribed` ack is the market's NEW live sid --
        that unsubscribe left the market's siblings CURRENT on a sid the
        venue no longer feeds, re-stamped fresh every reassert (review of
        5f8b2de5). A dead sid's books are already GAP and its sequence is
        void, so no book is ever current on a sid this runtime dropped."""
        sids = sorted(self.books.dead_sids - self.unsubscribed_sids)
        if sids:
            await ws.send(json.dumps(self.cmd.unsubscribe(sids)))
            self.unsubscribed_sids.update(sids)
        todo = sorted(self.books.resubscribe)
        if not todo:
            return
        # a market resubscribed past the bound with no snapshot making it
        # CURRENT since: the repairs do not converge -- end the session
        # before sending (fail closed; nothing is resubscribed again here)
        over = [t for t in todo if self.unrecovered.get(t, 0)
                >= MAX_RESUBSCRIBES_WITHOUT_RECOVERY]
        if over:
            self.storms += 1
            raise ResubscribeStorm(
                "%s: %d market(s) resubscribed %d times without a snapshot "
                "making them current (e.g. %s)" % (
                    R_RESUBSCRIBE_STORM, len(over),
                    MAX_RESUBSCRIBES_WITHOUT_RECOVERY, over[0]))
        for t in todo:
            self.unrecovered[t] = self.unrecovered.get(t, 0) + 1
        for t in todo:
            self.books.ticker_sid.pop(t, None)
        self.resubscribes += 1
        await self._subscribe(ws, todo)
        # stay GAP until each fresh snapshot arrives; do not re-send
        self.books.resubscribe.difference_update(todo)

    async def session(self, *, max_messages: int | None = None) -> None:
        ws = await self.connect()
        self.connections += 1
        self.books.on_connected()
        try:
            want = list(self.wanted())
            self.books.want(want)
            self.subscribed = set()
            self.unsubscribed_sids = set()
            self.pending.clear()
            self.first_id = self.cmd.n + 1
            # A NEW CONNECTION HAS NO SUBSCRIPTION: the one subscribe below
            # covers every wanted market. The previous connection's
            # disconnect left every book in the resubscribe set; acting on
            # it here re-sent a subscribe for every market on top of this
            # one (a second sid each, and a resubscribe counted that no gap
            # caused; the RC5 production readback shows 10 connections and
            # 18 resubscribes). Each book stays GAP until its fresh snapshot.
            self.books.resubscribe.clear()
            self.unrecovered.clear()
            await self._subscribe(ws, want)
            n = 0
            while max_messages is None or n < max_messages:
                # NOTHING TO READ IS NO REASON TO STOP LOOKING AT THE WANTED
                # SET: a session that started with it empty (the worker's
                # first read not landed yet) subscribed nothing, so the
                # venue sent nothing, and recv() waited forever while the
                # wanted set filled (review of ca102147). recv() is
                # cancellation-safe (websockets): no message is lost.
                try:
                    raw = await asyncio.wait_for(ws.recv(),
                                                 self.idle_recheck_s)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    n += 1
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if m.get("type") == "subscribed":
                        sid = (m.get("msg") or {}).get("sid")
                        cid = m.get("id")
                        self.books.bind(sid,
                                        self.pending.pop(cid, None) or [],
                                        ours=self._ours(cid))
                        # a reused sid number is a new subscription: if it
                        # gaps it is unsubscribed again
                        self.unsubscribed_sids.discard(sid)
                    if self.books.on_message(m, recv_at=self.clock()) == \
                            "SNAPSHOT":
                        # its book is CURRENT again: its repairs converged
                        self.unrecovered.pop(
                            (m.get("msg") or {}).get("market_ticker"), None)
                    if self._needs_repair():
                        await self._resubscribe_gapped(ws)
                add = [t for t in self.wanted() if t not in self.subscribed]
                if add:
                    self.books.want(add)
                    await self._subscribe(ws, add)
        finally:
            self.books.on_disconnected()
            try:
                await ws.close()
            except Exception:                                   # noqa: BLE001
                pass

    async def run(self, *, stop=None) -> None:
        i = 0
        while stop is None or not stop.is_set():
            try:
                await self.session()
                i = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:                            # noqa: BLE001
                self.last_error = type(exc).__name__
            await asyncio.sleep(self.backoff[min(i, len(self.backoff) - 1)])
            i += 1


def websockets_connect(key_id: str, private_key, *, url: str = WS_URL):
    """The production connector (websockets, keepalive pings)."""
    async def connect():
        import websockets
        headers = auth_headers(key_id, private_key, "GET", WS_PATH)
        return await websockets.connect(url, additional_headers=headers,
                                        ping_interval=10, ping_timeout=10,
                                        max_size=2 ** 22)
    return connect


# ── which mechanism serves Kalshi books (closeout 2026-10-08) ───────────

KALSHI_WS = "KALSHI_WS"
KALSHI_REST_FALLBACK = "KALSHI_REST_FALLBACK"
BEAT_MAX_AGE_S = 120.0


def mechanism(rest_detail: dict | None, ws_beat: dict | None,
              plane_beat: dict | None, *, now: float) -> dict:
    """PURE. Kalshi's market-data mechanism, from the three records that
    exist (never assumed): the REST worker's freshness (current books by
    persisted basis over the tracked denominator), the WebSocket runtime's
    heartbeat and the dedicated plane's boot record. KALSHI_WS is primary
    only when the plane is up and unrefused, the WebSocket runtime beats
    connected with >= 1 current book, and >= 1 tracked book is current on
    the WebSocket basis; otherwise KALSHI_REST_FALLBACK with every reason
    named. Beats are {"status", "detail", "at"}."""
    f = (rest_detail or {}).get("freshness") or {}
    by = f.get("current_by_source") or {}
    ws_cur, rest_cur = int(by.get("WS") or 0), int(by.get("REST") or 0)
    den = f.get("denominator")
    why = []
    pd = (plane_beat or {}).get("detail") or {}
    if plane_beat is None:
        why.append("MARKET_PLANE_ABSENT")
    elif now - float(plane_beat.get("at") or 0.0) > BEAT_MAX_AGE_S:
        why.append("MARKET_PLANE_HEARTBEAT_STALE")
    elif (pd.get("guard") or {}).get("refused"):
        why.append("MARKET_PLANE_REFUSED:%s" % pd["guard"]["refused"])
    wd = (ws_beat or {}).get("detail") or {}
    ws = wd.get("ws") or {}
    if ws_beat is None:
        why.append("KALSHI_WS_HEARTBEAT_ABSENT")
    elif now - float(ws_beat.get("at") or 0.0) > BEAT_MAX_AGE_S:
        why.append("KALSHI_WS_HEARTBEAT_STALE")
    elif wd.get("state") == "OWNER_ACTION_REQUIRED":
        why.append("KALSHI_WS_%s" % (wd.get("why") or "OWNER_ACTION"))
    else:
        if ws.get("connected") is not True:
            why.append("KALSHI_WS_NOT_CONNECTED")
        if int(ws.get("current") or 0) <= 0:
            why.append("KALSHI_WS_NO_CURRENT_BOOK")
    if ws_cur <= 0:
        why.append("NO_TRACKED_BOOK_CURRENT_ON_WS_BASIS")
    wsf = wd.get("freshness") or {}
    return {
        "mechanism": KALSHI_REST_FALLBACK if why else KALSHI_WS,
        "why": why,
        "plane": {"present": plane_beat is not None,
                  "commit": pd.get("commit"), "mode": (pd.get("guard") or {})
                  .get("mode"), "process_locked": (pd.get("guard") or {})
                  .get("process_locked"),
                  "age_s": (None if plane_beat is None else
                            round(now - float(plane_beat.get("at") or 0), 1))},
        "ws": {"state": wd.get("state") or (ws_beat or {}).get("status"),
               "connected": ws.get("connected"),
               "current_books": ws.get("current"),
               "subscribed_markets": wd.get("subscribed_markets"),
               "resubscribes": wd.get("resubscribes"),
               "connections": wd.get("connections"),
               # (RC6) what caused the resubscribes, as the books counted
               # it: a resubscribe follows a gap, a sid error or a snapshot
               # on a dead sid -- never a reconnect (that is `connections`)
               "gaps": ws.get("gaps"), "errors": ws.get("errors"),
               "disconnects": ws.get("disconnects"),
               "ignored_dead_sid": ws.get("ignored_dead_sid"),
               "by_state": ws.get("by_state"),
               "current_book_update_age_s": wd.get(
                   "current_book_update_age_s"),
               "numerator": wsf.get("numerator"),
               "denominator": wsf.get("denominator"),
               "account_limits_status": (wd.get("account_limits") or {})
               .get("status")},
        "tracked": {"denominator": den, "current_ws": ws_cur,
                    "current_rest": rest_cur,
                    "numerator": f.get("numerator"), "rate": f.get("rate"),
                    "ws_share": (round(ws_cur / den, 4) if den else None),
                    "sla_s": f.get("sla_s")},
        "accounting_rule": ("Kalshi freshness = current tracked books from "
                            "ANY basis / tracked books; WS share = those on "
                            "the snapshot-then-sequence WebSocket basis; "
                            "REST counted apart, never merged into WS")}
